"""
Despatch Router -- SNM Works
=============================================================================
Manages packaging, GST invoice reference, E-Way Bill documentation,
transport consignment tracking, QC hold gating, and QA release sign-off.

Permissions (role_permissions):
  - despatch.read: accounts_officer, chief_commercial, chief_compliance, chief_executive, chief_financial, chief_operating, chief_quality, export_executive, production_manager, store_keeper
  - despatch.create: chief_operating, export_executive, production_manager, store_keeper
  - despatch.update: accounts_officer, chief_operating, export_executive, store_keeper
  - despatch.approve: chief_quality (EXCLUSIVE Quality Assurance Release)

Non-Negotiable Rule 6 (Segregation of Duties):
  A person cannot approve a dispatch consignment they created.
  Enforced by database CHECK constraint: despatch_no_self_approval.

Database QC Hold Gate:
  Enforced by database trigger trg_check_despatch_qc_hold calling job_on_hold(job_id).
=============================================================================
"""

import asyncio
import datetime
import logging
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
from services.tally_gateway import sync_sales_voucher_for_despatch

logger = logging.getLogger("snm.despatch")

router = APIRouter(prefix="/despatch", tags=["Despatch & Logistics"])
templates = Jinja2Templates(directory="templates")

STATUSES = ["Packed", "Ready for Dispatch", "Dispatched", "In Transit", "Delivered", "Cancelled"]


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def get_next_despatch_no(conn: asyncpg.Connection) -> str:
    """
    Generates the next sequential Despatch Note Number in DSP-XXXX format.
    """
    rows = await conn.fetch("SELECT despatch_no FROM despatch WHERE despatch_no LIKE 'DSP-%'")
    max_num = 0
    pattern = re.compile(r"^DSP-(\d+)$")

    for r in rows:
        val = r["despatch_no"]
        m = pattern.match(val)
        if m:
            num = int(m.group(1))
            if num > max_num:
                max_num = num

    return f"DSP-{max_num + 1:04d}"


# ============================================================================
# 1. LIST DESPATCHES (REGISTER)
# ============================================================================

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def list_despatches(
    request: Request,
    status_filter: Optional[str] = "all",
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("despatch", "read")),
):
    """
    GET /despatch -- Register of shipments, dispatch notes, and transport tracking.
    """
    where_clauses = ["1=1"]
    params = []
    param_idx = 1

    if status_filter and status_filter != "all":
        where_clauses.append(f"d.status = ${param_idx}")
        params.append(status_filter)
        param_idx += 1

    if q and q.strip():
        search = f"%{q.strip()}%"
        where_clauses.append(f"(d.despatch_no ILIKE ${param_idx} OR d.invoice_no ILIKE ${param_idx} OR d.eway_bill ILIKE ${param_idx} OR d.destination ILIKE ${param_idx} OR j.job_no ILIKE ${param_idx} OR c.name ILIKE ${param_idx})")
        params.append(search)
        param_idx += 1

    where_sql = " AND ".join(where_clauses)

    # 1. Separate full-dataset aggregation query (Rule: Do NOT compute totals from paginated slice)
    agg_query = f"""
        SELECT 
            COALESCE(SUM(d.qty), 0.0)::float AS total_qty,
            COALESCE(SUM(d.rolls), 0)::int AS total_rolls,
            COUNT(*) FILTER (WHERE d.status IN ('Packed', 'Ready for Dispatch'))::int AS pending_release_count
        FROM despatch d
        JOIN jobs j ON d.job_id = j.id
        LEFT JOIN customers c ON j.customer_id = c.id
        WHERE {where_sql}
    """
    agg_row = await conn.fetchrow(agg_query, *params)
    total_qty = float(agg_row["total_qty"]) if agg_row else 0.0
    total_rolls = int(agg_row["total_rolls"]) if agg_row else 0
    pending_release_count = int(agg_row["pending_release_count"]) if agg_row else 0

    # 2. Paginated row query with windowed total_count
    offset = (page - 1) * page_size
    query = f"""
        SELECT 
            d.id::text,
            d.despatch_no,
            d.despatched_on::text,
            d.job_id::text,
            j.job_no,
            j.product as job_product,
            c.name as customer_name,
            d.invoice_no,
            d.eway_bill,
            d.qty,
            d.unit,
            d.rolls,
            d.gross_wt,
            d.transporter,
            d.lr_no,
            d.destination,
            d.status,
            d.override_reason,
            job_on_hold(d.job_id) as on_qc_hold,
            d.created_by::text,
            u_cr.full_name as creator_name,
            d.approved_by::text,
            u_ap.full_name as approver_name,
            d.created_at,
            COUNT(*) OVER() AS total_count
        FROM despatch d
        JOIN jobs j ON d.job_id = j.id
        LEFT JOIN customers c ON j.customer_id = c.id
        LEFT JOIN profiles u_cr ON d.created_by = u_cr.id
        LEFT JOIN profiles u_ap ON d.approved_by = u_ap.id
        WHERE {where_sql}
        ORDER BY d.despatched_on DESC, d.created_at DESC
        LIMIT ${param_idx} OFFSET ${param_idx + 1}
    """
    row_params = list(params) + [page_size, offset]
    rows = await conn.fetch(query, *row_params)

    entries = [dict(r) for r in rows]
    total_count = int(rows[0]["total_count"]) if rows else 0
    total_pages = max(1, math.ceil(total_count / page_size))

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="despatch/list.html",
        context={
            "user": user_info,
            "entries": entries,
            "total_count": total_count,
            "total_pages": total_pages,
            "page": page,
            "page_size": page_size,
            "total_qty": round(total_qty, 1),
            "total_rolls": total_rolls,
            "pending_release_count": pending_release_count,
            "selected_status": status_filter,
            "search_query": q or "",
            "statuses": STATUSES,
            "current_page": "despatch",
            "current_func": "OPS",
        },
    )


