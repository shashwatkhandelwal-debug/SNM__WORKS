"""
Costing Router — SNM Works
=============================================================================
Product Costing, Raw Material Conversion, Process Overheads, and Executive Sign-off.

Permissions:
  - costing.read: chief_executive, chief_financial, costing_analyst
  - costing.create: chief_executive, chief_financial, costing_analyst
  - costing.update: chief_executive, chief_financial, costing_analyst
  - costing.approve: chief_executive, chief_financial

Non-Negotiable Rules:
  - Confidentiality: Strictly restricted to FIN and EXEC roles.
  - Segregation of Duties: A user cannot approve a cost sheet they created (Rule 6).
  - Database Lock: Approved cost sheets are frozen at the database trigger level.
  - Pricing Formula: Margin on price vs markup on cost withheld until formally confirmed.
=============================================================================
"""

import asyncio
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
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)

from auth.dependencies import current_user, require
from database import get_db
from textiles.costing import calculate_manufacturing_cost, calculate_yarn_cost, calculate_selling_price

router = APIRouter(prefix="/costing", tags=["Costing & Financials"])
templates = Jinja2Templates(directory="templates")


# ============================================================================
# 1. COSTING REGISTER (LIST)
# ============================================================================

@router.get("", response_class=HTMLResponse)
async def list_costing(
    request: Request,
    status_filter: Optional[str] = "all",
    search: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("costing", "read")),
):
    """
    GET /costing — Executive register of job cost sheets.
    """
    query = """
        SELECT 
            c.job_id::text,
            j.job_no,
            j.product,
            cust.name AS customer_name,
            c.qty,
            c.unit,
            c.yarn_rate,
            c.yarn_consumption,
            c.wastage_pct,
            c.dyeing,
            c.coating,
            c.labour,
            c.overhead,
            c.packing,
            c.freight,
            c.margin_pct,
            c.status,
            c.created_by::text,
            p_cr.full_name AS creator_name,
            c.created_at,
            c.approved_by::text,
            p_ap.full_name AS approver_name,
            c.approved_at
        FROM costing c
        JOIN jobs j ON c.job_id = j.id
        LEFT JOIN customers cust ON j.customer_id = cust.id
        LEFT JOIN profiles p_cr ON c.created_by = p_cr.id
        LEFT JOIN profiles p_ap ON c.approved_by = p_ap.id
        WHERE 1=1
    """
    params = []
    param_idx = 1

    if status_filter and status_filter != "all":
        query += f" AND c.status = ${param_idx}"
        params.append(status_filter)
        param_idx += 1

    if search:
        query += f" AND (j.job_no ILIKE ${param_idx} OR j.product ILIKE ${param_idx} OR cust.name ILIKE ${param_idx})"
        params.append(f"%{search.strip()}%")
        param_idx += 1

    query += " ORDER BY c.created_at DESC;"
    rows = await conn.fetch(query, *params)

    items = []
    total_approved = 0
    total_draft = 0
    total_mfg_cost_sum = 0.0

    for r in rows:
        item = dict(r)
        yarn_c = calculate_yarn_cost(
            yarn_consumption_gpm=float(r["yarn_consumption"] or 0),
            yarn_rate_per_kg=float(r["yarn_rate"] or 0),
            wastage_pct=float(r["wastage_pct"] or 0),
        )
        costs = calculate_manufacturing_cost(
            yarn_cost=yarn_c,
            dyeing=float(r["dyeing"] or 0),
            coating=float(r["coating"] or 0),
            labour=float(r["labour"] or 0),
            overhead=float(r["overhead"] or 0),
            packing=float(r["packing"] or 0),
            freight=float(r["freight"] or 0),
        )
        item["yarn_cost_unit"] = costs["yarn_cost"]
        item["process_cost_unit"] = costs["process_cost"]
        item["overhead_cost_unit"] = costs["overhead_cost"]
        item["mfg_cost_unit"] = costs["total_manufacturing_cost"]
        item["total_mfg_cost"] = round(costs["total_manufacturing_cost"] * float(r["qty"] or 0), 2)
        total_mfg_cost_sum += item["total_mfg_cost"]

        if r["status"] == "Approved":
            total_approved += 1
        elif r["status"] == "Draft":
            total_draft += 1

        items.append(item)

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="costing/list.html",
        context={
            "user": user_info,
            "costings": items,
            "total_count": len(items),
            "total_approved": total_approved,
            "total_draft": total_draft,
            "total_mfg_cost_sum": round(total_mfg_cost_sum, 2),
            "selected_status": status_filter,
            "search": search or "",
            "current_page": "costing",
            "current_func": "FIN",
        },
    )


