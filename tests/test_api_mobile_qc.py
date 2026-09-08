import pytest
import database


@pytest.mark.asyncio
async def test_mobile_qc_options(inspector_client):
    """
    Verifies GET /api/v1/qc/options returns available dropdown values.
    """
    resp = await inspector_client.get("/api/v1/qc/options")
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs" in data
    assert "stages" in data
    assert "limit_kinds" in data
    assert "On-Loom Inspection" in data["stages"]
    assert "minimum" in data["limit_kinds"]


@pytest.mark.asyncio
async def test_mobile_qc_create_check_pass(inspector_client):
    """
    Verifies POST /api/v1/qc/checks creates a check row and PostgreSQL computes PASS verdict.
    """
    payload = {
        "stage": "On-Loom Inspection",
        "parameter": "Breaking Strength",
        "spec_value": 1000.0,
        "actual": 1050.0,
        "limit_type": "minimum",
        "unit": "kgf",
    }
    resp = await inspector_client.post("/api/v1/qc/checks", json=payload)
    assert resp.status_code == 201
    result = resp.json()
    assert "id" in result
    assert result["check_no"].startswith("Q-")
    assert result["verdict"] == "PASS"

    # Confirm row exists in the database
    async with database.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id::text, check_no, parameter, verdict FROM qc_checks WHERE id = $1::uuid",
            result["id"]
        )
        assert row is not None
        assert row["check_no"] == result["check_no"]
        assert row["verdict"] == "PASS"


@pytest.mark.asyncio
async def test_mobile_qc_create_check_fail(inspector_client):
    """
    Verifies POST /api/v1/qc/checks creates a check row and PostgreSQL computes FAIL verdict.
    """
    payload = {
        "stage": "On-Loom Inspection",
        "parameter": "Breaking Strength",
        "spec_value": 1000.0,
        "actual": 850.0,
        "limit_type": "minimum",
        "unit": "kgf",
    }
    resp = await inspector_client.post("/api/v1/qc/checks", json=payload)
    assert resp.status_code == 201
    result = resp.json()
    assert "id" in result
    assert result["verdict"] == "FAIL"

    # Confirm row exists in the database with computed FAIL verdict
    async with database.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id::text, check_no, parameter, verdict FROM qc_checks WHERE id = $1::uuid",
            result["id"]
        )
        assert row is not None
        assert row["check_no"] == result["check_no"]
        assert row["verdict"] == "FAIL"


@pytest.mark.asyncio
async def test_mobile_qc_list_checks(inspector_client):
    """
    Verifies GET /api/v1/qc/checks returns recent checks list.
    """
    resp = await inspector_client.get("/api/v1/qc/checks?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "checks" in data
    assert isinstance(data["checks"], list)


@pytest.mark.asyncio
async def test_mobile_qc_rejects_unauthorized(sales_client, anonymous_client):
    """
    Verifies 401 on anonymous and 403 on role lacking qc.create permission.
    """
    payload = {
        "stage": "On-Loom Inspection",
        "parameter": "Width",
        "spec_value": 44.0,
        "actual": 44.0,
    }
    # Unauthenticated
    resp_anon = await anonymous_client.post("/api/v1/qc/checks", json=payload)
    assert resp_anon.status_code == 401

    # Sales executive lacks qc.create
    resp_sales = await sales_client.post("/api/v1/qc/checks", json=payload)
    assert resp_sales.status_code == 403