# ============================================================================
# 2. NEW DESPATCH FORM (GET)
# ============================================================================

@router.get("/new", response_class=HTMLResponse)
async def new_despatch_form(
    request: Request,
    job_id: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("despatch", "create")),
):
    """
    GET /despatch/new -- Create a new dispatch consignment note.
    """
    next_despatch_no = await get_next_despatch_no(conn)

    job_rows = await conn.fetch(
        """
        SELECT 
            j.id::text, 
            j.job_no, 
            j.product, 
            j.qty_ordered, 
            j.unit,
            c.name as customer_name,
            job_on_hold(j.id) as on_qc_hold
        FROM jobs j
        LEFT JOIN customers c ON j.customer_id = c.id
        WHERE j.status NOT IN ('Completed', 'Cancelled')
        ORDER BY j.job_no DESC
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
        name="despatch/form.html",
        context={
            "user": user_info,
            "is_edit": False,
            "despatch_no_preview": next_despatch_no,
            "jobs": jobs,
            "selected_job_id": job_id or (jobs[0]["id"] if jobs else ""),
            "statuses": STATUSES,
            "current_page": "despatch",
            "current_func": "OPS",
        },
    )


# ============================================================================
# 3. CREATE DESPATCH (POST)
# ============================================================================

@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_despatch(
    request: Request,
    job_id: str = Form(...),
    despatched_on: Optional[str] = Form(None),
    invoice_no: Optional[str] = Form(None),
    eway_bill: Optional[str] = Form(None),
    qty: float = Form(...),
    unit: str = Form("m"),
    rolls: Optional[int] = Form(None),
    gross_wt: Optional[float] = Form(None),
    transporter: Optional[str] = Form(None),
    lr_no: Optional[str] = Form(None),
    destination: Optional[str] = Form(None),
    override_reason: Optional[str] = Form(None),
    status: str = Form("Packed"),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("despatch", "create")),
):
    """
    POST /despatch -- Creates a new dispatch consignment with database-level QC hold gate.
    """
    creator_id = user.get("id")
    if not creator_id:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Authenticated user ID is missing.")

    if not job_id or not job_id.strip():
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Job Card ID is required for every dispatch.")

    if status == "Dispatched":
        status = "Packed"

    parsed_date = None
    if despatched_on and despatched_on.strip():
        try:
            parsed_date = datetime.date.fromisoformat(despatched_on.strip())
        except ValueError:
            parsed_date = None

    max_retries = 20
    created_id = None

    for attempt in range(max_retries):
        despatch_no = await get_next_despatch_no(conn)
        new_id = uuid.uuid4()

        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO despatch (
                        id,
                        despatch_no,
                        despatched_on,
                        job_id,
                        invoice_no,
                        eway_bill,
                        qty,
                        unit,
                        rolls,
                        gross_wt,
                        transporter,
                        lr_no,
                        destination,
                        status,
                        override_reason,
                        created_by
                    ) VALUES (
                        $1::uuid, $2, COALESCE($3, CURRENT_DATE), $4::uuid,
                        $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16::uuid
                    )
                    RETURNING id::text;
                    """,
                    new_id,
                    despatch_no,
                    parsed_date,
                    uuid.UUID(job_id.strip()),
                    invoice_no.strip() if invoice_no else None,
                    eway_bill.strip() if eway_bill else None,
                    qty,
                    unit.strip() if unit else "m",
                    rolls,
                    gross_wt,
                    transporter.strip() if transporter else None,
                    lr_no.strip() if lr_no else None,
                    destination.strip() if destination else None,
                    status,
                    override_reason.strip() if override_reason else None,
                    uuid.UUID(creator_id),
                )
                created_id = row["id"]
                try:
                    await sync_sales_voucher_for_despatch(conn, uuid.UUID(created_id), uuid.UUID(creator_id))
                except Exception as sync_err:
                    logger.warning(f"Tally sales sync hook error: {sync_err}")
                break
        except UniqueViolationError:
            if attempt == max_retries - 1:
                raise HTTPException(
                    status_code=HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Could not generate unique Despatch No due to high concurrency. Please retry.",
                )
            await asyncio.sleep(0.01 * (attempt + 1))
        except CheckViolationError as e:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=f"Database constraint error: {str(e)}",
            )

    return RedirectResponse(url=f"/despatch/{created_id}", status_code=HTTP_303_SEE_OTHER)


