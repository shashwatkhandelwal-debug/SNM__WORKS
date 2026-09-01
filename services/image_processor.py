import io
import logging
import os
from typing import Optional
from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger("snm_works.image_processor")

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "fonts")
BARLOW_BOLD_PATH = os.path.join(FONT_DIR, "BarlowCondensed-Bold.ttf")
IBM_MONO_PATH = os.path.join(FONT_DIR, "IBMPlexMono-Regular.ttf")

COLOR_OLIVE = (71, 75, 47)  # #474B2F
COLOR_WHITE = (255, 255, 255)
COLOR_GREIGE = (233, 229, 218) # #E9E5DA
COLOR_LINE = (207, 200, 182) # #CFC8B6


def _load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(font_path, size)
    except Exception as exc:
        logger.warning(f"Could not load font from {font_path}: {exc}. Falling back to default font.")
        return ImageFont.load_default()


def brand_product_image(
    image_bytes: bytes,
    sku_code: str,
    standard: str,
    material: str,
    breaking_strength: str,
    family: str,
) -> bytes:
    """
    Pure Pillow function that automatically brands raw product photos.
    Adds an 80px olive top banner and 80px olive bottom banner.
    Output is 1200px wide high-quality JPEG bytes.
    """
    # 1. Open and orient raw image
    raw_img = Image.open(io.BytesIO(image_bytes))
    raw_img = ImageOps.exif_transpose(raw_img)
    if raw_img.mode != "RGB":
        raw_img = raw_img.convert("RGB")

    # 2. Scale image to 1200px width maintaining aspect ratio
    target_width = 1200
    orig_w, orig_h = raw_img.size
    aspect_ratio = orig_h / orig_w
    target_photo_h = max(int(target_width * aspect_ratio), 300)
    scaled_photo = raw_img.resize((target_width, target_photo_h), Image.Resampling.LANCZOS)

    # 3. Create final canvas (1200 x (photo_height + 160))
    banner_height = 80
    total_height = target_photo_h + (banner_height * 2)
    canvas = Image.new("RGB", (target_width, total_height), COLOR_OLIVE)

    # 4. Paste photo between top and bottom banners
    canvas.paste(scaled_photo, (0, banner_height))

    draw = ImageDraw.Draw(canvas)

    # 5. Load custom fonts
    font_brand = _load_font(BARLOW_BOLD_PATH, 28)
    font_sku = _load_font(IBM_MONO_PATH, 22)
    font_specs = _load_font(IBM_MONO_PATH, 18)
    font_origin = _load_font(BARLOW_BOLD_PATH, 20)

    # -------------------------------------------------------------------------
    # 6. Draw Top Banner (y: 0 -> 80)
    # -------------------------------------------------------------------------
    brand_text = "SWADESHI NIWAR MILLS"
    sku_text = f"SKU: {sku_code or 'SNM-PROD'}"

    # Measure text vertical alignments
    brand_bbox = draw.textbbox((0, 0), brand_text, font=font_brand)
    brand_h = brand_bbox[3] - brand_bbox[1]
    brand_y = (banner_height - brand_h) // 2

    draw.text((30, brand_y), brand_text, font=font_brand, fill=COLOR_WHITE)

    # Right-aligned SKU code
    sku_bbox = draw.textbbox((0, 0), sku_text, font=font_sku)
    sku_w = sku_bbox[2] - sku_bbox[0]
    sku_h = sku_bbox[3] - sku_bbox[1]
    sku_x = target_width - sku_w - 30
    sku_y = (banner_height - sku_h) // 2
    draw.text((sku_x, sku_y), sku_text, font=font_sku, fill=COLOR_WHITE)

    # Subtle bottom line separator on top banner
    draw.line([(0, banner_height - 1), (target_width, banner_height - 1)], fill=COLOR_LINE, width=1)

    # -------------------------------------------------------------------------
    # 7. Draw Bottom Banner (y: total_height - 80 -> total_height)
    # -------------------------------------------------------------------------
    bottom_y_start = total_height - banner_height
    # Subtle top line separator on bottom banner
    draw.line([(0, bottom_y_start), (target_width, bottom_y_start)], fill=COLOR_LINE, width=1)

    clean_std = (standard or "SNM Mill Standard").strip()
    clean_mat = (material or "High Tenacity").strip()
    clean_brk = (breaking_strength or "Contact us").strip()
    if clean_brk.lower().startswith("specified"):
        clean_brk = "Contact us"
    clean_fam = (family or "Technical Textiles").strip()

    # Left & Center Specs
    specs_parts = [
        f"STD: {clean_std}",
        f"MAT: {clean_mat}",
        f"STRENGTH: {clean_brk}",
        f"FAMILY: {clean_fam}",
    ]
    specs_str = "   •   ".join(specs_parts)

    origin_text = "Made in Kanpur, India"

    origin_bbox = draw.textbbox((0, 0), origin_text, font=font_origin)
    origin_w = origin_bbox[2] - origin_bbox[0]
    origin_h = origin_bbox[3] - origin_bbox[1]
    origin_x = target_width - origin_w - 30
    origin_y = bottom_y_start + (banner_height - origin_h) // 2

    # Draw right-aligned origin
    draw.text((origin_x, origin_y), origin_text, font=font_origin, fill=COLOR_GREIGE)

    # Draw left-aligned specs string
    specs_bbox = draw.textbbox((0, 0), specs_str, font=font_specs)
    specs_h = specs_bbox[3] - specs_bbox[1]
    specs_y = bottom_y_start + (banner_height - specs_h) // 2

    # If text is too wide, truncate gracefully
    max_specs_w = origin_x - 50
    specs_w = specs_bbox[2] - specs_bbox[0]
    if specs_w > max_specs_w:
        short_parts = [clean_std, clean_mat, clean_brk, clean_fam]
        specs_str = "  •  ".join(short_parts)

    draw.text((30, specs_y), specs_str, font=font_specs, fill=COLOR_WHITE)

    # 8. Export high-quality JPEG
    output_buf = io.BytesIO()
    canvas.save(output_buf, format="JPEG", quality=92, subsampling=0)
    return output_buf.getvalue()
