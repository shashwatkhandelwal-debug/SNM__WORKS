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
async def test_construction_creation_and_sequence_numbering(dev_client):
    """
    Verifies creating construction records sequentially generates CONST-0001, CONST-0002...
    and sets created_by to the authenticated product developer.
    """
    dev_user_id = TEST_USERS["product_developer"]["id"]

    # Clear table for predictable sequence
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    await conn.execute("UPDATE jobs SET construction_id = NULL;")
    await conn.execute("UPDATE skus SET construction_id = NULL;")
    await conn.execute("DELETE FROM constructions;")

    # 1. Create first construction (Narrow Woven)
    resp1 = await dev_client.post(
        "/constructions",
        data={
            "family": "Narrow Wovens",
            "product": "44mm Heavy Duty Webbing MIL-W-4088K Type VIII",
            "spec": "MIL-W-4088K Type VIII Class 1",
            "width_mm": "43.66",
            "weave": "2/2 Herringbone Twill",
            "warp_ends": "320",
            "warp_denier": "840.0",
            "picks_per_cm": "7.09",
            "weft_denier": "840.0",
            "warp_crimp": "5.0",
            "weft_crimp": "3.0",
            "warp_tenacity": "8.5",
            "efficiency": "85.0",
        },
        follow_redirects=False,
    )
    assert resp1.status_code == HTTP_303_SEE_OTHER
    const_id1 = resp1.headers["location"].split("/constructions/")[1]

    row1 = await conn.fetchrow("SELECT * FROM constructions WHERE id = $1::uuid;", const_id1)
    assert row1 is not None
    assert row1["spec_no"] == "CONST-0001"
    assert str(row1["created_by"]) == dev_user_id
    assert row1["status"] == "Draft"
    assert row1["family"] == "Narrow Wovens"
    assert float(row1["width_mm"]) == 43.66

    # 2. Create second construction (Broad Fabric)
    resp2 = await dev_client.post(
        "/constructions",
        data={
            "family": "Broad Fabric",
            "product": "PU Coated Technical Parachute Cloth",
            "width_cm": "150.0",
            "epi": "60.0",
            "ppi": "50.0",
            "warp_denier": "210.0",
            "weft_denier": "210.0",
            "warp_crimp": "4.0",
            "weft_crimp": "4.0",
            "finish": "Waterproof PU Coating",
        },
        follow_redirects=False,
    )
    assert resp2.status_code == HTTP_303_SEE_OTHER
    const_id2 = resp2.headers["location"].split("/constructions/")[1]

    row2 = await conn.fetchrow("SELECT * FROM constructions WHERE id = $1::uuid;", const_id2)
    assert row2 is not None
    assert row2["spec_no"] == "CONST-0002"
    assert str(row2["created_by"]) == dev_user_id

    await conn.close()


@pytest.mark.asyncio
async def test_construction_concurrent_sequence_generation():
    """
    Launches 10 concurrent construction creation requests to verify that the
    UniqueViolationError retry loop eliminates sequence collision race conditions.
    """
    token = make_test_token(
        user_id=TEST_USERS["product_developer"]["id"],
        email=TEST_USERS["product_developer"]["email"],
        role_code=TEST_USERS["product_developer"]["role_code"],
    )

    init_conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    await init_conn.execute("DELETE FROM constructions WHERE product LIKE 'Concurrent stress test webbing %';")
    await init_conn.close()

    async def create_single_construction(idx: int):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            return await client.post(
                "/constructions",
                data={
                    "family": "Narrow Wovens",
                    "product": f"Concurrent stress test webbing #{idx}",
                    "width_mm": "25.0",
                    "warp_ends": "150",
                    "warp_denier": "420.0",
                    "picks_per_cm": "8.0",
                    "weft_denier": "420.0",
                },
                follow_redirects=False,
            )

    results = await asyncio.gather(*[create_single_construction(i) for i in range(10)])
    for r in results:
        assert r.status_code == HTTP_303_SEE_OTHER

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    rows = await conn.fetch(
        "SELECT spec_no FROM constructions WHERE product LIKE 'Concurrent stress test webbing %' ORDER BY spec_no ASC;"
    )
    spec_nos = [r["spec_no"] for r in rows]
    assert len(spec_nos) == 10
    assert len(set(spec_nos)) == 10, "Duplicate Spec numbers were generated under concurrency!"
    await conn.close()


# ============================================================================
# 2. LIVE TEXTILES CALCULATION INTEGRATION
# ============================================================================

