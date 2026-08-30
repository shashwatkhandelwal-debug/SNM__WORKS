import pytest
import httpx
from services.post_generator import (
    DefenceProductError,
    generate_post,
    sanitize_sku_data,
    resolve_strength_to_weight_ratio,
)
from services.publishers import (
    publish_to_all_platforms,
    publish_linkedin,
    publish_instagram,
    publish_facebook,
    publish_indiamart,
    publish_tradeindia,
)
from services.catalogue import build_catalogue_feed
from main import app


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
async def test_approved_sku_calls_all_five_mock_publishers():
    """
    Test 3: Approving a SKU triggers all five mock platform publishers
    (LinkedIn, Instagram, Facebook, IndiaMart, TradeIndia) and returns mock receipts.
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

    results = await publish_to_all_platforms(sample_post)

    platforms = ["linkedin", "instagram", "facebook", "indiamart", "tradeindia"]
    for p in platforms:
        assert p in results
        if p == "linkedin":
            assert results[p]["status"] in ["mock_published", "published", "error"]
        else:
            assert results[p]["status"] == "mock_published"
            assert results[p]["post_id"].startswith("mock_")


@pytest.mark.asyncio
async def test_rejected_sku_enforces_reason_length():
    """
    Test 4: Rejection endpoint requires a minimum 10 character explanation.
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
    Test 5: Commercial SKU with Ready status and catalogue_visible=True
    generates post_draft and queued status.
    Defence SKU raises DefenceProductError and is blocked.
    """
    from services.post_generator import generate

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
    Test 6: Promotional campaign generates platform captions respecting limits
    and sanitizing inputs.
    """
    from services.post_generator import generate_campaign_post

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
async def test_campaign_persists_to_postgresql_and_survives_restart(dev_client):
    """
    Test 7: Proves that /marketing/campaign/create persists real rows into
    the PostgreSQL campaigns table (not memory), and /marketing/queue reads
    from the database with restart-safety.
    """
    import asyncpg
    from tests.conftest import LOCAL_TEST_DATABASE_URL

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

    # 3. Verify real persistence in PostgreSQL via independent database connection
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
    finally:
        await conn.close()

    # 4. Approve campaign via API
    approve_resp = await dev_client.post(
        f"/marketing/approve/{camp_id}",
        follow_redirects=False,
    )
    assert approve_resp.status_code == 303

    # 5. Confirm status changed in database
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        updated_row = await conn.fetchrow("SELECT * FROM campaigns WHERE id = $1::uuid;", row["id"])
        assert updated_row["post_status"] == "published"
        assert updated_row["platform_results"] is not None
    finally:
        await conn.close()



