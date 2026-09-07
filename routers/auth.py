from datetime import datetime, timedelta
import json
import logging
import secrets
from typing import Any, Dict, Optional
import urllib.parse
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import httpx

import database
from config import settings
from auth.jwt import decode_access_token
from auth.middleware import set_rls_claims
from services.crypto import encrypt_token
from services.linkedin_publisher import MEM_PLATFORM_CONNECTIONS

logger = logging.getLogger("snm_works.auth")
router = APIRouter(tags=["authentication"])
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    """
    Renders the sign-in page, or redirects to dashboard if already authenticated.
    """
    token = request.cookies.get("access_token")
    if token:
        try:
            decode_access_token(token)
            return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        except Exception:
            pass

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"user": None, "error": None}
    )


@router.post("/auth/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """
    Direct REST authentication against Supabase Auth without SDKs.
    On success, stores JWT in an httpOnly cookie and redirects to /dashboard.
    On failure, shows error message inline.
    """
    auth_url = f"{settings.supabase_url}/auth/v1/token?grant_type=password"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Content-Type": "application/json",
    }
    payload = {
        "email": email.strip(),
        "password": password,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(auth_url, json=payload, headers=headers)
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": f"Failed to connect to authentication service: {str(exc)}",
                "email": email,
                "user": None,
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if resp.status_code == 200:
        data = resp.json()
        access_token = data.get("access_token")
        if not access_token:
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "error": "Authentication server did not return an access token.",
                    "email": email,
                    "user": None,
                },
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

        response = RedirectResponse(
            url="/dashboard",
            status_code=status.HTTP_303_SEE_OTHER
        )
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            samesite="lax",
            secure=settings.environment != "development",
            max_age=3600 * 24 * 7,
            path="/",
        )
        return response
    else:
        try:
            err_data = resp.json()
            error_message = (
                err_data.get("error_description")
                or err_data.get("msg")
                or err_data.get("message")
                or "Invalid login credentials. Please check your email and password."
            )
        except Exception:
            error_message = f"Authentication failed with status {resp.status_code}."

        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": error_message,
                "email": email,
                "user": None,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )


@router.get("/auth/logout")
@router.post("/auth/logout")
async def logout():
    """
    Clears the access token cookie and redirects to the login screen.
    """
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="access_token", path="/")
    return response


# ============================================================================
# LinkedIn OAuth 2.0 Flow for SNM Company Page
# ============================================================================

@router.get("/auth/linkedin")
async def linkedin_oauth_redirect(request: Request):
    """
    Redirects to LinkedIn OAuth authorization URL with scopes
    w_organization_social and r_organization_social to post to the SNM company page.
    """
    token = request.cookies.get("access_token") or request.cookies.get("sb-access-token")
    if not token and "authorization" in request.headers:
        auth_hdr = request.headers.get("authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:]
    if not token:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    client_id = settings.linkedin_client_id or "linkedin_client_id_placeholder"
    redirect_uri = settings.linkedin_redirect_uri
    state = secrets.token_urlsafe(16)

    # Scopes for Personal Profile publishing & OpenID Connect member identity
    scopes = "openid profile w_member_social"

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": scopes,
    }
    auth_url = f"https://www.linkedin.com/oauth/v2/authorization?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=auth_url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/auth/linkedin/callback")
async def linkedin_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """
    Receives OAuth authorization code, exchanges it for an access token,
    queries OpenID Connect userinfo to resolve real member person URN (urn:li:person:{sub}),
    encrypts the token, and stores it in platform_connections.
    """
    token = request.cookies.get("access_token") or request.cookies.get("sb-access-token")
    if not token and "authorization" in request.headers:
        auth_hdr = request.headers.get("authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:]
    if not token:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    try:
        claims = decode_access_token(token)
        user_id = claims.get("sub")
    except Exception:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    if error or not code:
        err_msg = error_description or error or "LinkedIn authorization was cancelled or failed."
        logger.warning(f"LinkedIn OAuth error: {err_msg}")
        return RedirectResponse(url=f"/settings?error={urllib.parse.quote(err_msg)}", status_code=status.HTTP_303_SEE_OTHER)

    token_url = "https://www.linkedin.com/oauth/v2/accessToken"
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.linkedin_redirect_uri,
        "client_id": settings.linkedin_client_id or "linkedin_client_id_placeholder",
        "client_secret": settings.linkedin_client_secret or "linkedin_client_secret_placeholder",
    }

    access_token = None
    expires_in = 3600 * 24 * 60  # Default 60 days
    author_urn = None
    account_name = "LinkedIn Member"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(token_url, data=payload)
            if resp.status_code == 200:
                data = resp.json()
                access_token = data.get("access_token")
                expires_in = data.get("expires_in", expires_in)

                if access_token:
                    # Query OpenID Connect userinfo endpoint with the fresh access token
                    try:
                        userinfo_resp = await client.get(
                            "https://api.linkedin.com/v2/userinfo",
                            headers={"Authorization": f"Bearer {access_token}"}
                        )
                        if userinfo_resp.status_code == 200:
                            ui_data = userinfo_resp.json()
                            sub = ui_data.get("sub")
                            if sub:
                                author_urn = f"urn:li:person:{sub}"
                            name = ui_data.get("name") or f"{ui_data.get('given_name', '')} {ui_data.get('family_name', '')}".strip()
                            if name:
                                account_name = f"{name} (LinkedIn Personal)"
                        else:
                            logger.warning(f"LinkedIn userinfo returned {userinfo_resp.status_code}: {userinfo_resp.text}")
                    except Exception as u_exc:
                        logger.warning(f"Exception fetching LinkedIn userinfo: {u_exc}")
            else:
                logger.warning(f"LinkedIn token exchange returned {resp.status_code}: {resp.text}")
    except Exception as exc:
        logger.error(f"Error during LinkedIn token exchange: {exc}")

    # Fallback to configured organization/company ID if explicit in settings and not resolved via personal userinfo
    if not author_urn and settings.linkedin_company_page_id:
        cfg_id = settings.linkedin_company_page_id
        if cfg_id.startswith("urn:li:"):
            author_urn = cfg_id
        else:
            author_urn = f"urn:li:organization:{cfg_id}"

    # For development fallback if testing locally without live LinkedIn API app
    if not access_token:
        access_token = f"li_token_mock_{secrets.token_hex(16)}"

    if not author_urn:
        err_msg = "Could not resolve LinkedIn member identity. Please disconnect and reconnect your LinkedIn account."
        logger.error(err_msg)
        return RedirectResponse(url=f"/settings?error={urllib.parse.quote(err_msg)}", status_code=status.HTTP_303_SEE_OTHER)

    # Encrypt token
    encrypted_token = encrypt_token(access_token)
    expires_at = datetime.now() + timedelta(seconds=expires_in)

    conn_data = {
        "platform": "linkedin",
        "user_id": user_id,
        "account_name": account_name,
        "account_id": author_urn,
        "access_token_encrypted": encrypted_token,
        "token_expires_at": expires_at.isoformat(),
        "scopes": ["openid", "profile", "w_member_social"],
        "is_active": True,
        "updated_at": datetime.now().isoformat(),
    }

    # Store in memory
    MEM_PLATFORM_CONNECTIONS["linkedin"] = conn_data

    # Store in PostgreSQL database if pool available
    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    await conn.execute(
                        """
                        INSERT INTO platform_connections (
                            user_id, platform, account_name, account_id,
                            access_token_encrypted, token_expires_at, scopes, is_active, updated_at
                        )
                        VALUES ($1::uuid, $2, $3, $4, $5, $6::timestamptz, $7, true, now())
                        ON CONFLICT (user_id, platform) DO UPDATE SET
                            account_name = EXCLUDED.account_name,
                            account_id = EXCLUDED.account_id,
                            access_token_encrypted = EXCLUDED.access_token_encrypted,
                            token_expires_at = EXCLUDED.token_expires_at,
                            scopes = EXCLUDED.scopes,
                            is_active = true,
                            updated_at = now()
                        """,
                        user_id,
                        "linkedin",
                        conn_data["account_name"],
                        conn_data["account_id"],
                        encrypted_token,
                        expires_at,
                        conn_data["scopes"],
                    )
        except Exception as exc:
            logger.warning(f"Could not save LinkedIn connection to database: {exc}")

    return RedirectResponse(url="/settings?connected=linkedin", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/auth/linkedin/disconnect")
async def linkedin_disconnect(request: Request):
    """
    Disconnects the active LinkedIn integration.
    """
    token = request.cookies.get("access_token") or request.cookies.get("sb-access-token")
    if not token and "authorization" in request.headers:
        auth_hdr = request.headers.get("authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:]
    if not token:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    try:
        claims = decode_access_token(token)
        user_id = claims.get("sub")
    except Exception:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    MEM_PLATFORM_CONNECTIONS.pop("linkedin", None)

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    await conn.execute("DELETE FROM platform_connections WHERE user_id = $1::uuid AND platform = 'linkedin'", user_id)
        except Exception as exc:
            logger.warning(f"Could not remove LinkedIn connection from DB: {exc}")

    return RedirectResponse(url="/settings?disconnected=linkedin", status_code=status.HTTP_303_SEE_OTHER)
