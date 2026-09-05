"""
Pure Python post generator for the SNM Works marketing pipeline.
No framework imports.
"""

from typing import Any, Dict, List, Optional, Union

# Prohibited commercial and sensitive data keys that must never appear in posts or catalogues
RESTRICTED_KEYS = {
    "price", "unit_price", "cost", "unit_cost", "margin", "profit",
    "customer", "customer_name", "customer_id",
    "order_quantity", "order_qty", "quantity", "qty",
    "job_number", "job_no", "job_id", "batch_no"
}

FAMILY_DEFAULT_USE_CASES = {
    "narrow woven": "Load bearing, safety, military accessories",
    "narrow fabric": "Load bearing, safety, military accessories",
    "webbing": "Load bearing, safety, military accessories",
    "tape": "Load bearing, safety, military accessories",
    "fabric": "Technical protective clothing, covers, shelters",
    "broad fabric": "Technical protective clothing, covers, shelters",
    "cordage": "Lifting, rigging, safety lines",
    "rope": "Lifting, rigging, safety lines",
    "braid": "Lifting, rigging, safety lines",
}

PLATFORM_LIMITS = {
    "linkedin": 3000,
    "instagram": 2200,
    "facebook": 63206,
    "indiamart": 500,
    "tradeindia": 500,
}


class DefenceProductError(Exception):
    """
    Raised when an operation attempts to queue or publish a SKU governed by
    defence specifications (e.g., MIL- standards).
    """
    pass


