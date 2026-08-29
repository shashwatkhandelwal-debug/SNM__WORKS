"""
Catalogue feed builder for Swadeshi Niwar Mills.
Processes product data for public-facing buyer listings.
"""

from typing import Any, Dict, List, Optional
from services.post_generator import sanitize_sku_data


def is_defence_standard(standard: Optional[str]) -> bool:
    if not standard:
        return False
    return str(standard).strip().upper().startswith("MIL-")


def build_catalogue_item(sku: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transforms a single SKU row into a clean, public buyer catalogue item.
    Guarantees no internal commercial leaks (price, cost, margin, customer, job#).
    """
    clean_sku = sanitize_sku_data(sku)

    sku_code = clean_sku.get("sku_code", "")
    family = clean_sku.get("family", "Technical Textiles")
    title = clean_sku.get("title") or clean_sku.get("product") or f"{family} {sku_code}"
    standard = clean_sku.get("standard") or "SNM Industrial Standard"
    material = clean_sku.get("material") or "High Tenacity Synthetic"
    blurb = clean_sku.get("blurb") or f"Engineered {family.lower()} manufactured to precision standards."
    photo_path = clean_sku.get("photo_path")

    # Extract draft details if available for properties list
    post_draft = clean_sku.get("post_draft") or {}
    breaking_strength = post_draft.get("breaking_strength", "Specified per standard")
    strength_to_weight = post_draft.get("strength_to_weight_ratio", "Contact us for datasheet")
    use_case = post_draft.get("use_case", "Industrial and technical applications")

    properties = [
        {"label": "Standard", "value": standard},
        {"label": "Material", "value": material},
        {"label": "Breaking Strength", "value": breaking_strength},
        {"label": "Strength-to-Weight", "value": strength_to_weight},
        {"label": "Application", "value": use_case},
    ]

    return {
        "sku_code": sku_code,
        "family": family,
        "title": title,
        "standard": standard,
        "material": material,
        "blurb": blurb,
        "photo_path": photo_path,
        "status": clean_sku.get("status", "Published"),
        "properties": properties,
    }


def build_catalogue_feed(skus: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filters and formats SKUs eligible for the public catalogue.
    Strictly filters out MIL- specifications and SKUs not in 'Ready' or 'Published' status.
    """
    feed: List[Dict[str, Any]] = []

    for sku in skus:
        # Check standard
        standard = sku.get("standard")
        if is_defence_standard(standard):
            continue

        # Check status: must be Ready or Published
        status = str(sku.get("status", "")).strip().capitalize()
        post_status = str(sku.get("post_status", "")).strip().lower()
        catalogue_visible = bool(sku.get("catalogue_visible", True))

        if not catalogue_visible:
            continue

        if status not in ["Ready", "Published"] and post_status not in ["approved", "published"]:
            continue

        feed.append(build_catalogue_item(sku))

    return feed
