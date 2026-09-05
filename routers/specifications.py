import logging
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth.dependencies import current_user, require
from database import get_db

logger = logging.getLogger("snm_works.specifications")
router = APIRouter(tags=["specifications"])
templates = Jinja2Templates(directory="templates")


@router.get("/specifications", response_class=HTMLResponse)
async def list_specifications(
    request: Request,
    conn=Depends(get_db),
    user=Depends(require("specifications", "read")),
    q: Optional[str] = Query(None, description="Search query"),
    status_filter: Optional[str] = Query(None, description="Active status filter"),
):
    """
    Lists all master specifications with aggregate variant and requirement counts.
    Strictly enforced by PostgreSQL RLS.
    """
    query = """
        SELECT 
            s.id,
            s.spec_no,
            s.revision,
            s.title,
            s.issuing_body,
            s.issued_on,
            s.supersedes,
            s.distribution,
            s.scope,
            s.active,
            s.created_at,
            (SELECT COUNT(*) FROM spec_variants v WHERE v.spec_id = s.id) AS variant_count,
            (SELECT COUNT(*) FROM spec_requirements r WHERE r.spec_id = s.id) AS requirement_count,
            (SELECT COUNT(*) FROM spec_defects d WHERE d.spec_id = s.id) AS defect_count,
            (SELECT COUNT(*) FROM spec_sampling sm WHERE sm.spec_id = s.id) AS sampling_count
        FROM specifications s
        WHERE 1=1
    """
    params = []
    
    if q and q.strip():
        params.append(f"%{q.strip()}%")
        query += f" AND (s.spec_no ILIKE ${len(params)} OR s.title ILIKE ${len(params)} OR s.issuing_body ILIKE ${len(params)})"

    if status_filter == "active":
        query += " AND s.active = true"
    elif status_filter == "inactive":
        query += " AND s.active = false"

    query += " ORDER BY s.spec_no ASC, s.revision DESC;"

    rows = await conn.fetch(query, *params)
    specs = [dict(r) for r in rows]

    can_create = await conn.fetchval("SELECT auth_can('specifications', 'create')") or False

    return templates.TemplateResponse(
        request=request,
        name="specifications/index.html",
        context={
            "user": user,
            "specs": specs,
            "q": q or "",
            "status_filter": status_filter or "all",
            "can_create": can_create,
            "current_page": "specifications",
        },
    )


@router.get("/specifications/{spec_id}", response_class=HTMLResponse)
async def get_specification_detail(
    request: Request,
    spec_id: str,
    conn=Depends(get_db),
    user=Depends(require("specifications", "read")),
):
    """
    Displays full specification master record, variants, requirements,
    defect classification matrix (Table VI), ANSI/ASQC Z1.4 sampling plan,
    and physical type reference data.
    """
    # 1. Fetch Master Spec record
    try:
        spec_uuid = uuid.UUID(spec_id)
        spec_row = await conn.fetchrow(
            "SELECT * FROM specifications WHERE id = $1::uuid;",
            spec_uuid,
        )
    except ValueError:
        # Fallback query by spec_no if string identifier passed
        spec_row = await conn.fetchrow(
            "SELECT * FROM specifications WHERE spec_no = $1 OR (spec_no || revision) = $1 LIMIT 1;",
            spec_id,
        )

    if not spec_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Specification '{spec_id}' not found.",
        )

    spec = dict(spec_row)
    actual_spec_id = spec["id"]

    # 2. Fetch Type Variants (spec_variants)
    variant_rows = await conn.fetch(
        "SELECT * FROM spec_variants WHERE spec_id = $1 ORDER BY sort_order ASC, designation ASC;",
        actual_spec_id,
    )
    variants = [dict(r) for r in variant_rows]

    # 3. Fetch Requirements (spec_requirements)
    req_rows = await conn.fetch(
        """
        SELECT 
            r.*,
            v.designation AS variant_designation,
            v.class AS variant_class
        FROM spec_requirements r
        LEFT JOIN spec_variants v ON v.id = r.variant_id
        WHERE r.spec_id = $1
        ORDER BY r.sort_order ASC, r.parameter ASC;
        """,
        actual_spec_id,
    )
    requirements = [dict(r) for r in req_rows]

    # 4. Fetch Defect Classifications (spec_defects)
    defect_rows = await conn.fetch(
        """
        SELECT * FROM spec_defects 
        WHERE spec_id = $1 
        ORDER BY 
            CASE WHEN classification = 'Major' THEN 1 ELSE 2 END,
            examine ASC,
            defect ASC;
        """,
        actual_spec_id,
    )
    defects = [dict(r) for r in defect_rows]

    # 5. Fetch Sampling & Inspection Plan (spec_sampling)
    sampling_rows = await conn.fetch(
        """
        SELECT * FROM spec_sampling 
        WHERE spec_id = $1 
        ORDER BY purpose ASC, lot_from ASC;
        """,
        actual_spec_id,
    )
    sampling_plans = [dict(r) for r in sampling_rows]

    # 6. Fetch Physical Types Reference Data (mil_w_4088_types)
    ref_types_rows = await conn.fetch(
        "SELECT * FROM mil_w_4088_types ORDER BY break_min_lb ASC, type ASC;"
    )
    ref_types = [dict(r) for r in ref_types_rows]

    can_create = await conn.fetchval("SELECT auth_can('specifications', 'create')") or False
    can_update = await conn.fetchval("SELECT auth_can('specifications', 'update')") or False
    can_approve = await conn.fetchval("SELECT auth_can('specifications', 'approve')") or False

    return templates.TemplateResponse(
        request=request,
        name="specifications/detail.html",
        context={
            "user": user,
            "spec": spec,
            "variants": variants,
            "requirements": requirements,
            "defects": defects,
            "sampling_plans": sampling_plans,
            "ref_types": ref_types,
            "can_create": can_create,
            "can_update": can_update,
            "can_approve": can_approve,
            "current_page": "specifications",
        },
    )


