from typing import Any, Dict, List, Optional
import asyncpg
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from auth.dependencies import current_user, require
from database import get_db
from services.qc_service import (
    INSPECTION_STAGES,
    LIMIT_KINDS,
    fetch_active_jobs,
    list_qc_checks_records,
    record_qc_check,
)

router = APIRouter(prefix="/api/v1", tags=["Mobile API"])


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