@pytest.mark.asyncio
async def test_construction_live_calculation_endpoint(dev_client):
    """
    Tests POST /constructions/calculate HTMX endpoint for live engineering calculations.
    Verifies that the response contains accurate values computed via textiles/ library.
    """
    # 1. Narrow Woven Calculation (MIL-W-4088K Type VIII)
    resp = await dev_client.post(
        "/constructions/calculate",
        data={
            "family": "Narrow Wovens",
            "width_mm": "43.656",
            "warp_ends": "320",
            "warp_denier": "840.0",
            "picks_per_cm": "7.0866",
            "weft_denier": "840.0",
            "warp_crimp": "5.0",
            "weft_crimp": "3.0",
            "warp_tenacity": "8.5",
            "efficiency": "85.0",
        },
    )
    assert resp.status_code == HTTP_200_OK
    html = resp.text
    # Warp mass: 31.36 g/m, Total mass: 34.33 g/m, Break: ~4282 lbf
    assert "31.36" in html
    assert "34.33" in html
    assert "4282" in html or "4281" in html

    # 2. Broad Fabric Calculation with Jamming Warning (100 EPI x 840D)
    resp_jam = await dev_client.post(
        "/constructions/calculate",
        data={
            "family": "Broad Fabric",
            "epi": "100.0",
            "ppi": "50.0",
            "warp_denier": "840.0",
            "weft_denier": "840.0",
        },
    )
    assert resp_jam.status_code == HTTP_200_OK
    assert "Jamming Warning" in resp_jam.text


# ============================================================================
# 3. RBAC & APPROVAL SEPARATION
# ============================================================================

@pytest.mark.asyncio
async def test_product_developer_cannot_approve_construction(dev_client):
    """
    Proves that product_developer (who holds update, but NOT approve)
    is strictly forbidden (403) from approving a construction.
    """
    # 1. Create a draft construction
    resp = await dev_client.post(
        "/constructions",
        data={
            "family": "Narrow Wovens",
            "product": "Approval test webbing",
            "width_mm": "25.0",
            "warp_ends": "100",
            "warp_denier": "500.0",
            "picks_per_cm": "6.0",
            "weft_denier": "500.0",
        },
        follow_redirects=False,
    )
    const_id = resp.headers["location"].split("/constructions/")[1]

    # 2. Attempt approval via POST /approve endpoint
    approve_resp = await dev_client.post(f"/constructions/{const_id}/approve")
    assert approve_resp.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit approve on constructions" in approve_resp.text

    # 3. Attempt approval via general update route setting status = 'Approved'
    update_resp = await dev_client.post(
        f"/constructions/{const_id}/update",
        data={
            "family": "Narrow Wovens",
            "product": "Approval test webbing",
            "status": "Approved",
        },
    )
    assert update_resp.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit approving constructions" in update_resp.text


# ============================================================================
# 4. SEGREGATION OF DUTIES: SELF-APPROVAL CONSTRAINT
# ============================================================================

@pytest.mark.asyncio
async def test_no_self_approval_database_constraint(tech_client):
    """
    Proves that chief_technical cannot approve a construction that they created.
    Database CHECK constraint constructions_no_self_approval must reject this.
    """
    tech_user_id = TEST_USERS["chief_technical"]["id"]

    # 1. Chief Technical creates a construction
    resp = await tech_client.post(
        "/constructions",
        data={
            "family": "Narrow Wovens",
            "product": "Chief Tech self-created sling",
            "width_mm": "50.0",
            "warp_ends": "200",
            "warp_denier": "1000.0",
            "picks_per_cm": "5.0",
            "weft_denier": "1000.0",
        },
        follow_redirects=False,
    )
    const_id = resp.headers["location"].split("/constructions/")[1]

    # 2. Database level proof: Direct self-approval UPDATE violates constraint
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await conn.execute(
            """
            UPDATE constructions SET
                status = 'Approved',
                approved_by = $1::uuid,
                approved_at = NOW()
            WHERE id = $2::uuid;
            """,
            tech_user_id,
            const_id,
        )

    # 3. API level proof: Chief Technical attempts self-approval via POST /approve
    api_resp = await tech_client.post(f"/constructions/{const_id}/approve")
    assert api_resp.status_code == HTTP_400_BAD_REQUEST
    assert "Segregation of duties violation" in api_resp.text

    await conn.close()


@pytest.mark.asyncio
async def test_independent_technical_approval_succeeds(dev_client, tech_client):
    """
    Proves that Chief Technical Officer can approve a construction created by
    Product Developer, correctly recording approved_by and approved_at.
    """
    # 1. Product Developer creates construction
    resp = await dev_client.post(
        "/constructions",
        data={
            "family": "Narrow Wovens",
            "product": "Valid 25mm Nylon Tape for R&D",
            "width_mm": "25.0",
            "warp_ends": "180",
            "warp_denier": "840.0",
            "picks_per_cm": "8.0",
            "weft_denier": "840.0",
            "warp_tenacity": "8.5",
            "efficiency": "85.0",
        },
        follow_redirects=False,
    )
    const_id = resp.headers["location"].split("/constructions/")[1]

    # 2. Chief Technical approves construction
    approve_resp = await tech_client.post(f"/constructions/{const_id}/approve", follow_redirects=False)
    assert approve_resp.status_code == HTTP_303_SEE_OTHER

    # 3. Verify in database
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    row = await conn.fetchrow("SELECT * FROM constructions WHERE id = $1::uuid;", const_id)
    assert row["status"] == "Approved"
    assert str(row["approved_by"]) == TEST_USERS["chief_technical"]["id"]
    assert str(row["created_by"]) == TEST_USERS["product_developer"]["id"]
    assert row["approved_at"] is not None
    await conn.close()


