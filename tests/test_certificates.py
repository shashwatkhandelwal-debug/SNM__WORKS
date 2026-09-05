"""
Tests for Test Certificate PDF & Conformance Module -- SNM Works
=============================================================================
1. End-to-end certificate generation and ReportLab PDF streaming.
2. Hard Quality Gating:
   - Zero QC checks -> 400 Bad Request
   - Zero Lab tests -> 400 Bad Request
   - Failing QC check -> 400 Bad Request
   - Failing Lab test -> 400 Bad Request
   - Unapproved critical lab test -> 400 Bad Request
   - Active QC hold -> 400 Bad Request
3. Despatch linkage and duplicate active certificate prevention.
4. RBAC checks: sales/line inspector denied issuance (403), permitted download (200).
5. Certificate revocation workflow and audit logging.
6. Unauthenticated requests rejected with 401.
=============================================================================
"""

import hashlib
import io
import os
from typing import Any, Dict, List, Optional, Tuple
import uuid
import asyncpg
from asyncpg.exceptions import UniqueViolationError
import httpx
import pytest
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)

from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS, make_test_token
from textiles.pdf_certificate import generate_certificate_pdf


# Helper to seed a complete qualifying job card
async def seed_qualifying_job(
    conn: asyncpg.Connection,
    suffix: str = "QUAL",
    include_qc: bool = True,
    include_lab: bool = True,
    qc_pass: bool = True,
    lab_pass: bool = True,
    lab_approved: bool = True,
) -> Tuple[str, Optional[str]]:
    # 1. Customer
    cust_id = await conn.fetchval("SELECT id FROM customers LIMIT 1;")
    if not cust_id:
        cust_id = await conn.fetchval(
            """
            INSERT INTO customers (id, name, active)
            VALUES (gen_random_uuid(), 'Ordnance Factory Kanpur', true)
            RETURNING id;
            """
        )

    # 2. Construction
    const_id = await conn.fetchval(
        """
        INSERT INTO constructions (
            id, spec_no, revision, status, family, product, spec,
            customer_id, width_mm, weave, warp_denier, weft_denier,
            warp_ends, picks_per_cm
        ) VALUES (
            gen_random_uuid(), $1, 'A', 'Approved', 'Narrow Wovens',
            'MIL-W-4088K Type VIII Webbing', 'MIL-W-4088K',
            $2::uuid, 44.5, '2/2 Herringbone Twill', 840, 840, 320, 18
        )
        RETURNING id;
        """,
        f"SPEC-CER-{suffix}-{uuid.uuid4().hex[:4].upper()}",
        cust_id,
    )

    # 3. Job Card
    job_no = f"JOB-CER-{suffix}-{uuid.uuid4().hex[:6].upper()}"
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (
            id, job_no, customer_id, construction_id, product, spec,
            po_ref, qty_ordered, unit, status
        ) VALUES (
            gen_random_uuid(), $1, $2::uuid, $3::uuid,
            'MIL-W-4088K Type VIII Webbing', 'MIL-W-4088K',
            'OFK/PO/2026/089', 2000, 'm', 'In Production'
        )
        RETURNING id;
        """,
        job_no,
        cust_id,
        const_id,
    )

    # 4. Despatch Note
    despatch_no = f"DSP-CER-{suffix}-{uuid.uuid4().hex[:4].upper()}"
    despatch_id = await conn.fetchval(
        """
        INSERT INTO despatch (
            id, despatch_no, job_id, invoice_no, qty, unit, rolls, gross_wt,
            status, created_by
        ) VALUES (
            gen_random_uuid(), $1, $2::uuid, 'INV-2026-901', 2000, 'm', 20, 120.5,
            'Ready for Dispatch', $3::uuid
        )
        RETURNING id;
        """,
        despatch_no,
        job_id,
        uuid.UUID(TEST_USERS["chief_quality"]["id"]),
    )

    # 5. QC Checks
    if include_qc:
        actual_val = 44.5 if qc_pass else 52.0  # 52.0 is out of 44.5 ± 1.5 range -> FAIL
        check_no = f"QC-CER-{suffix}-{uuid.uuid4().hex[:6].upper()}"
        await conn.execute(
            """
            INSERT INTO qc_checks (
                job_id, check_no, stage, parameter, unit, method, limit_type,
                spec_value, tolerance, actual, inspector_id
            ) VALUES (
                $1::uuid, $2, 'Loom State', 'Width', 'mm', 'ASTM D3776', 'nominal',
                44.5, 1.5, $3, $4::uuid
            );
            """,
            job_id,
            check_no,
            actual_val,
            uuid.UUID(TEST_USERS["line_inspector"]["id"]),
        )

    # 6. Lab Tests
    if include_lab:
        specimens = [4250.0, 4180.0, 4310.0] if lab_pass else [3800.0, 3950.0, 3900.0]  # Min 4000 lb required
        approver = uuid.UUID(TEST_USERS["chief_quality"]["id"]) if lab_approved else None
        test_id = f"LT-CER-{suffix}-{uuid.uuid4().hex[:6].upper()}"
        await conn.execute(
            """
            INSERT INTO lab_tests (
                job_id, test_id, test_type, parameter, standard, limit_type,
                spec_value, unit, is_critical, specimens, result,
                approved_by, approved_at, created_by
            ) VALUES (
                $1::uuid, $2, 'Physical', 'Breaking Strength', 'MIL-STD-191 Method 4108', 'minimum',
                4000, 'lbf', true, $3, '4180 lbf',
                $4, NOW(), $5::uuid
            );
            """,
            job_id,
            test_id,
            specimens,
            approver,
            uuid.UUID(TEST_USERS["lab_analyst"]["id"]),
        )

    return str(job_id), str(despatch_id)


# ============================================================================
# 1. REPORTLAB PURE GENERATION UNIT TEST
# ============================================================================

def test_pure_pdf_generation():
    """
    Proves that generate_certificate_pdf outputs valid binary PDF stream
    and deterministic SHA-256 digest.
    """
    dummy_data = {
        "cert_no": "TC-2026-0001",
        "job_no": "JOB-2026-001",
        "product": "MIL-W-4088K Type VIII Webbing",
        "spec": "MIL-W-4088K",
        "customer_name": "Ordnance Factory Kanpur",
        "po_ref": "PO-9912",
        "qty": 2000,
        "unit": "m",
        "issued_at": "2026-08-30 10:00 UTC",
        "issuer_name": "Quality Officer",
        "approver_name": "Chief Quality Officer",
        "construction": {"spec_no": "SPEC-101", "weave": "2/2 Twill", "width_mm": 44.5, "warp_denier": 840, "weft_denier": 840, "warp_ends": 320, "picks_per_cm": 18},
        "qc_checks": [{"check_no": "QC-01", "parameter": "Width", "method": "ASTM D3776", "limit_type": "nominal", "spec_value": 44.5, "tolerance": 1.5, "actual": 44.6, "unit": "mm", "verdict": "PASS"}],
        "lab_tests": [{"test_id": "LT-01", "parameter": "Breaking Strength", "standard": "ASTM D5034", "limit_type": "minimum", "spec_value": 4000, "unit": "lbf", "is_critical": True, "specimens": [4200, 4250, 4180], "result": "4180 lbf", "verdict": "PASS"}],
    }

    pdf_bytes, sha_hash = generate_certificate_pdf(dummy_data)
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 2000
    assert hashlib.sha256(pdf_bytes).hexdigest() == sha_hash


# ============================================================================
# 2. END-TO-END CERTIFICATE ISSUANCE & DOWNLOAD
# ============================================================================

@pytest.mark.asyncio
async def test_successful_certificate_issuance_and_download(chief_quality_client):
    """
    Proves that a fully passing, approved job card generates a formal Certificate
    of Conformance, creates the test_certificates database record, and streams the PDF.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id, despatch_id = await seed_qualifying_job(conn, suffix="SUCCESS")
    await conn.close()

    # 1. Preview page loads and indicates eligible
    preview_resp = await chief_quality_client.get(f"/certificates/preview/{job_id}?despatch_id={despatch_id}")
    assert preview_resp.status_code == HTTP_200_OK
    assert "QUALITY COMPLIANCE GATE PASSED" in preview_resp.text

    # 2. Issue Certificate
    issue_resp = await chief_quality_client.post(
        "/certificates/issue",
        data={"job_id": job_id, "despatch_id": despatch_id},
        follow_redirects=False,
    )
    assert issue_resp.status_code == HTTP_303_SEE_OTHER
    cert_detail_url = issue_resp.headers["location"]
    cert_id = cert_detail_url.split("/")[-1]

    # 3. Verify Database Record
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    row = await conn.fetchrow(
        "SELECT cert_no, sha256_hash, total_qc_checks, total_lab_tests, status FROM test_certificates WHERE id = $1::uuid;",
        uuid.UUID(cert_id),
    )
    assert row["cert_no"].startswith("TC-")
    assert len(row["sha256_hash"]) == 64
    assert row["total_qc_checks"] == 1
    assert row["total_lab_tests"] == 1
    assert row["status"] == "Issued"
    await conn.close()

    # 4. Download PDF
    download_resp = await chief_quality_client.get(f"/certificates/{cert_id}/download")
    assert download_resp.status_code == HTTP_200_OK
    assert download_resp.headers["content-type"] == "application/pdf"
    assert download_resp.content.startswith(b"%PDF-")


