"""
Multi-platform publishing adapters for SNM Works marketing pipeline.
Supports LinkedIn (with live OAuth / Share API v2 integration or mock fallback)
and mock adapters for Instagram, Facebook, IndiaMart, and TradeIndia.

Standard Return Contract:
{
    "success": bool,
    "platform": str,
    "status": "mock_published",  # ONLY mock_published is returned by mock adapters
    "post_id": str,
    "url": str,
    "error": Optional[str],
    "published_at": str (ISO-8601 UTC)
}
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from services.linkedin_publisher import get_stored_linkedin_connection, publish_to_linkedin

logger = logging.getLogger("snm_works.publishers")

SUPPORTED_PLATFORMS = ["linkedin", "instagram", "facebook", "indiamart", "tradeindia"]


async def publish_linkedin(post: Dict[str, Any], image_bytes: Optional[bytes] = None) -> Dict[str, Any]:
    """
    Adapter for LinkedIn Marketing / Share API.
    If a real encrypted token is configured in platform_connections, calls LinkedIn Share API v2.
    Otherwise returns a standardized mock receipt.
    """
    try:
        connection = await get_stored_linkedin_connection()
        if connection:
            return await publish_to_linkedin(post_data=post, image_bytes=image_bytes)
    except Exception as exc:
        logger.warning(f"LinkedIn live check error: {exc}")

    await asyncio.sleep(0.01)
    post_uid = uuid.uuid4().hex[:8]
    return {
        "success": True,
        "platform": "linkedin",
        "status": "mock_published",
        "post_id": f"mock_li_{post_uid}",
        "url": f"https://www.linkedin.com/feed/update/urn:li:share:{post_uid}",
        "error": None,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }


async def publish_instagram(post: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adapter for Instagram Graph API.
    MOCK — replace with real Instagram Graph API call using Meta access token.
    """
    await asyncio.sleep(0.01)
    post_uid = uuid.uuid4().hex[:8]
    return {
        "success": True,
        "platform": "instagram",
        "status": "mock_published",
        "post_id": f"mock_ig_{post_uid}",
        "url": f"https://www.instagram.com/p/mock_ig_{post_uid}/",
        "error": None,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }


async def publish_facebook(post: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adapter for Facebook Graph API / Pages API.
    MOCK — replace with real Facebook Graph API call using Page access token.
    """
    await asyncio.sleep(0.01)
    post_uid = uuid.uuid4().hex[:8]
    return {
        "success": True,
        "platform": "facebook",
        "status": "mock_published",
        "post_id": f"mock_fb_{post_uid}",
        "url": f"https://www.facebook.com/swadeshiniwarmills/posts/mock_fb_{post_uid}",
        "error": None,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }


async def publish_indiamart(post: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adapter for IndiaMart Lead / Seller Catalogue API.
    MOCK — replace with real IndiaMart Seller API call.
    """
    await asyncio.sleep(0.01)
    post_uid = uuid.uuid4().hex[:8]
    return {
        "success": True,
        "platform": "indiamart",
        "status": "mock_published",
        "post_id": f"mock_im_{post_uid}",
        "url": f"https://www.indiamart.com/proddetail/mock_im_{post_uid}.html",
        "error": None,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }


async def publish_tradeindia(post: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adapter for TradeIndia B2B Catalogue API.
    MOCK — replace with real TradeIndia Catalogue API call.
    """
    await asyncio.sleep(0.01)
    post_uid = uuid.uuid4().hex[:8]
    return {
        "success": True,
        "platform": "tradeindia",
        "status": "mock_published",
        "post_id": f"mock_ti_{post_uid}",
        "url": f"https://www.tradeindia.com/products/mock_ti_{post_uid}.html",
        "error": None,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }


PLATFORM_PUBLISHERS = {
    "linkedin": publish_linkedin,
    "instagram": publish_instagram,
    "facebook": publish_facebook,
    "indiamart": publish_indiamart,
    "tradeindia": publish_tradeindia,
}


async def publish_post_to_platforms(
    post: Dict[str, Any],
    platforms: Optional[List[str]] = None,
    image_bytes: Optional[bytes] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Dispatches the post to specified target platforms (or all 5 if omitted/empty).
    Executes concurrently and returns standardized per-platform results.
    """
    if platforms:
        target_platforms = [p.strip().lower() for p in platforms if p and p.strip().lower() in PLATFORM_PUBLISHERS]
        if not target_platforms:
            target_platforms = list(SUPPORTED_PLATFORMS)
    else:
        target_platforms = list(SUPPORTED_PLATFORMS)

    tasks = []
    for p in target_platforms:
        if p == "linkedin":
            tasks.append(publish_linkedin(post, image_bytes=image_bytes))
        else:
            tasks.append(PLATFORM_PUBLISHERS[p](post))

    results_list = await asyncio.gather(*tasks)

    return {
        res["platform"]: res
        for res in results_list
    }


async def publish_to_all_platforms(
    post: Dict[str, Any],
    image_bytes: Optional[bytes] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Dispatches the post to all five target platforms and aggregates standardized receipts.
    Maintained for backward compatibility.
    """
    return await publish_post_to_platforms(post, platforms=None, image_bytes=image_bytes)
