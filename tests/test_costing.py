"""
Tests for Costing & Financial Estimation Module -- SNM Works
=============================================================================
1. Cost sheet creation and unit manufacturing cost computation.
2. Parameterized pricing formula stub ("pending confirmation" behavior).
3. Draft update capability before financial sign-off.
4. Separate approve permission (costing_analyst denied 403 on approve).
5. Non-Negotiable Rule 6 Self-Approval constraint (creator cannot approve).
6. Independent financial approval transition to 'Approved'.
7. Database Trigger Cost Freeze: Raw SQL update on approved record rejected.
8. Router update rejection on approved/locked cost sheet.
9. Confidentiality / RBAC isolation (sales, export, and shop-floor denied 403).
10. Unauthenticated access rejected with 401.
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
from textiles.costing import calculate_manufacturing_cost, calculate_yarn_cost, calculate_selling_price


# Helper to create a test job
async def get_or_create_costing_job(conn: asyncpg.Connection, suffix: str = "01") -> str:
    cust_id = await conn.fetchval("SELECT id FROM customers LIMIT 1;")
    if not cust_id:
        cust_id = await conn.fetchval("INSERT INTO customers (id, name, active) VALUES (gen_random_uuid(), 'OFK Ordnance Factory Kanpur', true) RETURNING id;")
    job_no = f"JOB-CST-{suffix}-{uuid.uuid4().hex[:6].upper()}"
    return await conn.fetchval(
        "INSERT INTO jobs (id, job_no, customer_id, product, qty_ordered, unit, status) "
        "VALUES (gen_random_uuid(), $1, $2::uuid, 'MIL-W-4088K Type VIII Webbing', 1000, 'm', 'In Production') RETURNING id::text;",
        job_no, cust_id,
    )


def test_yarn_and_manufacturing_cost_calculation():
    """
    Proves pure calculation of raw yarn cost, process conversion, and overheads.
    """
    # 45 g/m @ ₹280/kg with 5% waste -> (45/1000) * 280 * 1.05 = ₹13.23 / m
    yarn_cost = calculate_yarn_cost(yarn_consumption_gpm=45.0, yarn_rate_per_kg=280.0, wastage_pct=5.0)
    assert abs(yarn_cost - 13.23) < 0.001

    costs = calculate_manufacturing_cost(
        yarn_cost=yarn_cost,
        dyeing=2.50,
        coating=1.20,
        labour=3.80,
        overhead=1.50,
        packing=0.80,
        freight=0.60,
    )
    # process_cost = 2.50 + 1.20 + 3.80 = 7.50
    # overhead_cost = 1.50 + 0.80 + 0.60 = 2.90
    # total_mfg_cost = 13.23 + 7.50 + 2.90 = 23.63
    assert abs(costs["process_cost"] - 7.50) < 0.001
    assert abs(costs["overhead_cost"] - 2.90) < 0.001
    assert abs(costs["total_manufacturing_cost"] - 23.63) < 0.001


def test_pricing_formula_pending_stub():
    """
    Per user directive: Withholds calculation and returns an explicit pending sentinel
    when method is None.
    """
    res = calculate_selling_price(manufacturing_cost=23.63, margin_pct=15.0, method=None)
    assert res["status"] == "pending_formula"
    assert res["message"] == "Pricing formula pending confirmation"
    assert res["selling_price"] is None
    assert res["margin_amount"] is None


# ============================================================================
# 2. COST SHEET CREATION & DRAFT UPDATES
# ============================================================================

@pytest.mark.asyncio
async def test_costing_creation_and_detail(costing_analyst_client):
    """
    Proves that costing_analyst can prepare a draft cost sheet and view its breakdown.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await get_or_create_costing_job(conn, suffix="CREATE")
    await conn.close()

    # 1. Create draft cost sheet
    resp = await costing_analyst_client.post(
        "/costing",
        data={
            "job_id": job_id,
            "qty": "1000",
            "unit": "m",
            "yarn_rate": "280.00",
            "yarn_consumption": "45.00",
            "wastage_pct": "5.00",
            "dyeing": "2.50",
            "coating": "1.20",
            "labour": "3.80",
            "overhead": "1.50",
            "packing": "0.80",
            "freight": "0.60",
            "margin_pct": "15.00",
        },
        follow_redirects=False,
    )
    assert resp.status_code == HTTP_303_SEE_OTHER

    # 2. View detail page
    detail_resp = await costing_analyst_client.get(f"/costing/{job_id}")
    assert detail_resp.status_code == HTTP_200_OK
    assert "COST SHEET:" in detail_resp.text
    assert "Draft" in detail_resp.text
    assert "Pricing formula pending confirmation" in detail_resp.text
    assert "23.63" in detail_resp.text  # Unit manufacturing cost