# ============================================================================
# 3. HARD QUALITY GATING TESTS (REFUSAL ON NON-CONFORMANCE)
# ============================================================================

@pytest.mark.asyncio
async def test_gate_blocks_zero_qc_checks(chief_quality_client):
    """
    Zero QC checks recorded -> 400 Bad Request.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id, _ = await seed_qualifying_job(conn, suffix="NOQC", include_qc=False, include_lab=True)
    await conn.close()

    resp = await chief_quality_client.post("/certificates/issue", data={"job_id": job_id}, follow_redirects=False)
    assert resp.status_code == HTTP_400_BAD_REQUEST
    assert "Zero QC checks recorded" in resp.text


@pytest.mark.asyncio
async def test_gate_blocks_zero_lab_tests(chief_quality_client):
    """
    Zero Lab tests recorded -> 400 Bad Request.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id, _ = await seed_qualifying_job(conn, suffix="NOLAB", include_qc=True, include_lab=False)
    await conn.close()

    resp = await chief_quality_client.post("/certificates/issue", data={"job_id": job_id}, follow_redirects=False)
    assert resp.status_code == HTTP_400_BAD_REQUEST
    assert "Zero Lab tests recorded" in resp.text


@pytest.mark.asyncio
async def test_gate_blocks_failing_qc_check(chief_quality_client):
    """
    QC Check failed -> 400 Bad Request.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id, _ = await seed_qualifying_job(conn, suffix="FAILQC", qc_pass=False)
    await conn.close()

    resp = await chief_quality_client.post("/certificates/issue", data={"job_id": job_id}, follow_redirects=False)
    assert resp.status_code == HTTP_400_BAD_REQUEST
    assert "Quality Hold" in resp.text or "QC checks failed" in resp.text


@pytest.mark.asyncio
async def test_gate_blocks_failing_lab_test(chief_quality_client):
    """
    Lab Test failed -> 400 Bad Request.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id, _ = await seed_qualifying_job(conn, suffix="FAILLAB", lab_pass=False)
    await conn.close()

    resp = await chief_quality_client.post("/certificates/issue", data={"job_id": job_id}, follow_redirects=False)
    assert resp.status_code == HTTP_400_BAD_REQUEST
    assert "Quality Hold" in resp.text or "Lab tests failed" in resp.text


