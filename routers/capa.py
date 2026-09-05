import asyncio
from datetime import date, datetime
import logging
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from asyncpg.exceptions import CheckViolationError, UniqueViolationError
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
    HTTP_503_SERVICE_UNAVAILABLE,
)
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import get_db
from auth.dependencies import current_user, require

logger = logging.getLogger("snm_works.capa")
router = APIRouter(prefix="/capa", tags=["CAPA"])
templates = Jinja2Templates(directory="templates")

STATUS_STAGES = [
    "Open",
    "Investigating",
    "Action Taken",
    "Under Verification",
    "Closed",
    "Cancelled",
]

SOURCES = [
    "QC Check",
    "Lab Test",
    "Customer Complaint",
    "Internal Audit",
    "Process Deviation",
    "Supplier Non-Conformance",
    "Other",
]


async def get_next_capa_no(conn: asyncpg.Connection) -> str:
    """
    Computes the next sequence number in the format CAPA-0001, CAPA-0002, etc.
    """
    seq_val = await conn.fetchval(
        """
        SELECT COALESCE(
          MAX(SUBSTRING(capa_no FROM 'CAPA-(\\d+)')::int),
          0
        ) + 1 AS next_seq
        FROM capa
        WHERE capa_no LIKE 'CAPA-%'
        """
    )
    next_seq = int(seq_val) if seq_val is not None else 1
    return f"CAPA-{next_seq:04d}"


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def list_capas(
    request: Request,
    status_filter: Optional[str] = None,
    source_filter: Optional[str] = None,
    q: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("capa", "read")),
):
    """
    GET /capa -- List all CAPA records ordered by raised_on DESC, capa_no DESC.
    """
    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    query = """
        SELECT 
            c.id::text as id,
            c.capa_no,
            c.raised_on,
            c.source,
            c.reference,
            c.job_id::text as job_id,
            j.job_no,
            j.product as job_product,
            c.problem,
            c.root_cause,
            c.correction,
            c.preventive,
            c.owner_id::text as owner_id,
            p_owner.full_name as owner_name,
            c.raised_by::text as raised_by,
            p_raiser.full_name as raiser_name,
            c.due_date,
            c.status,
            c.verified_by::text as verified_by,
            p_verifier.full_name as verifier_name,
            c.verified_at,
            c.closed_at,
            c.created_at
        FROM capa c
        LEFT JOIN jobs j ON j.id = c.job_id
        LEFT JOIN profiles p_owner ON p_owner.id = c.owner_id
        LEFT JOIN profiles p_raiser ON p_raiser.id = c.raised_by
        LEFT JOIN profiles p_verifier ON p_verifier.id = c.verified_by
        WHERE 1=1
    """
    params: List[Any] = []
    idx = 1

    if status_filter and status_filter.strip() and status_filter.lower() != "all":
        query += f" AND c.status = ${idx}"
        params.append(status_filter.strip())
        idx += 1

    if source_filter and source_filter.strip() and source_filter.lower() != "all":
        query += f" AND c.source = ${idx}"
        params.append(source_filter.strip())
        idx += 1

    if q and q.strip():
        search_term = f"%{q.strip()}%"
        query += f" AND (c.capa_no ILIKE ${idx} OR c.problem ILIKE ${idx} OR j.job_no ILIKE ${idx} OR c.reference ILIKE ${idx})"
        params.append(search_term)
        idx += 1

    query += " ORDER BY c.raised_on DESC, c.capa_no DESC"

    rows = await conn.fetch(query, *params)
    today = date.today()
    capa_list = []
    for r in rows:
        item = dict(r)
        d_date = item.get("due_date")
        item["is_overdue"] = False
        if d_date and d_date < today and item.get("status") not in ("Closed", "Cancelled"):
            item["is_overdue"] = True
        capa_list.append(item)

    return templates.TemplateResponse(
        request=request,
        name="capa/list.html",
        context={
            "user": user_info,
            "capas": capa_list,
            "status_filter": status_filter or "all",
            "source_filter": source_filter or "all",
            "q": q or "",
            "status_stages": STATUS_STAGES,
            "sources": SOURCES,
            "current_page": "capa",
            "current_func": "QUA",
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def new_capa_form(
    request: Request,
    job_id: Optional[str] = None,
    qc_check_id: Optional[str] = None,
    lab_test_id: Optional[str] = None,
    source: Optional[str] = None,
    reference: Optional[str] = None,
    problem: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("capa", "create")),
):
    """
    GET /capa/new -- Renders CAPA creation form.
    """
    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    preview_capa_no = await get_next_capa_no(conn)

    # Active jobs for dropdown
    job_rows = await conn.fetch(
        "SELECT id::text, job_no, product FROM jobs WHERE status NOT IN ('Cancelled') ORDER BY raised_on DESC LIMIT 50"
    )
    jobs = [dict(j) for j in job_rows]

    # Active profiles for owner (assignee)
    profile_rows = await conn.fetch(
        "SELECT id::text, full_name, role FROM profiles WHERE active = true ORDER BY full_name ASC"
    )
    profiles = [dict(p) for p in profile_rows]

    return templates.TemplateResponse(
        request=request,
        name="capa/form.html",
        context={
            "user": user_info,
            "is_edit": False,
            "capa": {
                "capa_no": preview_capa_no,
                "raised_on": date.today().isoformat(),
                "source": source or "QC Check",
                "reference": reference or "",
                "job_id": job_id or "",
                "qc_check_id": qc_check_id or "",
                "lab_test_id": lab_test_id or "",
                "problem": problem or "",
                "root_cause": "",
                "correction": "",
                "preventive": "",
                "owner_id": "",
                "due_date": "",
                "status": "Open",
            },
            "jobs": jobs,
            "profiles": profiles,
            "status_stages": STATUS_STAGES,
            "sources": SOURCES,
            "current_page": "capa",
            "current_func": "QUA",
        },
    )


@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_capa(
    request: Request,
    source: str = Form(...),
    problem: str = Form(...),
    reference: Optional[str] = Form(None),
    job_id: Optional[str] = Form(None),
    qc_check_id: Optional[str] = Form(None),
    lab_test_id: Optional[str] = Form(None),
    root_cause: Optional[str] = Form(None),
    correction: Optional[str] = Form(None),
    preventive: Optional[str] = Form(None),
    owner_id: Optional[str] = Form(None),
    due_date: Optional[str] = Form(None),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("capa", "create")),
):
    """
    POST /capa -- Create a new CAPA record with sequence retry on collisions.
    """
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Authenticated user ID is required to raise a CAPA.",
        )

    clean_source = source.strip() if source else "Other"
    clean_problem = problem.strip()
    if not clean_problem:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Problem description is required.",
        )

    clean_ref = reference.strip() if reference else None
    clean_root = root_cause.strip() if root_cause else None
    clean_corr = correction.strip() if correction else None
    clean_prev = preventive.strip() if preventive else None

    resolved_job_id = None
    if job_id and job_id.strip():
        try:
            resolved_job_id = uuid.UUID(job_id.strip())
        except ValueError:
            resolved_job_id = None

    resolved_qc_id = None
    if qc_check_id and qc_check_id.strip():
        try:
            resolved_qc_id = uuid.UUID(qc_check_id.strip())
        except ValueError:
            resolved_qc_id = None

    resolved_lab_id = None
    if lab_test_id and lab_test_id.strip():
        try:
            resolved_lab_id = uuid.UUID(lab_test_id.strip())
        except ValueError:
            resolved_lab_id = None

    resolved_owner_id = None
    if owner_id and owner_id.strip():
        try:
            resolved_owner_id = uuid.UUID(owner_id.strip())
        except ValueError:
            resolved_owner_id = None

    parsed_due_date = None
    if due_date and due_date.strip():
        try:
            parsed_due_date = datetime.strptime(due_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            parsed_due_date = None

    new_id = uuid.uuid4()
    max_retries = 20
    created_id: Optional[uuid.UUID] = None

    for attempt in range(max_retries):
        capa_no = await get_next_capa_no(conn)
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO capa (
                        id, capa_no, raised_on, source, reference,
                        job_id, qc_check_id, lab_test_id,
                        problem, root_cause, correction, preventive,
                        owner_id, raised_by, due_date, status
                    ) VALUES (
                        $1, $2, CURRENT_DATE, $3, $4,
                        $5, $6, $7,
                        $8, $9, $10, $11,
                        $12, $13, $14, 'Open'
                    ) RETURNING id;
                    """,
                    new_id,
                    capa_no,
                    clean_source,
                    clean_ref,
                    resolved_job_id,
                    resolved_qc_id,
                    resolved_lab_id,
                    clean_problem,
                    clean_root,
                    clean_corr,
                    clean_prev,
                    resolved_owner_id,
                    uuid.UUID(user_id),
                    parsed_due_date,
                )
                created_id = row["id"]
                break
        except UniqueViolationError:
            if attempt == max_retries - 1:
                raise HTTPException(
                    status_code=HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Could not generate unique CAPA number due to concurrent load. Please retry.",
                )
            new_id = uuid.uuid4()
            await asyncio.sleep(0.01 * (attempt + 1))

    return RedirectResponse(
        url=f"/capa/{created_id}",
        status_code=HTTP_303_SEE_OTHER,
    )


@router.get("/{id}", response_class=HTMLResponse)
async def get_capa_detail(
    request: Request,
    id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("capa", "read")),
):
    """
    GET /capa/{id} -- View single CAPA record with all audit/link details.
    """
    try:
        capa_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Invalid CAPA ID format.")

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    row = await conn.fetchrow(
        """
        SELECT 
            c.id::text as id,
            c.capa_no,
            c.raised_on,
            c.source,
            c.reference,
            c.job_id::text as job_id,
            j.job_no,
            j.product as job_product,
            j.customer_id::text as customer_id,
            cust.name as customer_name,
            c.qc_check_id::text as qc_check_id,
            qc.check_no,
            c.lab_test_id::text as lab_test_id,
            lt.test_id as lab_test_no,
            c.problem,
            c.root_cause,
            c.correction,
            c.preventive,
            c.owner_id::text as owner_id,
            p_owner.full_name as owner_name,
            p_owner.email as owner_email,
            c.raised_by::text as raised_by,
            p_raiser.full_name as raiser_name,
            p_raiser.email as raiser_email,
            c.due_date,
            c.status,
            c.verification_notes,
            c.verified_by::text as verified_by,
            p_verifier.full_name as verifier_name,
            c.verified_at,
            c.closed_at,
            c.created_at
        FROM capa c
        LEFT JOIN jobs j ON j.id = c.job_id
        LEFT JOIN customers cust ON cust.id = j.customer_id
        LEFT JOIN qc_checks qc ON qc.id = c.qc_check_id
        LEFT JOIN lab_tests lt ON lt.id = c.lab_test_id
        LEFT JOIN profiles p_owner ON p_owner.id = c.owner_id
        LEFT JOIN profiles p_raiser ON p_raiser.id = c.raised_by
        LEFT JOIN profiles p_verifier ON p_verifier.id = c.verified_by
        WHERE c.id = $1;
        """,
        capa_uuid,
    )

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CAPA record not found.")

    capa_dict = dict(row)
    d_date = capa_dict.get("due_date")
    capa_dict["is_overdue"] = False
    if d_date and d_date < date.today() and capa_dict.get("status") not in ("Closed", "Cancelled"):
        capa_dict["is_overdue"] = True

    return templates.TemplateResponse(
        request=request,
        name="capa/detail.html",
        context={
            "user": user_info,
            "capa": capa_dict,
            "status_stages": STATUS_STAGES,
            "current_page": "capa",
            "current_func": "QUA",
        },
    )


@router.get("/{id}/edit", response_class=HTMLResponse)
async def edit_capa_form(
    request: Request,
    id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("capa", "update")),
):
    """
    GET /capa/{id}/edit -- Form to edit problem, root cause, actions, due date, status.
    """
    try:
        capa_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Invalid CAPA ID format.")

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    row = await conn.fetchrow(
        """
        SELECT 
            c.id::text as id,
            c.capa_no,
            c.raised_on,
            c.source,
            c.reference,
            c.job_id::text as job_id,
            c.qc_check_id::text as qc_check_id,
            c.lab_test_id::text as lab_test_id,
            c.problem,
            c.root_cause,
            c.correction,
            c.preventive,
            c.owner_id::text as owner_id,
            c.raised_by::text as raised_by,
            c.due_date,
            c.status,
            c.verification_notes
        FROM capa c
        WHERE c.id = $1;
        """,
        capa_uuid,
    )

    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="CAPA record not found.")

    # Active jobs
    job_rows = await conn.fetch(
        "SELECT id::text, job_no, product FROM jobs WHERE status NOT IN ('Cancelled') ORDER BY raised_on DESC LIMIT 50"
    )
    jobs = [dict(j) for j in job_rows]

    # Active profiles
    profile_rows = await conn.fetch(
        "SELECT id::text, full_name, role FROM profiles WHERE active = true ORDER BY full_name ASC"
    )
    profiles = [dict(p) for p in profile_rows]

    return templates.TemplateResponse(
        request=request,
        name="capa/form.html",
        context={
            "user": user_info,
            "is_edit": True,
            "capa": dict(row),
            "jobs": jobs,
            "profiles": profiles,
            "status_stages": STATUS_STAGES,
            "sources": SOURCES,
            "current_page": "capa",
            "current_func": "QUA",
        },
    )


@router.post("/{id}/update", response_class=HTMLResponse)
async def update_capa(
    request: Request,
    id: str,
    source: str = Form(...),
    problem: str = Form(...),
    reference: Optional[str] = Form(None),
    job_id: Optional[str] = Form(None),
    root_cause: Optional[str] = Form(None),
    correction: Optional[str] = Form(None),
    preventive: Optional[str] = Form(None),
    owner_id: Optional[str] = Form(None),
    due_date: Optional[str] = Form(None),
    status: str = Form(...),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("capa", "update")),
):
    """
    POST /capa/{id}/update -- Update CAPA details and lifecycle status.
    """
    try:
        capa_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Invalid CAPA ID format.")

    if status not in STATUS_STAGES:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Invalid status '{status}'. Must be one of {STATUS_STAGES}",
        )

    clean_source = source.strip() if source else "Other"
    clean_problem = problem.strip()
    clean_ref = reference.strip() if reference else None
    clean_root = root_cause.strip() if root_cause else None
    clean_corr = correction.strip() if correction else None
    clean_prev = preventive.strip() if preventive else None

    resolved_job_id = None
    if job_id and job_id.strip():
        try:
            resolved_job_id = uuid.UUID(job_id.strip())
        except ValueError:
            resolved_job_id = None

    resolved_owner_id = None
    if owner_id and owner_id.strip():
        try:
            resolved_owner_id = uuid.UUID(owner_id.strip())
        except ValueError:
            resolved_owner_id = None

    parsed_due_date = None
    if due_date and due_date.strip():
        try:
            parsed_due_date = datetime.strptime(due_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            parsed_due_date = None

    await conn.execute(
        """
        UPDATE capa SET
            source = $1,
            reference = $2,
            job_id = $3,
            problem = $4,
            root_cause = $5,
            correction = $6,
            preventive = $7,
            owner_id = $8,
            due_date = $9,
            status = $10
        WHERE id = $11;
        """,
        clean_source,
        clean_ref,
        resolved_job_id,
        clean_problem,
        clean_root,
        clean_corr,
        clean_prev,
        resolved_owner_id,
        parsed_due_date,
        status,
        capa_uuid,
    )

    return RedirectResponse(
        url=f"/capa/{id}",
        status_code=HTTP_303_SEE_OTHER,
    )


@router.post("/{id}/verify", response_class=HTMLResponse)
async def verify_and_close_capa(
    request: Request,
    id: str,
    verification_notes: str = Form(...),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("capa", "approve")),
):
    """
    POST /capa/{id}/verify -- Formally verify effectiveness and close CAPA.
    Enforces segregation of duties: verifier cannot be the person who raised the CAPA.
    """
    try:
        capa_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Invalid CAPA ID format.")

    verifier_id = user.get("id")
    if not verifier_id:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Authenticated user ID is required for verification.",
        )

    clean_notes = verification_notes.strip()
    if not clean_notes:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Verification notes describing the effectiveness check are required.",
        )

    try:
        res = await conn.execute(
            """
            UPDATE capa SET
                verification_notes = $1,
                verified_by = $2,
                verified_at = NOW(),
                closed_at = NOW(),
                status = 'Closed'
            WHERE id = $3;
            """,
            clean_notes,
            uuid.UUID(verifier_id),
            capa_uuid,
        )
        if res == "UPDATE 0":
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="CAPA record not found.")
    except CheckViolationError as exc:
        if "capa_no_self_verification" in str(exc):
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="Segregation of duties violation: the person who verifies/closes a CAPA cannot be the person who raised it.",
            )
        raise

    return RedirectResponse(
        url=f"/capa/{id}",
        status_code=HTTP_303_SEE_OTHER,
    )
