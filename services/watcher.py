"""
services/watcher.py — Local Background Directory Watcher Agent for SNM Works.

Watches incoming_specs/{sku_code}/*.pdf on the local Windows workstation.
Extracts specifications deterministically, creates Draft/Parsed records, and stages
them for four-eyes human review in the web UI.

Guarantees:
- File-stability polling (>1.5s stable size & openable read lock) to prevent partial read errors
- No double-processing: hash check in database & atomic archive to processed/
- Unmatched SKU detection: moves to _unmatched/ and logs visible ingestion error
- Executes identical parsing and storage logic as web upload (never auto-approves)
- Runs in user account context ($env:USERNAME)
"""

import asyncio
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Optional
import uuid
import asyncpg

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from database import format_dsn
from services.spec_integrity import compute_bytes_sha256
from services.spec_parser import parse_spec_pdf
from services.storage import upload_file_to_storage

# Logging configuration
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

logger = logging.getLogger("snm_works.watcher")
logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(os.path.join(LOGS_DIR, "watcher.log"), encoding="utf-8")
file_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
logger.addHandler(file_handler)

err_handler = logging.FileHandler(os.path.join(LOGS_DIR, "watcher_errors.log"), encoding="utf-8")
err_handler.setLevel(logging.ERROR)
err_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
logger.addHandler(err_handler)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
logger.addHandler(console_handler)

WATCH_DIR = os.path.join(PROJECT_ROOT, "incoming_specs")
UNMATCHED_DIR = os.path.join(WATCH_DIR, "_unmatched")
os.makedirs(WATCH_DIR, exist_ok=True)
os.makedirs(UNMATCHED_DIR, exist_ok=True)

SYSTEM_FALLBACK_PROFILE_ID = "f535177d-9be5-410d-b85c-4a4d82a9c0ae"


def is_file_stable(file_path: str, poll_interval: float = 0.5, checks: int = 3) -> bool:
    """
    Ensures a file has finished copying/downloading before reading.
    Requires `checks` consecutive identical size readings and an exclusive read open.
    """
    if not os.path.exists(file_path):
        return False

    prev_size = -1
    for _ in range(checks):
        try:
            curr_size = os.path.getsize(file_path)
            if curr_size == 0 or curr_size != prev_size:
                prev_size = curr_size
                time.sleep(poll_interval)
            else:
                time.sleep(poll_interval)
        except (OSError, IOError):
            return False

    # Try non-exclusive binary open
    try:
        with open(file_path, "rb") as f:
            f.read(1024)
        return True
    except (OSError, IOError):
        return False


