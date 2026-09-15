import asyncio
import hashlib
import json
import sys
from pathlib import Path
import asyncpg

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)

PROJECT_ROOT = Path(r"C:\Users\ASUS\Downloads\SNM_WORKS")
UPLOAD_BASE_DIR = PROJECT_ROOT / "static" / "uploads"
BATCH1_STAGING = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import\parsed_staging")
BATCH2_STAGING = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import_batch2\parsed_staging")

def file_sha256(filepath: Path) -> str:
    if not filepath or not filepath.is_file():
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

async def main():
    conn = await asyncpg.connect("postgresql://postgres@127.0.0.1:5433/snm_test_db")

    sample_targets = [
        ("CANVAS COTTON 375 GM IAF.pdf", "Batch 1"),
        ("CLOTH PLAIN WEAVE KHAKI.pdf", "Batch 1"),
        ("ARAMID SEWING THREAD.pdf", "Batch 1"),
        ("2011_3.pdf", "Batch 1"),
        ("CLOTH DRILL COTTON.pdf", "Batch 1"),
        ("pp rope 6mm og.pdf", "Batch 2"),
        ("INTERLINING FABRIC POLYESTER.pdf", "Batch 2"),
        ("CORD 2452 UD OPF.pdf", "Batch 2"),
        ("WEBBING 25.5 MM BS 500 KG.pdf", "Batch 2"),
    ]

    print("=" * 100)
    print("SPEC_PDF_UPLOADS VS. DISK VS. CURRENT STAGING COMPARISON")
    print("=" * 100)

    for fname, batch in sample_targets:
        r = await conn.fetchrow("""
            SELECT id, original_filename, parsed_json_path, parsed_json_sha256, status 
            FROM spec_pdf_uploads 
            WHERE lower(original_filename) = lower($1)
        """, fname)

        stem = Path(fname).stem
        b1_file = BATCH1_STAGING / f"{stem}.json"
        b2_file = BATCH2_STAGING / f"{stem}.json"

        if b1_file.exists():
            staging_file = b1_file
        elif b2_file.exists():
            staging_file = b2_file
        else:
            staging_file = None

        staging_sha = file_sha256(staging_file)

        if not r:
            print(f"\n[{fname}] ({batch})")
            print(f"  DB Status:               NOT PRESENT IN spec_pdf_uploads")
            print(f"  1. DB SHA256:            N/A")
            print(f"  2. Disk copy SHA256:     N/A (Never staged into static/uploads/spec-parsed/)")
            print(f"  3. Current staging SHA:  {staging_sha}")
            print(f"     Path: {staging_file}")
            print(f"  Verdict:                 NOT STAGED IN DB YET (Only exists in bulk_spec_import_batch2/parsed_staging)")
            continue

        db_sha = r["parsed_json_sha256"]
        json_rel = r["parsed_json_path"] or ""
        disk_copy = UPLOAD_BASE_DIR / json_rel.lstrip("/\\")
        disk_sha = file_sha256(disk_copy)

        matches_db_disk = (db_sha == disk_sha)
        matches_disk_staging = (disk_sha == staging_sha)
        matches_db_staging = (db_sha == staging_sha)

        print(f"\n[{fname}] ({batch})")
        print(f"  DB ID:                   {r['id']}")
        print(f"  DB Status:               {r['status']}")
        print(f"  1. DB SHA256:            {db_sha}")
        print(f"  2. Disk copy SHA256:     {disk_sha}")
        print(f"     Path: {disk_copy}")
        print(f"  3. Current staging SHA:  {staging_sha}")
        print(f"     Path: {staging_file}")
        print(f"  Match? DB == Disk: {matches_db_disk} | Disk == CurrentStaging: {matches_disk_staging}")
        if matches_db_disk and not matches_disk_staging:
            print(f"  Verdict:                 ⚠️ DIVERGED / STALE (DB and static/uploads are in sync with each other, but hold the PRE-FIX JSON!)")
        elif matches_disk_staging and matches_db_staging:
            print(f"  Verdict:                 ✓ IN SYNC")
        else:
            print(f"  Verdict:                 ⚠️ DIVERGED")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
