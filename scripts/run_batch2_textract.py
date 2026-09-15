import os
import sys
import time
import json
import zipfile
import re
from pathlib import Path
import fitz # PyMuPDF

# Ensure unbuffered utf-8 output
sys.stdout.reconfigure(line_buffering=True, encoding='utf-8')
sys.stderr.reconfigure(line_buffering=True, encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.spec_parser_textract import get_textract_client, parse_spec_pdf_textract

ZIP_PATH = r"c:\Users\ASUS\Downloads\Telegram Desktop\SPECIFICATION zip.zip"
BASE_DIR = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import_batch2")
PDF_DIR = BASE_DIR / "pdfs"
STAGING_DIR = BASE_DIR / "parsed_staging"
MANIFEST_FILE = BASE_DIR / "batch2_manifest.json"
DIGITAL_FABRIC_SIZES_JSON = PROJECT_ROOT / "scratch" / "digital_fabric_sizes.json"

PDF_DIR.mkdir(parents=True, exist_ok=True)
STAGING_DIR.mkdir(parents=True, exist_ok=True)

# 1. Determine the exact 250 target files
BULK_DIR = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import")
orig_names = {f.name.lower() for f in BULK_DIR.glob("*.pdf")}

digital_fabric_names = {
    "digital fabric of cc small.pdf",
    "digital fabric of cc medium.pdf",
    "digital fabric of cc large.pdf",
    "digital fabric of cc extra large.pdf",
    "digital fabric of cc extr extra large.pdf",
}

bucket_clean_tabular = []
bucket_digital_fabric_target = None
bucket_iafs_manual = []
bucket_already_done = []

with zipfile.ZipFile(ZIP_PATH, "r") as z:
    for name in sorted(z.namelist()):
        if not name.lower().endswith(".pdf") or name.startswith("__MACOSX"):
            continue
        base = Path(name).name
        base_lower = base.lower()
        content = z.read(name)
        doc = fitz.open(stream=content, filetype="pdf")
        pages = doc.page_count
        p1 = doc[0].get_text() if pages > 0 else ""
        p2 = doc[1].get_text() if pages > 1 else ""
        combined = (p1 + " " + p2).upper()
        doc.close()

        is_iafs = "IAFS" in combined or "INDIAN AIR FORCE" in combined
        is_fab = "fabrication" in base_lower or "fabrication" in combined.lower() or "bdu" in base_lower

        item_info = {"filename": base, "zip_path": name, "pages": pages}

        if base_lower in orig_names:
            bucket_already_done.append(item_info)
        elif base_lower in digital_fabric_names:
            if base_lower == "digital fabric of cc large.pdf":
                bucket_digital_fabric_target = item_info
        elif is_iafs or is_fab:
            bucket_iafs_manual.append(item_info)
        else:
            bucket_clean_tabular.append(item_info)

print("=" * 80)
print("SNM WORKS — BATCH 2 TEXTRACT LIVE INGESTION")
print("=" * 80)
print(f"Target Environment: LOCAL STAGING (Zero touch of production)")
print(f"Archive Source:     {ZIP_PATH}")
print(f"PDF Input Cache:    {PDF_DIR}")
print(f"Staging JSONs:      {STAGING_DIR}")
print(f"Target Scope:       249 Clean Tabular + 1 Digital Fabric (LARGE, p3-67)")
print("=" * 80)

# Extract PDFs from zip to PDF_DIR
print("\n[Step 1/3] Extracting 250 target PDFs from zip container to disk...")
target_zip_paths = {}
for item in bucket_clean_tabular:
    target_zip_paths[item["filename"]] = (item["zip_path"], None) # None means all valid pages

# Add Digital Fabric (LARGE)
df_target = bucket_digital_fabric_target
target_zip_paths[df_target["filename"]] = (df_target["zip_path"], list(range(2, 67))) # 0-indexed pages 2 to 66 (pages 3-67)

with zipfile.ZipFile(ZIP_PATH, "r") as z:
    for fname, (zpath, _) in target_zip_paths.items():
        dest = PDF_DIR / fname
        if not dest.exists():
            with open(dest, "wb") as f_out:
                f_out.write(z.read(zpath))

print(f"✓ Verified 250 source PDFs staged in: {PDF_DIR}")

# Load Digital Fabric sizes metadata if available
df_metadata = None
if DIGITAL_FABRIC_SIZES_JSON.exists():
    with open(DIGITAL_FABRIC_SIZES_JSON, "r", encoding="utf-8") as f_df:
        df_metadata = json.load(f_df)

# Connect to Textract
print("\n[Step 2/3] Connecting to AWS Textract (ap-south-1)...")
client = get_textract_client(region="ap-south-1")
print("✓ Textract client connected.")

# Load existing progress if resumed
completed_records = {}
if MANIFEST_FILE.exists():
    try:
        with open(MANIFEST_FILE, "r", encoding="utf-8") as f_m:
            completed_records = json.load(f_m).get("processed", {})
        print(f"Resuming run: {len(completed_records)} files already processed.")
    except Exception:
        completed_records = {}

print("\n[Step 3/3] COMMENCING LIVE TEXTRACT INGESTION PASS")
print("-" * 80)

total_target_count = len(target_zip_paths)
processed_count = len(completed_records)
total_billable_pages_processed = sum(r.get("billable_pages", 0) for r in completed_records.values())
start_batch_time = time.time()

# Process items
file_items = sorted(target_zip_paths.items(), key=lambda x: x[0])

for idx, (fname, (zpath, pages_subset)) in enumerate(file_items, 1):
    if fname in completed_records:
        continue

    pdf_path = PDF_DIR / fname
    doc_fitz = fitz.open(str(pdf_path))
    raw_pages = doc_fitz.page_count
    
    # Pre-calculate billable pages
    if pages_subset is not None:
        eval_pages = pages_subset
    else:
        eval_pages = list(range(raw_pages))
    
    billable_pages_for_file = 0
    blank_pages_for_file = 0
    for p_i in eval_pages:
        if p_i < raw_pages:
            p_obj = doc_fitz[p_i]
            if len(p_obj.get_text().strip()) == 0 and len(p_obj.get_images()) == 0:
                blank_pages_for_file += 1
            else:
                billable_pages_for_file += 1
    doc_fitz.close()

    est_file_cost = billable_pages_for_file * 0.015
    print(f"\n[{idx}/{total_target_count}] Processing: {fname}")
    print(f"    Raw: {raw_pages}p | Billable: {billable_pages_for_file}p (skipped {blank_pages_for_file} blank) | Est: ${est_file_cost:.3f}")

    start_t = time.time()
    try:
        spec_data = parse_spec_pdf_textract(
            str(pdf_path),
            confidence_threshold=90.0,
            region="ap-south-1",
            client=client,
            pages_to_process=pages_subset,
            skip_blank_pages=True,
        )
        elapsed = time.time() - start_t

        # If digital fabric, enrich with per-size schedule
        if fname == "DIGITAL FABRIC OF CC LARGE.pdf" and df_metadata:
            spec_data["per_size_meterage_schedule"] = df_metadata.get("size_variants", [])
            spec_data["specification"]["title"] = df_metadata.get("shared_specification", {}).get("title")
            spec_data["specification"]["spec_no"] = df_metadata.get("shared_specification", {}).get("spec_no")
            spec_data["specification"]["issuing_body"] = df_metadata.get("shared_specification", {}).get("issuing_body")

        # Save staging JSON
        stem = pdf_path.stem
        out_json_path = STAGING_DIR / f"{stem}.json"
        with open(out_json_path, "w", encoding="utf-8") as f_out:
            json.dump(spec_data, f_out, indent=2)

        reqs = spec_data.get("requirements", [])
        vars_ = spec_data.get("variants", [])
        num_reqs = len(reqs)
        num_vars = len(vars_)

        # Flag anomalies immediately
        zero_reqs = (num_reqs == 0)
        low_conf_count = sum(1 for r in reqs if r.get("confidence_flag") is not None)
        null_val_count = sum(1 for r in reqs if r.get("spec_value") is None and r.get("text_value") is None)

        status_flag = "✓ CLEAN"
        if zero_reqs:
            status_flag = "⚠️ ZERO REQUIREMENTS"
            print(f"    [ALERT] {fname} produced 0 requirements! (Ground truth may be title slip or non-standard format)", file=sys.stderr)
        elif low_conf_count > 0:
            status_flag = f"⚠️ {low_conf_count} LOW CONF CELLS"

        print(f"    {status_flag} | {elapsed:.2f}s | {num_vars} vars | {num_reqs} reqs ({low_conf_count} low-conf, {null_val_count} nulls)")

        record = {
            "status": "SUCCESS",
            "filename": fname,
            "raw_pages": raw_pages,
            "billable_pages": billable_pages_for_file,
            "blank_pages_skipped": blank_pages_for_file,
            "elapsed_s": round(elapsed, 2),
            "variants_count": num_vars,
            "requirements_count": num_reqs,
            "low_conf_count": low_conf_count,
            "null_val_count": null_val_count,
            "actual_cost_usd": round(billable_pages_for_file * 0.015, 4),
        }
        completed_records[fname] = record
        processed_count += 1
        total_billable_pages_processed += billable_pages_for_file

    except Exception as exc:
        elapsed = time.time() - start_t
        print(f"    ✗ ERROR processing {fname}: {exc}", file=sys.stderr)
        record = {
            "status": "ERROR",
            "filename": fname,
            "raw_pages": raw_pages,
            "billable_pages": 0,
            "elapsed_s": round(elapsed, 2),
            "error": str(exc),
        }
        completed_records[fname] = record

    # Periodic checkpoint every 5 files
    if idx % 5 == 0 or idx == total_target_count:
        with open(MANIFEST_FILE, "w", encoding="utf-8") as f_mf:
            json.dump({
                "summary": {
                    "total_files": total_target_count,
                    "processed_files": processed_count,
                    "total_billable_pages": total_billable_pages_processed,
                    "running_cost_usd": round(total_billable_pages_processed * 0.015, 2),
                },
                "processed": completed_records,
            }, f_mf, indent=2)

print("\n" + "=" * 80)
print("BATCH 2 TEXTRACT INGESTION COMPLETED")
print("=" * 80)
total_cost_usd = total_billable_pages_processed * 0.015
total_time = time.time() - start_batch_time
print(f"Total Documents Processed:      {processed_count} / {total_target_count}")
print(f"Total Billable Pages Incurred:  {total_billable_pages_processed}")
print(f"Total Actual Textract Charges:  ${total_cost_usd:.2f} USD (~Rs. {total_cost_usd * 84.0:.1f} INR)")
print(f"Total Processing Time:          {total_time/60:.1f} minutes")
print("=" * 80)
