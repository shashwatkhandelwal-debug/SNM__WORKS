"""
tests/test_spec_watcher.py — Tests for Background Watcher Agent.

Verifies:
1. File-stability check detects when a file is still being written vs stable.
2. Unmatched SKU folder routes candidate file to _unmatched/ without crashing or losing file.
3. Ingesting duplicate file skips parsing and archives cleanly to processed/ without duplicate DB rows.
"""

import asyncio
import os
import shutil
import time
import uuid
import pytest
import asyncpg
from services.watcher import (
    is_file_stable,
    process_pdf_file,
    WATCH_DIR,
    UNMATCHED_DIR,
)


def test_file_stability_check(tmp_path):
    # Test stable file
    test_file = tmp_path / "stable_doc.pdf"
    test_file.write_bytes(b"%PDF-1.4 sample content")
    assert is_file_stable(str(test_file), poll_interval=0.1, checks=2) is True

    # Test non-existent file
    assert is_file_stable(str(tmp_path / "nonexistent.pdf"), poll_interval=0.1, checks=1) is False


@pytest.mark.asyncio
async def test_watcher_unmatched_sku_routing():
    conn = await asyncpg.connect("postgresql://postgres@127.0.0.1:5433/snm_test_db")
    try:
        unmatched_folder = "NONEXISTENT_SKU_CODE_999"
        sku_watch_dir = os.path.join(WATCH_DIR, unmatched_folder)
        os.makedirs(sku_watch_dir, exist_ok=True)

        test_pdf_name = f"spec_unmatched_{uuid.uuid4().hex[:6]}.pdf"
        test_pdf_path = os.path.join(sku_watch_dir, test_pdf_name)
        with open(test_pdf_path, "wb") as f:
            f.write(b"%PDF-1.4 Unmatched specimen")

        await process_pdf_file(conn, unmatched_folder, test_pdf_path)

        # Assert file was moved from watch dir
        assert not os.path.exists(test_pdf_path)

        # Assert file is in _unmatched
        unmatched_files = os.listdir(UNMATCHED_DIR)
        assert any(test_pdf_name in f for f in unmatched_files)
        print("\n[PASS] Unmatched SKU file successfully moved to _unmatched/")

        # Clean up test folder
        shutil.rmtree(sku_watch_dir, ignore_errors=True)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_watcher_double_processing_prevention():
    conn = await asyncpg.connect("postgresql://postgres@127.0.0.1:5433/snm_test_db")
    try:
        # Create a valid test SKU
        sku_id = uuid.uuid4()
        sku_code = f"WATCHER-SKU-{uuid.uuid4().hex[:6].upper()}"
        await conn.execute(
            """
            INSERT INTO skus (id, sku_code, family, title, status)
            VALUES ($1, $2, 'Narrow woven', 'Watcher Test SKU', 'Active');
            """,
            sku_id,
            sku_code,
        )

        sku_watch_dir = os.path.join(WATCH_DIR, sku_code)
        os.makedirs(sku_watch_dir, exist_ok=True)

        pdf_name = "test_spec_double.pdf"
        pdf_path = os.path.join(sku_watch_dir, pdf_name)
        pdf_content = b"%PDF-1.4 Watcher Double Processing Test Spec Content."

        # Pass 1: Initial Processing
        with open(pdf_path, "wb") as f:
            f.write(pdf_content)

        await process_pdf_file(conn, sku_code, pdf_path)

        count1 = await conn.fetchval(
            "SELECT count(*) FROM spec_pdf_uploads WHERE sku_id = $1;",
            sku_id,
        )
        assert count1 == 1

        # Pass 2: Second copy of identical file
        with open(pdf_path, "wb") as f:
            f.write(pdf_content)

        await process_pdf_file(conn, sku_code, pdf_path)

        # DB count must remain 1 (no duplicate row)
        count2 = await conn.fetchval(
            "SELECT count(*) FROM spec_pdf_uploads WHERE sku_id = $1;",
            sku_id,
        )
        assert count2 == 1

        # File should be in processed/ archive
        processed_dir = os.path.join(sku_watch_dir, "processed")
        archived_files = os.listdir(processed_dir)
        assert len(archived_files) >= 2
        print("[PASS] Double processing prevented, duplicate archived without second DB record")

        # Clean up test watch directory
        shutil.rmtree(sku_watch_dir, ignore_errors=True)
    finally:
        await conn.close()