@pytest.mark.asyncio
async def test_draft_costing_update(costing_analyst_client):
    """
    Proves that draft cost parameters can be modified prior to approval.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await get_or_create_costing_job(conn, suffix="UPDATE")
    await conn.close()

    # Create
    await costing_analyst_client.post(
        "/costing",
        data={"job_id": job_id, "qty": "500", "unit": "m", "yarn_rate": "250.00", "yarn_consumption": "40.00"},
        follow_redirects=False,
    )

    # Update
    upd_resp = await costing_analyst_client.post(
        f"/costing/{job_id}",
        data={
            "qty": "600",
            "unit": "m",
            "yarn_rate": "270.00",
            "yarn_consumption": "42.00",
            "wastage_pct": "4.00",
            "dyeing": "2.00",
            "coating": "1.00",
            "labour": "3.00",
            "overhead": "1.00",
            "packing": "0.50",
            "freight": "0.50",
            "margin_pct": "20.00",
        },
        follow_redirects=False,
    )
    assert upd_resp.status_code == HTTP_303_SEE_OTHER

    # Verify in DB
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    row = await conn.fetchrow("SELECT qty, yarn_rate FROM costing WHERE job_id = $1::uuid;", uuid.UUID(job_id))
    assert float(row["qty"]) == 600.0
    assert float(row["yarn_rate"]) == 270.0
    await conn.close()


# ============================================================================
# 3. APPROVE PERMISSION SEPARATION & SELF-APPROVAL BLOCK
# ============================================================================

@pytest.mark.asyncio
async def test_costing_analyst_cannot_approve_cost_sheet(costing_analyst_client):
    """
    Proves that costing_analyst (holding costing.create/update) is denied 403
    on /approve (which requires costing.approve).
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await get_or_create_costing_job(conn, suffix="NOAPPR")
    await conn.close()

    await costing_analyst_client.post(
        "/costing",
        data={"job_id": job_id, "qty": "500", "unit": "m", "yarn_rate": "280"},
        follow_redirects=False,
    )

    appr_resp = await costing_analyst_client.post(f"/costing/{job_id}/approve", follow_redirects=False)
    assert appr_resp.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit approve on costing" in appr_resp.text


@pytest.mark.asyncio
async def test_no_self_approval_database_constraint(finance_client):
    """
    Non-Negotiable Rule 6: If chief_financial creates a cost sheet draft,
    they cannot approve their own record.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await get_or_create_costing_job(conn, suffix="SELF")
    cfo_id = TEST_USERS["chief_financial"]["id"]

    # 1. CFO creates draft cost sheet
    create_resp = await finance_client.post(
        "/costing",
        data={"job_id": job_id, "qty": "1000", "unit": "m", "yarn_rate": "300"},
        follow_redirects=False,
    )
    assert create_resp.status_code == HTTP_303_SEE_OTHER

    # 2. CFO attempts self-approval -> 400 Bad Request
    appr_resp = await finance_client.post(f"/costing/{job_id}/approve", follow_redirects=False)
    assert appr_resp.status_code == HTTP_400_BAD_REQUEST
    assert "Segregation of duties violation" in appr_resp.text

    # 3. Direct SQL update trying self-approval triggers database constraint
    with pytest.raises(CheckViolationError):
        await conn.execute(
            """
            UPDATE costing
            SET approved_by = created_by, status = 'Approved'
            WHERE job_id = $1::uuid;
            """,
            uuid.UUID(job_id),
        )
    await conn.close()


@pytest.mark.asyncio
async def test_independent_executive_approval_succeeds(costing_analyst_client, finance_client):
    """
    Proves that chief_financial can successfully approve a cost sheet prepared by costing_analyst.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await get_or_create_costing_job(conn, suffix="INDEP")
    await conn.close()

    # 1. Analyst prepares cost sheet
    await costing_analyst_client.post(
        "/costing",
        data={"job_id": job_id, "qty": "1000", "unit": "m", "yarn_rate": "285.00"},
        follow_redirects=False,
    )

    # 2. CFO approves
    appr_resp = await finance_client.post(f"/costing/{job_id}/approve", follow_redirects=False)
    assert appr_resp.status_code == HTTP_303_SEE_OTHER

    # 3. Verify in database
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    row = await conn.fetchrow("SELECT status, approved_by, approved_at FROM costing WHERE job_id = $1::uuid;", uuid.UUID(job_id))
    assert row["status"] == "Approved"
    assert str(row["approved_by"]) == TEST_USERS["chief_financial"]["id"]
    assert row["approved_at"] is not None
    await conn.close()


