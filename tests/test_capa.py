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
# 1. SEQUENCE NUMBERING & CREATION
# ============================================================================

@pytest.mark.asyncio
async def test_capa_creation_and_sequence_numbering(qa_client):
    """
    Verifies that creating CAPAs sequentially assigns CAPA-0001, CAPA-0002...
    and properly sets raised_by to the authenticated QA user.
    """
    qa_user_id = TEST_USERS["qa_manager"]["id"]
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    await conn.execute("DELETE FROM capa;")

    # 1. Create first CAPA
    resp1 = await qa_client.post(
        "/capa",
        data={
            "source": "QC Check",
            "problem": "Warp streak defect observed across 50m of roll #2",
            "reference": "Roll #2",
            "root_cause": "Tension discrepancy on creel position #14",
            "correction": "Segregated and marked B-grade",
            "preventive": "Recalibrated warp tension sensors",
            "due_date": "2026-09-15",
        },
        follow_redirects=False,
    )
    assert resp1.status_code == HTTP_303_SEE_OTHER
    redirect_url1 = resp1.headers["location"]
    capa_id1 = redirect_url1.split("/capa/")[1]

    # Verify in database
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    row1 = await conn.fetchrow("SELECT * FROM capa WHERE id = $1::uuid;", capa_id1)
    assert row1 is not None
    assert row1["capa_no"] == "CAPA-0001"
    assert str(row1["raised_by"]) == qa_user_id
    assert row1["status"] == "Open"
    assert row1["problem"] == "Warp streak defect observed across 50m of roll #2"

    # 2. Create second CAPA
    resp2 = await qa_client.post(
        "/capa",
        data={
            "source": "Customer Complaint",
            "problem": "Abrasion resistance below 1500 cycles on OFK lot #88",
            "reference": "OFK-2026-88",
            "due_date": "2026-09-20",
        },
        follow_redirects=False,
    )
    assert resp2.status_code == HTTP_303_SEE_OTHER
    capa_id2 = resp2.headers["location"].split("/capa/")[1]

    row2 = await conn.fetchrow("SELECT * FROM capa WHERE id = $1::uuid;", capa_id2)
    assert row2 is not None
    assert row2["capa_no"] == "CAPA-0002"
    assert str(row2["raised_by"]) == qa_user_id

    await conn.close()


@pytest.mark.asyncio
async def test_capa_concurrent_sequence_generation():
    """
    Launches 10 concurrent CAPA creation requests to verify that the
    UniqueViolationError retry loop eliminates sequence collision race conditions.
    """
    token = make_test_token(
        user_id=TEST_USERS["qa_manager"]["id"],
        email=TEST_USERS["qa_manager"]["email"],
        role_code=TEST_USERS["qa_manager"]["role_code"],
    )

    async def create_single_capa(idx: int):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            return await client.post(
                "/capa",
                data={
                    "source": "Process Deviation",
                    "problem": f"Concurrent stress test defect #{idx}",
                    "reference": f"STRESS-{idx}",
                },
                follow_redirects=False,
            )

    results = await asyncio.gather(*[create_single_capa(i) for i in range(10)])
    for r in results:
        assert r.status_code == HTTP_303_SEE_OTHER

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    rows = await conn.fetch(
        "SELECT capa_no FROM capa WHERE problem LIKE 'Concurrent stress test defect %' ORDER BY capa_no ASC;"
    )
    capa_nos = [r["capa_no"] for r in rows]
    assert len(capa_nos) == 10
    assert len(set(capa_nos)) == 10, "Duplicate CAPA numbers were generated under concurrency!"
    await conn.close()


# ============================================================================
# 2. STATUS TRANSITIONS & LIFECYCLE
# ============================================================================

