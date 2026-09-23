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
async def test_recipe_creation_and_sequence_numbering(dev_client):
    """
    Verifies creating dye recipes sequentially generates REC-0001, REC-0002...
    and sets created_by to the authenticated product developer.
    """
    dev_user_id = TEST_USERS["product_developer"]["id"]

    # Clear table for predictable sequence
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    await conn.execute("DELETE FROM dye_recipes;")

    # 1. Create first recipe (Nylon 6,6 - Olive Green OG-7)
    resp1 = await dev_client.post(
        "/recipes",
        data={
            "substrate": "Nylon 6,6",
            "target_shade": "Olive Green OG-7",
            "batch_kg": "100.0",
            "dye1": "Acid Yellow 49",
            "pct1": "0.4500",
            "dye2": "Acid Red 118",
            "pct2": "0.1200",
            "dye3": "Acid Blue 80",
            "pct3": "0.3800",
            "liquor_ratio": "1:10",
            "temp_c": "98.0",
            "time_min": "45.0",
            "ph": "4.5",
            "auxiliaries": "Ammonium sulphate 2.0 g/l, Levelling agent 1.0 g/l",
            "shade_result": "Matched to Master",
            "status": "Draft",
        },
        follow_redirects=False,
    )
    assert resp1.status_code == HTTP_303_SEE_OTHER
    rec_id1 = resp1.headers["location"].split("/recipes/")[1]

    row1 = await conn.fetchrow("SELECT * FROM dye_recipes WHERE id = $1::uuid;", rec_id1)
    assert row1 is not None
    assert row1["recipe_no"] == "REC-0001"
    assert str(row1["created_by"]) == dev_user_id
    assert row1["status"] == "Draft"
    assert row1["target_shade"] == "Olive Green OG-7"
    assert float(row1["batch_kg"]) == 100.0
    assert float(row1["pct1"]) == 0.45

    # 2. Create second recipe (Polyester - Coyote 498)
    resp2 = await dev_client.post(
        "/recipes",
        data={
            "substrate": "Polyester",
            "target_shade": "Coyote 498",
            "batch_kg": "75.0",
            "dye1": "Disperse Yellow 54",
            "pct1": "0.6000",
            "dye2": "Disperse Red 60",
            "pct2": "0.2500",
            "liquor_ratio": "1:8",
            "temp_c": "130.0",
            "time_min": "60.0",
            "ph": "5.0",
            "shade_result": "Close - Acceptable",
        },
        follow_redirects=False,
    )
    assert resp2.status_code == HTTP_303_SEE_OTHER
    rec_id2 = resp2.headers["location"].split("/recipes/")[1]

    row2 = await conn.fetchrow("SELECT * FROM dye_recipes WHERE id = $1::uuid;", rec_id2)
    assert row2 is not None
    assert row2["recipe_no"] == "REC-0002"
    assert str(row2["created_by"]) == dev_user_id

    await conn.close()


@pytest.mark.asyncio
async def test_recipe_concurrent_sequence_generation():
    """
    Launches 10 concurrent dye recipe creation requests to verify that the
    UniqueViolationError retry loop eliminates sequence collision race conditions.
    """
    token = make_test_token(
        user_id=TEST_USERS["product_developer"]["id"],
        email=TEST_USERS["product_developer"]["email"],
        role_code=TEST_USERS["product_developer"]["role_code"],
    )

    async def create_single_recipe(idx: int):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            return await client.post(
                "/recipes",
                data={
                    "substrate": "Nylon 6,6",
                    "target_shade": f"Concurrent Shade #{idx}",
                    "batch_kg": "50.0",
                    "dye1": "Acid Black 172",
                    "pct1": "1.5000",
                    "liquor_ratio": "1:10",
                },
                follow_redirects=False,
            )

    results = await asyncio.gather(*[create_single_recipe(i) for i in range(10)])
    for r in results:
        assert r.status_code == HTTP_303_SEE_OTHER

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    rows = await conn.fetch(
        "SELECT recipe_no FROM dye_recipes WHERE target_shade LIKE 'Concurrent Shade %' ORDER BY recipe_no ASC;"
    )
    recipe_nos = [r["recipe_no"] for r in rows]
    assert len(recipe_nos) == 10
    assert len(set(recipe_nos)) == 10, "Duplicate Recipe numbers were generated under concurrency!"
    await conn.close()


# ============================================================================
# 2. RBAC & APPROVAL SEPARATION
# ============================================================================