# ============================================================================
# 2. NEW COST SHEET FORM (GET)
# ============================================================================

@router.get("/new", response_class=HTMLResponse)
async def new_costing_form(
    request: Request,
    job_id: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("costing", "create")),
):
    """
    GET /costing/new — Cost sheet preparation form with auto-populating job details.
    """
    # Find active jobs without existing cost sheets
    available_jobs = await conn.fetch(
        """
        SELECT j.id::text, j.job_no, j.product, j.qty_ordered, j.unit, cust.name AS customer_name
        FROM jobs j
        LEFT JOIN customers cust ON j.customer_id = cust.id
        LEFT JOIN costing c ON j.id = c.job_id
        WHERE c.job_id IS NULL AND j.status <> 'Cancelled'
        ORDER BY j.created_at DESC;
        """
    )

    selected_job = None
    if job_id:
        selected_job = await conn.fetchrow(
            """
            SELECT j.id::text, j.job_no, j.product, j.qty_ordered, j.unit, cust.name AS customer_name
            FROM jobs j
            LEFT JOIN customers cust ON j.customer_id = cust.id
            WHERE j.id = $1::uuid;
            """,
            job_id,
        )

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="costing/form.html",
        context={
            "user": user_info,
            "available_jobs": [dict(j) for j in available_jobs],
            "selected_job": dict(selected_job) if selected_job else None,
            "is_edit": False,
            "current_page": "costing",
            "current_func": "FIN",
        },
    )


# ============================================================================
# 3. CREATE COST SHEET (POST)
# ============================================================================

@router.post("", response_class=HTMLResponse)
async def create_costing(
    request: Request,
    job_id: str = Form(...),
    qty: float = Form(...),
    unit: str = Form("m"),
    yarn_rate: float = Form(0.0),
    yarn_consumption: float = Form(0.0),
    wastage_pct: float = Form(0.0),
    dyeing: float = Form(0.0),
    coating: float = Form(0.0),
    labour: float = Form(0.0),
    overhead: float = Form(0.0),
    packing: float = Form(0.0),
    freight: float = Form(0.0),
    margin_pct: float = Form(0.0),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("costing", "create")),
):
    """
    POST /costing — Creates a new draft cost sheet for a job.
    """
    creator_id = user.get("id")
    if not creator_id:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    # Check if costing already exists for this job
    existing = await conn.fetchval("SELECT job_id FROM costing WHERE job_id = $1::uuid;", uuid.UUID(job_id))
    if existing:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="A cost sheet already exists for this job. Edit the existing cost sheet instead.",
        )

    try:
        await conn.execute(
            """
            INSERT INTO costing (
                job_id, qty, unit, yarn_rate, yarn_consumption, wastage_pct,
                dyeing, coating, labour, overhead, packing, freight, margin_pct,
                status, created_by, updated_by, created_at, updated_at
            ) VALUES (
                $1::uuid, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11, $12, $13,
                'Draft', $14::uuid, $14::uuid, NOW(), NOW()
            );
            """,
            uuid.UUID(job_id),
            qty,
            unit.strip(),
            yarn_rate,
            yarn_consumption,
            wastage_pct,
            dyeing,
            coating,
            labour,
            overhead,
            packing,
            freight,
            margin_pct,
            uuid.UUID(creator_id),
        )
    except CheckViolationError as e:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=f"Invalid cost parameter: {str(e)}")

    return RedirectResponse(url=f"/costing/{job_id}", status_code=HTTP_303_SEE_OTHER)


# ============================================================================
# 4. DETAIL VIEW (GET)
# ============================================================================

