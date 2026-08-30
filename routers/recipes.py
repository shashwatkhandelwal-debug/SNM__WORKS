"""
Dye Recipes Router — SNM Works
=============================================================================
Manages formulation, process parameters, batch liquor calculations,
controlled shade matching, and technical approval workflows for dye recipes.

Permissions (role_permissions):
  - recipes.read: chief_executive, chief_quality, chief_technical, process_engineer, product_developer
  - recipes.create: chief_technical, process_engineer, product_developer
  - recipes.update: chief_technical, process_engineer, product_developer
  - recipes.approve: chief_technical (EXCLUSIVE)

Non-Negotiable Rule 6 (Segregation of Duties):
  A person cannot approve a dye recipe they created.
  Enforced by database CHECK constraint: dye_recipes_no_self_approval.
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

router = APIRouter(prefix="/recipes", tags=["Dye Recipes"])
templates = Jinja2Templates(directory="templates")

STATUSES = ["Draft", "Lab Dip", "Approved", "Rejected", "Cancelled"]
SHADE_RESULTS = ["Matched to Master", "Close - Acceptable", "Off-shade", "Pending Review"]
SUBSTRATES = ["Nylon 6,6", "Polyester", "Cotton", "Aramid", "Nylon 6", "Polypropylene", "Blended"]


# ============================================================================
# HELPER FUNCTIONS & CALCULATIONS
# ============================================================================

async def get_next_recipe_no(conn: asyncpg.Connection) -> str:
    """
    Generates the next sequential Recipe Number in REC-XXXX format.
    """
    rows = await conn.fetch("SELECT recipe_no FROM dye_recipes WHERE recipe_no LIKE 'REC-%'")
    max_num = 0
    pattern = re.compile(r"^REC-(\d+)$")

    for r in rows:
        val = r["recipe_no"]
        m = pattern.match(val)
        if m:
            num = int(m.group(1))
            if num > max_num:
                max_num = num

    return f"REC-{max_num + 1:04d}"


def compute_recipe_math(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Computes dyestuff mass concentrations (% OWF), total dye grams,
    and bath liquor volume in litres.
    """
    pct1 = float(row.get("pct1") or 0.0)
    pct2 = float(row.get("pct2") or 0.0)
    pct3 = float(row.get("pct3") or 0.0)
    pct4 = float(row.get("pct4") or 0.0)
    total_pct = round(pct1 + pct2 + pct3 + pct4, 4)

    batch_kg = float(row.get("batch_kg") or 0.0)
    total_dye_grams = round(batch_kg * 1000.0 * (total_pct / 100.0), 2) if batch_kg > 0 else 0.0

    # Parse liquor ratio (e.g. "1:10" -> 10, "1:8" -> 8)
    liquor_ratio_str = str(row.get("liquor_ratio") or "1:10")
    ratio_multiplier = 10.0
    if ":" in liquor_ratio_str:
        parts = liquor_ratio_str.split(":")
        try:
            ratio_multiplier = float(parts[1].strip())
        except (ValueError, IndexError):
            ratio_multiplier = 10.0
    elif liquor_ratio_str.replace(".", "", 1).isdigit():
        ratio_multiplier = float(liquor_ratio_str)

    liquor_volume_litres = round(batch_kg * ratio_multiplier, 1) if batch_kg > 0 else 0.0

    dye_breakdown = []
    for idx, (dye_name, pct_val) in enumerate([
        (row.get("dye1"), pct1),
        (row.get("dye2"), pct2),
        (row.get("dye3"), pct3),
        (row.get("dye4"), pct4),
    ], start=1):
        if dye_name or pct_val > 0:
            dye_grams = round(batch_kg * 1000.0 * (pct_val / 100.0), 2) if batch_kg > 0 else 0.0
            dye_breakdown.append({
                "num": idx,
                "name": dye_name or f"Dye {idx}",
                "pct": pct_val,
                "grams": dye_grams,
            })

    return {
        "total_pct": total_pct,
        "total_dye_grams": total_dye_grams,
        "liquor_volume_litres": liquor_volume_litres,
        "ratio_multiplier": ratio_multiplier,
        "dye_breakdown": dye_breakdown,
    }


