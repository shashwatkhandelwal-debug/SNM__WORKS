import asyncio
import json
import uuid
import pytest
import asyncpg
from config import settings
from database import format_dsn


async def get_test_pool():
    target_url = settings.local_test_database_url or "postgresql://postgres:postgres@127.0.0.1:5433/snm_test_db"
    dsn = format_dsn(target_url)
    return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)


@pytest.mark.asyncio
async def test_authenticated_direct_insert_audit_fails_42501():
    """Direct INSERT into audit_log by authenticated fails with 42501 (RLS violation)."""
    pool = await get_test_pool()
    user_id = str(uuid.uuid4())
    uid = uuid.uuid4().hex[:8]

    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, $2)", user_id, f"audituser_{uid}@snmills.com")
        await conn.execute("UPDATE profiles SET full_name = 'Audit Test User', role = 'supervisor', active = true WHERE id = $1", user_id)
        await conn.execute("INSERT INTO user_roles (user_id, role_code, active) VALUES ($1, 'chief_quality', true) ON CONFLICT DO NOTHING", user_id)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", json.dumps({"sub": user_id, "role": "authenticated"}))
            
            with pytest.raises(Exception) as exc_info:
                await conn.execute("""
                    INSERT INTO audit_log (actor_id, actor_name, action, entity, entity_ref)
                    VALUES ($1, 'Direct Attacker', 'tamper', 'system', '0')
                """, uuid.UUID(user_id))
            
            err_msg = str(exc_info.value).lower()
            assert "permission denied" in err_msg or "violates row-level security" in err_msg or "42501" in str(type(exc_info.value))

    await pool.close()


@pytest.mark.asyncio
async def test_log_change_tables_trigger_audit_writes():
    """Trigger-driven writes under SET LOCAL ROLE authenticated still write audit rows cleanly."""
    pool = await get_test_pool()
    user_id = str(uuid.uuid4())
    uid = uuid.uuid4().hex[:8]

    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, $2)", user_id, f"triguser_{uid}@snmills.com")
        await conn.execute("UPDATE profiles SET full_name = 'Trigger Audit User', role = 'supervisor', active = true WHERE id = $1", user_id)

    async with pool.acquire() as conn:
        initial_count = await conn.fetchval("SELECT count(*) FROM audit_log;")
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", json.dumps({"sub": user_id, "role": "authenticated"}))
            await conn.execute("UPDATE profiles SET full_name = 'Trigger Audit User Updated' WHERE id = $1", user_id)
        
        new_count = await conn.fetchval("SELECT count(*) FROM audit_log;")
        assert new_count > initial_count, "Trigger-driven audit log write did not produce a row under authenticated."

    await pool.close()


@pytest.mark.asyncio
async def test_verify_audit_chain_ok():
    """verify_audit_chain() verifies hash chain integrity after writes."""
    pool = await get_test_pool()
    async with pool.acquire() as conn:
        chain_status = await conn.fetchval("SELECT status FROM verify_audit_chain();")
        assert chain_status == "OK", f"Audit chain verification failed with status: {chain_status}"
    await pool.close()
