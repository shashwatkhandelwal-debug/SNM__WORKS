"""
routers/spec_browser.py — Specifications Review Queue, Comparison Matrix, and Source PDF Viewer.
"""

import json
import logging
from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from auth.dependencies import current_user, require
from database import get_db
from services.spec_diff import compare_specification_revisions, compare_variant_requirements
from services.storage import get_file_from_storage

logger = logging.getLogger("snm_works.spec_browser")
router = APIRouter(tags=["specifications_browser"])
templates = Jinja2Templates(directory="templates")


@router.get("/specifications/queue", response_class=HTMLResponse)
async def specifications_queue(
    request: Request,
    conn=Depends(get_db),
    user=Depends(require("specifications", "read")),
    upload_status: Optional[str] = Query("all", description="Filter uploads by status"),
):
    """
    Approval & Ingestion Queue:
    1. Draft variants pending 4-eyes approval.
    2. Uploaded / parsed spec PDFs not yet loaded into live DB.
    """
    uid = user.get("id") or user.get("sub") if isinstance(user, dict) else getattr(user, "id", None)
    user_uuid = uuid.UUID(str(uid)) if uid else None

    # 1. Draft variants query
    draft_variants_rows = await conn.fetch(
        """
        SELECT 
            v.id AS variant_id,
            v.spec_id,
            v.designation,
            v.class,
            v.status,
            v.created_by,
            s.spec_no,
            s.revision,
            s.title AS spec_title,
            p.full_name AS creator_name
        FROM spec_variants v
        JOIN specifications s ON s.id = v.spec_id
        LEFT JOIN profiles p ON p.id = v.created_by
        WHERE v.status = 'Draft'
        ORDER BY s.spec_no ASC, v.designation ASC;
        """
    )
    draft_variants = [dict(r) for r in draft_variants_rows]

    # 2. Spec PDF Uploads queue (not Loaded)
    upload_where = ["u.status <> 'Loaded'"]
    upload_params: List[Any] = []
    if upload_status and upload_status.lower() != "all":
        upload_params.append(upload_status.strip())
        upload_where.append(f"u.status = ${len(upload_params)}::spec_upload_status")

    uploads_query = f"""
        SELECT 
            u.id AS upload_id,
            u.sku_id,
            u.spec_id,
            u.storage_path,
            u.pdf_sha256,
            u.source,
            u.original_filename,
            u.file_size_bytes,
            u.status,
            u.error_message,
            u.uploaded_at,
            p.full_name AS uploader_name,
            s.sku_code,
            s.title AS sku_title,
            spec.spec_no,
            spec.revision AS spec_revision
        FROM spec_pdf_uploads u
        LEFT JOIN profiles p ON p.id = u.uploaded_by
        LEFT JOIN skus s ON s.id = u.sku_id
        LEFT JOIN specifications spec ON spec.id = u.spec_id
        WHERE {' AND '.join(upload_where)}
        ORDER BY u.uploaded_at DESC;
    """
    upload_rows = await conn.fetch(uploads_query, *upload_params)
    pending_uploads = [dict(r) for r in upload_rows]

    can_approve = await conn.fetchval("SELECT auth_can('specifications', 'approve')") or False

    return templates.TemplateResponse(
        request=request,
        name="specifications/queue.html",
        context={
            "user": user,
            "user_uuid": user_uuid,
            "draft_variants": draft_variants,
            "pending_uploads": pending_uploads,
            "upload_status": upload_status or "all",
            "can_approve": can_approve,
            "current_page": "specifications",
        },
    )