@pytest.mark.asyncio
async def test_gate_blocks_unapproved_critical_lab_test(chief_quality_client):
    """
    Critical Lab Test not approved by QA -> 400 Bad Request.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id, _ = await seed_qualifying_job(conn, suffix="UNAPPR", lab_approved=False)
    await conn.close()

    resp = await chief_quality_client.post("/certificates/issue", data={"job_id": job_id}, follow_redirects=False)
    assert resp.status_code == HTTP_400_BAD_REQUEST
    assert "pending QA approval" in resp.text


@pytest.mark.asyncio
async def test_gate_blocks_job_on_quality_hold(chief_quality_client):
    """
    Job on QC hold -> 400 Bad Request.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id, _ = await seed_qualifying_job(conn, suffix="HOLD")
    # Insert an additional failing check that triggers job_on_hold
    hold_check_no = f"QC-HOLD-{uuid.uuid4().hex[:6].upper()}"
    await conn.execute(
        """
        INSERT INTO qc_checks (
            job_id, check_no, stage, parameter, unit, method, limit_type,
            spec_value, tolerance, actual, inspector_id
        ) VALUES (
            $1::uuid, $2, 'Loom State', 'Thickness', 'mm', 'ASTM D1777', 'minimum',
            1.5, 0.1, 0.8, $3::uuid
        );
        """,
        uuid.UUID(job_id),
        hold_check_no,
        uuid.UUID(TEST_USERS["line_inspector"]["id"]),
    )
    await conn.close()

    resp = await chief_quality_client.post("/certificates/issue", data={"job_id": job_id}, follow_redirects=False)
    assert resp.status_code == HTTP_400_BAD_REQUEST
    assert "currently on Quality Hold" in resp.text


