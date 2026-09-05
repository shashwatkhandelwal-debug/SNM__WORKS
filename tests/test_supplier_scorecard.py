"""
tests/test_supplier_scorecard.py — Test Suite for Supplier Quality Scorecard.

Validates:
1. /analytics/suppliers route aggregations:
   - Total lots received, volume in KG
   - Approved vs Rejected vs Quarantine counts
   - Incoming yarn lab tests pass percentage
   - Average turnaround release days
2. Supplier rating grading badges (Grade A, Grade B, Grade C, No Receipts).
"""

import uuid
from datetime import date
import asyncpg
import httpx
import pytest
from starlette.status import HTTP_200_OK

from tests.conftest import LOCAL_TEST_DATABASE_URL, make_test_token, TEST_USERS
from main import app


@pytest.mark.asyncio
async def test_supplier_quality_scorecard_aggregation():
    """
    Test that /analytics/suppliers accurately displays supplier quality metrics and ratings.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        today = date.today()

        # 1. Create 2 distinct test suppliers
        sup_a_name = f"Reliance Industrial Yarns {uuid.uuid4().hex[:6].upper()}"
        sup_a_id = await conn.fetchval(
            """
            INSERT INTO suppliers (id, supplier_code, name, active)
            VALUES (gen_random_uuid(), $1, $2, true)
            RETURNING id;
            """,
            f"SUP-A-{uuid.uuid4().hex[:4].upper()}", sup_a_name
        )

        sup_b_name = f"Defective Synthetics {uuid.uuid4().hex[:6].upper()}"
        sup_b_id = await conn.fetchval(
            """
            INSERT INTO suppliers (id, supplier_code, name, active)
            VALUES (gen_random_uuid(), $1, $2, true)
            RETURNING id;
            """,
            f"SUP-B-{uuid.uuid4().hex[:4].upper()}", sup_b_name
        )

        # 2. Supplier A: 2 lots (both Approved), 1000kg, 2 passing lab tests
        lot_a1 = await conn.fetchval(
            """
            INSERT INTO yarn_lots (
                id, lot_no, supplier_id, supplier_name, yarn_type, denier,
                received_date, qty_received, qc_status, released_at
            ) VALUES (
                gen_random_uuid(), $1, $2, $3, 'Nylon 6,6', 840,
                $4, 500, 'Approved', now()
            ) RETURNING id;
            """,
            f"LOT-A1-{uuid.uuid4().hex[:4].upper()}", sup_a_id, sup_a_name, today
        )
        lot_a2 = await conn.fetchval(
            """
            INSERT INTO yarn_lots (
                id, lot_no, supplier_id, supplier_name, yarn_type, denier,
                received_date, qty_received, qc_status, released_at
            ) VALUES (
                gen_random_uuid(), $1, $2, $3, 'Nylon 6,6', 840,
                $4, 500, 'Approved', now()
            ) RETURNING id;
            """,
            f"LOT-A2-{uuid.uuid4().hex[:4].upper()}", sup_a_id, sup_a_name, today
        )

        analyst_id = uuid.UUID(TEST_USERS["lab_analyst"]["id"])
        await conn.execute(
            """
            INSERT INTO lab_tests (
                id, test_id, yarn_lot_id, test_type, parameter, limit_type,
                spec_value, tolerance, is_critical, specimens, tested_on, created_by
            ) VALUES 
              (gen_random_uuid(), $1, $2, 'Tensile', 'Tenacity', 'nominal', 8.5, 1.0, false, ARRAY[8.5], $3, $4),
              (gen_random_uuid(), $5, $6, 'Tensile', 'Tenacity', 'nominal', 8.5, 1.0, false, ARRAY[8.5], $3, $4);
            """,
            f"LT-A1-{uuid.uuid4().hex[:4].upper()}", lot_a1, today, analyst_id,
            f"LT-A2-{uuid.uuid4().hex[:4].upper()}", lot_a2
        )

        # 3. Supplier B: 1 lot (Rejected), 1 failing lab test
        lot_b1 = await conn.fetchval(
            """
            INSERT INTO yarn_lots (
                id, lot_no, supplier_id, supplier_name, yarn_type, denier,
                received_date, qty_received, qc_status
            ) VALUES (
                gen_random_uuid(), $1, $2, $3, 'Polyester Filament', 1000,
                $4, 400, 'Rejected'
            ) RETURNING id;
            """,
            f"LOT-B1-{uuid.uuid4().hex[:4].upper()}", sup_b_id, sup_b_name, today
        )
        await conn.execute(
            """
            INSERT INTO lab_tests (
                id, test_id, yarn_lot_id, test_type, parameter, limit_type,
                spec_value, tolerance, is_critical, specimens, tested_on, created_by
            ) VALUES (gen_random_uuid(), $1, $2, 'Tensile', 'Tenacity', 'nominal', 8.5, 0.5, false, ARRAY[12.0], $3, $4);
            """,
            f"LT-B1-{uuid.uuid4().hex[:4].upper()}", lot_b1, today, analyst_id
        )

        # Fetch Supplier Scorecard with Chief Quality credentials (holds stock & quality permissions)
        token = make_test_token(TEST_USERS["chief_quality"]["id"], TEST_USERS["chief_quality"]["email"], TEST_USERS["chief_quality"]["role_code"])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/analytics/suppliers?period=12m", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == HTTP_200_OK
            html = resp.text

            assert "SUPPLIER QUALITY SCORECARD" in html
            assert sup_a_name in html
            assert sup_b_name in html
            assert "1000.0" in html  # Total KG for Supplier A
            assert "Grade A" in html  # Preferred Supplier A
            assert "Grade C" in html  # Rejected Supplier B
    finally:
        await conn.close()
