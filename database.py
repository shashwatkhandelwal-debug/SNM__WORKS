import logging
from typing import AsyncGenerator, Optional
import urllib.parse
import asyncpg
from fastapi import HTTPException, status
from config import settings

logger = logging.getLogger("snm_works.database")
pool: Optional[asyncpg.Pool] = None


def format_dsn(raw_dsn: Optional[str]) -> Optional[str]:
    """
    Safely formats PostgreSQL DSN ensuring special characters in credentials
    (such as '#', '!', '@') are properly percent-encoded for asyncpg.
    """
    if not raw_dsn:
        return None
    
    # If DSN is already well-formed or lacks user:password split, return as-is
    if "://" not in raw_dsn or "@" not in raw_dsn:
        return raw_dsn

    try:
        prefix, rest = raw_dsn.split("://", 1)
        userpass, hostdb = rest.split("@", 1)
        if ":" in userpass:
            user, pwd = userpass.split(":", 1)
            # If pwd has unescaped '#' or '%', unquote first then quote
            decoded_pwd = urllib.parse.unquote(pwd)
            encoded_pwd = urllib.parse.quote(decoded_pwd, safe="")
            return f"{prefix}://{user}:{encoded_pwd}@{hostdb}"
    except Exception:
        pass
    return raw_dsn


async def init_db_pool() -> Optional[asyncpg.Pool]:
    """
    Initializes the asyncpg connection pool connecting as snm_app on port 5432.
    """
    global pool
    if not settings.database_url:
        logger.warning("DATABASE_URL is not set; database connection pool not initialized.")
        return None

    dsn = format_dsn(settings.database_url)
    try:
        pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=2,
            max_size=10,
            command_timeout=30.0,
        )
        logger.info("Database connection pool initialized successfully.")
        return pool
    except Exception as exc:
        logger.error(f"Could not connect to database pool: {exc}")
        pool = None
        return None


async def close_db_pool() -> None:
    """
    Closes the asyncpg connection pool cleanly on application shutdown.
    """
    global pool
    if pool is not None:
        await pool.close()
        pool = None
        logger.info("Database connection pool closed.")


async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Yields one asyncpg connection per request, inside an isolated transaction.
    The RLS claim injector sets claims inside this transaction.
    """
    global pool
    if pool is None:
        # Try a lazy reconnection if pool wasn't available at startup
        await init_db_pool()

    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection pool is not configured or unavailable. Please ensure database credentials and 08_lockdown.sql have been run.",
        )
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn