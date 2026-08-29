from typing import Any, Dict, List
import asyncpg
from fastapi import APIRouter, Depends
from database import get_db
from auth.dependencies import current_user

router = APIRouter(prefix="/api/costing", tags=["commercial"])


@router.get("/")
@router.get("")
async def list_costing(
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(current_user),
) -> List[Dict[str, Any]]:
    """
    List costing records.
    The database RLS policy on the costing table determines which rows are returned.
    Postgres enforces auth_can('costing', 'read').
    """
    rows = await conn.fetch("SELECT * FROM costing ORDER BY created_at DESC")
    return [dict(r) for r in rows]