from typing import Any, Dict, List, Optional
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from auth.dependencies import current_user, require
from database import get_db
from services.downtime_service import (
    fetch_downtime_options,
    list_downtime_records,
    record_downtime_log,
)
from services.materials_service import (
    fetch_material_issue_options,
    issue_job_material,
    list_material_issues,
)
from services.qc_service import (
    INSPECTION_STAGES,
    LIMIT_KINDS,
    fetch_active_jobs,
    list_qc_checks_records,
    record_qc_check,
)

router = APIRouter(prefix="/api/v1", tags=["Mobile API"])


# ============================================================================
# 1. QUALITY CONTROL (QC) MOBILE ENDPOINTS
# ============================================================================

class QCCheckCreateSchema(BaseModel):
    job_id: Optional[str] = None
    checked_on: Optional[str] = None
    stage: str = "On-Loom Inspection"
    family: Optional[str] = None
    parameter: str
    unit: Optional[str] = None
    method: Optional[str] = None
    limit_type: str = "nominal"
    spec_value: float
    tolerance: Optional[float] = None
    upper_limit: Optional[float] = None
    actual: Optional[float] = None
    defect_code: Optional[str] = None
    action_taken: Optional[str] = None


@router.get("/qc/options")
async def get_qc_options(
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("qc", "read")),
):
    """
    Returns picker options for mobile QC checks:
    active jobs, standard inspection stages, and supported limit kinds.
    """
    jobs = await fetch_active_jobs(conn)
    return {
        "jobs": jobs,
        "stages": INSPECTION_STAGES,
        "limit_kinds": LIMIT_KINDS,
    }


@router.get("/qc/checks")
async def list_qc_checks_mobile(
    job_no: Optional[str] = None,
    verdict: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("qc", "read")),
):
    """
    Returns recent QC line inspection records for mobile review.
    """
    checks = await list_qc_checks_records(conn, job_no=job_no, verdict=verdict, q=q, limit=limit)
    return {
        "count": len(checks),
        "checks": checks,
    }


@router.post("/qc/checks", status_code=status.HTTP_201_CREATED)
async def create_qc_check_mobile(
    payload: QCCheckCreateSchema,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("qc", "create")),
):
    """
    Records a shop-floor QC check reading from the mobile client.
    Verdicts are evaluated strictly by PostgreSQL stored generated columns.
    """
    result = await record_qc_check(conn, user["id"], payload.model_dump())
    return result


# ============================================================================
# 2. DOWNTIME TELEMETRY MOBILE ENDPOINTS
# ============================================================================

class DowntimeLogCreateSchema(BaseModel):
    logged_on: Optional[str] = None
    shift: str = "Shift A (06:00-14:00)"
    machine: str
    reason: str
    minutes: float
    job_id: Optional[str] = None
    remarks: Optional[str] = None


@router.get("/downtime/options")
async def get_downtime_options(
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("downtime", "read")),
):
    """
    Returns picker options for mobile downtime logging:
    configured machines, production shifts, stoppage reasons, and active jobs.
    """
    options = await fetch_downtime_options(conn)
    return options


@router.get("/downtime/logs")
async def list_downtime_logs_mobile(
    machine: Optional[str] = None,
    shift: Optional[str] = None,
    reason: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("downtime", "read")),
):
    """
    Returns recent machine downtime stoppage logs for mobile review.
    """
    logs = await list_downtime_records(
        conn,
        machine_filter=machine or "all",
        shift_filter=shift or "all",
        reason_filter=reason or "all",
        q=q,
        limit=limit,
    )
    return {
        "count": len(logs),
        "logs": logs,
    }


@router.post("/downtime/logs", status_code=status.HTTP_201_CREATED)
async def create_downtime_log_mobile(
    payload: DowntimeLogCreateSchema,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("downtime", "create")),
):
    """
    Records a shop-floor machine stoppage log from the mobile client.
    Guarded by downtime.create permission.
    """
    result = await record_downtime_log(conn, user["id"], payload.model_dump())
    return result


# ============================================================================
# 3. MATERIALS ISSUE MOBILE ENDPOINTS
# ============================================================================

class MaterialIssueCreateSchema(BaseModel):
    job_id: str
    yarn_lot_id: str
    qty_issued: float
    unit: str = "kg"
    issued_date: Optional[str] = None
    remarks: Optional[str] = None


