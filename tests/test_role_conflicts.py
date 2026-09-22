"""
tests/test_role_conflicts.py — Unit Tests for Segregation of Duties & Role Conflicts Trigger.

Tests:
1. Non-owner profile assigned purchase_officer + accounts_officer (block severity) is blocked by DB trigger.
2. Owner profile assigned purchase_officer + accounts_officer is exempt from blocking conflict.
"""

import uuid
import asyncpg
import pytest
from asyncpg.exceptions import PostgresError
from tests.conftest import LOCAL_TEST_DATABASE_URL


@pytest.mark.asyncio
async def test_owner_role_conflict_exemption_and_non_owner_block():
    """
    Proves that check_user_role_conflicts() DB trigger:
    1. Blocks non-owner users from holding blocking conflict roles (purchase_officer + accounts_officer).
    2. Exempts owner users, permitting both roles without raising an exception.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    
    non_owner_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    
    try:
        # --- 1. Non-owner case (MUST BLOCK) ---
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, $2);", non_owner_id, f"non-owner-{non_owner_id.hex[:6]}@snmills.test")
        await conn.execute("UPDATE profiles SET full_name = 'Staff Member', role = 'operator', active = true WHERE id = $1;", non_owner_id)
        
        # First role succeeds
        await conn.execute("INSERT INTO user_roles (user_id, role_code, active) VALUES ($1, 'purchase_officer', true);", non_owner_id)
        
        # Second conflicting role must raise PostgresError (Segregation of duties)
        with pytest.raises(PostgresError) as exc_info:
            await conn.execute("INSERT INTO user_roles (user_id, role_code, active) VALUES ($1, 'accounts_officer', true);", non_owner_id)
        
        err_msg = str(exc_info.value)
        assert "Segregation of duties" in err_msg or "blocking conflict" in err_msg
        
        # --- 2. Owner case (MUST NOT BLOCK) ---
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, $2);", owner_id, f"owner-{owner_id.hex[:6]}@snmills.test")
        await conn.execute("UPDATE profiles SET full_name = 'Executive Owner', role = 'owner', active = true WHERE id = $1;", owner_id)
        
        # Both conflicting roles succeed for owner
        await conn.execute("INSERT INTO user_roles (user_id, role_code, active) VALUES ($1, 'purchase_officer', true);", owner_id)
        await conn.execute("INSERT INTO user_roles (user_id, role_code, active) VALUES ($1, 'accounts_officer', true);", owner_id)
        
        roles = await conn.fetch("SELECT role_code FROM user_roles WHERE user_id = $1 AND active = true;", owner_id)
        role_codes = {r["role_code"] for r in roles}
        assert role_codes == {"purchase_officer", "accounts_officer"}
        
    finally:
        await conn.execute("DELETE FROM user_roles WHERE user_id IN ($1, $2);", non_owner_id, owner_id)
        await conn.execute("DELETE FROM profiles WHERE id IN ($1, $2);", non_owner_id, owner_id)
        await conn.execute("DELETE FROM auth.users WHERE id IN ($1, $2);", non_owner_id, owner_id)
        await conn.close()
