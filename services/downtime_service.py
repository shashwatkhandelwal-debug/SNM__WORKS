from datetime import date, datetime
import logging
import re
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from asyncpg.exceptions import CheckViolationError, UniqueViolationError
from fastapi import HTTPException
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_503_SERVICE_UNAVAILABLE,
)

logger = logging.getLogger("snm_works.downtime_service")

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


def format_duration(minutes: Optional[float]) -> str:
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


async def fetch_machines(conn: asyncpg.Connection) -> List[str]:
    """
    Fetches configured machines list from masters table.
    """
    machine_rows = await conn.fetch(
        "SELECT value FROM masters WHERE list_name = 'machines' ORDER BY sort_order;"
    )
    return [m["value"] for m in machine_rows]


async def fetch_active_jobs(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    """
    Fetches active jobs for downtime job selection.
    """
    job_rows = await conn.fetch(
        """
        SELECT id::text, job_no, product, machine 
        FROM jobs 
        WHERE status NOT IN ('Completed', 'Cancelled', 'completed', 'cancelled')
        ORDER BY job_no DESC
        """
    )
    return [dict(j) for j in job_rows]


async def fetch_downtime_options(conn: asyncpg.Connection) -> Dict[str, Any]:
    """
    Returns machines, shifts, reasons, and active jobs for dropdown pickers.
    """
    machines = await fetch_machines(conn)
    jobs = await fetch_active_jobs(conn)
    return {
        "machines": machines,
        "shifts": SHIFTS,
        "reasons": REASONS,
        "jobs": jobs,
    }


async def record_downtime_log(
    conn: asyncpg.Connection,
    user_id: Any,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Core business logic to insert a new downtime log with race-safe sequence numbering.
    - Validates operator_id from user_id.
    - Validates non-negative minutes.
    - Handles concurrency retry loop on UniqueViolationError.
    - Returns dictionary containing inserted downtime details and formatted duration.
    """
    if not user_id:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="Authenticated operator ID is missing.",
        )

    try:
        operator_uuid = uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="Invalid operator ID format.",
        )

    try:
        minutes = float(data.get("minutes", 0.0))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Downtime minutes must be a valid number.",
        )

    if minutes < 0:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Downtime minutes cannot be negative.",
        )

    logged_on = data.get("logged_on")
    parsed_date = date.today()
    if isinstance(logged_on, date):
        parsed_date = logged_on
    elif logged_on and str(logged_on).strip():
        try:
            parsed_date = datetime.strptime(str(logged_on).strip(), "%Y-%m-%d").date()
        except ValueError:
            parsed_date = date.today()

    shift = str(data.get("shift") or "Shift A (06:00-14:00)").strip()
    machine = str(data.get("machine") or "").strip()
    if not machine:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Machine is required.",
        )

    reason = str(data.get("reason") or "").strip()
    if not reason:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Reason is required.",
        )

    job_id = data.get("job_id")
    job_uuid = None
    if job_id and str(job_id).strip():
        try:
            job_uuid = uuid.UUID(str(job_id).strip())
        except (ValueError, TypeError):
            job_uuid = None

    remarks = data.get("remarks")
    clean_remarks = str(remarks).strip() if remarks and str(remarks).strip() else None

    max_retries = 20
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
                        $1::uuid, $2, $3, $4, $5, $6, $7, $8::uuid, $9::uuid, $10
                    )
                    RETURNING 
                        id::text,
                        log_no,
                        logged_on::text,
                        shift,
                        machine,
                        reason,
                        minutes,
                        job_id::text,
                        operator_id::text,
                        remarks,
                        created_at;
                    """,
                    new_id,
                    log_no,
                    parsed_date,
                    shift,
                    machine,
                    reason,
                    minutes,
                    job_uuid,
                    operator_uuid,
                    clean_remarks,
                )
                res = dict(row)
                res["formatted_duration"] = format_duration(float(res["minutes"] or 0.0))
                return res
        except UniqueViolationError:
            if attempt == max_retries - 1:
                raise HTTPException(
                    status_code=HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Could not generate unique Log No due to high concurrency. Please retry.",
                )
            continue
        except CheckViolationError as e:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=f"Validation constraint error: {str(e)}",
            )

    raise HTTPException(
        status_code=HTTP_503_SERVICE_UNAVAILABLE,
        detail="Could not insert downtime record. Please retry.",
    )


async def list_downtime_records(
    conn: asyncpg.Connection,
    machine_filter: Optional[str] = "all",
    shift_filter: Optional[str] = "all",
    reason_filter: Optional[str] = "all",
    q: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Fetches list of downtime records with job and operator details and formatted duration.
    """
    query = """
        SELECT 
            d.id::text as id,
            d.log_no,
            d.logged_on::text as logged_on,
            d.shift,
            d.machine,
            d.reason,
            d.minutes,
            d.job_id::text as job_id,
            j.job_no,
            j.product as job_product,
            d.operator_id::text as operator_id,
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

    if machine_filter and machine_filter.strip() and machine_filter.lower() != "all":
        query += f" AND d.machine = ${param_idx}"
        params.append(machine_filter.strip())
        param_idx += 1

    if shift_filter and shift_filter.strip() and shift_filter.lower() != "all":
        query += f" AND d.shift = ${param_idx}"
        params.append(shift_filter.strip())
        param_idx += 1

    if reason_filter and reason_filter.strip() and reason_filter.lower() != "all":
        query += f" AND d.reason = ${param_idx}"
        params.append(reason_filter.strip())
        param_idx += 1

    if q and q.strip():
        search = f"%{q.strip()}%"
        query += f" AND (d.log_no ILIKE ${param_idx} OR d.machine ILIKE ${param_idx} OR d.reason ILIKE ${param_idx} OR j.job_no ILIKE ${param_idx})"
        params.append(search)
        param_idx += 1

    query += f" ORDER BY d.logged_on DESC, d.created_at DESC LIMIT {int(limit)}"

    rows = await conn.fetch(query, *params)
    entries = []
    for r in rows:
        item = dict(r)
        mins = float(item["minutes"] or 0.0)
        item["formatted_duration"] = format_duration(mins)
        entries.append(item)
    return entries
