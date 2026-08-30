"""
Multi-platform publishing adapters for SNM Works marketing pipeline.
LinkedIn is integrated with real OAuth and Share API v2.
Other platforms are mock adapters pending API credentials.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional
from services.linkedin_publisher import publish_to_linkedin

logger = logging.getLogger("snm_works.publishers")


async def publish_linkedin(post: Dict[str, Any], image_bytes: Optional[bytes] = None) -> Dict[str, Any]:
    """
    Dispatches to the real LinkedIn Share API v2.
    If LinkedIn is not connected, returns clear error receipt.
    """
    return await publish_to_linkedin(post_data=post, image_bytes=image_bytes)


async def publish_instagram(post: Dict[str, Any]) -> Dict[str, Any]:
    # MOCK — replace with real Instagram Graph API call
    await asyncio.sleep(0.01)
    return {
        "status": "mock_published",
        "post_id": f"mock_ig_{uuid.uuid4().hex[:8]}",
        "platform": "instagram",
    }


async def publish_facebook(post: Dict[str, Any]) -> Dict[str, Any]:
    # MOCK — replace with real Facebook Graph API call
    await asyncio.sleep(0.01)
    return {
        "status": "mock_published",
        "post_id": f"mock_fb_{uuid.uuid4().hex[:8]}",
        "platform": "facebook",
    }


async def publish_indiamart(post: Dict[str, Any]) -> Dict[str, Any]:
    # MOCK / API Key submission
    await asyncio.sleep(0.01)
    return {
        "status": "mock_published",
        "post_id": f"mock_im_{uuid.uuid4().hex[:8]}",
        "platform": "indiamart",
    }


async def publish_tradeindia(post: Dict[str, Any]) -> Dict[str, Any]:
    # MOCK / API Key submission
    await asyncio.sleep(0.01)
    return {
        "status": "mock_published",
        "post_id": f"mock_ti_{uuid.uuid4().hex[:8]}",
        "platform": "tradeindia",
    }


async def publish_to_all_platforms(post: Dict[str, Any], image_bytes: Optional[bytes] = None) -> Dict[str, Any]:
    """
    Dispatches the post to all five target platforms and aggregates receipt IDs.
    """
    li_res, ig_res, fb_res, im_res, ti_res = await asyncio.gather(
        publish_linkedin(post, image_bytes=image_bytes),
        publish_instagram(post),
        publish_facebook(post),
        publish_indiamart(post),
        publish_tradeindia(post),
    )

    return {
        "linkedin": li_res,
        "instagram": ig_res,
        "facebook": fb_res,
        "indiamart": im_res,
        "tradeindia": ti_res,
    }
