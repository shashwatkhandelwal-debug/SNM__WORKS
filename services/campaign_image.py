import io
import logging
import os
import textwrap
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("snm_works.campaign_image")

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "fonts")
BARLOW_BOLD_PATH = os.path.join(FONT_DIR, "BarlowCondensed-Bold.ttf")
IBM_MONO_PATH = os.path.join(FONT_DIR, "IBMPlexMono-Regular.ttf")

COLOR_OLIVE = (71, 75, 47)      # #474B2F
COLOR_WHITE = (255, 255, 255)
COLOR_GREIGE = (233, 229, 218)   # #E9E5DA
COLOR_LINE = (207, 200, 182)     # #CFC8B6


def _load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(font_path, size)
    except Exception as exc:
        logger.warning(f"Could not load font from {font_path}: {exc}")
        return ImageFont.load_default()


def generate_campaign_graphic(
    occasion: str,
    headline: str,
    body: Optional[str] = None,
    width: int = 1200,
    height: int = 630,
) -> bytes:
    """
    Pure Pillow generator for branded promotional campaign social cards.
    - Background: Olive green #4C5C33
    - Top Left: White 'SNM WORKS' wordmark
    - Center: Large white bold headline
    - Below Headline: Occasion subtitle in greige
    - Bottom Right: 'Made in Kanpur, India'
    - Border: Thin inner border in greige
    Returns PNG bytes.
    """
    img = Image.new("RGB", (width, height), COLOR_OLIVE)
    draw = ImageDraw.Draw(img)

    # 1. Thin inner border
    margin = 24
    draw.rectangle(
        [(margin, margin), (width - margin, height - margin)],
        outline=COLOR_LINE,
        width=2,
    )

    # 2. Top Left: SNM WORKS Wordmark & Kanpur Badge
    font_brand = _load_font(BARLOW_BOLD_PATH, 38)
    font_sub_brand = _load_font(IBM_MONO_PATH, 16)
    
    draw.text((margin + 28, margin + 24), "SWADESHI NIWAR MILLS", font=font_brand, fill=COLOR_WHITE)
    draw.text((margin + 30, margin + 68), "TECHNICAL TEXTILES • EST. KANPUR", font=font_sub_brand, fill=COLOR_GREIGE)

    # 3. Bottom Right: Origin and National Pride
    font_origin = _load_font(BARLOW_BOLD_PATH, 24)
    font_mono_tag = _load_font(IBM_MONO_PATH, 15)

    origin_text = "Made in Kanpur, India"
    origin_bbox = draw.textbbox((0, 0), origin_text, font=font_origin)
    origin_w = origin_bbox[2] - origin_bbox[0]
    draw.text((width - margin - 28 - origin_w, height - margin - 52), origin_text, font=font_origin, fill=COLOR_WHITE)

    tag_text = "DEFENCE • INDUSTRIAL • EXPORT"
    tag_bbox = draw.textbbox((0, 0), tag_text, font=font_mono_tag)
    tag_w = tag_bbox[2] - tag_bbox[0]
    draw.text((width - margin - 28 - tag_w, height - margin - 76), tag_text, font=font_mono_tag, fill=COLOR_GREIGE)

    # 4. Center Area: Occasion Tag & Headline
    clean_occ = (occasion or "Promotional Announcement").strip().upper()
    font_occ = _load_font(IBM_MONO_PATH, 20)
    occ_text = f"★ {clean_occ} ★"
    occ_bbox = draw.textbbox((0, 0), occ_text, font=font_occ)
    occ_w = occ_bbox[2] - occ_bbox[0]
    draw.text(((width - occ_w) // 2, 175), occ_text, font=font_occ, fill=COLOR_GREIGE)

    # Headline (Wrapped & Centered)
    font_headline = _load_font(BARLOW_BOLD_PATH, 54)
    clean_headline = (headline or "Excellence in Technical Textiles").strip()
    
    # Wrap text to approx 32 characters per line
    wrapped_lines = textwrap.wrap(clean_headline, width=32)
    if not wrapped_lines:
        wrapped_lines = [clean_headline]

    line_height = 62
    total_text_h = len(wrapped_lines) * line_height
    start_y = 225 + (160 - total_text_h) // 2

    for i, line in enumerate(wrapped_lines):
        line_bbox = draw.textbbox((0, 0), line, font=font_headline)
        line_w = line_bbox[2] - line_bbox[0]
        line_x = (width - line_w) // 2
        line_y = start_y + (i * line_height)
        draw.text((line_x, line_y), line, font=font_headline, fill=COLOR_WHITE)

    # 5. Output PNG bytes
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
