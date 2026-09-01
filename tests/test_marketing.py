import pytest
import httpx
import uuid
import asyncpg
from datetime import datetime

from config import settings
from main import app
from services.post_generator import (
    DefenceProductError,
    generate,
    generate_post,
    generate_campaign_post,
    sanitize_sku_data,
    resolve_strength_to_weight_ratio,
)
from services.publishers import (
    publish_to_all_platforms,
    publish_post_to_platforms,
    publish_linkedin,
    publish_instagram,
    publish_facebook,
    publish_indiamart,
    publish_tradeindia,
    SUPPORTED_PLATFORMS,
)
from services.catalogue import build_catalogue_feed
from tests.conftest import LOCAL_TEST_DATABASE_URL, make_test_token, TEST_USERS


def test_mil_spec_sku_raises_defence_product_error():
    """
    Test 1: Any SKU linked to a specification starting with MIL- is strictly blocked
    and raises DefenceProductError. It must never enter the marketing queue.
    """
    mil_sku = {
        "sku_code": "MIL-SPEC-4088-T8",
        "family": "Narrow woven",
        "title": "MIL-W-4088K Type VIII Webbing",
        "standard": "MIL-W-4088K Type VIII",
        "material": "Nylon 6.6",
        "blurb": "Military specification parachute webbing",
    }

    with pytest.raises(DefenceProductError) as excinfo:
        generate_post(mil_sku)

    assert "governed by defence specification" in str(excinfo.value)

    # Test lowercase mil- variant
    mil_sku_lower = {
        "sku_code": "MIL-SPEC-LOWER",
        "standard": "mil-w-4088k",
    }
    with pytest.raises(DefenceProductError):
        generate_post(mil_sku_lower)

    # Test catalogue feed excludes MIL- specs
    catalogue_result = build_catalogue_feed([
        mil_sku,
        {
            "sku_code": "COMM-NW-101",
            "family": "Narrow woven",
            "title": "Commercial Cargo Webbing",
            "standard": "IS 15041",
            "material": "Polyester",
            "status": "Published",
            "catalogue_visible": True,
        }
    ])
    assert len(catalogue_result) == 1
    assert catalogue_result[0]["sku_code"] == "COMM-NW-101"


def test_post_generator_strips_commercial_and_sensitive_fields():
    """
    Test 2: Prohibited commercial fields (price, cost, margin, customer name,
    order quantity, job number) are stripped before post generation.
    """
    raw_sku_with_leaks = {
        "sku_code": "SNM-COMM-001",
        "family": "Narrow woven",
        "title": "Heavy Duty Industrial Webbing 50mm",
        "standard": "EN 12195-2",
        "material": "High Tenacity Polyester",
        "blurb": "Certified cargo lashing webbing with low elongation.",
        "price": 185.50,
        "cost": 110.00,
        "margin": 0.40,
        "customer_name": "Ordnance Factory Kanpur",
        "customer_id": "cust-999-restricted",
        "order_quantity": 25000,
        "job_number": "JOB-2026-AUG-991",
    }

    clean = sanitize_sku_data(raw_sku_with_leaks)
    assert "price" not in clean
    assert "cost" not in clean
    assert "margin" not in clean
    assert "customer_name" not in clean
    assert "customer_id" not in clean
    assert "order_quantity" not in clean
    assert "job_number" not in clean

    post = generate_post(raw_sku_with_leaks)
    post_str = str(post)

    # Assert no sensitive values leaked into post captions or properties
    assert "185.50" not in post_str
    assert "110.00" not in post_str
    assert "cust-999-restricted" not in post_str
    assert "JOB-2026-AUG-991" not in post_str

    # Strength-to-weight ratio must never be null or empty
    assert post["strength_to_weight_ratio"] is not None
    assert len(post["strength_to_weight_ratio"]) > 0


