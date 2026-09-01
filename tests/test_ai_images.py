import base64
import io
import json
import pytest
import httpx
from PIL import Image
from unittest.mock import patch

from config import settings
from main import app
from services.ai_image import (
    GeminiImageGenerationError,
    build_sku_image_prompt,
    build_campaign_image_prompt,
    call_gemini_image_api,
    generate_hybrid_sku_image,
)
from tests.conftest import make_test_token, TEST_USERS


def _create_dummy_jpeg_b64(width: int = 800, height: int = 800, color: tuple = (180, 160, 140)) -> str:
    """Helper to generate a valid base64-encoded JPEG image string."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def test_build_sku_image_prompt_with_linked_construction():
    """
    Test 1: Factual prompt construction from real SKU and linked construction data.
    Computes theoretical_break_kgf() dynamically and includes width and weave.
    """
    sku_data = {
        "sku_code": "SNM-NW-POLY25",
        "family": "Narrow woven",
        "title": "25mm Industrial Cargo Webbing Tape",
        "standard": "IS 15041",
        "material": "Polyester",
        "colour": "Olive Drab",
    }
    construction_data = {
        "width_mm": 25.0,
        "weave": "2/2 Twill",
        "warp_ends": 150,
        "warp_denier": 420,
        "warp_tenacity": 8.5,
        "efficiency": 85.0,
    }

    prompt = build_sku_image_prompt(sku_data, construction_data)

    # 150 * 420 * 8.5 * 0.85 / 1000 = 455 kgf
    assert "Polyester 25mm Industrial Cargo Webbing Tape in Olive Drab shade" in prompt
    assert "25mm width" in prompt
    assert "2/2 Twill woven textile structure" in prompt
    assert "455 kgf" in prompt
    assert "no text, no typography, no logos, no watermarks" in prompt
    assert "None" not in prompt


def test_build_sku_image_prompt_without_construction_zero_fabrication():
    """
    Test 2: Zero-fabrication discipline when SKU has no linked construction (construction_id is None).
    Must cleanly omit width, weave, and breaking strength without placeholder text or 'None'.
    """
    sku_data = {
        "sku_code": "SNM-BRAND-TEST",
        "family": "Narrow woven",
        "product": None,
        "title": "Test Branded Webbing",
        "standard": "IS 1969",
        "material": "Nylon",
        "colour": None,
    }

    prompt = build_sku_image_prompt(sku_data, construction=None)

    assert "Nylon Test Branded Webbing" in prompt
    assert "neatly wound coil" in prompt
    assert "None" not in prompt
    assert "width" not in prompt
    assert "kgf" not in prompt
    assert "weave" not in prompt
    assert "no text, no typography, no logos, no watermarks" in prompt


def test_build_campaign_image_prompt():
    """
    Test 3: Campaign photo prompt generation with featured SKU details.
    """
    campaign_data = {
        "occasion": "DefExpo India 2026",
        "headline": "Advanced Technical Narrow Fabrics and Webbing",
    }
    featured_sku = {
        "material": "Aramid",
        "title": "Flame Retardant Webbing",
    }

    prompt = build_campaign_image_prompt(campaign_data, featured_sku)

    assert "DefExpo India 2026" in prompt
    assert "Advanced Technical Narrow Fabrics and Webbing" in prompt
    assert "high-tenacity Aramid Flame Retardant Webbing" in prompt
    assert "no text, no typography, no logos, no watermarks" in prompt


@pytest.mark.asyncio
async def test_call_gemini_image_api_missing_key_fails_loudly():
    """
    Test 4: Missing GEMINI_API_KEY fails LOUDLY with GeminiImageGenerationError.
    Never falls back silently.
    """
    with pytest.raises(GeminiImageGenerationError) as exc_info:
        await call_gemini_image_api(
            prompt="Test prompt",
            api_key="",
        )

    assert exc_info.value.reason == "MISSING_API_KEY"
    assert exc_info.value.status_code == 400
    assert "GEMINI_API_KEY is not configured" in str(exc_info.value)


@pytest.mark.asyncio
async def test_call_gemini_image_api_rate_limit_429_fails_loudly():
    """
    Test 5: HTTP 429 Rate Limit from Gemini raises loud GeminiImageGenerationError.
    """
    mock_transport = httpx.MockTransport(
        lambda request: httpx.Response(429, json={"error": {"message": "Resource has been exhausted (e.g. check quota)."}})
    )

    async with httpx.AsyncClient(transport=mock_transport) as mock_client:
        with pytest.raises(GeminiImageGenerationError) as exc_info:
            await call_gemini_image_api(
                prompt="Test prompt",
                api_key="test-api-key-12345",
                client=mock_client,
            )

        assert exc_info.value.reason == "RATE_LIMIT"
        assert exc_info.value.status_code == 429
        assert "rate limit or quota exceeded" in str(exc_info.value)


@pytest.mark.asyncio
async def test_call_gemini_image_api_safety_block_fails_loudly():
    """
    Test 6: Safety policy block from Gemini raises loud GeminiImageGenerationError.
    """
    safety_response = {
        "candidates": [
            {
                "finishReason": "SAFETY",
                "content": {"parts": []},
            }
        ]
    }
    mock_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=safety_response)
    )

    async with httpx.AsyncClient(transport=mock_transport) as mock_client:
        with pytest.raises(GeminiImageGenerationError) as exc_info:
            await call_gemini_image_api(
                prompt="Test prompt",
                api_key="test-api-key-12345",
                client=mock_client,
            )

        assert exc_info.value.reason == "SAFETY_VIOLATION"
        assert exc_info.value.status_code == 400
        assert "safety filter blocked" in str(exc_info.value)


@pytest.mark.asyncio
async def test_call_gemini_image_api_successful_response_parsing():
    """
    Test 7: Successful Gemini generateContent response with inlineData JPEG is decoded cleanly.
    """
    raw_b64 = _create_dummy_jpeg_b64(600, 600, (100, 150, 200))
    valid_response = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/jpeg",
                                "data": raw_b64,
                            }
                        }
                    ]
                },
            }
        ]
    }
    mock_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=valid_response)
    )

    async with httpx.AsyncClient(transport=mock_transport) as mock_client:
        image_bytes = await call_gemini_image_api(
            prompt="Studio photo of nylon webbing",
            api_key="test-api-key-12345",
            client=mock_client,
        )

    assert isinstance(image_bytes, bytes)
    img = Image.open(io.BytesIO(image_bytes))
    assert img.format == "JPEG"
    assert img.size == (600, 600)


@pytest.mark.asyncio
async def test_generate_hybrid_sku_image_pipeline():
    """
    Test 8: Full Stage 1 (Gemini AI Hero Photo) -> Stage 2 (Pillow Branding Overlay #474B2F) pipeline.
    Verifies 1200px JPEG width, top & bottom banners, and correct #474B2F color (71, 75, 47).
    """
    raw_b64 = _create_dummy_jpeg_b64(800, 600, (150, 150, 150))
    valid_response = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/jpeg",
                                "data": raw_b64,
                            }
                        }
                    ]
                },
            }
        ]
    }
    mock_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=valid_response)
    )

    sku_data = {
        "sku_code": "SNM-NW-POLY25",
        "family": "Narrow woven",
        "title": "25mm Industrial Cargo Webbing Tape",
        "standard": "IS 15041",
        "material": "Polyester",
        "colour": "Olive Drab",
    }
    construction_data = {
        "width_mm": 25.0,
        "weave": "2/2 Twill",
        "warp_ends": 150,
        "warp_denier": 420,
        "warp_tenacity": 8.5,
        "efficiency": 85.0,
    }

    async with httpx.AsyncClient(transport=mock_transport) as mock_client:
        branded_bytes = await generate_hybrid_sku_image(
            sku_data=sku_data,
            construction=construction_data,
            api_key="test-api-key-12345",
            client=mock_client,
        )

    out_img = Image.open(io.BytesIO(branded_bytes))
    assert out_img.format == "JPEG"
    assert out_img.size[0] == 1200
    # Expected height: (1200 * (600/800)) + 160 = 900 + 160 = 1060
    assert out_img.size[1] == 1060

    # Verify top banner pixel is #474B2F => rgb(71, 75, 47)
    top_pixel = out_img.getpixel((15, 15))
    assert abs(top_pixel[0] - 71) < 10
    assert abs(top_pixel[1] - 75) < 10
    assert abs(top_pixel[2] - 47) < 10

    # Verify bottom banner pixel is #474B2F
    bottom_pixel = out_img.getpixel((15, 1045))
    assert abs(bottom_pixel[0] - 71) < 10
    assert abs(bottom_pixel[1] - 75) < 10
    assert abs(bottom_pixel[2] - 47) < 10


@pytest.mark.asyncio
async def test_marketing_generate_ai_image_endpoint_authenticated(dev_client):
    """
    Test 9: POST /marketing/sku/{sku_id}/generate-ai-image route executes one-time generation
    and saves photo_path to storage and database.
    """
    from routers.skus import MEM_SKUS
    MEM_SKUS["SNM-BRAND-TEST"] = {
        "id": "SNM-BRAND-TEST",
        "sku_code": "SNM-BRAND-TEST",
        "family": "Narrow woven",
        "title": "Industrial High Strength Webbing",
        "standard": "IS 15041",
        "material": "Polyester",
        "colour": "Olive Drab",
    }

    dummy_hero_bytes = io.BytesIO()
    Image.new("RGB", (600, 400), color=(120, 130, 100)).save(dummy_hero_bytes, format="JPEG")
    dummy_hero_bytes_val = dummy_hero_bytes.getvalue()

    with patch("services.ai_image.call_gemini_image_api", return_value=dummy_hero_bytes_val):
        resp = await dev_client.post(
            "/marketing/sku/SNM-BRAND-TEST/generate-ai-image",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "AI Hero Image Generated & Saved Successfully" in resp.text
        assert "src=\"/storage/sku-images/" in resp.text


@pytest.mark.asyncio
async def test_marketing_generate_ai_image_endpoint_loud_failure(dev_client):
    """
    Test 10: POST /marketing/sku/{sku_id}/generate-ai-image surfaces loud error in HTMX response
    when Gemini API fails, without crashing or faking results.
    """
    from routers.skus import MEM_SKUS
    MEM_SKUS["SNM-BRAND-TEST"] = {
        "id": "SNM-BRAND-TEST",
        "sku_code": "SNM-BRAND-TEST",
        "family": "Narrow woven",
        "title": "Industrial High Strength Webbing",
        "standard": "IS 15041",
        "material": "Polyester",
        "colour": "Olive Drab",
    }

    with patch("services.ai_image.call_gemini_image_api", side_effect=GeminiImageGenerationError("Quota limit hit", reason="RATE_LIMIT", status_code=429)):
        resp = await dev_client.post(
            "/marketing/sku/SNM-BRAND-TEST/generate-ai-image",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 429
        assert "AI Image Generation Failed" in resp.text
        assert "Quota limit hit" in resp.text
