#!/usr/bin/env python3
"""
scripts/stage_trade_docs_for_review.py — Stage Parsed Trade Documents for Review.

Reads parsed document JSONs from data/trade_docs/parsed_staging/ and stages them
idempotently into the trade_documents and trade_document_items tables with status='Parsed'.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import asyncpg

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from auth.middleware import set_rls_claims

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("stage_trade_docs")

DEFAULT_LOCAL_DB = "postgresql://postgres@127.0.0.1:5433/snm_test_db"
DEFAULT_OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
STAGING_DIR = PROJECT_ROOT / "data" / "trade_docs" / "parsed_staging"


async def stage_trade_document(
    conn: asyncpg.Connection,
    data: Dict[str, Any],
    uploader_id: uuid.UUID = DEFAULT_OWNER_ID
) -> uuid.UUID:
    """Inserts or updates a parsed trade document and its line items."""
    party_name = data.get("party_name", "Unknown Party").strip()
    doc_number = data.get("doc_number", "UNKNOWN-DOC").strip()
    doc_type = data.get("doc_type", "purchase_bill")
    
    # Parse date
    raw_date = data.get("doc_date")
    if isinstance(raw_date, str):
        try:
            doc_date_obj = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            doc_date_obj = date.today()
    elif isinstance(raw_date, (date, datetime)):
        doc_date_obj = raw_date
    else:
        doc_date_obj = date.today()
    
    # 1. Lookup mapped tally ledger name if exists
    tally_ledger = data.get("tally_ledger_name")
    if not tally_ledger:
        tally_ledger = await conn.fetchval(
            "SELECT tally_ledger_name FROM party_ledger_mappings WHERE LOWER(extracted_name) = LOWER($1)",
            party_name
        )
    if not tally_ledger and data.get("gstin"):
        tally_ledger = await conn.fetchval(
            "SELECT tally_ledger_name FROM party_ledger_mappings WHERE gstin = $1",
            data.get("gstin")
        )

    # 2. Upsert trade_documents header
    doc_id = await conn.fetchval(
        """
        INSERT INTO trade_documents (
            doc_type,
            source,
            status,
            party_name,
            tally_ledger_name,
            gstin,
            doc_number,
            doc_date,
            supply_type,
            po_reference,
            total_taxable_value,
            total_cgst,
            total_sgst,
            total_igst,
            total_tax,
            round_off,
            net_payable,
            pdf_path,
            parsed_json_path,
            pdf_sha256,
            notes,
            created_by
        )
        VALUES (
            $1::trade_doc_type,
            $2::trade_doc_source,
            $3::trade_doc_status,
            $4, $5, $6, $7, $8, $9, $10,
            $11, $12, $13, $14, $15, $16, $17,
            $18, $19, $20, $21, $22
        )
        ON CONFLICT (doc_type, party_name, doc_number)
        DO UPDATE SET
            status = EXCLUDED.status,
            tally_ledger_name = COALESCE(EXCLUDED.tally_ledger_name, trade_documents.tally_ledger_name),
            gstin = EXCLUDED.gstin,
            doc_date = EXCLUDED.doc_date,
            supply_type = EXCLUDED.supply_type,
            total_taxable_value = EXCLUDED.total_taxable_value,
            total_cgst = EXCLUDED.total_cgst,
            total_sgst = EXCLUDED.total_sgst,
            total_igst = EXCLUDED.total_igst,
            total_tax = EXCLUDED.total_tax,
            round_off = EXCLUDED.round_off,
            net_payable = EXCLUDED.net_payable,
            pdf_path = EXCLUDED.pdf_path,
            parsed_json_path = EXCLUDED.parsed_json_path,
            pdf_sha256 = EXCLUDED.pdf_sha256,
            updated_at = now()
        RETURNING id
        """,
        doc_type,
        data.get("source", "manual"),
        data.get("status", "Parsed"),
        party_name,
        tally_ledger,
        data.get("gstin"),
        doc_number,
        doc_date_obj,
        data.get("supply_type", "intra_state"),
        data.get("po_reference"),
        data.get("total_taxable_value", 0.0),
        data.get("total_cgst", 0.0),
        data.get("total_sgst", 0.0),
        data.get("total_igst", 0.0),
        data.get("total_tax", 0.0),
        data.get("round_off", 0.0),
        data.get("net_payable", 0.0),
        data.get("pdf_path"),
        data.get("parsed_json_path"),
        data.get("pdf_sha256"),
        data.get("notes"),
        uploader_id
    )

    # 3. Insert line items
    items = data.get("items", [])
    if items:
        await conn.execute("DELETE FROM trade_document_items WHERE trade_doc_id = $1", doc_id)
        for idx, item in enumerate(items, start=1):
            await conn.execute(
                """
                INSERT INTO trade_document_items (
                    trade_doc_id,
                    line_no,
                    description,
                    hsn_sac,
                    tally_stock_item,
                    qty,
                    unit,
                    rate,
                    taxable_value,
                    cgst_rate,
                    cgst_amount,
                    sgst_rate,
                    sgst_amount,
                    igst_rate,
                    igst_amount,
                    line_total
                )
                VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15,
                    $16
                )
                """,
                doc_id,
                item.get("line_no", idx),
                item.get("description", "Item"),
                item.get("hsn_sac"),
                item.get("tally_stock_item"),
                item.get("qty", 1.0),
                item.get("unit", "PCS"),
                item.get("rate", 0.0),
                item.get("taxable_value", 0.0),
                item.get("cgst_rate", 0.0),
                item.get("cgst_amount", 0.0),
                item.get("sgst_rate", 0.0),
                item.get("sgst_amount", 0.0),
                item.get("igst_rate", 0.0),
                item.get("igst_amount", 0.0),
                item.get("line_total", 0.0)
            )

    return doc_id


async def main(staging_dir: Path = STAGING_DIR, db_url: str = DEFAULT_LOCAL_DB):
    print("=" * 80)
    print("  SNM Works — Trade Document Staging Bridge")
    print(f"  Staging Directory: {staging_dir}")
    print(f"  Target DB:        {db_url}")
    print("=" * 80)

    staging_dir.mkdir(parents=True, exist_ok=True)
    json_files = list(staging_dir.glob("*.json"))
    if not json_files:
        print(f"No parsed JSON documents found in {staging_dir}.")
        return

    conn = await asyncpg.connect(db_url)
    try:
        async with conn.transaction():
            await set_rls_claims(conn, {
                "sub": str(DEFAULT_OWNER_ID),
                "email": "yashkhandelwal95@gmail.com",
                "role": "authenticated"
            })

            staged_count = 0
            for jf in json_files:
                try:
                    with open(jf, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    data["parsed_json_path"] = str(jf)
                    doc_id = await stage_trade_document(conn, data, DEFAULT_OWNER_ID)
                    staged_count += 1
                    print(f" -> Staged [{data.get('doc_type')}]: {data.get('party_name')} - {data.get('doc_number')} (ID: {doc_id})")
                except Exception as e:
                    print(f" [!] Error staging {jf.name}: {e}")

            print(f"\nSuccessfully staged {staged_count} trade documents for interactive review.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage parsed trade documents into database")
    parser.add_argument("--dir", default=str(STAGING_DIR), help="Path to parsed staging directory")
    parser.add_argument("--db", default=DEFAULT_LOCAL_DB, help="Database connection URL")
    args = parser.parse_args()

    asyncio.run(main(Path(args.dir), args.db))
