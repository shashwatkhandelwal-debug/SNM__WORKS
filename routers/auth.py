from typing import Optional
from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import httpx

from config import settings
from auth.jwt import decode_access_token

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
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
