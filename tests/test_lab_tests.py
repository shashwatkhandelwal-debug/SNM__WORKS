import asyncio
import uuid
import asyncpg
import pytest
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
)
from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS


@pytest.mark.asyncio
async def test_lab_test_verdict_computed_by_postgres():
    """
    Test 1: Confirms that verdict is a PostgreSQL generated stored column.
    Tests all four limit kinds (nominal, minimum, maximum, range) and empty/pending.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        user_id = TEST_USERS["lab_analyst"]["id"]

        # 1. Nominal: target 43.65, tol 1.58 (tolerance range [42.07, 45.23])
        r1 = await conn.fetchrow(
            """
            INSERT INTO lab_tests (
                test_id, test_type, parameter, limit_type, spec_value,
                tolerance, is_critical, specimens, created_by
            ) VALUES (
                'TEST-VERDICT-NOM-PASS', 'Tensile / Breaking Strength', 'Width', 'nominal',
                43.65, 1.58, false, ARRAY[43.0, 44.0, 43.5], $1::uuid
            )
            RETURNING verdict;
            """,
            user_id,
        )
        assert r1["verdict"] == "PASS"

        r2 = await conn.fetchrow(
            """
            INSERT INTO lab_tests (
                test_id, test_type, parameter, limit_type, spec_value,
                tolerance, is_critical, specimens, created_by
            ) VALUES (
                'TEST-VERDICT-NOM-FAIL', 'Tensile / Breaking Strength', 'Width', 'nominal',
                43.65, 1.58, false, ARRAY[46.0, 48.0], $1::uuid
            )
            RETURNING verdict;
            """,
            user_id,
        )
        assert r2["verdict"] == "FAIL"

        # 2. Maximum: ceiling 49.6 g/m
        r3 = await conn.fetchrow(
            """
            INSERT INTO lab_tests (
                test_id, test_type, parameter, limit_type, spec_value,
                is_critical, specimens, created_by
            ) VALUES (
                'TEST-VERDICT-MAX-PASS', 'Weight / Linear Density', 'Weight per metre', 'maximum',
                49.6, false, ARRAY[48.2, 49.0], $1::uuid
            )
            RETURNING verdict;
            """,
            user_id,
        )
        assert r3["verdict"] == "PASS"

        r4 = await conn.fetchrow(
            """
            INSERT INTO lab_tests (
                test_id, test_type, parameter, limit_type, spec_value,
                is_critical, specimens, created_by
            ) VALUES (
                'TEST-VERDICT-MAX-FAIL', 'Weight / Linear Density', 'Weight per metre', 'maximum',
                49.6, false, ARRAY[48.0, 52.0], $1::uuid
            )
            RETURNING verdict;
            """,
            user_id,
        )
        assert r4["verdict"] == "FAIL"

        # 3. Range: 1.016 mm to 1.778 mm
        r5 = await conn.fetchrow(
            """
            INSERT INTO lab_tests (
                test_id, test_type, parameter, limit_type, spec_value,
                upper_limit, is_critical, specimens, created_by
            ) VALUES (
                'TEST-VERDICT-RNG-PASS', 'Thickness & Width', 'Thickness', 'range',
                1.016, 1.778, false, ARRAY[1.20, 1.50, 1.65], $1::uuid
            )
            RETURNING verdict;
            """,
            user_id,
        )
        assert r5["verdict"] == "PASS"

        r6 = await conn.fetchrow(
            """
            INSERT INTO lab_tests (
                test_id, test_type, parameter, limit_type, spec_value,
                upper_limit, is_critical, specimens, created_by
            ) VALUES (
                'TEST-VERDICT-RNG-FAIL', 'Thickness & Width', 'Thickness', 'range',
                1.016, 1.778, true, ARRAY[0.85, 1.40], $1::uuid
            )
            RETURNING verdict;
            """,
            user_id,
        )
        assert r6["verdict"] == "FAIL"

        # 4. Empty specimens -> 'Pending'
        r7 = await conn.fetchrow(
            """
            INSERT INTO lab_tests (
                test_id, test_type, parameter, limit_type, spec_value,
                is_critical, specimens, created_by
            ) VALUES (
                'TEST-VERDICT-EMPTY', 'Tensile / Breaking Strength', 'Breaking Strength', 'minimum',
                1800.0, true, '{}', $1::uuid
            )
            RETURNING verdict;
            """,
            user_id,
        )
        assert r7["verdict"] == "Pending"

    finally:
        await conn.execute("DELETE FROM lab_tests WHERE test_id LIKE 'TEST-VERDICT-%'")
        await conn.close()


@pytest.mark.asyncio
async def test_is_critical_min_not_average_rule():
    """
    Test 2: MIL-W-4088K clause 3.6.1 compliance test.
    Spec minimum = 1800.0 kgf.
    Specimen readings = [1850.0, 1820.0, 1790.0].
    - Average = (1850 + 1820 + 1790) / 3 = 1820.0 kgf (>= 1800.0 kgf, would pass).
    - Minimum specimen = 1790.0 kgf (< 1800.0 kgf, fails floor rule).

    When is_critical = true: PostgreSQL stored verdict must be 'FAIL'.
    When is_critical = false: PostgreSQL stored verdict must be 'PASS'.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        user_id = TEST_USERS["lab_analyst"]["id"]

        # Case A: is_critical = TRUE -> Minimum specimen (1790.0) causes FAIL
        critical_row = await conn.fetchrow(
            """
            INSERT INTO lab_tests (
                test_id, test_type, parameter, limit_type, spec_value,
                is_critical, specimens, created_by
            ) VALUES (
                'TEST-CRIT-TRUE', 'Tensile / Breaking Strength', 'Breaking Strength', 'minimum',
                1800.0, true, ARRAY[1850.0, 1820.0, 1790.0], $1::uuid
            )
            RETURNING verdict;
            """,
            user_id,
        )
        assert critical_row["verdict"] == "FAIL", (
            f"Expected FAIL due to min specimen 1790.0 < 1800.0, got {critical_row['verdict']}"
        )

        # Case B: is_critical = FALSE -> Average (1820.0) yields PASS
        non_critical_row = await conn.fetchrow(
            """
            INSERT INTO lab_tests (
                test_id, test_type, parameter, limit_type, spec_value,
                is_critical, specimens, created_by
            ) VALUES (
                'TEST-CRIT-FALSE', 'Tensile / Breaking Strength', 'Breaking Strength', 'minimum',
                1800.0, false, ARRAY[1850.0, 1820.0, 1790.0], $1::uuid
            )
            RETURNING verdict;
            """,
            user_id,
        )
        assert non_critical_row["verdict"] == "PASS", (
            f"Expected PASS due to avg 1820.0 >= 1800.0, got {non_critical_row['verdict']}"
        )

    finally:
        await conn.execute("DELETE FROM lab_tests WHERE test_id LIKE 'TEST-CRIT-%'")
        await conn.close()


