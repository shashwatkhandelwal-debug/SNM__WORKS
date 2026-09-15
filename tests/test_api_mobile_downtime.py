import pytest
import asyncpg
from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS


@pytest.mark.asyncio
async def test_mobile_downtime_options(operator_client):
    """
    Verifies GET /api/v1/downtime/options returns machines, shifts, reasons, and active jobs.
    """
    resp = await operator_client.get("/api/v1/downtime/options")
    assert resp.status_code == 200
    data = resp.json()
    assert "machines" in data
    assert "shifts" in data
    assert "reasons" in data
    assert "jobs" in data
    assert len(data["machines"]) > 0
    assert "Shift A (06:00-14:00)" in data["shifts"]
    assert "Mechanical Breakdown" in data["reasons"]


@pytest.mark.asyncio
async def test_mobile_downtime_create_log(operator_client):
    """
    Verifies POST /api/v1/downtime/logs creates a downtime log and returns formatted duration.
    Confirms persisted row and operator identity via direct database SELECT.
    """
    payload = {
        "shift": "Shift A (06:00-14:00)",
        "machine": "Loom 01 (Needle Loom 4-Space)",
        "reason": "Mechanical Breakdown",
        "minutes": 135.0,
        "remarks": "Mobile telemetry log: replaced warp needle.",
    }
    resp = await operator_client.post("/api/v1/downtime/logs", json=payload)
    assert resp.status_code == 201
    result = resp.json()
    assert "id" in result
    assert result["log_no"].startswith("DT-")
    assert result["formatted_duration"] == "2h 15m"
    assert result["machine"] == "Loom 01 (Needle Loom 4-Space)"
    assert result["reason"] == "Mechanical Breakdown"

    # Confirm directly in the database
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    row = await conn.fetchrow(
        "SELECT id::text, log_no, machine, reason, minutes, operator_id::text, remarks FROM downtime WHERE id = $1::uuid;",
        result["id"],
    )
    assert row is not None
    assert row["log_no"] == result["log_no"]
    assert row["operator_id"] == TEST_USERS["machine_operator"]["id"]
    assert float(row["minutes"]) == 135.0
    assert row["machine"] == "Loom 01 (Needle Loom 4-Space)"
    assert row["reason"] == "Mechanical Breakdown"
    assert row["remarks"] == "Mobile telemetry log: replaced warp needle."
    await conn.close()


@pytest.mark.asyncio
async def test_mobile_downtime_create_with_linked_job(operator_client):
    """
    Verifies linking an active job to a downtime log via mobile API.
    """
    import uuid
    uid_suffix = uuid.uuid4().hex[:6].upper()
    job_no = f"JOB-DT-MOB-{uid_suffix}"

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
        VALUES ($1, CURRENT_DATE, 'Type IV Webbing', 500, 'm', 0, 'In Progress')
        RETURNING id::text;
        """,
        job_no,
    )
    await conn.close()

    payload = {
        "shift": "Shift B (14:00-22:00)",
        "machine": "Loom 02 (Needle Loom 2-Space Heavy)",
        "reason": "Yarn Breakage / Warp Knotting",
        "minutes": 45.0,
        "job_id": job_id,
        "remarks": "Carrier yarn break during weaving.",
    }
    resp = await operator_client.post("/api/v1/downtime/logs", json=payload)
    assert resp.status_code == 201
    result = resp.json()
    assert result["job_id"] == job_id
    assert result["formatted_duration"] == "45m"

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    row = await conn.fetchrow("SELECT job_id::text FROM downtime WHERE id = $1::uuid;", result["id"])
    assert row is not None
    assert row["job_id"] == job_id
    await conn.close()


@pytest.mark.asyncio
async def test_mobile_downtime_list_logs(operator_client):
    """
    Verifies GET /api/v1/downtime/logs returns recent stoppage logs with formatted durations.
    """
    resp = await operator_client.get("/api/v1/downtime/logs?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "logs" in data
    assert isinstance(data["logs"], list)
    if data["count"] > 0:
        first = data["logs"][0]
        assert "log_no" in first
        assert "formatted_duration" in first


@pytest.mark.asyncio
async def test_mobile_downtime_validation_negative_minutes(operator_client):
    """
    Verifies 400 Bad Request when negative downtime minutes are submitted.
    """
    payload = {
        "machine": "Loom 01 (Needle Loom 4-Space)",
        "reason": "Mechanical Breakdown",
        "minutes": -30.0,
    }
    resp = await operator_client.post("/api/v1/downtime/logs", json=payload)
    assert resp.status_code == 400
    assert "negative" in resp.text.lower()


@pytest.mark.asyncio
async def test_mobile_downtime_auth_boundaries(sales_client, anonymous_client):
    """
    Verifies 401 on anonymous and 403 on role lacking downtime permissions.
    """
    payload = {
        "machine": "Loom 01 (Needle Loom 4-Space)",
        "reason": "Mechanical Breakdown",
        "minutes": 30.0,
    }
    # Unauthenticated -> 401
    resp_anon = await anonymous_client.post("/api/v1/downtime/logs", json=payload)
    assert resp_anon.status_code == 401

    resp_anon_opts = await anonymous_client.get("/api/v1/downtime/options")
    assert resp_anon_opts.status_code == 401

    # Sales executive lacks downtime.create and downtime.read -> 403
    resp_sales_post = await sales_client.post("/api/v1/downtime/logs", json=payload)
    assert resp_sales_post.status_code == 403

    resp_sales_get = await sales_client.get("/api/v1/downtime/options")
    assert resp_sales_get.status_code == 403