# ============================================================================
# 1. LIST DYE RECIPES (REGISTER)
# ============================================================================

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def list_recipes(
    request: Request,
    status_filter: Optional[str] = "all",
    substrate_filter: Optional[str] = "all",
    q: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("recipes", "read")),
):
    """
    GET /recipes — Register of dye recipes with status and substrate filters.
    """
    query = """
        SELECT 
            r.id::text,
            r.recipe_no,
            r.dyed_on::text,
            r.job_id::text,
            j.job_no,
            j.product as job_product,
            r.substrate,
            r.target_shade,
            r.batch_kg,
            r.dye1, r.pct1,
            r.dye2, r.pct2,
            r.dye3, r.pct3,
            r.dye4, r.pct4,
            r.liquor_ratio,
            r.temp_c,
            r.time_min,
            r.ph,
            r.auxiliaries,
            r.shade_result,
            r.status,
            r.created_by::text,
            u_cr.full_name as creator_name,
            r.approved_by::text,
            u_ap.full_name as approver_name,
            r.created_at
        FROM dye_recipes r
        LEFT JOIN jobs j ON r.job_id = j.id
        LEFT JOIN profiles u_cr ON r.created_by = u_cr.id
        LEFT JOIN profiles u_ap ON r.approved_by = u_ap.id
        WHERE 1=1
    """
    params = []
    param_idx = 1

    if status_filter and status_filter != "all":
        query += f" AND r.status = ${param_idx}"
        params.append(status_filter)
        param_idx += 1

    if substrate_filter and substrate_filter != "all":
        query += f" AND r.substrate = ${param_idx}"
        params.append(substrate_filter)
        param_idx += 1

    if q and q.strip():
        search = f"%{q.strip()}%"
        query += f" AND (r.recipe_no ILIKE ${param_idx} OR r.target_shade ILIKE ${param_idx} OR r.substrate ILIKE ${param_idx})"
        params.append(search)
        param_idx += 1

    query += " ORDER BY r.created_at DESC"

    rows = await conn.fetch(query, *params)
    recipes = []
    for r in rows:
        item = dict(r)
        item["math"] = compute_recipe_math(item)
        recipes.append(item)

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="recipes/list.html",
        context={
            "user": user_info,
            "recipes": recipes,
            "selected_status": status_filter,
            "selected_substrate": substrate_filter,
            "search_query": q or "",
            "statuses": STATUSES,
            "substrates": SUBSTRATES,
            "current_page": "recipes",
            "current_func": "TEC",
        },
    )


# ============================================================================
# 2. CREATE RECIPE FORM (GET)
# ============================================================================

@router.get("/new", response_class=HTMLResponse)
async def new_recipe_form(
    request: Request,
    job_id: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("recipes", "create")),
):
    """
    GET /recipes/new — Create a new dye formulation recipe.
    """
    next_recipe_no = await get_next_recipe_no(conn)

    job_rows = await conn.fetch(
        """
        SELECT id::text, job_no, product 
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
        name="recipes/form.html",
        context={
            "user": user_info,
            "is_edit": False,
            "recipe_no_preview": next_recipe_no,
            "jobs": jobs,
            "selected_job_id": job_id or "",
            "substrates": SUBSTRATES,
            "shade_results": SHADE_RESULTS,
            "statuses": STATUSES,
            "current_page": "recipes",
            "current_func": "TEC",
        },
    )


# ============================================================================
# 3. CREATE RECIPE (POST)
# ============================================================================

@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_recipe(
    request: Request,
    job_id: Optional[str] = Form(None),
    substrate: str = Form("Nylon 6,6"),
    target_shade: str = Form(...),
    batch_kg: Optional[float] = Form(None),
    dye1: Optional[str] = Form(None),
    pct1: Optional[float] = Form(None),
    dye2: Optional[str] = Form(None),
    pct2: Optional[float] = Form(None),
    dye3: Optional[str] = Form(None),
    pct3: Optional[float] = Form(None),
    dye4: Optional[str] = Form(None),
    pct4: Optional[float] = Form(None),
    liquor_ratio: str = Form("1:10"),
    temp_c: Optional[float] = Form(98.0),
    time_min: Optional[float] = Form(45.0),
    ph: Optional[float] = Form(4.5),
    auxiliaries: Optional[str] = Form(None),
    shade_result: Optional[str] = Form(None),
    status: str = Form("Draft"),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("recipes", "create")),
):
    """
    POST /recipes — Creates a new dye recipe with race-safe sequence numbering.
    """
    creator_id = user.get("id")
    if not creator_id:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Authenticated user ID is missing.")

    # Status cannot be created directly as 'Approved'
    if status == "Approved":
        status = "Draft"

    max_retries = 20
    created_id = None

    for attempt in range(max_retries):
        recipe_no = await get_next_recipe_no(conn)
        new_id = uuid.uuid4()

        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO dye_recipes (
                        id,
                        recipe_no,
                        dyed_on,
                        job_id,
                        substrate,
                        target_shade,
                        batch_kg,
                        dye1, pct1,
                        dye2, pct2,
                        dye3, pct3,
                        dye4, pct4,
                        liquor_ratio,
                        temp_c,
                        time_min,
                        ph,
                        auxiliaries,
                        shade_result,
                        status,
                        created_by
                    ) VALUES (
                        $1::uuid, $2, CURRENT_DATE, $3::uuid, $4, $5, $6,
                        $7, $8, $9, $10, $11, $12, $13, $14,
                        $15, $16, $17, $18, $19, $20, $21, $22::uuid
                    )
                    RETURNING id::text;
                    """,
                    new_id,
                    recipe_no,
                    uuid.UUID(job_id) if job_id and job_id.strip() else None,
                    substrate.strip(),
                    target_shade.strip(),
                    batch_kg,
                    dye1.strip() if dye1 else None,
                    pct1,
                    dye2.strip() if dye2 else None,
                    pct2,
                    dye3.strip() if dye3 else None,
                    pct3,
                    dye4.strip() if dye4 else None,
                    pct4,
                    liquor_ratio.strip() if liquor_ratio else "1:10",
                    temp_c,
                    time_min,
                    ph,
                    auxiliaries.strip() if auxiliaries else None,
                    shade_result.strip() if shade_result else None,
                    status,
                    uuid.UUID(creator_id),
                )
                created_id = row["id"]
                break
        except UniqueViolationError:
            if attempt == max_retries - 1:
                raise HTTPException(
                    status_code=HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Could not generate unique Recipe No due to high concurrency. Please retry.",
                )
            await asyncio.sleep(0.01 * (attempt + 1))
        except CheckViolationError as e:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=f"Validation constraint error: {str(e)}",
            )

    return RedirectResponse(url=f"/recipes/{created_id}", status_code=HTTP_303_SEE_OTHER)