@router.get("/materials/issue/options")
async def get_material_issue_options(
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("stock", "read")),
):
    """
    Returns picker options for mobile material issuing:
    active jobs and QA-Approved yarn lots with positive remaining balance.
    """
    options = await fetch_material_issue_options(conn)
    return options


@router.get("/materials/issues")
async def list_material_issues_mobile(
    job_id: Optional[str] = None,
    yarn_lot_id: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("stock", "read")),
):
    """
    Returns recent material issue records for mobile review.
    """
    issues = await list_material_issues(
        conn,
        job_id=job_id,
        yarn_lot_id=yarn_lot_id,
        q=q,
        limit=limit,
    )
    return {
        "count": len(issues),
        "issues": issues,
    }


@router.post("/materials/issue", status_code=status.HTTP_201_CREATED)
async def create_material_issue_mobile(
    payload: MaterialIssueCreateSchema,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("stock", "create")),
):
    """
    Issues material from an Approved yarn lot against a Job Card from mobile.
    Guarded by stock.create permission. Stock deduction executed via database trigger.
    """
    result = await issue_job_material(conn, user["id"], payload.model_dump())
    return result


# ============================================================================
# 4. JOBS & INSPECTION PLAN MOBILE ENDPOINTS
# ============================================================================

@router.get("/jobs")
async def list_jobs_mobile(
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("jobs", "read")),
):
    """
    Returns active production jobs for mobile review (read-only).
    Lightweight version of fields from the web jobs list.
    """
    query = """
        SELECT 
            j.id::text as id,
            j.job_no,
            j.raised_on::text as raised_on,
            j.customer_id::text as customer_id,
            c.name as customer_name,
            j.po_ref,
            j.product,
            j.spec,
            j.variant_id::text as variant_id,
            j.width_mm::float as width_mm,
            j.colour,
            j.qty_ordered::float as qty_ordered,
            j.unit,
            j.qty_produced::float as qty_produced,
            (j.qty_ordered - j.qty_produced)::float as balance,
            j.machine,
            j.delivery_due::text as delivery_due,
            j.status,
            j.remarks
        FROM jobs j
        LEFT JOIN customers c ON c.id = j.customer_id
        WHERE 1=1
    """
    params: List[Any] = []
    idx = 1

    if status and status.strip() and status.strip().lower() != "all":
        query += f" AND j.status = ${idx}"
        params.append(status.strip())
        idx += 1
    else:
        query += " AND j.status != 'Cancelled'"

    if q and q.strip():
        search_term = f"%{q.strip()}%"
        query += f" AND (j.job_no ILIKE ${idx} OR j.product ILIKE ${idx} OR c.name ILIKE ${idx})"
        params.append(search_term)
        idx += 1

    query += f" ORDER BY j.raised_on DESC, j.job_no DESC LIMIT ${idx}"
    params.append(limit)

    rows = await conn.fetch(query, *params)
    jobs = [dict(r) for r in rows]
    return {
        "count": len(jobs),
        "jobs": jobs,
    }