@pytest.mark.asyncio
async def test_concurrent_lab_test_sequence_generation(analyst_client):
    """
    Test 3: Concurrent test_id generation handles concurrent submissions cleanly
    without unique constraint crash.
    """
    async def post_test(idx: int):
        payload = {
            "test_type": "Tensile / Breaking Strength",
            "parameter": f"Tensile Strength #{idx}",
            "limit_type": "minimum",
            "spec_value": "1500",
            "is_critical": "true",
            "specimens_raw": "1550, 1560, 1540",
            "lab": "In-house",
        }
        return await analyst_client.post("/lab-tests", data=payload, follow_redirects=False)

    # Launch 5 concurrent submissions
    responses = await asyncio.gather(*(post_test(i) for i in range(5)))
    for res in responses:
        assert res.status_code == HTTP_303_SEE_OTHER
        assert "/lab-tests/" in res.headers["location"]


@pytest.mark.asyncio
async def test_role_without_tests_read_cannot_read_lab_tests(sales_client):
    """
    Test 4: RLS / Permission denial test.
    sales_executive does NOT hold tests.read.
    Must receive HTTP 403 Forbidden.
    """
    res = await sales_client.get("/lab-tests")
    assert res.status_code == HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_permitted_role_can_read_lab_tests_sanity_check(analyst_client, qa_client, chief_quality_client):
    """
    Test 5: RLS / Permission grant test.
    lab_analyst, qa_manager, and chief_quality all hold tests.read.
    Must receive HTTP 200 OK.
    """
    res_analyst = await analyst_client.get("/lab-tests")
    assert res_analyst.status_code == HTTP_200_OK
    assert "Laboratory Tests & Release" in res_analyst.text

    res_qa = await qa_client.get("/lab-tests")
    assert res_qa.status_code == HTTP_200_OK

    res_cq = await chief_quality_client.get("/lab-tests")
    assert res_cq.status_code == HTTP_200_OK