# ============================================================================
# 4. VIEW DESPATCH DETAIL (GET)
# ============================================================================

@router.get("/{despatch_id}", response_class=HTMLResponse)
async def view_despatch_detail(
    despatch_id: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("despatch", "read")),
):
    """
    GET /despatch/{despatch_id} -- View single dispatch consignment sheet.
    """
    row = await conn.fetchrow(
        """
        SELECT 
            d.id::text,
            d.despatch_no,
            d.despatched_on::text,
            d.job_id::text,
            j.job_no,
            j.product as job_product,
            j.qty_ordered as job_qty_ordered,
            j.unit as job_unit,
            c.name as customer_name,
            d.invoice_no,
            d.eway_bill,
            d.qty,
            d.unit,
            d.rolls,
            d.gross_wt,
            d.transporter,
            d.lr_no,
            d.destination,
            d.status,
            d.override_reason,
            job_on_hold(d.job_id) as on_qc_hold,
            d.created_by::text,
            u_cr.full_name as creator_name,
            d.approved_by::text,
            u_ap.full_name as approver_name,
            d.approved_at,
            d.created_at
        FROM despatch d
        JOIN jobs j ON d.job_id = j.id
        LEFT JOIN customers c ON j.customer_id = c.id
        LEFT JOIN profiles u_cr ON d.created_by = u_cr.id
        LEFT JOIN profiles u_ap ON d.approved_by = u_ap.id
        WHERE d.id = $1::uuid
        """,
        despatch_id,
    )

    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Despatch record not found.")

    entry = dict(row)

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="despatch/detail.html",
        context={
            "user": user_info,
            "d": entry,
            "current_page": "despatch",
            "current_func": "OPS",
        },
    )


# ============================================================================
# 5. EDIT DESPATCH FORM (GET)
# ============================================================================

