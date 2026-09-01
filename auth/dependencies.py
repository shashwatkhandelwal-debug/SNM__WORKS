from typing import Any, Dict, Optional
import asyncpg
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from database import get_db
from auth.jwt import decode_access_token
from auth.middleware import set_rls_claims

bearer_scheme = HTTPBearer(auto_error=False)


async def current_user(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Dict[str, Any]:
    """
    Extracts the caller's JWT, validates it, and injects transaction-scoped RLS claims.
    """
    token = None
    if auth_header:
        token = auth_header.credentials
    elif "access_token" in request.cookies:
        token = request.cookies.get("access_token")
    elif "sb-access-token" in request.cookies:
        token = request.cookies.get("sb-access-token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = decode_access_token(token)
    user_id = claims.get("sub")

    # Inject transaction-scoped RLS claims FIRST so all subsequent queries and RLS policies have identity context
    await set_rls_claims(conn, claims)

    if user_id:
        is_active = await conn.fetchval("SELECT active FROM profiles WHERE id = $1::uuid;", user_id)
        if is_active is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account has been deactivated.",
            )

    return {
        "id": user_id,
        "email": claims.get("email"),
        "role": claims.get("role", "authenticated"),
        "claims": claims,
    }


def require(module: str, action: str):
    """
    Checks authorization directly against Postgres auth_can(module, action)
    within the current request transaction.
    """
    async def dep(
        conn: asyncpg.Connection = Depends(get_db),
        user: Dict[str, Any] = Depends(current_user),
    ) -> Dict[str, Any]:
        can = await conn.fetchval("SELECT auth_can($1, $2)", module, action)
        if not can:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your roles do not permit {action} on {module}",
            )
        return user

    return dep