@router.post("/specifications/{spec_id}/variants", response_class=RedirectResponse)
async def create_specification_variant(
    request: Request,
    spec_id: str,
    designation: str = Form(...),
    class_val: Optional[str] = Form(None, alias="class"),
    description: Optional[str] = Form(None),
    sort_order: int = Form(0),
    conn=Depends(get_db),
    user=Depends(require("specifications", "create")),
):
    """
    Creates a new type variant under a master specification.
    Restricted to roles holding specifications.create (chief_technical, chief_quality, qa_manager, etc.)
    """
    try:
        spec_uuid = uuid.UUID(spec_id)
    except ValueError:
        spec_uuid = await conn.fetchval("SELECT id FROM specifications WHERE spec_no = $1;", spec_id)
        if not spec_uuid:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Specification not found")

    uid = user.get("id") or user.get("sub") if isinstance(user, dict) else getattr(user, "id", None)
    user_uuid = uuid.UUID(str(uid)) if uid else None

    await conn.execute(
        """
        INSERT INTO spec_variants (spec_id, designation, class, description, sort_order, created_by)
        VALUES ($1, $2, $3, $4, $5, $6);
        """,
        spec_uuid,
        designation.strip(),
        class_val.strip() if class_val else None,
        description.strip() if description else None,
        sort_order,
        user_uuid,
    )

    return RedirectResponse(url=f"/specifications/{spec_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/specifications/variants/{variant_id}/approve", response_class=RedirectResponse)
async def approve_specification_variant(
    request: Request,
    variant_id: str,
    conn=Depends(get_db),
    user=Depends(require("specifications", "approve")),
):
    """
    Formal 4-eyes approval of a specification variant.
    Enforces Segregation of Duties: Creator cannot approve their own variant (DB CHECK constraint & Python validation).
    Restricted to chief_quality and chief_knowledge.
    """
    try:
        var_uuid = uuid.UUID(variant_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid variant ID")

    variant = await conn.fetchrow("SELECT * FROM spec_variants WHERE id = $1;", var_uuid)
    if not variant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Specification variant not found")

    uid = user.get("id") or user.get("sub") if isinstance(user, dict) else getattr(user, "id", None)
    user_uuid = uuid.UUID(str(uid)) if uid else None

    # Self-approval check (Python layer + DB constraint backstop)
    if variant["created_by"] and variant["created_by"] == user_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot approve a specification variant you created (Segregation of Duties / 4-Eyes principle).",
        )

    await conn.execute(
        """
        UPDATE spec_variants
        SET status = 'Approved',
            approved_by = $1,
            approved_at = now()
        WHERE id = $2;
        """,
        user_uuid,
        var_uuid,
    )

    return RedirectResponse(url=f"/specifications/{variant['spec_id']}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/specifications/{spec_id}/requirements", response_class=RedirectResponse)
