from typing import Any, Dict, List, Optional
import asyncpg
from fastapi import APIRouter, Depends, Query, status
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
