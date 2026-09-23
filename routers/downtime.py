"""
Downtime Router -- SNM Works
=============================================================================
Operational telemetry and shop-floor machine stoppage logging.

Permissions (role_permissions):
  - downtime.read: chief_executive, chief_operating, chief_people, machine_operator, maintenance_officer, production_manager, shift_supervisor
  - downtime.create: chief_operating, machine_operator, maintenance_officer, production_manager, shift_supervisor
  - downtime.update: chief_operating, maintenance_officer, production_manager
=============================================================================
"""

import asyncio
import math
import re
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from asyncpg.exceptions import CheckViolationError, UniqueViolationError
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from auth.dependencies import current_user, require
from database import get_db
from services.downtime_service import (
    REASONS,
    SHIFTS,
    fetch_active_jobs,
    fetch_machines,
    format_duration,
    get_next_log_no,
    record_downtime_log,
)

router = APIRouter(prefix="/downtime", tags=["Downtime Telemetry"])
templates = Jinja2Templates(directory="templates")


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def get_next_log_no(conn: asyncpg.Connection) -> str:
    """
    Generates the next sequential Downtime Log Number in DT-XXXX format.
    """
    rows = await conn.fetch("SELECT log_no FROM downtime WHERE log_no LIKE 'DT-%'")
    max_num = 0
    pattern = re.compile(r"^DT-(\d+)$")

    for r in rows:
        val = r["log_no"]
        m = pattern.match(val)
        if m:
            num = int(m.group(1))
            if num > max_num:
                max_num = num

    return f"DT-{max_num + 1:04d}"


def format_duration(minutes: float) -> str:
    """
    Formats minutes into a human-readable string (e.g. 135 mins -> '2h 15m').
    """
    if minutes is None:
        return "0m"
    mins = int(round(minutes))
    hours = mins // 60
    rem_mins = mins % 60
    if hours > 0 and rem_mins > 0:
        return f"{hours}h {rem_mins}m"
    elif hours > 0:
        return f"{hours}h"
    else:
        return f"{rem_mins}m"


# ============================================================================
# 1. LIST DOWNTIME (REGISTER)
# ============================================================================

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def list_downtime(
    request: Request,
    machine_filter: Optional[str] = "all",
    shift_filter: Optional[str] = "all",
    reason_filter: Optional[str] = "all",
    search: Optional[str] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("downtime", "read")),
):
    """
    GET /downtime -- Register of shop-floor machine stoppages and telemetry metrics.
    """
    where_clauses = ["1=1"]
    params = []
    param_idx = 1

    if machine_filter and machine_filter != "all":
        where_clauses.append(f"d.machine = ${param_idx}")
        params.append(machine_filter)
        param_idx += 1

    if shift_filter and shift_filter != "all":
        where_clauses.append(f"d.shift = ${param_idx}")
        params.append(shift_filter)
        param_idx += 1

    if reason_filter and reason_filter != "all":
        where_clauses.append(f"d.reason = ${param_idx}")
        params.append(reason_filter)
        param_idx += 1

    search_term = (search or q or "").strip()
    if search_term:
        search_pattern = f"%{search_term}%"
        where_clauses.append(f"(d.log_no ILIKE ${param_idx} OR d.machine ILIKE ${param_idx} OR d.reason ILIKE ${param_idx} OR d.remarks ILIKE ${param_idx} OR j.job_no ILIKE ${param_idx})")
        params.append(search_pattern)
        param_idx += 1

    where_sql = " AND ".join(where_clauses)

    # 1. Total minutes across full dataset (Rule: Do NOT compute totals from paginated slice)
    agg_query = f"""
        SELECT COALESCE(SUM(d.minutes), 0.0)::float AS total_minutes
        FROM downtime d
        LEFT JOIN jobs j ON d.job_id = j.id
        WHERE {where_sql}
    """
    total_minutes_val = await conn.fetchval(agg_query, *params)
    total_minutes = float(total_minutes_val or 0.0)

    # Top reason across full dataset
    top_reason_query = f"""
        SELECT d.reason, COUNT(*) as cnt
        FROM downtime d
        LEFT JOIN jobs j ON d.job_id = j.id
        WHERE {where_sql}
        GROUP BY d.reason
        ORDER BY cnt DESC, d.reason ASC
        LIMIT 1
    """
    top_reason_row = await conn.fetchrow(top_reason_query, *params)
    top_reason = top_reason_row["reason"] if top_reason_row else "--"

    # Top machine across full dataset
    top_machine_query = f"""
        SELECT d.machine, SUM(d.minutes) as tot_mins
        FROM downtime d
        LEFT JOIN jobs j ON d.job_id = j.id
        WHERE {where_sql}
        GROUP BY d.machine
        ORDER BY tot_mins DESC, d.machine ASC
        LIMIT 1
    """
    top_machine_row = await conn.fetchrow(top_machine_query, *params)
    top_machine = top_machine_row["machine"] if top_machine_row else "--"

    # 2. Paginated row query with windowed total_count
    offset = (page - 1) * page_size
    query = f"""
        SELECT 
            d.id::text,
            d.log_no,
            d.logged_on::text,
            d.shift,
            d.machine,
            d.reason,
            d.minutes,
            d.job_id::text,
            j.job_no,
            j.product as job_product,
            d.operator_id::text,
            p.full_name as operator_name,
            d.remarks,
            d.created_at,
            COUNT(*) OVER() AS total_count
        FROM downtime d
        LEFT JOIN jobs j ON d.job_id = j.id
        LEFT JOIN profiles p ON d.operator_id = p.id
        WHERE {where_sql}
        ORDER BY d.logged_on DESC, d.created_at DESC
        LIMIT ${param_idx} OFFSET ${param_idx + 1}
    """
    row_params = list(params) + [page_size, offset]
    rows = await conn.fetch(query, *row_params)

    entries = []
    for r in rows:
        item = dict(r)
        mins = float(item["minutes"] or 0.0)
        item["formatted_duration"] = format_duration(mins)
        entries.append(item)

    total_count = int(rows[0]["total_count"]) if rows else 0
    total_pages = max(1, math.ceil(total_count / page_size))

    # Fetch machines from masters
    machine_rows = await conn.fetch("SELECT value FROM masters WHERE list_name = 'machines' ORDER BY sort_order;")
    machines = [m["value"] for m in machine_rows]

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="downtime/list.html",
        context={
            "user": user_info,
            "entries": entries,
            "total_count": total_count,
            "total_pages": total_pages,
            "page": page,
            "page_size": page_size,
            "total_minutes": int(round(total_minutes)),
            "total_hours": round(total_minutes / 60.0, 1),
            "top_reason": top_reason,
            "top_machine": top_machine,
            "selected_machine": machine_filter,
            "selected_shift": shift_filter,
            "selected_reason": reason_filter,
            "search_query": q or "",
            "machines": machines,
            "shifts": SHIFTS,
            "reasons": REASONS,
            "current_page": "downtime",
            "current_func": "OPS",
        },
    )