def sanitize_sku_data(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively strips restricted commercial data (price, cost, margin,
    customer, order quantity, job number) from dictionary structures.
    """
    if not isinstance(raw_data, dict):
        return raw_data

    clean: Dict[str, Any] = {}
    for key, value in raw_data.items():
        lower_key = str(key).strip().lower()
        if lower_key in RESTRICTED_KEYS:
            continue
        if any(bad in lower_key for bad in ["price", "cost", "margin", "customer", "order_qty", "job_no"]):
            continue

        if isinstance(value, dict):
            clean[key] = sanitize_sku_data(value)
        elif isinstance(value, list):
            clean[key] = [
                sanitize_sku_data(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            clean[key] = value

    return clean


def calculate_linear_density_gpm(construction: Dict[str, Any]) -> Optional[float]:
    """
    Computes linear mass in grams per meter (g/m) from construction parameters.
    """
    if not construction:
        return None

    # Check if weight_gpm is directly provided
    if construction.get("weight_gpm"):
        try:
            return float(construction["weight_gpm"])
        except (ValueError, TypeError):
            pass

    family = str(construction.get("family", "")).strip().lower()

    # Narrow woven calculation
    ends = construction.get("warp_ends")
    warp_denier = construction.get("warp_denier")
    picks_per_cm = construction.get("picks_per_cm")
    width_mm = construction.get("width_mm")
    weft_denier = construction.get("weft_denier")
    warp_crimp = float(construction.get("warp_crimp") or 0.0)
    weft_crimp = float(construction.get("weft_crimp") or 0.0)

    if ends and warp_denier and picks_per_cm and width_mm and weft_denier:
        try:
            warp_gpm = float(ends) * float(warp_denier) * (1 + warp_crimp / 100) / 9000.0
            weft_gpm = float(picks_per_cm) * 100.0 * (float(width_mm) / 1000.0) * float(weft_denier) * (1 + weft_crimp / 100) / 9000.0
            return warp_gpm + weft_gpm
        except (ValueError, TypeError, ZeroDivisionError):
            pass

    # Cordage calculation
    carriers = construction.get("carriers")
    yarns_per_carrier = construction.get("yarns_per_carrier")
    yarn_denier = construction.get("yarn_denier")
    core_yarns = construction.get("core_yarns") or 0
    tpm = construction.get("tpm") or 0.0
    contraction = float(construction.get("contraction") or 0.0)

    if carriers and yarns_per_carrier and yarn_denier:
        try:
            total_yarns = (int(carriers) * int(yarns_per_carrier)) + int(core_yarns)
            gpm = (total_yarns * float(yarn_denier) / 9000.0) * (1 + contraction / 100)
            return gpm
        except (ValueError, TypeError, ZeroDivisionError):
            pass

    return None


def resolve_strength_to_weight_ratio(
    breaking_strength_kgf: Optional[float],
    construction: Optional[Dict[str, Any]] = None,
    spec_requirements: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Calculates breaking strength (kgf) / weight (g/m) ratio.
    If unavailable, strictly returns 'Contact us for datasheet' — never null or empty.
    """
    gpm: Optional[float] = None

    if construction:
        gpm = calculate_linear_density_gpm(construction)

    if gpm is None and spec_requirements:
        # Search spec requirements for weight / linear density
        for req in spec_requirements:
            param = str(req.get("parameter", "")).strip().lower()
            val = req.get("nominal") or req.get("minimum") or req.get("maximum") or req.get("value")
            if not val:
                continue

            try:
                numeric_val = float(val)
                # Check for weight in oz/yd -> convert to g/m (1 oz/yd = 31.0034 g/m)
                if "oz/yd" in param or "oz/yd" in str(req.get("unit", "")).lower():
                    gpm = numeric_val * 31.0034
                    break
                elif "g/m" in param or "g/m" in str(req.get("unit", "")).lower() or "weight" in param:
                    gpm = numeric_val
                    break
            except (ValueError, TypeError):
                continue

    if breaking_strength_kgf and gpm and gpm > 0:
        ratio = breaking_strength_kgf / gpm
        return f"{ratio:.2f} kgf/(g/m)"

    return "Contact us for datasheet"


def extract_breaking_strength_kgf(
    spec_requirements: Optional[List[Dict[str, Any]]]
) -> Optional[float]:
    """
    Extracts breaking strength from specification requirements, converting to kgf if in lbf or N.
    """
    if not spec_requirements:
        return None

    for req in spec_requirements:
        param = str(req.get("parameter", "")).strip().lower()
        if "breaking strength" in param or "tensile strength" in param:
            val = req.get("minimum") or req.get("nominal") or req.get("maximum") or req.get("value")
            unit = str(req.get("unit", "")).strip().lower()
            if val:
                try:
                    num = float(val)
                    if "lb" in unit or "lbf" in unit or "lb" in param:
                        return num * 0.45359237  # lbf to kgf
                    elif "kn" in unit:
                        return num * 101.97162  # kN to kgf
                    elif "n" in unit:
                        return num * 0.10197162  # N to kgf
                    return num
                except (ValueError, TypeError):
                    continue
    return None


def format_platform_caption(
    platform: str,
    sku_code: str,
    title: str,
    standard: str,
    material: str,
    breaking_strength_display: str,
    strength_to_weight: str,
    use_case: str,
    blurb: str,
) -> str:
    """
    Generates structured, platform-tailored post text constrained to platform character limits.
    """
    limit = PLATFORM_LIMITS.get(platform.lower(), 2000)

    if platform.lower() == "indiamart":
        text = (
            f"{title} ({sku_code})\n"
            f"* Standard: {standard}\n"
            f"* Material: {material}\n"
            f"* Strength: {breaking_strength_display}\n"
            f"* S/W Ratio: {strength_to_weight}\n"
            f"* Application: {use_case}\n"
            f"Manufacturer: Swadeshi Niwar Mills, Kanpur."
        )
    elif platform.lower() == "tradeindia":
        text = (
            f"Swadeshi Niwar Mills — {title}\n"
            f"SKU: {sku_code} | Standard: {standard}\n"
            f"Composition: {material}\n"
            f"Breaking Strength: {breaking_strength_display}\n"
            f"Strength/Weight: {strength_to_weight}\n"
            f"Ideal for: {use_case}\n"
            f"Direct mill supply for technical & export requirements."
        )
    elif platform.lower() == "instagram":
        text = (
            f"⚙️ {title} | SKU: {sku_code}\n\n"
            f"Engineered for high-reliability technical applications by Swadeshi Niwar Mills.\n\n"
            f"📋 Technical Specs:\n"
            f"* Standard: {standard}\n"
            f"* Material: {material}\n"
            f"* Breaking Strength: {breaking_strength_display}\n"
            f"* Strength-to-Weight: {strength_to_weight}\n"
            f"* Primary Use: {use_case}\n\n"
            f"{blurb}\n\n"
            f"📍 Manufactured in Kanpur, India.\n"
            f"📩 Contact us for technical datasheets and sample requests.\n\n"
            f"#TechnicalTextiles #NarrowFabrics #Webbing #TextileEngineering #Manufacturing #MadeInIndia #IndustrialTextiles"
        )
    elif platform.lower() == "linkedin":
        text = (
            f"Product Focus: {title} ({sku_code})\n\n"
            f"Swadeshi Niwar Mills is pleased to present our high-performance technical textile solution manufactured in Kanpur, India.\n\n"
            f"Key Technical Specifications:\n"
            f"* Governing Standard: {standard}\n"
            f"* Material Formulation: {material}\n"
            f"* Breaking Strength: {breaking_strength_display}\n"
            f"* Strength-to-Weight Ratio: {strength_to_weight}\n"
            f"* Target Applications: {use_case}\n\n"
            f"Overview:\n{blurb}\n\n"
            f"Our manufacturing processes follow strict quality protocols and full lot traceability. "
            f"Reach out to our technical sales team for procurement specifications and batch datasheets.\n\n"
            f"#TechnicalTextiles #NarrowWovens #TextileManufacturing #DefenceSupply #IndianManufacturing #IndustrialSafety"
        )
    else:  # facebook / generic
        text = (
            f"🏭 Swadeshi Niwar Mills Product Catalogue — {title}\n\n"
            f"Product Code: {sku_code}\n"
            f"Standard: {standard}\n"
            f"Material: {material}\n"
            f"Breaking Strength: {breaking_strength_display}\n"
            f"Strength-to-Weight Ratio: {strength_to_weight}\n"
            f"Recommended Use: {use_case}\n\n"
            f"About this product:\n{blurb}\n\n"
            f"Manufactured to exacting tolerances at our Kanpur works. "
            f"Direct enquiries and bulk technical requirements are welcome.\n\n"
            f"#SwadeshiNiwarMills #TechnicalTextiles #MadeInIndia"
        )

    # Strictly enforce character limit
    if len(text) > limit:
        text = text[: limit - 3] + "..."

    return text


def generate_post(
    sku: Dict[str, Any],
    spec_requirements: Optional[List[Dict[str, Any]]] = None,
    construction: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generates a structured post draft object from a SKU record.
    Strictly enforces defence standard exclusion and commercial data redaction.
    """
    # 1. Strip prohibited commercial data
    clean_sku = sanitize_sku_data(sku)

    standard = str(clean_sku.get("standard") or "").strip()
    if standard.upper().startswith("MIL-"):
        raise DefenceProductError(
            f"SKU {clean_sku.get('sku_code', 'UNKNOWN')} is governed by defence specification '{standard}' "
            "and cannot be queued or published for marketing."
        )

    sku_code = str(clean_sku.get("sku_code") or "SKU-PROD")
    family = str(clean_sku.get("family") or "Narrow woven")
    title = str(clean_sku.get("title") or clean_sku.get("product") or f"{family} Specification")
    material = str(clean_sku.get("material") or "High Tenacity Synthetic")
    blurb = str(clean_sku.get("blurb") or f"Precision-engineered {family.lower()} for industrial requirements.")

    # 2. Extract breaking strength
    break_kgf = extract_breaking_strength_kgf(spec_requirements)
    if break_kgf:
        break_display = f"{break_kgf:.1f} kgf ({break_kgf * 2.20462:.0f} lbf)"
    else:
        break_display = "Specified per standard"

    # 3. Resolve strength to weight ratio (strictly never null/empty)
    strength_to_weight = resolve_strength_to_weight_ratio(
        breaking_strength_kgf=break_kgf,
        construction=construction,
        spec_requirements=spec_requirements,
    )

    # 4. Resolve use case
    use_case = clean_sku.get("use_case")
    if not use_case:
        use_case = FAMILY_DEFAULT_USE_CASES.get(
            family.lower(),
            "Industrial safety, load management, technical applications"
        )

    # 5. Build captions for all five platforms
    captions = {
        platform: format_platform_caption(
            platform=platform,
            sku_code=sku_code,
            title=title,
            standard=standard or "SNM Mill Standard",
            material=material,
            breaking_strength_display=break_display,
            strength_to_weight=strength_to_weight,
            use_case=use_case,
            blurb=blurb,
        )
        for platform in ["linkedin", "instagram", "facebook", "indiamart", "tradeindia"]
    }

    return {
        "sku_code": sku_code,
        "title": title,
        "family": family,
        "standard": standard or "SNM Mill Standard",
        "material": material,
        "description": blurb,
        "breaking_strength": break_display,
        "strength_to_weight_ratio": strength_to_weight,
        "use_case": use_case,
        "captions": captions,
    }


def format_campaign_caption(
    platform: str,
    occasion: str,
    headline: str,
    body: str,
    featured_sku: Optional[Dict[str, Any]] = None,
) -> str:
    limit = PLATFORM_LIMITS.get(platform.lower(), 2000)
    feat_text = ""
    if featured_sku:
        sku_title = featured_sku.get("title") or featured_sku.get("sku_code")
        sku_std = featured_sku.get("standard")
        feat_text = f"\n\nFeatured Product: {sku_title}" + (f" ({sku_std})" if sku_std else "")

    if platform.lower() == "indiamart":
        text = (
            f"Swadeshi Niwar Mills — {occasion}\n"
            f"{headline}\n"
            f"{body}{feat_text}\n"
            f"Manufacturer: Kanpur, India."
        )
    elif platform.lower() == "tradeindia":
        text = (
            f"Swadeshi Niwar Mills ({occasion})\n"
            f"{headline}\n"
            f"{body}{feat_text}\n"
            f"High-quality technical textiles from Kanpur, India."
        )
    elif platform.lower() == "instagram":
        text = (
            f"🇮🇳 {occasion} | {headline}\n\n"
            f"{body}{feat_text}\n\n"
            f"🏭 Manufactured at Swadeshi Niwar Mills, Kanpur.\n"
            f"Equipped with in-house testing, precision looms, and military-grade quality standards.\n\n"
            f"#IndependenceDay #MadeInIndia #KanpurTextiles #SwadeshiNiwarMills #TechnicalTextiles #IndianManufacturing #NarrowWovens"
        )
    elif platform.lower() == "linkedin":
        text = (
            f"{headline}\n\n"
            f"Occasion: {occasion}\n\n"
            f"{body}{feat_text}\n\n"
            f"Swadeshi Niwar Mills has been manufacturing technical textiles in Kanpur for decades — producing high-tenacity narrow fabrics, technical cloth, and engineered cordage. "
            f"We remain committed to supporting Indian industry, infrastructure, and defence supply chains with world-class quality.\n\n"
            f"#IndependenceDay #MadeInIndia #TechnicalTextiles #ManufacturingExcellence #Kanpur #IndianTextiles #DefenceSupply"
        )
    else:  # facebook
        text = (
            f"🇮🇳 Swadeshi Niwar Mills — {occasion}\n\n"
            f"{headline}\n\n"
            f"{body}{feat_text}\n\n"
            f"From our weaving sheds in Kanpur to defence and industrial clients across India, we take pride in precision manufacturing. "
            f"Jai Hind.\n\n"
            f"#SwadeshiNiwarMills #IndependenceDay #MadeInIndia #Kanpur"
        )

    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def generate_campaign_post(
    campaign: Dict[str, Any],
    featured_sku: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generates structured multi-platform captions for promotional and event campaigns.
    Strictly sanitizes inputs and respects platform limits.
    """
    clean_camp = sanitize_sku_data(campaign)
    occasion = str(clean_camp.get("occasion") or "Company Announcement").strip()
    headline = str(clean_camp.get("headline") or "Swadeshi Niwar Mills Update").strip()
    body = str(clean_camp.get("body") or "").strip()

    clean_featured = sanitize_sku_data(featured_sku) if featured_sku else None

    platforms = clean_camp.get("platforms") or ["linkedin", "instagram", "facebook", "indiamart", "tradeindia"]
    captions = {
        p: format_campaign_caption(
            platform=p,
            occasion=occasion,
            headline=headline,
            body=body,
            featured_sku=clean_featured,
        )
        for p in ["linkedin", "instagram", "facebook", "indiamart", "tradeindia"]
    }

    return {
        "type": "campaign",
        "occasion": occasion,
        "headline": headline,
        "body": body,
        "featured_sku": clean_featured.get("sku_code") if clean_featured else None,
        "platforms": platforms,
        "captions": captions,
    }


# Alias for concise invocation
generate = generate_post


async def check_and_queue(sku: Dict[str, Any], conn=None) -> bool:
    """
    Check four conditions:
      1. SKU has photo (photo_path is not null/empty)
      2. Status is 'Ready'
      3. catalogue_visible is True
      4. Standard does not start with MIL- (verified via generate() not raising DefenceProductError)
    If all met and post_status is 'none', generate draft and set post_status = 'queued'.
    Returns True if queued, False otherwise.
    """
    import json

    photo_path = sku.get("photo_path")
    status_val = str(sku.get("status") or "").strip().capitalize()
    is_visible = bool(sku.get("catalogue_visible"))
    current_post_status = sku.get("post_status", "none") or "none"

    if not photo_path:
        return False

    if status_val != "Ready":
        return False

    if not is_visible:
        return False

    if current_post_status != "none":
        return False

    try:
        post_draft_obj = generate(sku)
    except DefenceProductError:
        sku["post_status"] = "none"
        sku["catalogue_visible"] = False
        if conn is not None:
            sku_id = str(sku.get("id"))
            await conn.execute(
                """
                UPDATE skus
                SET post_status = 'none',
                    catalogue_visible = false
                WHERE id::text = $1
                """,
                sku_id
            )
        return False

    # All four conditions met!
    sku["post_status"] = "queued"
    sku["post_draft"] = post_draft_obj

    if conn is not None:
        sku_id = str(sku.get("id"))
        await conn.execute(
            """
            UPDATE skus
            SET post_status = 'queued',
                post_draft = $1::jsonb
            WHERE id::text = $2
            """,
            json.dumps(post_draft_obj),
            sku_id
        )

    return True

