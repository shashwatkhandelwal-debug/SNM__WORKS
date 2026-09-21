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
async def test_roleless_user_reads_zero_tasks():
    """A signed-in user with no roles assigned must see 0 tasks when selecting from tasks directly."""
    pool = await get_test_pool()
    roleless_user_id = str(uuid.uuid4())
    uid = uuid.uuid4().hex[:8]

    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, $2)", roleless_user_id, f"roleless_{uid}@snmills.com")
        await conn.execute("UPDATE profiles SET full_name = 'Roleless User', role = 'operator', active = true WHERE id = $1", roleless_user_id)
        # Ensure at least one task exists
        await conn.execute("""
            INSERT INTO tasks (title, detail, assigned_role, status)
            VALUES ('Sample Task for Read Test', 'Testing task visibility', 'qa_manager', 'open')
        """)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", json.dumps({"sub": roleless_user_id, "role": "authenticated"}))
            tasks = await conn.fetch("SELECT * FROM tasks;")
            assert len(tasks) == 0, f"Roleless user saw {len(tasks)} tasks; expected 0."

    await pool.close()


@pytest.mark.asyncio
async def test_tasks_read_permitted_user_reads_all_tasks():
    """A user holding a role with tasks.read permission reads all tasks through RLS."""
    pool = await get_test_pool()
    manager_user_id = str(uuid.uuid4())
    uid = uuid.uuid4().hex[:8]

    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, $2)", manager_user_id, f"manager_{uid}@snmills.com")
        await conn.execute("UPDATE profiles SET full_name = 'QA Manager', role = 'supervisor', active = true WHERE id = $1", manager_user_id)
        await conn.execute(
            "INSERT INTO user_roles (user_id, role_code, active) VALUES ($1, 'qa_manager', true) ON CONFLICT DO NOTHING",
            manager_user_id
        )
        await conn.execute("""
            INSERT INTO tasks (title, detail, assigned_role, status)
            VALUES ('QA Task 101', 'Verify QA report', 'qa_manager', 'open')
        """)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", json.dumps({"sub": manager_user_id, "role": "authenticated"}))
            tasks = await conn.fetch("SELECT * FROM tasks;")
            assert len(tasks) > 0, "Permitted user holding tasks.read could not read tasks."

    await pool.close()


@pytest.mark.asyncio
async def test_my_tasks_unchanged_for_role():
    """my_tasks() (SECURITY DEFINER) returns assigned tasks for role holders, and 0 for roleless accounts."""
    pool = await get_test_pool()
    manager_user_id = str(uuid.uuid4())
    roleless_user_id = str(uuid.uuid4())
    uid = uuid.uuid4().hex[:8]

    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, $2)", manager_user_id, f"mgr_tasks_{uid}@snmills.com")
        await conn.execute("UPDATE profiles SET full_name = 'QA Manager 2', role = 'supervisor', active = true WHERE id = $1", manager_user_id)
        await conn.execute("INSERT INTO user_roles (user_id, role_code, active) VALUES ($1, 'qa_manager', true) ON CONFLICT DO NOTHING", manager_user_id)

        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, $2)", roleless_user_id, f"roleless2_{uid}@snmills.com")
        await conn.execute("UPDATE profiles SET full_name = 'Roleless 2', role = 'operator', active = true WHERE id = $1", roleless_user_id)

        await conn.execute("""
            INSERT INTO tasks (title, detail, assigned_role, status)
            VALUES ('QA Specific Task', 'Specific for QA manager', 'qa_manager', 'open')
        """)

    # Permitted role fetches my_tasks()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", json.dumps({"sub": manager_user_id, "role": "authenticated"}))
            mgr_tasks = await conn.fetch("SELECT * FROM my_tasks();")
            assert len(mgr_tasks) > 0, "QA Manager received 0 tasks from my_tasks()."
            assert any(t["assigned_role"] == "qa_manager" for t in mgr_tasks)

    # Roleless account fetches my_tasks() -> 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", json.dumps({"sub": roleless_user_id, "role": "authenticated"}))
            roleless_tasks = await conn.fetch("SELECT * FROM my_tasks();")
            assert len(roleless_tasks) == 0, f"Roleless account received {len(roleless_tasks)} tasks from my_tasks(); expected 0."

    await pool.close()
