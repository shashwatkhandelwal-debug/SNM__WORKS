from datetime import datetime
import json
import logging
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
    HTTP_503_SERVICE_UNAVAILABLE,
)
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import database
from database import get_db
from auth.dependencies import current_user, require
from auth.jwt import decode_access_token
from auth.middleware import set_rls_claims
from services.image_processor import brand_product_image
from services.post_generator import DefenceProductError, check_and_queue, generate
from services.storage import get_file_from_storage, upload_file_to_storage

logger = logging.getLogger("snm_works.skus")
router = APIRouter(prefix="/skus", tags=["skus"])
templates = Jinja2Templates(directory="templates")

# In-memory storage cache to ensure local dev reliability
MEM_SKUS: Dict[str, Dict[str, Any]] = {}


async def get_user_claims(request: Request) -> Dict[str, Any]:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if not token:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return decode_access_token(token)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def list_skus(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("skus", "read")),
):
    user_info = {
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }
    skus_list: List[Dict[str, Any]] = []

    try:
        rows = await conn.fetch(
            """
            SELECT s.*, c.weave, c.width_mm
            FROM skus s
            LEFT JOIN constructions c ON c.id = s.construction_id
            ORDER BY s.created_on DESC, s.sku_code ASC
            """
        )
        for r in rows:
            row_dict = dict(r)
            if isinstance(row_dict.get("post_draft"), str):
                try:
                    row_dict["post_draft"] = json.loads(row_dict["post_draft"])
                except Exception:
                    pass
            skus_list.append(row_dict)
    except Exception as exc:
        logger.warning(f"Could not load SKUs from database: {exc}")

    # Merge in-memory SKUs
    for m_id, m_sku in MEM_SKUS.items():
        if not any(s.get("id") == m_id or s.get("sku_code") == m_sku.get("sku_code") for s in skus_list):
            skus_list.insert(0, m_sku)

    return templates.TemplateResponse(
        request=request,
        name="skus/list.html",
        context={
            "user": user_info,
            "skus": skus_list,
        }
    )


@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_sku(
    request: Request,
    sku_code: str = Form(...),
    family: str = Form(...),
    title: str = Form(...),
    standard: Optional[str] = Form(None),
    material: Optional[str] = Form(None),
    blurb: Optional[str] = Form(None),
    catalogue_visible: Optional[bool] = Form(False),
    status: str = Form("Draft"),
    user: Dict[str, Any] = Depends(require("skus", "create")),
):
    """
    Creates a new SKU record.
    Saves the SKU, then invokes check_and_queue() to evaluate marketing triggers.
    """
    claims = user.get("claims") or {}

    clean_sku_code = sku_code.strip()
    clean_family = family.strip()
    clean_title = title.strip()
    clean_standard = standard.strip() if standard else None
    clean_material = material.strip() if material else None
    clean_blurb = blurb.strip() if blurb else None
    is_catalogue_visible = bool(catalogue_visible)
    sku_status = status.strip().capitalize()

    new_sku_id = str(uuid.uuid4())
    post_status = "none"

    sku_dict = {
        "id": new_sku_id,
        "sku_code": clean_sku_code,
        "family": clean_family,
        "title": clean_title,
        "standard": clean_standard,
        "material": clean_material,
        "blurb": clean_blurb,
        "status": sku_status,
        "catalogue_visible": is_catalogue_visible,
        "photo_path": None,
        "post_status": post_status,
        "post_draft": None,
        "created_on": datetime.now().isoformat(),
    }

    # Save in memory
    MEM_SKUS[new_sku_id] = sku_dict

    # Check and queue trigger (if photo is present and all 4 conditions met)
    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    await conn.execute(
                        """
                        INSERT INTO skus (id, sku_code, family, title, standard, material, blurb, status, catalogue_visible, post_status)
                        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        """,
                        new_sku_id,
                        clean_sku_code,
                        clean_family,
                        clean_title,
                        clean_standard,
                        clean_material,
                        clean_blurb,
                        sku_status,
                        is_catalogue_visible,
                        post_status,
                    )
                    await check_and_queue(sku_dict, conn=conn)
        except Exception as exc:
            logger.warning(f"Could not insert SKU in database: {exc}")
            await check_and_queue(sku_dict, conn=None)
    else:
        await check_and_queue(sku_dict, conn=None)

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        response = Response(status_code=HTTP_200_OK)
        response.headers["HX-Redirect"] = f"/skus/{new_sku_id}"
        return response

    return RedirectResponse(url=f"/skus/{new_sku_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/{sku_id}/upload-image")
