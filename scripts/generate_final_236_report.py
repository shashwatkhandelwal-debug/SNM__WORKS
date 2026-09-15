import json
from pathlib import Path

BASE_DIR = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import_batch2")
STAGING_DIR = BASE_DIR / "parsed_staging"
PDF_DIR = BASE_DIR / "pdfs"
MANIFEST_FILE = BASE_DIR / "batch2_manifest.json"
BEFORE_SNAPSHOT = Path(r"C:\Users\ASUS\.gemini\antigravity\brain\3f336c5d-452a-4710-b9e6-19531da60d24\scratch\before_snapshot_236.json")
FINAL_REPORT_FILE = Path(r"C:\Users\ASUS\.gemini\antigravity\brain\3f336c5d-452a-4710-b9e6-19531da60d24\scratch\batch2_final_236_report.json")

with open(BEFORE_SNAPSHOT, "r", encoding="utf-8") as f:
    before = json.load(f)

with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
    m = json.load(f)

run_meta = m.get("batch_236_run", {})

# Calculate after totals across the 236 files
tot_reqs = 0
tot_clean = 0
tot_low_struct = 0
tot_low_conf = 0
tot_nulls = 0
tot_suspicious = 0

per_file_stats = []

for fname in before.keys():
    stem = Path(fname).stem
    jf = STAGING_DIR / f"{stem}.json"
    if not jf.exists():
        continue
    with open(jf, "r", encoding="utf-8") as f:
        d = json.load(f)
    reqs = d.get("requirements", [])
    vars_ = d.get("variants", [])
    clean_c = sum(1 for r in reqs if r.get("confidence_flag") is None)
    ls_c = sum(1 for r in reqs if r.get("confidence_flag") and "LOW_STRUCTURE_CONFIDENCE" in r.get("confidence_flag"))
    lc_c = sum(1 for r in reqs if r.get("confidence_flag") and "LOW_CONFIDENCE" in r.get("confidence_flag"))
    null_c = sum(1 for r in reqs if r.get("spec_value") is None and r.get("text_value") is None)
    
    susp = 0
    for r in reqs:
        if r.get("confidence_flag") is None:
            p = (r.get("parameter") or "").strip()
            if any(p.startswith(k) for k in ("IS:", "IS :", "JSS", "8.", "9.", "12", "15", "3.70", "101", "(a)", "(b)", "(c)")):
                susp += 1

    tot_reqs += len(reqs)
    tot_clean += clean_c
    tot_low_struct += ls_c
    tot_low_conf += lc_c
    tot_nulls += null_c
    tot_suspicious += susp

    per_file_stats.append({
        "filename": fname,
        "variants": len(vars_),
        "requirements": len(reqs),
        "clean": clean_c,
        "low_structure": ls_c,
        "low_confidence": lc_c,
        "nulls": null_c,
        "suspicious": susp,
    })

before_reqs = sum(v["requirements"] for v in before.values())
before_clean = sum(v["clean"] for v in before.values())
before_low_conf = sum(v["low_conf"] for v in before.values())
before_nulls = sum(v["nulls"] for v in before.values())
before_susp = sum(v["suspicious"] for v in before.values())

report = {
    "run_metadata": run_meta,
    "before_totals": {
        "requirements": before_reqs,
        "clean_requirements": before_clean,
        "low_confidence": before_low_conf,
        "null_values": before_nulls,
        "suspicious_parameters": before_susp,
    },
    "after_totals": {
        "requirements": tot_reqs,
        "clean_requirements": tot_clean,
        "low_structure_confidence_rescued": tot_low_struct,
        "total_values_populated": tot_clean + tot_low_struct,
        "low_confidence": tot_low_conf,
        "null_values": tot_nulls,
        "suspicious_parameters": tot_suspicious,
    },
    "per_file_stats": per_file_stats,
}

with open(FINAL_REPORT_FILE, "w", encoding="utf-8") as f_out:
    json.dump(report, f_out, indent=2)

print("=== FINAL AUDIT REPORT: BATCH 2 REMAINING 236 DOCUMENTS ===")
print("Status:               ", run_meta.get("status"))
print("Documents Target:     ", run_meta.get("target_count"))
print("Documents Success:    ", run_meta.get("successful_count"))
print("Documents Failed:     ", run_meta.get("failed_count"))
print("Billable Pages:       ", run_meta.get("billable_pages"))
print("Incurred Cost:        $", f"{run_meta.get('incurred_cost_usd', 0.0):.3f}")
print("Total Elapsed Time:   ", f"{run_meta.get('total_elapsed_sec', 0.0) / 60:.1f} minutes")

print("\n=== BEFORE vs AFTER COMPARISON (236 DOCUMENTS) ===")
print(f"Total Requirements Extracted:     {before_reqs} -> {tot_reqs}")
print(f"Clean High-Confidence Reqs:       {before_clean} -> {tot_clean}")
print(f"LOW_STRUCTURE_CONFIDENCE Rescued: 0 -> {tot_low_struct}")
print(f"Populated Requirement Values:     {before_clean} -> {tot_clean + tot_low_struct} (+{tot_clean + tot_low_struct - before_clean} net values populated)")
print(f"LOW_CONFIDENCE Cells:             {before_low_conf} -> {tot_low_conf} (-{before_low_conf - tot_low_conf})")
print(f"Null Spec/Text Values:            {before_nulls} -> {tot_nulls} (-{before_nulls - tot_nulls})")
print(f"Suspicious Non-Parameter Rows:    {before_susp} -> {tot_suspicious} (-{before_susp - tot_suspicious} removed)")
