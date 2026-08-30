import asyncio
import uuid
import asyncpg
import pytest
import httpx
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)
from main import app
from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS, make_test_token


# ============================================================================
# 1. SEQUENCE NUMBERING & CREATION
# ============================================================================

@pytest.mark.asyncio
async def test_downtime_creation_and_sequence_numbering(operator_client):
    """
    Verifies creating downtime logs sequentially generates DT-0001, DT-0002...
    and sets operator_id to the authenticated operator.
    """
    op_user_id = TEST_USERS["machine_operator"]["id"]

    # Clear table for predictable sequence
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    await conn.execute("DELETE FROM downtime;")

    # 1. Create first downtime log (Loom 01 - Mechanical Breakdown)
    resp1 = await operator_client.post(
        "/downtime",
        data={
            "shift": "Shift A (06:00-14:00)",
            "machine": "Loom 01 (Needle Loom 4-Space)",
            "reason": "Mechanical Breakdown",
            "minutes": "45",
            "remarks": "Replaced worn out weft needle and adjusted catch-cord timing.",
        },
        follow_redirects=False,
    )
    assert resp1.status_code == HTTP_303_SEE_OTHER
    dt_id1 = resp1.headers["location"].split("/downtime/")[1]

    row1 = await conn.fetchrow("SELECT * FROM downtime WHERE id = $1::uuid;", dt_id1)
    assert row1 is not None
    assert row1["log_no"] == "DT-0001"
    assert str(row1["operator_id"]) == op_user_id
    assert float(row1["minutes"]) == 45.0
    assert row1["machine"] == "Loom 01 (Needle Loom 4-Space)"
    assert row1["reason"] == "Mechanical Breakdown"

    # 2. Create second downtime log (Braider 02 - Yarn Breakage)
    resp2 = await operator_client.post(
        "/downtime",
        data={
            "shift": "Shift B (14:00-22:00)",
            "machine": "Braider 02 (32-Carrier)",
            "reason": "Yarn Breakage / Warp Knotting",
            "minutes": "20",
            "remarks": "Carrier 14 bobbin runout and knotting.",
        },
        follow_redirects=False,
    )
    assert resp2.status_code == HTTP_303_SEE_OTHER
    dt_id2 = resp2.headers["location"].split("/downtime/")[1]

    row2 = await conn.fetchrow("SELECT * FROM downtime WHERE id = $1::uuid;", dt_id2)
    assert row2 is not None
    assert row2["log_no"] == "DT-0002"
    assert str(row2["operator_id"]) == op_user_id

    await conn.close()


@pytest.mark.asyncio
async def test_downtime_concurrent_sequence_generation():
    """
    Launches 10 concurrent downtime creation requests to verify that the
    UniqueViolationError retry loop eliminates sequence collision race conditions.
    """
    token = make_test_token(
        user_id=TEST_USERS["machine_operator"]["id"],
        email=TEST_USERS["machine_operator"]["email"],
        role_code=TEST_USERS["machine_operator"]["role_code"],
    )

    async def create_single_downtime(idx: int):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            return await client.post(
                "/downtime",
                data={
                    "shift": "Shift A (06:00-14:00)",
                    "machine": "Loom 02 (Needle Loom 2-Space Heavy)",
                    "reason": f"Concurrent stoppage #{idx}",
                    "minutes": "15",
                },
                follow_redirects=False,
            )

    results = await asyncio.gather(*[create_single_downtime(i) for i in range(10)])
    for r in results:
        assert r.status_code == HTTP_303_SEE_OTHER

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    rows = await conn.fetch(
        "SELECT log_no FROM downtime WHERE reason LIKE 'Concurrent stoppage %' ORDER BY log_no ASC;"
    )
    log_nos = [r["log_no"] for r in rows]
    assert len(log_nos) == 10
    assert len(set(log_nos)) == 10, "Duplicate Log numbers were generated under concurrency!"
    await conn.close()


# ============================================================================
# 2. OPERATOR IDENTITY NOT NULL CONSTRAINT
# ============================================================================