@router.post("/{sku_id}/upload-photo")
async def upload_sku_photo(
    request: Request,
    sku_id: str,
    image_file: UploadFile = File(...),
):
    """
    Accepts raw product photo (max 10MB, jpg/png/webp), automatically brands it
    with top and bottom olive banners, saves to Supabase Storage, and evaluates check_and_queue().
    """
    try:
        claims = await get_user_claims(request)
        token = request.cookies.get("access_token")
    except HTTPException:
        return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

    raw_bytes = await image_file.read()
    if not raw_bytes:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")

    if len(raw_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="File size exceeds 10MB limit.")

    # 1. Fetch SKU data
    sku_data: Optional[Dict[str, Any]] = None
    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    row = await conn.fetchrow("SELECT * FROM skus WHERE id::text = $1 OR sku_code = $1", sku_id)
                    if row:
                        sku_data = dict(row)
        except Exception:
            pass

    if not sku_data:
        sku_data = MEM_SKUS.get(sku_id)
        if not sku_data:
            for s in MEM_SKUS.values():
                if s.get("sku_code") == sku_id:
                    sku_data = s
                    break

    if not sku_data:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="SKU not found.")

    actual_sku_id = str(sku_data.get("id") or sku_id)

    # 2. Automatically Brand Photo with Pillow
    try:
        branded_bytes = brand_product_image(
            image_bytes=raw_bytes,
            sku_code=sku_data.get("sku_code") or "SNM-PROD",
            standard=sku_data.get("standard") or "",
            material=sku_data.get("material") or "",
            breaking_strength=str(sku_data.get("breaking_strength") or "Contact us"),
            family=sku_data.get("family") or "Narrow woven",
        )
    except Exception as e:
        logger.warning(f"Failed to process/brand image for SKU {actual_sku_id}: {e}")
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Invalid image file or format: {e}",
        )

    # 3. Store to Supabase Storage & local cache
    storage_path = f"{actual_sku_id}.jpg"
    photo_key = await upload_file_to_storage(
        bucket_id="sku-images",
        destination_path=storage_path,
        file_bytes=branded_bytes,
        content_type="image/jpeg",
        user_token=token,
    )

    sku_data["photo_path"] = photo_key
    sku_data["photo_at"] = datetime.now().isoformat()

    # 4. Save photo_path in database & run check_and_queue
    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    await conn.execute(
                        """
                        UPDATE skus
                        SET photo_path = $1,
                            photo_at = now()
                        WHERE id::text = $2
                        """,
                        photo_key,
                        actual_sku_id
                    )
                    await check_and_queue(sku_data, conn=conn)
        except Exception as exc:
            logger.warning(f"Failed to update photo_path in database: {exc}")
            await check_and_queue(sku_data, conn=None)
    else:
        await check_and_queue(sku_data, conn=None)

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        queued_badge = ""
        if sku_data.get("post_status") == "queued":
            queued_badge = """
            <div class="alert alert-pass" style="margin-top: 0.75rem; padding: 0.5rem 0.75rem; font-size: 0.85rem;">
              [x] Photo verified & branded! Marketing draft automatically queued for approval.
            </div>
            """
        return HTMLResponse(
            f"""
            <div id="sku-photo-box">
              <div style="border: 2px solid var(--snm-olive); border-radius: 4px; overflow: hidden; background: var(--snm-machine-black);">
                <img src="/skus/{actual_sku_id}/image-preview?t={int(datetime.now().timestamp())}" alt="Branded Product Visual" style="width: 100%; height: auto; display: block;">
              </div>
              <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 0.5rem;">
                <span class="badge badge-pass" style="font-size: 0.75rem;">BRANDED & STORED</span>
                <a href="/skus/{actual_sku_id}/image-preview" target="_blank" class="btn btn-outline btn-sm" style="font-size: 0.75rem;">
                  Open Full Resolution ↗
                </a>
              </div>
              {queued_badge}
            </div>
            """
        )

    return RedirectResponse(url=f"/skus/{actual_sku_id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/{sku_id}/image-preview")
