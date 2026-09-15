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

CACHE_DIR.mkdir(parents=True, exist_ok=True)

TARGET_14 = [
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
]

print("=" * 80)
print("SNM WORKS — TARGETED RE-PROCESSING OF 14 FLAGGED DOCUMENTS")
print("=" * 80)
print(f"Target PDFs:        {len(TARGET_14)} files")
print(f"Response Cache:     {CACHE_DIR}")
print(f"Staging Output:     {STAGING_DIR}")
print("=" * 80)

# Connect to Textract
client = get_textract_client(region="ap-south-1")
print("Connected to AWS Textract (ap-south-1).\n")

results = []
total_pages = 0

for idx, fname in enumerate(TARGET_14, 1):
    pdf_path = PDF_DIR / fname
    if not pdf_path.exists():
        print(f"[{idx}/14] ERROR: {fname} not found!")
        continue

    doc_fitz = fitz.open(str(pdf_path))
    raw_pages = doc_fitz.page_count
    billable_pages = sum(1 for p in doc_fitz if len(p.get_text().strip()) > 0 or len(p.get_images()) > 0)
    doc_fitz.close()
    total_pages += billable_pages

    print(f"[{idx}/14] Processing: {fname} ({billable_pages} billable pages)...")
    start_t = time.time()

    spec_data = parse_spec_pdf_textract(
        str(pdf_path),
        confidence_threshold=90.0,
        region="ap-south-1",
        client=client,
        skip_blank_pages=True,
        cache_dir=str(CACHE_DIR),
    )
    elapsed = time.time() - start_t

    # Save to staging JSON
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

    # Check for suspicious parameters
    suspicious = 0
    for r in reqs:
        p = (r.get("parameter") or "").strip()
        if any(p.startswith(k) for k in ("IS:", "IS :", "JSS", "8.", "9.", "12", "15", "3.70", "101")):
            suspicious += 1

    print(f"    ✓ Done in {elapsed:.2f}s | {len(vars_)} vars | {len(reqs)} reqs | {clean_count} clean | {low_struct_count} low-structure | {low_conf_count} low-conf | {null_val_count} nulls | {suspicious} suspicious")

    results.append({
        "filename": fname,
        "billable_pages": billable_pages,
        "elapsed_sec": round(elapsed, 2),
        "variants": len(vars_),
        "total_requirements": len(reqs),
        "clean_requirements": clean_count,
        "low_structure_confidence": low_struct_count,
        "low_confidence": low_conf_count,
        "null_values": null_val_count,
        "suspicious_count": suspicious,
    })

# Update manifest
if MANIFEST_FILE.exists():
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f_m:
        manifest_data = json.load(f_m)
    for res in results:
        fname = res["filename"]
        if fname in manifest_data.get("processed", {}):
            manifest_data["processed"][fname]["total_requirements"] = res["total_requirements"]
            manifest_data["processed"][fname]["clean_requirements"] = res["clean_requirements"]
            manifest_data["processed"][fname]["low_structure_confidence"] = res["low_structure_confidence"]
            manifest_data["processed"][fname]["status"] = "REPROCESSED_CLEAN" if res["clean_requirements"] > 0 else "MANUAL_REVIEW"
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f_m:
        json.dump(manifest_data, f_m, indent=2)
    print("\nUpdated batch2_manifest.json with fresh metrics.")

print("\n" + "=" * 80)
print(f"REPROCESSING COMPLETE: 14/14 documents processed.")
print(f"Total Billable Pages: {total_pages}")
print(f"Estimated Cost: ${total_pages * 0.015:.3f}")
print("=" * 80)