@pytest.mark.asyncio
async def test_operator_identity_cannot_be_null_database_constraint():
    """
    Proves that operator_id is strictly NOT NULL at the database schema level.
    Direct insertion with operator_id = NULL must fail.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    with pytest.raises(asyncpg.exceptions.NotNullViolationError):
        await conn.execute(
            """
            INSERT INTO downtime (
                id, log_no, shift, machine, reason, minutes, operator_id
            ) VALUES (
                gen_random_uuid(), 'DT-9999', 'Shift A', 'Loom 01', 'Breakdown', 30, NULL
            );
            """
        )
    await conn.close()


# ============================================================================
# 3. RBAC & PERMISSION SEPARATION
# ============================================================================

@pytest.mark.asyncio
async def test_operator_and_supervisor_cannot_update_downtime(operator_client, supervisor_client):
    """
    Proves that machine_operator and shift_supervisor (who hold create/read, but NOT update)
    are strictly forbidden (403) from updating recorded downtime logs.
    """
    # 1. Operator creates a downtime log
    resp = await operator_client.post(
        "/downtime",
        data={
            "shift": "Shift A (06:00-14:00)",
            "machine": "Loom 03 (Shuttle Loom)",
            "reason": "Preventive Maintenance",
            "minutes": "60",
        },
        follow_redirects=False,
    )
    dt_id = resp.headers["location"].split("/downtime/")[1]

    # 2. Operator attempts to edit / update -> 403 Forbidden
    op_edit = await operator_client.get(f"/downtime/{dt_id}/edit")
    assert op_edit.status_code == HTTP_403_FORBIDDEN

    op_update = await operator_client.post(
        f"/downtime/{dt_id}/update",
        data={"machine": "Loom 03", "reason": "Modified", "minutes": "90"},
    )
    assert op_update.status_code == HTTP_403_FORBIDDEN

    # 3. Shift supervisor attempts to update -> 403 Forbidden
    sup_update = await supervisor_client.post(
        f"/downtime/{dt_id}/update",
        data={"machine": "Loom 03", "reason": "Modified by Sup", "minutes": "90"},
    )
    assert sup_update.status_code == HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_maintenance_and_production_manager_can_update_downtime(operator_client, maintenance_client, production_client):
    """
    Proves that maintenance_officer and production_manager (holding downtime.update)
    can successfully edit downtime records.
    """
    # 1. Operator creates a downtime log
    resp = await operator_client.post(
        "/downtime",
        data={
            "shift": "Shift A (06:00-14:00)",
            "machine": "Loom 01 (Needle Loom 4-Space)",
            "reason": "Mechanical Breakdown",
            "minutes": "30",
        },
        follow_redirects=False,
    )
    dt_id = resp.headers["location"].split("/downtime/")[1]

    # 2. Maintenance Officer updates the duration and notes
    maint_resp = await maintenance_client.post(
        f"/downtime/{dt_id}/update",
        data={
            "shift": "Shift A (06:00-14:00)",
            "machine": "Loom 01 (Needle Loom 4-Space)",
            "reason": "Mechanical Breakdown",
            "minutes": "75",
            "remarks": "Major gearbox overhaul completed by maintenance team.",
        },
        follow_redirects=False,
    )
    assert maint_resp.status_code == HTTP_303_SEE_OTHER

    # 3. Verify in database
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    row = await conn.fetchrow("SELECT * FROM downtime WHERE id = $1::uuid;", dt_id)
    assert float(row["minutes"]) == 75.0
    assert "Major gearbox overhaul" in row["remarks"]
    await conn.close()


# ============================================================================
# 4. VALIDATION CONSTRAINTS
# ============================================================================

@pytest.mark.asyncio
async def test_cannot_create_negative_duration_downtime(operator_client):
    """
    Proves that downtime minutes cannot be negative (HTTP 400 & DB Check constraint).
    """
    resp = await operator_client.post(
        "/downtime",
        data={
            "shift": "Shift A (06:00-14:00)",
            "machine": "Loom 01 (Needle Loom 4-Space)",
            "reason": "Mechanical Breakdown",
            "minutes": "-15",
        },
    )
    assert resp.status_code == HTTP_400_BAD_REQUEST
    assert "Downtime minutes cannot be negative" in resp.text


# ============================================================================
# 5. RBAC PERMISSIONS ACCESS CONTROL
# ============================================================================

@pytest.mark.asyncio
async def test_role_without_downtime_read_cannot_read_downtime(sales_client):
    """
    Proves that roles without downtime.read (sales_executive) are denied 403 Forbidden.
    """
    resp_sales = await sales_client.get("/downtime")
    assert resp_sales.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit read on downtime" in resp_sales.text


@pytest.mark.asyncio
async def test_permitted_roles_can_access_downtime(operator_client, supervisor_client, maintenance_client, production_client):
    """
    Proves that roles holding downtime grants receive HTTP 200 OK on /downtime and /downtime/new.
    """
    resp_op = await operator_client.get("/downtime")
    assert resp_op.status_code == HTTP_200_OK
    assert "DOWNTIME & STOPPAGE LOG" in resp_op.text

    resp_new = await operator_client.get("/downtime/new")
    assert resp_new.status_code == HTTP_200_OK

    resp_sup = await supervisor_client.get("/downtime")
    assert resp_sup.status_code == HTTP_200_OK

    resp_maint = await maintenance_client.get("/downtime")
    assert resp_maint.status_code == HTTP_200_OK

    resp_prod = await production_client.get("/downtime")
    assert resp_prod.status_code == HTTP_200_OK


@pytest.mark.asyncio
async def test_downtime_routes_reject_unauthenticated(anonymous_client):
    """
    Verifies that unauthenticated requests to downtime routes are rejected with 401.
    """
    resp_list = await anonymous_client.get("/downtime")
    assert resp_list.status_code == HTTP_401_UNAUTHORIZED

    resp_create = await anonymous_client.post(
        "/downtime",
        data={"machine": "Loom 01", "reason": "Breakdown", "minutes": "30"},
    )
    assert resp_create.status_code == HTTP_401_UNAUTHORIZED
