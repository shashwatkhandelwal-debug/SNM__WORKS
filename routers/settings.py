from datetime import datetime
import json
import logging
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import database
from auth.jwt import decode_access_token
from auth.middleware import set_rls_claims
from services.crypto import encrypt_token, decrypt_token
from services.linkedin_publisher import MEM_PLATFORM_CONNECTIONS

logger = logging.getLogger("snm_works.settings")
router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="templates")


async def get_user_claims(request: Request) -> Dict[str, Any]:
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

    return decode_access_token(token)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def settings_dashboard_view(request: Request):
    """
    Renders the platform connection settings page.
    Shows real-time connection status for LinkedIn (OAuth),
    and API key configuration forms for IndiaMart and TradeIndia.
    """
    try:
        claims = await get_user_claims(request)
        user_info = {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "full_name": claims.get("user_metadata", {}).get("full_name") or claims.get("email"),
        }
    except HTTPException:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    connections: Dict[str, Dict[str, Any]] = {}

    # 1. Fetch from PostgreSQL
    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    rows = await conn.fetch("SELECT * FROM platform_connections WHERE user_id = $1::uuid", claims.get("sub"))
                    for r in rows:
                        row_dict = dict(r)
                        connections[row_dict["platform"]] = row_dict
        except Exception as exc:
            logger.warning(f"Could not load platform connections from database: {exc}")

    # 2. Merge in-memory fallback
    for p, c in MEM_PLATFORM_CONNECTIONS.items():
        if p not in connections:
            connections[p] = c

    # Format platform status cards
    platforms_status = {
        "linkedin": {
            "name": "LinkedIn (Company Page)",
            "type": "oauth",
            "connected": "linkedin" in connections and connections["linkedin"].get("is_active", True),
            "account_name": connections.get("linkedin", {}).get("account_name") or "Swadeshi Niwar Mills",
            "account_id": connections.get("linkedin", {}).get("account_id") or "Company Page",
            "expires_at": connections.get("linkedin", {}).get("token_expires_at"),
            "scopes": connections.get("linkedin", {}).get("scopes") or ["w_organization_social", "r_organization_social"],
        },
        "instagram": {
            "name": "Instagram Business",
            "type": "oauth",
            "connected": False,
            "status_text": "Meta Graph API (Requires Facebook Page Link)",
        },
        "facebook": {
            "name": "Facebook Company Page",
            "type": "oauth",
            "connected": False,
            "status_text": "Meta Graph API (Pending App Secret)",
        },
        "indiamart": {
            "name": "IndiaMart Portal Integration",
            "type": "apikey",
            "configured": "indiamart" in connections,
            "has_key": bool(connections.get("indiamart", {}).get("access_token_encrypted")),
        },
        "tradeindia": {
            "name": "TradeIndia Portal Integration",
            "type": "apikey",
            "configured": "tradeindia" in connections,
            "has_key": bool(connections.get("tradeindia", {}).get("access_token_encrypted")),
        },
    }

    alert_connected = request.query_params.get("connected")
    alert_disconnected = request.query_params.get("disconnected")
    alert_saved = request.query_params.get("saved")
    alert_error = request.query_params.get("error")

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "user": user_info,
            "platforms": platforms_status,
            "alert_connected": alert_connected,
            "alert_disconnected": alert_disconnected,
            "alert_saved": alert_saved,
            "alert_error": alert_error,
        }
    )


@router.post("/api-key")
async def save_api_key(
    request: Request,
    platform: str = Form(...),
    api_key: str = Form(...),
):
    """
    Saves an encrypted API key for IndiaMart or TradeIndia.
    """
    try:
        claims = await get_user_claims(request)
        user_id = claims.get("sub")
    except HTTPException:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    clean_platform = platform.strip().lower()
    if clean_platform not in ["indiamart", "tradeindia"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported platform.")

    clean_key = api_key.strip()
    if not clean_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API key cannot be empty.")

    encrypted_key = encrypt_token(clean_key)

    conn_data = {
        "platform": clean_platform,
        "user_id": user_id,
        "account_name": f"{clean_platform.capitalize()} API Key",
        "account_id": f"{clean_platform}_verified_key",
        "access_token_encrypted": encrypted_key,
        "is_active": True,
        "updated_at": datetime.now().isoformat(),
    }

    MEM_PLATFORM_CONNECTIONS[clean_platform] = conn_data

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    await conn.execute(
                        """
                        INSERT INTO platform_connections (
                            user_id, platform, account_name, account_id, access_token_encrypted, is_active, updated_at
                        )
                        VALUES ($1::uuid, $2, $3, $4, $5, true, now())
                        ON CONFLICT (user_id, platform) DO UPDATE SET
                            access_token_encrypted = EXCLUDED.access_token_encrypted,
                            is_active = true,
                            updated_at = now()
                        """,
                        user_id,
                        clean_platform,
                        conn_data["account_name"],
                        conn_data["account_id"],
                        encrypted_key,
                    )
        except Exception as exc:
            logger.warning(f"Could not save API key in database: {exc}")

    return RedirectResponse(url=f"/settings?saved={clean_platform}", status_code=status.HTTP_303_SEE_OTHER)