# ============================================================================
# 4. DATABASE COST FREEZE TRIGGER (RAW SQL TEST)
# ============================================================================

@pytest.mark.asyncio
async def test_database_freeze_trigger_blocks_raw_sql_update_on_approved_costing(
    costing_analyst_client, finance_client
):
    """
    Proves that PostgreSQL trigger trg_freeze_approved_costing:
    1. Permits Draft -> Approved status transition.
    2. Rejects modifications to cost fields when status is Approved (frozen).
    3. Permits Approved -> Archived status transition (non-cost field change).
    4. Rejects modifications to cost fields when status is Archived (also frozen).
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await get_or_create_costing_job(conn, suffix="FREEZE")
    cfo_id = TEST_USERS["chief_financial"]["id"]

    try:
        # 1. Create Draft cost sheet
        await costing_analyst_client.post(
            "/costing",
            data={"job_id": job_id, "qty": "1000", "unit": "m", "yarn_rate": "280.00"},
            follow_redirects=False,
        )

        # 2. Update to Approved -> succeeds
        await conn.execute(
            "UPDATE costing SET status = 'Approved', approved_by = $1::uuid, approved_at = now() WHERE job_id = $2::uuid;",
            uuid.UUID(cfo_id), uuid.UUID(job_id),
        )

        # 3. Attempt update to cost field (qty) on Approved -> raises CheckViolationError (frozen)
        with pytest.raises(CheckViolationError) as excinfo1:
            await conn.execute("UPDATE costing SET qty = 2000 WHERE job_id = $1::uuid;", uuid.UUID(job_id))
        assert "frozen" in str(excinfo1.value).lower() or "Cannot modify cost parameters" in str(excinfo1.value)

        # 4. Attempt router update on Approved -> 400 Bad Request
        resp = await costing_analyst_client.post(
            f"/costing/{job_id}",
            data={"qty": "1000", "unit": "m", "yarn_rate": "400.00"},
            follow_redirects=False,
        )
        assert resp.status_code == HTTP_400_BAD_REQUEST

        # 5. Update only status to Archived -> succeeds (non-cost field change)
        await conn.execute("UPDATE costing SET status = 'Archived' WHERE job_id = $1::uuid;", uuid.UUID(job_id))
        row = await conn.fetchrow("SELECT status FROM costing WHERE job_id = $1::uuid;", uuid.UUID(job_id))
        assert row["status"] == "Archived"

        # 6. Attempt update to cost field (yarn_rate) on Archived -> also raises CheckViolationError (frozen)
        with pytest.raises(CheckViolationError) as excinfo2:
            await conn.execute("UPDATE costing SET yarn_rate = 350.00 WHERE job_id = $1::uuid;", uuid.UUID(job_id))
        assert "frozen" in str(excinfo2.value).lower() or "Cannot modify cost parameters" in str(excinfo2.value)

    finally:
        await conn.execute("DELETE FROM costing WHERE job_id = $1::uuid;", uuid.UUID(job_id))
        await conn.execute("DELETE FROM jobs WHERE id = $1::uuid;", uuid.UUID(job_id))
        await conn.close()


# ============================================================================
# 5. CONFIDENTIALITY / RBAC ISOLATION
# ============================================================================

@pytest.mark.asyncio
async def test_commercial_and_shopfloor_roles_denied_costing_access(
    sales_client, export_client, supervisor_client, store_client, inspector_client
):
    """
    Non-Negotiable Rule: Product costing is strictly confidential to FIN and EXEC roles.
    Sales, export, supervisors, storekeepers, and inspectors are refused 403 Forbidden.
    """
    # 1. sales_executive denied
    r1 = await sales_client.get("/costing")
    assert r1.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit read on costing" in r1.text

    # 2. export_executive denied
    r2 = await export_client.get("/costing")
    assert r2.status_code == HTTP_403_FORBIDDEN

    # 3. shift_supervisor denied
    r3 = await supervisor_client.get("/costing")
    assert r3.status_code == HTTP_403_FORBIDDEN

    # 4. store_keeper denied
    r4 = await store_client.get("/costing")
    assert r4.status_code == HTTP_403_FORBIDDEN

    # 5. line_inspector denied
    r5 = await inspector_client.get("/costing")
    assert r5.status_code == HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_permitted_roles_can_access_costing(costing_analyst_client, finance_client):
    """
    Proves that costing_analyst and chief_financial receive 200 OK on /costing and /costing/new.
    """
    resp1 = await costing_analyst_client.get("/costing")
    assert resp1.status_code == HTTP_200_OK
    assert "PRODUCT COSTING & ESTIMATION REGISTER" in resp1.text

    resp2 = await costing_analyst_client.get("/costing/new")
    assert resp2.status_code == HTTP_200_OK

    resp3 = await finance_client.get("/costing")
    assert resp3.status_code == HTTP_200_OK


# ============================================================================
# 6. UNAUTHENTICATED ACCESS REJECTED
# ============================================================================

@pytest.mark.asyncio
async def test_costing_routes_reject_unauthenticated(anonymous_client):
    """
    Proves unauthenticated requests receive 401 Unauthorized.
    """
    resp = await anonymous_client.get("/costing")
    assert resp.status_code == HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_costing_pagination(costing_analyst_client):
    """
    Pagination Test: Seeds 28 costing sheets with linked jobs, queries page 1 (25 rows) and page 2 (3 rows).
    Verifies:
    1. page 1 contains exactly 25 rows and total_count = 28.
    2. page 2 contains exactly 3 rows.
    3. Separate SQL aggregates (total_approved, total_draft, total_mfg_cost_sum) compute full totals across all pages.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:8].upper()
    user_id = TEST_USERS["costing_analyst"]["id"]
    job_ids = []

    try:
        cust_id = await conn.fetchval("SELECT id FROM customers LIMIT 1;")
        if not cust_id:
            cust_id = await conn.fetchval("INSERT INTO customers (id, name, active) VALUES (gen_random_uuid(), 'Test Customer', true) RETURNING id;")

        for i in range(1, 29):
            job_id = await conn.fetchval(
                """
                INSERT INTO jobs (id, job_no, customer_id, product, qty_ordered, unit, status)
                VALUES (gen_random_uuid(), $1, $2::uuid, $3, 1000, 'm', 'In Production')
                RETURNING id::text;
                """,
                f"JOB-PAG-{uid}-{i:02d}",
                cust_id,
                f"Costing Product {uid} #{i:02d}",
            )
            job_ids.append(job_id)

            await conn.execute(
                """
                INSERT INTO costing (
                    job_id, qty, unit, yarn_rate, yarn_consumption, wastage_pct,
                    dyeing, coating, labour, overhead, packing, freight, margin_pct,
                    status, created_by
                ) VALUES (
                    $1::uuid, 1000, 'm', 280.0, 45.0, 5.0,
                    2.50, 1.20, 3.80, 1.50, 0.80, 0.60, 15.0,
                    $2, $3::uuid
                );
                """,
                uuid.UUID(job_id),
                "Draft" if i <= 18 else "Approved",
                uuid.UUID(user_id),
            )

        # Page 1
        resp1 = await costing_analyst_client.get(f"/costing?q={uid}&page=1&page_size=25")
        assert resp1.status_code == HTTP_200_OK
        assert "Showing <strong>25</strong> of <strong>28</strong> cost sheet" in resp1.text
        assert "Page 1 of 2" in resp1.text
        assert resp1.text.count(f"JOB-PAG-{uid}") == 25
        # Total draft: 18, Total approved: 10
        assert ">18</div>" in resp1.text
        assert ">10</div>" in resp1.text
        # Cumulative manufacturing cost across all 28 sheets:
        # Unit cost = (45/1000 * 280 * 1.05) + (2.50 + 1.20 + 3.80) + (1.50 + 0.80 + 0.60) = 13.23 + 7.50 + 2.90 = 23.63
        # Per sheet = 23.63 * 1000 = 23,630.00
        # Total across 28 sheets = 28 * 23,630.00 = 661,640.00
        assert "661640" in resp1.text or "661,640" in resp1.text

        # Page 2
        resp2 = await costing_analyst_client.get(f"/costing?q={uid}&page=2&page_size=25")
        assert resp2.status_code == HTTP_200_OK
        assert "Showing <strong>3</strong> of <strong>28</strong> cost sheet" in resp2.text
        assert "Page 2 of 2" in resp2.text
        assert resp2.text.count(f"JOB-PAG-{uid}") == 3
        assert ">18</div>" in resp2.text
        assert "661640" in resp2.text or "661,640" in resp2.text
    finally:
        for j_id in job_ids:
            await conn.execute("DELETE FROM costing WHERE job_id = $1::uuid;", uuid.UUID(j_id))
            await conn.execute("DELETE FROM jobs WHERE id = $1::uuid;", uuid.UUID(j_id))
        await conn.close()

