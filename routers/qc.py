from datetime import date, datetime
import logging
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
)
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import get_db
from auth.dependencies import current_user, require
from services.qc_service import record_qc_check

logger = logging.getLogger("snm_works.qc")
router = APIRouter(prefix="/qc", tags=["QC"])
templates = Jinja2Templates(directory="templates")

INSPECTION_STAGES = [
    "On-Loom Inspection",
    "Greige Inspection",
    "Dyeing / Scouring",
    "Finishing / Heat Setting",
    "Final Inspection",
    "Incoming Yarn Inspection",
]

LIMIT_KINDS = ["nominal", "minimum", "maximum", "range"]


async def get_next_qc_check_no(conn: asyncpg.Connection) -> str:
    """
    Auto-generates next check_no using:
    select coalesce(max(substring(check_no from 'Q-(\\d+)')::int), 0) + 1 from qc_checks
    """
    seq_val = await conn.fetchval(
        """
        SELECT COALESCE(
          MAX(SUBSTRING(check_no FROM 'Q-(\\d+)')::int),
          0
        ) + 1 AS next_seq
        FROM qc_checks
        """
    )
    next_seq = int(seq_val) if seq_val is not None else 1
    return f"Q-{next_seq:04d}"


async def fetch_active_jobs(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    """
    Fetches list of active jobs from PostgreSQL for the QC check dropdown.
    """
    rows = await conn.fetch(
        """
        SELECT id::text, job_no, product, status
        FROM jobs
        WHERE status != 'Cancelled'
        ORDER BY raised_on DESC, job_no DESC
        LIMIT 100
        """
    )
    return [dict(r) for r in rows]


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def list_qc_checks(
    request: Request,
    job_no: Optional[str] = None,
    verdict: Optional[str] = None,
    q: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("qc", "read")),
):
    """
    GET /qc -- list all checks, filterable by job_no and verdict.
    Enforces auth_can('qc', 'read') and RLS.
    Verdict is retrieved strictly from the database generated column.
    """
    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    query = """
        SELECT 
            qc.id::text as id,
            qc.check_no,
            qc.job_id::text as job_id,
            j.job_no,
            j.product as job_product,
            qc.checked_on,
            qc.stage,
            qc.family,
            qc.parameter,
            qc.unit,
            qc.method,
            qc.limit_type,
            qc.spec_value,
            qc.tolerance,
            qc.upper_limit,
            qc.actual,
            qc.defect_code,
            qc.action_taken,
            qc.verdict,
            p.full_name as inspector_name,
            qc.created_at
        FROM qc_checks qc
        LEFT JOIN jobs j ON j.id = qc.job_id
        LEFT JOIN profiles p ON p.id = qc.inspector_id
        WHERE 1=1
    """
    params: List[Any] = []
    idx = 1

    if job_no and job_no.strip():
        query += f" AND j.job_no ILIKE ${idx}"
        params.append(f"%{job_no.strip()}%")
        idx += 1

    if verdict and verdict.strip() and verdict.lower() != "all":
        if verdict.upper() == "PENDING":
            query += " AND qc.verdict IS NULL"
        else:
            query += f" AND qc.verdict = ${idx}"
            params.append(verdict.strip().upper())
            idx += 1

    if q and q.strip():
        search_term = f"%{q.strip()}%"
        query += f" AND (qc.check_no ILIKE ${idx} OR qc.parameter ILIKE ${idx} OR qc.stage ILIKE ${idx} OR j.job_no ILIKE ${idx})"
        params.append(search_term)
        idx += 1

    query += " ORDER BY qc.checked_on DESC, qc.created_at DESC"

    rows = await conn.fetch(query, *params)
    checks_list = [dict(r) for r in rows]

    return templates.TemplateResponse(
        request=request,
        name="qc/list.html",
        context={
            "user": user_info,
            "checks": checks_list,
            "job_no_filter": job_no or "",
            "verdict_filter": verdict or "all",
            "q": q or "",
            "current_page": "qc",
            "current_func": "QUA",
        }
    )