# ============================================================================
# 4. VIEW RECIPE DETAIL (GET)
# ============================================================================

@router.get("/{recipe_id}", response_class=HTMLResponse)
async def view_recipe_detail(
    recipe_id: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("recipes", "read")),
):
    """
    GET /recipes/{recipe_id} — Detailed technical recipe view with formula math.
    """
    row = await conn.fetchrow(
        """
        SELECT 
            r.id::text,
            r.recipe_no,
            r.dyed_on::text,
            r.job_id::text,
            j.job_no,
            j.product as job_product,
            r.substrate,
            r.target_shade,
            r.batch_kg,
            r.dye1, r.pct1,
            r.dye2, r.pct2,
            r.dye3, r.pct3,
            r.dye4, r.pct4,
            r.liquor_ratio,
            r.temp_c,
            r.time_min,
            r.ph,
            r.auxiliaries,
            r.shade_result,
            r.status,
            r.created_by::text,
            u_cr.full_name as creator_name,
            r.approved_by::text,
            u_ap.full_name as approver_name,
            r.approved_at,
            r.created_at
        FROM dye_recipes r
        LEFT JOIN jobs j ON r.job_id = j.id
        LEFT JOIN profiles u_cr ON r.created_by = u_cr.id
        LEFT JOIN profiles u_ap ON r.approved_by = u_ap.id
        WHERE r.id = $1::uuid
        """,
        recipe_id,
    )

    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Dye recipe not found.")

    recipe = dict(row)
    recipe["math"] = compute_recipe_math(recipe)

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="recipes/detail.html",
        context={
            "user": user_info,
            "r": recipe,
            "current_page": "recipes",
            "current_func": "TEC",
        },
    )


# ============================================================================
# 5. EDIT RECIPE FORM (GET)
# ============================================================================

@router.get("/{recipe_id}/edit", response_class=HTMLResponse)
async def edit_recipe_form(
    recipe_id: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("recipes", "update")),
):
    """
    GET /recipes/{recipe_id}/edit — Form to edit formulation parameters.
    """
    row = await conn.fetchrow(
        """
        SELECT * FROM dye_recipes WHERE id = $1::uuid;
        """,
        recipe_id,
    )
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Dye recipe not found.")

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
        name="recipes/form.html",
        context={
            "user": user_info,
            "is_edit": True,
            "r": item_dict,
            "recipe_no_preview": item_dict["recipe_no"],
            "jobs": jobs,
            "selected_job_id": str(item_dict.get("job_id") or ""),
            "substrates": SUBSTRATES,
            "shade_results": SHADE_RESULTS,
            "statuses": STATUSES,
            "current_page": "recipes",
            "current_func": "TEC",
        },
    )


# ============================================================================
# 6. UPDATE RECIPE (POST)
# ============================================================================