def test_strength_to_weight_ratio_calculation_and_fallback():
    """
    Strength to weight ratio must be breaking strength / weight in g/m,
    or fallback to 'Contact us for datasheet' — never null or empty.
    """
    # 1. With linked construction
    construction = {
        "warp_ends": 300,
        "warp_denier": 840,
        "picks_per_cm": 15,
        "width_mm": 50,
        "weft_denier": 840,
        "warp_crimp": 2.0,
        "weft_crimp": 3.0,
    }
    ratio_str = resolve_strength_to_weight_ratio(
        breaking_strength_kgf=1800.0,
        construction=construction,
    )
    assert "kgf/(g/m)" in ratio_str

    # 2. Truly unavailable fallback
    fallback_str = resolve_strength_to_weight_ratio(
        breaking_strength_kgf=None,
        construction=None,
        spec_requirements=None,
    )
    assert fallback_str == "Contact us for datasheet"


@pytest.mark.asyncio
async def test_standardized_mock_publisher_contract():
    """
    Test 3: Confirms all 5 mock publishers conform to the standardized contract:
    success (bool), platform (str), status ("mock_published", NEVER "published"),
    post_id (str), url (str), error (None), published_at (ISO timestamp).
    """
    sample_post = {
        "sku_code": "SNM-TEST-APPROVE",
        "title": "Industrial Slings Webbing 50mm",
        "standard": "IS 15041",
        "material": "Polyester",
        "breaking_strength": "2000 kgf",
        "strength_to_weight_ratio": "35.20 kgf/(g/m)",
        "use_case": "Lifting slings and cargo tie-downs",
    }

    publishers = [
        publish_linkedin,
        publish_instagram,
        publish_facebook,
        publish_indiamart,
        publish_tradeindia,
    ]

    for pub_fn in publishers:
        res = await pub_fn(sample_post)
        assert res["success"] is True
        # Critical assertion: status MUST be "mock_published", NEVER "published"
        assert res["status"] == "mock_published"
        assert res["status"] != "published"
        assert res["platform"] in SUPPORTED_PLATFORMS
        assert res["post_id"].startswith("mock_")
        assert res["url"].startswith("https://")
        assert res["error"] is None
        assert isinstance(res["published_at"], str)
        assert "T" in res["published_at"]


@pytest.mark.asyncio
async def test_selective_platform_publishing():
    """
    Test 4: publish_post_to_platforms only dispatches to the requested subset of platforms.
    """
    sample_post = {
        "sku_code": "SNM-SELECTIVE",
        "title": "Narrow Cargo Tape 25mm",
    }

    # Dispatch to only LinkedIn and Instagram
    results = await publish_post_to_platforms(sample_post, platforms=["linkedin", "instagram"])
    assert set(results.keys()) == {"linkedin", "instagram"}
    assert results["linkedin"]["status"] == "mock_published"
    assert results["instagram"]["status"] == "mock_published"
    assert "facebook" not in results
    assert "indiamart" not in results
    assert "tradeindia" not in results


@pytest.mark.asyncio
async def test_rejected_sku_enforces_reason_length():
    """
    Test 5: Rejection endpoint requires a minimum 10 character explanation.
    """
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Unauthenticated request redirects
        res_unauth = await client.post(
            "/marketing/reject/dummy-id",
            data={"reason": "Valid reason exceeding ten characters."},
            follow_redirects=False,
        )
        assert res_unauth.status_code == 303

        # 2. Public catalogue route is publicly accessible without login
        res_cat = await client.get("/catalogue")
        assert res_cat.status_code == 200
        assert "TECHNICAL TEXTILES CATALOGUE" in res_cat.text


