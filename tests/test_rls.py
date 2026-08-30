import json
import uuid
import asyncpg
import pytest
import httpx
from starlette.status import HTTP_200_OK, HTTP_403_FORBIDDEN
from main import app
from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS, make_test_token


# ============================================================================
# 1. QC CHECKS RLS & RBAC TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_role_without_qc_read_cannot_read_qc(sales_client):
    """
    Proves that a role without qc.read permission (sales_executive)
    is strictly denied access to GET /qc with HTTP 403 Forbidden.
    """
    resp = await sales_client.get("/qc")
    assert resp.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit read on qc" in resp.text


@pytest.mark.asyncio
async def test_permitted_role_can_read_qc_sanity_check(qa_client, inspector_client):
    """
    Proves that roles with qc.read (qa_manager and line_inspector)
    are granted HTTP 200 OK access to GET /qc.
    """
    resp_qa = await qa_client.get("/qc")
    assert resp_qa.status_code == HTTP_200_OK
    assert "QC Line Checks" in resp_qa.text or "Quality Control" in resp_qa.text

    resp_insp = await inspector_client.get("/qc")
    assert resp_insp.status_code == HTTP_200_OK


@pytest.mark.asyncio
async def test_legacy_qc_role_without_user_roles_is_denied():
    """
    Confirms that legacy profiles.role = 'qc' alone without an active role
    in user_roles is strictly denied access to qc_checks at both API (403)
    and PostgreSQL RLS (0 rows) levels.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.qc.only@snmills.com"

    try:
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy QC User', 'qc', true) ON CONFLICT (id) DO UPDATE SET role = 'qc';", fake_user_id)
        await conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        # 1. API Level -> 403 Forbidden
        token = make_test_token(user_id=fake_user_id, email=fake_email, role_code="qc")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}) as client:
            res = await client.get("/qc")
            assert res.status_code == HTTP_403_FORBIDDEN

        # 2. Database RLS Level -> 0 rows returned
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", json.dumps({"sub": fake_user_id, "role": "authenticated"}))
            rows = await conn.fetch("SELECT * FROM qc_checks")
            assert len(rows) == 0, "Database RLS permitted read via legacy profiles.role on qc_checks!"

    finally:
        await conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await conn.close()


# ============================================================================
# 2. JOBS RLS & RBAC TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_role_without_jobs_read_cannot_read_jobs(hr_client):
    """
    Proves that a role without jobs.read permission (hr_officer)
    is strictly denied access to GET /jobs with HTTP 403 Forbidden.
    """
    resp = await hr_client.get("/jobs")
    assert resp.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit read on jobs" in resp.text


@pytest.mark.asyncio
async def test_permitted_role_can_read_jobs_sanity_check(production_client, sales_client):
    """
    Proves that roles with jobs.read (production_manager, sales_executive)
    are granted HTTP 200 OK access to GET /jobs.
    """
    resp_prod = await production_client.get("/jobs")
    assert resp_prod.status_code == HTTP_200_OK

    resp_sales = await sales_client.get("/jobs")
    assert resp_sales.status_code == HTTP_200_OK


@pytest.mark.asyncio
async def test_legacy_supervisor_role_without_user_roles_is_denied():
    """
    Confirms that legacy profiles.role = 'supervisor' alone without an active role
    in user_roles is strictly denied access to jobs at both API (403)
    and PostgreSQL RLS (0 rows) levels.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.supervisor.only@snmills.com"

    try:
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy Supervisor', 'supervisor', true) ON CONFLICT (id) DO UPDATE SET role = 'supervisor';", fake_user_id)
        await conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        # 1. API Level -> 403 Forbidden
        token = make_test_token(user_id=fake_user_id, email=fake_email, role_code="supervisor")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}) as client:
            res = await client.get("/jobs")
            assert res.status_code == HTTP_403_FORBIDDEN

        # 2. Database RLS Level -> 0 rows returned
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", json.dumps({"sub": fake_user_id, "role": "authenticated"}))
            rows = await conn.fetch("SELECT * FROM jobs")
            assert len(rows) == 0, "Database RLS permitted read via legacy profiles.role on jobs!"

    finally:
        await conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await conn.close()


# ============================================================================
# 3. SKUS RLS & RBAC TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_role_without_skus_read_cannot_read_skus(hr_client, inspector_client):
    """
    Proves that roles without skus.read permission (hr_officer, line_inspector)
    are strictly denied access to GET /skus with HTTP 403 Forbidden.
    """
    resp_hr = await hr_client.get("/skus")
    assert resp_hr.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit read on skus" in resp_hr.text

    resp_insp = await inspector_client.get("/skus")
    assert resp_insp.status_code == HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_permitted_role_can_read_skus_sanity_check(sales_client):
    """
    Proves that roles with skus.read (sales_executive)
    are granted HTTP 200 OK access to GET /skus.
    """
    resp = await sales_client.get("/skus")
    assert resp.status_code == HTTP_200_OK