@router.post("/{recipe_id}/update", response_class=HTMLResponse)
async def update_recipe(
    recipe_id: str,
    request: Request,
    job_id: Optional[str] = Form(None),
    substrate: str = Form("Nylon 6,6"),
    target_shade: str = Form(...),
    batch_kg: Optional[float] = Form(None),
    dye1: Optional[str] = Form(None),
    pct1: Optional[float] = Form(None),
    dye2: Optional[str] = Form(None),
    pct2: Optional[float] = Form(None),
    dye3: Optional[str] = Form(None),
    pct3: Optional[float] = Form(None),
    dye4: Optional[str] = Form(None),
    pct4: Optional[float] = Form(None),
    liquor_ratio: str = Form("1:10"),
    temp_c: Optional[float] = Form(98.0),
    time_min: Optional[float] = Form(45.0),
    ph: Optional[float] = Form(4.5),
    auxiliaries: Optional[str] = Form(None),
    shade_result: Optional[str] = Form(None),
    status: str = Form("Draft"),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("recipes", "update")),
):
    """
    POST /recipes/{recipe_id}/update — Updates dye recipe parameters.
    Blocks transitions to Approved status unless user holds recipes.approve.
    """
    if status == "Approved":
        can_approve = await conn.fetchval("SELECT auth_can('recipes', 'approve')")
        if not can_approve:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="Your roles do not permit approving recipes. Use dedicated approval sign-off.",
            )

    try:
        await conn.execute(
            """
            UPDATE dye_recipes SET
                job_id = $1::uuid,
                substrate = $2,
                target_shade = $3,
                batch_kg = $4,
                dye1 = $5, pct1 = $6,
                dye2 = $7, pct2 = $8,
                dye3 = $9, pct3 = $10,
                dye4 = $11, pct4 = $12,
                liquor_ratio = $13,
                temp_c = $14,
                time_min = $15,
                ph = $16,
                auxiliaries = $17,
                shade_result = $18,
                status = $19
            WHERE id = $20::uuid;
            """,
            uuid.UUID(job_id) if job_id and job_id.strip() else None,
            substrate.strip(),
            target_shade.strip(),
            batch_kg,
            dye1.strip() if dye1 else None,
            pct1,
            dye2.strip() if dye2 else None,
            pct2,
            dye3.strip() if dye3 else None,
            pct3,
            dye4.strip() if dye4 else None,
            pct4,
            liquor_ratio.strip() if liquor_ratio else "1:10",
            temp_c,
            time_min,
            ph,
            auxiliaries.strip() if auxiliaries else None,
            shade_result.strip() if shade_result else None,
            status,
            uuid.UUID(recipe_id),
        )
    except CheckViolationError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Validation constraint error: {str(e)}",
        )

    return RedirectResponse(url=f"/recipes/{recipe_id}", status_code=HTTP_303_SEE_OTHER)


# ============================================================================
# 7. APPROVE RECIPE (POST)
# ============================================================================

@router.post("/{recipe_id}/approve", response_class=HTMLResponse)
async def approve_recipe(
    recipe_id: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("recipes", "approve")),
):
    """
    POST /recipes/{recipe_id}/approve — Technical release & sign-off.
    Gated on:
      1. Role holding recipes.approve (chief_technical)
      2. Shade result must be 'Matched to Master' or 'Close - Acceptable'
      3. Segregation of duties: Creator cannot approve their own recipe.
    """
    approver_id = user.get("id")
    if not approver_id:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Authenticated user ID is missing.")

    row = await conn.fetchrow(
        "SELECT id, recipe_no, created_by::text, shade_result, status FROM dye_recipes WHERE id = $1::uuid;",
        recipe_id,
    )
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Dye recipe not found.")

    # 1. Controlled shade result gate
    shade_res = row.get("shade_result")
    if shade_res not in ("Matched to Master", "Close - Acceptable"):
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Cannot approve recipe: Shade result must be 'Matched to Master' or 'Close - Acceptable' (current: {shade_res or 'None'}).",
        )

    # 2. Segregation of duties check (Python pre-check + DB constraint enforcement)
    if str(row["created_by"]) == approver_id:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Segregation of duties violation: the person who created a dye recipe cannot approve it.",
        )

    try:
        await conn.execute(
            """
            UPDATE dye_recipes SET
                status = 'Approved',
                approved_by = $1::uuid,
                approved_at = NOW()
            WHERE id = $2::uuid;
            """,
            uuid.UUID(approver_id),
            uuid.UUID(recipe_id),
        )
    except CheckViolationError:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Database constraint violation: self-approval is forbidden by policy.",
        )

    return RedirectResponse(url=f"/recipes/{recipe_id}", status_code=HTTP_303_SEE_OTHER)
