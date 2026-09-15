import uuid
import pytest
import asyncpg
from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS


@pytest.mark.asyncio
async def test_mobile_material_issue_options(store_client):
    """
    Verifies GET /api/v1/materials/issue/options returns active jobs and approved lots.
    """
    resp = await store_client.get("/api/v1/materials/issue/options")
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs" in data
    assert "approved_lots" in data
    assert isinstance(data["jobs"], list)
    assert isinstance(data["approved_lots"], list)


@pytest.mark.asyncio
async def test_mobile_material_issue_success_and_stock_deduction(store_client):
    """
    Verifies POST /api/v1/materials/issue records an issue and database trigger
    atomically decrements yarn_lots.qty_remaining. Confirms via direct database SELECT.
    """
    uid_suffix = uuid.uuid4().hex[:6].upper()
    job_no = f"JOB-MOB-{uid_suffix}"
    lot_no = f"LOT-MOB-{uid_suffix}"

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
        VALUES ($1, CURRENT_DATE, 'MIL-W-4088K Type VIII', 2000, 'm', 0, 'In Progress')
        RETURNING id::text;
        """,
        job_no,
    )
    lot_id = await conn.fetchval(
        """
        INSERT INTO yarn_lots (
            lot_no, supplier_name, yarn_type, denier, qty_received, qty_issued, qc_status
        )
        VALUES ($1, 'Reliance Industries Ltd', 'Nylon 6,6', 840, 500.0, 0.0, 'Approved')
        RETURNING id::text;
        """,
        lot_no,
    )
    await conn.close()

    payload = {
        "job_id": job_id,
        "yarn_lot_id": lot_id,
        "qty_issued": 175.0,
        "unit": "kg",
        "remarks": "Shop-floor material issue via mobile scanner.",
    }
    resp = await store_client.post("/api/v1/materials/issue", json=payload)
    assert resp.status_code == 201
    result = resp.json()
    assert "id" in result
    assert result["issue_no"].startswith("ISS-")
    assert result["qty_issued"] == 175.0
    assert result["remaining_lot_qty"] == 325.0

    # Direct database SELECT verification of issue row and updated yarn lot
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    issue_row = await conn.fetchrow(
        "SELECT id::text, issue_no, job_id::text, yarn_lot_id::text, qty_issued, issued_by::text, remarks FROM job_material_issues WHERE id = $1::uuid;",
        result["id"],
    )
    assert issue_row is not None
    assert issue_row["issue_no"] == result["issue_no"]
    assert issue_row["job_id"] == job_id
    assert issue_row["yarn_lot_id"] == lot_id
    assert float(issue_row["qty_issued"]) == 175.0
    assert issue_row["issued_by"] == TEST_USERS["store_keeper"]["id"]
    assert issue_row["remarks"] == "Shop-floor material issue via mobile scanner."

    lot_row = await conn.fetchrow("SELECT qty_received, qty_issued, qty_remaining FROM yarn_lots WHERE id = $1::uuid;", lot_id)
    assert float(lot_row["qty_received"]) == 500.0
    assert float(lot_row["qty_issued"]) == 175.0
    assert float(lot_row["qty_remaining"]) == 325.0
    await conn.close()


@pytest.mark.asyncio
async def test_mobile_material_issue_quarantine_blocked(store_client):
    """
    Verifies that attempting to issue from a Quarantine lot via mobile is rejected (400) by DB trigger.
    """
    uid_suffix = uuid.uuid4().hex[:6].upper()
    job_no = f"JOB-MOB-Q-{uid_suffix}"
    lot_no = f"LOT-MOB-Q-{uid_suffix}"

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
        VALUES ($1, CURRENT_DATE, 'Type VIII Webbing', 1000, 'm', 0, 'In Progress')
        RETURNING id::text;
        """,
        job_no,
    )
    lot_id = await conn.fetchval(
        """
        INSERT INTO yarn_lots (
            lot_no, supplier_name, yarn_type, denier, qty_received, qty_issued, qc_status
        )
        VALUES ($1, 'SRF Ltd', 'Nylon 6,6', 840, 200.0, 0.0, 'Quarantine')
        RETURNING id::text;
        """,
        lot_no,
    )
    await conn.close()

    payload = {
        "job_id": job_id,
        "yarn_lot_id": lot_id,
        "qty_issued": 50.0,
        "unit": "kg",
    }
    resp = await store_client.post("/api/v1/materials/issue", json=payload)
    assert resp.status_code == 400
    assert 'Only "Approved" lots can be issued' in resp.text or "status" in resp.text


@pytest.mark.asyncio
async def test_mobile_material_issue_overflow_blocked(store_client):
    """
    Verifies that attempting to issue more than remaining balance is rejected (400).
    """
    uid_suffix = uuid.uuid4().hex[:6].upper()
    job_no = f"JOB-MOB-OVR-{uid_suffix}"
    lot_no = f"LOT-MOB-OVR-{uid_suffix}"

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
        VALUES ($1, CURRENT_DATE, 'Type VIII Webbing', 1000, 'm', 0, 'In Progress')
        RETURNING id::text;
        """,
        job_no,
    )
    lot_id = await conn.fetchval(
        """
        INSERT INTO yarn_lots (
            lot_no, supplier_name, yarn_type, denier, qty_received, qty_issued, qc_status
        )
        VALUES ($1, 'SRF Ltd', 'Nylon 6,6', 840, 100.0, 0.0, 'Approved')
        RETURNING id::text;
        """,
        lot_no,
    )
    await conn.close()

    payload = {
        "job_id": job_id,
        "yarn_lot_id": lot_id,
        "qty_issued": 250.0,  # Only 100 available
        "unit": "kg",
    }
    resp = await store_client.post("/api/v1/materials/issue", json=payload)
    assert resp.status_code == 400
    assert "insufficient" in resp.text.lower() or "exceeds" in resp.text.lower() or "remaining" in resp.text.lower()


@pytest.mark.asyncio
async def test_mobile_material_issues_list(store_client):
    """
    Verifies GET /api/v1/materials/issues returns list of recent material issues.
    """
    resp = await store_client.get("/api/v1/materials/issues?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "issues" in data
    assert isinstance(data["issues"], list)


@pytest.mark.asyncio
async def test_mobile_material_issue_auth_boundaries(sales_client, anonymous_client):
    """
    Verifies 401 on anonymous and 403 on role lacking stock.create permissions.
    """
    payload = {
        "job_id": str(uuid.uuid4()),
        "yarn_lot_id": str(uuid.uuid4()),
        "qty_issued": 50.0,
    }
    # Anonymous -> 401
    resp_anon = await anonymous_client.post("/api/v1/materials/issue", json=payload)
    assert resp_anon.status_code == 401

    resp_anon_opts = await anonymous_client.get("/api/v1/materials/issue/options")
    assert resp_anon_opts.status_code == 401

    # Sales executive lacks stock.create and stock.read -> 403
    resp_sales_post = await sales_client.post("/api/v1/materials/issue", json=payload)
    assert resp_sales_post.status_code == 403

    resp_sales_get = await sales_client.get("/api/v1/materials/issue/options")
    assert resp_sales_get.status_code == 403
