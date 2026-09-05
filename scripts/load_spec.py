#!/usr/bin/env python3
"""
scripts/load_spec.py — CLI Specification Document Loader for SNM Works.

Thin CLI wrapper around services.spec_loader.load_specification_document.

Usage:
  python scripts/load_spec.py <path/to/spec.json> [--target {local,prod}] [--created-by <UUID>]
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Optional
import uuid
import asyncpg

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.spec_loader import (
    load_specification_document,
    normalize_limit_type,
    normalize_defect_classification_and_clause,
    parse_date,
)

DEFAULT_LOCAL_DB = "postgresql://postgres@127.0.0.1:5433/snm_test_db"
DEFAULT_TEST_PROFILE_ID = "f535177d-9be5-410d-b85c-4a4d82a9c0ae"


async def main_async(json_path: Path, target: str, created_by_arg: Optional[str]):
    if not json_path.is_file():
        print(f"Error: File not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    # Validate created_by
    if target == "prod":
        if not created_by_arg:
            print("Error: --created-by <UUID> is required when targeting production.", file=sys.stderr)
            sys.exit(1)
        raw_created_by = created_by_arg.strip()
    else:
        raw_created_by = (created_by_arg or DEFAULT_TEST_PROFILE_ID).strip()

    try:
        created_by_uuid = uuid.UUID(raw_created_by)
    except ValueError:
        print(f"Error: Invalid UUID format for --created-by: '{raw_created_by}'", file=sys.stderr)
        sys.exit(1)

    with open(json_path, "r", encoding="utf-8") as f:
        spec_data = json.load(f)

    if target == "prod":
        from config import settings
        db_url = settings.database_url
        masked_host = db_url.split("@")[-1] if "@" in db_url else db_url
        print("=" * 80)
        print("WARNING: TARGET IS PRODUCTION DATABASE!")
        print(f"Target Host:       {masked_host}")
        print(f"Created By UUID:   {created_by_uuid}")
        print("=" * 80)
        confirm = input("Are you sure you want to load this specification into PRODUCTION? Type 'yes' to proceed: ")
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
        async with conn.transaction():
            result = await load_specification_document(conn, spec_data, created_by_uuid)
    except Exception as e:
        print(f"Error during specification load (transaction rolled back): {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await conn.close()

    total_rows = (
        1
        + result["variants_inserted"]
        + result["requirements_inserted"]
        + result["defects_inserted"]
        + result["sampling_inserted"]
    )
    print("\n" + "=" * 80)
    print("SPECIFICATION LOAD SUMMARY")
    print("=" * 80)
    print(f"Target Database:  {target_display}")
    print(f"Specification:    {result['spec_no']} (Rev {result['revision']}) — {result['title']}")
    print(f"Created Spec ID:  {result['spec_id']}")
    print(f"Created By UUID:  {created_by_uuid}")
    print("")
    print("Rows Inserted:")
    print(f"  - specifications:       1")
    print(f"  - spec_variants:     {result['variants_inserted']:>4}")
    print(f"  - spec_requirements: {result['requirements_inserted']:>4}")
    print(f"  - spec_defects:      {result['defects_inserted']:>4}")
    print(f"  - spec_sampling:     {result['sampling_inserted']:>4}")
    print("  " + "-" * 25)
    print(f"  Total Rows:          {total_rows:>4}")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="Load a specification JSON definition into SNM Works database.")
    parser.add_argument("json_file", type=Path, help="Path to the specification JSON file.")
    parser.add_argument(
        "--target",
        choices=["local", "prod"],
        default="local",
        help="Target database: 'local' (default, 127.0.0.1:5433) or 'prod' (Supabase production).",
    )
    parser.add_argument(
        "--created-by",
        type=str,
        default=None,
        help="UUID of the profile creating this specification. Required for --target prod.",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args.json_file, args.target, args.created_by))


if __name__ == "__main__":
    main()