def test_sku_generate_post_order_of_operations():
    """
    Test 6: Commercial SKU with Ready status and catalogue_visible=True
    generates post_draft and queued status.
    Defence SKU raises DefenceProductError and is blocked.
    """
    commercial_sku = {
        "sku_code": "SNM-TEST-50",
        "family": "Narrow woven",
        "title": "Test Webbing 50mm",
        "standard": "IS 1969",
        "material": "Nylon",
        "blurb": "50mm high tensile industrial webbing",
        "status": "Ready",
        "catalogue_visible": True,
    }

    draft = generate(commercial_sku)
    assert draft["sku_code"] == "SNM-TEST-50"
    assert draft["standard"] == "IS 1969"
    assert "linkedin" in draft["captions"]
    assert "Test Webbing 50mm" in draft["captions"]["linkedin"]

    defence_sku = {
        "sku_code": "MIL-TEST-4088",
        "family": "Narrow woven",
        "title": "Type VIII Parachute Webbing",
        "standard": "MIL-W-4088K",
        "material": "Nylon 6.6",
        "status": "Ready",
        "catalogue_visible": True,
    }

    with pytest.raises(DefenceProductError):
        generate(defence_sku)


def test_campaign_generation_and_caption_formatting():
    """
    Test 7: Promotional campaign generates platform captions respecting limits
    and sanitizing inputs.
    """
    camp = {
        "occasion": "Independence Day 2026",
        "headline": "Proud to manufacture in Kanpur, India",
        "body": "On this Independence Day, Swadeshi Niwar Mills celebrates 77 years of Indian independence. We manufacture technical textiles in Kanpur — narrow wovens, fabrics and cordage — supplying defence, industrial and export markets. Jai Hind.",
        "platforms": ["linkedin", "instagram", "facebook", "indiamart", "tradeindia"],
    }

    draft = generate_campaign_post(camp)
    assert draft["type"] == "campaign"
    assert draft["occasion"] == "Independence Day 2026"
    assert "linkedin" in draft["captions"]
    assert "Proud to manufacture in Kanpur, India" in draft["captions"]["linkedin"]
    assert len(draft["captions"]["linkedin"]) <= 3000
    assert len(draft["captions"]["instagram"]) <= 2200
    assert len(draft["captions"]["indiamart"]) <= 500
    assert len(draft["captions"]["tradeindia"]) <= 500


@pytest.mark.asyncio
async def test_campaign_featuring_mil_spec_sku_is_rejected_with_defence_message(dev_client):
    """
    Test 8: Bug #6 Check — Creating a promotional campaign featuring a real MIL-spec SKU
    is strictly rejected with HTTP 400 and the defence security message.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    mil_sku_id = uuid.uuid4()
    try:
        # Seed a real MIL-spec SKU in database
        await conn.execute(
            """
            INSERT INTO skus (id, sku_code, family, title, standard, material, status, catalogue_visible, post_status)
            VALUES ($1, 'MIL-W-4088-TEST-CAMPAIGN', 'Narrow woven', 'Defence Parachute Webbing', 'MIL-W-4088K Type VII', 'Nylon 6.6', 'Ready', false, 'none')
            ON CONFLICT (sku_code) DO NOTHING;
            """,
            mil_sku_id
        )

        # Attempt to create campaign featuring the defence SKU
        resp = await dev_client.post(
            "/marketing/campaign/create",
            data={
                "occasion": "DefExpo 2026",
                "headline": "Featured Military Webbing",
                "body": "Check out our MIL-spec parachute webbing.",
                "featured_sku_id": str(mil_sku_id),
                "platforms": ["linkedin", "instagram"],
            },
            follow_redirects=False,
        )

        # Must be rejected with HTTP 400
        assert resp.status_code == 400
        assert "Defence specification SKUs cannot be featured in marketing campaigns" in resp.json()["detail"]
    finally:
        await conn.execute("DELETE FROM skus WHERE id = $1;", mil_sku_id)
        await conn.close()


@pytest.mark.asyncio
async def test_state_machine_guard_on_approve_and_reject(dev_client):
    """
    Test 9: State Machine Guard — Only items in 'queued' status can be approved or rejected.
    Attempting to re-approve an already published item or re-reject a rejected item returns HTTP 400.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    camp_id = uuid.uuid4()
    try:
        # 1. Create a queued campaign
        await conn.execute(
            """
            INSERT INTO campaigns (id, occasion, headline, body, post_status, platforms)
            VALUES ($1, 'Test State Machine', 'State Guard Headline', 'Body copy', 'queued', ARRAY['linkedin', 'instagram']);
            """,
            camp_id
        )

        # 2. First approval succeeds (transitions queued -> published)
        approve_resp1 = await dev_client.post(f"/marketing/approve/{camp_id}", follow_redirects=False)
        assert approve_resp1.status_code == 303

        # Verify status is published
        row = await conn.fetchrow("SELECT post_status, platform_results FROM campaigns WHERE id = $1;", camp_id)
        assert row["post_status"] == "published"
        platform_res = row["platform_results"]
        if isinstance(platform_res, str):
            import json
            platform_res = json.loads(platform_res)
        # Verify platform results use mock_published
        assert platform_res["linkedin"]["status"] == "mock_published"
        assert platform_res["linkedin"]["status"] != "published"

        # 3. Second approval attempt on already-published campaign is blocked with HTTP 400
        approve_resp2 = await dev_client.post(f"/marketing/approve/{camp_id}", follow_redirects=False)
        assert approve_resp2.status_code == 400
        assert "is currently in 'published' status and cannot be approved" in approve_resp2.json()["detail"]

        # 4. Attempt to reject an already-published campaign is also blocked
        reject_resp = await dev_client.post(
            f"/marketing/reject/{camp_id}",
            data={"reason": "Cannot reject already published post."},
            follow_redirects=False,
        )
        assert reject_resp.status_code == 400
        assert "is currently in 'published' status and cannot be rejected" in reject_resp.json()["detail"]
    finally:
        await conn.execute("DELETE FROM campaigns WHERE id = $1;", camp_id)
        await conn.close()