@router.get("/{job_id}", response_class=HTMLResponse)
async def costing_detail(
    job_id: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("costing", "read")),
):
    """
    GET /costing/{job_id} — Financial cost sheet breakdown and review.
    """
    row = await conn.fetchrow(
        """
        SELECT 
            c.job_id::text,
            j.job_no,
            j.product,
            cust.name AS customer_name,
            c.qty,
            c.unit,
            c.yarn_rate,
            c.yarn_consumption,
            c.wastage_pct,
            c.dyeing,
            c.coating,
            c.labour,
            c.overhead,
            c.packing,
            c.freight,
            c.margin_pct,
            c.status,
            c.created_by::text,
            p_cr.full_name AS creator_name,
            c.created_at,
            c.approved_by::text,
            p_ap.full_name AS approver_name,
            c.approved_at,
            c.updated_at
        FROM costing c
        JOIN jobs j ON c.job_id = j.id
        LEFT JOIN customers cust ON j.customer_id = cust.id
        LEFT JOIN profiles p_cr ON c.created_by = p_cr.id
        LEFT JOIN profiles p_ap ON c.approved_by = p_ap.id
        WHERE c.job_id = $1::uuid;
        """,
        job_id,
    )
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Cost sheet not found.")

    item = dict(row)
    qty_val = float(row["qty"] or 0)
    dyeing_val = float(row["dyeing"] or 0)
    coating_val = float(row["coating"] or 0)
    labour_val = float(row["labour"] or 0)
    overhead_val = float(row["overhead"] or 0)
    packing_val = float(row["packing"] or 0)
    freight_val = float(row["freight"] or 0)
    margin_val = float(row["margin_pct"] or 0)

    yarn_c = calculate_yarn_cost(
        yarn_consumption_gpm=float(row["yarn_consumption"] or 0),
        yarn_rate_per_kg=float(row["yarn_rate"] or 0),
        wastage_pct=float(row["wastage_pct"] or 0),
    )
    costs = calculate_manufacturing_cost(
        yarn_cost=yarn_c,
        dyeing=dyeing_val,
        coating=coating_val,
        labour=labour_val,
        overhead=overhead_val,
        packing=packing_val,
        freight=freight_val,
    )
    item["qty"] = qty_val
    item["yarn_rate"] = float(row["yarn_rate"] or 0)
    item["yarn_consumption"] = float(row["yarn_consumption"] or 0)
    item["wastage_pct"] = float(row["wastage_pct"] or 0)
    item["dyeing"] = dyeing_val
    item["coating"] = coating_val
    item["labour"] = labour_val
    item["overhead"] = overhead_val
    item["packing"] = packing_val
    item["freight"] = freight_val
    item["margin_pct"] = margin_val
    item["yarn_cost_unit"] = costs["yarn_cost"]
    item["process_cost_unit"] = costs["process_cost"]
    item["overhead_cost_unit"] = costs["overhead_cost"]
    item["mfg_cost_unit"] = costs["total_manufacturing_cost"]
    item["total_mfg_cost"] = round(costs["total_manufacturing_cost"] * qty_val, 2)

    # Check approve authorization
    can_approve = await conn.fetchval(
        "SELECT auth_can('costing', 'approve');"
    )

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="costing/detail.html",
        context={
            "user": user_info,
            "costing": item,
            "can_approve": bool(can_approve),
            "is_creator": str(row["created_by"]) == user.get("id"),
            "current_page": "costing",
            "current_func": "FIN",
        },
    )


# ============================================================================
# 5. EDIT FORM (GET)
# ============================================================================

@router.get("/{job_id}/edit", response_class=HTMLResponse)
async def edit_costing_form(
    job_id: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("costing", "update")),
):
    """
    GET /costing/{job_id}/edit — Edit parameters of a draft cost sheet.
    """
    row = await conn.fetchrow(
        """
        SELECT 
            c.job_id::text,
            j.job_no,
            j.product,
            cust.name AS customer_name,
            c.qty,
            c.unit,
            c.yarn_rate,
            c.yarn_consumption,
            c.wastage_pct,
            c.dyeing,
            c.coating,
            c.labour,
            c.overhead,
            c.packing,
            c.freight,
            c.margin_pct,
            c.status
        FROM costing c
        JOIN jobs j ON c.job_id = j.id
        LEFT JOIN customers cust ON j.customer_id = cust.id
        WHERE c.job_id = $1::uuid;
        """,
        job_id,
    )
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Cost sheet not found.")

    if row["status"] != "Draft":
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Cannot edit cost sheet in '{row['status']}' status. Parameters are locked.",
        )

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="costing/form.html",
        context={
            "user": user_info,
            "costing": dict(row),
            "is_edit": True,
            "current_page": "costing",
            "current_func": "FIN",
        },
    )


