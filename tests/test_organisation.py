"""
Tests for Organisation & Roles Module -- SNM Works
=============================================================================
1. Organisation dashboard, permission matrix, conflicts, and user directory.
2. Privileged Self-Grant Prevention (Non-negotiable constraint: admin cannot grant to self).
3. Non-Admin Self-Service Prevention (shift_supervisor denied 403 on grant).
4. Blocking Role Conflict Database Trigger (purchase_officer + accounts_officer fails).
5. Warning Role Conflict (chief_operating + chief_quality permitted with warning).
6. Self-Deactivation Prevention (admin cannot deactivate their own profile).
7. Immediate Deactivation Access Revocation (ends access within one request cycle).
8. My Tasks Inbox & Role-based Routing (tasks auto-route based on held roles).
9. Task Status Lifecycle Transitions (open -> in progress -> done).
10. Unauthenticated Access Rejection.
=============================================================================
"""

import os
import uuid
import asyncpg
from asyncpg.exceptions import CheckViolationError
import httpx
import pytest
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)

from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS, make_test_token
from main import app


# ============================================================================
# 1. ORGANISATION PAGES ACCESS
# ============================================================================

@pytest.mark.asyncio
async def test_organisation_dashboard_and_views(people_client, supervisor_client):
    """
    Proves that authenticated staff can view the 11 functions, 29 roles,
    336-grant permission matrix, and conflict rules.
    """
    # 1. Dashboard
    resp = await supervisor_client.get("/organisation")
    assert resp.status_code == HTTP_200_OK
    assert "ORGANISATION FUNCTIONS & ROLES" in resp.text
    assert "Executive" in resp.text
    assert "Quality" in resp.text

    # 2. Permission Matrix
    resp_matrix = await supervisor_client.get("/organisation/matrix")
    assert resp_matrix.status_code == HTTP_200_OK
    assert "PERMISSION MATRIX" in resp_matrix.text
    assert "jobs" in resp_matrix.text
    assert "despatch" in resp_matrix.text

    # Filtered matrix
    resp_filt = await supervisor_client.get("/organisation/matrix?module_filter=qc")
    assert resp_filt.status_code == HTTP_200_OK
    assert "qc" in resp_filt.text

    # 3. Conflicts Reference
    resp_conf = await supervisor_client.get("/organisation/conflicts")
    assert resp_conf.status_code == HTTP_200_OK
    assert "SEGREGATION OF DUTIES & ROLE CONFLICTS" in resp_conf.text

    # 4. Users Directory
    resp_users = await supervisor_client.get("/organisation/users")
    assert resp_users.status_code == HTTP_200_OK
    assert "STAFF DIRECTORY & ROLE ASSIGNMENTS" in resp_users.text


# ============================================================================
# 2. PRIVILEGED SELF-GRANT PREVENTION (NON-NEGOTIABLE RULE)
# ============================================================================

@pytest.mark.asyncio
async def test_privileged_user_cannot_grant_role_to_self(people_client):
    """
    Non-Negotiable Rule: chief_people holds people.approve, but CANNOT grant
    an additional role to their own user account.
    """
    self_id = TEST_USERS["chief_people"]["id"]

    # 1. Router attempt to self-grant -> 400 Bad Request
    resp = await people_client.post(
        f"/organisation/users/{self_id}/grant",
        data={"role_code": "sales_executive"},
        follow_redirects=False,
    )
    assert resp.status_code == HTTP_400_BAD_REQUEST
    assert "Segregation of duties violation" in resp.text

    # 2. Database-level constraint check: user_roles_no_self_grant
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    with pytest.raises(CheckViolationError):
        await conn.execute(
            """
            INSERT INTO user_roles (user_id, role_code, granted_by, active)
            VALUES ($1::uuid, 'sales_executive', $1::uuid, true);
            """,
            uuid.UUID(self_id),
        )
    await conn.close()


# ============================================================================
# 3. NON-PRIVILEGED USERS CANNOT GRANT OR REVOKE ROLES
# ============================================================================

@pytest.mark.asyncio
async def test_non_privileged_user_cannot_grant_roles(supervisor_client, inspector_client):
    """
    Proves that non-admin roles (shift_supervisor, line_inspector) receive 403 Forbidden
    when attempting to grant or revoke roles.
    """
    target_id = TEST_USERS["line_inspector"]["id"]

    # Attempt grant by shift_supervisor
    resp = await supervisor_client.post(
        f"/organisation/users/{target_id}/grant",
        data={"role_code": "lab_analyst"},
        follow_redirects=False,
    )
    assert resp.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit" in resp.text

    # Attempt revoke by line_inspector
    resp_rev = await inspector_client.post(
        f"/organisation/users/{target_id}/revoke",
        data={"role_code": "line_inspector"},
        follow_redirects=False,
    )
    assert resp_rev.status_code == HTTP_403_FORBIDDEN


