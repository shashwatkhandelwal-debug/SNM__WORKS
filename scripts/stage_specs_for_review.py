#!/usr/bin/env python3
"""
scripts/stage_specs_for_review.py — Stage Bulk Ingested Specifications for Interactive Split-Screen Review.

Bridges 44 auto-extracted specifications + 11 manual-review specifications into
the existing spec_pdf_uploads review layer:
1. Verifies storage mirroring into static/uploads/spec-docs/ and static/uploads/spec-parsed/.
2. Connects to database, auto-detecting schema constraints (sku_id nullability, source check).
3. Creates idempotent spec_pdf_uploads rows (status='Parsed') with deterministic UUIDs.
4. Generates an interactive, family-grouped review queue HTML.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import asyncpg

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from auth.middleware import set_rls_claims

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("stage_specs_for_review")

DEFAULT_LOCAL_DB = "postgresql://postgres@127.0.0.1:5433/snm_test_db"
DEFAULT_OWNER_ID = uuid.UUID("f535177d-9be5-410d-b85c-4a4d82a9c0ae")

INPUT_DIR = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import")
STAGING_DIR = INPUT_DIR / "parsed_staging"
UPLOAD_BASE_DIR = PROJECT_ROOT / "static" / "uploads"

BATCH_CONFIG = [
    ("Batch 1: Canvas (Cotton & Flax)", ["canvas"]),
    ("Batch 2: Fabrics, Drills & Ducks", ["drill", "duck", "calico", "ripstop", "rip", "plain weave", "blended fabric"]),
    ("Batch 3: Webbing & Belting", ["belt", "web belt"]),
    ("Batch 4: Cordage & Braids (Nylon)", ["cord nylon", "braided cord", "cord 1800", "cord 200", "cord 250", "cord 68", "cord 2940", "cord 3120", "cord 3600", "cord 12740"]),
    ("Batch 5: Cordage (Cotton, Flax & Elastic)", ["cotton braided", "cord flax", "cord 1785 black", "elastic"]),
    ("Batch 6: Defence Standards (IND/TC & DMSRDE)", ["1981_6", "2000_55", "2011_3", "2018_6", "2019_2", "2020_02", "2021_2", "78(a)", "94(a)"]),
    ("Batch 7: Sewing Thread", ["thread", "aramid"]),
    ("Batch 8: Omnibus Standards & End-Item Manuals", ["cord 1785 white", "cord 2450n", "cord lock", "adrde 6 items", "bag kit", "assistance of", "cyq"]),
]


def assign_batch(filename: str) -> str:
    lower = filename.lower()
    # Check Batch 8 manual items first
    for k in BATCH_CONFIG[7][1]:
        if k in lower:
            return BATCH_CONFIG[7][0]
    # Check Batch 6 year/number specs
    for k in BATCH_CONFIG[5][1]:
        if k in lower:
            return BATCH_CONFIG[5][0]
    # Check other batches
    for batch_name, keywords in BATCH_CONFIG:
        for kw in keywords:
            if kw in lower:
                return batch_name
    return "Batch 8: Omnibus Standards & End-Item Manuals"


async def main_async(target: str):
    print("=" * 80)
    print("  SNM Works — Spec Review Bridge & Staging Orchestrator")
    print(f"  Source PDFs:     {INPUT_DIR}")
    print(f"  Staging JSONs:   {STAGING_DIR}")
    print("=" * 80)

    # 1. Ensure local storage mirrors exist
    (UPLOAD_BASE_DIR / "spec-docs" / "standalone").mkdir(parents=True, exist_ok=True)
    (UPLOAD_BASE_DIR / "spec-parsed").mkdir(parents=True, exist_ok=True)

    # 2. Resolve database target connection string
    if target == "prod":
        from config import settings
        db_url = settings.database_url
        if not db_url:
            print("ERROR: settings.database_url is not set.", file=sys.stderr)
            sys.exit(1)
        masked_host = db_url.split("@")[-1] if "@" in db_url else db_url
        print("=" * 80)
        print("WARNING: TARGET IS PRODUCTION DATABASE!")
        print(f"Target Host:       {masked_host}")
        print("=" * 80)
        confirm = input("Are you sure you want to stage specifications into PRODUCTION? Type 'yes' to proceed: ")
        if confirm.strip().lower() != "yes":
            print("Aborted by user.")
            sys.exit(1)
    else:
        db_url = DEFAULT_LOCAL_DB

    target_display = "PRODUCTION" if target == "prod" else f"LOCAL ({db_url})"
    print(f"Connecting to {target_display}...")

    try:
        conn = await asyncpg.connect(db_url)
    except Exception as e:
        print(f"Database connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        # Resolve uploader profile before querying RLS-protected tables
        uploader_id = await conn.fetchval("SELECT id FROM profiles WHERE role = 'owner' LIMIT 1;")
        if not uploader_id:
            uploader_id = DEFAULT_OWNER_ID

        # Precondition check: fail-fast on schema prerequisites
        sku_col_info = await conn.fetchrow("""
            SELECT is_nullable FROM information_schema.columns
            WHERE table_name = 'spec_pdf_uploads' AND column_name = 'sku_id';
        """)
        sku_nullable = (sku_col_info and sku_col_info['is_nullable'] == 'YES')

        source_con = await conn.fetchval("""
            SELECT pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid = 'public.spec_pdf_uploads'::regclass AND contype = 'c'
              AND conname = 'spec_pdf_uploads_source_check';
        """)
        allows_bulk = bool(source_con and 'bulk_textract_batch' in source_con)

        if not sku_nullable or not allows_bulk:
            raise RuntimeError("Migration 29 (spec_uploads_standalone_and_source) is not applied on this target. Aborting.")

        target_sku_id = None
        source_val = 'bulk_textract_batch'

        # 3. Read manual review list
        manual_csv_path = STAGING_DIR / "manual_review_needed.csv"
        manual_docs = {}
        if manual_csv_path.exists():
            with open(manual_csv_path, encoding="utf-8") as f_csv:
                reader = csv.DictReader(f_csv)
                for r in reader:
                    manual_docs[r["filename"]] = r["reason"]

        # 4. Scan all PDFs in input dir
        pdf_files = sorted(INPUT_DIR.glob("*.pdf"))
        print(f"\nProcessing {len(pdf_files)} specification documents...")

        staged_items: List[Dict[str, Any]] = []

        for pdf_path in pdf_files:
            fn = pdf_path.name
            clean_stem = pdf_path.stem
            is_manual = fn in manual_docs

            # Deterministic upload UUID based on filename
            upload_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"snm.spec.{fn.lower()}")

            # Read PDF and compute hash
            pdf_bytes = pdf_path.read_bytes()
            pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

            # Store PDF locally
            pdf_rel = f"standalone/{upload_id}.pdf"
            local_pdf_path = UPLOAD_BASE_DIR / "spec-docs" / pdf_rel
            local_pdf_path.parent.mkdir(parents=True, exist_ok=True)
            local_pdf_path.write_bytes(pdf_bytes)

            # Resolve JSON content
            json_staging_path = STAGING_DIR / f"{clean_stem}.json"
            if not is_manual and json_staging_path.exists():
                json_bytes = json_staging_path.read_bytes()
                spec_data = json.loads(json_bytes.decode("utf-8"))
            else:
                reason = manual_docs.get(fn, "Manual review entry")
                spec_data = {
                    "specification": {
                        "spec_no": clean_stem,
                        "revision": "R0",
                        "title": clean_stem,
                        "issuing_body": "INDIAN DEFENCE",
                        "notes": f"Routed to manual review: {reason}",
                    },
                    "variants": [],
                    "requirements": [],
                    "defects": [],
                    "sampling": [],
                }
                json_bytes = json.dumps(spec_data, indent=2).encode("utf-8")

            json_sha256 = hashlib.sha256(json_bytes).hexdigest()
            json_rel = f"{upload_id}_raw.json"
            local_json_path = UPLOAD_BASE_DIR / "spec-parsed" / json_rel
            local_json_path.write_bytes(json_bytes)

            # Insert or update spec_pdf_uploads in DB inside transaction with RLS claims
            async with conn.transaction():
                await set_rls_claims(conn, {"sub": str(uploader_id), "email": "yashkhandelwal95@gmail.com", "role": "authenticated"})
                await conn.execute("""
                    INSERT INTO spec_pdf_uploads (
                        id, sku_id, storage_path, pdf_sha256, parsed_json_path,
                        parsed_json_sha256, source, original_filename, file_size_bytes,
                        status, uploaded_by
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, 'Parsed', $10
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        sku_id = EXCLUDED.sku_id,
                        storage_path = EXCLUDED.storage_path,
                        pdf_sha256 = EXCLUDED.pdf_sha256,
                        parsed_json_path = EXCLUDED.parsed_json_path,
                        parsed_json_sha256 = EXCLUDED.parsed_json_sha256,
                        source = EXCLUDED.source,
                        original_filename = EXCLUDED.original_filename,
                        file_size_bytes = EXCLUDED.file_size_bytes,
                        status = 'Parsed';
                """,
                    upload_id,
                    target_sku_id,
                    f"spec-docs/{pdf_rel}",
                    pdf_sha256,
                    f"spec-parsed/{json_rel}",
                    json_sha256,
                    source_val,
                    fn,
                    len(pdf_bytes),
                    uploader_id,
                )

            batch_name = assign_batch(fn)
            spec_header = spec_data.get("specification", {})
            staged_items.append({
                "upload_id": str(upload_id),
                "filename": fn,
                "batch": batch_name,
                "is_manual": is_manual,
                "spec_no": spec_header.get("spec_no"),
                "title": spec_header.get("title"),
                "req_count": len(spec_data.get("requirements", [])),
                "var_count": len(spec_data.get("variants", [])),
                "review_url": f"/skus/standalone/spec-review/{upload_id}",
                "pdf_url": f"/skus/standalone/spec-uploads/{upload_id}/pdf",
            })
    finally:
        await conn.close()

    # 5. Generate Review Queue HTML
    queue_html = generate_queue_html(staged_items)
    out_html_path = STAGING_DIR / "review_queue.html"
    out_html_path.write_text(queue_html, encoding="utf-8")
    print(f"\n[Artifact] Generated interactive Review Queue: {out_html_path}")

    # 6. Print console summary by batch
    print("\n" + "=" * 80)
    print("  SPECIFICATION REVIEW QUEUE — BATCH BREAKDOWN")
    print("=" * 80)

    by_batch: Dict[str, List[Dict[str, Any]]] = {}
    for it in staged_items:
        by_batch.setdefault(it["batch"], []).append(it)

    total_reqs = sum(it["req_count"] for it in staged_items)
    for b_title, kw in BATCH_CONFIG:
        items = by_batch.get(b_title, [])
        if not items:
            continue
        auto_c = sum(1 for it in items if not it["is_manual"])
        man_c = sum(1 for it in items if it["is_manual"])
        b_reqs = sum(it["req_count"] for it in items)
        print(f"\n[{b_title}] — {len(items)} specs (Auto: {auto_c}, Manual: {man_c}) | {b_reqs} reqs")
        for it in items:
            tag = "[MANUAL]" if it["is_manual"] else "[AUTO]  "
            req_str = f"{it['req_count']:>2} reqs" if not it["is_manual"] else "  blank"
            print(f"   {tag} {req_str} | {it['filename']:<42} -> {it['review_url']}")

    print("\n" + "=" * 80)
    print(f"  TOTAL STAGED FOR REVIEW: {len(staged_items)} documents ({total_reqs} extracted requirements)")
    print("  Ready for interactive review at: http://localhost:8080/skus/standalone/spec-review/<upload_id>")
    print("=" * 80)


def generate_queue_html(items: List[Dict[str, Any]]) -> str:
    rows_html = ""
    curr_batch = None
    for it in items:
        if it["batch"] != curr_batch:
            curr_batch = it["batch"]
            rows_html += f"""
            <tr style="background: #E9E5DA; font-weight: 700; font-family: monospace;">
                <td colspan="6" style="padding: 10px 12px; color: #1B2017;">{curr_batch}</td>
            </tr>
            """
        tag_class = "badge-hold" if it["is_manual"] else "badge-pass"
        tag_text = "MANUAL" if it["is_manual"] else "AUTO"
        rows_html += f"""
        <tr style="border-bottom: 1px solid #CFC8B6;">
            <td style="padding: 8px 12px;"><span style="display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 0.75rem; font-family: monospace;" class="badge {tag_class}">{tag_text}</span></td>
            <td style="padding: 8px 12px; font-weight: 600;">{it['filename']}</td>
            <td style="padding: 8px 12px; font-family: monospace;">{it['spec_no'] or '—'}</td>
            <td style="padding: 8px 12px;">{it['title'] or '—'}</td>
            <td style="padding: 8px 12px; text-align: right; font-family: monospace;">{it['req_count']}</td>
            <td style="padding: 8px 12px; text-align: right;">
                <a href="{it['review_url']}" style="display: inline-block; background: #474B2F; color: #fff; padding: 4px 10px; border-radius: 3px; text-decoration: none; font-size: 0.8rem; font-weight: 600;">Review & Load →</a>
            </td>
        </tr>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Specification Review Queue — Swadeshi Niwar Mills</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #F6F4EE; color: #1B2017; margin: 2rem; }}
        h1 {{ font-family: 'Barlow Condensed', sans-serif; letter-spacing: 0.04em; margin-bottom: 0.25rem; }}
        table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #CFC8B6; margin-top: 1rem; font-size: 0.85rem; }}
        th {{ background: #1B2017; color: #fff; text-align: left; padding: 10px 12px; font-size: 0.8rem; letter-spacing: 0.05em; }}
        .badge-pass {{ background: #3F6B34; color: #fff; }}
        .badge-hold {{ background: #9A6407; color: #fff; }}
    </style>
</head>
<body>
    <h1>SPECIFICATION REVIEW QUEUE (55 DOCUMENTS)</h1>
    <p style="color: #666; font-size: 0.9rem; margin-top: 0;">
        Organized by technical textile families. Split-screen verification against source military and industrial standards.
    </p>
    <table>
        <thead>
            <tr>
                <th style="width: 80px;">Mode</th>
                <th>Source PDF</th>
                <th style="width: 180px;">Spec Number</th>
                <th>Extracted / Document Title</th>
                <th style="width: 80px; text-align: right;">Reqs</th>
                <th style="width: 130px; text-align: right;">Action</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Stage bulk ingested specification documents for review.")
    parser.add_argument(
        "--target",
        choices=["local", "prod"],
        default="local",
        help="Target database: 'local' (default, 127.0.0.1:5433) or 'prod' (Supabase production).",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args.target))


if __name__ == "__main__":
    main()
