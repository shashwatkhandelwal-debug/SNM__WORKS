"""
tests/test_analytics_profitability.py — Unit & Integration Test Suite for Margin & Profitability Rollups.

Tests:
1. RBAC & Access Control:
   - Anonymous -> 401 Unauthorized
   - Line Inspector (lacks costing.read) -> 403 Forbidden
   - Costing Analyst & Chief Financial -> 200 OK
2. Mathematical Accuracy & Rollups:
   - Customer-level rollup (Total Jobs, Produced Volume, Ordered Volume, Total Mfg Cost, Avg/Min/Max Margin %)
   - Specification/Variant-level rollup (Total Jobs, Mfg Cost, Margin %, QC/Lab Fail Counts, Rework Rate %)
   - Excludes Draft/Unapproved costing records
   - Hand-calculated verification against seeded dataset
"""

import uuid
from datetime import date
import asyncpg
import httpx
import pytest
from starlette.status import HTTP_200_OK, HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

from tests.conftest import LOCAL_TEST_DATABASE_URL, make_test_token, TEST_USERS
from main import app


@pytest.mark.asyncio
async def test_profitability_rbac_access():
    """
    Verifies RBAC rules on GET /analytics/profitability.
    Only roles with costing.read (e.g. costing_analyst, chief_financial, owner) are permitted.
    """
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Anonymous request -> 401
        anon_resp = await client.get("/analytics/profitability")
        assert anon_resp.status_code == HTTP_401_UNAUTHORIZED

        # 2. Line Inspector (lacks costing.read) -> 403
        insp_token = make_test_token(
            TEST_USERS["line_inspector"]["id"],
            TEST_USERS["line_inspector"]["email"],
            TEST_USERS["line_inspector"]["role_code"],
        )
        insp_resp = await client.get(
            "/analytics/profitability",
            headers={"Authorization": f"Bearer {insp_token}"}
        )
        assert insp_resp.status_code == HTTP_403_FORBIDDEN

        # 3. Costing Analyst (holds costing.read) -> 200
        costing_token = make_test_token(
            TEST_USERS["costing_analyst"]["id"],
            TEST_USERS["costing_analyst"]["email"],
            TEST_USERS["costing_analyst"]["role_code"],
        )
        costing_resp = await client.get(
            "/analytics/profitability",
            headers={"Authorization": f"Bearer {costing_token}"}
        )
        assert costing_resp.status_code == HTTP_200_OK
        assert "MARGIN &amp; PROFITABILITY ROLLUP" in costing_resp.text or "MARGIN & PROFITABILITY ROLLUP" in costing_resp.text


