"""
routers/analytics.py — Executive KPI Dashboard, Supplier Scorecard & Report Snapshots.
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
)
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth.dependencies import current_user, require
from database import get_db

logger = logging.getLogger("snm_works.analytics")
router = APIRouter(prefix="/analytics", tags=["Analytics & KPIs"])
templates = Jinja2Templates(directory="templates")


def require_any(*permissions: tuple[str, str]):
    async def dep(
        conn: asyncpg.Connection = Depends(get_db),
        user: Dict[str, Any] = Depends(current_user),
    ) -> Dict[str, Any]:
        for module, action in permissions:
            can = await conn.fetchval("SELECT auth_can($1, $2)", module, action)
            if can:
                return user
        raise HTTPException(
            status_code=403,
            detail="Your roles do not permit viewing this analytics view",
        )
    return dep


async def get_next_report_no(conn: asyncpg.Connection) -> str:
    """Thread-safe sequence generator for KPI reports: KPI-YYYY-NNNN."""
    val = await conn.fetchval("SELECT nextval('kpi_report_seq');")
    year = date.today().year
    return f"KPI-{year}-{int(val):04d}"


def resolve_period_dates(period: str) -> tuple[date, date]:
    today = date.today()
    if period == "7d":
        return today - timedelta(days=7), today
    elif period == "30d":
        return today - timedelta(days=30), today
    elif period == "90d":
        return today - timedelta(days=90), today
    elif period == "12m":
        return today - timedelta(days=365), today
    elif period == "all":
        return date(2000, 1, 1), today
    return today - timedelta(days=30), today


async def compute_kpi_metrics(conn: asyncpg.Connection, start_d: date, end_d: date) -> Dict[str, Any]:
    """Pure helper querying and aggregating real operational data across the four KPI categories."""
    # 1. QC Checks & Defects
    qc_agg = await conn.fetchrow(
        """
        SELECT 
          COUNT(*) AS total_qc,
          COUNT(*) FILTER (WHERE verdict = 'PASS') AS pass_qc,
          COUNT(*) FILTER (WHERE verdict = 'FAIL') AS fail_qc,
          ROUND(COALESCE(COUNT(*) FILTER (WHERE verdict = 'PASS') * 100.0 / NULLIF(COUNT(*), 0), 0.0), 2) AS qc_pass_rate
        FROM qc_checks
        WHERE checked_on BETWEEN $1 AND $2;
        """,
        start_d, end_d
    )

    defects = await conn.fetch(
        """
        SELECT COALESCE(NULLIF(defect_code, ''), 'Unspecified Defect') AS defect, COUNT(*) AS count
        FROM qc_checks
        WHERE checked_on BETWEEN $1 AND $2 AND verdict = 'FAIL'
        GROUP BY defect ORDER BY count DESC LIMIT 8;
        """,
        start_d, end_d
    )

    # 2. Defect Trends (Weekly)
    trends = await conn.fetch(
        """
        SELECT 
          DATE_TRUNC('week', checked_on)::date AS week_start,
          COUNT(*) AS total_checks,
          COUNT(*) FILTER (WHERE verdict = 'FAIL') AS fail_count,
          COUNT(*) FILTER (WHERE verdict = 'PASS') AS pass_count
        FROM qc_checks
        WHERE checked_on BETWEEN $1 AND $2
        GROUP BY week_start ORDER BY week_start ASC;
        """,
        start_d, end_d
    )

    # 3. Lab Tests
    lab_agg = await conn.fetchrow(
        """
        SELECT 
          COUNT(*) AS total_lab,
          COUNT(*) FILTER (WHERE verdict = 'PASS') AS pass_lab,
          COUNT(*) FILTER (WHERE verdict = 'FAIL') AS fail_lab,
          ROUND(COALESCE(COUNT(*) FILTER (WHERE verdict = 'PASS') * 100.0 / NULLIF(COUNT(*), 0), 0.0), 2) AS lab_pass_rate
        FROM lab_tests
        WHERE DATE(created_at) BETWEEN $1 AND $2;
        """,
        start_d, end_d
    )

    # 4. CAPA Closure Times & Distribution
    capa_agg = await conn.fetchrow(
        """
        SELECT 
          COUNT(*) AS total_capa,
          COUNT(*) FILTER (WHERE status = 'Closed') AS closed_capa,
          COUNT(*) FILTER (WHERE status NOT IN ('Closed', 'Cancelled')) AS open_capa,
          COUNT(*) FILTER (WHERE due_date < CURRENT_DATE AND status NOT IN ('Closed', 'Cancelled')) AS overdue_capa,
          ROUND(COALESCE(AVG(EXTRACT(EPOCH FROM (closed_at - created_at)) / 86400.0) FILTER (WHERE closed_at IS NOT NULL), 0.0), 1) AS avg_closure_days,
          COUNT(*) FILTER (WHERE closed_at IS NOT NULL AND (EXTRACT(EPOCH FROM (closed_at - created_at)) / 86400.0) < 3) AS closed_lt_3d,
          COUNT(*) FILTER (WHERE closed_at IS NOT NULL AND (EXTRACT(EPOCH FROM (closed_at - created_at)) / 86400.0) BETWEEN 3 AND 7) AS closed_3_7d,
          COUNT(*) FILTER (WHERE closed_at IS NOT NULL AND (EXTRACT(EPOCH FROM (closed_at - created_at)) / 86400.0) BETWEEN 8 AND 14) AS closed_8_14d,
          COUNT(*) FILTER (WHERE closed_at IS NOT NULL AND (EXTRACT(EPOCH FROM (closed_at - created_at)) / 86400.0) > 14) AS closed_gt_14d
        FROM capa
        WHERE raised_on BETWEEN $1 AND $2;
        """,
        start_d, end_d
    )

    # 5. Despatch On-Time Rate
    despatch_agg = await conn.fetchrow(
        """
        SELECT 
          COUNT(*) AS total_despatches,
          COUNT(*) FILTER (WHERE j.delivery_due IS NOT NULL AND d.despatched_on <= j.delivery_due) AS on_time_count,
          COUNT(*) FILTER (WHERE j.delivery_due IS NOT NULL AND d.despatched_on > j.delivery_due) AS late_count,
          COUNT(*) FILTER (WHERE j.delivery_due IS NULL) AS untracked_count,
          ROUND(COALESCE(COUNT(*) FILTER (WHERE j.delivery_due IS NOT NULL AND d.despatched_on <= j.delivery_due) * 100.0 / 
                NULLIF(COUNT(*) FILTER (WHERE j.delivery_due IS NOT NULL), 0), 0.0), 2) AS on_time_rate,
          ROUND(COALESCE(AVG(d.despatched_on - j.delivery_due) FILTER (WHERE j.delivery_due IS NOT NULL AND d.despatched_on > j.delivery_due), 0.0), 1) AS avg_delay_days
        FROM despatch d
        JOIN jobs j ON j.id = d.job_id
        WHERE d.despatched_on BETWEEN $1 AND $2;
        """,
        start_d, end_d
    )

    return {
        "qc": dict(qc_agg) if qc_agg else {},
        "defects": [dict(r) for r in defects],
        "trends": [dict(r) for r in trends],
        "lab": dict(lab_agg) if lab_agg else {},
        "capa": dict(capa_agg) if capa_agg else {},
        "despatch": dict(despatch_agg) if despatch_agg else {},
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def kpi_dashboard(
    request: Request,
    period: str = Query("30d"),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("qc", "read")),
):
    """Renders real-time executive KPI analytics dashboard."""
    user_info = {
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }
    start_d, end_d = resolve_period_dates(period)
    metrics = await compute_kpi_metrics(conn, start_d, end_d)

    return templates.TemplateResponse(
        request=request,
        name="analytics/dashboard.html",
        context={
            "user": user_info,
            "period": period,
            "start_date": start_d,
            "end_date": end_d,
            "metrics": metrics,
            "current_page": "analytics",
        },
    )


@router.post("/snapshots")
async def create_kpi_snapshot(
    request: Request,
    title: str = Form("Management Review & Quality Report"),
    period: str = Form("30d"),
    notes: Optional[str] = Form(None),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("qc", "create")),
):
    """Freezes computed KPI metrics into an immutable, audit-trailed report snapshot."""
    start_d, end_d = resolve_period_dates(period)
    metrics = await compute_kpi_metrics(conn, start_d, end_d)
    report_no = await get_next_report_no(conn)
    user_id = uuid.UUID(user["id"])

    qc_m = metrics["qc"]
    lab_m = metrics["lab"]
    capa_m = metrics["capa"]
    dsp_m = metrics["despatch"]

    row = await conn.fetchrow(
        """
        INSERT INTO kpi_report_snapshots (
          report_no, title, period_start, period_end,
          total_qc_checks, qc_pass_rate_pct,
          total_lab_tests, lab_pass_rate_pct,
          total_capa, closed_capa, avg_capa_closure_days,
          total_despatches, on_time_despatch_rate_pct,
          metrics_payload, notes, generated_by
        ) VALUES (
          $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16
        ) RETURNING id::text;
        """,
        report_no,
        title.strip(),
        start_d,
        end_d,
        qc_m.get("total_qc", 0),
        qc_m.get("qc_pass_rate", 0.0),
        lab_m.get("total_lab", 0),
        lab_m.get("lab_pass_rate", 0.0),
        capa_m.get("total_capa", 0),
        capa_m.get("closed_capa", 0),
        capa_m.get("avg_closure_days"),
        dsp_m.get("total_despatches", 0),
        dsp_m.get("on_time_rate"),
        json.dumps(metrics, default=str),
        notes.strip() if notes else None,
        user_id,
    )
    return RedirectResponse(url=f"/analytics/reports/{row['id']}", status_code=HTTP_303_SEE_OTHER)


@router.get("/reports", response_class=HTMLResponse)
async def list_kpi_reports(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("qc", "read")),
):
    """Renders register of frozen, immutable compliance report snapshots."""
    user_info = {
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }
    rows = await conn.fetch(
        """
        SELECT s.*, p.full_name AS generated_by_name
        FROM kpi_report_snapshots s
        LEFT JOIN profiles p ON p.id = s.generated_by
        ORDER BY s.created_at DESC;
        """
    )
    return templates.TemplateResponse(
        request=request,
        name="analytics/reports.html",
        context={
            "user": user_info,
            "reports": [dict(r) for r in rows],
            "current_page": "analytics",
        },
    )


@router.get("/reports/{report_id}", response_class=HTMLResponse)
async def get_kpi_report_detail(
    request: Request,
    report_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("qc", "read")),
):
    """Renders immutable historical KPI report detail view."""
    user_info = {
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }
    rep_uuid = uuid.UUID(report_id)
    row = await conn.fetchrow(
        """
        SELECT s.*, p.full_name AS generated_by_name
        FROM kpi_report_snapshots s
        LEFT JOIN profiles p ON p.id = s.generated_by
        WHERE s.id = $1;
        """,
        rep_uuid,
    )
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Report snapshot not found.")

    rep_data = dict(row)
    payload = json.loads(rep_data["metrics_payload"]) if isinstance(rep_data["metrics_payload"], str) else rep_data["metrics_payload"]

    return templates.TemplateResponse(
        request=request,
        name="analytics/report_detail.html",
        context={
            "user": user_info,
            "report": rep_data,
            "metrics": payload,
            "current_page": "analytics",
        },
    )


@router.get("/suppliers", response_class=HTMLResponse)
async def supplier_quality_scorecard(
    request: Request,
    period: str = Query("12m"),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require_any(("stock", "read"), ("qc", "read"), ("purchase", "read"), ("tests", "read"))),
):
    """Renders supplier quality scorecard aggregated across yarn lots and incoming lab tests."""
    user_info = {
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }
    start_d, end_d = resolve_period_dates(period)

    # Aggregate metrics grouped by supplier
    rows = await conn.fetch(
        """
        SELECT 
          s.id,
          s.supplier_code,
          s.name AS supplier_name,
          s.active,
          COUNT(DISTINCT yl.id) AS total_lots_received,
          COUNT(DISTINCT yl.id) FILTER (WHERE yl.qc_status = 'Approved') AS lots_approved,
          COUNT(DISTINCT yl.id) FILTER (WHERE yl.qc_status = 'Rejected') AS lots_rejected,
          COUNT(DISTINCT yl.id) FILTER (WHERE yl.qc_status = 'Quarantine') AS lots_quarantine,
          COALESCE(SUM(yl.qty_received), 0.0) AS total_kg_received,
          COUNT(lt.id) AS total_tests_run,
          COUNT(lt.id) FILTER (WHERE lt.verdict = 'PASS') AS passed_tests,
          COUNT(lt.id) FILTER (WHERE lt.verdict = 'FAIL') AS failed_tests,
          ROUND(COALESCE(COUNT(lt.id) FILTER (WHERE lt.verdict = 'PASS') * 100.0 / NULLIF(COUNT(lt.id), 0), 0.0), 1) AS test_pass_rate,
          ROUND(COALESCE(AVG(EXTRACT(EPOCH FROM (yl.released_at - yl.created_at)) / 86400.0) FILTER (WHERE yl.released_at IS NOT NULL), 0.0), 1) AS avg_turnaround_days
        FROM suppliers s
        LEFT JOIN yarn_lots yl ON yl.supplier_id = s.id AND yl.received_date BETWEEN $1 AND $2
        LEFT JOIN lab_tests lt ON lt.yarn_lot_id = yl.id
        GROUP BY s.id, s.supplier_code, s.name, s.active
        ORDER BY total_lots_received DESC, s.name ASC;
        """,
        start_d, end_d
    )

    return templates.TemplateResponse(
        request=request,
        name="analytics/suppliers.html",
        context={
            "user": user_info,
            "period": period,
            "start_date": start_d,
            "end_date": end_d,
            "scorecards": [dict(r) for r in rows],
            "current_page": "analytics",
        },
    )