# ============================================================================
# 2. NEW DOWNTIME ENTRY FORM (GET)
# ============================================================================

@router.get("/new", response_class=HTMLResponse)
async def new_downtime_form(
    request: Request,
    job_id: Optional[str] = None,
    machine: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("downtime", "create")),
):
    """
    GET /downtime/new -- Shop-floor downtime log entry form.
    """
    next_log_no = await get_next_log_no(conn)

    machine_rows = await conn.fetch("SELECT value FROM masters WHERE list_name = 'machines' ORDER BY sort_order;")
    machines = [m["value"] for m in machine_rows]

    job_rows = await conn.fetch(
        """
        SELECT id::text, job_no, product, machine 
        FROM jobs 
        WHERE status NOT IN ('Completed', 'Cancelled')
        ORDER BY job_no DESC
        """
    )
    jobs = [dict(j) for j in job_rows]

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="downtime/form.html",
        context={
            "user": user_info,
            "is_edit": False,
            "log_no_preview": next_log_no,
            "machines": machines,
            "selected_machine": machine or (machines[0] if machines else "Loom 01 (Needle Loom 4-Space)"),
            "shifts": SHIFTS,
            "reasons": REASONS,
            "jobs": jobs,
            "selected_job_id": job_id or "",
            "current_page": "downtime",
            "current_func": "OPS",
        },
    )


# ============================================================================
# 3. CREATE DOWNTIME ENTRY (POST)
# ============================================================================

@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_downtime(
    request: Request,
    logged_on: Optional[str] = Form(None),
    shift: str = Form("Shift A (06:00-14:00)"),
    machine: str = Form(...),
    reason: str = Form(...),
    minutes: float = Form(...),
    job_id: Optional[str] = Form(None),
    remarks: Optional[str] = Form(None),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("downtime", "create")),
):
    """
    POST /downtime -- Creates a new downtime stoppage log with race-safe sequence numbering.
    """
    data = {
        "logged_on": logged_on,
        "shift": shift,
        "machine": machine,
        "reason": reason,
        "minutes": minutes,
        "job_id": job_id,
        "remarks": remarks,
    }
    result = await record_downtime_log(conn, user.get("id"), data)
    created_id = result["id"]

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        from fastapi import Response
        response = Response(status_code=HTTP_200_OK)
        response.headers["HX-Redirect"] = f"/downtime/{created_id}"
        return response

    return RedirectResponse(url=f"/downtime/{created_id}", status_code=HTTP_303_SEE_OTHER)


