import asyncio
from datetime import date, datetime
import logging
import math
from typing import Any, Dict, List, Optional
import uuid

import asyncpg
from asyncpg.exceptions import CheckViolationError, UniqueViolationError
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_503_SERVICE_UNAVAILABLE,
)
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import get_db
from auth.dependencies import current_user, require
from textiles import (
    warp_gpm,
    weft_gpm,
    total_narrow_gpm,
    total_narrow_oz_yd,
    theoretical_break_kgf,
    theoretical_break_lbf,
    warp_gsm,
    weft_gsm,
    total_fabric_gsm,
    total_fabric_oz_yd2,
    cover_factor_warp,
    cover_factor_weft,
    cover_factor_total,
    check_cover_factor_jamming,
    braid_sheath_gpm,
    braid_core_gpm,
    total_cord_gpm,
    total_cord_oz_yd,
    theoretical_cord_break_kgf,
    theoretical_cord_break_lbf,
    in_to_mm,
    mm_to_in,
    ppi_to_picks_per_cm,
    picks_per_cm_to_ppi,
    denier_to_tex,
)

logger = logging.getLogger("snm_works.engineering")
router = APIRouter(prefix="/constructions", tags=["Engineering"])
templates = Jinja2Templates(directory="templates")

FAMILIES = ["Narrow Wovens", "Broad Fabric", "Cordage"]
STATUSES = ["Draft", "Under Review", "Approved", "Obsolete", "Cancelled"]


# ============================================================================
# HELPER: SEQUENCE GENERATOR (CONST-0001)
# ============================================================================

async def get_next_spec_no(conn: asyncpg.Connection) -> str:
    """
    Computes the next sequence number for constructions: CONST-0001, CONST-0002...
    """
    seq_val = await conn.fetchval(
        """
        SELECT COALESCE(
            MAX(
                NULLIF(SUBSTRING(spec_no FROM 'CONST-([0-9]+)'), '')::integer
            ), 0
        ) + 1
        FROM constructions;
        """
    )
    next_num = seq_val or 1
    return f"CONST-{next_num:04d}"


# ============================================================================
# HELPER: LIVE TEXTILE CALCULATION ENGINE
# ============================================================================

