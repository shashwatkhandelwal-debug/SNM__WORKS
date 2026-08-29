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
from services.campaign_image import generate_campaign_graphic
from services.post_generator import DefenceProductError, generate_post, generate_campaign_post
from services.publishers import publish_to_all_platforms
from services.storage import get_file_from_storage, upload_file_to_storage

logger = logging.getLogger("snm_works.marketing")
router = APIRouter(prefix="/marketing", tags=["marketing"])
templates = Jinja2Templates(directory="templates")

# In-memory storage for promotional campaigns
MEM_CAMPAIGNS: Dict[str, Dict[str, Any]] = {}


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
        try:
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

                    # 2. Fetch queued Campaigns
                    try:
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
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning(f"Failed to query queued items from database: {exc}")

    # Merge in-memory queued SKUs
    try:
        from routers.skus import MEM_SKUS
        for m_id, m_sku in MEM_SKUS.items():
            if m_sku.get("post_status") == "queued":
                if not any(s.get("id") == m_id or s.get("sku_code") == m_sku.get("sku_code") for s in queued_skus):
                    queued_skus.append(m_sku)
    except Exception:
        pass

    # Merge in-memory queued Campaigns
    for c_id, c_camp in MEM_CAMPAIGNS.items():
        if c_camp.get("post_status") == "queued":
            if not any(c.get("id") == c_id for c in queued_campaigns):
                queued_campaigns.append(c_camp)

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

    # Merge in-memory ready SKUs
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
            "now_str": now_str,
        }
    )


@router.post("/campaign/generate-image")
async def generate_campaign_image_endpoint(
    request: Request,
    occasion: str = Form("Independence Day 2026"),
    headline: str = Form("Proud to manufacture in Kanpur, India"),
    body: Optional[str] = Form(None),
):
    """
    Generates a branded Pillow PNG graphic for the promotional campaign
    and returns an inline HTMX preview snippet.
    """
    try:
        user = await get_authenticated_user(request)
        token = request.cookies.get("access_token")
    except HTTPException:
        return Response(status_code=status.HTTP_401_UNAUTHORIZED, content="Authentication required")

    png_bytes = generate_campaign_graphic(
        occasion=occasion,
        headline=headline,
        body=body,
    )

    temp_id = f"temp-{uuid.uuid4()}"
    photo_key = await upload_file_to_storage(
        bucket_id="campaign-images",
        destination_path=f"{temp_id}.png",
        file_bytes=png_bytes,
        content_type="image/png",
        user_token=token,
    )

    return HTMLResponse(
        f"""
        <div id="campaign-image-preview" style="border: 2px solid var(--snm-olive); border-radius: 4px; overflow: hidden; background: #000; margin-top: 1rem;">
          <img src="/marketing/campaign/{temp_id}/image-preview" alt="Generated Campaign Graphic" style="width: 100%; height: auto; display: block;">
          <div style="display: flex; justify-content: space-between; align-items: center; background: var(--snm-paper); padding: 0.5rem 0.75rem;">
            <span class="badge badge-pass" style="font-size: 0.75rem;">BRANDED GRAPHIC READY</span>
            <input type="hidden" name="image_temp_id" value="{temp_id}">
            <a href="/marketing/campaign/{temp_id}/image-preview" target="_blank" class="btn btn-outline btn-sm" style="font-size: 0.75rem;">
              View Full Size ↗
            </a>
          </div>
        </div>
        """
    )


@router.get("/campaign/{campaign_id}/image-preview")
async def get_campaign_image_preview(campaign_id: str):
    """
    Serves the campaign PNG graphic directly in browser.
    """
    file_bytes = await get_file_from_storage("campaign-images", f"{campaign_id}.png")
    if file_bytes:
        return Response(content=file_bytes, media_type="image/png")

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign graphic not found.")


