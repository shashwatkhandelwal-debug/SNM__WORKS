"""
routers/specifications.py — Master Specifications Register, Detail Tabs, Variants, Requirements & Approval.
"""

import logging
import math
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional
import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth.dependencies import current_user, require
from database import get_db
from routers.spec_browser import router as browser_router

logger = logging.getLogger("snm_works.specifications")
router = APIRouter(tags=["specifications"])
router.include_router(browser_router)
templates = Jinja2Templates(directory="templates")

SORT_WHITELIST = {
    "spec_no_asc": "s.spec_no ASC, s.revision DESC",
    "spec_no_desc": "s.spec_no DESC, s.revision DESC",
    "title_asc": "s.title ASC",
    "title_desc": "s.title DESC",
    "issued_on_asc": "s.issued_on ASC NULLS LAST",
    "issued_on_desc": "s.issued_on DESC NULLS LAST",
    "created_at_desc": "s.created_at DESC",
}


@router.get("/specifications", response_class=HTMLResponse)
async def list_specifications(
    request: Request,
    conn=Depends(get_db),
    user=Depends(require("specifications", "read")),
    q: Optional[str] = Query(None),
    issuing_body: Optional[str] = Query(None),
    active: Optional[str] = Query("all"),
    status_filter: Optional[str] = Query(None),
    variant_status: Optional[str] = Query(None),
    has_pdf: Optional[str] = Query(None),
    sort: Optional[str] = Query("spec_no_asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Lists specifications with aggregate counts, advanced filters, sorting, and DB pagination."""
    active_val = active if active != "all" else (status_filter if status_filter in ("active", "inactive") else "all")
    where_clauses, params = ["1=1"], []

    if q and q.strip():
        params.append(f"%{q.strip()}%")
        where_clauses.append(f"(s.spec_no ILIKE ${len(params)} OR s.title ILIKE ${len(params)} OR s.issuing_body ILIKE ${len(params)})")

    if issuing_body and issuing_body.strip() and issuing_body.lower() != "all":
        params.append(issuing_body.strip())
        where_clauses.append(f"s.issuing_body = ${len(params)}")

    if active_val == "active":
        where_clauses.append("s.active = true")
    elif active_val == "inactive":
        where_clauses.append("s.active = false")

    if variant_status and variant_status.strip() and variant_status.lower() not in ("all", "any"):
        params.append(variant_status.strip())
        where_clauses.append(f"EXISTS (SELECT 1 FROM spec_variants v WHERE v.spec_id = s.id AND v.status = ${len(params)})")

    if has_pdf and has_pdf.strip().lower() in ("true", "1", "yes"):
        where_clauses.append("EXISTS (SELECT 1 FROM spec_pdf_uploads u WHERE u.spec_id = s.id)")

    order_sql = SORT_WHITELIST.get(sort, "s.spec_no ASC, s.revision DESC")
    offset = (page - 1) * page_size
    params.extend([page_size, offset])

    query = f"""
        SELECT s.id, s.spec_no, s.revision, s.title, s.issuing_body, s.issued_on, s.supersedes,
               s.distribution, s.scope, s.active, s.created_at,
               (SELECT COUNT(*) FROM spec_variants v WHERE v.spec_id = s.id) AS variant_count,
               (SELECT COUNT(*) FROM spec_requirements r WHERE r.spec_id = s.id) AS requirement_count,
               (SELECT COUNT(*) FROM spec_defects d WHERE d.spec_id = s.id) AS defect_count,
               (SELECT COUNT(*) FROM spec_sampling sm WHERE sm.spec_id = s.id) AS sampling_count,
               (SELECT COUNT(*) FROM spec_pdf_uploads u WHERE u.spec_id = s.id) AS pdf_count,
               COUNT(*) OVER() AS total_count
        FROM specifications s
        WHERE {' AND '.join(where_clauses)}
        ORDER BY {order_sql}
        LIMIT ${len(params) - 1} OFFSET ${len(params)};
    """
    rows = await conn.fetch(query, *params)
    specs = [dict(r) for r in rows]
    total_count = int(specs[0]["total_count"]) if specs else 0
    total_pages = max(1, math.ceil(total_count / page_size))

    bodies_rows = await conn.fetch("SELECT DISTINCT issuing_body FROM specifications WHERE issuing_body IS NOT NULL AND issuing_body <> '' ORDER BY issuing_body ASC;")
    issuing_bodies = [r["issuing_body"] for r in bodies_rows]
    can_create = await conn.fetchval("SELECT auth_can('specifications', 'create')") or False

    return templates.TemplateResponse(
        request=request,
        name="specifications/index.html",
        context={
            "user": user,
            "specs": specs,
            "q": q or "",
            "issuing_body": issuing_body or "all",
            "issuing_bodies": issuing_bodies,
            "active": active_val,
            "variant_status": variant_status or "all",
            "has_pdf": has_pdf or "",
            "sort": sort or "spec_no_asc",
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "can_create": can_create,
            "current_page": "specifications",
        },
    )


@router.get("/specifications/variants/{variant_id}/plan", response_class=HTMLResponse)
async def get_variant_inspection_plan(
    request: Request,
    variant_id: str,
    conn=Depends(get_db),
    user=Depends(require("specifications", "read")),
):
    """Inspection Plan preview generated by calling PostgreSQL spec_check_plan(variant_id)."""
    try:
        var_uuid = uuid.UUID(variant_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid variant ID '{variant_id}'")

    var_row = await conn.fetchrow(
        "SELECT v.*, s.spec_no, s.revision, s.title as spec_title, s.issuing_body, s.active as spec_active "
        "FROM spec_variants v JOIN specifications s ON s.id = v.spec_id WHERE v.id = $1;",
        var_uuid,
    )
    if not var_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Specification variant '{variant_id}' not found.")

    plan_rows = await conn.fetch("SELECT * FROM spec_check_plan($1::uuid);", var_uuid)
    return templates.TemplateResponse(
        request=request,
        name="specifications/inspection_plan.html",
        context={
            "user": user,
            "variant": dict(var_row),
            "plan_items": [dict(r) for r in plan_rows],
            "job": None,
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

    try:
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
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A variant with this designation and class already exists for this specification."
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
    """Formal release authority to approve / activate a master specification revision."""
    try:
        spec_uuid = uuid.UUID(spec_id)
    except ValueError:
        spec_uuid = await conn.fetchval("SELECT id FROM specifications WHERE spec_no = $1;", spec_id)
        if not spec_uuid:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Specification not found")

    await conn.execute("UPDATE specifications SET active = true, updated_at = now() WHERE id = $1;", spec_uuid)
    return RedirectResponse(url=f"/specifications/{spec_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/specifications/{spec_id}", response_class=HTMLResponse)
async def get_specification_detail(
    request: Request,
    spec_id: str,
    variant_id: Optional[str] = Query(None),
    conn=Depends(get_db),
    user=Depends(require("specifications", "read")),
):
    """Displays master specification details, tabs for variants, requirements, defects, sampling, and source PDFs."""
    try:
        spec_uuid = uuid.UUID(spec_id)
        spec_row = await conn.fetchrow("SELECT * FROM specifications WHERE id = $1::uuid;", spec_uuid)
    except ValueError:
        spec_row = await conn.fetchrow("SELECT * FROM specifications WHERE spec_no = $1 OR (spec_no || revision) = $1 LIMIT 1;", spec_id)

    if not spec_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Specification '{spec_id}' not found.")

    spec = dict(spec_row)
    actual_spec_id = spec["id"]

    variant_rows = await conn.fetch("SELECT * FROM spec_variants WHERE spec_id = $1 ORDER BY sort_order ASC, designation ASC;", actual_spec_id)
    variants = [dict(r) for r in variant_rows]

    selected_var_uuid = None
    if variant_id and variant_id.strip() and variant_id.lower() not in ("all", ""):
        try:
            selected_var_uuid = uuid.UUID(variant_id.strip())
        except ValueError:
            selected_var_uuid = None

    if selected_var_uuid:
        req_rows = await conn.fetch(
            "SELECT r.*, v.designation AS variant_designation, v.class AS variant_class "
            "FROM spec_requirements r LEFT JOIN spec_variants v ON v.id = r.variant_id "
            "WHERE r.spec_id = $1 AND (r.variant_id = $2 OR r.variant_id IS NULL) "
            "ORDER BY r.sort_order ASC, r.parameter ASC;",
            actual_spec_id, selected_var_uuid,
        )
    else:
        req_rows = await conn.fetch(
            "SELECT r.*, v.designation AS variant_designation, v.class AS variant_class "
            "FROM spec_requirements r LEFT JOIN spec_variants v ON v.id = r.variant_id "
            "WHERE r.spec_id = $1 ORDER BY r.sort_order ASC, r.parameter ASC;",
            actual_spec_id,
        )

    defect_rows = await conn.fetch("SELECT * FROM spec_defects WHERE spec_id = $1 ORDER BY CASE WHEN classification = 'Major' THEN 1 ELSE 2 END, examine ASC, defect ASC;", actual_spec_id)
    sampling_rows = await conn.fetch("SELECT * FROM spec_sampling WHERE spec_id = $1 ORDER BY purpose ASC, lot_from ASC;", actual_spec_id)
    pdf_rows = await conn.fetch("SELECT u.*, p.full_name AS uploader_name FROM spec_pdf_uploads u LEFT JOIN profiles p ON p.id = u.uploaded_by WHERE u.spec_id = $1 ORDER BY u.uploaded_at DESC;", actual_spec_id)

    ref_types = []
    if "4088" in spec.get("spec_no", ""):
        ref_types_rows = await conn.fetch("SELECT * FROM mil_w_4088_types ORDER BY break_min_lb ASC, type ASC;")
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
            "requirements": [dict(r) for r in req_rows],
            "defects": [dict(r) for r in defect_rows],
            "sampling_plans": [dict(r) for r in sampling_rows],
            "source_pdfs": [dict(r) for r in pdf_rows],
            "ref_types": ref_types,
            "selected_variant_id": str(selected_var_uuid) if selected_var_uuid else "",
            "can_create": can_create,
            "can_update": can_update,
            "can_approve": can_approve,
            "current_page": "specifications",
        },
    )
