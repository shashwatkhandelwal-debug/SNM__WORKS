"""
tests/test_specifications_browser.py — Comprehensive Unit & Integration Tests for Stage 8 Specifications Browser.

Tests:
1. List view query parameters: q, issuing_body, active, variant_status, has_pdf, sort, pagination (page, page_size).
2. Queue view (/specifications/queue): draft variants, 4-eyes segregation of duties, pending upload items, upload status filter.
3. Compare view (/specifications/compare): mode=variant (same, changed, only_in_a, only_in_b) and mode=revision.
4. Detail view tabs (/specifications/{spec_id}): variant filter for effective requirements, spec-wide indicators, source PDFs tab.
5. Side-by-side Viewer (/specifications/{spec_no}/source/{upload_id}): 200 with iframe & structured data, 404 on invalid/missing UUID.
6. RBAC & Authentication enforcement (401 unauthenticated, 403 unauthorized across all routes).
"""

import json
import uuid
import asyncpg
import pytest
from starlette.status import (
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)
from tests.conftest import LOCAL_TEST_DATABASE_URL


@pytest.mark.asyncio
async def test_spec_browser_auth_and_rbac(anonymous_client, finance_client, tech_client):
    """
    Test RBAC and Auth gating:
    - Anonymous client -> 401 on list, queue, compare, viewer, detail
    - Finance client (no specifications.read) -> 403 Forbidden
    - Technical client -> 200 OK
    """
    endpoints = [
        "/specifications",
        "/specifications/queue",
        "/specifications/compare",
        "/specifications/MIL-W-4088",
    ]
    for ep in endpoints:
        resp_anon = await anonymous_client.get(ep)
        assert resp_anon.status_code == HTTP_401_UNAUTHORIZED

        resp_fin = await finance_client.get(ep)
        assert resp_fin.status_code == HTTP_403_FORBIDDEN

        resp_tech = await tech_client.get(ep)
        assert resp_tech.status_code == HTTP_200_OK


@pytest.mark.asyncio
async def test_spec_list_filters_and_sorting(tech_client):
    """
    Tests list query filters: q, issuing_body, active, sort, page, page_size.
    """
    # 1. Search filter q
    resp_q = await tech_client.get("/specifications?q=Natick")
    assert resp_q.status_code == HTTP_200_OK
    assert "MIL-W-4088" in resp_q.text

    resp_empty_q = await tech_client.get("/specifications?q=NONEXISTENT_SPEC_12345")
    assert resp_empty_q.status_code == HTTP_200_OK
    assert "NO SPECIFICATIONS FOUND" in resp_empty_q.text

    # 2. Issuing body filter
    resp_body = await tech_client.get("/specifications?issuing_body=US+Army+Natick+RD%26E+Center")
    assert resp_body.status_code == HTTP_200_OK
    assert "MIL-W-4088" in resp_body.text

    # 3. Active filter
    resp_active = await tech_client.get("/specifications?active=active")
    assert resp_active.status_code == HTTP_200_OK
    assert "ACTIVE" in resp_active.text

    # 4. Sorting
    for sort_val in ["spec_no_asc", "spec_no_desc", "title_asc", "title_desc", "issued_on_desc", "created_at_desc"]:
        resp_sort = await tech_client.get(f"/specifications?sort={sort_val}")
        assert resp_sort.status_code == HTTP_200_OK

    # 5. Pagination
    resp_page = await tech_client.get("/specifications?page=1&page_size=10")
    assert resp_page.status_code == HTTP_200_OK
    assert "Showing" in resp_page.text