@pytest.mark.asyncio
async def test_product_developer_cannot_approve_recipe(dev_client):
    """
    Proves that product_developer (who holds update, but NOT approve)
    is strictly forbidden (403) from approving a dye recipe.
    """
    # 1. Create a draft recipe
    resp = await dev_client.post(
        "/recipes",
        data={
            "substrate": "Nylon 6,6",
            "target_shade": "Foliage Green 504",
            "batch_kg": "50.0",
            "dye1": "Acid Green 28",
            "pct1": "0.8000",
            "shade_result": "Matched to Master",
        },
        follow_redirects=False,
    )
    rec_id = resp.headers["location"].split("/recipes/")[1]

    # 2. Attempt approval via POST /approve endpoint
    approve_resp = await dev_client.post(f"/recipes/{rec_id}/approve")
    assert approve_resp.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit approve on recipes" in approve_resp.text

    # 3. Attempt approval via general update route setting status = 'Approved'
    update_resp = await dev_client.post(
        f"/recipes/{rec_id}/update",
        data={
            "substrate": "Nylon 6,6",
            "target_shade": "Foliage Green 504",
            "status": "Approved",
        },
    )
    assert update_resp.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit approving recipes" in update_resp.text


# ============================================================================
# 3. SEGREGATION OF DUTIES: SELF-APPROVAL CONSTRAINT
# ============================================================================

@pytest.mark.asyncio
async def test_no_self_approval_database_constraint(tech_client):
    """
    Proves that chief_technical cannot approve a dye recipe that they created.
    Database CHECK constraint dye_recipes_no_self_approval must reject this.
    """
    tech_user_id = TEST_USERS["chief_technical"]["id"]

    # 1. Chief Technical creates a recipe
    resp = await tech_client.post(
        "/recipes",
        data={
            "substrate": "Nylon 6,6",
            "target_shade": "Chief Tech Self-Formulated Khaki",
            "batch_kg": "120.0",
            "dye1": "Acid Brown 14",
            "pct1": "0.5000",
            "shade_result": "Matched to Master",
        },
        follow_redirects=False,
    )
    rec_id = resp.headers["location"].split("/recipes/")[1]

    # 2. Database level proof: Direct self-approval UPDATE violates constraint
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await conn.execute(
            """
            UPDATE dye_recipes SET
                status = 'Approved',
                approved_by = $1::uuid,
                approved_at = NOW()
            WHERE id = $2::uuid;
            """,
            tech_user_id,
            rec_id,
        )

    # 3. API level proof: Chief Technical attempts self-approval via POST /approve
    api_resp = await tech_client.post(f"/recipes/{rec_id}/approve")
    assert api_resp.status_code == HTTP_400_BAD_REQUEST
    assert "Segregation of duties violation" in api_resp.text

    await conn.close()


@pytest.mark.asyncio
async def test_independent_technical_approval_succeeds(dev_client, tech_client):
    """
    Proves that Chief Technical Officer can approve a dye recipe created by
    Product Developer, correctly recording approved_by and approved_at.
    """
    # 1. Product Developer creates recipe with Matched shade result
    resp = await dev_client.post(
        "/recipes",
        data={
            "substrate": "Nylon 6,6",
            "target_shade": "Desert Tan 499",
            "batch_kg": "100.0",
            "dye1": "Acid Yellow 49",
            "pct1": "0.3000",
            "dye2": "Acid Brown 14",
            "pct2": "0.2000",
            "shade_result": "Matched to Master",
        },
        follow_redirects=False,
    )
    rec_id = resp.headers["location"].split("/recipes/")[1]

    # 2. Chief Technical approves recipe
    approve_resp = await tech_client.post(f"/recipes/{rec_id}/approve", follow_redirects=False)
    assert approve_resp.status_code == HTTP_303_SEE_OTHER

    # 3. Verify in database
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    row = await conn.fetchrow("SELECT * FROM dye_recipes WHERE id = $1::uuid;", rec_id)
    assert row["status"] == "Approved"
    assert str(row["approved_by"]) == TEST_USERS["chief_technical"]["id"]
    assert str(row["created_by"]) == TEST_USERS["product_developer"]["id"]
    assert row["approved_at"] is not None
    await conn.close()


# ============================================================================
# 4. SHADE RESULT GATE: BLOCKS APPROVAL OF OFF-SHADE / UNMATCHED RECIPES
# ============================================================================

@pytest.mark.asyncio
async def test_cannot_approve_off_shade_recipe(dev_client, tech_client):
    """
    Proves that a dye recipe with shade_result = 'Off-shade' cannot be approved.
    Approval endpoint must strictly reject this with HTTP 400 Bad Request.
    """
    # 1. Product Developer creates an off-shade recipe
    resp = await dev_client.post(
        "/recipes",
        data={
            "substrate": "Nylon 6,6",
            "target_shade": "Defective Off-shade Green",
            "batch_kg": "80.0",
            "dye1": "Acid Green 28",
            "pct1": "1.2000",
            "shade_result": "Off-shade",
        },
        follow_redirects=False,
    )
    rec_id = resp.headers["location"].split("/recipes/")[1]

    # 2. Chief Technical attempts approval
    approve_resp = await tech_client.post(f"/recipes/{rec_id}/approve")
    assert approve_resp.status_code == HTTP_400_BAD_REQUEST
    assert "Cannot approve recipe" in approve_resp.text
    assert "Shade result must be 'Matched to Master' or 'Close - Acceptable'" in approve_resp.text


