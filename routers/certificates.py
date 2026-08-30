"""
Test Certificates Router — SNM Works
=============================================================================
Conformance Test Certificates & Quality Analysis (CoA) PDF Generator.

Permissions:
  - tests.release / tests.approve: chief_quality, qa_manager (Issuance Authority)
  - tests.read / despatch.read: quality, operations, commercial (View & Download)

Gating & Compliance Rules:
  - 100% PASS required on all QC Checks and Lab Tests.
  - Zero tolerance on missing tests (both QC and Lab testing required).
  - All critical lab tests must be formally approved.
  - Blocks issuance if job is on QC hold.
  - Thread-safe human-facing certificate sequence: TC-YYYY-XXXX.
  - Generates tamper-evident SHA-256 digest logged in test_certificates and audit_log.
=============================================================================
"""

import asyncio
from datetime import datetime
import hashlib
import io
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from asyncpg.exceptions import CheckViolationError, UniqueViolationError
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)

from auth.dependencies import current_user, require
from database import get_db
from textiles.pdf_certificate import generate_certificate_pdf

router = APIRouter(prefix="/certificates", tags=["Test Certificates & Compliance"])
templates = Jinja2Templates(directory="templates")


async def generate_cert_number(conn: asyncpg.Connection) -> str:
    """
    Generates a thread-safe sequence-based certificate number: TC-YYYY-XXXX.
    """
    year = datetime.now().year
    seq_val = await conn.fetchval("SELECT nextval('test_certificate_seq');")
    return f"TC-{year}-{int(seq_val):04d}"


