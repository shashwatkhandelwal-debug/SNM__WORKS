from datetime import datetime
import json
import logging
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import database
from auth.jwt import decode_access_token
from auth.middleware import set_rls_claims
from services.ai_image import GeminiImageGenerationError, generate_hybrid_sku_image
from services.campaign_image import generate_campaign_graphic
from services.post_generator import DefenceProductError, generate_post, generate_campaign_post
from services.publishers import SUPPORTED_PLATFORMS, publish_post_to_platforms, publish_to_all_platforms
from services.storage import get_file_from_storage, upload_file_to_storage

logger = logging.getLogger("snm_works.marketing")
router = APIRouter(prefix="/marketing", tags=["marketing"])
templates = Jinja2Templates(directory="templates")


async def get_authenticated_user(request: Request) -> Dict[str, Any]:
    """
    Extracts and validates user JWT from cookie or authorization header.
    Redirects unauthenticated requests to login page.
    """
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    try:
        claims = decode_access_token(token)
        return {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "claims": claims,
        }
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )


@router.get("/queue", response_class=HTMLResponse)
async def marketing_queue_view(
    request: Request,
):
    """
    Displays the owner approval queue for SKUs and Campaigns in 'queued' post status.
    Requires authentication.
    """
    try:
        user = await get_authenticated_user(request)
    except HTTPException:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    queued_skus: List[Dict[str, Any]] = []
    queued_campaigns: List[Dict[str, Any]] = []

    if database.pool is not None:
        async with database.pool.acquire() as conn:
            async with conn.transaction():
                await set_rls_claims(conn, user["claims"])
                
                # 1. Fetch queued SKUs
                rows = await conn.fetch(
                    """
                    SELECT s.*, c.weave, c.width_mm, c.warp_ends, c.picks_per_cm,
                           c.warp_denier, c.weft_denier, c.warp_crimp, c.weft_crimp
                    FROM skus s
                    LEFT JOIN constructions c ON c.id = s.construction_id
                    WHERE s.post_status = 'queued'
                    ORDER BY s.created_on DESC
                    """
                )
                for r in rows:
                    row_dict = dict(r)
                    if isinstance(row_dict.get("post_draft"), str):
                        try:
                            row_dict["post_draft"] = json.loads(row_dict["post_draft"])
                        except Exception:
                            pass
                    if not row_dict.get("post_draft"):
                        try:
                            row_dict["post_draft"] = generate_post(row_dict, construction=row_dict)
                        except DefenceProductError:
                            continue
                    queued_skus.append(row_dict)

                # 2. Fetch queued Campaigns from PostgreSQL (No in-memory fallback)
                c_rows = await conn.fetch(
                    """
                    SELECT * FROM campaigns
                    WHERE post_status = 'queued'
                    ORDER BY scheduled_at DESC, created_at DESC
                    """
                )
                for cr in c_rows:
                    cr_dict = dict(cr)
                    if isinstance(cr_dict.get("post_draft"), str):
                        try:
                            cr_dict["post_draft"] = json.loads(cr_dict["post_draft"])
                        except Exception:
                            pass
                    queued_campaigns.append(cr_dict)

    # Merge in-memory queued SKUs for backward compatibility if any
    try:
        from routers.skus import MEM_SKUS
        for m_id, m_sku in MEM_SKUS.items():
            if m_sku.get("post_status") == "queued":
                if not any(s.get("id") == m_id or s.get("sku_code") == m_sku.get("sku_code") for s in queued_skus):
                    queued_skus.append(m_sku)
    except Exception:
        pass

    return templates.TemplateResponse(
        request=request,
        name="marketing/queue.html",
        context={
            "user": user,
            "queued_skus": queued_skus,
            "queued_campaigns": queued_campaigns,
        }
    )


