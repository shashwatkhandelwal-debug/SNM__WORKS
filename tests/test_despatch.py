import asyncio
import uuid
import asyncpg
import pytest
import httpx
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)
from main import app
from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS, make_test_token


# ============================================================================
# HELPER: Seed clean or failed job
# ============================================================================

async def get_or_create_job(conn: asyncpg.Connection, suffix: str = "01", fail_qc: bool = False) -> str:
    cust_id = await conn.fetchval("SELECT id FROM customers LIMIT 1;")
    if not cust_id:
        cust_id = await conn.fetchval(
            """
            INSERT INTO customers (id, name, active)
            VALUES (gen_random_uuid(), 'OFK Ordnance Factory Kanpur', true)
            RETURNING id;
            """
        )

    job_no = f"JOB-DSP-{suffix}-{uuid.uuid4().hex[:6].upper()}"
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (
            id, job_no, customer_id, product, qty_ordered, unit, status
        ) VALUES (
            gen_random_uuid(), $1, $2::uuid, 'MIL-W-4088K Type VIII Webbing', 1000, 'm', 'In Production'
        )
        RETURNING id::text;
        """,
        job_no,
        cust_id,
    )

    if fail_qc:
        # Insert a failing QC check to put the job on QC hold
        check_no = f"QC-FAIL-{suffix}-{uuid.uuid4().hex[:6].upper()}"
        await conn.execute(
            """
            INSERT INTO qc_checks (
                id, check_no, job_id, stage, parameter, unit, method, limit_type,
                spec_value, tolerance, actual, inspector_id
            ) VALUES (
                gen_random_uuid(), $1, $2::uuid, 'Final Inspection', 'Width', 'mm', 'Vernier Caliper', 'nominal',
                44.0, 1.0, 30.0, $3::uuid
            );
            """,
            check_no,
            uuid.UUID(job_id),
            uuid.UUID(TEST_USERS["line_inspector"]["id"]),
        )

    return job_id


# ============================================================================
# 1. SEQUENCE NUMBERING & CREATION
# ============================================================================

@pytest.mark.asyncio
async def test_despatch_creation_and_sequence_numbering(store_client):
    """
    Verifies creating despatch notes sequentially generates DSP-0001, DSP-0002...
    and sets created_by to the authenticated store keeper.
    """
    store_user_id = TEST_USERS["store_keeper"]["id"]

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    await conn.execute("DELETE FROM despatch;")
    job_id1 = await get_or_create_job(conn, suffix="01", fail_qc=False)
    job_id2 = await get_or_create_job(conn, suffix="02", fail_qc=False)

    # 1. Create first despatch note
    resp1 = await store_client.post(
        "/despatch",
        data={
            "job_id": job_id1,
            "despatched_on": "2026-08-30",
            "invoice_no": "SNM/26-27/0101",
            "eway_bill": "541289654123",
            "qty": "500",
            "unit": "m",
            "rolls": "5",
            "gross_wt": "42.5",
            "transporter": "V-Trans",
            "lr_no": "KAN-8891",
            "destination": "OFK Kanpur Depot",
        },
        follow_redirects=False,
    )
    assert resp1.status_code == HTTP_303_SEE_OTHER
    dsp_id1 = resp1.headers["location"].split("/despatch/")[1]

    row1 = await conn.fetchrow("SELECT * FROM despatch WHERE id = $1::uuid;", dsp_id1)
    assert row1 is not None
    assert row1["despatch_no"] == "DSP-0001"
    assert str(row1["created_by"]) == store_user_id
    assert float(row1["qty"]) == 500.0
    assert row1["status"] == "Packed"

    # 2. Create second despatch note
    resp2 = await store_client.post(
        "/despatch",
        data={
            "job_id": job_id2,
            "qty": "300",
            "unit": "m",
        },
        follow_redirects=False,
    )
    assert resp2.status_code == HTTP_303_SEE_OTHER
    dsp_id2 = resp2.headers["location"].split("/despatch/")[1]

    row2 = await conn.fetchrow("SELECT * FROM despatch WHERE id = $1::uuid;", dsp_id2)
    assert row2 is not None
    assert row2["despatch_no"] == "DSP-0002"
    assert str(row2["created_by"]) == store_user_id

    await conn.close()


@pytest.mark.asyncio
async def test_despatch_concurrent_sequence_generation():
    """
    Launches 10 concurrent despatch creation requests to verify that the
    UniqueViolationError retry loop eliminates sequence collision race conditions.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await get_or_create_job(conn, suffix="CONC", fail_qc=False)
    await conn.close()

    token = make_test_token(
        user_id=TEST_USERS["store_keeper"]["id"],
        email=TEST_USERS["store_keeper"]["email"],
        role_code=TEST_USERS["store_keeper"]["role_code"],
    )

    async def create_single_despatch(idx: int):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            return await client.post(
                "/despatch",
                data={
                    "job_id": job_id,
                    "qty": f"{100 + idx}",
                    "destination": f"Destination #{idx}",
                },
                follow_redirects=False,
            )

    results = await asyncio.gather(*[create_single_despatch(i) for i in range(10)])
    for r in results:
        assert r.status_code == HTTP_303_SEE_OTHER

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    rows = await conn.fetch(
        "SELECT despatch_no FROM despatch WHERE destination LIKE 'Destination %' ORDER BY despatch_no ASC;"
    )
    dsp_nos = [r["despatch_no"] for r in rows]
    assert len(dsp_nos) == 10
    assert len(set(dsp_nos)) == 10, "Duplicate Despatch numbers were generated under concurrency!"
    await conn.close()