def compute_construction_metrics(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calls pure functions from the textiles/ library to compute engineering metrics.
    """
    family = row.get("family") or "Narrow Wovens"
    results: Dict[str, Any] = {
        "family": family,
        "is_jammed": False,
        "jamming_warning": None,
        "total_gpm": 0.0,
        "total_gsm": 0.0,
        "theoretical_break_lbf": 0.0,
        "theoretical_break_kgf": 0.0,
        "width_inches": 0.0,
    }

    if family == "Narrow Wovens":
        ends = int(row.get("warp_ends") or 0)
        warp_den = float(row.get("warp_denier") or 0.0)
        weft_den = float(row.get("weft_denier") or 0.0)
        width_mm = float(row.get("width_mm") or 0.0)
        ppcm = float(row.get("picks_per_cm") or 0.0)
        warp_crimp = float(row.get("warp_crimp") or 0.0)
        weft_crimp = float(row.get("weft_crimp") or 0.0)
        tenacity = float(row.get("warp_tenacity") or 8.5)
        eff = float(row.get("efficiency") or 85.0)

        w_gpm = warp_gpm(ends, warp_den, warp_crimp)
        f_gpm = weft_gpm(ppcm, width_mm, weft_den, weft_crimp)
        tot_gpm = total_narrow_gpm(w_gpm, f_gpm)
        tot_oz_yd = total_narrow_oz_yd(tot_gpm)

        brk_kgf = theoretical_break_kgf(ends, warp_den, tenacity, eff)
        brk_lbf = theoretical_break_lbf(ends, warp_den, tenacity, eff)

        results.update({
            "warp_gpm": round(w_gpm, 2),
            "weft_gpm": round(f_gpm, 2),
            "total_gpm": round(tot_gpm, 2),
            "total_oz_yd": round(tot_oz_yd, 2),
            "theoretical_break_kgf": round(brk_kgf, 1),
            "theoretical_break_lbf": round(brk_lbf, 0),
            "width_inches": round(mm_to_in(width_mm), 3) if width_mm else 0.0,
            "ppi": round(picks_per_cm_to_ppi(ppcm), 1) if ppcm else 0.0,
            "warp_tex": round(denier_to_tex(warp_den), 1) if warp_den else 0.0,
        })

    elif family == "Broad Fabric":
        epi = float(row.get("epi") or 0.0)
        ppi = float(row.get("ppi") or 0.0)
        warp_den = float(row.get("warp_denier") or 0.0)
        weft_den = float(row.get("weft_denier") or 0.0)
        warp_crimp = float(row.get("warp_crimp") or 0.0)
        weft_crimp = float(row.get("weft_crimp") or 0.0)

        w_gsm = warp_gsm(epi, warp_den, warp_crimp)
        f_gsm = weft_gsm(ppi, weft_den, weft_crimp)
        tot_gsm = total_fabric_gsm(w_gsm, f_gsm)
        tot_oz_yd2 = total_fabric_oz_yd2(tot_gsm)

        kw = cover_factor_warp(epi, warp_den)
        kf = cover_factor_weft(ppi, weft_den)
        kt = cover_factor_total(epi, ppi, warp_den, weft_den)
        is_jammed, msg = check_cover_factor_jamming(epi, ppi, warp_den, weft_den)

        results.update({
            "warp_gsm": round(w_gsm, 1),
            "weft_gsm": round(f_gsm, 1),
            "total_gsm": round(tot_gsm, 1),
            "total_oz_yd2": round(tot_oz_yd2, 2),
            "cover_factor_warp": round(kw, 2),
            "cover_factor_weft": round(kf, 2),
            "cover_factor_total": round(kt, 2),
            "is_jammed": is_jammed,
            "jamming_warning": msg if is_jammed else None,
        })

    elif family == "Cordage":
        carriers = int(row.get("carriers") or 0)
        ypc = int(row.get("yarns_per_carrier") or 0)
        core_yarns = int(row.get("core_yarns") or 0)
        yarn_den = float(row.get("yarn_denier") or 0.0)
        contraction = float(row.get("contraction") or 0.0)
        tenacity = float(row.get("warp_tenacity") or 8.0)
        eff = float(row.get("efficiency") or 75.0)

        s_gpm = braid_sheath_gpm(carriers, ypc, yarn_den, contraction)
        c_gpm = braid_core_gpm(core_yarns, yarn_den, 2.0)
        tot_gpm = total_cord_gpm(s_gpm, c_gpm)
        tot_oz_yd = total_cord_oz_yd(tot_gpm)

        brk_kgf = theoretical_cord_break_kgf(
            carriers=carriers,
            yarns_per_carrier=ypc,
            sheath_denier=yarn_den,
            sheath_tenacity=tenacity,
            core_yarns=core_yarns,
            core_denier=yarn_den,
            core_tenacity=tenacity,
            braid_efficiency_pct=eff,
        )
        brk_lbf = theoretical_cord_break_lbf(
            carriers=carriers,
            yarns_per_carrier=ypc,
            sheath_denier=yarn_den,
            sheath_tenacity=tenacity,
            core_yarns=core_yarns,
            core_denier=yarn_den,
            core_tenacity=tenacity,
            braid_efficiency_pct=eff,
        )

        results.update({
            "sheath_gpm": round(s_gpm, 2),
            "core_gpm": round(c_gpm, 2),
            "total_gpm": round(tot_gpm, 2),
            "total_oz_yd": round(tot_oz_yd, 2),
            "theoretical_break_kgf": round(brk_kgf, 1),
            "theoretical_break_lbf": round(brk_lbf, 0),
        })

    return results


# ============================================================================
# 1. LIST CONSTRUCTIONS
# ============================================================================

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def list_constructions(
    request: Request,
    family: Optional[str] = None,
    status_filter: Optional[str] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("constructions", "read")),
):
    """
    GET /constructions -- Browse engineering construction specifications.
    """
    query = """
        SELECT 
            c.id::text as id,
            c.spec_no,
            c.created_on,
            c.revision,
            c.status,
            c.family,
            c.product,
            c.spec,
            c.width_mm,
            c.width_cm,
            c.weave,
            c.warp_denier,
            c.weft_denier,
            c.warp_ends,
            c.picks_per_cm,
            c.epi,
            c.ppi,
            c.carriers,
            c.yarns_per_carrier,
            c.core_yarns,
            c.yarn_denier,
            c.warp_crimp,
            c.weft_crimp,
            c.warp_tenacity,
            c.efficiency,
            c.created_by::text as created_by,
            p_cr.full_name as creator_name,
            c.approved_by::text as approved_by,
            p_ap.full_name as approver_name,
            cust.name as customer_name,
            COUNT(*) OVER() AS total_count
        FROM constructions c
        LEFT JOIN profiles p_cr ON c.created_by = p_cr.id
        LEFT JOIN profiles p_ap ON c.approved_by = p_ap.id
        LEFT JOIN customers cust ON c.customer_id = cust.id
        WHERE 1=1
    """
    params = []
    idx = 1

    if family and family.strip() and family.lower() != "all":
        query += f" AND c.family = ${idx}"
        params.append(family.strip())
        idx += 1

    if status_filter and status_filter.strip() and status_filter.lower() != "all":
        query += f" AND c.status = ${idx}"
        params.append(status_filter.strip())
        idx += 1

    if q and q.strip():
        query += f" AND (c.spec_no ILIKE ${idx} OR c.product ILIKE ${idx} OR c.spec ILIKE ${idx})"
        params.append(f"%{q.strip()}%")
        idx += 1

    offset = (page - 1) * page_size
    query += f" ORDER BY c.created_on DESC, c.spec_no DESC LIMIT ${idx} OFFSET ${idx + 1};"
    params.extend([page_size, offset])

    rows = await conn.fetch(query, *params)
    items = []
    for r in rows:
        item_dict = dict(r)
        metrics = compute_construction_metrics(item_dict)
        item_dict["metrics"] = metrics
        items.append(item_dict)

    total_count = int(rows[0]["total_count"]) if rows else 0
    total_pages = max(1, math.ceil(total_count / page_size))

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="constructions/list.html",
        context={
            "user": user_info,
            "constructions": items,
            "total_count": total_count,
            "total_pages": total_pages,
            "page": page,
            "page_size": page_size,
            "families": FAMILIES,
            "statuses": STATUSES,
            "selected_family": family or "all",
            "selected_status": status_filter or "all",
            "search_query": q or "",
            "current_page": "constructions",
            "current_func": "TEC",
        },
    )


# ============================================================================
# 2. HTMX LIVE CALCULATION ENDPOINT
# ============================================================================

@router.post("/calculate", response_class=HTMLResponse)
async def calculate_live_metrics(
    request: Request,
    family: str = Form("Narrow Wovens"),
    width_mm: Optional[float] = Form(None),
    warp_ends: Optional[int] = Form(None),
    warp_denier: Optional[float] = Form(None),
    weft_denier: Optional[float] = Form(None),
    picks_per_cm: Optional[float] = Form(None),
    warp_crimp: Optional[float] = Form(5.0),
    weft_crimp: Optional[float] = Form(3.0),
    warp_tenacity: Optional[float] = Form(8.5),
    efficiency: Optional[float] = Form(85.0),
    epi: Optional[float] = Form(None),
    ppi: Optional[float] = Form(None),
    carriers: Optional[int] = Form(None),
    yarns_per_carrier: Optional[int] = Form(None),
    core_yarns: Optional[int] = Form(None),
    yarn_denier: Optional[float] = Form(None),
    contraction: Optional[float] = Form(15.0),
    user: Dict[str, Any] = Depends(current_user),
):
    """
    POST /constructions/calculate -- HTMX endpoint for live engineering recalculations.
    """
    form_data = {
        "family": family,
        "width_mm": width_mm,
        "warp_ends": warp_ends,
        "warp_denier": warp_denier,
        "weft_denier": weft_denier,
        "picks_per_cm": picks_per_cm,
        "warp_crimp": warp_crimp,
        "weft_crimp": weft_crimp,
        "warp_tenacity": warp_tenacity,
        "efficiency": efficiency,
        "epi": epi,
        "ppi": ppi,
        "carriers": carriers,
        "yarns_per_carrier": yarns_per_carrier,
        "core_yarns": core_yarns,
        "yarn_denier": yarn_denier,
        "contraction": contraction,
    }
    metrics = compute_construction_metrics(form_data)
    return templates.TemplateResponse(
        request=request,
        name="constructions/calc_card.html",
        context={"metrics": metrics, "family": family},
    )


# ============================================================================
# 3. NEW CONSTRUCTION FORM
# ============================================================================

@router.get("/new", response_class=HTMLResponse)
async def new_construction_form(
    request: Request,
    family: Optional[str] = "Narrow Wovens",
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("constructions", "create")),
):
    """
    GET /constructions/new -- Create a new construction specification.
    """
    next_spec_no = await get_next_spec_no(conn)
    customer_rows = await conn.fetch("SELECT id::text, name FROM customers WHERE active = true ORDER BY name ASC")
    customers = [dict(c) for c in customer_rows]

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    initial_metrics = compute_construction_metrics({
        "family": family or "Narrow Wovens",
        "warp_ends": 320,
        "warp_denier": 840.0,
        "weft_denier": 840.0,
        "width_mm": 43.66,
        "picks_per_cm": 7.09,
        "warp_crimp": 5.0,
        "weft_crimp": 3.0,
        "warp_tenacity": 8.5,
        "efficiency": 85.0,
    })

    return templates.TemplateResponse(
        request=request,
        name="constructions/form.html",
        context={
            "user": user_info,
            "is_edit": False,
            "spec_no_preview": next_spec_no,
            "families": FAMILIES,
            "selected_family": family or "Narrow Wovens",
            "customers": customers,
            "metrics": initial_metrics,
            "current_page": "constructions",
            "current_func": "TEC",
        },
    )


# ============================================================================
# 4. CREATE CONSTRUCTION (POST)
# ============================================================================

@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_construction(
    request: Request,
    family: str = Form(...),
    product: str = Form(...),
    spec: Optional[str] = Form(None),
    customer_id: Optional[str] = Form(None),
    revision: str = Form("R0"),
    status: str = Form("Draft"),
    # Narrow woven
    width_mm: Optional[float] = Form(None),
    weave: Optional[str] = Form(None),
    warp_denier: Optional[float] = Form(None),
    weft_denier: Optional[float] = Form(None),
    warp_ends: Optional[int] = Form(None),
    picks_per_cm: Optional[float] = Form(None),
    selvedge: Optional[str] = Form(None),
    # Broad fabric
    width_cm: Optional[float] = Form(None),
    epi: Optional[float] = Form(None),
    ppi: Optional[float] = Form(None),
    finish: Optional[str] = Form(None),
    # Cordage
    cord_type: Optional[str] = Form(None),
    diameter_mm: Optional[float] = Form(None),
    carriers: Optional[int] = Form(None),
    yarns_per_carrier: Optional[int] = Form(None),
    core_yarns: Optional[int] = Form(None),
    yarn_denier: Optional[float] = Form(None),
    tpm: Optional[float] = Form(None),
    contraction: Optional[float] = Form(None),
    # Shared
    warp_crimp: Optional[float] = Form(None),
    weft_crimp: Optional[float] = Form(None),
    warp_tenacity: Optional[float] = Form(None),
    weft_tenacity: Optional[float] = Form(None),
    efficiency: Optional[float] = Form(85.0),
    notes: Optional[str] = Form(None),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("constructions", "create")),
):
    """
    POST /constructions -- Insert new construction record with collision retry.
    """
    creator_id = user.get("id")
    if not creator_id:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="User ID required.")

    clean_product = product.strip()
    if not clean_product:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Product name is required.")

    # Status cannot be created as Approved directly
    clean_status = status.strip() if status else "Draft"
    if clean_status == "Approved":
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="Constructions cannot be created directly with 'Approved' status. Create as Draft and submit for approval.",
        )

    resolved_cust_id = None
    if customer_id and customer_id.strip():
        try:
            resolved_cust_id = uuid.UUID(customer_id.strip())
        except ValueError:
            resolved_cust_id = None

    new_id = uuid.uuid4()
    max_retries = 20
    created_id: Optional[uuid.UUID] = None

    for attempt in range(max_retries):
        spec_no = await get_next_spec_no(conn)
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO constructions (
                        id, spec_no, created_on, revision, status,
                        family, product, spec, customer_id,
                        width_mm, weave, warp_denier, weft_denier,
                        warp_ends, picks_per_cm, selvedge,
                        width_cm, epi, ppi, finish,
                        cord_type, diameter_mm, carriers, yarns_per_carrier,
                        core_yarns, yarn_denier, tpm, contraction,
                        warp_crimp, weft_crimp, warp_tenacity, weft_tenacity,
                        efficiency, notes, created_by
                    ) VALUES (
                        $1, $2, CURRENT_DATE, $3, $4,
                        $5, $6, $7, $8,
                        $9, $10, $11, $12,
                        $13, $14, $15,
                        $16, $17, $18, $19,
                        $20, $21, $22, $23,
                        $24, $25, $26, $27,
                        $28, $29, $30, $31,
                        $32, $33, $34
                    ) RETURNING id;
                    """,
                    new_id,
                    spec_no,
                    revision.strip() if revision else "R0",
                    clean_status,
                    family.strip(),
                    clean_product,
                    spec.strip() if spec else None,
                    resolved_cust_id,
                    width_mm,
                    weave.strip() if weave else None,
                    warp_denier,
                    weft_denier,
                    warp_ends,
                    picks_per_cm,
                    selvedge.strip() if selvedge else None,
                    width_cm,
                    epi,
                    ppi,
                    finish.strip() if finish else None,
                    cord_type.strip() if cord_type else None,
                    diameter_mm,
                    carriers,
                    yarns_per_carrier,
                    core_yarns,
                    yarn_denier,
                    tpm,
                    contraction,
                    warp_crimp,
                    weft_crimp,
                    warp_tenacity,
                    weft_tenacity,
                    efficiency,
                    notes.strip() if notes else None,
                    uuid.UUID(creator_id),
                )
                created_id = row["id"]
                break
        except UniqueViolationError:
            if attempt == max_retries - 1:
                raise HTTPException(
                    status_code=HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Could not generate unique Spec No due to high load. Please retry.",
                )
            new_id = uuid.uuid4()
            await asyncio.sleep(0.01 * (attempt + 1))

    return RedirectResponse(
        url=f"/constructions/{created_id}",
        status_code=HTTP_303_SEE_OTHER,
    )