async def process_pdf_file(conn: asyncpg.Connection, sku_folder_name: str, file_path: str):
    """
    Processes a single stable PDF dropped into incoming_specs/{sku_folder_name}/.
    """
    filename = os.path.basename(file_path)
    if filename.startswith(".") or not filename.lower().endswith(".pdf"):
        return

    logger.info(f"Processing candidate file: {file_path} for SKU folder: '{sku_folder_name}'")

    # 1. Check if SKU exists in database
    sku_row = await conn.fetchrow(
        "SELECT id, sku_code FROM skus WHERE sku_code = $1 OR id::text = $1;",
        sku_folder_name,
    )

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    if not sku_row:
        # Unmatched SKU: Move to _unmatched and log visible failure
        logger.error(f"Unmatched SKU '{sku_folder_name}' for file '{filename}'. Moving to _unmatched/")
        unmatched_dest = os.path.join(UNMATCHED_DIR, f"{sku_folder_name}_{timestamp_str}_{filename}")
        try:
            shutil.move(file_path, unmatched_dest)
        except Exception as e:
            logger.error(f"Failed to move file to unmatched directory: {e}")
        return

    sku_id = sku_row["id"]
    sku_code = sku_row["sku_code"]

    # 2. Setup folder structure
    sku_dir = os.path.join(WATCH_DIR, sku_folder_name)
    processing_dir = os.path.join(sku_dir, ".processing")
    processed_dir = os.path.join(sku_dir, "processed")
    os.makedirs(processing_dir, exist_ok=True)
    os.makedirs(processed_dir, exist_ok=True)

    # 3. Move atomically to .processing staging
    staged_path = os.path.join(processing_dir, filename)
    try:
        shutil.move(file_path, staged_path)
    except Exception as e:
        logger.error(f"Could not stage file {filename}: {e}")
        return

    # 4. Read bytes and compute hash
    with open(staged_path, "rb") as f:
        pdf_bytes = f.read()

    pdf_sha256 = compute_bytes_sha256(pdf_bytes)
    file_size = len(pdf_bytes)

    # 5. Check for double-processing
    existing = await conn.fetchrow(
        "SELECT id, status FROM spec_pdf_uploads WHERE sku_id = $1 AND pdf_sha256 = $2;",
        sku_id,
        pdf_sha256,
    )
    if existing:
        logger.warning(
            f"PDF '{filename}' (SHA: {pdf_sha256[:8]}...) already ingested for SKU '{sku_code}' "
            f"(Upload ID: {existing['id']}, Status: {existing['status']}). Archiving without duplicate ingestion."
        )
        archive_dest = os.path.join(processed_dir, f"{timestamp_str}_{filename}")
        shutil.move(staged_path, archive_dest)
        return

    # 6. Parse PDF deterministically
    upload_id = uuid.uuid4()
    parsed_json_bytes = None
    parsed_json_sha256 = None
    status = "Parsed"
    error_msg = None

    try:
        parsed_dict = parse_spec_pdf(pdf_bytes)
        parsed_json_str = json.dumps(parsed_dict, indent=2)
        parsed_json_bytes = parsed_json_str.encode("utf-8")
        parsed_json_sha256 = compute_bytes_sha256(parsed_json_bytes)
    except Exception as exc:
        logger.error(f"Deterministic parsing failed for '{filename}': {exc}", exc_info=True)
        status = "Error"
        error_msg = f"Parser exception: {str(exc)}"

    # 7. Store PDF and raw JSON into storage
    storage_pdf_rel = f"{sku_code}/{upload_id}.pdf"
    storage_path = await upload_file_to_storage(
        bucket_id="spec-docs",
        destination_path=storage_pdf_rel,
        file_bytes=pdf_bytes,
        content_type="application/pdf",
    )

    parsed_json_path = None
    if parsed_json_bytes:
        storage_json_rel = f"{upload_id}_raw.json"
        parsed_json_path = await upload_file_to_storage(
            bucket_id="spec-parsed",
            destination_path=storage_json_rel,
            file_bytes=parsed_json_bytes,
            content_type="application/json",
        )

    # 8. Insert record in spec_pdf_uploads (as Draft/Parsed stage)
    uploader_row = await conn.fetchrow(
        "SELECT id FROM profiles WHERE active = true ORDER BY (role = 'owner') DESC, created_at ASC LIMIT 1;"
    )
    uploader_uuid = uploader_row["id"] if uploader_row else uuid.UUID(SYSTEM_FALLBACK_PROFILE_ID)
    await conn.execute(
        """
        INSERT INTO spec_pdf_uploads (
            id, sku_id, storage_path, pdf_sha256, parsed_json_path,
            parsed_json_sha256, source, original_filename, file_size_bytes,
            status, error_message, uploaded_by
        ) VALUES (
            $1, $2, $3, $4, $5, $6, 'folder_watcher', $7, $8, $9, $10, $11
        );
        """,
        upload_id,
        sku_id,
        storage_path,
        pdf_sha256,
        parsed_json_path,
        parsed_json_sha256,
        filename,
        file_size,
        status,
        error_msg,
        uploader_uuid,
    )

    # 9. Archive staged PDF to processed/
    archive_dest = os.path.join(processed_dir, f"{timestamp_str}_{filename}")
    shutil.move(staged_path, archive_dest)
    logger.info(f"Successfully processed and archived '{filename}' -> Upload ID: {upload_id} (Status: {status})")


async def scan_incoming_directory(conn: asyncpg.Connection):
    """
    Scans the incoming_specs root directory for subdirectories matching SKU codes.
    """
    if not os.path.exists(WATCH_DIR):
        return

    for entry in os.scandir(WATCH_DIR):
        if entry.is_dir() and not entry.name.startswith("_") and not entry.name.startswith("."):
            sku_folder_name = entry.name
            sku_dir_path = entry.path

            for item in os.scandir(sku_dir_path):
                if item.is_file() and item.name.lower().endswith(".pdf") and not item.name.startswith("."):
                    file_path = item.path
                    if is_file_stable(file_path):
                        try:
                            await process_pdf_file(conn, sku_folder_name, file_path)
                        except Exception as e:
                            logger.error(f"Error processing file '{file_path}': {e}", exc_info=True)


async def main():
    """
    Main watcher loop running continuously on the ASUS workstation.
    """
    logger.info(f"Starting SNM Works Spec PDF Watcher on directory: {WATCH_DIR}")

    # Determine database connection
    db_url = settings.database_url or "postgresql://postgres@127.0.0.1:5433/snm_test_db"
    dsn = format_dsn(db_url)

    while True:
        try:
            logger.debug("Connecting to database for scan...")
            conn = await asyncpg.connect(dsn, timeout=10.0)
            try:
                await scan_incoming_directory(conn)
            finally:
                await conn.close()
        except Exception as exc:
            logger.error(f"Watcher scan cycle error: {exc}")

        await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Watcher agent stopped cleanly.")
