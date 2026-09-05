"""
services/spec_integrity.py — Spec PDF & Audit Chain Cryptographic Integrity Verification.

Provides two-tier integrity verification:
1. Application layer: Recomputes physical SHA-256 hashes of the stored PDF, raw parsed JSON,
   and human-corrected JSON directly from storage/disk.
2. PostgreSQL layer: Calls verify_spec_pdf_integrity() to compare physical hashes against
   stored records and verify the cryptographic SHA-256 audit hash chain.
"""

import hashlib
import json
import logging
import os
from typing import Any, Dict, Optional
import uuid
import asyncpg

from services.storage import get_file_from_storage, UPLOAD_BASE_DIR

logger = logging.getLogger("snm_works.spec_integrity")


def compute_bytes_sha256(data: bytes) -> str:
    """Computes the 64-character lowercase hexadecimal SHA-256 hash of a byte string."""
    return hashlib.sha256(data).hexdigest()


def compute_file_sha256(file_path: str) -> Optional[str]:
    """Computes the SHA-256 hash of a file on local disk, returning None if missing."""
    if not os.path.exists(file_path):
        return None
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


async def verify_upload_integrity(
    conn: asyncpg.Connection,
    upload_id: uuid.UUID,
    user_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Executes full cryptographic integrity verification for a given spec_pdf_uploads record.
    
    1. Fetches upload metadata from DB.
    2. Reads stored physical files (PDF, raw JSON, corrected JSON).
    3. Computes actual SHA-256 checksums in Python layer.
    4. Executes PostgreSQL verify_spec_pdf_integrity() function.
    """
    # 1. Fetch upload metadata
    row = await conn.fetchrow(
        """
        SELECT u.*, s.sku_code 
        FROM spec_pdf_uploads u
        JOIN skus s ON s.id = u.sku_id
        WHERE u.id = $1;
        """,
        upload_id,
    )
    if not row:
        raise ValueError(f"Upload record '{upload_id}' not found.")

    upload = dict(row)
    storage_path = upload["storage_path"]
    parsed_json_path = upload.get("parsed_json_path")
    corrected_json_path = upload.get("corrected_json_path")

    # 2. Read physical PDF and compute actual hash
    bucket = "spec-docs"
    rel_path = storage_path.split("/", 1)[1] if "/" in storage_path and storage_path.startswith("spec-docs/") else storage_path
    pdf_bytes = await get_file_from_storage(bucket, rel_path, user_token=user_token)
    actual_pdf_sha256 = compute_bytes_sha256(pdf_bytes) if pdf_bytes else "FILE_NOT_FOUND"

    # 3. Read raw parsed JSON and compute actual hash (if applicable)
    actual_parsed_sha256 = None
    if parsed_json_path:
        json_bucket = "spec-parsed"
        json_rel = parsed_json_path.split("/", 1)[1] if "/" in parsed_json_path and parsed_json_path.startswith("spec-parsed/") else parsed_json_path
        json_bytes = await get_file_from_storage(json_bucket, json_rel, user_token=user_token)
        actual_parsed_sha256 = compute_bytes_sha256(json_bytes) if json_bytes else "FILE_NOT_FOUND"

    # 4. Read corrected JSON and compute actual hash (if applicable)
    actual_corrected_sha256 = None
    if corrected_json_path:
        json_bucket = "spec-parsed"
        corr_rel = corrected_json_path.split("/", 1)[1] if "/" in corrected_json_path and corrected_json_path.startswith("spec-parsed/") else corrected_json_path
        corr_bytes = await get_file_from_storage(json_bucket, corr_rel, user_token=user_token)
        actual_corrected_sha256 = compute_bytes_sha256(corr_bytes) if corr_bytes else "FILE_NOT_FOUND"

    # 5. Call PostgreSQL verification function
    res = await conn.fetchrow(
        """
        SELECT * FROM verify_spec_pdf_integrity($1, $2, $3, $4);
        """,
        upload_id,
        actual_pdf_sha256,
        actual_parsed_sha256,
        actual_corrected_sha256,
    )

    if not res:
        raise RuntimeError(f"Integrity check failed to return result for upload '{upload_id}'.")

    res_dict = dict(res)
    if isinstance(res_dict.get("details"), str):
        try:
            res_dict["details"] = json.loads(res_dict["details"])
        except Exception:
            pass

    return res_dict