@router.get("/new", response_class=HTMLResponse)
async def new_qc_check_form(
    request: Request,
    job_id: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("qc", "create")),
):
    """
    GET /qc/new -- create form, accepts optional ?job_id= to pre-fill job.
    Enforces auth_can('qc', 'create').
    """
    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    preview_check_no = await get_next_qc_check_no(conn)
    jobs = await fetch_active_jobs(conn)

    return templates.TemplateResponse(
        request=request,
        name="qc/form.html",
        context={
            "user": user_info,
            "is_edit": False,
            "check": {
                "check_no": preview_check_no,
                "job_id": job_id or "",
                "checked_on": date.today().isoformat(),
                "stage": "On-Loom Inspection",
                "limit_type": "nominal",
                "spec_value": 0.0,
                "tolerance": None,
                "upper_limit": None,
                "actual": None,
            },
            "selected_job_id": job_id or "",
            "jobs": jobs,
            "stages": INSPECTION_STAGES,
            "limit_kinds": LIMIT_KINDS,
            "current_page": "qc",
            "current_func": "QUA",
        }
    )


@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_qc_check(
    request: Request,
    job_id: Optional[str] = Form(None),
    checked_on: Optional[str] = Form(None),
    stage: str = Form("On-Loom Inspection"),
    family: Optional[str] = Form(None),
    parameter: str = Form(...),
    unit: Optional[str] = Form(None),
    method: Optional[str] = Form(None),
    limit_type: str = Form("nominal"),
    spec_value: float = Form(...),
    tolerance: Optional[float] = Form(None),
    upper_limit: Optional[float] = Form(None),
    actual: Optional[float] = Form(None),
    defect_code: Optional[str] = Form(None),
    action_taken: Optional[str] = Form(None),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("qc", "create")),
):
    """
    POST /qc -- insert new inspection record.
    - Requires authenticated user holding 'qc.create' permission.
    - inspector_id is strictly resolved from user["id"] (UUID).
    - check_no handles concurrency by catching unique constraint collisions and retrying.
    - verdict is computed strictly by PostgreSQL generated column (never sent in INSERT).
    """
    data = {
        "job_id": job_id,
        "checked_on": checked_on,
        "stage": stage,
        "family": family,
        "parameter": parameter,
        "unit": unit,
        "method": method,
        "limit_type": limit_type,
        "spec_value": spec_value,
        "tolerance": tolerance,
        "upper_limit": upper_limit,
        "actual": actual,
        "defect_code": defect_code,
        "action_taken": action_taken,
    }
    result = await record_qc_check(conn, user["id"], data)
    new_qc_id = result["id"]

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        response = Response(status_code=HTTP_200_OK)
        response.headers["HX-Redirect"] = f"/qc/{new_qc_id}"
        return response

    return RedirectResponse(url=f"/qc/{new_qc_id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/{check_id}", response_class=HTMLResponse)
async def qc_check_detail(
    request: Request,
    check_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("qc", "read")),
):
    """
    GET /qc/{check_id} -- detail page displaying inspection specification,
    actual reading, and PostgreSQL computed verdict badge.
    """
    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    row = await conn.fetchrow(
        """
        SELECT 
            qc.id::text as id,
            qc.check_no,
            qc.job_id::text as job_id,
            j.job_no,
            j.product as job_product,
            j.status as job_status,
            c.name as customer_name,
            qc.checked_on,
            qc.stage,
            qc.family,
            qc.parameter,
            qc.unit,
            qc.method,
            qc.limit_type,
            qc.spec_value,
            qc.tolerance,
            qc.upper_limit,
            qc.actual,
            qc.defect_code,
            qc.action_taken,
            qc.verdict,
            p.full_name as inspector_name,
            qc.created_at
        FROM qc_checks qc
        LEFT JOIN jobs j ON j.id = qc.job_id
        LEFT JOIN customers c ON c.id = j.customer_id
        LEFT JOIN profiles p ON p.id = qc.inspector_id
        WHERE qc.id::text = $1 OR qc.check_no = $1
        """,
        check_id,
    )

    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="QC Check record not found")

    check_data = dict(row)

    return templates.TemplateResponse(
        request=request,
        name="qc/detail.html",
        context={
            "user": user_info,
            "check": check_data,
            "current_page": "qc",
            "current_func": "QUA",
        }
    )