@pytest.mark.asyncio
async def test_capa_lifecycle_transitions(qa_client):
    """
    Tests updating a CAPA through lifecycle statuses:
    Open -> Investigating -> Action Taken -> Under Verification.
    Also verifies rejection of invalid status strings.
    """
    # 1. Create CAPA
    resp = await qa_client.post(
        "/capa",
        data={
            "source": "Lab Test",
            "problem": "Elongation at break 12% exceeding 10% maximum limit",
            "due_date": "2026-10-01",
        },
        follow_redirects=False,
    )
    capa_id = resp.headers["location"].split("/capa/")[1]

    # 2. Update to 'Investigating'
    update1 = await qa_client.post(
        f"/capa/{capa_id}/update",
        data={
            "source": "Lab Test",
            "problem": "Elongation at break 12% exceeding 10% maximum limit",
            "root_cause": "Heat setting temperature was 15C lower than recipe requirement",
            "status": "Investigating",
        },
        follow_redirects=False,
    )
    assert update1.status_code == HTTP_303_SEE_OTHER

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    row1 = await conn.fetchrow("SELECT * FROM capa WHERE id = $1::uuid;", capa_id)
    assert row1["status"] == "Investigating"
    assert row1["root_cause"] == "Heat setting temperature was 15C lower than recipe requirement"

    # 3. Update to 'Action Taken'
    update2 = await qa_client.post(
        f"/capa/{capa_id}/update",
        data={
            "source": "Lab Test",
            "problem": "Elongation at break 12% exceeding 10% maximum limit",
            "root_cause": "Heat setting temperature was 15C lower than recipe requirement",
            "correction": "Re-run thermo-fixation on lot B at 195C",
            "preventive": "Installed digital temperature controller alarm",
            "status": "Action Taken",
        },
        follow_redirects=False,
    )
    assert update2.status_code == HTTP_303_SEE_OTHER

    row2 = await conn.fetchrow("SELECT status FROM capa WHERE id = $1::uuid;", capa_id)
    assert row2["status"] == "Action Taken"

    # 4. Attempt update with invalid status
    invalid_resp = await qa_client.post(
        f"/capa/{capa_id}/update",
        data={
            "source": "Lab Test",
            "problem": "Elongation at break 12% exceeding 10% maximum limit",
            "status": "NonExistentStatus",
        },
    )
    assert invalid_resp.status_code == HTTP_400_BAD_REQUEST

    await conn.close()


# ============================================================================
# 3. SEGREGATION OF DUTIES: SELF-VERIFICATION CONSTRAINT
# ============================================================================

@pytest.mark.asyncio
async def test_no_self_verification_database_constraint(qa_client, chief_quality_client):
    """
    Proves that a person cannot verify or close a CAPA that they raised.
    Database CHECK constraint capa_no_self_verification must reject this.
    """
    # 1. QA Manager raises a CAPA
    resp = await qa_client.post(
        "/capa",
        data={
            "source": "Internal Audit",
            "problem": "Calibration sticker expired on tensile tester #1",
            "due_date": "2026-09-10",
        },
        follow_redirects=False,
    )
    capa_id = resp.headers["location"].split("/capa/")[1]

    # 2. Database level proof: Attempt direct self-verification in PostgreSQL
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    qa_user_id = TEST_USERS["qa_manager"]["id"]

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await conn.execute(
            """
            UPDATE capa SET
                verified_by = $1::uuid,
                verified_at = NOW(),
                status = 'Closed'
            WHERE id = $2::uuid;
            """,
            qa_user_id,
            capa_id,
        )

    # 3. API level proof: QA Manager attempts self-verification via POST /verify
    # Note: QA Manager does not have capa.approve, so API rejects with 403.
    # If QA Manager's token had approve, the database constraint would return 400.
    resp_self = await qa_client.post(
        f"/capa/{capa_id}/verify",
        data={"verification_notes": "I verified my own CAPA"},
    )
    assert resp_self.status_code == HTTP_403_FORBIDDEN

    # 4. Legitimate verification by Chief Quality Officer (different user holding capa.approve)
    resp_approve = await chief_quality_client.post(
        f"/capa/{capa_id}/verify",
        data={"verification_notes": "Calibration certificate reviewed from NABL accredited agency. Sticker updated."},
        follow_redirects=False,
    )
    assert resp_approve.status_code == HTTP_303_SEE_OTHER

    # Check database state
    row = await conn.fetchrow("SELECT * FROM capa WHERE id = $1::uuid;", capa_id)
    assert row["status"] == "Closed"
    assert str(row["verified_by"]) == TEST_USERS["chief_quality"]["id"]
    assert row["verified_at"] is not None
    assert row["closed_at"] is not None
    assert "NABL accredited agency" in row["verification_notes"]

    await conn.close()