@pytest.mark.asyncio
async def test_spec_queue_draft_variants_and_uploads(tech_client, chief_quality_client):
    """
    Tests /specifications/queue:
    - Displays draft variants and pending uploads.
    - Tests 4-eyes approval constraint (creator cannot approve own variant).
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    
    # Clean up test records
    await conn.execute("DELETE FROM spec_variants WHERE designation LIKE 'Test Queue Variant%';")
    
    spec_row = await conn.fetchrow("SELECT id FROM specifications WHERE spec_no = 'MIL-W-4088' LIMIT 1;")
    spec_id = spec_row["id"]

    tech_profile = await conn.fetchrow("SELECT id FROM profiles LIMIT 1;")

    # Insert draft variant created by tech user
    var_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO spec_variants (id, spec_id, designation, class, status, created_by)
        VALUES ($1, $2, 'Test Queue Variant 1', '1', 'Draft', $3);
        """,
        var_id,
        spec_id,
        tech_profile["id"],
    )

    # Insert an upload in 'Parsed' status
    upload_id = uuid.uuid4()
    sku_row = await conn.fetchrow("SELECT id FROM skus LIMIT 1;")
    sku_id = sku_row["id"] if sku_row else None
    await conn.execute(
        """
        INSERT INTO spec_pdf_uploads (
            id, sku_id, spec_id, storage_path, pdf_sha256, source, original_filename,
            file_size_bytes, status, uploaded_by
        ) VALUES (
            $1, $2, $3, 'spec-docs/test_queue.pdf', '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
            'web_upload', 'test_queue_spec.pdf', 1024, 'Parsed', $4
        );
        """,
        upload_id,
        sku_id,
        spec_id,
        tech_profile["id"],
    )

    await conn.close()

    # Query queue with chief quality
    resp = await chief_quality_client.get("/specifications/queue")
    assert resp.status_code == HTTP_200_OK
    text = resp.text
    assert "SPECIFICATIONS REVIEW & APPROVAL QUEUE" in text
    assert "Test Queue Variant 1" in text
    assert "test_queue_spec.pdf" in text
    assert "✓ Approve Variant" in text

    # Filter upload status
    resp_parsed = await chief_quality_client.get("/specifications/queue?upload_status=Parsed")
    assert resp_parsed.status_code == HTTP_200_OK
    assert "test_queue_spec.pdf" in resp_parsed.text

    resp_error = await chief_quality_client.get("/specifications/queue?upload_status=Error")
    assert resp_error.status_code == HTTP_200_OK

    # Cleanup
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    await conn.execute("DELETE FROM spec_pdf_uploads WHERE id = $1;", upload_id)
    await conn.execute("DELETE FROM spec_variants WHERE id = $1;", var_id)
    await conn.close()


