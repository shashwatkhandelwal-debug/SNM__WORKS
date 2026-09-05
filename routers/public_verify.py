"""
routers/public_verify.py — Unauthenticated Public Test Certificate Verification.

Provides cryptographically tamper-evident, enumeration-resistant public verification
backed by PostgreSQL SECURITY DEFINER function verify_public_certificate().

Security Guarantees:
1. Requires exact 32-character hex hash fragment + certificate number (fails closed).
2. Rate-limited (60 requests/minute per client IP) to prevent denial of service.
3. Strict zero commercial data leakage (no customer, no pricing, no lots/suppliers).
"""

import time
import string
from collections import defaultdict
from typing import Dict, List, Optional
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.status import HTTP_200_OK, HTTP_404_NOT_FOUND, HTTP_429_TOO_MANY_REQUESTS

from database import get_db

router = APIRouter(tags=["Public Verification"])
templates = Jinja2Templates(directory="templates")

# In-memory sliding-window IP rate limiter (60 req / 60s per client IP)
RATE_LIMIT_WINDOW = 60.0  # seconds
MAX_REQUESTS_PER_WINDOW = 60
_ip_request_timestamps: Dict[str, List[float]] = defaultdict(list)


def check_rate_limit(request: Request) -> bool:
    """Returns True if request is allowed, False if rate-limited."""
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    now = time.time()
    
    # Prune timestamps older than window
    timestamps = [t for t in _ip_request_timestamps[client_ip] if now - t < RATE_LIMIT_WINDOW]
    if len(timestamps) >= MAX_REQUESTS_PER_WINDOW:
        _ip_request_timestamps[client_ip] = timestamps
        return False
        
    timestamps.append(now)
    _ip_request_timestamps[client_ip] = timestamps
    return True


@router.get("/verify/{cert_no}/{hash_fragment}", response_class=HTMLResponse)
async def verify_certificate_public(
    request: Request,
    cert_no: str,
    hash_fragment: str,
    conn: asyncpg.Connection = Depends(get_db),
):
    """
    Public certificate verification endpoint.
    Zero authentication required. Fails closed immediately on any hash mismatch or format issue.
    """
    # 1. Check Rate Limit
    if not check_rate_limit(request):
        return templates.TemplateResponse(
            request=request,
            name="public/verify_certificate.html",
            context={
                "is_valid": False,
                "error_type": "rate_limited",
                "error_message": "Too Many Requests — verification rate limit exceeded. Please wait a minute before trying again.",
            },
            status_code=HTTP_429_TOO_MANY_REQUESTS,
        )

    # 2. Strict 32-Character Hex Validation (Fail closed before DB query)
    cleaned_cert = cert_no.strip().upper()
    cleaned_hash = hash_fragment.strip().lower()

    if len(cleaned_hash) != 32 or not all(c in string.hexdigits for c in cleaned_hash) or not cleaned_cert:
        return templates.TemplateResponse(
            request=request,
            name="public/verify_certificate.html",
            context={
                "is_valid": False,
                "error_type": "not_found",
                "error_message": "Certificate of Conformance not found or verification link is invalid.",
                "cert_no": cert_no,
            },
            status_code=HTTP_404_NOT_FOUND,
        )

    # 3. Call SECURITY DEFINER Postgres function
    row = await conn.fetchrow(
        "SELECT * FROM verify_public_certificate($1, $2);",
        cleaned_cert,
        cleaned_hash,
    )

    if not row or not row.get("cert_no"):
        return templates.TemplateResponse(
            request=request,
            name="public/verify_certificate.html",
            context={
                "is_valid": False,
                "error_type": "not_found",
                "error_message": "Certificate of Conformance not found or verification link is invalid.",
                "cert_no": cert_no,
            },
            status_code=HTTP_404_NOT_FOUND,
        )

    cert_data = dict(row)
    return templates.TemplateResponse(
        request=request,
        name="public/verify_certificate.html",
        context={
            "is_valid": True,
            "cert": cert_data,
            "hash_fragment": cleaned_hash,
        },
        status_code=HTTP_200_OK,
    )
