import io
import pytest
from PIL import Image
import httpx
from main import app
from config import settings
from services.image_processor import brand_product_image
from services.campaign_image import generate_campaign_graphic
from services.post_generator import check_and_queue


def test_brand_product_image_dimensions_and_banners():
    """
    Test 1: brand_product_image() creates 1200px wide JPEG with top & bottom 80px olive banners.
    """
    # Create a 600x400 test image in memory
    raw_img = Image.new("RGB", (600, 400), color=(200, 200, 200))
    buf = io.BytesIO()
    raw_img.save(buf, format="JPEG")
    raw_bytes = buf.getvalue()

    branded_bytes = brand_product_image(
        image_bytes=raw_bytes,
        sku_code="SNM-TEST-50",
        standard="IS 1969",
        material="Nylon",
        breaking_strength="2000 kgf",
        family="Narrow woven",
    )

    out_img = Image.open(io.BytesIO(branded_bytes))
    assert out_img.format == "JPEG"
    assert out_img.size[0] == 1200
    # Expected height: (1200 * (400/600)) + 160 = 800 + 160 = 960
    assert out_img.size[1] == 960

    # Verify top banner pixel is olive green (#4C5C33 => rgb(76, 92, 51))
    top_pixel = out_img.getpixel((10, 10))
    assert abs(top_pixel[0] - 76) < 10
    assert abs(top_pixel[1] - 92) < 10
    assert abs(top_pixel[2] - 51) < 10

    # Verify bottom banner pixel is olive green
    bottom_pixel = out_img.getpixel((10, 950))
    assert abs(bottom_pixel[0] - 76) < 10
    assert abs(bottom_pixel[1] - 92) < 10
    assert abs(bottom_pixel[2] - 51) < 10


def test_campaign_image_generation():
    """
    Test 2: generate_campaign_graphic() creates valid 1200x630 PNG card with olive background.
    """
    png_bytes = generate_campaign_graphic(
        occasion="Independence Day 2026",
        headline="Proud to manufacture in Kanpur, India",
        body="Swadeshi Niwar Mills technical textiles.",
    )

    out_img = Image.open(io.BytesIO(png_bytes))
    assert out_img.format == "PNG"
    assert out_img.size == (1200, 630)


@pytest.mark.asyncio
async def test_check_and_queue_conditions():
    """
    Test 3: check_and_queue() only queues when:
    (1) photo present + (2) status Ready + (3) catalogue_visible True + (4) standard not MIL-.
    """
    # 1. Missing photo -> False
    sku_no_photo = {
        "id": "sku-1",
        "sku_code": "TEST-1",
        "status": "Ready",
        "catalogue_visible": True,
        "standard": "IS 1969",
        "photo_path": None,
        "post_status": "none",
    }
    assert await check_and_queue(sku_no_photo) is False
    assert sku_no_photo.get("post_status") == "none"

    # 2. Status not Ready -> False
    sku_draft = {
        "id": "sku-2",
        "sku_code": "TEST-2",
        "status": "Draft",
        "catalogue_visible": True,
        "standard": "IS 1969",
        "photo_path": "sku-images/sku-2.jpg",
        "post_status": "none",
    }
    assert await check_and_queue(sku_draft) is False
    assert sku_draft.get("post_status") == "none"

    # 3. Defence MIL- standard -> False
    sku_defence = {
        "id": "sku-3",
        "sku_code": "MIL-TEST",
        "status": "Ready",
        "catalogue_visible": True,
        "standard": "MIL-W-4088K Type VIII",
        "photo_path": "sku-images/sku-3.jpg",
        "post_status": "none",
    }
    assert await check_and_queue(sku_defence) is False
    assert sku_defence.get("post_status") == "none"
    assert sku_defence.get("catalogue_visible") is False

    # 4. All four conditions met -> True & queued
    sku_ready = {
        "id": "sku-4",
        "sku_code": "COMM-4",
        "title": "Commercial Webbing 50mm",
        "family": "Narrow woven",
        "status": "Ready",
        "catalogue_visible": True,
        "standard": "IS 1969",
        "material": "Nylon",
        "photo_path": "sku-images/sku-4.jpg",
        "post_status": "none",
    }
    assert await check_and_queue(sku_ready) is True
    assert sku_ready.get("post_status") == "queued"
    assert sku_ready.get("post_draft") is not None
    assert "linkedin" in sku_ready["post_draft"]["captions"]


@pytest.mark.asyncio
async def test_image_upload_and_preview_endpoints():
    """
    Test 4: POST /skus/{sku_id}/upload-photo brands the photo, stores it,
    and GET /skus/{sku_id}/image-preview serves the branded image directly.
    """
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Step 1: Login
        login_res = await client.post(
            "/auth/login",
            data={"email": settings.test_supervisor_email, "password": settings.test_supervisor_password},
            follow_redirects=False,
        )
        cookies = client.cookies

        # Step 2: Create a ready SKU
        sku_payload = {
            "sku_code": "SNM-BRAND-TEST",
            "family": "Narrow woven",
            "title": "Test Branded Webbing",
            "standard": "IS 1969",
            "material": "Nylon",
            "status": "Ready",
            "catalogue_visible": "true",
        }
        create_res = await client.post("/skus", data=sku_payload, cookies=cookies, follow_redirects=False)
        sku_id = create_res.headers.get("location").split("/")[-1]

        # Step 3: Upload raw image
        raw_img = Image.new("RGB", (400, 300), color=(180, 180, 180))
        img_buf = io.BytesIO()
        raw_img.save(img_buf, format="JPEG")
        img_bytes = img_buf.getvalue()

        upload_res = await client.post(
            f"/skus/{sku_id}/upload-photo",
            files={"image_file": ("test_photo.jpg", img_bytes, "image/jpeg")},
            cookies=cookies,
        )
        assert upload_res.status_code == 303 or upload_res.status_code == 200

        # Step 4: Preview endpoint
        preview_res = await client.get(f"/skus/{sku_id}/image-preview")
        assert preview_res.status_code == 200
        assert preview_res.headers["content-type"] == "image/jpeg"

        # Step 5: Generate Campaign Image
        camp_img_res = await client.post(
            "/marketing/campaign/generate-image",
            data={
                "occasion": "Independence Day 2026",
                "headline": "Proud to manufacture in Kanpur, India",
                "body": "Celebrating 77 years of Indian Independence.",
            },
            cookies=cookies,
        )
        assert camp_img_res.status_code == 200
        assert "BRANDED GRAPHIC READY" in camp_img_res.text
