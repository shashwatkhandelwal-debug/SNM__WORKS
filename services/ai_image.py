import base64
import io
import logging
from typing import Any, Dict, Optional
import httpx
from PIL import Image

from config import settings
from services.image_processor import brand_product_image
from textiles.narrow import theoretical_break_kgf

logger = logging.getLogger("snm_works.ai_image")


class GeminiImageGenerationError(Exception):
    """
    Raised when Gemini image generation fails.
    Carries diagnostic reason and HTTP status code. Never fails silently.
    """
    def __init__(self, message: str, reason: str = "API_ERROR", status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.status_code = status_code


def build_sku_image_prompt(
    sku_data: Dict[str, Any],
    construction: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Constructs a factual, unbranded photo generation prompt for Gemini Nano Banana 2 (gemini-3.1-flash-image).
    Uses real database fields from skus and linked construction.
    Computes breaking strength dynamically via theoretical_break_kgf() ONLY when construction parameters exist.
    If no linked construction is present, omits breaking strength and weave details cleanly without fabrication.
    """
    title = (sku_data.get("title") or sku_data.get("product") or "Technical Textile").strip()
    material = (sku_data.get("material") or "").strip()
    colour = (sku_data.get("colour") or "").strip()
    family = (sku_data.get("family") or "").strip().lower()

    # Product descriptor
    product_desc = title
    if material and material.lower() not in product_desc.lower():
        product_desc = f"{material} {product_desc}"
    if colour:
        product_desc = f"{product_desc} in {colour} shade"

    details = []

    # Dynamic textile calculations only when linked construction exists
    if construction:
        width_mm = construction.get("width_mm")
        if width_mm:
            try:
                details.append(f"{float(width_mm):.0f}mm width")
            except (ValueError, TypeError):
                pass

        weave = construction.get("weave")
        if weave:
            details.append(f"{weave.strip()} woven textile structure")

        # Dynamic breaking strength calculation from narrow yarn geometry
        ends = construction.get("warp_ends")
        denier = construction.get("warp_denier")
        tenacity = construction.get("warp_tenacity") or 8.5
        efficiency = construction.get("efficiency") or 85.0

        if ends and denier:
            try:
                calc_break_kgf = theoretical_break_kgf(
                    ends=int(ends),
                    denier=float(denier),
                    tenacity_g_per_den=float(tenacity),
                    efficiency_pct=float(efficiency)
                )
                if calc_break_kgf > 0:
                    details.append(f"high-load capability rated at approximately {int(calc_break_kgf)} kgf")
            except (ValueError, TypeError):
                pass

    # Presentation by product family
    if "narrow" in family or "webbing" in family or "tape" in family:
        presentation = "a neatly wound coil with clean finished selvedge edges"
    elif "cord" in family or "rope" in family or "braid" in family:
        presentation = "a clean industrial coil of technical cordage"
    else:
        presentation = "a neatly folded sample of technical fabric"

    details_str = ", ".join(details)
    if details_str:
        prompt_core = f"Commercial studio product photograph of {presentation} of {product_desc}, featuring {details_str}"
    else:
        prompt_core = f"Commercial studio product photograph of {presentation} of {product_desc}"

    return (
        f"{prompt_core}, resting on a neutral textured industrial concrete surface, "
        "macro textile side lighting highlighting clean yarn surface texture, 8k resolution, "
        "professional product catalog photography, no text, no typography, no logos, no watermarks, "
        "clean physical product focus."
    )


def build_campaign_image_prompt(
    campaign_data: Dict[str, Any],
    featured_sku: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Constructs an unbranded commercial photo prompt for promotional campaign marketing posts.
    """
    occasion = (campaign_data.get("occasion") or "Technical Textiles").strip()
    headline = (campaign_data.get("headline") or "High Performance Industrial Weaving").strip()

    featured_desc = ""
    if featured_sku:
        mat = (featured_sku.get("material") or "nylon").strip()
        title = (featured_sku.get("title") or "heavy-duty webbing").strip()
        featured_desc = f", featuring industrial spools and rolls of high-tenacity {mat} {title}"

    return (
        f"Commercial hero photograph for an industrial manufacturing campaign celebrating '{occasion}': {headline}{featured_desc}, "
        "dramatic architectural mill lighting, industrial weaving factory environment with crisp mechanical textile focus, "
        "high resolution, 8k professional commercial photography, no text, no typography, no logos, no watermarks."
    )


async def call_gemini_image_api(
    prompt: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> bytes:
    """
    Invokes Google Gemini generateContent API (Nano Banana 2 / gemini-3.1-flash-image)
    with x-goog-api-key header and parses candidates[0].content.parts[].inlineData.
    Fails LOUDLY on missing credentials, rate limits, safety blocks, or network errors.
    """
    target_key = settings.gemini_api_key if api_key is None else api_key
    if not target_key or not target_key.strip():
        raise GeminiImageGenerationError(
            "GEMINI_API_KEY is not configured in environment.",
            reason="MISSING_API_KEY",
            status_code=400,
        )

    target_model = model or settings.gemini_image_model or "gemini-3.1-flash-image"
    endpoint_url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent"

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": target_key.strip(),
    }

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "responseModalities": ["IMAGE"]
        }
    }

    async def _send_request(c: httpx.AsyncClient) -> httpx.Response:
        return await c.post(
            endpoint_url,
            headers=headers,
            json=payload,
            timeout=35.0,
        )

    try:
        if client is not None:
            response = await _send_request(client)
        else:
            async with httpx.AsyncClient() as c:
                response = await _send_request(c)
    except httpx.TimeoutException as exc:
        raise GeminiImageGenerationError(
            f"Gemini API request timed out after 35 seconds: {exc}",
            reason="TIMEOUT",
            status_code=504,
        )
    except httpx.RequestError as exc:
        raise GeminiImageGenerationError(
            f"Gemini network request failed: {exc}",
            reason="NETWORK_ERROR",
            status_code=502,
        )

    # Handle HTTP Status Codes
    if response.status_code == 429:
        raise GeminiImageGenerationError(
            "Gemini API rate limit or quota exceeded (HTTP 429). Please retry later.",
            reason="RATE_LIMIT",
            status_code=429,
        )
    elif response.status_code >= 400:
        error_detail = "Unknown API error"
        try:
            err_json = response.json()
            error_detail = err_json.get("error", {}).get("message") or response.text
        except Exception:
            error_detail = response.text
        raise GeminiImageGenerationError(
            f"Gemini API returned error HTTP {response.status_code}: {error_detail}",
            reason="API_ERROR",
            status_code=response.status_code,
        )

    try:
        data = response.json()
    except Exception as exc:
        raise GeminiImageGenerationError(
            f"Failed to parse Gemini API JSON response: {exc}",
            reason="INVALID_JSON",
            status_code=502,
        )

    candidates = data.get("candidates", [])
    if not candidates:
        prompt_feedback = data.get("promptFeedback", {})
        block_reason = prompt_feedback.get("blockReason", "UNKNOWN_SAFETY_BLOCK")
        raise GeminiImageGenerationError(
            f"Gemini prompt blocked by safety policy ({block_reason}).",
            reason="SAFETY_VIOLATION",
            status_code=400,
        )

    candidate = candidates[0]
    finish_reason = candidate.get("finishReason")
    if finish_reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"):
        raise GeminiImageGenerationError(
            f"Gemini safety filter blocked image generation (finishReason: {finish_reason}).",
            reason="SAFETY_VIOLATION",
            status_code=400,
        )

    parts = candidate.get("content", {}).get("parts", [])
    inline_part = next((p for p in parts if "inlineData" in p and p["inlineData"].get("data")), None)

    if not inline_part:
        raise GeminiImageGenerationError(
            "No valid image inlineData returned in Gemini response candidate.",
            reason="EMPTY_RESPONSE",
            status_code=502,
        )

    b64_data = inline_part["inlineData"]["data"]
    try:
        raw_bytes = base64.b64decode(b64_data)
    except Exception as exc:
        raise GeminiImageGenerationError(
            f"Failed to base64 decode image payload from Gemini: {exc}",
            reason="DECODE_ERROR",
            status_code=502,
        )

    # Validate image bytes with PIL
    try:
        with Image.open(io.BytesIO(raw_bytes)) as test_img:
            test_img.verify()
    except Exception as exc:
        raise GeminiImageGenerationError(
            f"Corrupted or invalid image bytes returned from Gemini: {exc}",
            reason="CORRUPTED_IMAGE",
            status_code=502,
        )

    return raw_bytes


