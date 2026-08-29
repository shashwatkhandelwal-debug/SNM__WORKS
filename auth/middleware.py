import json
from typing import Any, Dict
import asyncpg


async def set_rls_claims(conn: asyncpg.Connection, claims: Dict[str, Any]) -> None:
    """
    SET LOCAL is transaction-scoped. Plain SET would persist on a pooled
    connection and leak one user's identity into the next request.
    Always LOCAL. No exceptions.
    """
    await conn.execute("SET LOCAL ROLE authenticated")
    claims_json = json.dumps(claims)
    await conn.execute(
        "SELECT set_config('request.jwt.claims', $1, true)",
        claims_json
    )