# ============================================================================
# 2. SCHEMA NOT NULL CONSTRAINTS
# ============================================================================

@pytest.mark.asyncio
async def test_job_id_and_created_by_not_null_database_constraints():
    """
    Proves that job_id and created_by are strictly NOT NULL at the database level.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await get_or_create_job(conn, suffix="NN", fail_qc=False)
    user_id = TEST_USERS["store_keeper"]["id"]

    # 1. Null job_id must fail
    with pytest.raises(asyncpg.exceptions.NotNullViolationError):
        await conn.execute(
            """
            INSERT INTO despatch (id, despatch_no, job_id, created_by)
            VALUES (gen_random_uuid(), 'DSP-9991', NULL, $1::uuid);
            """,
            uuid.UUID(user_id),
        )

    # 2. Null created_by must fail
    with pytest.raises(asyncpg.exceptions.NotNullViolationError):
        await conn.execute(
            """
            INSERT INTO despatch (id, despatch_no, job_id, created_by)
            VALUES (gen_random_uuid(), 'DSP-9992', $1::uuid, NULL);
            """,
            uuid.UUID(job_id),
        )

    await conn.close()


# ============================================================================
# 3. DATABASE LEVEL QC HOLD GATE
# ============================================================================

@pytest.mark.asyncio
async def test_database_qc_hold_trigger_blocks_unauthorized_dispatch(store_client):
    """
    Proves that PostgreSQL trigger trg_check_despatch_qc_hold blocks raw SQL and
    router inserts when attempting to despatch a job on QC hold without an override reason.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    hold_job_id = await get_or_create_job(conn, suffix="HOLD1", fail_qc=True)
    user_id = TEST_USERS["store_keeper"]["id"]

    # 1. Raw SQL without override_reason must fail via check_violation
    with pytest.raises(asyncpg.exceptions.CheckViolationError) as exc_info:
        await conn.execute(
            """
            INSERT INTO despatch (id, despatch_no, job_id, qty, created_by, override_reason)
            VALUES (gen_random_uuid(), 'DSP-RAW-FAIL', $1::uuid, 100, $2::uuid, NULL);
            """,
            uuid.UUID(hold_job_id),
            uuid.UUID(user_id),
        )
    assert "Cannot despatch job on QC hold" in str(exc_info.value)

    # 2. Raw SQL with short (<10 chars) override_reason must fail
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await conn.execute(
            """
            INSERT INTO despatch (id, despatch_no, job_id, qty, created_by, override_reason)
            VALUES (gen_random_uuid(), 'DSP-RAW-SHORT', $1::uuid, 100, $2::uuid, 'short');
            """,
            uuid.UUID(hold_job_id),
            uuid.UUID(user_id),
        )

    # 3. Router attempt without override_reason returns HTTP 400 Bad Request
    resp = await store_client.post(
        "/despatch",
        data={
            "job_id": hold_job_id,
            "qty": "250",
            "unit": "m",
        },
    )
    assert resp.status_code == HTTP_400_BAD_REQUEST
    assert "Cannot despatch job on QC hold" in resp.text

    # 4. Router attempt with valid override reason (>= 10 chars) succeeds!
    resp_valid = await store_client.post(
        "/despatch",
        data={
            "job_id": hold_job_id,
            "qty": "250",
            "unit": "m",
            "override_reason": "Concession granted under OFK letter amendment 2026/08",
        },
        follow_redirects=False,
    )
    assert resp_valid.status_code == HTTP_303_SEE_OTHER
    dsp_id = resp_valid.headers["location"].split("/despatch/")[1]

    row = await conn.fetchrow("SELECT * FROM despatch WHERE id = $1::uuid;", dsp_id)
    assert row is not None
    assert row["override_reason"] == "Concession granted under OFK letter amendment 2026/08"

    await conn.close()


# ============================================================================
# 4. RBAC APPROVAL SEPARATION & NON-NEGOTIABLE RULE 6
# ============================================================================

