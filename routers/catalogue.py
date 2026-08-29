import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import database
from services.catalogue import build_catalogue_feed

logger = logging.getLogger("snm_works.catalogue")
router = APIRouter(prefix="/catalogue", tags=["catalogue"])
templates = Jinja2Templates(directory="templates")

SAMPLE_PUBLIC_SKUS: List[Dict[str, Any]] = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "sku_code": "SNM-NW-25-PES",
        "family": "Narrow woven",
        "title": "25mm High-Tenacity Polyester Webbing",
        "standard": "IS 15041 / SNM-STD-101",
        "material": "High Tenacity Polyester (HT-PES)",
        "blurb": "Heavy-duty 25mm industrial polyester webbing with high abrasion resistance and UV stability.",
        "status": "Published",
        "post_status": "published",
        "catalogue_visible": True,
        "post_draft": {
            "breaking_strength": "1200 kgf (2645 lbf)",
            "strength_to_weight_ratio": "38.50 kgf/(g/m)",
            "use_case": "Load securing, tie-downs, harnesses, cargo management",
        },
    },
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "sku_code": "SNM-NW-45-NYL",
        "family": "Narrow woven",
        "title": "45mm Tactical Nylon Safety Webbing",
        "standard": "EN 1492-1 / SNM-STD-104",
        "material": "Polyamide 6.6 (Nylon 6.6)",
        "blurb": "Precision 45mm multi-layer safety webbing engineered for fall arrest harnesses and industrial slings.",
        "status": "Published",
        "post_status": "published",
        "catalogue_visible": True,
        "post_draft": {
            "breaking_strength": "3000 kgf (6614 lbf)",
            "strength_to_weight_ratio": "42.10 kgf/(g/m)",
            "use_case": "Fall arrest harnesses, safety slings, rescue gear",
        },
    },
    {
        "id": "33333333-3333-3333-3333-333333333333",
        "sku_code": "SNM-CRD-12-BRD",
        "family": "Cordage",
        "title": "12mm 16-Plait Polyester Static Braid",
        "standard": "BS EN 892 / SNM-STD-202",
        "material": "Continuous Filament Polyester",
        "blurb": "Static braided cordage with low-elongation core for rigging, hoisting, and industrial positioning.",
        "status": "Published",
        "post_status": "published",
        "catalogue_visible": True,
        "post_draft": {
            "breaking_strength": "2400 kgf (5291 lbf)",
            "strength_to_weight_ratio": "26.80 kgf/(g/m)",
            "use_case": "Lifting, rigging, safety lines, industrial positioning",
        },
    },
    {
        "id": "44444444-4444-4444-4444-444444444444",
        "sku_code": "SNM-FAB-500-DUK",
        "family": "Fabric",
        "title": "500 GSM Heavy Industrial Cotton Duck",
        "standard": "IS 1422 / SNM-STD-305",
        "material": "100% Combed Cotton Ring Spun",
        "blurb": "Heavy-duty technical cloth for protective equipment covers, machine shields, and rugged tarpaulins.",
        "status": "Published",
        "post_status": "published",
        "catalogue_visible": True,
        "post_draft": {
            "breaking_strength": "180 kgf / 5cm strip",
            "strength_to_weight_ratio": "Contact us for datasheet",
            "use_case": "Technical protective clothing, covers, shelters, machine guards",
        },
    },
]


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def public_catalogue_view(
    request: Request,
    family: Optional[str] = None,
):
    """
    Public-facing product catalogue for buyers, clients, and search engines.
    Requires no authentication.
    Strictly excludes defence specifications (MIL-) and internal commercial details.
    """
    raw_skus: List[Dict[str, Any]] = []

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT s.*, c.weave, c.width_mm, c.warp_ends, c.picks_per_cm
                    FROM skus s
                    LEFT JOIN constructions c ON c.id = s.construction_id
                    WHERE s.status IN ('Ready', 'Published')
                       OR s.post_status IN ('approved', 'published')
                    ORDER BY s.created_on DESC
                    """
                )
                raw_skus = [dict(r) for r in rows]
        except Exception as exc:
            logger.warning(f"Could not query SKUs for catalogue from database: {exc}")

    # Fallback to standard product catalogue if DB is empty during initial setup
    if not raw_skus:
        raw_skus = SAMPLE_PUBLIC_SKUS

    # Build filtered catalogue feed
    catalogue_items = build_catalogue_feed(raw_skus)

    # Filter by family if requested
    if family and family.upper() != "ALL":
        catalogue_items = [
            item for item in catalogue_items
            if str(item.get("family", "")).strip().lower() == family.strip().lower()
        ]

    return templates.TemplateResponse(
        request=request,
        name="marketing/catalogue.html",
        context={
            "items": catalogue_items,
            "selected_family": family or "ALL",
            "families": ["ALL", "Narrow woven", "Fabric", "Cordage"],
        }
    )