async def create_specification_requirement(
    request: Request,
    spec_id: str,
    parameter: str = Form(...),
    limit_type: str = Form(...),
    unit: Optional[str] = Form(None),
    spec_value: Optional[float] = Form(None),
    tolerance: Optional[float] = Form(None),
    upper_limit: Optional[float] = Form(None),
    text_value: Optional[str] = Form(None),
    test_method: Optional[str] = Form(None),
    clause_ref: Optional[str] = Form(None),
    is_critical: bool = Form(False),
    variant_id: Optional[str] = Form(None),
    sort_order: int = Form(0),
    notes: Optional[str] = Form(None),
    conn=Depends(get_db),
    user=Depends(require("specifications", "create")),
):
    """
    Attaches a technical requirement parameter to either a specific variant or spec-wide.
    Restricted to roles holding specifications.create.
    """
    try:
        spec_uuid = uuid.UUID(spec_id)
    except ValueError:
        spec_uuid = await conn.fetchval("SELECT id FROM specifications WHERE spec_no = $1;", spec_id)
        if not spec_uuid:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Specification not found")

    var_uuid = None
    if variant_id and variant_id.strip():
        try:
            var_uuid = uuid.UUID(variant_id.strip())
        except ValueError:
            var_uuid = None

    await conn.execute(
        """
        INSERT INTO spec_requirements (
            spec_id, variant_id, parameter, unit, limit_type,
            spec_value, tolerance, upper_limit, text_value,
            test_method, clause_ref, is_critical, sort_order, notes
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14);
        """,
        spec_uuid,
        var_uuid,
        parameter.strip(),
        unit.strip() if unit else None,
        limit_type.strip(),
        Decimal(str(spec_value)) if spec_value is not None else None,
        Decimal(str(tolerance)) if tolerance is not None else None,
        Decimal(str(upper_limit)) if upper_limit is not None else None,
        text_value.strip() if text_value else None,
        test_method.strip() if test_method else None,
        clause_ref.strip() if clause_ref else None,
        is_critical,
        sort_order,
        notes.strip() if notes else None,
    )

    return RedirectResponse(url=f"/specifications/{spec_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/specifications/{spec_id}/approve", response_class=RedirectResponse)
async def approve_specification(
    request: Request,
    spec_id: str,
    conn=Depends(get_db),
    user=Depends(require("specifications", "approve")),
):
    """
    Formal release authority to approve / activate a master specification revision.
    Strictly restricted to chief_quality and chief_knowledge.
    """
    try:
        spec_uuid = uuid.UUID(spec_id)
    except ValueError:
        spec_uuid = await conn.fetchval("SELECT id FROM specifications WHERE spec_no = $1;", spec_id)
        if not spec_uuid:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Specification not found")

    await conn.execute(
        "UPDATE specifications SET active = true, updated_at = now() WHERE id = $1;",
        spec_uuid,
    )

    return RedirectResponse(url=f"/specifications/{spec_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/specifications/variants/{variant_id}/plan", response_class=HTMLResponse)
async def get_variant_inspection_plan(
    request: Request,
    variant_id: str,
    conn=Depends(get_db),
    user=Depends(require("specifications", "read")),
):
    """
    Inspection Plan preview generated by calling PostgreSQL spec_check_plan(variant_id).
    Combines variant-specific requirements and spec-wide generic requirements.
    """
    try:
        var_uuid = uuid.UUID(variant_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid variant ID '{variant_id}'")

    var_row = await conn.fetchrow(
        """
        SELECT 
            v.*,
            s.spec_no,
            s.revision,
            s.title as spec_title,
            s.issuing_body,
            s.active as spec_active
        FROM spec_variants v
        JOIN specifications s ON s.id = v.spec_id
        WHERE v.id = $1;
        """,
        var_uuid,
    )
    if not var_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Specification variant '{variant_id}' not found.")

    variant = dict(var_row)

    # Call PostgreSQL spec_check_plan function
    plan_rows = await conn.fetch("SELECT * FROM spec_check_plan($1::uuid);", var_uuid)
    plan_items = [dict(r) for r in plan_rows]

    return templates.TemplateResponse(
        request=request,
        name="specifications/inspection_plan.html",
        context={
            "user": user,
            "variant": variant,
            "plan_items": plan_items,
            "job": None,
            "current_page": "specifications",
        },
    )