async def fetch_certificate_dataset(
    conn: asyncpg.Connection,
    job_id: uuid.UUID,
    despatch_id: Optional[uuid.UUID] = None,
    cert_no: Optional[str] = None,
    issued_at: Optional[datetime] = None,
    issuer_name: Optional[str] = None,
    sha256_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Assembles the complete dataset for a job, construction, QC checks, and Lab tests.
    """
    # 1. Job & Customer & Construction data
    job = await conn.fetchrow(
        """
        SELECT 
            j.id::text,
            j.job_no,
            j.product,
            j.spec,
            j.po_ref,
            j.qty_ordered,
            j.unit,
            j.delivery_due,
            j.status AS job_status,
            cust.name AS customer_name,
            c.spec_no AS const_spec_no,
            c.weave AS const_weave,
            c.width_mm AS const_width_mm,
            c.warp_denier AS const_warp_denier,
            c.weft_denier AS const_weft_denier,
            c.warp_ends AS const_warp_ends,
            c.picks_per_cm AS const_picks_per_cm
        FROM jobs j
        LEFT JOIN customers cust ON j.customer_id = cust.id
        LEFT JOIN constructions c ON j.construction_id = c.id
        WHERE j.id = $1::uuid;
        """,
        job_id,
    )
    if not job:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job card not found.")

    # 2. Despatch data (if linked)
    despatch_data = None
    if despatch_id:
        despatch_data = await conn.fetchrow(
            """
            SELECT despatch_no, invoice_no, despatched_on, qty, unit, rolls, gross_wt
            FROM despatch
            WHERE id = $1::uuid;
            """,
            despatch_id,
        )

    # 3. QC Checks
    qc_rows = await conn.fetch(
        """
        SELECT 
            check_no, checked_on, stage, family, parameter, unit,
            method, limit_type, spec_value, tolerance, upper_limit,
            actual, verdict
        FROM qc_checks
        WHERE job_id = $1::uuid
        ORDER BY created_at ASC;
        """,
        job_id,
    )

    # 4. Lab Tests
    lab_rows = await conn.fetch(
        """
        SELECT 
            lt.test_id, lt.tested_on, lt.test_type, lt.standard, lt.lab,
            lt.requirement, lt.result, lt.unit, lt.parameter, lt.limit_type,
            lt.spec_value, lt.tolerance, lt.upper_limit, lt.is_critical,
            lt.specimens, lt.verdict, lt.approved_by, lt.approved_at,
            p_ap.full_name AS approver_name
        FROM lab_tests lt
        LEFT JOIN profiles p_ap ON lt.approved_by = p_ap.id
        WHERE lt.job_id = $1::uuid
        ORDER BY lt.created_at ASC;
        """,
        job_id,
    )

    # Find the most senior QA approver name from lab tests
    approver_name = "QA Manager / Chief Quality Officer"
    for lt in lab_rows:
        if lt["approver_name"]:
            approver_name = lt["approver_name"]
            break

    construction_dict = None
    if job["const_spec_no"]:
        construction_dict = {
            "spec_no": job["const_spec_no"],
            "weave": job["const_weave"],
            "width_mm": float(job["const_width_mm"] or 0),
            "warp_denier": float(job["const_warp_denier"] or 0),
            "weft_denier": float(job["const_weft_denier"] or 0),
            "warp_ends": job["const_warp_ends"],
            "picks_per_cm": float(job["const_picks_per_cm"] or 0),
        }

    dataset = {
        "job_id": str(job_id),
        "job_no": job["job_no"],
        "product": job["product"],
        "spec": job["spec"] or "MIL-W-4088K",
        "po_ref": job["po_ref"] or "—",
        "qty": float(job["qty_ordered"] or 0) if not despatch_data else float(despatch_data["qty"] or 0),
        "unit": job["unit"] if not despatch_data else despatch_data["unit"],
        "customer_name": job["customer_name"] or "Ordnance Factory Kanpur / Internal",
        "cert_no": cert_no or "DRAFT-PREVIEW",
        "issued_at": issued_at.strftime("%Y-%m-%d %H:%M UTC") if issued_at else datetime.now().strftime("%Y-%m-%d"),
        "issuer_name": issuer_name or "Quality Assurance Laboratory",
        "approver_name": approver_name,
        "sha256_hash": sha256_hash or "",
        "despatch_no": despatch_data["despatch_no"] if despatch_data else None,
        "invoice_no": despatch_data["invoice_no"] if despatch_data else None,
        "rolls": despatch_data["rolls"] if despatch_data else None,
        "gross_wt": float(despatch_data["gross_wt"]) if despatch_data and despatch_data["gross_wt"] else None,
        "construction": construction_dict,
        "qc_checks": [dict(q) for q in qc_rows],
        "lab_tests": [dict(l) for l in lab_rows],
    }
    return dataset


# ============================================================================
# 1. CERTIFICATE REGISTER (LIST)
# ============================================================================

@router.get("", response_class=HTMLResponse)
async def list_certificates(
    request: Request,
    search: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(current_user),
):
    """
    GET /certificates — Register of issued test certificates and issuance readiness.
    Accessible to Quality, Despatch, Commercial, and Operations staff.
    """
    can_read = await conn.fetchval(
        "SELECT auth_can('tests', 'read') OR auth_can('despatch', 'read') OR auth_can('jobs', 'read');"
    )
    if not can_read:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Your roles do not permit reading test certificates.")
    # 1. Fetch issued certificates
    query = """
        SELECT 
            tc.id::text,
            tc.cert_no,
            tc.job_id::text,
            j.job_no,
            j.product,
            cust.name AS customer_name,
            d.despatch_no,
            tc.issued_at,
            tc.issued_by::text,
            p.full_name AS issuer_name,
            tc.sha256_hash,
            tc.total_qc_checks,
            tc.total_lab_tests,
            tc.status
        FROM test_certificates tc
        JOIN jobs j ON tc.job_id = j.id
        LEFT JOIN customers cust ON j.customer_id = cust.id
        LEFT JOIN despatch d ON tc.despatch_id = d.id
        LEFT JOIN profiles p ON tc.issued_by = p.id
        WHERE 1=1
    """
    params = []
    if search:
        query += " AND (tc.cert_no ILIKE $1 OR j.job_no ILIKE $1 OR j.product ILIKE $1 OR cust.name ILIKE $1)"
        params.append(f"%{search.strip()}%")

    query += " ORDER BY tc.issued_at DESC;"
    rows = await conn.fetch(query, *params)

    # 2. Fetch jobs ready for certificate issuance (passing QC + Lab tests)
    ready_jobs = await conn.fetch(
        """
        SELECT 
            j.id::text,
            j.job_no,
            j.product,
            cust.name AS customer_name,
            COUNT(DISTINCT qc.id) AS qc_count,
            COUNT(DISTINCT lt.id) AS lab_count
        FROM jobs j
        LEFT JOIN customers cust ON j.customer_id = cust.id
        JOIN qc_checks qc ON j.id = qc.job_id
        JOIN lab_tests lt ON j.id = lt.job_id
        WHERE j.status <> 'Cancelled'
          AND NOT job_on_hold(j.id)
        GROUP BY j.id, j.job_no, j.product, cust.name
        HAVING COUNT(DISTINCT qc.id) > 0 
           AND COUNT(DISTINCT lt.id) > 0
           AND bool_and(qc.verdict = 'PASS')
           AND bool_and(lt.verdict = 'PASS')
        ORDER BY j.created_at DESC
        LIMIT 10;
        """
    )

    can_issue = await conn.fetchval("SELECT auth_can('tests', 'release');")

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="certificates/list.html",
        context={
            "user": user_info,
            "certificates": [dict(r) for r in rows],
            "ready_jobs": [dict(rj) for rj in ready_jobs],
            "total_count": len(rows),
            "search": search or "",
            "can_issue": bool(can_issue),
            "current_page": "certificates",
            "current_func": "QUA",
        },
    )


# ============================================================================
# 2. CERTIFICATE PREVIEW & QUALITY GATING CHECK (GET)
# ============================================================================

@router.get("/preview/{job_id}", response_class=HTMLResponse)
async def preview_certificate(
    job_id: str,
    despatch_id: Optional[str] = None,
    request: Request = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(current_user),
):
    """
    GET /certificates/preview/{job_id} — Pre-issuance compliance and quality verification.
    """
    can_read = await conn.fetchval(
        "SELECT auth_can('tests', 'read') OR auth_can('despatch', 'read') OR auth_can('jobs', 'read');"
    )
    if not can_read:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Your roles do not permit reading test certificates.")

    job_uuid = uuid.UUID(job_id)
    despatch_uuid = uuid.UUID(despatch_id) if despatch_id else None

    # Check QC hold
    is_on_hold = await conn.fetchval("SELECT job_on_hold($1::uuid);", job_uuid)

    dataset = await fetch_certificate_dataset(
        conn=conn,
        job_id=job_uuid,
        despatch_id=despatch_uuid,
        issuer_name=user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    )

    # Evaluate Gating Conditions
    qc_checks = dataset["qc_checks"]
    lab_tests = dataset["lab_tests"]

    has_qc = len(qc_checks) > 0
    has_lab = len(lab_tests) > 0
    all_qc_pass = has_qc and all(q["verdict"] == "PASS" for q in qc_checks)
    all_lab_pass = has_lab and all(lt["verdict"] == "PASS" for lt in lab_tests)
    all_lab_approved = has_lab and all(lt["approved_by"] is not None for lt in lab_tests if lt["is_critical"])

    is_eligible = (
        not is_on_hold
        and has_qc
        and has_lab
        and all_qc_pass
        and all_lab_pass
        and all_lab_approved
    )

    can_issue = await conn.fetchval("SELECT auth_can('tests', 'release');")

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="certificates/preview.html",
        context={
            "user": user_info,
            "cert": dataset,
            "is_on_hold": is_on_hold,
            "has_qc": has_qc,
            "has_lab": has_lab,
            "all_qc_pass": all_qc_pass,
            "all_lab_pass": all_lab_pass,
            "all_lab_approved": all_lab_approved,
            "is_eligible": is_eligible,
            "can_issue": bool(can_issue),
            "despatch_id": despatch_id or "",
            "current_page": "certificates",
            "current_func": "QUA",
        },
    )


# ============================================================================
# 3. ISSUE CERTIFICATE OF CONFORMANCE (POST)
# ============================================================================

@router.post("/issue", response_class=HTMLResponse)
async def issue_certificate(
    request: Request,
    job_id: str = Form(...),
    despatch_id: Optional[str] = Form(None),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tests", "release")),
):
    """
    POST /certificates/issue — Issues a formal Conformance Certificate.
    Strictly gated on 100% PASS rate and QC hold release.
    """
    issuer_id = user.get("id")
    if not issuer_id:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    job_uuid = uuid.UUID(job_id)
    despatch_uuid = uuid.UUID(despatch_id) if despatch_id and despatch_id.strip() else None

    # 1. Quality Hold Check
    is_on_hold = await conn.fetchval("SELECT job_on_hold($1::uuid);", job_uuid)
    if is_on_hold:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Cannot issue certificate: This job is currently on Quality Hold due to non-conforming test results.",
        )

    # 2. Check Despatch Duplicate Active Certificate
    if despatch_uuid:
        existing_cert = await conn.fetchrow(
            """
            SELECT cert_no FROM test_certificates 
            WHERE despatch_id = $1::uuid AND status = 'Issued';
            """,
            despatch_uuid,
        )
        if existing_cert:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=f"An active certificate ({existing_cert['cert_no']}) has already been issued for this despatch note.",
            )

    # 3. Fetch Dataset and Validate Gating
    issuer_name = user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email")
    dataset = await fetch_certificate_dataset(conn=conn, job_id=job_uuid, despatch_id=despatch_uuid, issuer_name=issuer_name)

    qc_checks = dataset["qc_checks"]
    lab_tests = dataset["lab_tests"]

    if not qc_checks:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Cannot issue certificate: Zero QC checks recorded for this job.")

    if not lab_tests:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Cannot issue certificate: Zero Lab tests recorded for this job.")

    failing_qc = [q["check_no"] for q in qc_checks if q["verdict"] != "PASS"]
    if failing_qc:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Cannot issue certificate: The following QC checks failed: {', '.join(failing_qc)}.",
        )

    failing_lab = [l["test_id"] for l in lab_tests if l["verdict"] != "PASS"]
    if failing_lab:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Cannot issue certificate: The following Lab tests failed: {', '.join(failing_lab)}.",
        )

    unapproved_lab = [l["test_id"] for l in lab_tests if l["is_critical"] and not l["approved_by"]]
    if unapproved_lab:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Cannot issue certificate: The following critical lab tests are pending QA approval: {', '.join(unapproved_lab)}.",
        )

    # 4. Generate Certificate Number & PDF
    cert_no = await generate_cert_number(conn)
    issued_at = datetime.now()
    dataset["cert_no"] = cert_no
    dataset["issued_at"] = issued_at.strftime("%Y-%m-%d %H:%M UTC")

    pdf_bytes, sha256_digest = generate_certificate_pdf(dataset)

    # 5. Save to database
    cert_id = await conn.fetchval(
        """
        INSERT INTO test_certificates (
            cert_no, job_id, despatch_id, issued_by, issued_at,
            sha256_hash, total_qc_checks, total_lab_tests, status
        ) VALUES (
            $1, $2::uuid, $3, $4::uuid, $5,
            $6, $7, $8, 'Issued'
        )
        RETURNING id::text;
        """,
        cert_no,
        job_uuid,
        despatch_uuid,
        uuid.UUID(issuer_id),
        issued_at,
        sha256_digest,
        len(qc_checks),
        len(lab_tests),
    )

    return RedirectResponse(url=f"/certificates/{cert_id}", status_code=HTTP_303_SEE_OTHER)


# ============================================================================
# 4. CERTIFICATE DETAIL VIEW (GET)
# ============================================================================

@router.get("/{cert_id}", response_class=HTMLResponse)
async def certificate_detail(
    cert_id: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(current_user),
):
    """
    GET /certificates/{cert_id} — View issued certificate metadata and download link.
    """
    can_read = await conn.fetchval(
        "SELECT auth_can('tests', 'read') OR auth_can('despatch', 'read') OR auth_can('jobs', 'read');"
    )
    if not can_read:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Your roles do not permit reading test certificates.")

    cert = await conn.fetchrow(
        """
        SELECT 
            tc.id::text,
            tc.cert_no,
            tc.job_id::text,
            j.job_no,
            j.product,
            j.spec,
            cust.name AS customer_name,
            tc.despatch_id::text,
            d.despatch_no,
            tc.issued_at,
            tc.issued_by::text,
            p.full_name AS issuer_name,
            tc.sha256_hash,
            tc.total_qc_checks,
            tc.total_lab_tests,
            tc.status,
            tc.revoked_at,
            tc.revocation_reason
        FROM test_certificates tc
        JOIN jobs j ON tc.job_id = j.id
        LEFT JOIN customers cust ON j.customer_id = cust.id
        LEFT JOIN despatch d ON tc.despatch_id = d.id
        LEFT JOIN profiles p ON tc.issued_by = p.id
        WHERE tc.id = $1::uuid;
        """,
        uuid.UUID(cert_id),
    )
    if not cert:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Test certificate not found.")

    can_revoke = await conn.fetchval("SELECT auth_can('tests', 'release');")

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="certificates/detail.html",
        context={
            "user": user_info,
            "cert": dict(cert),
            "can_revoke": bool(can_revoke),
            "current_page": "certificates",
            "current_func": "QUA",
        },
    )


# ============================================================================
# 5. STREAM PDF DOWNLOAD (GET)
# ============================================================================

@router.get("/{cert_id}/download")
async def download_certificate_pdf(
    cert_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(current_user),
):
    """
    GET /certificates/{cert_id}/download — Streams the binary PDF test certificate.
    """
    can_read = await conn.fetchval(
        "SELECT auth_can('tests', 'read') OR auth_can('despatch', 'read') OR auth_can('jobs', 'read');"
    )
    if not can_read:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Your roles do not permit reading test certificates.")
    cert = await conn.fetchrow(
        """
        SELECT 
            tc.id, tc.cert_no, tc.job_id, tc.despatch_id, tc.issued_at,
            tc.sha256_hash, tc.status, p.full_name AS issuer_name
        FROM test_certificates tc
        LEFT JOIN profiles p ON tc.issued_by = p.id
        WHERE tc.id = $1::uuid;
        """,
        uuid.UUID(cert_id),
    )
    if not cert:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Test certificate not found.")

    dataset = await fetch_certificate_dataset(
        conn=conn,
        job_id=cert["job_id"],
        despatch_id=cert["despatch_id"],
        cert_no=cert["cert_no"],
        issued_at=cert["issued_at"],
        issuer_name=cert["issuer_name"],
        sha256_hash=cert["sha256_hash"],
    )

    pdf_bytes, _ = generate_certificate_pdf(dataset)
    filename = f"{cert['cert_no']}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


# ============================================================================
# 6. REVOKE CERTIFICATE (POST)
# ============================================================================

@router.post("/{cert_id}/revoke", response_class=HTMLResponse)
async def revoke_certificate(
    cert_id: str,
    reason: str = Form(...),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tests", "release")),
):
    """
    POST /certificates/{cert_id}/revoke — Revokes an issued certificate.
    """
    if len(reason.strip()) < 10:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Revocation reason must be at least 10 characters explaining the non-conformance.",
        )

    revoker_id = user.get("id")
    await conn.execute(
        """
        UPDATE test_certificates
        SET 
            status = 'Revoked',
            revoked_by = $1::uuid,
            revoked_at = NOW(),
            revocation_reason = $2
        WHERE id = $3::uuid;
        """,
        uuid.UUID(revoker_id),
        reason.strip(),
        uuid.UUID(cert_id),
    )

    return RedirectResponse(url=f"/certificates/{cert_id}", status_code=HTTP_303_SEE_OTHER)