# ============================================================================
# 4. VIEW DOWNTIME DETAIL (GET)
# ============================================================================

@router.get("/{downtime_id}", response_class=HTMLResponse)
async def view_downtime_detail(
    downtime_id: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("downtime", "read")),
):
    """
    GET /downtime/{downtime_id} -- View single stoppage telemetry record.
    """
    row = await conn.fetchrow(
        """
        SELECT 
            d.id::text,
            d.log_no,
            d.logged_on::text,
            d.shift,
            d.machine,
            d.reason,
            d.minutes,
            d.job_id::text,
            j.job_no,
            j.product as job_product,
            d.operator_id::text,
            p.full_name as operator_name,
            d.remarks,
            d.created_at
        FROM downtime d
        LEFT JOIN jobs j ON d.job_id = j.id
        LEFT JOIN profiles p ON d.operator_id = p.id
        WHERE d.id = $1::uuid
        """,
        downtime_id,
    )

    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Downtime record not found.")

    entry = dict(row)
    entry["formatted_duration"] = format_duration(float(entry["minutes"] or 0.0))

    # Check if current user can edit (holds downtime.update)
    can_edit = await conn.fetchval("SELECT auth_can('downtime', 'update')")

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="downtime/detail.html",
        context={
            "user": user_info,
            "d": entry,
            "can_edit": bool(can_edit),
            "current_page": "downtime",
            "current_func": "OPS",
        },
    )


# ============================================================================
# 5. EDIT DOWNTIME FORM (GET)
# ============================================================================

@router.get("/{downtime_id}/edit", response_class=HTMLResponse)
async def edit_downtime_form(
    downtime_id: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("downtime", "update")),
):
    """
    GET /downtime/{downtime_id}/edit -- Form to edit a recorded downtime log.
    Guarded by downtime.update (production_manager, maintenance_officer, chief_operating).
    """
    row = await conn.fetchrow(
        "SELECT * FROM downtime WHERE id = $1::uuid;",
        downtime_id,
    )
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Downtime record not found.")

    machine_rows = await conn.fetch("SELECT value FROM masters WHERE list_name = 'machines' ORDER BY sort_order;")
    machines = [m["value"] for m in machine_rows]

    job_rows = await conn.fetch(
        """
        SELECT id::text, job_no, product 
        FROM jobs 
        WHERE status NOT IN ('Completed', 'Cancelled')
        ORDER BY job_no DESC
        """
    )
    jobs = [dict(j) for j in job_rows]

    item_dict = dict(row)
    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="downtime/form.html",
        context={
            "user": user_info,
            "is_edit": True,
            "d": item_dict,
            "log_no_preview": item_dict["log_no"],
            "machines": machines,
            "selected_machine": item_dict.get("machine"),
            "shifts": SHIFTS,
            "reasons": REASONS,
            "jobs": jobs,
            "selected_job_id": str(item_dict.get("job_id") or ""),
            "current_page": "downtime",
            "current_func": "OPS",
        },
    )


# ============================================================================
# 6. UPDATE DOWNTIME ENTRY (POST)
# ============================================================================

@router.post("/{downtime_id}/update", response_class=HTMLResponse)
async def update_downtime(
    downtime_id: str,
    request: Request,
    logged_on: Optional[str] = Form(None),
    shift: str = Form("Shift A (06:00-14:00)"),
    machine: str = Form(...),
    reason: str = Form(...),
    minutes: float = Form(...),
    job_id: Optional[str] = Form(None),
    remarks: Optional[str] = Form(None),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("downtime", "update")),
):
    """
    POST /downtime/{downtime_id}/update -- Updates downtime record.
    """
    if minutes < 0:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Downtime minutes cannot be negative.")

    try:
        await conn.execute(
            """
            UPDATE downtime SET
                logged_on = COALESCE($1::date, logged_on),
                shift = $2,
                machine = $3,
                reason = $4,
                minutes = $5,
                job_id = $6::uuid,
                remarks = $7
            WHERE id = $8::uuid;
            """,
            logged_on.strip() if logged_on and logged_on.strip() else None,
            shift.strip(),
            machine.strip(),
            reason.strip(),
            minutes,
            uuid.UUID(job_id) if job_id and job_id.strip() else None,
            remarks.strip() if remarks else None,
            uuid.UUID(downtime_id),
        )
    except CheckViolationError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Validation constraint error: {str(e)}",
        )

    return RedirectResponse(url=f"/downtime/{downtime_id}", status_code=HTTP_303_SEE_OTHER)
