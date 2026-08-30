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

logger = logging.getLogger("snm_works.lab_tests")
router = APIRouter(prefix="/lab-tests", tags=["Lab Tests"])
templates = Jinja2Templates(directory="templates")

TEST_TYPES = [
    "Tensile / Breaking Strength",
    "Elongation at Break",
    "Tear Strength",
    "Abrasion Resistance",
    "Thickness & Width",
    "Weight / Linear Density",
    "Colour Fastness to Light",
    "Colour Fastness to Washing",
    "Colour Fastness to Crocking",
    "pH of Water Extract",
    "Spectral Reflectance",
    "Hydrostatic Head",
]

LIMIT_KINDS = ["nominal", "minimum", "maximum", "range"]


async def get_next_lab_test_id(conn: asyncpg.Connection) -> str:
    """
    Auto-generates next test_id using:
    select coalesce(max(substring(test_id from 'LT-(\\d+)')::int), 0) + 1 from lab_tests
    """
    seq_val = await conn.fetchval(
        """
        SELECT COALESCE(
          MAX(SUBSTRING(test_id FROM 'LT-(\\d+)')::int),
          0
        ) + 1 AS next_seq
        FROM lab_tests
        """
    )
    next_seq = int(seq_val) if seq_val is not None else 1
    return f"LT-{next_seq:04d}"


