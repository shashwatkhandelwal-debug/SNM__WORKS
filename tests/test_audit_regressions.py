"""
tests/test_audit_regressions.py — Dedicated Permanent Regression Tests for Audit Bug Fixes.

Covers:
1. textiles/pdf_certificate.py: NoneType.split() fix when DB columns/parameters are NULL.
2. routers/materials.py: p.email -> p.full_name fix in GRN profile joins.
3. routers/sku_specs.py: Invalid UUID handling on GET /skus/{sku_id}/spec-uploads/{upload_id}/pdf returns 404 (not 500).
4. routers/skus.py: PIL.UnidentifiedImageError handling on POST /skus/{id}/upload-photo returns 400 (not 500).
5. templates/marketing/_campaign_preview.html: POST /marketing/campaign/generate-preview renders template successfully.
6. routers/marketing.py: create_campaign gracefully handles unauthorized insertion without 500 crash.
7. routers/marketing.py: unauthenticated HTMX generation requests return 401.
8. routers/auth.py: Auth service network failure returns 503 / 502 (never 500).
9. routers/jobs.py & routers/skus.py: Explicit Layer 2 RBAC requirement enforced.
10. Uncovered route happy-paths:
    - GET /costing/{job_id}/edit
    - POST /tally/logs/{log_id}/retry
    - POST /skus/{sku_id}/spec-upload & GET /skus/{sku_id}/spec-uploads/{upload_id}/pdf
"""

import io
import json
import uuid
import pytest
import asyncpg
from unittest.mock import patch, AsyncMock
import httpx
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_502_BAD_GATEWAY,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from main import app
from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS, make_test_token
from textiles.pdf_certificate import generate_certificate_pdf


# ---------------------------------------------------------------------------
# Bug 1: PDF Certificate Generation with NULL / NoneType parameters
# ---------------------------------------------------------------------------
def test_pdf_certificate_handles_none_parameters():
    """
    Regression Test 1: generate_certificate_pdf must not raise AttributeError
    when parameter strings or numerical limits are None in the database record.
    """
    sparse_data = {
        "cert_no": "TC-REG-001",
        "job_no": "JOB-REG-001",
        "product": "Webbing",
        "spec": None,
        "customer_name": "Test Customer",
        "po_ref": None,
        "qty": 100,
        "unit": "m",
        "issued_at": "2026-09-06",
        "issuer_name": "QA Staff",
        "approver_name": "QA Manager",
        "construction": None,
        "qc_checks": [
            {
                "check_no": "QC-REG-01",
                "parameter": None,
                "method": None,
                "limit_type": "nominal",
                "spec_value": None,
                "tolerance": None,
                "actual": 25.0,
                "unit": None,
                "verdict": "PASS",
            }
        ],
        "lab_tests": [
            {
                "test_id": "LT-REG-01",
                "parameter": None,
                "standard": None,
                "limit_type": "minimum",
                "spec_value": 4000,
                "unit": None,
                "is_critical": False,
                "specimens": None,
                "result": None,
                "verdict": "PASS",
            }
        ],
    }
    pdf_bytes, sha_hash = generate_certificate_pdf(sparse_data)
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000
    assert len(sha_hash) == 64


# ---------------------------------------------------------------------------
# Bug 2: Materials GRN New Page Profiles Join (p.full_name, not p.email)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_materials_grn_new_page_renders_cleanly(store_client):
    """
    Regression Test 2: GET /materials/grn/new queries profiles table using
    p.full_name (avoiding undefined column p.email crash).
    """
    resp = await store_client.get("/materials/grn/new")
    assert resp.status_code == HTTP_200_OK
    assert "Goods Receipt Note" in resp.text or "GRN" in resp.text


# ---------------------------------------------------------------------------
# Bug 3: Spec PDF Viewer handles invalid non-UUID upload IDs with 404 (not 500)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sku_spec_pdf_invalid_uuid_returns_404(chief_quality_client):
    """
    Regression Test 3: GET /skus/{sku_id}/spec-uploads/not-a-uuid/pdf must return 404, not unhandled ValueError 500.
    """
    resp = await chief_quality_client.get("/skus/some-sku/spec-uploads/invalid-not-a-uuid/pdf")
    assert resp.status_code == HTTP_404_NOT_FOUND
    assert "PDF upload record not found" in resp.text or "404" in resp.text