@router.get("/specifications/compare", response_class=HTMLResponse)
async def compare_specifications(
    request: Request,
    conn=Depends(get_db),
    user=Depends(require("specifications", "read")),
    mode: str = Query("variant", description="Comparison mode: variant or revision"),
    a: Optional[str] = Query(None, description="Variant A ID or Revision A Spec ID"),
    b: Optional[str] = Query(None, description="Variant B ID or Revision B Spec ID"),
    spec_no: Optional[str] = Query(None, description="Specification number for revision compare"),
):
    """
    Specification Comparison Engine:
    - Mode 'variant': Side-by-side requirement matrix of two variants with difference highlighting.
    - Mode 'revision': Header diff, variant additions/removals, and requirement changes between revisions.
    """
    all_var_rows = await conn.fetch(
        """
        SELECT v.id, v.designation, v.class, v.status, s.spec_no, s.revision, s.title AS spec_title
        FROM spec_variants v
        JOIN specifications s ON s.id = v.spec_id
        ORDER BY s.spec_no ASC, v.designation ASC;
        """
    )
    variant_options = [dict(r) for r in all_var_rows]

    all_specs_rows = await conn.fetch(
        "SELECT id, spec_no, revision, title FROM specifications ORDER BY spec_no ASC, revision DESC;"
    )
    spec_options = [dict(r) for r in all_specs_rows]

    diff_data: Optional[Dict[str, Any]] = None
    variant_a_info = None
    variant_b_info = None
    spec_a_info = None
    spec_b_info = None

    if mode == "variant" and a and b:
        try:
            uuid_a = uuid.UUID(a)
            uuid_b = uuid.UUID(b)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid variant UUID parameter.")

        row_a = await conn.fetchrow(
            """
            SELECT v.*, s.spec_no, s.revision, s.title AS spec_title 
            FROM spec_variants v JOIN specifications s ON s.id = v.spec_id WHERE v.id = $1;
            """,
            uuid_a,
        )
        row_b = await conn.fetchrow(
            """
            SELECT v.*, s.spec_no, s.revision, s.title AS spec_title 
            FROM spec_variants v JOIN specifications s ON s.id = v.spec_id WHERE v.id = $1;
            """,
            uuid_b,
        )
        if row_a and row_b:
            variant_a_info = dict(row_a)
            variant_b_info = dict(row_b)

            reqs_a_rows = await conn.fetch(
                """
                SELECT r.*, v.designation AS variant_designation
                FROM spec_requirements r
                LEFT JOIN spec_variants v ON v.id = r.variant_id
                WHERE r.spec_id = $1 AND (r.variant_id = $2 OR r.variant_id IS NULL)
                ORDER BY r.sort_order ASC, r.parameter ASC;
                """,
                variant_a_info["spec_id"],
                uuid_a,
            )
            reqs_b_rows = await conn.fetch(
                """
                SELECT r.*, v.designation AS variant_designation
                FROM spec_requirements r
                LEFT JOIN spec_variants v ON v.id = r.variant_id
                WHERE r.spec_id = $1 AND (r.variant_id = $2 OR r.variant_id IS NULL)
                ORDER BY r.sort_order ASC, r.parameter ASC;
                """,
                variant_b_info["spec_id"],
                uuid_b,
            )
            diff_matrix = compare_variant_requirements([dict(r) for r in reqs_a_rows], [dict(r) for r in reqs_b_rows])
            diff_data = {"req_diffs": diff_matrix}

    elif mode == "revision" and a and b:
        try:
            uuid_a = uuid.UUID(a)
            uuid_b = uuid.UUID(b)
            spec_a_row = await conn.fetchrow("SELECT * FROM specifications WHERE id = $1;", uuid_a)
            spec_b_row = await conn.fetchrow("SELECT * FROM specifications WHERE id = $1;", uuid_b)
        except ValueError:
            spec_a_row = await conn.fetchrow("SELECT * FROM specifications WHERE spec_no = $1 AND revision = $2 LIMIT 1;", spec_no or a, a)
            spec_b_row = await conn.fetchrow("SELECT * FROM specifications WHERE spec_no = $1 AND revision = $2 LIMIT 1;", spec_no or b, b)

        if spec_a_row and spec_b_row:
            spec_a_info = dict(spec_a_row)
            spec_b_info = dict(spec_b_row)

            vars_a = await conn.fetch("SELECT * FROM spec_variants WHERE spec_id = $1;", spec_a_info["id"])
            vars_b = await conn.fetch("SELECT * FROM spec_variants WHERE spec_id = $1;", spec_b_info["id"])

            reqs_a = await conn.fetch("SELECT * FROM spec_requirements WHERE spec_id = $1;", spec_a_info["id"])
            reqs_b = await conn.fetch("SELECT * FROM spec_requirements WHERE spec_id = $1;", spec_b_info["id"])

            diff_data = compare_specification_revisions(
                spec_a_info,
                spec_b_info,
                [dict(r) for r in vars_a],
                [dict(r) for r in vars_b],
                [dict(r) for r in reqs_a],
                [dict(r) for r in reqs_b],
            )

    return templates.TemplateResponse(
        request=request,
        name="specifications/compare.html",
        context={
            "user": user,
            "mode": mode,
            "a": a or "",
            "b": b or "",
            "spec_no": spec_no or "",
            "variant_options": variant_options,
            "spec_options": spec_options,
            "variant_a": variant_a_info,
            "variant_b": variant_b_info,
            "spec_a": spec_a_info,
            "spec_b": spec_b_info,
            "diff_data": diff_data,
            "current_page": "specifications",
        },
    )


@router.get("/specifications/{spec_no}/source/{upload_id}", response_class=HTMLResponse)
async def view_specification_source_pdf(
    request: Request,
    spec_no: str,
    upload_id: str,
    conn=Depends(get_db),
    user=Depends(require("specifications", "read")),
):
    """
    Side-by-side read-only viewer for specification source PDF and parsed/corrected structured data.
    """
    try:
        upload_uuid = uuid.UUID(upload_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid upload ID '{upload_id}'.")

    upload_row = await conn.fetchrow(
        """
        SELECT u.*, p.full_name AS uploader_name, s.sku_code
        FROM spec_pdf_uploads u
        LEFT JOIN profiles p ON p.id = u.uploaded_by
        LEFT JOIN skus s ON s.id = u.sku_id
        WHERE u.id = $1;
        """,
        upload_uuid,
    )
    if not upload_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source PDF upload not found.")

    upload_data = dict(upload_row)

    # Fetch master specification record
    spec_row = None
    if upload_data.get("spec_id"):
        spec_row = await conn.fetchrow("SELECT * FROM specifications WHERE id = $1;", upload_data["spec_id"])
    if not spec_row:
        spec_row = await conn.fetchrow("SELECT * FROM specifications WHERE spec_no = $1 OR (spec_no || revision) = $1 LIMIT 1;", spec_no)

    spec_dict = dict(spec_row) if spec_row else {"spec_no": spec_no, "revision": "—", "title": upload_data.get("original_filename")}

    # Read structured JSON from storage
    json_path = upload_data.get("corrected_json_path") or upload_data.get("parsed_json_path")
    spec_data: Dict[str, Any] = {}
    if json_path:
        rel_path = json_path.split("/", 1)[1] if "/" in json_path and json_path.startswith("spec-parsed/") else json_path
        json_bytes = await get_file_from_storage("spec-parsed", rel_path)
        if json_bytes:
            try:
                spec_data = json.loads(json_bytes.decode("utf-8"))
            except Exception as exc:
                logger.warning(f"Could not parse structured spec json: {exc}")

    return templates.TemplateResponse(
        request=request,
        name="specifications/viewer.html",
        context={
            "user": user,
            "spec": spec_dict,
            "upload": upload_data,
            "spec_data": spec_data,
            "spec_json_str": json.dumps(spec_data, indent=2),
            "current_page": "specifications",
        },
    )