async def fetch_active_jobs(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
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


def parse_specimens_input(specimens_str: Optional[str]) -> List[float]:
    """
    Parses comma, space, or newline-separated specimen readings into a list of floats.
    """
    if not specimens_str:
        return []
    readings = []
    # Replace newlines, commas, tabs with spaces
    cleaned = specimens_str.replace(",", " ").replace(";", " ")
    for token in cleaned.split():
        token = token.strip()
        if token:
            try:
                readings.append(float(token))
            except ValueError:
                continue
    return readings


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def list_lab_tests(
    request: Request,
    job_no: Optional[str] = None,
    verdict: Optional[str] = None,
    test_type: Optional[str] = None,
    q: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tests", "read")),
):
    """
    GET /lab-tests — list all lab tests with status filters.
    """
    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    query = """
        SELECT 
            lt.id::text as id,
            lt.test_id,
            lt.job_id::text as job_id,
            j.job_no,
            j.product as job_product,
            lt.tested_on,
            lt.test_type,
            lt.parameter,
            lt.standard,
            lt.lab,
            lt.unit,
            lt.limit_type,
            lt.spec_value,
            lt.tolerance,
            lt.upper_limit,
            lt.is_critical,
            lt.specimens,
            lt.verdict,
            lt.approved_at,
            lt.report_filed,
            p_creator.full_name as creator_name,
            p_approver.full_name as approver_name,
            lt.created_at
        FROM lab_tests lt
        LEFT JOIN jobs j ON j.id = lt.job_id
        LEFT JOIN profiles p_creator ON p_creator.id = lt.created_by
        LEFT JOIN profiles p_approver ON p_approver.id = lt.approved_by
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
            query += " AND (lt.verdict = 'Pending' OR lt.verdict IS NULL)"
        else:
            query += f" AND lt.verdict = ${idx}"
            params.append(verdict.strip().upper())
            idx += 1

    if test_type and test_type.strip() and test_type.lower() != "all":
        query += f" AND lt.test_type = ${idx}"
        params.append(test_type.strip())
        idx += 1

    if q and q.strip():
        search_term = f"%{q.strip()}%"
        query += f" AND (lt.test_id ILIKE ${idx} OR lt.parameter ILIKE ${idx} OR lt.test_type ILIKE ${idx} OR j.job_no ILIKE ${idx})"
        params.append(search_term)
        idx += 1

    query += " ORDER BY lt.tested_on DESC, lt.created_at DESC"

    rows = await conn.fetch(query, *params)
    tests_list = [dict(r) for r in rows]

    return templates.TemplateResponse(
        request=request,
        name="lab_tests/list.html",
        context={
            "user": user_info,
            "tests": tests_list,
            "job_no_filter": job_no or "",
            "verdict_filter": verdict or "all",
            "test_type_filter": test_type or "all",
            "test_types": TEST_TYPES,
            "q": q or "",
            "current_page": "lab-tests",
            "current_func": "QUA",
        }
    )


@router.get("/new", response_class=HTMLResponse)
async def new_lab_test_form(
    request: Request,
    job_id: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tests", "create")),
):
    """
    GET /lab-tests/new — create form.
    """
    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    preview_test_id = await get_next_lab_test_id(conn)
    jobs = await fetch_active_jobs(conn)

    return templates.TemplateResponse(
        request=request,
        name="lab_tests/form.html",
        context={
            "user": user_info,
            "is_edit": False,
            "test": {
                "test_id": preview_test_id,
                "job_id": job_id or "",
                "tested_on": date.today().isoformat(),
                "test_type": "Tensile / Breaking Strength",
                "parameter": "Breaking Strength",
                "standard": "MIL-W-4088K / ASTM D5034",
                "lab": "In-house",
                "unit": "kgf",
                "limit_type": "minimum",
                "spec_value": 1800.0,
                "tolerance": None,
                "upper_limit": None,
                "is_critical": True,
                "specimens": [],
                "specimens_text": "",
                "remarks": "",
            },
            "selected_job_id": job_id or "",
            "jobs": jobs,
            "test_types": TEST_TYPES,
            "limit_kinds": LIMIT_KINDS,
            "current_page": "lab-tests",
            "current_func": "QUA",
        }
    )


@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_lab_test(
    request: Request,
    job_id: Optional[str] = Form(None),
    tested_on: Optional[str] = Form(None),
    test_type: str = Form("Tensile / Breaking Strength"),
    parameter: str = Form(...),
    standard: Optional[str] = Form(None),
    lab: str = Form("In-house"),
    report_no: Optional[str] = Form(None),
    unit: Optional[str] = Form(None),
    limit_type: str = Form("minimum"),
    spec_value: float = Form(...),
    tolerance: Optional[float] = Form(None),
    upper_limit: Optional[float] = Form(None),
    is_critical: bool = Form(False),
    specimens_raw: Optional[str] = Form(None),
    requirement: Optional[str] = Form(None),
    remarks: Optional[str] = Form(None),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tests", "create")),
):
    """
    POST /lab-tests — create lab test record.
    - created_by resolved strictly from authenticated user.
    - test_id sequence retried on UniqueViolationError.
    - verdict evaluated strictly by PostgreSQL generated column.
    """
    creator_id = uuid.UUID(str(user["id"]))

    parsed_date = date.today()
    if tested_on and tested_on.strip():
        try:
            parsed_date = datetime.strptime(tested_on.strip(), "%Y-%m-%d").date()
        except ValueError:
            parsed_date = date.today()

    clean_test_type = test_type.strip()
    clean_parameter = parameter.strip()
    clean_standard = standard.strip() if standard else None
    clean_lab = lab.strip() if lab else "In-house"
    clean_report_no = report_no.strip() if report_no else None
    clean_unit = unit.strip() if unit else None
    clean_limit_type = limit_type.strip().lower() if limit_type else "nominal"
    clean_requirement = requirement.strip() if requirement else None
    clean_remarks = remarks.strip() if remarks else None

    resolved_job_id = None
    if job_id and job_id.strip():
        try:
            resolved_job_id = uuid.UUID(job_id.strip())
        except (ValueError, TypeError):
            resolved_job_id = None

    specimen_readings = parse_specimens_input(specimens_raw)
    max_retries = 10
    new_test_uuid = str(uuid.uuid4())
    for attempt in range(max_retries):
        generated_test_id = await get_next_lab_test_id(conn)
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO lab_tests (
                        id, test_id, job_id, tested_on, test_type, parameter,
                        standard, lab, report_no, unit, limit_type, spec_value,
                        tolerance, upper_limit, is_critical, specimens,
                        requirement, remarks, created_by
                    ) VALUES (
                        $1::uuid, $2, $3, $4, $5, $6,
                        $7, $8, $9, $10, $11::limit_kind, $12,
                        $13, $14, $15, $16,
                        $17, $18, $19
                    )
                    RETURNING id::text, test_id, verdict
                    """,
                    uuid.UUID(new_test_uuid),
                    generated_test_id,
                    resolved_job_id,
                    parsed_date,
                    clean_test_type,
                    clean_parameter,
                    clean_standard,
                    clean_lab,
                    clean_report_no,
                    clean_unit,
                    clean_limit_type,
                    spec_value,
                    tolerance,
                    upper_limit,
                    is_critical,
                    specimen_readings,
                    clean_requirement,
                    clean_remarks,
                    creator_id,
                )
            break
        except asyncpg.UniqueViolationError:
            if attempt == max_retries - 1:
                raise HTTPException(
                    status_code=HTTP_400_BAD_REQUEST,
                    detail="Could not generate unique test ID due to concurrent submissions. Please retry.",
                )
            new_test_uuid = str(uuid.uuid4())
            continue

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        response = Response(status_code=HTTP_200_OK)
        response.headers["HX-Redirect"] = f"/lab-tests/{new_test_uuid}"
        return response

    return RedirectResponse(url=f"/lab-tests/{new_test_uuid}", status_code=HTTP_303_SEE_OTHER)