# ============================================================================
# 5. VIEW CONSTRUCTION DETAIL
# ============================================================================

@router.get("/{id}", response_class=HTMLResponse)
async def get_construction_detail(
    request: Request,
    id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("constructions", "read")),
):
    """
    GET /constructions/{id} -- Detailed technical specification card with live metrics.
    """
    try:
        const_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Invalid ID format.")

    row = await conn.fetchrow(
        """
        SELECT 
            c.id::text as id,
            c.spec_no,
            c.created_on,
            c.revision,
            c.status,
            c.family,
            c.product,
            c.spec,
            c.customer_id::text as customer_id,
            c.width_mm,
            c.width_cm,
            c.weave,
            c.warp_denier,
            c.weft_denier,
            c.warp_ends,
            c.picks_per_cm,
            c.selvedge,
            c.epi,
            c.ppi,
            c.finish,
            c.cord_type,
            c.diameter_mm,
            c.carriers,
            c.yarns_per_carrier,
            c.core_yarns,
            c.yarn_denier,
            c.tpm,
            c.contraction,
            c.warp_crimp,
            c.weft_crimp,
            c.warp_tenacity,
            c.weft_tenacity,
            c.efficiency,
            c.notes,
            c.created_by::text as created_by,
            p_cr.full_name as creator_name,
            c.approved_by::text as approved_by,
            p_ap.full_name as approver_name,
            c.approved_at,
            c.created_at,
            c.updated_at,
            cust.name as customer_name
        FROM constructions c
        LEFT JOIN profiles p_cr ON c.created_by = p_cr.id
        LEFT JOIN profiles p_ap ON c.approved_by = p_ap.id
        LEFT JOIN customers cust ON c.customer_id = cust.id
        WHERE c.id = $1;
        """,
        const_uuid,
    )

    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Construction record not found.")

    item_dict = dict(row)
    metrics = compute_construction_metrics(item_dict)

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="constructions/detail.html",
        context={
            "user": user_info,
            "c": item_dict,
            "metrics": metrics,
            "current_page": "constructions",
            "current_func": "TEC",
        },
    )