@pytest.mark.asyncio
async def test_store_keeper_cannot_approve_despatch(store_client):
    """
    Proves that store_keeper (holding create/update, but NOT approve) is denied 403 Forbidden
    when attempting Quality Assurance release sign-off.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await get_or_create_job(conn, suffix="APPR1", fail_qc=False)
    await conn.close()

    # 1. Store keeper creates a dispatch note
    create_resp = await store_client.post(
        "/despatch",
        data={"job_id": job_id, "qty": "100", "unit": "m"},
        follow_redirects=False,
    )
    dsp_id = create_resp.headers["location"].split("/despatch/")[1]

    # 2. Store keeper attempts to approve -> 403 Forbidden
    appr_resp = await store_client.post(f"/despatch/{dsp_id}/approve")
    assert appr_resp.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit approve on despatch" in appr_resp.text


@pytest.mark.asyncio
async def test_no_self_approval_database_constraint(chief_quality_client):
    """
    Non-Negotiable Rule 6: If chief_quality creates a dispatch note, they cannot
    perform the Quality Release approval on their own record.
    """
    qa_user_id = TEST_USERS["chief_quality"]["id"]
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await get_or_create_job(conn, suffix="SELF", fail_qc=False)
    
    # 1. Insert a dispatch note where created_by is chief_quality
    dsp_id = await conn.fetchval(
        """
        INSERT INTO despatch (
            id, despatch_no, job_id, qty, unit, status, created_by
        ) VALUES (
            gen_random_uuid(), $1, $2::uuid, 150, 'm', 'Packed', $3::uuid
        )
        RETURNING id::text;
        """,
        f"DSP-SELF-{uuid.uuid4().hex[:6].upper()}",
        uuid.UUID(job_id),
        uuid.UUID(qa_user_id),
    )
    await conn.close()

    # 2. chief_quality attempts self-approval -> 400 Bad Request
    appr_resp = await chief_quality_client.post(f"/despatch/{dsp_id}/approve")
    assert appr_resp.status_code == HTTP_400_BAD_REQUEST
    assert "Segregation of duties violation" in appr_resp.text

    # 3. Direct SQL update trying self-approval triggers database constraint
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await conn.execute(
            """
            UPDATE despatch
            SET approved_by = created_by, status = 'Dispatched'
            WHERE id = $1::uuid;
            """,
            uuid.UUID(dsp_id),
        )
    await conn.close()


@pytest.mark.asyncio
async def test_independent_quality_release_succeeds(store_client, chief_quality_client):
    """
    Proves that chief_quality can successfully approve and release a dispatch note
    created by store_keeper.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await get_or_create_job(conn, suffix="INDEP", fail_qc=False)
    await conn.close()

    # 1. store_keeper creates dispatch note
    create_resp = await store_client.post(
        "/despatch",
        data={"job_id": job_id, "qty": "500", "unit": "m", "invoice_no": "SNM/26-27/0200"},
        follow_redirects=False,
    )
    dsp_id = create_resp.headers["location"].split("/despatch/")[1]

    # 2. chief_quality approves release
    appr_resp = await chief_quality_client.post(f"/despatch/{dsp_id}/approve", follow_redirects=False)
    assert appr_resp.status_code == HTTP_303_SEE_OTHER

    # 3. Verify in database
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    row = await conn.fetchrow("SELECT * FROM despatch WHERE id = $1::uuid;", dsp_id)
    assert row["status"] == "Dispatched"
    assert str(row["approved_by"]) == TEST_USERS["chief_quality"]["id"]
    assert row["approved_at"] is not None
    await conn.close()


# ============================================================================
# 5. RBAC PERMISSIONS ACCESS CONTROL
# ============================================================================

@pytest.mark.asyncio
async def test_role_without_despatch_read_cannot_read_despatch(sales_client):
    """
    Proves that roles without despatch.read (sales_executive) are denied 403 Forbidden.
    """
    resp = await sales_client.get("/despatch")
    assert resp.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit read on despatch" in resp.text


@pytest.mark.asyncio
async def test_permitted_roles_can_access_despatch(store_client, export_client, production_client, chief_quality_client):
    """
    Proves that roles holding despatch grants receive HTTP 200 OK on /despatch and /despatch/new.
    """
    resp_store = await store_client.get("/despatch")
    assert resp_store.status_code == HTTP_200_OK
    assert "DESPATCH & FINISHED GOODS SHIPMENTS" in resp_store.text

    resp_new = await store_client.get("/despatch/new")
    assert resp_new.status_code == HTTP_200_OK

    resp_exp = await export_client.get("/despatch")
    assert resp_exp.status_code == HTTP_200_OK

    resp_prod = await production_client.get("/despatch")
    assert resp_prod.status_code == HTTP_200_OK

    resp_qa = await chief_quality_client.get("/despatch")
    assert resp_qa.status_code == HTTP_200_OK


@pytest.mark.asyncio
async def test_despatch_routes_reject_unauthenticated(anonymous_client):
    """
    Verifies that unauthenticated requests to despatch routes are rejected with 401.
    """
    resp_list = await anonymous_client.get("/despatch")
    assert resp_list.status_code == HTTP_401_UNAUTHORIZED

    resp_create = await anonymous_client.post(
        "/despatch",
        data={"qty": "100"},
    )
    assert resp_create.status_code == HTTP_401_UNAUTHORIZED