@router.get("/{check_id}/edit", response_class=HTMLResponse)
async def edit_qc_check_form(
    request: Request,
    check_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("qc", "update")),
):
    """
    GET /qc/{check_id}/edit -- render edit form populated with current readings.
    Requires 'qc.update' permission. Never allows editing of verdict directly.
    """
    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    row = await conn.fetchrow(
        """
        SELECT 
            qc.id::text as id, qc.check_no, qc.job_id::text as job_id,
            j.job_no, j.product as job_product, qc.checked_on,
            qc.stage, qc.family, qc.parameter, qc.unit, qc.method,
            qc.limit_type, qc.spec_value, qc.tolerance, qc.upper_limit,
            qc.actual, qc.defect_code, qc.action_taken, qc.verdict
        FROM qc_checks qc
        LEFT JOIN jobs j ON j.id = qc.job_id
        WHERE qc.id::text = $1 OR qc.check_no = $1
        """,
        check_id,
    )

    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="QC Check record not found")

    check_data = dict(row)
    jobs = await fetch_active_jobs(conn)

    return templates.TemplateResponse(
        request=request,
        name="qc/form.html",
        context={
            "user": user_info,
            "is_edit": True,
            "check": check_data,
            "selected_job_id": check_data.get("job_id") or "",
            "jobs": jobs,
            "stages": INSPECTION_STAGES,
            "limit_kinds": LIMIT_KINDS,
            "current_page": "qc",
            "current_func": "QUA",
        }
    )


@router.post("/{check_id}/update", response_class=HTMLResponse)
async def update_qc_check(
    request: Request,
    check_id: str,
    job_id: Optional[str] = Form(None),
    checked_on: Optional[str] = Form(None),
    stage: str = Form("On-Loom Inspection"),
    family: Optional[str] = Form(None),
    parameter: str = Form(...),
    unit: Optional[str] = Form(None),
    method: Optional[str] = Form(None),
    limit_type: str = Form("nominal"),
    spec_value: float = Form(...),
    tolerance: Optional[float] = Form(None),
    upper_limit: Optional[float] = Form(None),
    actual: Optional[float] = Form(None),
    defect_code: Optional[str] = Form(None),
    action_taken: Optional[str] = Form(None),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("qc", "update")),
):
    """
    POST /qc/{check_id}/update -- update reading and parameters only, NEVER verdict.
    PostgreSQL automatically re-evaluates the stored generated verdict column.
    """
    parsed_date = date.today()
    if checked_on and checked_on.strip():
        try:
            parsed_date = datetime.strptime(checked_on.strip(), "%Y-%m-%d").date()
        except ValueError:
            parsed_date = date.today()

    clean_stage = stage.strip() if stage else "On-Loom Inspection"
    clean_family = family.strip() if family else None
    clean_parameter = parameter.strip()
    clean_unit = unit.strip() if unit else None
    clean_method = method.strip() if method else None
    clean_limit_type = limit_type.strip().lower() if limit_type else "nominal"
    clean_defect_code = defect_code.strip() if defect_code else None
    clean_action_taken = action_taken.strip() if action_taken else None

    resolved_job_id = None
    if job_id and job_id.strip():
        try:
            resolved_job_id = uuid.UUID(job_id.strip())
        except (ValueError, TypeError):
            resolved_job_id = None

    row = await conn.fetchrow(
        """
        UPDATE qc_checks
        SET job_id = $1,
            checked_on = $2,
            stage = $3,
            family = $4,
            parameter = $5,
            unit = $6,
            method = $7,
            limit_type = $8::limit_kind,
            spec_value = $9,
            tolerance = $10,
            upper_limit = $11,
            actual = $12,
            defect_code = $13,
            action_taken = $14
        WHERE id::text = $15 OR check_no = $15
        RETURNING id::text, verdict
        """,
        resolved_job_id,
        parsed_date,
        clean_stage,
        clean_family,
        clean_parameter,
        clean_unit,
        clean_method,
        clean_limit_type,
        spec_value,
        tolerance,
        upper_limit,
        actual,
        clean_defect_code,
        clean_action_taken,
        check_id,
    )

    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="QC Check record not found")

    actual_uuid = row["id"]

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        response = Response(status_code=HTTP_200_OK)
        response.headers["HX-Redirect"] = f"/qc/{actual_uuid}"
        return response

    return RedirectResponse(url=f"/qc/{actual_uuid}", status_code=HTTP_303_SEE_OTHER)
