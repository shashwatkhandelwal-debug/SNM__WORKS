"""
Multi-platform publishing adapters for SNM Works marketing pipeline.
All endpoints are mock implementations until live credentials are provided.
"""

import asyncio
import uuid
from typing import Any, Dict


async def publish_linkedin(post: Dict[str, Any]) -> Dict[str, Any]:
    # MOCK — replace with real LinkedIn API call
    await asyncio.sleep(0.01)
    return {
        "status": "mock_published",
        "post_id": f"mock_li_{uuid.uuid4().hex[:8]}",
        "platform": "linkedin",
    }


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
    # MOCK — replace with real IndiaMart Lead/Catalogue API call
    await asyncio.sleep(0.01)
    return {
        "status": "mock_published",
        "post_id": f"mock_im_{uuid.uuid4().hex[:8]}",
        "platform": "indiamart",
    }


async def publish_tradeindia(post: Dict[str, Any]) -> Dict[str, Any]:
    # MOCK — replace with real TradeIndia Catalogue API call
    await asyncio.sleep(0.01)
    return {
        "status": "mock_published",
        "post_id": f"mock_ti_{uuid.uuid4().hex[:8]}",
        "platform": "tradeindia",
    }


async def publish_to_all_platforms(post: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dispatches the post to all five target platforms and aggregates receipt IDs.
    """
    li_res, ig_res, fb_res, im_res, ti_res = await asyncio.gather(
        publish_linkedin(post),
        publish_instagram(post),
        publish_facebook(post),
        publish_indiamart(post),
        publish_tradeindia(post),
    )

    return {
        "linkedin": {"status": li_res["status"], "post_id": li_res["post_id"]},
        "instagram": {"status": ig_res["status"], "post_id": ig_res["post_id"]},
        "facebook": {"status": fb_res["status"], "post_id": fb_res["post_id"]},
        "indiamart": {"status": im_res["status"], "post_id": im_res["post_id"]},
        "tradeindia": {"status": ti_res["status"], "post_id": ti_res["post_id"]},
    }