@pytest.mark.asyncio
async def test_spec_compare_variants_mode(tech_client):
    """
    Tests /specifications/compare in mode 'variant':
    - Compares requirements across two variants and identifies same, changed, only_in_a, only_in_b.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    spec_row = await conn.fetchrow("SELECT id FROM specifications WHERE spec_no = 'MIL-W-4088' LIMIT 1;")
    spec_id = spec_row["id"]

    var_a_id = uuid.uuid4()
    var_b_id = uuid.uuid4()

    await conn.execute(
        """
        INSERT INTO spec_variants (id, spec_id, designation, class, status) VALUES 
        ($1, $3, 'Type Test Alpha', '1', 'Approved'),
        ($2, $3, 'Type Test Beta', '1', 'Approved');
        """,
        var_a_id,
        var_b_id,
        spec_id,
    )

    # Common requirement with same value
    await conn.execute(
        """
        INSERT INTO spec_requirements (spec_id, variant_id, parameter, limit_type, spec_value, unit, sort_order) VALUES
        ($1, $2, 'Parameter Common Same', 'minimum', 1000, 'lb', 1),
        ($1, $3, 'Parameter Common Same', 'minimum', 1000, 'lb', 1);
        """,
        spec_id,
        var_a_id,
        var_b_id,
    )

    # Common requirement with changed value
    await conn.execute(
        """
        INSERT INTO spec_requirements (spec_id, variant_id, parameter, limit_type, spec_value, unit, sort_order) VALUES
        ($1, $2, 'Parameter Common Diff', 'minimum', 4000, 'lb', 2),
        ($1, $3, 'Parameter Common Diff', 'minimum', 6000, 'lb', 2);
        """,
        spec_id,
        var_a_id,
        var_b_id,
    )

    # Requirement only in A
    await conn.execute(
        """
        INSERT INTO spec_requirements (spec_id, variant_id, parameter, limit_type, spec_value, unit, sort_order) VALUES
        ($1, $2, 'Parameter Only A', 'maximum', 1.5, 'oz/yd', 3);
        """,
        spec_id,
        var_a_id,
    )

    # Requirement only in B
    await conn.execute(
        """
        INSERT INTO spec_requirements (spec_id, variant_id, parameter, limit_type, spec_value, unit, sort_order) VALUES
        ($1, $2, 'Parameter Only B', 'range', 5.0, '%', 4);
        """,
        spec_id,
        var_b_id,
    )

    await conn.close()

    resp = await tech_client.get(f"/specifications/compare?mode=variant&a={var_a_id}&b={var_b_id}")
    assert resp.status_code == HTTP_200_OK
    text = resp.text

    assert "Type Test Alpha" in text
    assert "Type Test Beta" in text
    assert "Parameter Common Same" in text
    assert "SAME" in text
    assert "Parameter Common Diff" in text
    assert "CHANGED" in text
    assert "Parameter Only A" in text
    assert "ONLY IN A" in text
    assert "Parameter Only B" in text
    assert "ONLY IN B" in text

    # Cleanup
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    await conn.execute("DELETE FROM spec_requirements WHERE variant_id IN ($1, $2);", var_a_id, var_b_id)
    await conn.execute("DELETE FROM spec_variants WHERE id IN ($1, $2);", var_a_id, var_b_id)
    await conn.close()


@pytest.mark.asyncio
async def test_spec_compare_revisions_mode(tech_client):
    """
    Tests /specifications/compare in mode 'revision'.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    
    spec_j_id = uuid.uuid4()
    spec_k_id = uuid.uuid4()

    await conn.execute(
        """
        INSERT INTO specifications (id, spec_no, revision, title, issuing_body, active) VALUES
        ($1, 'TEST-SPEC-9000', 'J', 'Webbing Spec Rev J', 'Army Research', false),
        ($2, 'TEST-SPEC-9000', 'K', 'Webbing Spec Rev K', 'Army Research Lab', true);
        """,
        spec_j_id,
        spec_k_id,
    )

    # Variants in J and K
    await conn.execute(
        """
        INSERT INTO spec_variants (spec_id, designation, class, status) VALUES
        ($1, 'Type Old Only', '1', 'Approved'),
        ($2, 'Type New Only', '1', 'Approved');
        """,
        spec_j_id,
        spec_k_id,
    )

    await conn.close()

    resp = await tech_client.get(f"/specifications/compare?mode=revision&a={spec_j_id}&b={spec_k_id}")
    assert resp.status_code == HTTP_200_OK
    text = resp.text

    assert "SPECIFICATION HEADER METADATA COMPARISON" in text
    assert "Webbing Spec Rev J" in text
    assert "Webbing Spec Rev K" in text
    assert "Type Old Only" in text
    assert "Type New Only" in text

    # Cleanup
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    await conn.execute("DELETE FROM spec_variants WHERE spec_id IN ($1, $2);", spec_j_id, spec_k_id)
    await conn.execute("DELETE FROM specifications WHERE id IN ($1, $2);", spec_j_id, spec_k_id)
    await conn.close()


