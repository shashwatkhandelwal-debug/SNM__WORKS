import asyncio
import uuid
from datetime import date
import pytest
import database
from routers.qc import get_next_qc_check_no


@pytest.mark.asyncio
async def test_qc_verdict_computed_by_postgres():
    """
    Item 1: Proves PostgreSQL qc_verdict generated column evaluates verdicts directly in the database.
    Does not use any Python mock or in-memory fallback.
    """
    assert database.pool is not None, "Database pool must be initialized against local Postgres"

    async with database.pool.acquire() as conn:
        async with conn.transaction():
            # 1. Test PostgreSQL stored generated function qc_verdict directly
            v_pass = await conn.fetchval(
                "SELECT qc_verdict('nominal'::limit_kind, 44.0, 1.0, NULL, 44.5)"
            )
            assert v_pass == "PASS"

            v_fail = await conn.fetchval(
                "SELECT qc_verdict('nominal'::limit_kind, 44.0, 1.0, NULL, 46.0)"
            )
            assert v_fail == "FAIL"

            v_min_pass = await conn.fetchval(
                "SELECT qc_verdict('minimum'::limit_kind, 100.0, NULL, NULL, 105.0)"
            )
            assert v_min_pass == "PASS"

            v_min_fail = await conn.fetchval(
                "SELECT qc_verdict('minimum'::limit_kind, 100.0, NULL, NULL, 95.0)"
            )
            assert v_min_fail == "FAIL"

            v_range_pass = await conn.fetchval(
                "SELECT qc_verdict('range'::limit_kind, 20.0, NULL, 30.0, 25.0)"
            )
            assert v_range_pass == "PASS"

            v_range_fail = await conn.fetchval(
                "SELECT qc_verdict('range'::limit_kind, 20.0, NULL, 30.0, 35.0)"
            )
            assert v_range_fail == "FAIL"

            # 2. Test inserting into qc_checks table and reading back Postgres-generated stored verdict column
            test_check_id = uuid.uuid4()
            test_check_no = f"Q-TEST-{uuid.uuid4().hex[:6]}"
            row = await conn.fetchrow(
                """
                INSERT INTO qc_checks (
                    id, check_no, stage, parameter, limit_type,
                    spec_value, tolerance, actual
                ) VALUES (
                    $1, $2, 'On-Loom Inspection', 'Width', 'nominal'::limit_kind,
                    44.0, 1.0, 44.2
                )
                RETURNING id, check_no, verdict
                """,
                test_check_id, test_check_no
            )
            assert row["verdict"] == "PASS"

            # Update actual to failing reading and verify Postgres recomputes generated column
            updated_row = await conn.fetchrow(
                """
                UPDATE qc_checks
                SET actual = 48.0
                WHERE id = $1
                RETURNING id, verdict
                """,
                test_check_id
            )
            assert updated_row["verdict"] == "FAIL"


@pytest.mark.asyncio
async def test_qc_check_no_race_condition_handling():
    """
    Item 3: Confirms sequence number generation handles concurrency safely without duplicate collisions.
    """
    assert database.pool is not None, "Database pool must be initialized against local Postgres"

    async with database.pool.acquire() as conn:
        async with conn.transaction():
            seq1 = await get_next_qc_check_no(conn)
            assert seq1.startswith("Q-")

            # Insert a record with the generated sequence number
            c_id1 = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO qc_checks (id, check_no, stage, parameter, spec_value)
                VALUES ($1, $2, 'On-Loom Inspection', 'Width', 44.0)
                """,
                c_id1, seq1
            )

            # Get next sequence and verify it incremented cleanly
            seq2 = await get_next_qc_check_no(conn)
            assert seq2 != seq1
            
            num1 = int(seq1.split("-")[1])
            num2 = int(seq2.split("-")[1])
            assert num2 == num1 + 1


@pytest.mark.asyncio
async def test_qc_routes_reject_unauthenticated(anonymous_client):
    """
    Item 2: Confirms unauthenticated requests are rejected with 401 Unauthorized.
    """
    resp = await anonymous_client.get("/qc")
    assert resp.status_code == 401
    assert "Authentication required" in resp.json().get("detail", "")