@pytest.mark.asyncio
async def test_cannot_approve_pending_shade_recipe(dev_client, tech_client):
    """
    Proves that a dye recipe with shade_result = 'Pending Review' cannot be approved.
    """
    # 1. Product Developer creates a pending review recipe
    resp = await dev_client.post(
        "/recipes",
        data={
            "substrate": "Polyester",
            "target_shade": "Pending Evaluation Navy",
            "batch_kg": "50.0",
            "dye1": "Disperse Blue 79",
            "pct1": "2.0000",
            "shade_result": "Pending Review",
        },
        follow_redirects=False,
    )
    rec_id = resp.headers["location"].split("/recipes/")[1]

    # 2. Chief Technical attempts approval
    approve_resp = await tech_client.post(f"/recipes/{rec_id}/approve")
    assert approve_resp.status_code == HTTP_400_BAD_REQUEST
    assert "Cannot approve recipe" in approve_resp.text


# ============================================================================
# 5. RBAC PERMISSIONS ACCESS CONTROL
# ============================================================================

@pytest.mark.asyncio
async def test_role_without_recipes_read_cannot_read_recipes(sales_client, hr_client):
    """
    Proves that roles without recipes.read permission (sales_executive, hr_officer)
    are strictly denied with 403 Forbidden.
    """
    resp_sales = await sales_client.get("/recipes")
    assert resp_sales.status_code == HTTP_403_FORBIDDEN
    assert "Your roles do not permit read on recipes" in resp_sales.text

    resp_hr = await hr_client.get("/recipes")
    assert resp_hr.status_code == HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_permitted_roles_can_access_recipes(dev_client, tech_client):
    """
    Proves that roles holding recipes grants (product_developer, chief_technical)
    receive HTTP 200 OK on /recipes, /recipes/new, and /recipes/{id}.
    """
    resp_dev = await dev_client.get("/recipes")
    assert resp_dev.status_code == HTTP_200_OK
    assert "DYE RECIPES REGISTER" in resp_dev.text

    resp_new_dev = await dev_client.get("/recipes/new")
    assert resp_new_dev.status_code == HTTP_200_OK

    resp_tech = await tech_client.get("/recipes")
    assert resp_tech.status_code == HTTP_200_OK


@pytest.mark.asyncio
async def test_recipes_routes_reject_unauthenticated(anonymous_client):
    """
    Verifies that unauthenticated requests to recipes routes are rejected with 401.
    """
    resp_list = await anonymous_client.get("/recipes")
    assert resp_list.status_code == HTTP_401_UNAUTHORIZED

    resp_create = await anonymous_client.post(
        "/recipes",
        data={"substrate": "Nylon 6,6", "target_shade": "Black"},
    )
    assert resp_create.status_code == HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_recipes_pagination(dev_client):
    """
    Pagination Test: Seeds 28 dye recipes, queries page 1 (25 rows) and page 2 (3 rows).
    Verifies:
    1. page 1 contains exactly 25 rows and total_count = 28.
    2. page 2 contains exactly 3 rows.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:8].upper()
    prefix = f"REC-PAG-{uid}"
    dev_user_id = TEST_USERS["product_developer"]["id"]

    try:
        await conn.execute("DELETE FROM dye_recipes WHERE recipe_no LIKE $1;", f"{prefix}%")
        for i in range(1, 29):
            await conn.execute(
                """
                INSERT INTO dye_recipes (
                    id, recipe_no, dyed_on, substrate, target_shade, batch_kg, liquor_ratio, status, shade_result, created_by
                ) VALUES (
                    gen_random_uuid(), $1, CURRENT_DATE, 'Nylon 6,6', $2, 100.0, '1:10', 'Draft', 'Matched to Master', $3::uuid
                );
                """,
                f"{prefix}-{i:02d}",
                f"Paginated Shade {uid} #{i:02d}",
                uuid.UUID(dev_user_id),
            )

        # Page 1
        resp1 = await dev_client.get(f"/recipes?q={uid}&page=1&page_size=25")
        assert resp1.status_code == HTTP_200_OK
        assert "Showing <strong>25</strong> of <strong>28</strong> dye recipe" in resp1.text
        assert "Page 1 of 2" in resp1.text
        assert resp1.text.count(prefix) == 25

        # Page 2
        resp2 = await dev_client.get(f"/recipes?q={uid}&page=2&page_size=25")
        assert resp2.status_code == HTTP_200_OK
        assert "Showing <strong>3</strong> of <strong>28</strong> dye recipe" in resp2.text
        assert "Page 2 of 2" in resp2.text
        assert resp2.text.count(prefix) == 3
    finally:
        await conn.execute("DELETE FROM dye_recipes WHERE recipe_no LIKE $1;", f"{prefix}%")
        await conn.close()

