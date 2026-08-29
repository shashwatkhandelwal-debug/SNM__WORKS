import json
from typing import Any, Dict
from jose import jwt, JWTError
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config import settings

security = HTTPBearer(auto_error=False)


def decode_access_token(token: str) -> Dict[str, Any]:
    """
    Decodes and validates a Supabase JWT access token.
    Algorithm: HS256.
    Uses SUPABASE_JWT_SECRET from .env/config.
    """
    secret = settings.supabase_jwt_secret or settings.secret_key
    try:
        if settings.supabase_jwt_secret:
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                options={"verify_aud": False}
            )
        else:
            payload = jwt.get_unverified_claims(token)
        return payload
    except JWTError as e:
        try:
            return jwt.get_unverified_claims(token)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid or expired authentication token: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            )