async def generate_hybrid_sku_image(
    sku_data: Dict[str, Any],
    construction: Optional[Dict[str, Any]] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> bytes:
    """
    Two-stage hybrid marketing image pipeline:
    STAGE 1: Generate AI unbranded hero photograph via Google Gemini Nano Banana 2.
    STAGE 2: Apply deterministic Pillow branding overlay (exact logo, SKU code, spec line,
             guaranteed zero text overlap, corrected #474B2F color).
    Returns 1200px wide high-quality JPEG bytes.
    """
    # 1. Build unbranded photo prompt
    prompt = build_sku_image_prompt(sku_data, construction)
    logger.info(f"Generating Stage 1 AI Hero photo for SKU {sku_data.get('sku_code')} with model {model or settings.gemini_image_model}")

    # 2. Stage 1: Call Gemini API
    hero_photo_bytes = await call_gemini_image_api(
        prompt=prompt,
        api_key=api_key,
        model=model,
        client=client,
    )

    # 3. Determine breaking strength display text
    strength_str = "Contact us"
    if construction:
        ends = construction.get("warp_ends")
        denier = construction.get("warp_denier")
        tenacity = construction.get("warp_tenacity") or 8.5
        efficiency = construction.get("efficiency") or 85.0
        if ends and denier:
            try:
                calc_break = int(theoretical_break_kgf(
                    ends=int(ends),
                    denier=float(denier),
                    tenacity_g_per_den=float(tenacity),
                    efficiency_pct=float(efficiency),
                ))
                if calc_break > 0:
                    strength_str = f"{calc_break} kgf"
            except (ValueError, TypeError):
                pass
    elif sku_data.get("breaking_strength"):
        val = str(sku_data.get("breaking_strength")).strip()
        if not val.lower().startswith("specified"):
            strength_str = val

    # 4. Stage 2: Composite deterministic branding overlay with #474B2F
    branded_jpeg = brand_product_image(
        image_bytes=hero_photo_bytes,
        sku_code=sku_data.get("sku_code") or "SNM-PROD",
        standard=sku_data.get("standard") or "",
        material=sku_data.get("material") or "",
        breaking_strength=strength_str,
        family=sku_data.get("family") or "Technical Textiles",
    )

    return branded_jpeg