# ============================================================================
# 4. DUPLICATE ACTIVE CERTIFICATE PREVENTION FOR SAME DESPATCH
# ============================================================================

@pytest.mark.asyncio
async def test_prevents_duplicate_active_certificate_on_same_despatch(chief_quality_client):
    """
    Proves that a single despatch note cannot have multiple active certificates.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id, despatch_id = await seed_qualifying_job(conn, suffix="DUP")
    await conn.close()

    # 1. First issuance succeeds
    resp1 = await chief_quality_client.post(
        "/certificates/issue",
        data={"job_id": job_id, "despatch_id": despatch_id},
        follow_redirects=False,
    )
    assert resp1.status_code == HTTP_303_SEE_OTHER

    # 2. Second issuance attempt on same despatch fails -> 400 Bad Request
    resp2 = await chief_quality_client.post(
        "/certificates/issue",
        data={"job_id": job_id, "despatch_id": despatch_id},
        follow_redirects=False,
    )
    assert resp2.status_code == HTTP_400_BAD_REQUEST
    assert "already been issued for this despatch note" in resp2.text


# ============================================================================
# 5. RBAC PERMISSIONS & REVOCATION WORKFLOW
# ============================================================================

@pytest.mark.asyncio
async def test_commercial_staff_can_download_but_cannot_issue_certificate(
    sales_client, chief_quality_client
):
    """
    Proves sales_executive is denied 403 on issuance (requires tests.release),
    but can view the register and download an issued certificate.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id, despatch_id = await seed_qualifying_job(conn, suffix="RBAC")
    await conn.close()

    # 1. Sales attempts issuance -> 403 Forbidden
    issue_resp = await sales_client.post(
        "/certificates/issue",
        data={"job_id": job_id, "despatch_id": despatch_id},
        follow_redirects=False,
    )
    assert issue_resp.status_code == HTTP_403_FORBIDDEN

    # 2. QA issues certificate
    qa_issue = await chief_quality_client.post(
        "/certificates/issue",
        data={"job_id": job_id, "despatch_id": despatch_id},
        follow_redirects=False,
    )
    assert qa_issue.status_code == HTTP_303_SEE_OTHER
    cert_id = qa_issue.headers["location"].split("/")[-1]

    # 3. Sales downloads issued certificate -> 200 OK
    dl_resp = await sales_client.get(f"/certificates/{cert_id}/download")
    assert dl_resp.status_code == HTTP_200_OK
    assert dl_resp.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_certificate_revocation_lifecycle(chief_quality_client):
    """
    Proves chief_quality can formally revoke an issued certificate with an audit reason.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id, despatch_id = await seed_qualifying_job(conn, suffix="REVOKE")
    await conn.close()

    # Issue
    issue_resp = await chief_quality_client.post(
        "/certificates/issue",
        data={"job_id": job_id, "despatch_id": despatch_id},
        follow_redirects=False,
    )
    cert_id = issue_resp.headers["location"].split("/")[-1]

    # Revoke
    rev_resp = await chief_quality_client.post(
        f"/certificates/{cert_id}/revoke",
        data={"reason": "Customer specification changed post-weaving. New testing required."},
        follow_redirects=False,
    )
    assert rev_resp.status_code == HTTP_303_SEE_OTHER

    # Verify DB
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    row = await conn.fetchrow("SELECT status, revocation_reason FROM test_certificates WHERE id = $1::uuid;", uuid.UUID(cert_id))
    assert row["status"] == "Revoked"
    assert "Customer specification changed" in row["revocation_reason"]
    await conn.close()


# ============================================================================
# 6. DATABASE RLS HARDENING & UNAUTHENTICATED ACCESS TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_database_rls_denies_unauthorized_user_zero_rows(chief_quality_client):
    """
    Proves that PostgreSQL Row Level Security (FORCE RLS) directly denies access:
    A user holding none of tests.read / despatch.read / qc.read / jobs.read
    (such as hr_officer or an unassigned legacy profile) queries test_certificates
    under SET LOCAL ROLE authenticated and receives 0 rows.
    """
    import json
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id, despatch_id = await seed_qualifying_job(conn, suffix="RLSTEST")
    await conn.close()

    # 1. Issue a valid certificate as chief_quality
    issue_resp = await chief_quality_client.post(
        "/certificates/issue",
        data={"job_id": job_id, "despatch_id": despatch_id},
        follow_redirects=False,
    )
    assert issue_resp.status_code == HTTP_303_SEE_OTHER

    # 2. Query directly via PostgreSQL with claims for an unauthorized role (hr_officer)
    hr_user_id = TEST_USERS["hr_officer"]["id"]
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true);",
                json.dumps({"sub": hr_user_id, "role": "authenticated"}),
            )
            # Must return 0 rows because HR officer has no tests.read/despatch.read/qc.read/jobs.read
            rows = await conn.fetch("SELECT * FROM test_certificates;")
            assert len(rows) == 0, f"Database RLS leaked test_certificates to HR officer! Got {len(rows)} rows."

            # Must reject direct INSERT via PostgreSQL RLS
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    """
                    INSERT INTO test_certificates (cert_no, job_id, issued_by, issued_at, sha256_hash, total_qc_checks, total_lab_tests)
                    VALUES ('TC-RLS-HACK-01', $1::uuid, $2::uuid, now(), 'dummy_hash', 1, 1);
                    """,
                    uuid.UUID(job_id),
                    uuid.UUID(hr_user_id),
                )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_unauthorized_role_api_denied_viewing_certificates(hr_client):
    """
    Proves that a role without reading permissions (hr_officer) receives HTTP 403 Forbidden
    when attempting to list certificates.
    """
    resp = await hr_client.get("/certificates")
    assert resp.status_code == HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_certificates_routes_reject_unauthenticated(anonymous_client):
    """
    Proves unauthenticated requests receive 401.
    """
    resp = await anonymous_client.get("/certificates")
    assert resp.status_code == HTTP_401_UNAUTHORIZED