# ============================================================================
# 6. UPDATE COST SHEET (POST)
# ============================================================================

@router.post("/{job_id}", response_class=HTMLResponse)
async def update_costing(
    job_id: str,
    qty: float = Form(...),
    unit: str = Form("m"),
    yarn_rate: float = Form(0.0),
    yarn_consumption: float = Form(0.0),
    wastage_pct: float = Form(0.0),
    dyeing: float = Form(0.0),
    coating: float = Form(0.0),
    labour: float = Form(0.0),
    overhead: float = Form(0.0),
    packing: float = Form(0.0),
    freight: float = Form(0.0),
    margin_pct: float = Form(0.0),
    request: Request = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("costing", "update")),
):
    """
    POST /costing/{job_id} — Updates a draft cost sheet.
    """
    row = await conn.fetchrow("SELECT status FROM costing WHERE job_id = $1::uuid;", uuid.UUID(job_id))
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Cost sheet not found.")

    if row["status"] != "Draft":
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Cannot update cost sheet in '{row['status']}' status. Cost parameters are frozen.",
        )

    try:
        await conn.execute(
            """
            UPDATE costing
            SET 
                qty = $1,
                unit = $2,
                yarn_rate = $3,
                yarn_consumption = $4,
                wastage_pct = $5,
                dyeing = $6,
                coating = $7,
                labour = $8,
                overhead = $9,
                packing = $10,
                freight = $11,
                margin_pct = $12,
                updated_by = $13::uuid,
                updated_at = NOW()
            WHERE job_id = $14::uuid;
            """,
            qty,
            unit.strip(),
            yarn_rate,
            yarn_consumption,
            wastage_pct,
            dyeing,
            coating,
            labour,
            overhead,
            packing,
            freight,
            margin_pct,
            uuid.UUID(user.get("id")),
            uuid.UUID(job_id),
        )
    except CheckViolationError as e:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=f"Database check error: {str(e)}")

    return RedirectResponse(url=f"/costing/{job_id}", status_code=HTTP_303_SEE_OTHER)


# ============================================================================
# 7. APPROVE & LOCK COST SHEET (POST)
# ============================================================================

@router.post("/{job_id}/approve", response_class=HTMLResponse)
async def approve_costing(
    job_id: str,
    request: Request = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("costing", "approve")),
):
    """
    POST /costing/{job_id}/approve — Financial sign-off and permanent freeze.
    Guarded by:
      1. Role check: requires costing.approve (chief_financial / chief_executive)
      2. Non-negotiable Rule 6 self-approval constraint (approved_by <> created_by)
    """
    approver_id = user.get("id")
    row = await conn.fetchrow(
        "SELECT status, created_by::text FROM costing WHERE job_id = $1::uuid;",
        uuid.UUID(job_id),
    )
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Cost sheet not found.")

    if row["status"] != "Draft":
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=f"Cost sheet is already in '{row['status']}' status.")

    # Self-approval check
    if row["created_by"] == approver_id:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Segregation of duties violation: The person who prepared the cost sheet cannot sign off/approve it.",
        )

    try:
        await conn.execute(
            """
            UPDATE costing
            SET 
                status = 'Approved',
                approved_by = $1::uuid,
                approved_at = NOW(),
                updated_by = $1::uuid,
                updated_at = NOW()
            WHERE job_id = $2::uuid;
            """,
            uuid.UUID(approver_id),
            uuid.UUID(job_id),
        )
    except CheckViolationError as e:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=f"Approval constraint violation: {str(e)}")

    return RedirectResponse(url=f"/costing/{job_id}", status_code=HTTP_303_SEE_OTHER)
