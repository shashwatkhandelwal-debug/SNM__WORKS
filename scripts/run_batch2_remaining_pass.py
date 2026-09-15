import os
import sys
import time
import json
from pathlib import Path
import fitz

# Ensure unbuffered utf-8 output
sys.stdout.reconfigure(line_buffering=True, encoding='utf-8')
sys.stderr.reconfigure(line_buffering=True, encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.spec_parser_textract import get_textract_client, parse_spec_pdf_textract

BASE_DIR = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import_batch2")
PDF_DIR = BASE_DIR / "pdfs"
STAGING_DIR = BASE_DIR / "parsed_staging"
MANIFEST_FILE = BASE_DIR / "batch2_manifest.json"
CACHE_DIR = BASE_DIR / "raw_textract_cache"
DIGITAL_FABRIC_SIZES_JSON = PROJECT_ROOT / "scratch" / "digital_fabric_sizes.json"
BEFORE_SNAPSHOT_FILE = PROJECT_ROOT / "scratch" / "before_snapshot_236.json"
FINAL_REPORT_FILE = PROJECT_ROOT / "scratch" / "batch2_final_236_report.json"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
STAGING_DIR.mkdir(parents=True, exist_ok=True)

TARGET_14 = {
    "pp rope 6mm og.pdf",
    "INTERLINING FABRIC POLYESTER.pdf",
    "CORD 2452 UD OPF.pdf",
    "CORD 492N VR NO 5.pdf",
    "CORD NYLON 23 KGF.pdf",
    "FABRIC NYLON 48 GRM PLAIN WEAVE.pdf",
    "FABRIC NYLON 48 GSM.pdf",
    "FABRIC PLAIN WEAVE 48GSM.pdf",
    "fabric_nylon_66_48_gsmspec_2002_56_b_2026-02-02-15-11-25_452343b5b309fd0dbd80629d1fd9abb3.pdf",
    "FABRIC NYLON 66 AIE TEXSTURED.pdf",
    "WEBBING 25.5 MM BS 500 KG.pdf",
    "WEBBING 25.5MM 550 KGF.pdf",
    "WEBBING NYLON 14MM 1100 KGF.pdf",
    "WEBBING NYLON 51MM 480 KG.pdf"
}

all_pdfs = sorted([f.name for f in PDF_DIR.glob("*.pdf")])
target_files = [f for f in all_pdfs if f not in TARGET_14]

print("=" * 80)
print("SNM WORKS — BATCH 2 REMAINING 236 DOCUMENTS INGESTION PASS")
print("=" * 80)
print(f"Target PDFs:        {len(target_files)} files (236 remaining)")
print(f"Excluded:           14 files already reprocessed")
print(f"Response Cache:     {CACHE_DIR}")
print(f"Staging Directory:  {STAGING_DIR}")
print(f"Hard Budget Cap:    $30.00")
print(f"Checkpoint Interval: Every 25 documents")
print("=" * 80)

# Load digital fabric metadata
df_metadata = None
if DIGITAL_FABRIC_SIZES_JSON.exists():
    try:
        with open(DIGITAL_FABRIC_SIZES_JSON, "r", encoding="utf-8") as f_df:
            df_metadata = json.load(f_df)
    except Exception as e:
        print(f"Warning: Could not load {DIGITAL_FABRIC_SIZES_JSON}: {e}")

# Connect to Textract
client = get_textract_client(region="ap-south-1")
print("Connected to AWS Textract (ap-south-1).\n")

# Load existing manifest
manifest_data = {}
if MANIFEST_FILE.exists():
    try:
        with open(MANIFEST_FILE, "r", encoding="utf-8") as f_m:
            manifest_data = json.load(f_m)
    except Exception as e:
        print(f"Warning: Could not read manifest: {e}")
        manifest_data = {}

manifest_processed = manifest_data.setdefault("processed", {})
run_meta = manifest_data.setdefault("batch_236_run", {
    "status": "IN_PROGRESS",
    "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    "target_count": len(target_files),
    "processed_count": 0,
    "successful_count": 0,
    "failed_count": 0,
    "billable_pages": 0,
    "incurred_cost_usd": 0.0,
    "failed_files": [],
    "checkpoints": [],
})

HARD_BUDGET_CAP_USD = 30.00
running_billable_pages = 0
running_cost_usd = 0.0
successful_count = 0
failed_records = []
consecutive_zero_req_count = 0
results = []

start_total_time = time.time()

for idx, fname in enumerate(target_files, 1):
    # Hard budget check
    if running_cost_usd >= HARD_BUDGET_CAP_USD:
        print(f"\n[ALERT] HARD BUDGET CAP OF ${HARD_BUDGET_CAP_USD:.2f} REACHED! Stopping run immediately.")
        run_meta["status"] = "STOPPED_BUDGET_CAP"
        break

    pdf_path = PDF_DIR / fname
    if not pdf_path.exists():
        print(f"[{idx}/236] ERROR: {fname} does not exist on disk!")
        failed_records.append({"filename": fname, "error": "File not found", "attempts": 0})
        continue

    # Determine pages
    pages_subset = None
    if fname == "DIGITAL FABRIC OF CC LARGE.pdf":
        pages_subset = list(range(2, 67)) # Pages 3-67

    doc_fitz = fitz.open(str(pdf_path))
    raw_pages = doc_fitz.page_count
    if pages_subset is not None:
        eval_pages = pages_subset
    else:
        eval_pages = list(range(raw_pages))

    billable_pages = 0
    blank_pages = 0
    for p_i in eval_pages:
        if p_i < raw_pages:
            p_obj = doc_fitz[p_i]
            if len(p_obj.get_text().strip()) == 0 and len(p_obj.get_images()) == 0:
                blank_pages += 1
            else:
                billable_pages += 1
    doc_fitz.close()

    est_doc_cost = billable_pages * 0.015
    print(f"\n[{idx}/236] Processing: {fname}")
    print(f"    Raw: {raw_pages}p | Billable: {billable_pages}p (skipped {blank_pages} blank) | Est: ${est_doc_cost:.3f} | Run Total: ${running_cost_usd + est_doc_cost:.3f}")

    # Process with retry (up to 2 retries = 3 attempts total)
    spec_data = None
    attempts = 0
    max_attempts = 3
    last_error = None

    while attempts < max_attempts and spec_data is None:
        attempts += 1
        start_t = time.time()
        try:
            spec_data = parse_spec_pdf_textract(
                str(pdf_path),
                confidence_threshold=90.0,
                region="ap-south-1",
                client=client,
                pages_to_process=pages_subset,
                skip_blank_pages=True,
                cache_dir=str(CACHE_DIR),
            )
            elapsed = time.time() - start_t
        except Exception as exc:
            last_error = str(exc)
            elapsed = time.time() - start_t
            print(f"    [ATTEMPT {attempts}/{max_attempts} FAILED] {exc} ({elapsed:.2f}s)")
            if attempts < max_attempts:
                time.sleep(2 * attempts)

    if spec_data is None:
        print(f"    ❌ FAILED after {max_attempts} attempts. Moving to next file.")
        failed_records.append({"filename": fname, "error": last_error, "attempts": max_attempts})
        manifest_processed[fname] = {
            "status": "FAILED",
            "error": last_error,
            "billable_pages": billable_pages,
            "attempts": max_attempts,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        continue

    # Enrich digital fabric if applicable
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

    clean_count = sum(1 for r in reqs if r.get("confidence_flag") is None)
    low_struct_count = sum(1 for r in reqs if r.get("confidence_flag") and "LOW_STRUCTURE_CONFIDENCE" in r.get("confidence_flag"))
    low_conf_count = sum(1 for r in reqs if r.get("confidence_flag") and "LOW_CONFIDENCE" in r.get("confidence_flag"))
    null_val_count = sum(1 for r in reqs if r.get("spec_value") is None and r.get("text_value") is None)

    # Check suspicious
    suspicious = 0
    for r in reqs:
        p = (r.get("parameter") or "").strip()
        if any(p.startswith(k) for k in ("IS:", "IS :", "JSS", "8.", "9.", "12", "15", "3.70", "101")):
            suspicious += 1

    running_billable_pages += billable_pages
    running_cost_usd += est_doc_cost
    successful_count += 1

    if len(reqs) == 0:
        consecutive_zero_req_count += 1
    else:
        consecutive_zero_req_count = 0

    print(f"    ✓ {elapsed:.2f}s | {len(vars_)} vars | {len(reqs)} reqs ({clean_count} clean, {low_struct_count} low-struct, {low_conf_count} low-conf, {null_val_count} nulls) | {suspicious} suspicious")

    manifest_processed[fname] = {
        "status": "SUCCESS",
        "billable_pages": billable_pages,
        "total_requirements": len(reqs),
        "clean_requirements": clean_count,
        "low_structure_confidence": low_struct_count,
        "low_confidence": low_conf_count,
        "null_values": null_val_count,
        "variants": len(vars_),
        "elapsed_sec": round(elapsed, 2),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    results.append({
        "filename": fname,
        "billable_pages": billable_pages,
        "variants": len(vars_),
        "requirements": len(reqs),
        "clean": clean_count,
        "low_structure": low_struct_count,
        "low_conf": low_conf_count,
        "nulls": null_val_count,
        "suspicious": suspicious,
        "elapsed": round(elapsed, 2),
    })

    # Anomaly safety stop
    if consecutive_zero_req_count >= 10:
        print(f"\n[ALERT] 10 consecutive documents produced 0 requirements! Stopping run for investigation.")
        run_meta["status"] = "STOPPED_CONSECUTIVE_ZERO_REQS"
        break

    # Checkpoint every 25 documents or at end
    if idx % 25 == 0 or idx == len(target_files):
        run_meta["processed_count"] = idx
        run_meta["successful_count"] = successful_count
        run_meta["failed_count"] = len(failed_records)
        run_meta["billable_pages"] = running_billable_pages
        run_meta["incurred_cost_usd"] = round(running_cost_usd, 3)
        run_meta["failed_files"] = failed_records
        run_meta["checkpoints"].append({
            "index": idx,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cost_usd": round(running_cost_usd, 3),
            "billable_pages": running_billable_pages,
            "successful": successful_count,
            "failed": len(failed_records),
        })
        with open(MANIFEST_FILE, "w", encoding="utf-8") as f_m:
            json.dump(manifest_data, f_m, indent=2)
        print(f"\n>>> [CHECKPOINT at {idx}/{len(target_files)}] Pages: {running_billable_pages} | Cost: ${running_cost_usd:.3f} | Success: {successful_count} | Failed: {len(failed_records)} <<<\n")

# Finalize run metadata
if run_meta["status"] == "IN_PROGRESS":
    run_meta["status"] = "COMPLETED"

run_meta["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
run_meta["total_elapsed_sec"] = round(time.time() - start_total_time, 1)
run_meta["processed_count"] = len(results) + len(failed_records)
run_meta["successful_count"] = successful_count
run_meta["failed_count"] = len(failed_records)
run_meta["billable_pages"] = running_billable_pages
run_meta["incurred_cost_usd"] = round(running_cost_usd, 3)
run_meta["failed_files"] = failed_records

with open(MANIFEST_FILE, "w", encoding="utf-8") as f_m:
    json.dump(manifest_data, f_m, indent=2)

print("\n" + "=" * 80)
print(f"RUN FINISHED WITH STATUS: {run_meta['status']}")
print(f"Total Processed:    {len(results)} success / {len(failed_records)} failed out of {len(target_files)}")
print(f"Total Pages:        {running_billable_pages}")
print(f"Total Incurred Cost: ${running_cost_usd:.3f}")
print(f"Total Run Time:     {run_meta['total_elapsed_sec'] / 60:.1f} minutes")
print("=" * 80)

# Build Before vs After comparison if snapshot exists
if BEFORE_SNAPSHOT_FILE.exists():
    try:
        with open(BEFORE_SNAPSHOT_FILE, "r", encoding="utf-8") as f_b:
            before_snapshot = json.load(f_b)

        before_total_reqs = sum(v["requirements"] for v in before_snapshot.values())
        before_clean_reqs = sum(v["clean"] for v in before_snapshot.values())
        before_low_conf = sum(v["low_conf"] for v in before_snapshot.values())
        before_nulls = sum(v["nulls"] for v in before_snapshot.values())
        before_suspicious = sum(v["suspicious"] for v in before_snapshot.values())

        after_total_reqs = sum(r["requirements"] for r in results)
        after_clean_reqs = sum(r["clean"] for r in results)
        after_low_struct = sum(r["low_structure"] for r in results)
        after_low_conf = sum(r["low_conf"] for r in results)
        after_nulls = sum(r["nulls"] for r in results)
        after_suspicious = sum(r["suspicious"] for r in results)

        comparison_report = {
            "run_metadata": run_meta,
            "before_totals": {
                "requirements": before_total_reqs,
                "clean_requirements": before_clean_reqs,
                "low_confidence": before_low_conf,
                "null_values": before_nulls,
                "suspicious_parameters": before_suspicious,
            },
            "after_totals": {
                "requirements": after_total_reqs,
                "clean_requirements": after_clean_reqs,
                "low_structure_confidence_rescued": after_low_struct,
                "low_confidence": after_low_conf,
                "null_values": after_nulls,
                "suspicious_parameters": after_suspicious,
            },
            "failed_files": failed_records,
            "detailed_results": results,
        }

        with open(FINAL_REPORT_FILE, "w", encoding="utf-8") as f_r:
            json.dump(comparison_report, f_r, indent=2)

        print("\n=== BEFORE vs AFTER AUDIT SUMMARY (236 DOCUMENTS) ===")
        print(f"Total Requirements:       {before_total_reqs} -> {after_total_reqs}")
        print(f"Clean Requirements:        {before_clean_reqs} -> {after_clean_reqs}")
        print(f"LOW_STRUCTURE Rescued:     0 -> {after_low_struct}")
        print(f"LOW_CONFIDENCE:            {before_low_conf} -> {after_low_conf}")
        print(f"Null Values:               {before_nulls} -> {after_nulls}")
        print(f"Suspicious Parameters:     {before_suspicious} -> {after_suspicious}")
        print("=" * 80)
    except Exception as e:
        print(f"Warning: Could not build comparison report: {e}")