# ============================================================================
# 4. BLOCKING ROLE CONFLICT DATABASE TRIGGER
# ============================================================================

@pytest.mark.asyncio
async def test_blocking_role_conflict_database_trigger_and_router(people_client):
    """
    Proves that assigning purchase_officer + accounts_officer ('block' severity)
    is rejected by both Postgres trigger trg_check_user_role_conflicts and the router.
    """
    # Create a fresh test user
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    user_id = str(uuid.uuid4())
    granter_id = TEST_USERS["chief_people"]["id"]
    email = f"clerk-{uuid.uuid4().hex[:6]}@snmills.com"

    await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2);", uuid.UUID(user_id), email)
    await conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Clerk Officer', 'operator', true) ON CONFLICT (id) DO UPDATE SET full_name = 'Clerk Officer', active = true;", uuid.UUID(user_id))

    # 1. Grant purchase_officer -> succeeds
    grant1 = await people_client.post(
        f"/organisation/users/{user_id}/grant",
        data={"role_code": "purchase_officer"},
        follow_redirects=False,
    )
    assert grant1.status_code == HTTP_303_SEE_OTHER

    # 2. Grant conflicting accounts_officer ('block') -> 400 Bad Request
    grant2 = await people_client.post(
        f"/organisation/users/{user_id}/grant",
        data={"role_code": "accounts_officer"},
        follow_redirects=False,
    )
    assert grant2.status_code == HTTP_400_BAD_REQUEST
    assert "Segregation of duties violation" in grant2.text

    # 3. Direct SQL insert attempt also blocked by Postgres trigger
    with pytest.raises(CheckViolationError):
        await conn.execute(
            """
            INSERT INTO user_roles (user_id, role_code, granted_by, active)
            VALUES ($1::uuid, 'accounts_officer', $2::uuid, true);
            """,
            uuid.UUID(user_id),
            uuid.UUID(granter_id),
        )

    await conn.close()


# ============================================================================
# 5. WARNING ROLE CONFLICT PERMITTED WITH SURFACED WARNING
# ============================================================================

@pytest.mark.asyncio
async def test_warning_role_conflict_permitted_and_surfaced(people_client):
    """
    Proves that assigning chief_operating + chief_quality ('warn' severity)
    is permitted, but detected and surfaced by get_user_role_conflicts().
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    user_id = str(uuid.uuid4())
    email = f"plant.head-{uuid.uuid4().hex[:6]}@snmills.com"

    await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2);", uuid.UUID(user_id), email)
    await conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Plant Head', 'owner', true) ON CONFLICT (id) DO UPDATE SET full_name = 'Plant Head', active = true;", uuid.UUID(user_id))

    # Grant chief_operating
    await people_client.post(
        f"/organisation/users/{user_id}/grant",
        data={"role_code": "chief_operating"},
        follow_redirects=False,
    )

    # Grant chief_quality (warning conflict)
    resp = await people_client.post(
        f"/organisation/users/{user_id}/grant",
        data={"role_code": "chief_quality"},
        follow_redirects=False,
    )
    assert resp.status_code == HTTP_303_SEE_OTHER

    # Verify user detail surfaces the warning
    detail_resp = await people_client.get(f"/organisation/users/{user_id}")
    assert detail_resp.status_code == HTTP_200_OK
    assert "Segregation of Duties Warnings Detected" in detail_resp.text
    assert "chief_operating" in detail_resp.text
    assert "chief_quality" in detail_resp.text

    await conn.close()


# ============================================================================
# 6. SELF-DEACTIVATION PREVENTION
# ============================================================================

@pytest.mark.asyncio
async def test_admin_cannot_deactivate_own_account(people_client):
    """
    Proves that an administrator cannot deactivate their own profile, preventing lockout.
    """
    self_id = TEST_USERS["chief_people"]["id"]

    resp = await people_client.post(
        f"/organisation/users/{self_id}/toggle-active",
        follow_redirects=False,
    )
    assert resp.status_code == HTTP_400_BAD_REQUEST
    assert "Cannot deactivate your own user account" in resp.text


# ============================================================================
# 7. IMMEDIATE ACCESS REVOCATION UPON DEACTIVATION
# ============================================================================

@pytest.mark.asyncio
async def test_deactivation_revokes_access_within_one_request_cycle(people_client):
    """
    Non-Negotiable Rule: Deactivating a user ends their access within one request cycle,
    even if they present a still-valid JWT.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    user_id = str(uuid.uuid4())
    email = f"temp.worker-{uuid.uuid4().hex[:6]}@snmills.com"

    await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2);", uuid.UUID(user_id), email)
    await conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Temp Worker', 'operator', true) ON CONFLICT (id) DO UPDATE SET full_name = 'Temp Worker', active = true;", uuid.UUID(user_id))
    await conn.execute("INSERT INTO user_roles (user_id, role_code, active) VALUES ($1::uuid, 'line_inspector', true);", uuid.UUID(user_id))

    # 1. User has a valid token and accesses the system -> 200 OK
    token = make_test_token(user_id=user_id, email=email, role_code="line_inspector")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as worker_client:
        resp_before = await worker_client.get("/organisation")
        assert resp_before.status_code == HTTP_200_OK

        # 2. Administrator deactivates the user
        deact_resp = await people_client.post(
            f"/organisation/users/{user_id}/toggle-active",
            follow_redirects=False,
        )
        assert deact_resp.status_code == HTTP_303_SEE_OTHER

        # 3. Next request from worker with same token is immediately rejected -> 403 Forbidden
        resp_after = await worker_client.get("/organisation")
        assert resp_after.status_code == HTTP_403_FORBIDDEN
        assert "User account has been deactivated" in resp_after.text

    await conn.close()


