import asyncio
import json
import pytest
import asyncpg
from asyncpg.exceptions import InsufficientPrivilegeError
from config import settings
from database import format_dsn


async def get_test_pool():
    target_url = settings.local_test_database_url or "postgresql://postgres:postgres@127.0.0.1:5433/snm_test_db"
    dsn = format_dsn(target_url)
    return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)


@pytest.mark.asyncio
async def test_guard_a_authenticated_cannot_execute_diagnostics():
    """(a) authenticated role has NO EXECUTE on security_posture or verify_audit_chain."""
    pool = await get_test_pool()
    async with pool.acquire() as conn:
        sec_posture_priv = await conn.fetchval(
            "SELECT has_function_privilege('authenticated', 'public.security_posture()', 'EXECUTE');"
        )
        assert sec_posture_priv is False, "authenticated should NOT have EXECUTE on public.security_posture()"

        verify_audit_priv = await conn.fetchval(
            "SELECT has_function_privilege('authenticated', 'public.verify_audit_chain()', 'EXECUTE');"
        )
        assert verify_audit_priv is False, "authenticated should NOT have EXECUTE on public.verify_audit_chain()"

    await pool.close()


@pytest.mark.asyncio
async def test_guard_b_legacy_functions_dropped():
    """(b) rls_report, auth_role, and has_role(text) do not exist (to_regprocedure returns NULL)."""
    pool = await get_test_pool()
    async with pool.acquire() as conn:
        for fn in ['public.rls_report()', 'public.auth_role()', 'public.has_role(text)']:
            reg = await conn.fetchval("SELECT to_regprocedure($1);", fn)
            assert reg is None, f"Function {fn} should have been dropped, but to_regprocedure returned: {reg}"

    await pool.close()


@pytest.mark.asyncio
async def test_guard_c_superuser_can_run_diagnostics():
    """(c) Superuser connection can run security_posture() and verify_audit_chain()."""
    pool = await get_test_pool()
    async with pool.acquire() as conn:
        posture_rows = await conn.fetch("SELECT * FROM public.security_posture();")
        assert len(posture_rows) > 0, "security_posture() should return posture rows for superuser"

        audit_chain_status = await conn.fetchval("SELECT status FROM public.verify_audit_chain();")
        assert audit_chain_status == "OK", f"Expected audit_chain status 'OK', got {audit_chain_status}"

    await pool.close()


@pytest.mark.asyncio
async def test_guard_e_authenticated_session_cannot_call_security_posture():
    """(e) Authenticated user calling security_posture() directly raises InsufficientPrivilegeError (42501)."""
    pool = await get_test_pool()
    async with pool.acquire() as conn:
        owner_id = "00000000-0000-0000-0000-000000000001"
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", json.dumps({"sub": owner_id, "role": "authenticated"}))

            with pytest.raises(InsufficientPrivilegeError) as exc_info:
                await conn.fetch("SELECT * FROM public.security_posture();")
            assert "permission denied" in str(exc_info.value).lower() or "42501" in str(type(exc_info.value))

    await pool.close()