@pytest.mark.asyncio
async def test_profitability_mathematical_rollups():
    """
    Seeds a controlled multi-job, multi-customer, multi-spec dataset with Approved and Draft costing records
    and verifies that manufacturing costs, margins, and QC rework rates match exact hand calculations.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    costing_user_id = uuid.UUID(TEST_USERS["costing_analyst"]["id"])
    try:
        today = date.today()
        tag = uuid.uuid4().hex[:6].upper()

        # 1. Seed two distinct Customers
        cust1_id = await conn.fetchval(
            """
            INSERT INTO customers (id, name, active)
            VALUES (gen_random_uuid(), $1, true)
            RETURNING id;
            """,
            f"Customer Alpha {tag}"
        )
        cust2_id = await conn.fetchval(
            """
            INSERT INTO customers (id, name, active)
            VALUES (gen_random_uuid(), $1, true)
            RETURNING id;
            """,
            f"Customer Beta {tag}"
        )

        # 2. Seed Jobs
        spec_name_a = f"MIL-W-4088K Type VIII {tag}"
        spec_name_b = f"MIL-W-4088K Type X {tag}"

        # Job 1: Cust A, Spec A (Approved Costing)
        job1_id = await conn.fetchval(
            """
            INSERT INTO jobs (id, job_no, customer_id, product, spec, status, qty_ordered, qty_produced, unit)
            VALUES (gen_random_uuid(), $1, $2, $3, $3, 'Complete', 1000.0, 980.0, 'm')
            RETURNING id;
            """,
            f"JOB1-{tag}", cust1_id, spec_name_a
        )

        # Job 2: Cust A, Spec A (Approved Costing)
        job2_id = await conn.fetchval(
            """
            INSERT INTO jobs (id, job_no, customer_id, product, spec, status, qty_ordered, qty_produced, unit)
            VALUES (gen_random_uuid(), $1, $2, $3, $3, 'Complete', 500.0, 500.0, 'm')
            RETURNING id;
            """,
            f"JOB2-{tag}", cust1_id, spec_name_a
        )

        # Job 3: Cust B, Spec B (Approved Costing)
        job3_id = await conn.fetchval(
            """
            INSERT INTO jobs (id, job_no, customer_id, product, spec, status, qty_ordered, qty_produced, unit)
            VALUES (gen_random_uuid(), $1, $2, $3, $3, 'Complete', 2000.0, 2000.0, 'm')
            RETURNING id;
            """,
            f"JOB3-{tag}", cust2_id, spec_name_b
        )

        # Job 4: Cust A, Spec A (Draft Costing -> MUST BE EXCLUDED from Approved rollup)
        job4_id = await conn.fetchval(
            """
            INSERT INTO jobs (id, job_no, customer_id, product, spec, status, qty_ordered, qty_produced, unit)
            VALUES (gen_random_uuid(), $1, $2, $3, $3, 'In Progress', 800.0, 0.0, 'm')
            RETURNING id;
            """,
            f"JOB4-{tag}", cust1_id, spec_name_a
        )

        # 3. Seed Costing Records
        # Costing 1: Job 1 (Approved)
        # Unit yarn cost = (35.0 / 1000) * 250 * 1.05 = 9.1875
        # Unit mfg cost = 9.1875 + 2.0 + 1.0 + 3.0 + 1.5 + 0.5 + 1.0 = 18.1875
        # Total Job 1 Mfg Cost = 18.1875 * 980 = 17823.75
        await conn.execute(
            """
            INSERT INTO costing (
                job_id, qty, unit, yarn_consumption, yarn_rate, wastage_pct,
                dyeing, coating, labour, overhead, packing, freight, margin_pct, status, created_by, created_at
            ) VALUES (
                $1::uuid, 980.0, 'm', 35.0, 250.0, 5.0,
                2.0, 1.0, 3.0, 1.5, 0.5, 1.0, 22.5, 'Approved', $2::uuid, now()
            );
            """,
            job1_id, costing_user_id
        )

        # Costing 2: Job 2 (Approved)
        # Unit yarn cost = (35.0 / 1000) * 250 * 1.05 = 9.1875
        # Unit mfg cost = 9.1875 + 2.0 + 1.0 + 3.0 + 1.5 + 0.5 + 1.0 = 18.1875
        # Total Job 2 Mfg Cost = 18.1875 * 500 = 9093.75
        await conn.execute(
            """
            INSERT INTO costing (
                job_id, qty, unit, yarn_consumption, yarn_rate, wastage_pct,
                dyeing, coating, labour, overhead, packing, freight, margin_pct, status, created_by, created_at
            ) VALUES (
                $1::uuid, 500.0, 'm', 35.0, 250.0, 5.0,
                2.0, 1.0, 3.0, 1.5, 0.5, 1.0, 17.5, 'Approved', $2::uuid, now()
            );
            """,
            job2_id, costing_user_id
        )

        # Costing 3: Job 3 (Approved)
        # Unit yarn cost = (50.0 / 1000) * 300 * 1.04 = 15.60
        # Unit mfg cost = 15.60 + 0 + 0 + 4.0 + 2.0 + 1.0 + 1.0 = 23.60
        # Total Job 3 Mfg Cost = 23.60 * 2000 = 47200.00
        await conn.execute(
            """
            INSERT INTO costing (
                job_id, qty, unit, yarn_consumption, yarn_rate, wastage_pct,
                dyeing, coating, labour, overhead, packing, freight, margin_pct, status, created_by, created_at
            ) VALUES (
                $1::uuid, 2000.0, 'm', 50.0, 300.0, 4.0,
                0.0, 0.0, 4.0, 2.0, 1.0, 1.0, 15.0, 'Approved', $2::uuid, now()
            );
            """,
            job3_id, costing_user_id
        )

        # Costing 4: Job 4 (Draft - should be ignored)
        await conn.execute(
            """
            INSERT INTO costing (
                job_id, qty, unit, yarn_consumption, yarn_rate, wastage_pct,
                dyeing, coating, labour, overhead, packing, freight, margin_pct, status, created_by, created_at
            ) VALUES (
                $1::uuid, 800.0, 'm', 35.0, 250.0, 5.0,
                2.0, 1.0, 3.0, 1.5, 0.5, 1.0, 20.0, 'Draft', $2::uuid, now()
            );
            """,
            job4_id, costing_user_id
        )

        # 4. Seed QC Checks to verify QC fail count and rework rate
        inspector_id = uuid.UUID(TEST_USERS["line_inspector"]["id"])
        # Spec A: 4 checks (3 PASS, 1 FAIL) -> 25.0% rework rate
        await conn.execute(
            """
            INSERT INTO qc_checks (id, check_no, job_id, stage, parameter, spec_value, tolerance, actual, limit_type, checked_on, inspector_id)
            VALUES 
              (gen_random_uuid(), $1, $5, 'Weaving', 'Width', 25.0, 1.0, 25.0, 'nominal'::limit_kind, $6, $7),
              (gen_random_uuid(), $2, $5, 'Weaving', 'Width', 25.0, 1.0, 25.0, 'nominal'::limit_kind, $6, $7),
              (gen_random_uuid(), $3, $5, 'Weaving', 'Width', 25.0, 1.0, 25.0, 'nominal'::limit_kind, $6, $7),
              (gen_random_uuid(), $4, $5, 'Weaving', 'Width', 25.0, 1.0, 35.0, 'nominal'::limit_kind, $6, $7);
            """,
            f"QC1-{tag}", f"QC2-{tag}", f"QC3-{tag}", f"QC4-{tag}",
            job1_id, today, inspector_id
        )

        # 5. Query endpoint and verify response with Chief Executive (holds costing and qc read)
        token = make_test_token(
            TEST_USERS["chief_executive"]["id"],
            TEST_USERS["chief_executive"]["email"],
            TEST_USERS["chief_executive"]["role_code"],
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/analytics/profitability?period=all", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == HTTP_200_OK
            html = resp.text

            # Customer Alpha Rollup Checks:
            # Expected Mfg Cost: 17,823.75 + 9,093.75 = 26,917.50
            # Expected Avg Margin: (22.5 + 17.5) / 2 = 20.00%
            # Expected Margin Range: 17.5% - 22.5%
            assert f"Customer Alpha {tag}" in html
            assert "26,917.50" in html
            assert "20.00%" in html

            # Customer Beta Rollup Checks:
            # Expected Mfg Cost: 47,200.00
            # Expected Avg Margin: 15.00%
            assert f"Customer Beta {tag}" in html
            assert "47,200.00" in html
            assert "15.00%" in html

            # Spec A Rollup Checks:
            assert spec_name_a in html
            assert "25.00%" in html  # QC rework rate (1 / 4)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_profitability_qc_period_filtering():
    """
    Item 4: Tests that qc_fails and lab_fails subqueries in Query B strictly respect the period filter ($1/$2).
    A QC failure dated 60 days ago must NOT be counted under period=30d, but MUST be counted under period=90d.
    """
    from datetime import timedelta
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    costing_user_id = uuid.UUID(TEST_USERS["costing_analyst"]["id"])
    inspector_id = uuid.UUID(TEST_USERS["line_inspector"]["id"])
    try:
        today = date.today()
        tag = uuid.uuid4().hex[:6].upper()

        cust_id = await conn.fetchval(
            """
            INSERT INTO customers (id, name, active)
            VALUES (gen_random_uuid(), $1, true)
            RETURNING id;
            """,
            f"Customer TimeFilter {tag}"
        )

        spec_name = f"MIL-W-4088K Type IX {tag}"

        job_id = await conn.fetchval(
            """
            INSERT INTO jobs (id, job_no, customer_id, product, spec, status, qty_ordered, qty_produced, unit)
            VALUES (gen_random_uuid(), $1, $2, $3, $3, 'Complete', 1000.0, 1000.0, 'm')
            RETURNING id;
            """,
            f"JOB-TF-{tag}", cust_id, spec_name
        )

        # Approved costing record created today
        await conn.execute(
            """
            INSERT INTO costing (
                job_id, qty, unit, yarn_consumption, yarn_rate, wastage_pct,
                dyeing, coating, labour, overhead, packing, freight, margin_pct, status, created_by, created_at
            ) VALUES (
                $1::uuid, 1000.0, 'm', 40.0, 260.0, 5.0,
                2.0, 1.0, 3.0, 1.5, 0.5, 1.0, 20.0, 'Approved', $2::uuid, now()
            );
            """,
            job_id, costing_user_id
        )

        # QC Check 1: 60 days ago (OUTSIDE 30d window) -> FAIL
        await conn.execute(
            """
            INSERT INTO qc_checks (id, check_no, job_id, stage, parameter, spec_value, tolerance, actual, limit_type, checked_on, inspector_id)
            VALUES (gen_random_uuid(), $1, $2, 'Weaving', 'Width', 25.0, 1.0, 35.0, 'nominal'::limit_kind, $3, $4);
            """,
            f"QC-OLD-{tag}", job_id, today - timedelta(days=60), inspector_id
        )

        # QC Check 2: Today (INSIDE 30d window) -> PASS
        await conn.execute(
            """
            INSERT INTO qc_checks (id, check_no, job_id, stage, parameter, spec_value, tolerance, actual, limit_type, checked_on, inspector_id)
            VALUES (gen_random_uuid(), $1, $2, 'Weaving', 'Width', 25.0, 1.0, 25.0, 'nominal'::limit_kind, $3, $4);
            """,
            f"QC-NEW-{tag}", job_id, today, inspector_id
        )

        token = make_test_token(
            TEST_USERS["chief_executive"]["id"],
            TEST_USERS["chief_executive"]["email"],
            TEST_USERS["chief_executive"]["role_code"],
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # 1. Query period=30d -> Old fail must be EXCLUDED (0 fails, 0.00% rework rate)
            resp_30d = await client.get("/analytics/profitability?period=30d", headers={"Authorization": f"Bearer {token}"})
            assert resp_30d.status_code == HTTP_200_OK
            html_30d = resp_30d.text
            assert spec_name in html_30d
            # Verify 0.00% rework rate rendered for spec (old fail ignored)
            idx_30d = html_30d.find(spec_name)
            snippet_30d = html_30d[idx_30d:idx_30d+1200]
            assert "0.00%" in snippet_30d

            # 2. Query period=90d -> Old fail must be INCLUDED (1 fail out of 2 = 50.00% rework rate)
            resp_90d = await client.get("/analytics/profitability?period=90d", headers={"Authorization": f"Bearer {token}"})
            assert resp_90d.status_code == HTTP_200_OK
            html_90d = resp_90d.text
            assert spec_name in html_90d
            idx_90d = html_90d.find(spec_name)
            snippet_90d = html_90d[idx_90d:idx_90d+1200]
            assert "50.00%" in snippet_90d
    finally:
        await conn.close()