# ============================================================================
# 4. RBAC PERMISSIONS ACCESS CONTROL
# ============================================================================

@pytest.mark.asyncio
async def test_role_without_capa_read_cannot_read_capa(sales_client, hr_client):
    """
    Proves that roles without capa.read permission (sales_executive, hr_officer)
    are strictly denied access with HTTP 403 Forbidden.
    """
    resp_sales = await sales_client.get("/capa")
    assert resp_sales.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit read on capa" in resp_sales.text

    resp_hr = await hr_client.get("/capa")
    assert resp_hr.status_code == HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_permitted_roles_can_access_capa(qa_client, chief_quality_client):
    """
    Proves that roles holding capa grants (qa_manager, chief_quality)
    are granted HTTP 200 OK access to GET /capa and GET /capa/new.
    """
    # 1. QA Manager access
    resp_qa = await qa_client.get("/capa")
    assert resp_qa.status_code == HTTP_200_OK
    assert "CAPA Register" in resp_qa.text

    resp_new_qa = await qa_client.get("/capa/new")
    assert resp_new_qa.status_code == HTTP_200_OK

    # 2. Chief Quality Officer access
    resp_cq = await chief_quality_client.get("/capa")
    assert resp_cq.status_code == HTTP_200_OK


@pytest.mark.asyncio
async def test_capa_routes_reject_unauthenticated(anonymous_client):
    """
    Verifies that unauthenticated requests to CAPA routes are rejected with 401.
    """
    resp_list = await anonymous_client.get("/capa")
    assert resp_list.status_code == HTTP_401_UNAUTHORIZED

    resp_create = await anonymous_client.post(
        "/capa",
        data={"source": "QC Check", "problem": "Test defect"},
    )
    assert resp_create.status_code == HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_capa_pagination(chief_quality_client):
    """
    Pagination Test: Seeds 28 CAPA records, queries page 1 (25 rows) and page 2 (3 rows).
    Verifies:
    1. page 1 contains exactly 25 rows and total_count = 28.
    2. page 2 contains exactly 3 rows.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:8].upper()
    prefix = f"CAPA-PAG-{uid}"
    qa_user_id = TEST_USERS["chief_quality"]["id"]

    try:
        for i in range(1, 29):
            await conn.execute(
                """
                INSERT INTO capa (
                    id, capa_no, raised_on, source, problem, status, raised_by
                ) VALUES (
                    gen_random_uuid(), $1, CURRENT_DATE, 'Internal QC', $2, 'Open', $3::uuid
                );
                """,
                f"{prefix}-{i:02d}",
                f"Paginated Problem Description {uid} #{i:02d}",
                uuid.UUID(qa_user_id),
            )

        # Page 1
        resp1 = await chief_quality_client.get(f"/capa?q={uid}&page=1&page_size=25")
        assert resp1.status_code == HTTP_200_OK
        assert "Showing <strong>25</strong> of <strong>28</strong> CAPA record" in resp1.text
        assert "Page 1 of 2" in resp1.text
        assert resp1.text.count(prefix) == 25

        # Page 2
        resp2 = await chief_quality_client.get(f"/capa?q={uid}&page=2&page_size=25")
        assert resp2.status_code == HTTP_200_OK
        assert "Showing <strong>3</strong> of <strong>28</strong> CAPA record" in resp2.text
        assert "Page 2 of 2" in resp2.text
        assert resp2.text.count(prefix) == 3
    finally:
        await conn.execute("DELETE FROM capa WHERE capa_no LIKE $1;", f"{prefix}%")
        await conn.close()