# ---------------------------------------------------------------------------
# Bug 4: Non-Image Upload on SKU Photo returns 400 (not 500 PIL UnidentifiedImageError)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sku_photo_upload_invalid_image_returns_400(sales_client):
    """
    Regression Test 4: POST /skus/{id}/upload-photo with corrupt/non-image bytes returns 400.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    sku_id = await conn.fetchval(
        """
        INSERT INTO skus (sku_code, title, family, product, status)
        VALUES ('SKU-IMG-REG-01', 'Test Image Reg SKU', 'narrow', 'webbing', 'Draft')
        ON CONFLICT (sku_code) DO UPDATE SET title = EXCLUDED.title
        RETURNING id;
        """
    )
    await conn.close()

    resp = await sales_client.post(
        f"/skus/{sku_id}/upload-photo",
        files={"image_file": ("test.txt", b"This is not a real image file content", "text/plain")},
    )
    assert resp.status_code == HTTP_400_BAD_REQUEST
    assert "Invalid image file or format" in resp.text


# ---------------------------------------------------------------------------
# Bug 5 & 7: Marketing Campaign Preview Template & Unauthenticated Check
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_marketing_campaign_preview_template_and_auth(sales_client, anonymous_client):
    """
    Regression Test 5 & 7:
    - POST /marketing/campaign/generate-preview renders _campaign_preview.html partial (200 OK).
    - Unauthenticated request returns 401 Unauthorized.
    """
    # Authenticated request renders partial template
    resp = await sales_client.post(
        "/marketing/campaign/generate-preview",
        data={
            "occasion": "Republic Day Special",
            "headline": "Defence Grade Reliability",
            "body": "Military & Aerospace Procurement technical webbing.",
        },
    )
    assert resp.status_code == HTTP_200_OK
    assert "Republic Day Special" in resp.text or "Defence Grade Reliability" in resp.text

    # Unauthenticated request returns 401
    unauth_resp = await anonymous_client.post(
        "/marketing/campaign/generate-preview",
        data={
            "occasion": "Unauth Test",
            "headline": "Unauth Headline",
            "body": "Unauth Body",
        },
    )
    assert unauth_resp.status_code == HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# Bug 6: Campaign Create Permission Handling (Handled gracefully, never 500)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_campaign_create_unauthorized_role_handled_gracefully(hr_client):
    """
    Regression Test 6: POST /marketing/campaign/create catches Postgres RLS check
    violation gracefully and redirects without raising an unhandled 500 error.
    """
    resp = await hr_client.post(
        "/marketing/campaign/create",
        data={
            "occasion": "National Day",
            "headline": "Testing Headline",
            "body": "Testing Body Description",
        },
        follow_redirects=False,
    )
    assert resp.status_code == HTTP_303_SEE_OTHER


# ---------------------------------------------------------------------------
# Bug 8: Auth Login External Network Failure Returns 503 / 502 (Never 500)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_auth_login_connection_failure_returns_503(anonymous_client):
    """
    Regression Test 8: When external Supabase Auth fails with ConnectError,
    POST /auth/login returns 503 Service Unavailable, not unhandled 500.
    """
    with patch("routers.auth.httpx.AsyncClient") as mock_client_cls:
        mock_instance = AsyncMock()
        mock_instance.post.side_effect = httpx.ConnectError("Network is unreachable")
        mock_instance.__aenter__.return_value = mock_instance
        mock_instance.__aexit__.return_value = None
        mock_client_cls.return_value = mock_instance

        resp = await anonymous_client.post(
            "/auth/login",
            data={"email": "test@snmills.com", "password": "dummy_password"},
        )
        assert resp.status_code == HTTP_503_SERVICE_UNAVAILABLE
        assert "Failed to connect to authentication service" in resp.text


# ---------------------------------------------------------------------------
# Bug 9: Explicit Layer 2 RBAC enforcement on create_job and create_sku
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_layer2_rbac_on_jobs_and_skus_creation(hr_client):
    """
    Regression Test 9: HR Officer (lacking jobs.create and skus.create) is denied 403
    at FastAPI layer before database transaction.
    """
    job_resp = await hr_client.post(
        "/jobs",
        data={
            "job_no": "JOB-RBAC-DENY",
            "product": "Webbing",
            "qty_ordered": "100",
            "unit": "m",
        },
    )
    assert job_resp.status_code == HTTP_403_FORBIDDEN

    sku_resp = await hr_client.post(
        "/skus",
        data={
            "sku_code": "SKU-RBAC-DENY",
            "title": "Denied SKU",
            "family": "narrow",
            "product": "webbing",
        },
    )
    assert sku_resp.status_code == HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Uncovered Routes: Dedicated Happy-Path Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_costing_edit_form_happy_path(costing_analyst_client):
    """
    Dedicated Test for GET /costing/{job_id}/edit.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    cust_id = await conn.fetchval("SELECT id FROM customers LIMIT 1;")
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (job_no, customer_id, product, qty_ordered, unit, status)
        VALUES ($1, $2::uuid, 'Costing Edit Test', 500, 'm', 'In Production')
        RETURNING id::text;
        """,
        f"JOB-EDIT-{uuid.uuid4().hex[:6].upper()}",
        cust_id,
    )
    # Create draft costing
    await conn.execute(
        """
        INSERT INTO costing (job_id, qty, unit, yarn_rate, yarn_consumption, status, created_by)
        VALUES ($1::uuid, 500, 'm', 260.0, 40.0, 'Draft', $2::uuid);
        """,
        uuid.UUID(job_id),
        uuid.UUID(TEST_USERS["costing_analyst"]["id"]),
    )
    await conn.close()

    resp = await costing_analyst_client.get(f"/costing/{job_id}/edit")
    assert resp.status_code == HTTP_200_OK
    assert "COST SHEET" in resp.text or "Edit" in resp.text


@pytest.mark.asyncio
async def test_tally_retry_log_happy_path(chief_financial_client):
    """
    Dedicated Test for POST /tally/logs/{log_id}/retry.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    log_id = uuid.uuid4()
    # Create test customer with valid Tally mapping
    cust_id = await conn.fetchval(
        """
        INSERT INTO customers (name, tally_ledger_name, gst_state, gstin, active)
        VALUES ('Tally Retry Test Customer', 'Tally Retry Customer Ledger', 'Uttar Pradesh', '09AAAAA0000A1Z5', true)
        ON CONFLICT (name) DO UPDATE SET tally_ledger_name = 'Tally Retry Customer Ledger', gst_state = 'Uttar Pradesh'
        RETURNING id;
        """
    )
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (job_no, customer_id, product, qty_ordered, unit, agreed_rate, status)
        VALUES ($1, $2::uuid, 'Tally Retry Job', 1000, 'm', 150.0, 'In Production')
        RETURNING id;
        """,
        f"JOB-TLR-{uuid.uuid4().hex[:6].upper()}",
        cust_id,
    )
    desp_id = await conn.fetchval(
        """
        INSERT INTO despatch (despatch_no, job_id, qty, unit, status, created_by)
        VALUES ($1, $2::uuid, 500, 'm', 'Dispatched', $3::uuid)
        RETURNING id;
        """,
        f"DSP-TLR-{uuid.uuid4().hex[:4].upper()}",
        job_id,
        uuid.UUID(TEST_USERS["chief_financial"]["id"]),
    )

    await conn.execute(
        """
        INSERT INTO tally_sync_log (
            id, voucher_type, source_type, source_id, voucher_number, status, xml_payload, error_message
        ) VALUES (
            $1, 'Sales', 'despatch', $2::uuid, 'VCH-RETRY-01', 'Failed', '<ENVELOPE></ENVELOPE>', 'Network timeout'
        );
        """,
        log_id,
        desp_id,
    )
    await conn.close()

    resp = await chief_financial_client.post(f"/tally/logs/{log_id}/retry", follow_redirects=False)
    assert resp.status_code == HTTP_303_SEE_OTHER

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    row = await conn.fetchrow("SELECT status FROM tally_sync_log WHERE source_id = $1 ORDER BY created_at DESC LIMIT 1;", desp_id)
    assert row["status"] in ("Stubbed", "Pending", "Success")
    await conn.close()


@pytest.mark.asyncio
async def test_sku_specs_upload_and_stream_pdf_happy_path(chief_quality_client):
    """
    Dedicated Test for POST /skus/{sku_id}/spec-upload and GET /skus/{sku_id}/spec-uploads/{upload_id}/pdf.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    sku_id = await conn.fetchval(
        """
        INSERT INTO skus (sku_code, title, family, product, status)
        VALUES ('SKU-SPEC-PDF-01', 'Spec PDF Test SKU', 'narrow', 'webbing', 'Draft')
        ON CONFLICT (sku_code) DO UPDATE SET title = EXCLUDED.title
        RETURNING id;
        """
    )
    await conn.close()

    fake_pdf = b"%PDF-1.4 Mock military specification content for regression test"
    upload_resp = await chief_quality_client.post(
        f"/skus/{sku_id}/spec-upload",
        files={"pdf_file": ("mil_spec.pdf", fake_pdf, "application/pdf")},
        follow_redirects=False,
    )
    assert upload_resp.status_code in (HTTP_200_OK, HTTP_303_SEE_OTHER)

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    upload_row = await conn.fetchrow("SELECT id FROM spec_pdf_uploads WHERE sku_id = $1 ORDER BY uploaded_at DESC LIMIT 1;", sku_id)
    assert upload_row is not None
    upload_id = upload_row["id"]
    await conn.close()

    stream_resp = await chief_quality_client.get(f"/skus/{sku_id}/spec-uploads/{upload_id}/pdf")
    assert stream_resp.status_code == HTTP_200_OK
    assert stream_resp.headers["content-type"] == "application/pdf"
