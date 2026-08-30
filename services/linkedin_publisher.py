from datetime import datetime
import json
import logging
import os
from typing import Any, Dict, Optional
import httpx
from config import settings
from services.crypto import decrypt_token

# One-time setup: run this in PowerShell to generate your encryption key:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Copy the output into .env as ENCRYPTION_KEY=

logger = logging.getLogger("snm_works.linkedin_publisher")

# Global memory cache for platform connections during development
MEM_PLATFORM_CONNECTIONS: Dict[str, Dict[str, Any]] = {}


async def get_stored_linkedin_connection(user_id: Optional[str] = None, conn=None) -> Optional[Dict[str, Any]]:
    """
    Fetches the active encrypted LinkedIn connection from PostgreSQL or in-memory store,
    decrypts the access token, and returns connection details.
    """
    connection_row: Optional[Dict[str, Any]] = None

    if conn is not None:
        try:
            row = await conn.fetchrow(
                """
                SELECT * FROM platform_connections
                WHERE platform = 'linkedin' AND is_active = true
                ORDER BY updated_at DESC LIMIT 1
                """
            )
            if row:
                connection_row = dict(row)
        except Exception as exc:
            logger.warning(f"Could not fetch LinkedIn connection from DB: {exc}")

    if not connection_row:
        connection_row = MEM_PLATFORM_CONNECTIONS.get("linkedin")

    if not connection_row:
        return None

    encrypted_token = connection_row.get("access_token_encrypted")
    if not encrypted_token:
        return None

    try:
        raw_token = decrypt_token(encrypted_token)
    except Exception as exc:
        logger.error(f"Failed to decrypt LinkedIn access token: {exc}")
        return None

    # Resolve company page ID or author URN
    company_id = connection_row.get("account_id") or settings.linkedin_company_page_id
    if company_id and not company_id.startswith("urn:li:"):
        author_urn = f"urn:li:organization:{company_id}"
    elif company_id:
        author_urn = company_id
    else:
        author_urn = "urn:li:organization:10000001"  # Default fallback

    return {
        "access_token": raw_token,
        "author_urn": author_urn,
        "account_name": connection_row.get("account_name") or "Swadeshi Niwar Mills",
        "company_id": company_id,
        "metadata": connection_row.get("metadata") or {},
    }


async def upload_linkedin_image_asset(access_token: str, author_urn: str, image_bytes: bytes) -> Optional[str]:
    """
    Registers and uploads an image asset to LinkedIn using the Assets API v2.
    Returns the digitalmediaAsset URN (e.g., 'urn:li:digitalmediaAsset:C5622AQ...').
    """
    register_url = "https://api.linkedin.com/v2/assets?action=registerUpload"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    payload = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": author_urn,
            "supportedUploadMechanism": ["SYNCHRONOUS_UPLOAD"],
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            reg_resp = await client.post(register_url, json=payload, headers=headers, timeout=15.0)
            if reg_resp.status_code != 200:
                logger.warning(f"LinkedIn asset registration failed: {reg_resp.status_code} {reg_resp.text}")
                return None

            reg_data = reg_resp.json()
            upload_mechanism = reg_data["value"]["uploadMechanism"]["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]
            upload_url = upload_mechanism["uploadUrl"]
            asset_urn = reg_data["value"]["asset"]

            # Step 2: Upload raw image binary bytes
            upload_headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "image/jpeg",
            }
            up_resp = await client.post(upload_url, content=image_bytes, headers=upload_headers, timeout=30.0)
            if up_resp.status_code in [200, 201]:
                logger.info(f"Successfully uploaded media asset to LinkedIn: {asset_urn}")
                return asset_urn
            else:
                logger.warning(f"LinkedIn image binary upload failed: {up_resp.status_code}")
                return None
    except Exception as exc:
        logger.error(f"Exception uploading image to LinkedIn: {exc}")
        return None


async def publish_to_linkedin(
    post_data: Dict[str, Any],
    image_bytes: Optional[bytes] = None,
    user_id: Optional[str] = None,
    conn=None,
) -> Dict[str, Any]:
    """
    Posts to the SNM company page using LinkedIn Share API v2 (POST https://api.linkedin.com/v2/ugcPosts).
    If no token is stored, returns a clear error message.
    """
    connection = await get_stored_linkedin_connection(user_id=user_id, conn=conn)
    if not connection:
        return {
            "status": "error",
            "platform": "linkedin",
            "error": "LinkedIn not connected — go to Settings to connect your account",
        }

    access_token = connection["access_token"]
    author_urn = connection["author_urn"]

    # 1. Resolve post caption text
    captions = post_data.get("captions") or {}
    caption_text = captions.get("linkedin") or post_data.get("description") or post_data.get("headline") or post_data.get("title") or "Swadeshi Niwar Mills technical textiles update."
    title_text = post_data.get("title") or post_data.get("headline") or "Swadeshi Niwar Mills Product Update"

    # 2. Upload media asset if image_bytes provided
    asset_urn: Optional[str] = None
    if image_bytes and len(image_bytes) > 0:
        asset_urn = await upload_linkedin_image_asset(
            access_token=access_token,
            author_urn=author_urn,
            image_bytes=image_bytes,
        )

    # 3. Construct UGC Post payload
    ugc_url = "https://api.linkedin.com/v2/ugcPosts"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    if asset_urn:
        share_content = {
            "shareCommentary": {"text": caption_text},
            "shareMediaCategory": "IMAGE",
            "media": [
                {
                    "status": "READY",
                    "description": {"text": title_text},
                    "media": asset_urn,
                    "title": {"text": title_text},
                }
            ],
        }
    else:
        share_content = {
            "shareCommentary": {"text": caption_text},
            "shareMediaCategory": "NONE",
        }

    payload = {
        "author": author_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": share_content
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(ugc_url, json=payload, headers=headers, timeout=20.0)
            if resp.status_code in [200, 201]:
                resp_json = resp.json()
                post_id = resp_json.get("id") or f"urn:li:ugcPost:{int(datetime.now().timestamp())}"
                post_url = f"https://www.linkedin.com/feed/update/{post_id}"
                logger.info(f"Published to LinkedIn Company Page: {post_id}")
                return {
                    "status": "published",
                    "platform": "linkedin",
                    "post_id": post_id,
                    "post_url": post_url,
                    "published_at": datetime.now().isoformat(),
                    "author": author_urn,
                }
            else:
                logger.warning(f"LinkedIn UGC post returned {resp.status_code}: {resp.text}")
                # If API error occurred with LinkedIn servers, return structured error
                return {
                    "status": "error",
                    "platform": "linkedin",
                    "error": f"LinkedIn API returned status {resp.status_code}: {resp.text[:200]}",
                }
    except Exception as exc:
        logger.error(f"Error publishing to LinkedIn: {exc}")
        return {
            "status": "error",
            "platform": "linkedin",
            "error": f"Connection error publishing to LinkedIn: {str(exc)}",
        }