@router.post("/campaign")
async def create_campaign(
    request: Request,
    occasion: str = Form(...),
    headline: str = Form(...),
    body: str = Form(...),
    featured_sku_id: Optional[str] = Form(None),
    platforms: List[str] = Form(...),
    scheduled_at: Optional[str] = Form(None),
    image_temp_id: Optional[str] = Form(None),
):
    """
    Creates and queues a promotional campaign post.
    Generates platform-specific captions and places into the marketing approval queue.
    """
    try:
        user = await get_authenticated_user(request)
        token = request.cookies.get("access_token")
    except HTTPException:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    clean_occasion = occasion.strip()
    clean_headline = headline.strip()[:100]
    clean_body = body.strip()[:500]
    campaign_id = str(uuid.uuid4())

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
        "platforms": platforms,
        "scheduled_at": scheduled_at or datetime.now().isoformat(),
        "image_path": image_path,
        "post_status": "queued",
        "created_at": datetime.now().isoformat(),
    }

    # Generate multi-platform captions
    post_draft = generate_campaign_post(campaign_data, featured_sku=featured_sku_dict)
    campaign_data["post_draft"] = post_draft

    # Store in memory
    MEM_CAMPAIGNS[campaign_id] = campaign_data

    # Store in PostgreSQL if table exists
    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, user["claims"])
                    await conn.execute(
                        """
                        INSERT INTO campaigns (id, occasion, headline, body, featured_sku_id, platforms, scheduled_at, post_status, post_draft)
                        VALUES ($1::uuid, $2, $3, $4, $5::uuid, $6, $7::timestamptz, $8, $9::jsonb)
                        """,
                        campaign_id,
                        clean_occasion,
                        clean_headline,
                        clean_body,
                        featured_sku_id.strip() if featured_sku_id else None,
                        platforms,
                        scheduled_at or datetime.now().isoformat(),
                        "queued",
                        json.dumps(post_draft),
                    )
        except Exception as exc:
            logger.warning(f"Could not persist campaign into database: {exc}")

    return RedirectResponse(url="/marketing/queue", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/approve/{item_id}")
async def approve_post(
    request: Request,
    item_id: str,
):
    """
    Approves a queued SKU or Campaign and triggers mock publishing to target platforms.
    Updates post_status to 'published', stores platform_results receipt, and timestamps.
    """
    try:
        user = await get_authenticated_user(request)
    except HTTPException:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    target_sku: Optional[Dict[str, Any]] = None
    target_campaign: Optional[Dict[str, Any]] = None
    post_draft: Optional[Dict[str, Any]] = None

    # Check if item is in MEM_CAMPAIGNS
    if item_id in MEM_CAMPAIGNS:
        target_campaign = MEM_CAMPAIGNS[item_id]
        post_draft = target_campaign.get("post_draft")

    # Check database for SKU or Campaign
    if database.pool is not None:
        async with database.pool.acquire() as conn:
            async with conn.transaction():
                await set_rls_claims(conn, user["claims"])
                # Check campaign
                try:
                    c_row = await conn.fetchrow("SELECT * FROM campaigns WHERE id::text = $1", item_id)
                    if c_row:
                        target_campaign = dict(c_row)
                        post_draft = target_campaign.get("post_draft")
                except Exception:
                    pass

                # Check SKU
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

    if target_campaign:
        if isinstance(post_draft, str):
            try:
                post_draft = json.loads(post_draft)
            except Exception:
                pass
        if not post_draft:
            post_draft = generate_campaign_post(target_campaign)

        # Publish to platforms
        platform_results = await publish_to_all_platforms(post_draft)
        target_campaign["post_status"] = "published"
        target_campaign["platform_results"] = platform_results

        if database.pool is not None:
            try:
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
                            json.dumps(platform_results),
                            item_id
                        )
            except Exception:
                pass

        is_htmx = request.headers.get("hx-request") == "true"
        if is_htmx:
            return HTMLResponse(
                f"""
                <div class="card alert alert-pass" id="camp-card-{item_id}" style="margin-bottom: 1.5rem; border-left: 5px solid var(--snm-pass);">
                  <div style="display: flex; align-items: center; justify-content: space-between;">
                    <div>
                      <strong>Campaign Successfully Published!</strong>
                      <div style="font-family: var(--font-mono); font-size: 0.8rem; margin-top: 0.25rem;">
                        "{target_campaign.get('headline')}" dispatched to all platforms.
                      </div>
                    </div>
                    <span class="badge badge-pass">PUBLISHED</span>
                  </div>
                </div>
                """
            )

        return RedirectResponse(url="/marketing/queue", status_code=status.HTTP_303_SEE_OTHER)

    # If SKU
    if not target_sku:
        from routers.skus import MEM_SKUS
        target_sku = MEM_SKUS.get(item_id)

    if target_sku:
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
    else:
        post_draft = {
            "sku_code": item_id,
            "title": f"Technical Textile Product {item_id}",
            "standard": "SNM Commercial Standard",
            "material": "High Tenacity Polyester",
        }

    # Dispatch to all 5 mock platforms
    platform_results = await publish_to_all_platforms(post_draft)

    if target_sku:
        target_sku["post_status"] = "published"
        target_sku["platform_results"] = platform_results

    # Update database record
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
                        json.dumps(platform_results),
                        item_id
                    )
        except Exception as exc:
            logger.warning(f"Could not persist post publication in database: {exc}")

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        return HTMLResponse(
            f"""
            <div class="card alert alert-pass" id="sku-card-{item_id}" style="margin-bottom: 1.5rem; border-left: 5px solid var(--snm-pass);">
              <div style="display: flex; align-items: center; justify-content: space-between;">
                <div>
                  <strong>Post Successfully Published!</strong>
                  <div style="font-family: var(--font-mono); font-size: 0.8rem; margin-top: 0.25rem;">
                    SKU {item_id} dispatched to LinkedIn, Instagram, Facebook, IndiaMart, and TradeIndia.
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
    Updates post_status to 'rejected' and stores the rejection reason.
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

    # Check Campaign in memory
    if item_id in MEM_CAMPAIGNS:
        MEM_CAMPAIGNS[item_id]["post_status"] = "rejected"
        MEM_CAMPAIGNS[item_id]["rejection_reason"] = reason.strip()

    # Check SKU in memory
    try:
        from routers.skus import MEM_SKUS
        if item_id in MEM_SKUS:
            MEM_SKUS[item_id]["post_status"] = "rejected"
            MEM_SKUS[item_id]["rejection_reason"] = reason.strip()
    except Exception:
        pass

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, user["claims"])
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
                    try:
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
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning(f"Could not persist post rejection in database: {exc}")

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        return HTMLResponse(
            f"""
            <div class="card alert alert-fail" id="sku-card-{item_id}" style="margin-bottom: 1.5rem; border-left: 5px solid var(--snm-fail);">
              <div style="display: flex; align-items: center; justify-content: space-between;">
                <div>
                  <strong>Post Draft Rejected</strong>
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