@router.get("/{test_id}", response_class=HTMLResponse)
async def lab_test_detail(
    request: Request,
    test_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tests", "read")),
):
    """
    GET /lab-tests/{test_id} — detail view with specimens breakdown and verdict.
    """
    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    row = await conn.fetchrow(
        """
        SELECT 
            lt.id::text as id,
            lt.test_id,
            lt.job_id::text as job_id,
            j.job_no,
            j.product as job_product,
            j.status as job_status,
            c.name as customer_name,
            lt.tested_on,
            lt.test_type,
            lt.parameter,
            lt.standard,
            lt.lab,
            lt.report_no,
            lt.unit,
            lt.limit_type,
            lt.spec_value,
            lt.tolerance,
            lt.upper_limit,
            lt.is_critical,
            lt.specimens,
            lt.verdict,
            lt.requirement,
            lt.remarks,
            lt.report_filed,
            lt.created_by::text as created_by,
            lt.approved_by::text as approved_by,
            lt.approved_at,
            p_creator.full_name as creator_name,
            p_approver.full_name as approver_name,
            lt.created_at
        FROM lab_tests lt
        LEFT JOIN jobs j ON j.id = lt.job_id
        LEFT JOIN customers c ON c.id = j.customer_id
        LEFT JOIN profiles p_creator ON p_creator.id = lt.created_by
        LEFT JOIN profiles p_approver ON p_approver.id = lt.approved_by
        WHERE lt.id::text = $1 OR lt.test_id = $1
        """,
        test_id,
    )

    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Lab Test record not found")

    test_data = dict(row)
    specimens = test_data.get("specimens") or []
    min_val = min(specimens) if specimens else None
    max_val = max(specimens) if specimens else None
    avg_val = (sum(specimens) / len(specimens)) if specimens else None

    # Check if current user can approve (must have tests.approve permission and NOT be the creator)
    user_can_approve = False
    if test_data.get("approved_by") is None:
        can_approve_perm = await conn.fetchval("SELECT auth_can('tests', 'approve')")
        if can_approve_perm and str(user_info["id"]) != test_data.get("created_by"):
            user_can_approve = True

    return templates.TemplateResponse(
        request=request,
        name="lab_tests/detail.html",
        context={
            "user": user_info,
            "test": test_data,
            "specimens": specimens,
            "min_val": min_val,
            "max_val": max_val,
            "avg_val": avg_val,
            "user_can_approve": user_can_approve,
            "current_page": "lab-tests",
            "current_func": "QUA",
        }
    )


@router.get("/{test_id}/edit", response_class=HTMLResponse)
async def edit_lab_test_form(
    request: Request,
    test_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tests", "update")),
):
    """
    GET /lab-tests/{test_id}/edit — edit form.
    """
    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    row = await conn.fetchrow(
        """
        SELECT 
            lt.id::text as id, lt.test_id, lt.job_id::text as job_id,
            j.job_no, j.product as job_product, lt.tested_on,
            lt.test_type, lt.parameter, lt.standard, lt.lab,
            lt.report_no, lt.unit, lt.limit_type, lt.spec_value,
            lt.tolerance, lt.upper_limit, lt.is_critical, lt.specimens,
            lt.requirement, lt.remarks, lt.verdict
        FROM lab_tests lt
        LEFT JOIN jobs j ON j.id = lt.job_id
        WHERE lt.id::text = $1 OR lt.test_id = $1
        """,
        test_id,
    )

    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Lab Test record not found")

    test_data = dict(row)
    specimens = test_data.get("specimens") or []
    test_data["specimens_text"] = ", ".join(str(s) for s in specimens)
    jobs = await fetch_active_jobs(conn)

    return templates.TemplateResponse(
        request=request,
        name="lab_tests/form.html",
        context={
            "user": user_info,
            "is_edit": True,
            "test": test_data,
            "selected_job_id": test_data.get("job_id") or "",
            "jobs": jobs,
            "test_types": TEST_TYPES,
            "limit_kinds": LIMIT_KINDS,
            "current_page": "lab-tests",
            "current_func": "QUA",
        }
    )