@pytest.mark.asyncio
async def test_lab_test_routes_reject_unauthenticated(anonymous_client):
    """
    Test 6: Unauthenticated requests are rejected.
    """
    res = await anonymous_client.get("/lab-tests")
    assert res.status_code in (HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN)


@pytest.mark.asyncio
async def test_no_self_approval_database_constraint():
    """
    Test 7: Segregation of Duties constraint (lab_tests_no_self_approval).
    A user cannot approve a test record they created.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    user_id = TEST_USERS["lab_analyst"]["id"]
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO lab_tests (
                test_id, test_type, parameter, limit_type, spec_value,
                is_critical, specimens, created_by
            ) VALUES (
                'TEST-SOD-001', 'Tensile / Breaking Strength', 'Breaking Strength', 'minimum',
                1800.0, true, ARRAY[1850.0], $1::uuid
            )
            RETURNING id;
            """,
            user_id,
        )
        test_uuid = row["id"]

        # Attempt self-approval in PostgreSQL -> must raise CheckViolationError
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                UPDATE lab_tests
                SET approved_by = $1::uuid, approved_at = now()
                WHERE id = $2::uuid
                """,
                user_id,
                test_uuid,
            )

    finally:
        await conn.execute("DELETE FROM lab_tests WHERE test_id = 'TEST-SOD-001'")
        await conn.close()


@pytest.mark.asyncio
async def test_legacy_profile_role_without_user_roles_permission_is_denied():
    """
    Test 8: Confirms that legacy profiles.role column ('qc', 'owner', 'supervisor')
    does NOT grant access if the user has no role granting tests.read in user_roles.
    Tests both API HTTP 403 enforcement and database RLS enforcement.
    """
    import json
    import httpx
    from main import app
    from tests.conftest import make_test_token

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    legacy_user_id = str(uuid.uuid4())
    legacy_email = "legacy.qc.user@snmills.com"

    try:
        # Seed user with legacy profiles.role = 'qc', but NO rows in user_roles
        await conn.execute(
            "INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;",
            legacy_user_id, legacy_email
        )
        await conn.execute(
            """
            INSERT INTO profiles (id, full_name, role, active)
            VALUES ($1::uuid, 'Legacy QC User', 'qc', true)
            ON CONFLICT (id) DO UPDATE SET role = 'qc', active = true;
            """,
            legacy_user_id
        )
        await conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", legacy_user_id)

        # 1. API Level: Request must be rejected with 403 Forbidden
        token = make_test_token(user_id=legacy_user_id, email=legacy_email, role_code="qc")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"}
        ) as client:
            res = await client.get("/lab-tests")
            assert res.status_code == HTTP_403_FORBIDDEN

        # 2. Database RLS Level: Direct query under authenticated role must return 0 rows
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true)",
                json.dumps({"sub": legacy_user_id, "role": "authenticated"})
            )
            # Query lab_tests table
            rows = await conn.fetch("SELECT * FROM lab_tests")
            assert len(rows) == 0, (
                "Database RLS allowed reading lab_tests via legacy profiles.role! "
                "auth_can(module, action) was bypassed."
            )

    finally:
        await conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid", legacy_user_id)
        await conn.execute("DELETE FROM profiles WHERE id = $1::uuid", legacy_user_id)
        await conn.execute("DELETE FROM auth.users WHERE id = $1::uuid", legacy_user_id)
        await conn.close()
