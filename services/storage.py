import io
import logging
import os
from typing import Optional
import httpx
from config import settings

logger = logging.getLogger("snm_works.storage")

UPLOAD_BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads")
os.makedirs(os.path.join(UPLOAD_BASE_DIR, "sku-images"), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_BASE_DIR, "campaign-images"), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_BASE_DIR, "spec-docs"), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_BASE_DIR, "spec-parsed"), exist_ok=True)


async def upload_file_to_storage(
    bucket_id: str,
    destination_path: str,
    file_bytes: bytes,
    content_type: str = "image/jpeg",
    user_token: Optional[str] = None,
) -> str:
    """
    Uploads a file to Supabase Storage and mirrors to local static/uploads/ cache.
    Returns the relative storage object path (e.g. 'sku-images/{sku_id}.jpg').
    """
    # 1. Save locally for instant development preview
    local_target = os.path.join(UPLOAD_BASE_DIR, bucket_id, destination_path)
    os.makedirs(os.path.dirname(local_target), exist_ok=True)
    with open(local_target, "wb") as f:
        f.write(file_bytes)
    logger.info(f"Saved local file cache at {local_target}")

    # 2. Upload to Supabase Storage REST endpoint
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {user_token or settings.supabase_publishable_key}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    url = f"{settings.supabase_url}/storage/v1/object/{bucket_id}/{destination_path}"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, content=file_bytes, headers=headers, timeout=15.0)
            if resp.status_code in [200, 201]:
                logger.info(f"Successfully uploaded {destination_path} to Supabase Storage bucket {bucket_id}")
            else:
                logger.warning(f"Supabase storage upload returned status {resp.status_code}: {resp.text}")
    except Exception as exc:
        logger.warning(f"Supabase storage upload exception: {exc}")

    return f"{bucket_id}/{destination_path}"


async def get_file_from_storage(
    bucket_id: str,
    file_path: str,
    user_token: Optional[str] = None,
) -> Optional[bytes]:
    """
    Retrieves file bytes from local cache or Supabase Storage.
    """
    # 1. Check local cache
    local_target = os.path.join(UPLOAD_BASE_DIR, bucket_id, file_path)
    if os.path.exists(local_target):
        with open(local_target, "rb") as f:
            return f.read()

    # 2. Check Supabase Storage
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {user_token or settings.supabase_publishable_key}",
    }
    url = f"{settings.supabase_url}/storage/v1/object/authenticated/{bucket_id}/{file_path}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                return resp.content
    except Exception as exc:
        logger.warning(f"Failed to fetch {file_path} from Supabase storage: {exc}")

    return None
