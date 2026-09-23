import pytest
from datetime import date, timedelta
from routers.jobs import validate_status_transition, get_next_job_no, MEM_JOBS


def test_status_transition_rules():
    # 1. Forward progression is allowed
    ok, _ = validate_status_transition("Planned", "In progress")
    assert ok is True

    ok, _ = validate_status_transition("In progress", "Complete")
    assert ok is True

    ok, _ = validate_status_transition("Complete", "Despatched")
    assert ok is True

    # Same status is allowed
    ok, _ = validate_status_transition("Planned", "Planned")
    assert ok is True

    # 2. Backward progression is blocked
    ok, msg = validate_status_transition("Complete", "Planned")
    assert ok is False
    assert "cannot move backward" in msg

    ok, msg = validate_status_transition("Despatched", "In progress")
    assert ok is False
    assert "cannot move backward" in msg

    # 3. Cancelled is allowed from any active state
    ok, _ = validate_status_transition("Planned", "Cancelled")
    assert ok is True

    ok, _ = validate_status_transition("In progress", "Cancelled")
    assert ok is True

    ok, _ = validate_status_transition("Complete", "Cancelled")
    assert ok is True

    # 4. Moving away from Cancelled is blocked
    ok, msg = validate_status_transition("Cancelled", "Planned")
    assert ok is False
    assert "Cancelled" in msg


@pytest.mark.asyncio
async def test_job_number_formatting():
    # Empty in-memory test fallback
    MEM_JOBS.clear()
    next_no = await get_next_job_no(None)
    assert next_no == "SNM/26-27/0001"

    # Add mock job SNM/26-27/0011
    MEM_JOBS["test-job-1"] = {"job_no": "SNM/26-27/0011"}
    next_no = await get_next_job_no(None)
    assert next_no == "SNM/26-27/0012"


@pytest.mark.asyncio
async def test_inline_status_update_unpermitted_role_403(inspector_client):
    import uuid
    import asyncpg
    from tests.conftest import LOCAL_TEST_DATABASE_URL
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:6].upper()
    job_id = None
    try:
        job_id = await conn.fetchval(
            """
            INSERT INTO jobs (id, job_no, product, qty_ordered, unit, status, raised_on)
            VALUES (gen_random_uuid(), $1, 'Permission Test Webbing', 100, 'm', 'Planned', CURRENT_DATE)
            RETURNING id::text;
            """,
            f"SNM/TEST-PERM-{uid}",
        )
        # Line inspector has jobs:read but NOT jobs:update
        resp = await inspector_client.post(f"/jobs/{job_id}/status", data={"status": "In progress"})
        assert resp.status_code == 403

        # Assert DB status is completely unchanged
        status_in_db = await conn.fetchval("SELECT status FROM jobs WHERE id::text = $1", job_id)
        assert status_in_db == "Planned"
    finally:
        if job_id:
            await conn.execute("DELETE FROM jobs WHERE id::text = $1", job_id)
        await conn.close()


@pytest.mark.asyncio
async def test_inline_status_update_permitted_role_updates_database(production_client):
    import uuid
    import asyncpg
    from tests.conftest import LOCAL_TEST_DATABASE_URL
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:6].upper()
    job_id = None
    try:
        job_id = await conn.fetchval(
            """
            INSERT INTO jobs (id, job_no, product, qty_ordered, unit, status, raised_on)
            VALUES (gen_random_uuid(), $1, 'Status Update Webbing', 500, 'm', 'Planned', CURRENT_DATE)
            RETURNING id::text;
            """,
            f"SNM/TEST-UPD-{uid}",
        )
        # 1. Successful forward update
        resp = await production_client.post(f"/jobs/{job_id}/status", data={"status": "In progress"})
        assert resp.status_code == 200
        assert "IN PROGRESS" in resp.text

        # Directly query DB to prove write was actually committed
        status_in_db = await conn.fetchval("SELECT status FROM jobs WHERE id::text = $1", job_id)
        assert status_in_db == "In progress"

        # 2. Invalid backward update should return 400
        resp_invalid = await production_client.post(f"/jobs/{job_id}/status", data={"status": "Planned"})
        assert resp_invalid.status_code == 400
        assert "cannot move backward" in resp_invalid.text

        # DB status must remain In progress
        status_after_invalid = await conn.fetchval("SELECT status FROM jobs WHERE id::text = $1", job_id)
        assert status_after_invalid == "In progress"
    finally:
        if job_id:
            await conn.execute("DELETE FROM jobs WHERE id::text = $1", job_id)
        await conn.close()

