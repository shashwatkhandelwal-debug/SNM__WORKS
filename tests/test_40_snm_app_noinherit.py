import asyncio
import json
import os
import urllib.parse
import uuid
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
async def test_snm_app_membership_noinherit_properties():
    """pg_auth_members shows inherit_option = false, set_option = true for snm_app -> authenticated on PG16+."""
    pool = await get_test_pool()

    async with pool.acquire() as conn:
        version_num = await conn.fetchval("SELECT current_setting('server_version_num')::int;")
        if version_num < 160000:
            await pool.close()
            pytest.skip("GRANT ... WITH INHERIT is a PostgreSQL 16+ feature")

        membership = await conn.fetchrow("""
            SELECT m.admin_option, m.inherit_option, m.set_option
            FROM pg_auth_members m
            JOIN pg_roles r_member ON r_member.oid = m.member
            JOIN pg_roles r_role ON r_role.oid = m.roleid
            WHERE r_member.rolname = 'snm_app' AND r_role.rolname = 'authenticated';
        """)
        assert membership is not None, "Membership record for snm_app -> authenticated not found in pg_auth_members!"
        assert membership["inherit_option"] is False, f"Expected inherit_option=False, got {membership['inherit_option']}"
        assert membership["set_option"] is True, f"Expected set_option=True, got {membership['set_option']}"

    await pool.close()


@pytest.mark.asyncio
async def test_plain_snm_app_cannot_delete_or_insert_audit_and_set_role_works():
    """A plain snm_app session (no SET ROLE) cannot DELETE from platform_connections/user_roles nor INSERT into audit_log, while SET LOCAL ROLE authenticated works."""
    pool = await get_test_pool()

    async with pool.acquire() as conn:
        # 1. Plain snm_app session cannot DELETE from platform_connections
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE snm_app;")
            cur_role = await conn.fetchval("SELECT current_user;")
            assert cur_role == "snm_app"

            with pytest.raises(InsufficientPrivilegeError) as exc_conn:
                await conn.execute("DELETE FROM platform_connections;")
            assert "permission denied" in str(exc_conn.value).lower() or "42501" in str(type(exc_conn.value))

        # 2. Plain snm_app session cannot DELETE from user_roles
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE snm_app;")
            with pytest.raises(InsufficientPrivilegeError) as exc_roles:
                await conn.execute("DELETE FROM user_roles;")
            assert "permission denied" in str(exc_roles.value).lower() or "42501" in str(type(exc_roles.value))

        # 3. Plain snm_app session cannot INSERT into audit_log
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE snm_app;")
            with pytest.raises(InsufficientPrivilegeError) as exc_audit:
                await conn.execute("""
                    INSERT INTO audit_log (actor_id, actor_name, action, entity, entity_ref)
                    VALUES ('00000000-0000-0000-0000-000000000001', 'Direct App', 'tamper', 'system', '0')
                """)
            assert "permission denied" in str(exc_audit.value).lower() or "42501" in str(type(exc_audit.value))

        # 4. SET LOCAL ROLE authenticated inside a transaction succeeds and allows permitted operations
        owner_id = "00000000-0000-0000-0000-000000000001"
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE snm_app;")
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", json.dumps({"sub": owner_id, "role": "authenticated"}))
            cur_user = await conn.fetchval("SELECT current_user;")
            assert cur_user == "authenticated"
            
            roles = await conn.fetch("SELECT * FROM public.my_roles();")
            assert len(roles) > 0

    await pool.close()