@pytest.mark.asyncio
async def test_legacy_store_role_without_user_roles_is_denied():
    """
    Confirms that legacy profiles.role = 'store' alone without an active role
    in user_roles is strictly denied access to skus at both API (403)
    and PostgreSQL RLS (0 rows) levels.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.store.only@snmills.com"

    try:
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy Store User', 'store', true) ON CONFLICT (id) DO UPDATE SET role = 'store';", fake_user_id)
        await conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        # 1. API Level -> 403 Forbidden
        token = make_test_token(user_id=fake_user_id, email=fake_email, role_code="store")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}) as client:
            res = await client.get("/skus")
            assert res.status_code == HTTP_403_FORBIDDEN

        # 2. Database RLS Level -> 0 rows returned
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", json.dumps({"sub": fake_user_id, "role": "authenticated"}))
            rows = await conn.fetch("SELECT * FROM skus")
            assert len(rows) == 0, "Database RLS permitted read via legacy profiles.role on skus!"

    finally:
        await conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await conn.close()


# ============================================================================
# 4. RLS BATCH 2 TESTS: 13 TABLES CONVERTED TO auth_can()
# ============================================================================

async def _query_as_user(user_id: str, query: str, *args):
    """Executes a query under SET LOCAL ROLE authenticated with specific user claims."""
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true)",
                json.dumps({"sub": str(user_id), "role": "authenticated"}),
            )
            return await conn.fetch(query, *args)
    finally:
        await conn.close()


async def _execute_as_user(user_id: str, query: str, *args):
    """Executes a non-SELECT query under SET LOCAL ROLE authenticated with specific user claims."""
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true)",
                json.dumps({"sub": str(user_id), "role": "authenticated"}),
            )
            return await conn.execute(query, *args)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Table 1: profiles
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_profiles_rls_permitted_hr_can_read_all():
    hr_user_id = TEST_USERS["hr_officer"]["id"]
    rows = await _query_as_user(hr_user_id, "SELECT id FROM profiles;")
    assert len(rows) > 5, "HR Officer holding people.read could not view all profiles under RLS!"


@pytest.mark.asyncio
async def test_profiles_rls_self_read_granted_other_users_denied():
    op_user_id = TEST_USERS["machine_operator"]["id"]
    rows = await _query_as_user(op_user_id, "SELECT id FROM profiles;")
    assert len(rows) == 1, f"Regular operator saw {len(rows)} profile rows, expected only self (1 row)!"
    assert str(rows[0]["id"]) == str(op_user_id)

@pytest.mark.asyncio
async def test_profiles_rls_legacy_role_alone_denied_other_users():
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.supervisor.prof@snmills.com"
    try:
        await admin_conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await admin_conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy Sup User', 'supervisor', true) ON CONFLICT (id) DO UPDATE SET role = 'supervisor';", fake_user_id)
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        rows = await _query_as_user(fake_user_id, "SELECT id FROM profiles;")
        assert len(rows) == 1, "Legacy profile role alone was able to view other users' profiles!"
        assert str(rows[0]["id"]) == str(fake_user_id)
    finally:
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Table 2: customers
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_customers_rls_permitted_role_succeeds():
    cust_id = str(uuid.uuid4())
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"INSERT INTO customers (id, name) VALUES ('{cust_id}', 'RLS Test Cust Permitted') ON CONFLICT (name) DO NOTHING;")
        rows = await _query_as_user(TEST_USERS["sales_executive"]["id"], "SELECT * FROM customers WHERE id = $1;", uuid.UUID(cust_id))
        assert len(rows) == 1, "Sales executive with customers.read could not read customers table!"
    finally:
        await admin_conn.execute(f"DELETE FROM customers WHERE id = '{cust_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_customers_rls_denied_role_zero_rows():
    cust_id = str(uuid.uuid4())
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"INSERT INTO customers (id, name) VALUES ('{cust_id}', 'RLS Test Cust Denied') ON CONFLICT (name) DO NOTHING;")
        rows = await _query_as_user(TEST_USERS["machine_operator"]["id"], "SELECT * FROM customers WHERE id = $1;", uuid.UUID(cust_id))
        assert len(rows) == 0, f"Operator leaked {len(rows)} customer rows!"
    finally:
        await admin_conn.execute(f"DELETE FROM customers WHERE id = '{cust_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_customers_rls_legacy_role_alone_denied():
    cust_id = str(uuid.uuid4())
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.supervisor.cust@snmills.com"
    try:
        await admin_conn.execute(f"INSERT INTO customers (id, name) VALUES ('{cust_id}', 'RLS Test Cust Legacy') ON CONFLICT (name) DO NOTHING;")
        await admin_conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await admin_conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy Sup User', 'supervisor', true) ON CONFLICT (id) DO UPDATE SET role = 'supervisor';", fake_user_id)
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        rows = await _query_as_user(fake_user_id, "SELECT * FROM customers WHERE id = $1;", uuid.UUID(cust_id))
        assert len(rows) == 0, "Legacy profiles.role alone leaked customer rows without active user_roles!"
    finally:
        await admin_conn.execute(f"DELETE FROM customers WHERE id = '{cust_id}';")
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Table 3: constructions
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_constructions_rls_permitted_role_succeeds():
    cid = str(uuid.uuid4())
    user_id = TEST_USERS["chief_technical"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO constructions (id, spec_no, created_on, revision, status, family, product, created_by, weave, width_mm, warp_ends, picks_per_cm, warp_denier, weft_denier)
            VALUES ('{cid}', 'SPEC-RLS-CONST-P', CURRENT_DATE, '0', 'Draft', 'Narrow woven', 'Test Webbing', '{user_id}', 'Plain', 25.0, 100, 10.0, 840, 840)
            ON CONFLICT (id) DO NOTHING;
        """)
        rows = await _query_as_user(TEST_USERS["product_developer"]["id"], "SELECT * FROM constructions WHERE id = $1;", uuid.UUID(cid))
        assert len(rows) == 1, "Product developer holding constructions.read could not read constructions table!"
    finally:
        await admin_conn.execute(f"DELETE FROM constructions WHERE id = '{cid}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_constructions_rls_denied_role_zero_rows():
    cid = str(uuid.uuid4())
    user_id = TEST_USERS["chief_technical"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO constructions (id, spec_no, created_on, revision, status, family, product, created_by, weave, width_mm, warp_ends, picks_per_cm, warp_denier, weft_denier)
            VALUES ('{cid}', 'SPEC-RLS-CONST-D', CURRENT_DATE, '0', 'Draft', 'Narrow woven', 'Test Webbing', '{user_id}', 'Plain', 25.0, 100, 10.0, 840, 840)
            ON CONFLICT (id) DO NOTHING;
        """)
        rows = await _query_as_user(TEST_USERS["hr_officer"]["id"], "SELECT * FROM constructions WHERE id = $1;", uuid.UUID(cid))
        assert len(rows) == 0, f"HR officer leaked {len(rows)} construction rows!"
    finally:
        await admin_conn.execute(f"DELETE FROM constructions WHERE id = '{cid}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_constructions_rls_legacy_role_alone_denied():
    cid = str(uuid.uuid4())
    user_id = TEST_USERS["chief_technical"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.supervisor.const@snmills.com"
    try:
        await admin_conn.execute(f"""
            INSERT INTO constructions (id, spec_no, created_on, revision, status, family, product, created_by, weave, width_mm, warp_ends, picks_per_cm, warp_denier, weft_denier)
            VALUES ('{cid}', 'SPEC-RLS-CONST-L', CURRENT_DATE, '0', 'Draft', 'Narrow woven', 'Test Webbing', '{user_id}', 'Plain', 25.0, 100, 10.0, 840, 840)
            ON CONFLICT (id) DO NOTHING;
        """)
        await admin_conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await admin_conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy Supervisor', 'supervisor', true) ON CONFLICT (id) DO UPDATE SET role = 'supervisor';", fake_user_id)
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        rows = await _query_as_user(fake_user_id, "SELECT * FROM constructions WHERE id = $1;", uuid.UUID(cid))
        assert len(rows) == 0, "Legacy profiles.role alone leaked construction rows without active user_roles!"
    finally:
        await admin_conn.execute(f"DELETE FROM constructions WHERE id = '{cid}';")
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Table 4: downtime
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_downtime_rls_permitted_role_succeeds():
    dt_id = str(uuid.uuid4())
    op_id = TEST_USERS["machine_operator"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO downtime (id, log_no, logged_on, shift, machine, reason, minutes, operator_id)
            VALUES ('{dt_id}', 'DT-RLS-P01', CURRENT_DATE, 'A', 'LOOM-01', 'Mechanical Jam', 30, '{op_id}')
            ON CONFLICT (id) DO NOTHING;
        """)
        rows = await _query_as_user(op_id, "SELECT * FROM downtime WHERE id = $1;", uuid.UUID(dt_id))
        assert len(rows) == 1, "Machine operator holding downtime.read could not read downtime table!"
    finally:
        await admin_conn.execute(f"DELETE FROM downtime WHERE id = '{dt_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_downtime_rls_denied_role_zero_rows():
    dt_id = str(uuid.uuid4())
    op_id = TEST_USERS["machine_operator"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO downtime (id, log_no, logged_on, shift, machine, reason, minutes, operator_id)
            VALUES ('{dt_id}', 'DT-RLS-D01', CURRENT_DATE, 'A', 'LOOM-01', 'Mechanical Jam', 30, '{op_id}')
            ON CONFLICT (id) DO NOTHING;
        """)
        rows = await _query_as_user(TEST_USERS["sales_executive"]["id"], "SELECT * FROM downtime WHERE id = $1;", uuid.UUID(dt_id))
        assert len(rows) == 0, f"Sales executive leaked {len(rows)} downtime rows!"
    finally:
        await admin_conn.execute(f"DELETE FROM downtime WHERE id = '{dt_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_downtime_rls_legacy_role_alone_denied():
    dt_id = str(uuid.uuid4())
    op_id = TEST_USERS["machine_operator"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.operator.dt@snmills.com"
    try:
        await admin_conn.execute(f"""
            INSERT INTO downtime (id, log_no, logged_on, shift, machine, reason, minutes, operator_id)
            VALUES ('{dt_id}', 'DT-RLS-L01', CURRENT_DATE, 'A', 'LOOM-01', 'Mechanical Jam', 30, '{op_id}')
            ON CONFLICT (id) DO NOTHING;
        """)
        await admin_conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await admin_conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy Op User', 'operator', true) ON CONFLICT (id) DO UPDATE SET role = 'operator';", fake_user_id)
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        rows = await _query_as_user(fake_user_id, "SELECT * FROM downtime WHERE id = $1;", uuid.UUID(dt_id))
        assert len(rows) == 0, "Legacy profiles.role alone leaked downtime rows without active user_roles!"
    finally:
        await admin_conn.execute(f"DELETE FROM downtime WHERE id = '{dt_id}';")
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Table 5: despatch
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_despatch_rls_permitted_role_succeeds():
    dsp_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    store_user_id = TEST_USERS["store_keeper"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO jobs (id, job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
            VALUES ('{job_id}', 'JOB-RLS-DSP-P', CURRENT_DATE, 'Test Webbing', 1000, 'm', 1000, 'Dispatched') ON CONFLICT (job_no) DO NOTHING;
            INSERT INTO despatch (id, despatch_no, despatched_on, job_id, created_by, qty, unit, status)
            VALUES ('{dsp_id}', 'DSP-RLS-P01', CURRENT_DATE, '{job_id}', '{store_user_id}', 500, 'm', 'Packed')
            ON CONFLICT (id) DO NOTHING;
        """)
        rows = await _query_as_user(TEST_USERS["export_executive"]["id"], "SELECT * FROM despatch WHERE id = $1;", uuid.UUID(dsp_id))
        assert len(rows) == 1, "Export executive holding despatch.read could not read despatch table!"
    finally:
        await admin_conn.execute(f"DELETE FROM despatch WHERE id = '{dsp_id}'; DELETE FROM jobs WHERE id = '{job_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_despatch_rls_denied_role_zero_rows():
    dsp_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    store_user_id = TEST_USERS["store_keeper"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO jobs (id, job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
            VALUES ('{job_id}', 'JOB-RLS-DSP-D', CURRENT_DATE, 'Test Webbing', 1000, 'm', 1000, 'Dispatched') ON CONFLICT (job_no) DO NOTHING;
            INSERT INTO despatch (id, despatch_no, despatched_on, job_id, created_by, qty, unit, status)
            VALUES ('{dsp_id}', 'DSP-RLS-D01', CURRENT_DATE, '{job_id}', '{store_user_id}', 500, 'm', 'Packed')
            ON CONFLICT (id) DO NOTHING;
        """)
        rows = await _query_as_user(TEST_USERS["machine_operator"]["id"], "SELECT * FROM despatch WHERE id = $1;", uuid.UUID(dsp_id))
        assert len(rows) == 0, f"Operator leaked {len(rows)} despatch rows!"
    finally:
        await admin_conn.execute(f"DELETE FROM despatch WHERE id = '{dsp_id}'; DELETE FROM jobs WHERE id = '{job_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_despatch_rls_legacy_role_alone_denied():
    dsp_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    store_user_id = TEST_USERS["store_keeper"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.store.dsp@snmills.com"
    try:
        await admin_conn.execute(f"""
            INSERT INTO jobs (id, job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
            VALUES ('{job_id}', 'JOB-RLS-DSP-L', CURRENT_DATE, 'Test Webbing', 1000, 'm', 1000, 'Dispatched') ON CONFLICT (job_no) DO NOTHING;
            INSERT INTO despatch (id, despatch_no, despatched_on, job_id, created_by, qty, unit, status)
            VALUES ('{dsp_id}', 'DSP-RLS-L01', CURRENT_DATE, '{job_id}', '{store_user_id}', 500, 'm', 'Packed')
            ON CONFLICT (id) DO NOTHING;
        """)
        await admin_conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await admin_conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy Store User', 'store', true) ON CONFLICT (id) DO UPDATE SET role = 'store';", fake_user_id)
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        rows = await _query_as_user(fake_user_id, "SELECT * FROM despatch WHERE id = $1;", uuid.UUID(dsp_id))
        assert len(rows) == 0, "Legacy profiles.role alone leaked despatch rows without active user_roles!"
    finally:
        await admin_conn.execute(f"DELETE FROM despatch WHERE id = '{dsp_id}'; DELETE FROM jobs WHERE id = '{job_id}';")
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Table 6: capa
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_capa_rls_permitted_role_succeeds():
    capa_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    qa_id = TEST_USERS["qa_manager"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO jobs (id, job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
            VALUES ('{job_id}', 'JOB-RLS-CAPA-P', CURRENT_DATE, 'Test Webbing', 1000, 'm', 500, 'In Production') ON CONFLICT (job_no) DO NOTHING;
            INSERT INTO capa (id, capa_no, raised_on, source, job_id, problem, status, raised_by)
            VALUES ('{capa_id}', 'CAPA-RLS-P01', CURRENT_DATE, 'QC Failure', '{job_id}', 'Tensile failure', 'Open', '{qa_id}')
            ON CONFLICT (id) DO NOTHING;
        """)
        rows = await _query_as_user(qa_id, "SELECT * FROM capa WHERE id = $1;", uuid.UUID(capa_id))
        assert len(rows) == 1, "QA Manager holding capa.read could not read capa table!"
    finally:
        await admin_conn.execute(f"DELETE FROM capa WHERE id = '{capa_id}'; DELETE FROM jobs WHERE id = '{job_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_capa_rls_denied_role_zero_rows():
    capa_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    qa_id = TEST_USERS["qa_manager"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO jobs (id, job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
            VALUES ('{job_id}', 'JOB-RLS-CAPA-D', CURRENT_DATE, 'Test Webbing', 1000, 'm', 500, 'In Production') ON CONFLICT (job_no) DO NOTHING;
            INSERT INTO capa (id, capa_no, raised_on, source, job_id, problem, status, raised_by)
            VALUES ('{capa_id}', 'CAPA-RLS-D01', CURRENT_DATE, 'QC Failure', '{job_id}', 'Tensile failure', 'Open', '{qa_id}')
            ON CONFLICT (id) DO NOTHING;
        """)
        rows = await _query_as_user(TEST_USERS["sales_executive"]["id"], "SELECT * FROM capa WHERE id = $1;", uuid.UUID(capa_id))
        assert len(rows) == 0, f"Sales executive leaked {len(rows)} capa rows!"
    finally:
        await admin_conn.execute(f"DELETE FROM capa WHERE id = '{capa_id}'; DELETE FROM jobs WHERE id = '{job_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_capa_rls_legacy_role_alone_denied():
    capa_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    qa_id = TEST_USERS["qa_manager"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.qc.capa@snmills.com"
    try:
        await admin_conn.execute(f"""
            INSERT INTO jobs (id, job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
            VALUES ('{job_id}', 'JOB-RLS-CAPA-L', CURRENT_DATE, 'Test Webbing', 1000, 'm', 500, 'In Production') ON CONFLICT (job_no) DO NOTHING;
            INSERT INTO capa (id, capa_no, raised_on, source, job_id, problem, status, raised_by)
            VALUES ('{capa_id}', 'CAPA-RLS-L01', CURRENT_DATE, 'QC Failure', '{job_id}', 'Tensile failure', 'Open', '{qa_id}')
            ON CONFLICT (id) DO NOTHING;
        """)
        await admin_conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await admin_conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy QC User', 'qc', true) ON CONFLICT (id) DO UPDATE SET role = 'qc';", fake_user_id)
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        rows = await _query_as_user(fake_user_id, "SELECT * FROM capa WHERE id = $1;", uuid.UUID(capa_id))
        assert len(rows) == 0, "Legacy profiles.role alone leaked capa rows without active user_roles!"
    finally:
        await admin_conn.execute(f"DELETE FROM capa WHERE id = '{capa_id}'; DELETE FROM jobs WHERE id = '{job_id}';")
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Table 7: dye_recipes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dye_recipes_rls_permitted_role_succeeds():
    rec_id = str(uuid.uuid4())
    dev_id = TEST_USERS["product_developer"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO dye_recipes (id, recipe_no, dyed_on, target_shade, created_by, status)
            VALUES ('{rec_id}', 'DR-RLS-P01', CURRENT_DATE, 'Olive Drab 7', '{dev_id}', 'Draft')
            ON CONFLICT (id) DO NOTHING;
        """)
        rows = await _query_as_user(dev_id, "SELECT * FROM dye_recipes WHERE id = $1;", uuid.UUID(rec_id))
        assert len(rows) == 1, "Product developer holding recipes.read could not read dye_recipes table!"
    finally:
        await admin_conn.execute(f"DELETE FROM dye_recipes WHERE id = '{rec_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_dye_recipes_rls_denied_role_zero_rows():
    rec_id = str(uuid.uuid4())
    dev_id = TEST_USERS["product_developer"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO dye_recipes (id, recipe_no, dyed_on, target_shade, created_by, status)
            VALUES ('{rec_id}', 'DR-RLS-D01', CURRENT_DATE, 'Olive Drab 7', '{dev_id}', 'Draft')
            ON CONFLICT (id) DO NOTHING;
        """)
        rows = await _query_as_user(TEST_USERS["sales_executive"]["id"], "SELECT * FROM dye_recipes WHERE id = $1;", uuid.UUID(rec_id))
        assert len(rows) == 0, f"Sales executive leaked {len(rows)} dye_recipes rows!"
    finally:
        await admin_conn.execute(f"DELETE FROM dye_recipes WHERE id = '{rec_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_dye_recipes_rls_legacy_role_alone_denied():
    rec_id = str(uuid.uuid4())
    dev_id = TEST_USERS["product_developer"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.supervisor.dr@snmills.com"
    try:
        await admin_conn.execute(f"""
            INSERT INTO dye_recipes (id, recipe_no, dyed_on, target_shade, created_by, status)
            VALUES ('{rec_id}', 'DR-RLS-L01', CURRENT_DATE, 'Olive Drab 7', '{dev_id}', 'Draft')
            ON CONFLICT (id) DO NOTHING;
        """)
        await admin_conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await admin_conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy Supervisor', 'supervisor', true) ON CONFLICT (id) DO UPDATE SET role = 'supervisor';", fake_user_id)
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        rows = await _query_as_user(fake_user_id, "SELECT * FROM dye_recipes WHERE id = $1;", uuid.UUID(rec_id))
        assert len(rows) == 0, "Legacy profiles.role alone leaked dye_recipes rows without active user_roles!"
    finally:
        await admin_conn.execute(f"DELETE FROM dye_recipes WHERE id = '{rec_id}';")
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Table 8: costing
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_costing_rls_permitted_role_succeeds():
    job_id = str(uuid.uuid4())
    analyst_id = TEST_USERS["costing_analyst"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO jobs (id, job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
            VALUES ('{job_id}', 'JOB-RLS-CST-P', CURRENT_DATE, 'Test Webbing', 1000, 'm', 0, 'Planning') ON CONFLICT (job_no) DO NOTHING;
            INSERT INTO costing (job_id, created_by, yarn_rate, yarn_consumption, status)
            VALUES ('{job_id}', '{analyst_id}', 220.0, 1.25, 'Draft')
            ON CONFLICT (job_id) DO NOTHING;
        """)
        rows = await _query_as_user(analyst_id, "SELECT * FROM costing WHERE job_id = $1;", uuid.UUID(job_id))
        assert len(rows) == 1, "Costing analyst holding costing.read could not read costing table!"
    finally:
        await admin_conn.execute(f"DELETE FROM costing WHERE job_id = '{job_id}'; DELETE FROM jobs WHERE id = '{job_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_costing_rls_denied_role_zero_rows():
    job_id = str(uuid.uuid4())
    analyst_id = TEST_USERS["costing_analyst"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO jobs (id, job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
            VALUES ('{job_id}', 'JOB-RLS-CST-D', CURRENT_DATE, 'Test Webbing', 1000, 'm', 0, 'Planning') ON CONFLICT (job_no) DO NOTHING;
            INSERT INTO costing (job_id, created_by, yarn_rate, yarn_consumption, status)
            VALUES ('{job_id}', '{analyst_id}', 220.0, 1.25, 'Draft')
            ON CONFLICT (job_id) DO NOTHING;
        """)
        rows = await _query_as_user(TEST_USERS["sales_executive"]["id"], "SELECT * FROM costing WHERE job_id = $1;", uuid.UUID(job_id))
        assert len(rows) == 0, f"Sales executive leaked {len(rows)} costing rows!"
    finally:
        await admin_conn.execute(f"DELETE FROM costing WHERE job_id = '{job_id}'; DELETE FROM jobs WHERE id = '{job_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_costing_rls_legacy_role_alone_denied():
    job_id = str(uuid.uuid4())
    analyst_id = TEST_USERS["costing_analyst"]["id"]
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.owner.cost@snmills.com"
    try:
        await admin_conn.execute(f"""
            INSERT INTO jobs (id, job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
            VALUES ('{job_id}', 'JOB-RLS-CST-L', CURRENT_DATE, 'Test Webbing', 1000, 'm', 0, 'Planning') ON CONFLICT (job_no) DO NOTHING;
            INSERT INTO costing (job_id, created_by, yarn_rate, yarn_consumption, status)
            VALUES ('{job_id}', '{analyst_id}', 220.0, 1.25, 'Draft')
            ON CONFLICT (job_id) DO NOTHING;
        """)
        await admin_conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await admin_conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy Owner User', 'owner', true) ON CONFLICT (id) DO UPDATE SET role = 'owner';", fake_user_id)
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        rows = await _query_as_user(fake_user_id, "SELECT * FROM costing WHERE job_id = $1;", uuid.UUID(job_id))
        assert len(rows) == 0, "Legacy profiles.role alone leaked costing rows without active user_roles!"
    finally:
        await admin_conn.execute(f"DELETE FROM costing WHERE job_id = '{job_id}'; DELETE FROM jobs WHERE id = '{job_id}';")
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Table 9: audit_log
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_log_rls_permitted_role_succeeds():
    auditor_id = TEST_USERS["chief_quality"]["id"]
    rows = await _query_as_user(auditor_id, "SELECT * FROM audit_log LIMIT 5;")
    assert len(rows) > 0, "Chief Quality holding audit.read could not read audit_log!"


@pytest.mark.asyncio
async def test_audit_log_rls_denied_role_zero_rows():
    operator_id = TEST_USERS["machine_operator"]["id"]
    rows = await _query_as_user(operator_id, "SELECT * FROM audit_log;")
    assert len(rows) == 0, f"Operator without audit.read leaked {len(rows)} audit rows!"


@pytest.mark.asyncio
async def test_audit_log_rls_legacy_role_alone_denied():
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.owner.audit@snmills.com"
    try:
        await admin_conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await admin_conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy Owner User', 'owner', true) ON CONFLICT (id) DO UPDATE SET role = 'owner';", fake_user_id)
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        rows = await _query_as_user(fake_user_id, "SELECT * FROM audit_log;")
        assert len(rows) == 0, "Legacy profiles.role alone leaked audit_log rows without active user_roles!"
    finally:
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.close()


@pytest.mark.asyncio
async def test_audit_log_rls_immutable_no_update_no_delete():
    auditor_id = TEST_USERS["chief_quality"]["id"]
    
    # Check DELETE rejection in own transaction
    conn_del = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        async with conn_del.transaction():
            await conn_del.execute("SET LOCAL ROLE authenticated")
            await conn_del.execute(
                "SELECT set_config('request.jwt.claims', $1, true)",
                json.dumps({"sub": str(auditor_id), "role": "authenticated"}),
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn_del.execute("DELETE FROM audit_log;")
    finally:
        await conn_del.close()

    # Check UPDATE rejection (0 rows updated under RLS as no UPDATE policies exist)
    conn_upd = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        async with conn_upd.transaction():
            await conn_upd.execute("SET LOCAL ROLE authenticated")
            await conn_upd.execute(
                "SELECT set_config('request.jwt.claims', $1, true)",
                json.dumps({"sub": str(auditor_id), "role": "authenticated"}),
            )
            res = await conn_upd.execute("UPDATE audit_log SET action = 'tampered';")
            assert res == "UPDATE 0", f"Unauthorized update on audit_log updated {res} rows!"
    finally:
        await conn_upd.close()


# ---------------------------------------------------------------------------
# Table 10: campaigns
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_campaigns_rls_permitted_role_succeeds():
    cmp_id = str(uuid.uuid4())
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO campaigns (id, occasion, headline, body, post_status)
            VALUES ('{cmp_id}', 'RLS Perm Occasion', 'RLS Perm Headline', 'RLS Perm Body', 'queued')
            ON CONFLICT (id) DO NOTHING;
        """)
        rows = await _query_as_user(TEST_USERS["sales_executive"]["id"], "SELECT * FROM campaigns WHERE id = $1;", uuid.UUID(cmp_id))
        assert len(rows) == 1, "Sales executive holding skus.read could not read campaigns table!"
    finally:
        await admin_conn.execute(f"DELETE FROM campaigns WHERE id = '{cmp_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_campaigns_rls_denied_role_zero_rows():
    cmp_id = str(uuid.uuid4())
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await admin_conn.execute(f"""
            INSERT INTO campaigns (id, occasion, headline, body, post_status)
            VALUES ('{cmp_id}', 'RLS Deny Occasion', 'RLS Deny Headline', 'RLS Deny Body', 'queued')
            ON CONFLICT (id) DO NOTHING;
        """)
        rows = await _query_as_user(TEST_USERS["machine_operator"]["id"], "SELECT * FROM campaigns WHERE id = $1;", uuid.UUID(cmp_id))
        assert len(rows) == 0, f"Operator leaked {len(rows)} campaigns rows!"
    finally:
        await admin_conn.execute(f"DELETE FROM campaigns WHERE id = '{cmp_id}';")
        await admin_conn.close()


@pytest.mark.asyncio
async def test_campaigns_rls_legacy_role_alone_denied():
    cmp_id = str(uuid.uuid4())
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.operator.cmp@snmills.com"
    try:
        await admin_conn.execute(f"""
            INSERT INTO campaigns (id, occasion, headline, body, post_status)
            VALUES ('{cmp_id}', 'RLS Legacy Occasion', 'RLS Legacy Headline', 'RLS Legacy Body', 'queued')
            ON CONFLICT (id) DO NOTHING;
        """)
        await admin_conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await admin_conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy Op User', 'operator', true) ON CONFLICT (id) DO UPDATE SET role = 'operator';", fake_user_id)
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        rows = await _query_as_user(fake_user_id, "SELECT * FROM campaigns WHERE id = $1;", uuid.UUID(cmp_id))
        assert len(rows) == 0, "Legacy profiles.role alone leaked campaigns rows without active user_roles!"
    finally:
        await admin_conn.execute(f"DELETE FROM campaigns WHERE id = '{cmp_id}';")
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Table 11: masters
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_masters_rls_authenticated_users_can_read():
    operator_id = TEST_USERS["machine_operator"]["id"]
    rows = await _query_as_user(operator_id, "SELECT * FROM masters LIMIT 5;")
    assert len(rows) > 0, "Authenticated operator could not read shared reference masters!"


@pytest.mark.asyncio
async def test_masters_rls_unauthorized_modification_denied():
    operator_id = TEST_USERS["machine_operator"]["id"]
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true)",
                json.dumps({"sub": str(operator_id), "role": "authenticated"}),
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("INSERT INTO masters (list_name, value, sort_order) VALUES ('test', 'HACK', 99);")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_masters_rls_legacy_role_alone_cannot_modify():
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.owner.masters@snmills.com"
    try:
        await admin_conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await admin_conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy Owner User', 'owner', true) ON CONFLICT (id) DO UPDATE SET role = 'owner';", fake_user_id)
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE authenticated")
                await conn.execute(
                    "SELECT set_config('request.jwt.claims', $1, true)",
                    json.dumps({"sub": str(fake_user_id), "role": "authenticated"}),
                )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.execute("INSERT INTO masters (list_name, value, sort_order) VALUES ('test', 'HACK_LEGACY', 99);")
        finally:
            await conn.close()
    finally:
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Table 12: param_library
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_param_library_rls_permitted_role_can_read():
    qa_id = TEST_USERS["qa_manager"]["id"]
    rows = await _query_as_user(qa_id, "SELECT * FROM param_library LIMIT 5;")
    assert len(rows) > 0, "QA Manager holding specifications.read could not read param_library!"


@pytest.mark.asyncio
async def test_param_library_rls_unauthorized_modification_denied():
    operator_id = TEST_USERS["machine_operator"]["id"]
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true)",
                json.dumps({"sub": str(operator_id), "role": "authenticated"}),
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("INSERT INTO param_library (id, family, name) VALUES (99999, 'Test', 'HACK_PARAM');")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_param_library_rls_legacy_role_alone_cannot_modify():
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.qc.params@snmills.com"
    try:
        await admin_conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await admin_conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy QC User', 'qc', true) ON CONFLICT (id) DO UPDATE SET role = 'qc';", fake_user_id)
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE authenticated")
                await conn.execute(
                    "SELECT set_config('request.jwt.claims', $1, true)",
                    json.dumps({"sub": str(fake_user_id), "role": "authenticated"}),
                )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.execute("INSERT INTO param_library (id, family, name) VALUES (99998, 'Test', 'HACK_LEGACY_PARAM');")
        finally:
            await conn.close()
    finally:
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Table 13: mil_w_4088_types
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mil_w_4088_types_rls_permitted_role_can_read():
    dev_id = TEST_USERS["product_developer"]["id"]
    rows = await _query_as_user(dev_id, "SELECT * FROM mil_w_4088_types LIMIT 5;")
    assert len(rows) > 0, "Product developer holding specifications.read could not read mil_w_4088_types!"


@pytest.mark.asyncio
async def test_mil_w_4088_types_rls_unauthorized_modification_denied():
    operator_id = TEST_USERS["machine_operator"]["id"]
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true)",
                json.dumps({"sub": str(operator_id), "role": "authenticated"}),
            )
            # Unauthorized UPDATE matches 0 rows under RLS USING clause
            res = await conn.execute("UPDATE mil_w_4088_types SET break_min_lb = 0 WHERE type = 'VIII';")
            assert res == "UPDATE 0", f"Unauthorized update on mil_w_4088_types updated {res} rows!"

            # Unauthorized INSERT raises InsufficientPrivilegeError under RLS WITH CHECK clause
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("INSERT INTO mil_w_4088_types (type, width_in, width_tol_in, thick_min_in, thick_max_in, weight_max_oz_yd, break_min_lb, ends_face_back, ends_binder, picks_class1, filling_1a2) VALUES ('HACK', 1, 0, 0, 0, 0, 0, 0, 0, 0, 0);")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_mil_w_4088_types_rls_legacy_role_alone_cannot_modify():
    admin_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    fake_user_id = str(uuid.uuid4())
    fake_email = "legacy.supervisor.mil@snmills.com"
    try:
        await admin_conn.execute("INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING;", fake_user_id, fake_email)
        await admin_conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1::uuid, 'Legacy Sup User', 'supervisor', true) ON CONFLICT (id) DO UPDATE SET role = 'supervisor';", fake_user_id)
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)

        conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE authenticated")
                await conn.execute(
                    "SELECT set_config('request.jwt.claims', $1, true)",
                    json.dumps({"sub": str(fake_user_id), "role": "authenticated"}),
                )
                res = await conn.execute("UPDATE mil_w_4088_types SET break_min_lb = 0 WHERE type = 'VIII';")
                assert res == "UPDATE 0", f"Legacy role alone updated {res} rows on mil_w_4088_types!"

                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.execute("INSERT INTO mil_w_4088_types (type, width_in, width_tol_in, thick_min_in, thick_max_in, weight_max_oz_yd, break_min_lb, ends_face_back, ends_binder, picks_class1, filling_1a2) VALUES ('HACK_LEGACY', 1, 0, 0, 0, 0, 0, 0, 0, 0, 0);")
        finally:
            await conn.close()
    finally:
        await admin_conn.execute("DELETE FROM user_roles WHERE user_id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM profiles WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.execute("DELETE FROM auth.users WHERE id = $1::uuid;", fake_user_id)
        await admin_conn.close()