@pytest.mark.asyncio
async def test_campaign_rejection_htmx_card_id_matches(dev_client):
    """
    Test 10: Bug #2 Check — Rejecting a campaign via HTMX returns camp-card-{id}, NOT sku-card-{id}.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    camp_id = uuid.uuid4()
    try:
        await conn.execute(
            """
            INSERT INTO campaigns (id, occasion, headline, body, post_status, platforms)
            VALUES ($1, 'HTMX Target Check', 'HTMX Headline', 'Body copy for rejection', 'queued', ARRAY['linkedin']);
            """,
            camp_id
        )

        # Reject via HTMX request
        reject_resp = await dev_client.post(
            f"/marketing/reject/{camp_id}",
            data={"reason": "Copy needs significant technical rewrite."},
            headers={"HX-Request": "true"},
        )

        assert reject_resp.status_code == 200
        # Crucial check: must have id="camp-card-{camp_id}", NEVER "sku-card-"
        assert f'id="camp-card-{camp_id}"' in reject_resp.text
        assert "Campaign Draft Rejected" in reject_resp.text
        assert "Copy needs significant technical rewrite." in reject_resp.text

        # Verify in database
        row = await conn.fetchrow("SELECT post_status, rejection_reason FROM campaigns WHERE id = $1;", camp_id)
        assert row["post_status"] == "rejected"
        assert row["rejection_reason"] == "Copy needs significant technical rewrite."
    finally:
        await conn.execute("DELETE FROM campaigns WHERE id = $1;", camp_id)
        await conn.close()


@pytest.mark.asyncio
async def test_campaign_persists_to_postgresql_and_survives_restart(dev_client):
    """
    Test 11: Proves that /marketing/campaign/create persists real rows into
    the PostgreSQL campaigns table, and only dispatches to selected platforms.
    """
    form_data = {
        "occasion": "Republic Day 2027",
        "headline": "High-Tenacity Technical Webbing Made in Kanpur",
        "body": "Swadeshi Niwar Mills delivers military and commercial grade technical textiles engineered for extreme strength.",
        "platforms": ["linkedin", "instagram"],
    }

    # 1. Create campaign via API
    resp = await dev_client.post(
        "/marketing/campaign/create",
        data=form_data,
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # 2. Query queue view
    queue_resp = await dev_client.get("/marketing/queue")
    assert queue_resp.status_code == 200
    assert "Republic Day 2027" in queue_resp.text

    # 3. Verify real persistence in PostgreSQL
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        row = await conn.fetchrow(
            "SELECT * FROM campaigns WHERE occasion = $1 AND post_status = 'queued';",
            "Republic Day 2027",
        )
        assert row is not None, "Campaign was NOT found in real PostgreSQL campaigns table!"
        camp_id = str(row["id"])
        assert row["headline"] == "High-Tenacity Technical Webbing Made in Kanpur"
        assert row["post_draft"] is not None
        assert row["platforms"] == ["linkedin", "instagram"]
    finally:
        await conn.close()

    # 4. Approve campaign via API
    approve_resp = await dev_client.post(
        f"/marketing/approve/{camp_id}",
        follow_redirects=False,
    )
    assert approve_resp.status_code == 303

    # 5. Confirm status changed in database and ONLY selected platforms were published
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        updated_row = await conn.fetchrow("SELECT * FROM campaigns WHERE id = $1::uuid;", row["id"])
        assert updated_row["post_status"] == "published"
        platform_res = updated_row["platform_results"]
        if isinstance(platform_res, str):
            import json
            platform_res = json.loads(platform_res)
        assert set(platform_res.keys()) == {"linkedin", "instagram"}
        assert platform_res["linkedin"]["status"] == "mock_published"
        assert platform_res["linkedin"]["status"] != "published"
        assert platform_res["instagram"]["status"] == "mock_published"
        assert platform_res["instagram"]["status"] != "published"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_campaign_form_creation_and_queue_html_card_rendering(dev_client):
    """
    Test 13: End-to-end campaign form creation, HTMX graphic preview,
    and verification of rendered campaign card in /marketing/queue.
    """
    # 1. Verify GET /marketing/campaign redirects to /marketing/queue
    index_res = await dev_client.get("/marketing/campaign", follow_redirects=False)
    assert index_res.status_code == 303
    assert index_res.headers["location"] == "/marketing/queue"

    # 2. Verify form action on /marketing/campaign/new
    form_res = await dev_client.get("/marketing/campaign/new")
    assert form_res.status_code == 200
    assert 'action="/marketing/campaign/create"' in form_res.text
    assert 'hx-post="/marketing/campaign/generate-image"' in form_res.text

    # 3. Test HTMX live graphic preview button endpoint
    preview_res = await dev_client.post(
        "/marketing/campaign/generate-image",
        data={
            "occasion": "Independence Day 2026",
            "headline": "Proud to manufacture in Kanpur, India",
            "body": "Celebrating Indian technical textile engineering.",
        },
    )
    assert preview_res.status_code == 200
    assert "Live Branded Graphic Preview" in preview_res.text
    assert "/marketing/campaign/image-preview?" in preview_res.text

    # 4. Submit form payload to /marketing/campaign/create
    form_data = {
        "occasion": "Independence Day 2026",
        "headline": "Proud to manufacture in Kanpur, India",
        "body": "Swadeshi Niwar Mills technical textiles engineered for extreme strength.",
        "platforms": ["linkedin", "instagram", "facebook", "indiamart", "tradeindia"],
    }
    submit_res = await dev_client.post(
        "/marketing/campaign/create",
        data=form_data,
        follow_redirects=False,
    )
    assert submit_res.status_code == 303
    assert submit_res.headers["location"] == "/marketing/queue"

    # 5. Fetch /marketing/queue and assert rendered campaign card
    queue_res = await dev_client.get("/marketing/queue")
    assert queue_res.status_code == 200
    assert "Independence Day 2026" in queue_res.text
    assert "Proud to manufacture in Kanpur, India" in queue_res.text
    assert "camp-card-" in queue_res.text
    assert "CAMPAIGN" in queue_res.text