async def get_sku_image_preview(
    sku_id: str,
):
    """
    Returns the branded product JPEG directly in browser so the owner can inspect it.
    """
    # 1. Fetch from storage
    file_bytes = await get_file_from_storage("sku-images", f"{sku_id}.jpg")
    if file_bytes:
        return Response(content=file_bytes, media_type="image/jpeg")

    # 2. Check if sku_code was passed instead of uuid
    for s in MEM_SKUS.values():
        if s.get("sku_code") == sku_id or s.get("id") == sku_id:
            actual_id = s.get("id")
            fb = await get_file_from_storage("sku-images", f"{actual_id}.jpg")
            if fb:
                return Response(content=fb, media_type="image/jpeg")

    raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Branded product image not found.")


@router.get("/{sku_id}", response_class=HTMLResponse)
async def sku_detail_view(
    request: Request,
    sku_id: str,
):
    """
    Renders detailed SKU specifications, product photo branding section,
    generated post draft if queued, and a red Defence badge if the standard is a defence standard.
    """
    try:
        claims = await get_user_claims(request)
        user_info = {
            "email": claims.get("email"),
            "full_name": claims.get("user_metadata", {}).get("full_name") or claims.get("email"),
        }
    except HTTPException:
        return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

    sku_data: Optional[Dict[str, Any]] = None
    linked_specs: List[Dict[str, Any]] = []
    pdf_uploads: List[Dict[str, Any]] = []

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    row = await conn.fetchrow(
                        """
                        SELECT s.*, c.weave, c.width_mm, c.warp_ends, c.picks_per_cm,
                               c.warp_denier, c.weft_denier, c.warp_crimp, c.weft_crimp
                        FROM skus s
                        LEFT JOIN constructions c ON c.id = s.construction_id
                        WHERE s.id::text = $1 OR s.sku_code = $1
                        """,
                        sku_id
                    )
                    if row:
                        sku_data = dict(row)
                        actual_sku_uuid = row["id"]

                        # Fetch linked specifications via junction table
                        try:
                            spec_rows = await conn.fetch(
                                """
                                SELECT ss.*, s.spec_no, s.revision, s.title AS spec_title, s.issuing_body,
                                       v.designation AS variant_designation, v.class AS variant_class, v.status AS variant_status
                                FROM sku_specifications ss
                                JOIN specifications s ON s.id = ss.spec_id
                                LEFT JOIN spec_variants v ON v.id = ss.variant_id
                                WHERE ss.sku_id = $1
                                ORDER BY ss.is_primary DESC, s.spec_no ASC;
                                """,
                                actual_sku_uuid
                            )
                            linked_specs = [dict(r) for r in spec_rows]
                        except Exception as s_exc:
                            logger.warning(f"Could not fetch linked specifications: {s_exc}")

                        # Fetch spec PDF uploads
                        try:
                            upload_rows = await conn.fetch(
                                """
                                SELECT u.*, p.full_name AS uploader_name
                                FROM spec_pdf_uploads u
                                LEFT JOIN profiles p ON p.id = u.uploaded_by
                                WHERE u.sku_id = $1
                                ORDER BY u.uploaded_at DESC;
                                """,
                                actual_sku_uuid
                            )
                            pdf_uploads = [dict(r) for r in upload_rows]
                        except Exception as u_exc:
                            logger.warning(f"Could not fetch spec uploads: {u_exc}")
        except Exception as exc:
            logger.warning(f"Could not fetch SKU detail: {exc}")

    # Fallback to in-memory store
    if not sku_data:
        sku_data = MEM_SKUS.get(sku_id)
        if not sku_data:
            for s in MEM_SKUS.values():
                if s.get("sku_code") == sku_id:
                    sku_data = s
                    break

    if not sku_data:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="SKU not found")

    # Deserialize post_draft if stored as string
    if isinstance(sku_data.get("post_draft"), str):
        try:
            sku_data["post_draft"] = json.loads(sku_data["post_draft"])
        except Exception:
            pass

    # Check if standard is defence by checking DefenceProductError
    is_defence = False
    try:
        generate({"standard": sku_data.get("standard") or ""})
    except DefenceProductError:
        is_defence = True

    return templates.TemplateResponse(
        request=request,
        name="skus/detail.html",
        context={
            "user": user_info,
            "sku": sku_data,
            "draft": sku_data.get("post_draft"),
            "is_defence": is_defence,
            "linked_specs": linked_specs,
            "pdf_uploads": pdf_uploads,
        }
    )