@router.get("/campaign/new", response_class=HTMLResponse)
async def new_campaign_view(request: Request):
    """
    Renders the one-off promotional marketing campaign creator,
    pre-filled with the Independence Day 2026 template.
    """
    try:
        user = await get_authenticated_user(request)
    except HTTPException:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    ready_skus: List[Dict[str, Any]] = []

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, user["claims"])
                    rows = await conn.fetch(
                        """
                        SELECT id, sku_code, title, family, standard
                        FROM skus
                        WHERE status IN ('Ready', 'Published')
                        ORDER BY title ASC
                        """
                    )
                    ready_skus = [dict(r) for r in rows]
        except Exception:
            pass

    # Merge in-memory ready SKUs if any
    try:
        from routers.skus import MEM_SKUS
        for m_id, m_sku in MEM_SKUS.items():
            if m_sku.get("status") in ["Ready", "Published"]:
                if not any(s.get("id") == m_id or s.get("sku_code") == m_sku.get("sku_code") for s in ready_skus):
                    ready_skus.append(m_sku)
    except Exception:
        pass

    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M")

    return templates.TemplateResponse(
        request=request,
        name="marketing/campaign_new.html",
        context={
            "user": user,
            "ready_skus": ready_skus,
            "default_scheduled_at": now_str,
        }
    )