# ============================================================================
# 6. EDIT CONSTRUCTION FORM
# ============================================================================

@router.get("/{id}/edit", response_class=HTMLResponse)
async def edit_construction_form(
    request: Request,
    id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("constructions", "update")),
):
    """
    GET /constructions/{id}/edit -- Form to edit parameters of a draft specification.
    """
    try:
        const_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Invalid ID format.")

    row = await conn.fetchrow(
        """
        SELECT 
            c.*,
            c.id::text as id,
            c.customer_id::text as customer_id
        FROM constructions c
        WHERE c.id = $1;
        """,
        const_uuid,
    )
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Construction not found.")

    customer_rows = await conn.fetch("SELECT id::text, name FROM customers WHERE active = true ORDER BY name ASC")
    customers = [dict(c) for c in customer_rows]

    item_dict = dict(row)
    metrics = compute_construction_metrics(item_dict)

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="constructions/form.html",
        context={
            "user": user_info,
            "is_edit": True,
            "c": item_dict,
            "spec_no_preview": item_dict["spec_no"],
            "families": FAMILIES,
            "statuses": STATUSES,
            "selected_family": item_dict.get("family"),
            "customers": customers,
            "metrics": metrics,
            "current_page": "constructions",
            "current_func": "TEC",
        },
    )