@router.get("/{despatch_id}/edit", response_class=HTMLResponse)
async def edit_despatch_form(
    despatch_id: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("despatch", "update")),
):
    """
    GET /despatch/{despatch_id}/edit -- Form to edit consignment and logistics parameters.
    """
    row = await conn.fetchrow(
        "SELECT * FROM despatch WHERE id = $1::uuid;",
        despatch_id,
    )
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Despatch record not found.")

    job_rows = await conn.fetch(
        """
        SELECT 
            j.id::text, 
            j.job_no, 
            j.product, 
            j.qty_ordered, 
            j.unit,
            c.name as customer_name,
            job_on_hold(j.id) as on_qc_hold
        FROM jobs j
        LEFT JOIN customers c ON j.customer_id = c.id
        WHERE j.status NOT IN ('Completed', 'Cancelled')
        ORDER BY j.job_no DESC
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
        name="despatch/form.html",
        context={
            "user": user_info,
            "is_edit": True,
            "d": item_dict,
            "despatch_no_preview": item_dict["despatch_no"],
            "jobs": jobs,
            "selected_job_id": str(item_dict.get("job_id") or ""),
            "statuses": STATUSES,
            "current_page": "despatch",
            "current_func": "OPS",
        },
    )


# ============================================================================
# 6. UPDATE DESPATCH (POST)
# ============================================================================

@router.post("/{despatch_id}/update", response_class=HTMLResponse)
async def update_despatch(
    despatch_id: str,
    request: Request,
    job_id: str = Form(...),
    despatched_on: Optional[str] = Form(None),
    invoice_no: Optional[str] = Form(None),
    eway_bill: Optional[str] = Form(None),
    qty: float = Form(...),
    unit: str = Form("m"),
    rolls: Optional[int] = Form(None),
    gross_wt: Optional[float] = Form(None),
    transporter: Optional[str] = Form(None),
    lr_no: Optional[str] = Form(None),
    destination: Optional[str] = Form(None),
    override_reason: Optional[str] = Form(None),
    status: str = Form("Packed"),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("despatch", "update")),
):
    """
    POST /despatch/{despatch_id}/update -- Updates dispatch details.
    """
    if status == "Dispatched":
        can_approve = await conn.fetchval("SELECT auth_can('despatch', 'approve')")
        if not can_approve:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="Your roles do not permit releasing dispatches. Use dedicated Quality Release sign-off.",
            )

    parsed_date = None
    if despatched_on and despatched_on.strip():
        try:
            parsed_date = datetime.date.fromisoformat(despatched_on.strip())
        except ValueError:
            parsed_date = None

    try:
        await conn.execute(
            """
            UPDATE despatch SET
                job_id = $1::uuid,
                despatched_on = COALESCE($2, despatched_on),
                invoice_no = $3,
                eway_bill = $4,
                qty = $5,
                unit = $6,
                rolls = $7,
                gross_wt = $8,
                transporter = $9,
                lr_no = $10,
                destination = $11,
                status = $12,
                override_reason = $13
            WHERE id = $14::uuid;
            """,
            uuid.UUID(job_id.strip()),
            parsed_date,
            invoice_no.strip() if invoice_no else None,
            eway_bill.strip() if eway_bill else None,
            qty,
            unit.strip() if unit else "m",
            rolls,
            gross_wt,
            transporter.strip() if transporter else None,
            lr_no.strip() if lr_no else None,
            destination.strip() if destination else None,
            status,
            override_reason.strip() if override_reason else None,
            uuid.UUID(despatch_id),
        )
    except CheckViolationError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Database constraint error: {str(e)}",
        )

    return RedirectResponse(url=f"/despatch/{despatch_id}", status_code=HTTP_303_SEE_OTHER)


# ============================================================================
# 7. QUALITY ASSURANCE RELEASE & APPROVE (POST)
# ============================================================================

@router.post("/{despatch_id}/approve", response_class=HTMLResponse)
async def approve_despatch(
    despatch_id: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("despatch", "approve")),
):
    """
    POST /despatch/{despatch_id}/approve -- Quality Assurance Release sign-off.
    Guarded by:
      1. Role holding despatch.approve (chief_quality)
      2. Segregation of duties: Creator cannot approve their own dispatch note.
      3. Trigger check: Job on QC hold requires valid override_reason.
    """
    approver_id = user.get("id")
    if not approver_id:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Authenticated user ID is missing.")

    row = await conn.fetchrow(
        "SELECT id, despatch_no, job_id, created_by::text, override_reason, status FROM despatch WHERE id = $1::uuid;",
        despatch_id,
    )
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Despatch record not found.")

    # 1. Segregation of duties pre-check
    if str(row["created_by"]) == approver_id:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Segregation of duties violation: the person who created a dispatch note cannot approve/release it.",
        )

    try:
        await conn.execute(
            """
            UPDATE despatch SET
                status = 'Dispatched',
                approved_by = $1::uuid,
                approved_at = NOW()
            WHERE id = $2::uuid;
            """,
            uuid.UUID(approver_id),
            uuid.UUID(despatch_id),
        )
    except CheckViolationError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Database constraint error: {str(e)}",
        )

    return RedirectResponse(url=f"/despatch/{despatch_id}", status_code=HTTP_303_SEE_OTHER)