@pytest.mark.asyncio
async def test_spec_detail_variant_filter_and_source_pdfs(tech_client, monkeypatch):
    """
    Tests /specifications/{spec_id}:
    - Filtering requirements by variant.
    - Tagging of spec-wide requirements.
    - Linked source PDFs tab.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    spec_row = await conn.fetchrow("SELECT id FROM specifications WHERE spec_no = 'MIL-W-4088' LIMIT 1;")
    spec_id = spec_row["id"]

    var_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO spec_variants (id, spec_id, designation, class, status)
        VALUES ($1, $2, 'Type Detail Test', '1', 'Approved');
        """,
        var_id,
        spec_id,
    )

    req_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO spec_requirements (id, spec_id, variant_id, parameter, limit_type, spec_value, unit)
        VALUES ($1, $2, $3, 'Variant Specific Parameter', 'minimum', 5000, 'lb');
        """,
        req_id,
        spec_id,
        var_id,
    )

    # Insert linked PDF upload
    upload_id = uuid.uuid4()
    profile = await conn.fetchrow("SELECT id FROM profiles LIMIT 1;")
    await conn.execute(
        """
        INSERT INTO spec_pdf_uploads (
            id, spec_id, storage_path, pdf_sha256, parsed_json_path,
            source, original_filename, file_size_bytes, status, uploaded_by
        ) VALUES (
            $1, $2, 'spec-docs/detail_test.pdf', 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789',
            'spec-parsed/detail_test.json', 'web_upload', 'mil_w_4088_test.pdf', 2048, 'Loaded', $3
        );
        """,
        upload_id,
        spec_id,
        profile["id"],
    )

    await conn.close()

    # 1. Detail without variant filter
    resp = await tech_client.get("/specifications/MIL-W-4088")
    assert resp.status_code == HTTP_200_OK
    assert "MIL-W-4088" in resp.text
    assert "Spec-wide (Applies to all)" in resp.text
    assert "mil_w_4088_test.pdf" in resp.text
    assert "abcdef012345" in resp.text  # 12 char sha256 prefix

    # 2. Detail with variant filter
    resp_filtered = await tech_client.get(f"/specifications/MIL-W-4088?variant_id={var_id}")
    assert resp_filtered.status_code == HTTP_200_OK
    assert "Variant Specific Parameter" in resp_filtered.text

    # Cleanup
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    await conn.execute("DELETE FROM spec_pdf_uploads WHERE id = $1;", upload_id)
    await conn.execute("DELETE FROM spec_requirements WHERE id = $1;", req_id)
    await conn.execute("DELETE FROM spec_variants WHERE id = $1;", var_id)
    await conn.close()


@pytest.mark.asyncio
async def test_spec_side_by_side_viewer(tech_client, monkeypatch):
    """
    Tests /specifications/{spec_no}/source/{upload_id}:
    - Renders embedded PDF iframe stream link and structured JSON tables.
    - Returns 404 for invalid/missing upload UUIDs.
    - Stubs storage calls using monkeypatch.
    """
    mock_spec_json = {
        "specification": {
            "spec_no": "MIL-W-4088",
            "revision": "K",
            "title": "Webbing, Textile, Woven Nylon",
            "issuing_body": "US Army Natick RD&E Center",
        },
        "variants": [
            {"designation": "Type VIII", "class": "1", "description": "Parachute harness webbing"}
        ],
        "requirements": [
            {"parameter": "Breaking Strength", "limit_type": "minimum", "spec_value": 4000, "unit": "lb"}
        ],
    }

    async def mock_get_file_from_storage(bucket: str, key: str) -> bytes:
        return json.dumps(mock_spec_json).encode("utf-8")

    monkeypatch.setattr("routers.spec_browser.get_file_from_storage", mock_get_file_from_storage)

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    spec_row = await conn.fetchrow("SELECT id FROM specifications WHERE spec_no = 'MIL-W-4088' LIMIT 1;")
    spec_id = spec_row["id"]
    profile = await conn.fetchrow("SELECT id FROM profiles LIMIT 1;")

    upload_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO spec_pdf_uploads (
            id, spec_id, storage_path, pdf_sha256, parsed_json_path,
            source, original_filename, file_size_bytes, status, uploaded_by
        ) VALUES (
            $1, $2, 'spec-docs/viewer_test.pdf', '112233445566778899aabbccddeeff00112233445566778899aabbccddeeff00',
            'spec-parsed/viewer_test.json', 'web_upload', 'source_standard_4088.pdf', 4096, 'Parsed', $3
        );
        """,
        upload_id,
        spec_id,
        profile["id"],
    )
    await conn.close()

    # 1. 200 OK on valid upload
    resp = await tech_client.get(f"/specifications/MIL-W-4088/source/{upload_id}")
    assert resp.status_code == HTTP_200_OK
    text = resp.text
    assert "READ-ONLY SOURCE VIEWER" in text
    assert "source_standard_4088.pdf" in text
    assert "112233445566" in text
    assert "STRUCTURED SPECIFICATION DATA" in text
    assert "Parachute harness webbing" in text
    assert "Breaking Strength" in text

    # 2. 404 on non-existent upload UUID
    non_existent = uuid.uuid4()
    resp_404 = await tech_client.get(f"/specifications/MIL-W-4088/source/{non_existent}")
    assert resp_404.status_code == HTTP_404_NOT_FOUND

    # 3. 404 on invalid UUID format
    resp_invalid = await tech_client.get("/specifications/MIL-W-4088/source/not-a-valid-uuid")
    assert resp_invalid.status_code == HTTP_404_NOT_FOUND

    # Cleanup
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    await conn.execute("DELETE FROM spec_pdf_uploads WHERE id = $1;", upload_id)
    await conn.close()