# ============================================================================
# 7. UPDATE CONSTRUCTION (POST)
# ============================================================================

@router.post("/{id}/update", response_class=HTMLResponse)
async def update_construction(
    request: Request,
    id: str,
    family: str = Form(...),
    product: str = Form(...),
    spec: Optional[str] = Form(None),
    customer_id: Optional[str] = Form(None),
    revision: str = Form("R0"),
    status: str = Form(...),
    width_mm: Optional[float] = Form(None),
    weave: Optional[str] = Form(None),
    warp_denier: Optional[float] = Form(None),
    weft_denier: Optional[float] = Form(None),
    warp_ends: Optional[int] = Form(None),
    picks_per_cm: Optional[float] = Form(None),
    selvedge: Optional[str] = Form(None),
    width_cm: Optional[float] = Form(None),
    epi: Optional[float] = Form(None),
    ppi: Optional[float] = Form(None),
    finish: Optional[str] = Form(None),
    cord_type: Optional[str] = Form(None),
    diameter_mm: Optional[float] = Form(None),
    carriers: Optional[int] = Form(None),
    yarns_per_carrier: Optional[int] = Form(None),
    core_yarns: Optional[int] = Form(None),
    yarn_denier: Optional[float] = Form(None),
    tpm: Optional[float] = Form(None),
    contraction: Optional[float] = Form(None),
    warp_crimp: Optional[float] = Form(None),
    weft_crimp: Optional[float] = Form(None),
    warp_tenacity: Optional[float] = Form(None),
    weft_tenacity: Optional[float] = Form(None),
    efficiency: Optional[float] = Form(85.0),
    notes: Optional[str] = Form(None),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("constructions", "update")),
):
    """
    POST /constructions/{id}/update -- Update construction parameters.
    Approval state transitions are blocked here unless user holds constructions.approve.
    """
    try:
        const_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Invalid ID format.")

    # Check if attempting to set Approved via general update route
    if status.strip() == "Approved":
        # Check if user holds constructions.approve
        can_approve = await conn.fetchval("SELECT auth_can('constructions', 'approve')")
        if not can_approve:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="Your roles do not permit approving constructions. Transition to 'Approved' requires chief_technical role via POST /approve.",
            )

    resolved_cust_id = None
    if customer_id and customer_id.strip():
        try:
            resolved_cust_id = uuid.UUID(customer_id.strip())
        except ValueError:
            resolved_cust_id = None

    await conn.execute(
        """
        UPDATE constructions SET
            family = $1,
            product = $2,
            spec = $3,
            customer_id = $4,
            revision = $5,
            status = $6,
            width_mm = $7,
            weave = $8,
            warp_denier = $9,
            weft_denier = $10,
            warp_ends = $11,
            picks_per_cm = $12,
            selvedge = $13,
            width_cm = $14,
            epi = $15,
            ppi = $16,
            finish = $17,
            cord_type = $18,
            diameter_mm = $19,
            carriers = $20,
            yarns_per_carrier = $21,
            core_yarns = $22,
            yarn_denier = $23,
            tpm = $24,
            contraction = $25,
            warp_crimp = $26,
            weft_crimp = $27,
            warp_tenacity = $28,
            weft_tenacity = $29,
            efficiency = $30,
            notes = $31
        WHERE id = $32;
        """,
        family.strip(),
        product.strip(),
        spec.strip() if spec else None,
        resolved_cust_id,
        revision.strip() if revision else "R0",
        status.strip(),
        width_mm,
        weave.strip() if weave else None,
        warp_denier,
        weft_denier,
        warp_ends,
        picks_per_cm,
        selvedge.strip() if selvedge else None,
        width_cm,
        epi,
        ppi,
        finish.strip() if finish else None,
        cord_type.strip() if cord_type else None,
        diameter_mm,
        carriers,
        yarns_per_carrier,
        core_yarns,
        yarn_denier,
        tpm,
        contraction,
        warp_crimp,
        weft_crimp,
        warp_tenacity,
        weft_tenacity,
        efficiency,
        notes.strip() if notes else None,
        const_uuid,
    )

    return RedirectResponse(
        url=f"/constructions/{id}",
        status_code=HTTP_303_SEE_OTHER,
    )