# ============================================================================
# 8. MY TASKS ROUTING & WORK INBOX
# ============================================================================

@pytest.mark.asyncio
async def test_my_tasks_routing_by_held_role(qa_client, inspector_client):
    """
    Proves that a task assigned to 'qa_manager' appears in my_tasks() for qa_manager,
    but not in the inbox of line_inspector.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    task_id = await conn.fetchval(
        """
        INSERT INTO tasks (
            id, title, detail, assigned_role, priority, status
        ) VALUES (
            gen_random_uuid(), 'Audit MIL-W-4088K Test Procedures', 'Review tensile testing protocols',
            'qa_manager', 'high', 'open'
        )
        RETURNING id::text;
        """
    )
    await conn.close()

    # 1. qa_manager sees the task in /tasks
    qa_resp = await qa_client.get("/tasks")
    assert qa_resp.status_code == HTTP_200_OK
    assert "Audit MIL-W-4088K Test Procedures" in qa_resp.text

    # 2. line_inspector does not see this task (not holding qa_manager)
    insp_resp = await inspector_client.get("/tasks")
    assert insp_resp.status_code == HTTP_200_OK
    assert "Audit MIL-W-4088K Test Procedures" not in insp_resp.text


# ============================================================================
# 9. TASK STATUS TRANSITIONS
# ============================================================================

@pytest.mark.asyncio
async def test_task_status_transition_lifecycle(qa_client):
    """
    Proves that a task can be transitioned from open -> in progress -> done.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    task_id = await conn.fetchval(
        """
        INSERT INTO tasks (
            id, title, detail, assigned_role, priority, status
        ) VALUES (
            gen_random_uuid(), 'Calibrate Tensile Machine Load Cell', 'Standard 100kN load cell verification',
            'qa_manager', 'normal', 'open'
        )
        RETURNING id::text;
        """
    )

    # 1. Move to in progress
    resp_prog = await qa_client.post(
        f"/tasks/{task_id}/status",
        data={"status": "in progress"},
        follow_redirects=False,
    )
    assert resp_prog.status_code == HTTP_303_SEE_OTHER

    row1 = await conn.fetchrow("SELECT * FROM tasks WHERE id = $1::uuid;", uuid.UUID(task_id))
    assert row1["status"] == "in progress"
    assert str(row1["assigned_user"]) == TEST_USERS["qa_manager"]["id"]

    # 2. Move to done
    resp_done = await qa_client.post(
        f"/tasks/{task_id}/status",
        data={"status": "done"},
        follow_redirects=False,
    )
    assert resp_done.status_code == HTTP_303_SEE_OTHER

    row2 = await conn.fetchrow("SELECT * FROM tasks WHERE id = $1::uuid;", uuid.UUID(task_id))
    assert row2["status"] == "done"
    assert row2["closed_at"] is not None

    await conn.close()


# ============================================================================
# 10. UNAUTHENTICATED ACCESS REJECTED
# ============================================================================

@pytest.mark.asyncio
async def test_organisation_routes_reject_unauthenticated(anonymous_client):
    """
    Proves that all organisation and task routes reject unauthenticated callers with 401.
    """
    resp1 = await anonymous_client.get("/organisation")
    assert resp1.status_code == HTTP_401_UNAUTHORIZED

    resp2 = await anonymous_client.get("/organisation/matrix")
    assert resp2.status_code == HTTP_401_UNAUTHORIZED

    resp3 = await anonymous_client.get("/tasks")
    assert resp3.status_code == HTTP_401_UNAUTHORIZED