@router.post("/{test_id}/update", response_class=HTMLResponse)
async def update_lab_test(
    request: Request,
    test_id: str,
    job_id: Optional[str] = Form(None),
    tested_on: Optional[str] = Form(None),
    test_type: str = Form("Tensile / Breaking Strength"),
    parameter: str = Form(...),
    standard: Optional[str] = Form(None),
    lab: str = Form("In-house"),
    report_no: Optional[str] = Form(None),
    unit: Optional[str] = Form(None),
    limit_type: str = Form("minimum"),
    spec_value: float = Form(...),
    tolerance: Optional[float] = Form(None),
    upper_limit: Optional[float] = Form(None),
    is_critical: bool = Form(False),
    specimens_raw: Optional[str] = Form(None),
    requirement: Optional[str] = Form(None),
    remarks: Optional[str] = Form(None),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tests", "update")),
):
    """
    POST /lab-tests/{test_id}/update — update specimen readings. Verdict is re-evaluated by Postgres.
    """
    parsed_date = date.today()
    if tested_on and tested_on.strip():
        try:
            parsed_date = datetime.strptime(tested_on.strip(), "%Y-%m-%d").date()
        except ValueError:
            parsed_date = date.today()

    clean_test_type = test_type.strip()
    clean_parameter = parameter.strip()
    clean_standard = standard.strip() if standard else None
    clean_lab = lab.strip() if lab else "In-house"
    clean_report_no = report_no.strip() if report_no else None
    clean_unit = unit.strip() if unit else None
    clean_limit_type = limit_type.strip().lower() if limit_type else "nominal"
    clean_requirement = requirement.strip() if requirement else None
    clean_remarks = remarks.strip() if remarks else None

    resolved_job_id = None
    if job_id and job_id.strip():
        try:
            resolved_job_id = uuid.UUID(job_id.strip())
        except (ValueError, TypeError):
            resolved_job_id = None

    specimen_readings = parse_specimens_input(specimens_raw)

    row = await conn.fetchrow(
        """
        UPDATE lab_tests
        SET job_id = $1,
            tested_on = $2,
            test_type = $3,
            parameter = $4,
            standard = $5,
            lab = $6,
            report_no = $7,
            unit = $8,
            limit_type = $9::limit_kind,
            spec_value = $10,
            tolerance = $11,
            upper_limit = $12,
            is_critical = $13,
            specimens = $14,
            requirement = $15,
            remarks = $16
        WHERE id::text = $17 OR test_id = $17
        RETURNING id::text, verdict
        """,
        resolved_job_id,
        parsed_date,
        clean_test_type,
        clean_parameter,
        clean_standard,
        clean_lab,
        clean_report_no,
        clean_unit,
        clean_limit_type,
        spec_value,
        tolerance,
        upper_limit,
        is_critical,
        specimen_readings,
        clean_requirement,
        clean_remarks,
        test_id,
    )

    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Lab Test record not found")

    actual_uuid = row["id"]

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        response = Response(status_code=HTTP_200_OK)
        response.headers["HX-Redirect"] = f"/lab-tests/{actual_uuid}"
        return response

    return RedirectResponse(url=f"/lab-tests/{actual_uuid}", status_code=HTTP_303_SEE_OTHER)


@router.post("/{test_id}/approve", response_class=HTMLResponse)
async def approve_lab_test(
    request: Request,
    test_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tests", "approve")),
):
    """
    POST /lab-tests/{test_id}/approve — approve a test record.
    Enforces check constraint lab_tests_no_self_approval at the database level.
    """
    approver_id = uuid.UUID(str(user["id"]))

    try:
        row = await conn.fetchrow(
            """
            UPDATE lab_tests
            SET approved_by = $1,
                approved_at = NOW()
            WHERE (id::text = $2 OR test_id = $2)
            RETURNING id::text, test_id, created_by
            """,
            approver_id,
            test_id,
        )
    except asyncpg.CheckViolationError:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Segregation of Duties Violation: You cannot approve a lab test record that you entered.",
        )

    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Lab Test record not found")

    actual_uuid = row["id"]

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        response = Response(status_code=HTTP_200_OK)
        response.headers["HX-Redirect"] = f"/lab-tests/{actual_uuid}"
        return response

    return RedirectResponse(url=f"/lab-tests/{actual_uuid}", status_code=HTTP_303_SEE_OTHER)