# ============================================================================
# 8. FORMAL APPROVAL ENDPOINT (POST)
# ============================================================================

@router.post("/{id}/approve", response_class=HTMLResponse)
async def approve_construction(
    request: Request,
    id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("constructions", "approve")),
):
    """
    POST /constructions/{id}/approve -- Formally approve an engineering construction.
    Guarded by constructions.approve (chief_technical).
    Enforces Segregation of Duties: Creator cannot approve their own construction.
    Enforces Physics Validation: Cover factor cannot exceed jamming limit of 28.0.
    """
    try:
        const_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Invalid ID format.")

    approver_id = user.get("id")
    if not approver_id:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Approver user ID required.")

    row = await conn.fetchrow("SELECT * FROM constructions WHERE id = $1;", const_uuid)
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Construction record not found.")

    # 1. Physics Check: Broad fabric jamming validation
    metrics = compute_construction_metrics(dict(row))
    if metrics.get("is_jammed"):
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Cannot approve construction: {metrics.get('jamming_warning')}",
        )

    # 2. Database Constraint & Update: Segregation of Duties
    try:
        res = await conn.execute(
            """
            UPDATE constructions SET
                status = 'Approved',
                approved_by = $1,
                approved_at = NOW()
            WHERE id = $2;
            """,
            uuid.UUID(approver_id),
            const_uuid,
        )
        if res == "UPDATE 0":
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Record not found.")
    except CheckViolationError as exc:
        if "constructions_no_self_approval" in str(exc):
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="Segregation of duties violation: the person who created a construction specification cannot approve it.",
            )
        raise

    return RedirectResponse(
        url=f"/constructions/{id}",
        status_code=HTTP_303_SEE_OTHER,
    )