@router.post("/campaign/generate-preview", response_class=HTMLResponse)
async def generate_campaign_preview(
    request: Request,
    occasion: str = Form(...),
    headline: str = Form(...),
    body: str = Form(...),
    featured_sku_id: Optional[str] = Form(None),
):
    """
    HTMX live-preview endpoint for campaign creator.
    Generates preview captions for all 5 platforms and returns preview card.
    """
    try:
        user = await get_authenticated_user(request)
    except HTTPException:
        return HTMLResponse("<div class='alert alert-fail'>Authentication required</div>")

    clean_occasion = occasion.strip()[:80]
    clean_headline = headline.strip()[:100]
    clean_body = body.strip()[:500]

    featured_sku_dict: Optional[Dict[str, Any]] = None
    if featured_sku_id and featured_sku_id.strip():
        if database.pool is not None:
            try:
                async with database.pool.acquire() as conn:
                    async with conn.transaction():
                        await set_rls_claims(conn, user["claims"])
                        row = await conn.fetchrow("SELECT * FROM skus WHERE id::text = $1", featured_sku_id.strip())
                        if row:
                            featured_sku_dict = dict(row)
            except Exception:
                pass
        if not featured_sku_dict:
            try:
                from routers.skus import MEM_SKUS
                featured_sku_dict = MEM_SKUS.get(featured_sku_id.strip())
            except Exception:
                pass

        if featured_sku_dict:
            std = str(featured_sku_dict.get("standard") or "").strip().upper()
            code = str(featured_sku_dict.get("sku_code") or "").strip().upper()
            if std.startswith("MIL-") or "MIL-W-" in std or "MIL-SPEC" in std or "MIL-" in code:
                return HTMLResponse(
                    "<div class='alert alert-fail'><strong>Defence Product Blocked:</strong> Defence specification products cannot be featured in marketing campaigns.</div>",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

    campaign_data = {
        "occasion": clean_occasion,
        "headline": clean_headline,
        "body": clean_body,
        "featured_sku_id": featured_sku_id,
    }

    post_draft = generate_campaign_post(campaign_data, featured_sku=featured_sku_dict)

    return templates.TemplateResponse(
        request=request,
        name="marketing/_campaign_preview.html",
        context={
            "occasion": clean_occasion,
            "headline": clean_headline,
            "body": clean_body,
            "featured_sku": featured_sku_dict,
            "post_draft": post_draft,
        }
    )


@router.get("/campaign/image-preview", response_class=Response)
async def preview_campaign_image(
    occasion: str = "Independence Day 2026",
    headline: str = "Proudly Weaving Defence-Grade Narrow Fabrics for India",
    body: str = "Swadeshi Niwar Mills salutes the armed forces with MIL-spec technical webbing.",
):
    """
    Returns dynamically generated banner PNG for live visual preview in form.
    """
    graphic_bytes = generate_campaign_graphic(
        occasion=occasion.strip()[:80],
        headline=headline.strip()[:100],
        body=body.strip()[:500],
    )
    return Response(content=graphic_bytes, media_type="image/png")


@router.post("/campaign/create")
async def create_campaign(
    request: Request,
    occasion: str = Form(...),
    headline: str = Form(...),
    body: str = Form(...),
    featured_sku_id: Optional[str] = Form(None),
    scheduled_at: Optional[str] = Form(None),
    platforms: List[str] = Form(["linkedin", "instagram", "facebook", "indiamart", "tradeindia"]),
):
    """
    Creates a new promotional marketing campaign, generates banner graphic,
    builds platform-specific captions, and queues for owner approval.
    Persists directly to PostgreSQL campaigns table.
    Blocks defence specification products from featured campaigns.
    """
    token = request.cookies.get("access_token")
    user = await get_authenticated_user(request)

    clean_occasion = occasion.strip()[:80]
    clean_headline = headline.strip()[:100]
    clean_body = body.strip()[:500]
    campaign_id = str(uuid.uuid4())

    clean_platforms = [p.strip().lower() for p in platforms if p and p.strip().lower() in SUPPORTED_PLATFORMS]
    if not clean_platforms:
        clean_platforms = list(SUPPORTED_PLATFORMS)

    featured_sku_dict: Optional[Dict[str, Any]] = None
    if featured_sku_id and featured_sku_id.strip():
        if database.pool is not None:
            try:
                async with database.pool.acquire() as conn:
                    async with conn.transaction():
                        await set_rls_claims(conn, user["claims"])
                        row = await conn.fetchrow("SELECT * FROM skus WHERE id::text = $1", featured_sku_id.strip())
                        if row:
                            featured_sku_dict = dict(row)
            except Exception:
                pass
        if not featured_sku_dict:
            try:
                from routers.skus import MEM_SKUS
                featured_sku_dict = MEM_SKUS.get(featured_sku_id.strip())
            except Exception:
                pass

        if featured_sku_dict:
            std = str(featured_sku_dict.get("standard") or "").strip().upper()
            code = str(featured_sku_dict.get("sku_code") or "").strip().upper()
            if std.startswith("MIL-") or "MIL-W-" in std or "MIL-SPEC" in std or "MIL-" in code:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Defence specification SKUs cannot be featured in marketing campaigns.",
                )

    # Ensure graphic exists or generate one
    graphic_bytes = generate_campaign_graphic(occasion=clean_occasion, headline=clean_headline, body=clean_body)
    image_path = await upload_file_to_storage(
        bucket_id="campaign-images",
        destination_path=f"{campaign_id}.png",
        file_bytes=graphic_bytes,
        content_type="image/png",
        user_token=token,
    )

    campaign_data = {
        "id": campaign_id,
        "occasion": clean_occasion,
        "headline": clean_headline,
        "body": clean_body,
        "featured_sku_id": featured_sku_id.strip() if featured_sku_id else None,
        "platforms": clean_platforms,
        "scheduled_at": scheduled_at or datetime.now().isoformat(),
        "image_path": image_path,
        "post_status": "queued",
        "created_at": datetime.now().isoformat(),
    }

    # Generate multi-platform captions
    post_draft = generate_campaign_post(campaign_data, featured_sku=featured_sku_dict)
    campaign_data["post_draft"] = post_draft

    # Parse scheduled_at datetime for asyncpg timestamptz
    sched_dt: datetime
    if scheduled_at and scheduled_at.strip():
        try:
            sched_dt = datetime.fromisoformat(scheduled_at.strip())
        except Exception:
            sched_dt = datetime.now()
    else:
        sched_dt = datetime.now()

    # Store directly in PostgreSQL campaigns table (No in-memory fallback)
    if database.pool is not None:
        async with database.pool.acquire() as conn:
            async with conn.transaction():
                await set_rls_claims(conn, user["claims"])
                await conn.execute(
                    """
                    INSERT INTO campaigns (id, occasion, headline, body, featured_sku_id, platforms, scheduled_at, post_status, post_draft)
                    VALUES ($1::uuid, $2, $3, $4, $5::uuid, $6, $7, $8, $9::jsonb)
                    """,
                    uuid.UUID(campaign_id),
                    clean_occasion,
                    clean_headline,
                    clean_body,
                    uuid.UUID(featured_sku_id.strip()) if featured_sku_id else None,
                    clean_platforms,
                    sched_dt,
                    "queued",
                    json.dumps(post_draft, ensure_ascii=False),
                )

    return RedirectResponse(url="/marketing/queue", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sku/{sku_id}/generate-ai-image")
async def generate_sku_ai_image(
    request: Request,
    sku_id: str,
):
    """
    Generates a hybrid AI marketing hero image (Gemini Nano Banana 2 + Pillow branding overlay)
    for a given SKU, uploads to storage, updates photo_path in PostgreSQL/memory,
    and returns an updated HTML snippet or redirects.
    Fails LOUDLY on API errors, rate limits, or missing credentials.
    """
    try:
        user = await get_authenticated_user(request)
        token = request.cookies.get("access_token")
    except HTTPException:
        return HTMLResponse("<div class='alert alert-fail'>Authentication required</div>", status_code=401)

    sku_dict: Optional[Dict[str, Any]] = None
    construction_dict: Optional[Dict[str, Any]] = None

    # Fetch from PostgreSQL
    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, user["claims"])
                    row = await conn.fetchrow(
                        """
                        SELECT s.*, c.weave, c.width_mm, c.warp_ends, c.warp_denier,
                               c.warp_tenacity, c.efficiency, c.picks_per_cm, c.weft_denier
                        FROM skus s
                        LEFT JOIN constructions c ON c.id = s.construction_id
                        WHERE s.id::text = $1 OR s.sku_code = $1
                        """,
                        sku_id
                    )
                    if row:
                        sku_dict = dict(row)
                        if sku_dict.get("construction_id"):
                            construction_dict = sku_dict
        except Exception as exc:
            logger.warning(f"Error fetching SKU {sku_id} from db: {exc}")

    # Fallback to MEM_SKUS
    if not sku_dict:
        try:
            from routers.skus import MEM_SKUS
            sku_dict = MEM_SKUS.get(sku_id)
            if not sku_dict:
                for s in MEM_SKUS.values():
                    if s.get("sku_code") == sku_id:
                        sku_dict = s
                        break
        except Exception:
            pass

    if not sku_dict:
        raise HTTPException(status_code=404, detail="SKU not found.")

    actual_sku_id = str(sku_dict.get("id") or sku_id)

    # Generate hybrid AI image (Stage 1 AI Hero + Stage 2 Pillow Overlay)
    try:
        branded_jpeg_bytes = await generate_hybrid_sku_image(
            sku_data=sku_dict,
            construction=construction_dict,
        )
    except GeminiImageGenerationError as err:
        logger.error(f"Gemini image generation failed for SKU {actual_sku_id}: {err.message}")
        is_htmx = request.headers.get("hx-request") == "true"
        if is_htmx:
            return HTMLResponse(
                f"<div class='alert alert-fail' style='margin-bottom:0.5rem;'><strong>AI Image Generation Failed:</strong> {err.message}</div>",
                status_code=err.status_code if err.status_code in (400, 429, 502, 504) else 500,
            )
        raise HTTPException(status_code=err.status_code if err.status_code in (400, 429, 502, 504) else 500, detail=err.message)

    # Save to storage (One-time save)
    storage_path = f"{actual_sku_id}.jpg"
    photo_key = await upload_file_to_storage(
        bucket_id="sku-images",
        destination_path=storage_path,
        file_bytes=branded_jpeg_bytes,
        content_type="image/jpeg",
        user_token=token,
    )

    sku_dict["photo_path"] = photo_key
    sku_dict["photo_at"] = datetime.now().isoformat()

    # Update database
    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, user["claims"])
                    await conn.execute(
                        """
                        UPDATE skus
                        SET photo_path = $1,
                            photo_at = now()
                        WHERE id::text = $2
                        """,
                        photo_key,
                        actual_sku_id,
                    )
        except Exception as exc:
            logger.warning(f"Failed to update photo_path in database: {exc}")

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        return HTMLResponse(
            f"""
            <div class="alert alert-pass" style="margin-bottom: 0.5rem;">
                ✓ AI Hero Image Generated & Saved Successfully
            </div>
            <img src="/storage/sku-images/{photo_key}" alt="AI Branded Webbing" style="width: 100%; border-radius: 4px; border: 1px solid var(--snm-line); margin-bottom: 0.5rem;" />
            """
        )

    return RedirectResponse(url="/marketing/queue", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/approve/{item_id}")
async def approve_post(
    request: Request,
    item_id: str,
):
    """
    Approves a queued SKU or Campaign and triggers mock publishing to target platforms.
    Enforces state machine: only items in 'queued' status can be approved.
    Enforces defence sanitization: MIL-spec SKUs or campaigns featuring MIL-spec SKUs are blocked.
    Dispatches strictly to selected platforms.
    Updates post_status to 'published', stores platform_results receipt, and timestamps.
    """
    try:
        user = await get_authenticated_user(request)
    except HTTPException:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    target_sku: Optional[Dict[str, Any]] = None
    target_campaign: Optional[Dict[str, Any]] = None
    post_draft: Optional[Dict[str, Any]] = None

    # Check database for Campaign or SKU
    if database.pool is not None:
        async with database.pool.acquire() as conn:
            async with conn.transaction():
                await set_rls_claims(conn, user["claims"])
                # Check campaign
                c_row = await conn.fetchrow("SELECT * FROM campaigns WHERE id::text = $1", item_id)
                if c_row:
                    target_campaign = dict(c_row)
                    post_draft = target_campaign.get("post_draft")

                # Check SKU if not campaign
                if not target_campaign:
                    row = await conn.fetchrow(
                        """
                        SELECT s.*, c.weave, c.width_mm, c.warp_ends, c.picks_per_cm,
                               c.warp_denier, c.weft_denier, c.warp_crimp, c.weft_crimp
                        FROM skus s
                        LEFT JOIN constructions c ON c.id = s.construction_id
                        WHERE s.id::text = $1
                        """,
                        item_id
                    )
                    if row:
                        target_sku = dict(row)

    # Fallback to MEM_SKUS if not found
    if not target_campaign and not target_sku:
        try:
            from routers.skus import MEM_SKUS
            target_sku = MEM_SKUS.get(item_id)
        except Exception:
            pass

    if not target_campaign and not target_sku:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Marketing item not found.")

    # 1. Handle Campaign Approval
    if target_campaign:
        current_status = target_campaign.get("post_status")
        if current_status != "queued":
            msg = f"Campaign is currently in '{current_status}' status and cannot be approved."
            if request.headers.get("hx-request") == "true":
                return HTMLResponse(
                    f"""<div class="card alert alert-fail" id="camp-card-{item_id}" style="margin-bottom: 1.5rem; border-left: 5px solid var(--snm-fail);">
                        <strong>Action Blocked:</strong> {msg}
                    </div>""",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

        # Defence validation if featured SKU is linked
        if target_campaign.get("featured_sku_id"):
            feat_sku = None
            if database.pool is not None:
                async with database.pool.acquire() as conn:
                    async with conn.transaction():
                        await set_rls_claims(conn, user["claims"])
                        feat_row = await conn.fetchrow("SELECT standard, sku_code FROM skus WHERE id = $1", target_campaign["featured_sku_id"])
                        if feat_row:
                            feat_sku = dict(feat_row)
            if feat_sku:
                std = str(feat_sku.get("standard") or "").strip().upper()
                code = str(feat_sku.get("sku_code") or "").strip().upper()
                if std.startswith("MIL-") or "MIL-W-" in std or "MIL-SPEC" in std or "MIL-" in code:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Campaign features a defence specification SKU and cannot be published.",
                    )

        if isinstance(post_draft, str):
            try:
                post_draft = json.loads(post_draft)
            except Exception:
                pass
        if not post_draft:
            post_draft = generate_campaign_post(target_campaign)

        target_platforms = target_campaign.get("platforms")
        platform_results = await publish_post_to_platforms(post_draft, platforms=target_platforms)
        target_campaign["post_status"] = "published"
        target_campaign["platform_results"] = platform_results

        if database.pool is not None:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, user["claims"])
                    await conn.execute(
                        """
                        UPDATE campaigns
                        SET post_status = 'published',
                            platform_results = $1::jsonb,
                            post_approved_at = now(),
                            post_published_at = now()
                        WHERE id::text = $2
                        """,
                        json.dumps(platform_results, ensure_ascii=False),
                        item_id
                    )

        is_htmx = request.headers.get("hx-request") == "true"
        if is_htmx:
            plat_names = ", ".join([p.capitalize() for p in platform_results.keys()])
            return HTMLResponse(
                f"""
                <div class="card alert alert-pass" id="camp-card-{item_id}" style="margin-bottom: 1.5rem; border-left: 5px solid var(--snm-pass);">
                  <div style="display: flex; align-items: center; justify-content: space-between;">
                    <div>
                      <strong>Campaign Successfully Published!</strong>
                      <div style="font-family: var(--font-mono); font-size: 0.8rem; margin-top: 0.25rem;">
                        "{target_campaign.get('headline')}" dispatched to {plat_names}.
                      </div>
                    </div>
                    <span class="badge badge-pass">PUBLISHED</span>
                  </div>
                </div>
                """
            )

        return RedirectResponse(url="/marketing/queue", status_code=status.HTTP_303_SEE_OTHER)

    # 2. Handle SKU Approval
    current_status = target_sku.get("post_status")
    if current_status != "queued":
        msg = f"SKU is currently in '{current_status}' status and cannot be approved."
        if request.headers.get("hx-request") == "true":
            return HTMLResponse(
                f"""<div class="card alert alert-fail" id="sku-card-{item_id}" style="margin-bottom: 1.5rem; border-left: 5px solid var(--snm-fail);">
                    <strong>Action Blocked:</strong> {msg}
                </div>""",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

    standard = str(target_sku.get("standard") or "")
    if standard.upper().startswith("MIL-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Defence specification SKUs cannot be published.",
        )

    post_draft = target_sku.get("post_draft")
    if isinstance(post_draft, str):
        try:
            post_draft = json.loads(post_draft)
        except Exception:
            post_draft = None

    if not post_draft:
        post_draft = generate_post(target_sku, construction=target_sku)

    target_platforms = target_sku.get("platforms")
    platform_results = await publish_post_to_platforms(post_draft, platforms=target_platforms)

    target_sku["post_status"] = "published"
    target_sku["platform_results"] = platform_results

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, user["claims"])
                    await conn.execute(
                        """
                        UPDATE skus
                        SET post_status = 'published',
                            platform_results = $1::jsonb,
                            post_approved_at = now(),
                            post_published_at = now()
                        WHERE id::text = $2
                        """,
                        json.dumps(platform_results, ensure_ascii=False),
                        item_id
                    )
        except Exception as exc:
            logger.warning(f"Could not persist post publication in database: {exc}")

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        sku_code = target_sku.get("sku_code") or item_id
        plat_names = ", ".join([p.capitalize() for p in platform_results.keys()])
        return HTMLResponse(
            f"""
            <div class="card alert alert-pass" id="sku-card-{item_id}" style="margin-bottom: 1.5rem; border-left: 5px solid var(--snm-pass);">
              <div style="display: flex; align-items: center; justify-content: space-between;">
                <div>
                  <strong>Post Successfully Published!</strong>
                  <div style="font-family: var(--font-mono); font-size: 0.8rem; margin-top: 0.25rem;">
                    SKU {sku_code} dispatched to {plat_names}.
                  </div>
                </div>
                <span class="badge badge-pass">PUBLISHED</span>
              </div>
            </div>
            """
        )

    return RedirectResponse(url="/marketing/queue", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/reject/{item_id}")
async def reject_post(
    request: Request,
    item_id: str,
    reason: str = Form(...),
):
    """
    Rejects a queued post with a mandatory reason (minimum 10 characters).
    Enforces state machine: only items in 'queued' status can be rejected.
    Returns correct HTMX replacement target (camp-card- for campaigns, sku-card- for SKUs).
    """
    try:
        user = await get_authenticated_user(request)
    except HTTPException:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    if len(reason.strip()) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rejection reason must be at least 10 characters.",
        )

    is_campaign = False
    is_sku = False
    current_status = None

    if database.pool is not None:
        async with database.pool.acquire() as conn:
            async with conn.transaction():
                await set_rls_claims(conn, user["claims"])
                # Check campaign
                c_row = await conn.fetchrow("SELECT post_status FROM campaigns WHERE id::text = $1", item_id)
                if c_row:
                    is_campaign = True
                    current_status = c_row["post_status"]
                else:
                    s_row = await conn.fetchrow("SELECT post_status FROM skus WHERE id::text = $1", item_id)
                    if s_row:
                        is_sku = True
                        current_status = s_row["post_status"]

    if not is_campaign and not is_sku:
        try:
            from routers.skus import MEM_SKUS
            if item_id in MEM_SKUS:
                is_sku = True
                current_status = MEM_SKUS[item_id].get("post_status")
        except Exception:
            pass

    if not is_campaign and not is_sku:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Marketing item not found.")

    card_dom_id = f"camp-card-{item_id}" if is_campaign else f"sku-card-{item_id}"

    if current_status != "queued":
        msg = f"Item is currently in '{current_status}' status and cannot be rejected."
        if request.headers.get("hx-request") == "true":
            return HTMLResponse(
                f"""<div class="card alert alert-fail" id="{card_dom_id}" style="margin-bottom: 1.5rem; border-left: 5px solid var(--snm-fail);">
                    <strong>Action Blocked:</strong> {msg}
                </div>""",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

    # Apply rejection update
    if is_sku:
        try:
            from routers.skus import MEM_SKUS
            if item_id in MEM_SKUS:
                MEM_SKUS[item_id]["post_status"] = "rejected"
                MEM_SKUS[item_id]["rejection_reason"] = reason.strip()
        except Exception:
            pass

    if database.pool is not None:
        async with database.pool.acquire() as conn:
            async with conn.transaction():
                await set_rls_claims(conn, user["claims"])
                if is_campaign:
                    await conn.execute(
                        """
                        UPDATE campaigns
                        SET post_status = 'rejected',
                            rejection_reason = $1
                        WHERE id::text = $2
                        """,
                        reason.strip(),
                        item_id
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE skus
                        SET post_status = 'rejected',
                            rejection_reason = $1
                        WHERE id::text = $2
                        """,
                        reason.strip(),
                        item_id
                    )

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        return HTMLResponse(
            f"""
            <div class="card alert alert-fail" id="{card_dom_id}" style="margin-bottom: 1.5rem; border-left: 5px solid var(--snm-fail);">
              <div style="display: flex; align-items: center; justify-content: space-between;">
                <div>
                  <strong>{'Campaign' if is_campaign else 'Post'} Draft Rejected</strong>
                  <div style="font-family: var(--font-mono); font-size: 0.8rem; margin-top: 0.25rem;">
                    Reason: {reason.strip()}
                  </div>
                </div>
                <span class="badge badge-fail">REJECTED</span>
              </div>
            </div>
            """
        )

    return RedirectResponse(url="/marketing/queue", status_code=status.HTTP_303_SEE_OTHER)
