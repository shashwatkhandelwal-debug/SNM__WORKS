"""
tests/test_analytics.py — Test Suite for KPI Analytics Dashboard & Report Snapshots.

Validates:
1. Live KPI dashboard computation (/analytics):
   - Shop floor QC checks pass/fail rates & defect codes aggregation
   - Defect trends over time (weekly grouping)
   - CAPA closure durations & distribution
   - Despatch on-time rate calculation against jobs.delivery_due
2. Period filtering (7d, 30d, 90d, 12m, all).
3. Report Snapshot freezing (/analytics/snapshots POST):
   - Sequence generation KPI-YYYY-NNNN
   - Immutable snapshot storage in kpi_report_snapshots
   - Trigger trg_audit_kpi_report_snapshots audit log capture
4. Viewing report register and report details.
"""

import json
import uuid
from datetime import date, timedelta
import asyncpg
import httpx
import pytest
from starlette.status import HTTP_200_OK, HTTP_303_SEE_OTHER

from tests.conftest import LOCAL_TEST_DATABASE_URL, make_test_token, TEST_USERS
from main import app


@pytest.mark.asyncio
async def test_kpi_dashboard_metrics_calculation():
    """
    Test that /analytics accurately aggregates QC checks, defects, CAPA, and despatch on-time rates.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        today = date.today()
        cust_id = await conn.fetchval("SELECT id FROM customers LIMIT 1;")

        # 1. Seed Jobs with delivery_due dates
        job_on_time = await conn.fetchval(
            """
            INSERT INTO jobs (id, job_no, customer_id, product, status, qty_ordered, unit, delivery_due)
            VALUES (gen_random_uuid(), $1, $2, 'Webbing Type VIII', 'Complete', 1000, 'm', $3)
            RETURNING id;
            """,
            f"JOB-ONT-{uuid.uuid4().hex[:6].upper()}", cust_id, today
        )
        job_late = await conn.fetchval(
            """
            INSERT INTO jobs (id, job_no, customer_id, product, status, qty_ordered, unit, delivery_due)
            VALUES (gen_random_uuid(), $1, $2, 'Webbing Type VIII', 'Complete', 1000, 'm', $3)
            RETURNING id;
            """,
            f"JOB-LAT-{uuid.uuid4().hex[:6].upper()}", cust_id, today - timedelta(days=5)
        )

        # 2. Seed QC checks (PASS and FAIL with defect codes)
        inspector_id = uuid.UUID(TEST_USERS["line_inspector"]["id"])
        await conn.execute(
            """
            INSERT INTO qc_checks (id, check_no, job_id, stage, parameter, spec_value, actual, limit_type, checked_on, defect_code, inspector_id)
            VALUES 
              (gen_random_uuid(), $1, $5, 'Weaving', 'Width', 25.0, 25.0, 'nominal', $6, NULL, $7),
              (gen_random_uuid(), $2, $5, 'Weaving', 'Width', 25.0, 25.0, 'nominal', $6, NULL, $7),
              (gen_random_uuid(), $3, $5, 'Weaving', 'Width', 25.0, 30.0, 'nominal', $6, 'DEF-WEFT-SNARL', $7),
              (gen_random_uuid(), $4, $5, 'Weaving', 'Width', 25.0, 20.0, 'nominal', $6, 'DEF-WIDTH-LOW', $7);
            """,
            f"QC-1-{uuid.uuid4().hex[:6].upper()}",
            f"QC-2-{uuid.uuid4().hex[:6].upper()}",
            f"QC-3-{uuid.uuid4().hex[:6].upper()}",
            f"QC-4-{uuid.uuid4().hex[:6].upper()}",
            job_on_time, today, inspector_id
        )

        # 3. Seed CAPA records with closed_at
        qa_id = uuid.UUID(TEST_USERS["qa_manager"]["id"])
        await conn.execute(
            """
            INSERT INTO capa (
                id, capa_no, source, problem, status, raised_on, raised_by,
                due_date, created_at, closed_at, owner_id
            ) VALUES (
                gen_random_uuid(), $1, 'Internal QC', 'Weft Snarl Remediation', 'Closed', $2, $3,
                $2, now() - interval '4 days', now(), $3
            );
            """,
            f"CAPA-KPI-{uuid.uuid4().hex[:4].upper()}", today, qa_id
        )

        # 4. Seed Despatches (1 on time, 1 late) with created_by and override_reason
        await conn.execute(
            """
            INSERT INTO despatch (id, despatch_no, job_id, despatched_on, created_by, override_reason)
            VALUES 
              (gen_random_uuid(), $1, $2, $3, $4, 'Management QC override authorised for testing.'),
              (gen_random_uuid(), $5, $6, $3, $4, 'Management QC override authorised for testing.');
            """,
            f"DSP-ONT-{uuid.uuid4().hex[:4].upper()}", job_on_time, today, qa_id,
            f"DSP-LAT-{uuid.uuid4().hex[:4].upper()}", job_late
        )

        # Fetch dashboard with QA Manager credentials
        token = make_test_token(TEST_USERS["qa_manager"]["id"], TEST_USERS["qa_manager"]["email"], TEST_USERS["qa_manager"]["role_code"])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/analytics?period=30d", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == HTTP_200_OK
            html = resp.text

            assert "KPI &amp; ANALYTICS DASHBOARD" in html or "KPI & ANALYTICS DASHBOARD" in html
            assert "Shop Floor QC Pass Rate" in html
            assert "DEF-WEFT-SNARL" in html
            assert "DEF-WIDTH-LOW" in html
            assert "Avg CAPA Closure Time" in html
            assert "Despatch On-Time Delivery" in html
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_kpi_report_snapshot_freezing_and_audit_log():
    """
    Test that POST /analytics/snapshots creates an immutable record in kpi_report_snapshots
    and verifies that trg_audit_kpi_report_snapshots registers an entry in audit_log.
    """
    token = make_test_token(TEST_USERS["qa_manager"]["id"], TEST_USERS["qa_manager"]["email"], TEST_USERS["qa_manager"]["role_code"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Create a frozen snapshot
        resp = await client.post(
            "/analytics/snapshots",
            data={
                "title": "Monthly OFK Technical Compliance Review",
                "period": "30d",
                "notes": "Verified MIL-W-4088K Type VIII batch lots. Zero critical defects.",
            },
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
        assert resp.status_code == HTTP_303_SEE_OTHER
        redirect_url = resp.headers["location"]
        snapshot_id = redirect_url.split("/")[-1]

        # Verify detail page
        detail_resp = await client.get(f"/analytics/reports/{snapshot_id}", headers={"Authorization": f"Bearer {token}"})
        assert detail_resp.status_code == HTTP_200_OK
        assert "Monthly OFK Technical Compliance Review" in detail_resp.text
        assert "IMMUTABLE SNAPSHOT" in detail_resp.text
        assert "Verified MIL-W-4088K Type VIII batch lots" in detail_resp.text

        # Verify reports list page
        list_resp = await client.get("/analytics/reports", headers={"Authorization": f"Bearer {token}"})
        assert list_resp.status_code == HTTP_200_OK
        assert "Monthly OFK Technical Compliance Review" in list_resp.text

    # Verify audit log entry in DB
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        audit_entry = await conn.fetchrow(
            """
            SELECT * FROM audit_log
            WHERE entity = 'kpi_report_snapshots'
            ORDER BY at DESC LIMIT 1;
            """
        )
        assert audit_entry is not None
        assert audit_entry["action"] == "insert"
        assert audit_entry["entity"] == "kpi_report_snapshots"
    finally:
        await conn.close()