@router.get("/jobs/{job_id}/inspection-plan")
async def get_job_inspection_plan_mobile(
    job_id: str,
    variant_id: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user_jobs: Dict[str, Any] = Depends(require("jobs", "read")),
    user_specs: Dict[str, Any] = Depends(require("specifications", "read")),
):
    """
    Returns the guided checklist inspection plan for a specific job.
    Requires specifications:read and jobs:read permissions.
    """
    # 1. Fetch Job
    job_row = await conn.fetchrow(
        """
        SELECT 
            j.id::text as id,
            j.job_no,
            j.raised_on::text as raised_on,
            j.customer_id::text as customer_id,
            c.name as customer_name,
            j.po_ref,
            j.product,
            j.spec,
            j.variant_id::text as variant_id,
            j.width_mm::float as width_mm,
            j.colour,
            j.qty_ordered::float as qty_ordered,
            j.unit,
            j.qty_produced::float as qty_produced,
            j.machine,
            j.delivery_due::text as delivery_due,
            j.status,
            j.remarks
        FROM jobs j
        LEFT JOIN customers c ON c.id = j.customer_id
        WHERE j.id::text = $1 OR j.job_no = $1;
        """,
        job_id,
    )
    if not job_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found",
        )

    job_data = dict(job_row)

    # 2. Fetch Available Specification Variants
    v_rows = await conn.fetch(
        """
        SELECT 
            v.id::text as id,
            v.designation,
            v.designation as variant_code,
            v.designation as name,
            v.class,
            v.description,
            v.sort_order,
            v.status,
            s.spec_no,
            s.revision,
            s.title as spec_title,
            s.issuing_body,
            s.active as spec_active
        FROM spec_variants v
        JOIN specifications s ON s.id = v.spec_id
        ORDER BY s.spec_no ASC, v.sort_order ASC, v.designation ASC;
        """
    )
    available_variants = [dict(r) for r in v_rows]

    # 3. Resolve Target Variant
    selected_variant: Optional[Dict[str, Any]] = None
    plan_items: List[Dict[str, Any]] = []

    target_variant_id: Optional[str] = None
    if variant_id and variant_id.strip():
        target_variant_id = variant_id.strip()
    elif job_data.get("variant_id"):
        target_variant_id = job_data["variant_id"]

    if target_variant_id:
        try:
            import uuid
            var_uuid = uuid.UUID(target_variant_id)
            v_row = await conn.fetchrow(
                """
                SELECT 
                    v.id::text as id,
                    v.designation,
                    v.designation as variant_code,
                    v.designation as name,
                    v.class,
                    v.description,
                    v.sort_order,
                    v.status,
                    s.spec_no,
                    s.revision,
                    s.title as spec_title,
                    s.issuing_body,
                    s.active as spec_active
                FROM spec_variants v
                JOIN specifications s ON s.id = v.spec_id
                WHERE v.id = $1;
                """,
                var_uuid,
            )
            if v_row:
                selected_variant = dict(v_row)
                plan_rows = await conn.fetch(
                    "SELECT * FROM spec_check_plan($1::uuid);",
                    var_uuid,
                )

                # Fetch qc_checks and lab_tests for completion matching
                job_uuid = uuid.UUID(job_data["id"])
                qc_checks_raw = await conn.fetch(
                    """
                    SELECT 
                        id::text as id, check_no, checked_on::text as checked_on, parameter, actual::text as actual,
                        verdict, 'qc_checks' as source, created_at::text as created_at
                    FROM qc_checks
                    WHERE job_id = $1
                    ORDER BY checked_on DESC, created_at DESC;
                    """,
                    job_uuid,
                )
                lab_tests_raw = await conn.fetch(
                    """
                    SELECT 
                        id::text as id, test_id as check_no, tested_on::text as checked_on, parameter,
                        result as actual, verdict, 'lab_tests' as source, created_at::text as created_at
                    FROM lab_tests
                    WHERE job_id = $1
                    ORDER BY tested_on DESC, created_at DESC;
                    """,
                    job_uuid,
                )
                all_checks = [dict(r) for r in qc_checks_raw] + [dict(r) for r in lab_tests_raw]

                for pr in plan_rows:
                    item = dict(pr)
                    # Cast any numeric/decimal fields to float
                    for k, v in item.items():
                        if hasattr(v, "__float__") and not isinstance(v, (bool, str)):
                            item[k] = float(v)
                    req_param = (item.get("parameter") or "").strip().lower()
                    matches = [
                        c for c in all_checks
                        if (c.get("parameter") or "").strip().lower() == req_param
                    ]
                    matches.sort(
                        key=lambda x: (str(x.get("checked_on") or ""), str(x.get("created_at") or "")),
                        reverse=True,
                    )
                    item["matched_checks"] = matches
                    item["is_checked"] = len(matches) > 0
                    item["latest_verdict"] = (
                        matches[0]["verdict"].upper()
                        if matches and matches[0].get("verdict")
                        else None
                    )
                    plan_items.append(item)
        except (ValueError, asyncpg.PostgresError) as err:
            pass

    checked_count = sum(1 for item in plan_items if item.get("is_checked"))

    return {
        "job": job_data,
        "selected_variant": selected_variant,
        "available_variants": available_variants,
        "requires_variant_selection": selected_variant is None,
        "plan_items": plan_items,
        "summary": {
            "total_items": len(plan_items),
            "checked_items": checked_count,
            "pending_items": len(plan_items) - checked_count,
        },
    }

