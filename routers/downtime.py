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
import re
import uuid
from typing import Any, Dict, List, Optional
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

router = APIRouter(prefix="/downtime", tags=["Downtime Telemetry"])
templates = Jinja2Templates(directory="templates")

SHIFTS = [
    "Shift A (06:00-14:00)",
    "Shift B (14:00-22:00)",
    "Night Shift (22:00-06:00)",
    "General Shift (09:00-17:30)",
]

REASONS = [
    "Mechanical Breakdown",
    "Electrical Fault",
    "Yarn Breakage / Warp Knotting",
    "Weft Package Change",
    "Beam Change / Creeling",
    "Preventive Maintenance",
    "Shade Matching / Dyebath Wait",
    "No Operator / Absenteeism",
    "Power Outage / Utilities",
    "Cleaning & Lubrication",
    "Quality Inspection Hold",
    "Other Stoppage",
]


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
    q: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("downtime", "read")),
):
    """
    GET /downtime -- Register of shop-floor machine stoppages and telemetry metrics.
    """
    query = """
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
        WHERE 1=1
    """
    params = []
    param_idx = 1

    if machine_filter and machine_filter != "all":
        query += f" AND d.machine = ${param_idx}"
        params.append(machine_filter)
        param_idx += 1

    if shift_filter and shift_filter != "all":
        query += f" AND d.shift = ${param_idx}"
        params.append(shift_filter)
        param_idx += 1

    if reason_filter and reason_filter != "all":
        query += f" AND d.reason = ${param_idx}"
        params.append(reason_filter)
        param_idx += 1

    if q and q.strip():
        search = f"%{q.strip()}%"
        query += f" AND (d.log_no ILIKE ${param_idx} OR d.machine ILIKE ${param_idx} OR d.reason ILIKE ${param_idx} OR j.job_no ILIKE ${param_idx})"
        params.append(search)
        param_idx += 1

    query += " ORDER BY d.logged_on DESC, d.created_at DESC"

    rows = await conn.fetch(query, *params)
    entries = []
    total_minutes = 0.0
    reason_counts: Dict[str, int] = {}
    machine_counts: Dict[str, float] = {}

    for r in rows:
        item = dict(r)
        mins = float(item["minutes"] or 0.0)
        item["formatted_duration"] = format_duration(mins)
        total_minutes += mins
        entries.append(item)

        # Aggregate stats
        r_name = item["reason"]
        reason_counts[r_name] = reason_counts.get(r_name, 0) + 1

        m_name = item["machine"]
        machine_counts[m_name] = machine_counts.get(m_name, 0.0) + mins

    # Fetch machines from masters
    machine_rows = await conn.fetch("SELECT value FROM masters WHERE list_name = 'machines' ORDER BY sort_order;")
    machines = [m["value"] for m in machine_rows]

    top_reason = max(reason_counts, key=reason_counts.get) if reason_counts else "--"
    top_machine = max(machine_counts, key=machine_counts.get) if machine_counts else "--"

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
            "total_count": len(entries),
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
    operator_id = user.get("id")
    if not operator_id:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Authenticated operator ID is missing.")

    if minutes < 0:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Downtime minutes cannot be negative.")

    max_retries = 20
    created_id = None

    for attempt in range(max_retries):
        log_no = await get_next_log_no(conn)
        new_id = uuid.uuid4()

        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO downtime (
                        id,
                        log_no,
                        logged_on,
                        shift,
                        machine,
                        reason,
                        minutes,
                        job_id,
                        operator_id,
                        remarks
                    ) VALUES (
                        $1::uuid, $2, COALESCE($3::date, CURRENT_DATE), $4, $5, $6, $7, $8::uuid, $9::uuid, $10
                    )
                    RETURNING id::text;
                    """,
                    new_id,
                    log_no,
                    logged_on.strip() if logged_on and logged_on.strip() else None,
                    shift.strip(),
                    machine.strip(),
                    reason.strip(),
                    minutes,
                    uuid.UUID(job_id) if job_id and job_id.strip() else None,
                    uuid.UUID(operator_id),
                    remarks.strip() if remarks else None,
                )
                created_id = row["id"]
                break
        except UniqueViolationError:
            if attempt == max_retries - 1:
                raise HTTPException(
                    status_code=HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Could not generate unique Log No due to high concurrency. Please retry.",
                )
            await asyncio.sleep(0.01 * (attempt + 1))
        except CheckViolationError as e:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=f"Validation constraint error: {str(e)}",
            )

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
