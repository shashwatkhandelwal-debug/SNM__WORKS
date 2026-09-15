from datetime import date, datetime
import logging
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from fastapi import HTTPException
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)

logger = logging.getLogger("snm_works.materials_service")


async def fetch_approved_yarn_lots(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    """
    Fetches Approved yarn lots with available stock (qty_remaining > 0).
    """
    rows = await conn.fetch(
        """
        SELECT 
            id::text,
            lot_no,
            supplier_lot_no,
            supplier_name,
            yarn_type,
            denier,
            filament_count,
            qty_received,
            qty_issued,
            qty_remaining,
            unit,
            storage_location
        FROM yarn_lots
        WHERE qc_status = 'Approved' AND qty_remaining > 0
        ORDER BY lot_no ASC;
        """
    )
    return [dict(r) for r in rows]


async def fetch_active_jobs(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    """
    Fetches active (non-completed, non-cancelled) production jobs.
    """
    rows = await conn.fetch(
        """
        SELECT 
            id::text,
            job_no,
            product,
            status,
            qty_ordered,
            unit
        FROM jobs
        WHERE status NOT IN ('Completed', 'Cancelled', 'completed', 'cancelled')
        ORDER BY job_no DESC;
        """
    )
    return [dict(r) for r in rows]


async def fetch_material_issue_options(conn: asyncpg.Connection) -> Dict[str, Any]:
    """
    Returns picker options for material issues:
    active jobs and approved yarn lots with positive balance.
    """
    jobs = await fetch_active_jobs(conn)
    lots = await fetch_approved_yarn_lots(conn)
    return {
        "jobs": jobs,
        "approved_lots": lots,
    }


async def issue_job_material(
    conn: asyncpg.Connection,
    user_id: Any,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Core business logic to issue material from an Approved yarn lot against a Job Card.
    - Generates atomic sequential issue_no: ISS-YYYY-NNNN.
    - Validates job_id, yarn_lot_id, qty_issued > 0.
    - Inserts into job_material_issues.
    - The PostgreSQL trigger process_job_material_issue() handles atomic stock validation
      and deduction (quarantine gating, overflow prevention).
    - Returns dictionary with issue record details and remaining lot balance.
    """
    job_id = data.get("job_id")
    yarn_lot_id = data.get("yarn_lot_id")

    if not job_id or not str(job_id).strip():
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Job ID is required.",
        )
    if not yarn_lot_id or not str(yarn_lot_id).strip():
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Yarn Lot ID is required.",
        )

    try:
        j_uuid = uuid.UUID(str(job_id).strip())
        l_uuid = uuid.UUID(str(yarn_lot_id).strip())
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Invalid Job ID or Yarn Lot ID format.",
        )

    try:
        qty_issued = float(data.get("qty_issued", 0.0))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Issued quantity must be a valid number.",
        )

    if qty_issued <= 0:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Issued quantity must be greater than 0.",
        )

    user_uuid = None
    if user_id:
        try:
            user_uuid = uuid.UUID(str(user_id))
        except (ValueError, TypeError):
            user_uuid = None

    issued_date = data.get("issued_date")
    parsed_date = date.today()
    if isinstance(issued_date, date):
        parsed_date = issued_date
    elif issued_date and str(issued_date).strip():
        try:
            parsed_date = datetime.strptime(str(issued_date).strip(), "%Y-%m-%d").date()
        except ValueError:
            parsed_date = date.today()

    unit = str(data.get("unit") or "kg").strip()
    remarks = data.get("remarks")
    clean_remarks = str(remarks).strip() if remarks and str(remarks).strip() else None

    # Generate sequence issue number: ISS-YYYY-NNNN
    current_year = date.today().year
    seq_val = await conn.fetchval("SELECT nextval('material_issue_seq');")
    issue_no = f"ISS-{current_year}-{seq_val:04d}"

    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO job_material_issues (
                    issue_no,
                    job_id,
                    yarn_lot_id,
                    qty_issued,
                    unit,
                    issued_date,
                    issued_by,
                    remarks
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id::text, issue_no, job_id::text, yarn_lot_id::text, qty_issued, unit, issued_date::text, remarks, created_at;
                """,
                issue_no,
                j_uuid,
                l_uuid,
                qty_issued,
                unit,
                parsed_date,
                user_uuid,
                clean_remarks,
            )

            # Fetch updated remaining balance of the yarn lot post-trigger
            rem_qty = await conn.fetchval(
                "SELECT qty_remaining FROM yarn_lots WHERE id = $1;",
                l_uuid,
            )

            result = dict(row)
            result["remaining_lot_qty"] = float(rem_qty) if rem_qty is not None else 0.0
            return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Material issue failed: {e}")
        # Surface database trigger validation error
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e).replace("RAISE EXCEPTION", "").strip(),
        )


async def list_material_issues(
    conn: asyncpg.Connection,
    job_id: Optional[str] = None,
    yarn_lot_id: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Fetches list of material issues with joined job, yarn lot, and issuer profile.
    """
    query = """
        SELECT 
            i.id::text as id,
            i.issue_no,
            i.issued_date::text as issued_date,
            i.qty_issued,
            i.unit,
            i.remarks,
            j.id::text as job_id,
            j.job_no,
            j.product as job_product,
            y.id::text as yarn_lot_id,
            y.lot_no,
            y.yarn_type,
            y.denier,
            y.qty_remaining as remaining_lot_qty,
            p.full_name as issued_by_name,
            i.created_at
        FROM job_material_issues i
        JOIN jobs j ON j.id = i.job_id
        JOIN yarn_lots y ON y.id = i.yarn_lot_id
        LEFT JOIN profiles p ON p.id = i.issued_by
        WHERE 1=1
    """
    params = []
    idx = 1

    if job_id and str(job_id).strip():
        query += f" AND (i.job_id::text = ${idx} OR j.job_no ILIKE ${idx})"
        params.append(str(job_id).strip())
        idx += 1

    if yarn_lot_id and str(yarn_lot_id).strip():
        query += f" AND (i.yarn_lot_id::text = ${idx} OR y.lot_no ILIKE ${idx})"
        params.append(str(yarn_lot_id).strip())
        idx += 1

    if q and q.strip():
        search = f"%{q.strip()}%"
        query += f" AND (i.issue_no ILIKE ${idx} OR j.job_no ILIKE ${idx} OR y.lot_no ILIKE ${idx} OR y.yarn_type ILIKE ${idx})"
        params.append(search)
        idx += 1

    query += f" ORDER BY i.issued_date DESC, i.created_at DESC LIMIT {int(limit)}"

    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]