# ============================================================================
# 5. PHYSICS VALIDATION: BLOCKS APPROVAL OF JAMMED SPECIFICATIONS
# ============================================================================

@pytest.mark.asyncio
async def test_cannot_approve_jammed_fabric_construction(dev_client, tech_client):
    """
    Proves that a construction with physically impossible cover factor (> 28.0)
    cannot be approved, even by chief_technical.
    """
    # 1. Product developer creates jammed fabric specification
    resp = await dev_client.post(
        "/constructions",
        data={
            "family": "Broad Fabric",
            "product": "Over-dense Jammed Fabric",
            "epi": "120.0",
            "ppi": "60.0",
            "warp_denier": "840.0",
            "weft_denier": "840.0",
        },
        follow_redirects=False,
    )
    const_id = resp.headers["location"].split("/constructions/")[1]

    # 2. Chief Technical attempts approval
    approve_resp = await tech_client.post(f"/constructions/{const_id}/approve")
    assert approve_resp.status_code == HTTP_400_BAD_REQUEST
    assert "Cannot approve construction" in approve_resp.text
    assert "exceeds physical limit of 28.0" in approve_resp.text


# ============================================================================
# 6. RBAC PERMISSIONS ACCESS CONTROL
# ============================================================================

@pytest.mark.asyncio
async def test_role_without_constructions_read_cannot_read_constructions(sales_client, hr_client):
    """
    Proves that roles without constructions.read permission (sales_executive, hr_officer)
    are strictly denied with 403 Forbidden.
    """
    resp_sales = await sales_client.get("/constructions")
    assert resp_sales.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit read on constructions" in resp_sales.text

    resp_hr = await hr_client.get("/constructions")
    assert resp_hr.status_code == HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_permitted_roles_can_access_constructions(dev_client, tech_client):
    """
    Proves that roles holding constructions grants (product_developer, chief_technical)
    receive HTTP 200 OK on /constructions and /constructions/new.
    """
    resp_dev = await dev_client.get("/constructions")
    assert resp_dev.status_code == HTTP_200_OK
    assert "ENGINEERING CONSTRUCTIONS" in resp_dev.text

    resp_new_dev = await dev_client.get("/constructions/new")
    assert resp_new_dev.status_code == HTTP_200_OK

    resp_tech = await tech_client.get("/constructions")
    assert resp_tech.status_code == HTTP_200_OK


@pytest.mark.asyncio
async def test_constructions_routes_reject_unauthenticated(anonymous_client):
    """
    Verifies that unauthenticated requests to constructions routes are rejected with 401.
    """
    resp_list = await anonymous_client.get("/constructions")
    assert resp_list.status_code == HTTP_401_UNAUTHORIZED

    resp_create = await anonymous_client.post(
        "/constructions",
        data={"family": "Narrow Wovens", "product": "Test Product"},
    )
    assert resp_create.status_code == HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_constructions_pagination(dev_client):
    """
    Pagination Test: Seeds 28 constructions, queries page 1 (25 rows) and page 2 (3 rows).
    Verifies:
    1. page 1 contains exactly 25 rows and total_count = 28.
    2. page 2 contains exactly 3 rows.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:8].upper()
    prefix = f"CONST-PAG-{uid}"
    dev_user_id = TEST_USERS["product_developer"]["id"]

    try:
        for i in range(1, 29):
            await conn.execute(
                """
                INSERT INTO constructions (
                    id, spec_no, created_on, status, family, product, width_mm, weave,
                    warp_denier, weft_denier, warp_ends, picks_per_cm, created_by
                ) VALUES (
                    gen_random_uuid(), $1, CURRENT_DATE, 'Draft', 'Narrow Wovens', $2,
                    25.0, 'Plain', 840.0, 840.0, 180, 8.0, $3::uuid
                );
                """,
                f"{prefix}-{i:02d}",
                f"Paginated Webbing {uid} #{i:02d}",
                uuid.UUID(dev_user_id),
            )

        # Page 1
        resp1 = await dev_client.get(f"/constructions?q={uid}&page=1&page_size=25")
        assert resp1.status_code == HTTP_200_OK
        assert "Showing <strong>25</strong> of <strong>28</strong> construction specification" in resp1.text
        assert "Page 1 of 2" in resp1.text
        assert resp1.text.count(prefix) == 25

        # Page 2
        resp2 = await dev_client.get(f"/constructions?q={uid}&page=2&page_size=25")
        assert resp2.status_code == HTTP_200_OK
        assert "Showing <strong>3</strong> of <strong>28</strong> construction specification" in resp2.text
        assert "Page 2 of 2" in resp2.text
        assert resp2.text.count(prefix) == 3
    finally:
        await conn.execute("DELETE FROM constructions WHERE spec_no LIKE $1;", f"{prefix}%")
        await conn.close()

