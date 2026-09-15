import json
from pathlib import Path

BATCH1_DIR = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import")
STAGING_DIR = BATCH1_DIR / "parsed_staging"
SNAPSHOT_FILE = Path(r"C:\Users\ASUS\.gemini\antigravity\brain\3f336c5d-452a-4710-b9e6-19531da60d24\scratch\before_snapshot_55.json")

pdf_files = sorted([f.name for f in BATCH1_DIR.glob("*.pdf")])
print(f"Total PDFs in Batch 1: {len(pdf_files)}")

snapshot = {}
tot_reqs = 0
tot_clean = 0
tot_low_conf = 0
tot_nulls = 0
tot_suspicious = 0

for fname in pdf_files:
    stem = Path(fname).stem
    jf = STAGING_DIR / f"{stem}.json"
    if not jf.exists():
        print(f"  Warning: {jf.name} not found")
        continue
    with open(jf, "r", encoding="utf-8") as f:
        d = json.load(f)
    reqs = d.get("requirements", [])
    vars_ = d.get("variants", [])

    clean_c = sum(1 for r in reqs if r.get("confidence_flag") is None and (r.get("spec_value") is not None or r.get("text_value") is not None))
    lc_c = sum(1 for r in reqs if r.get("confidence_flag") is not None)
    null_c = sum(1 for r in reqs if r.get("spec_value") is None and r.get("text_value") is None)

    susp = 0
    for r in reqs:
        if r.get("confidence_flag") is None:
            p = (r.get("parameter") or "").strip()
            if any(p.startswith(k) for k in ("IS:", "IS :", "JSS", "8.", "9.", "12", "15", "3.70", "101", "(a)", "(b)", "(c)")):
                susp += 1

    snapshot[fname] = {
        "variants": len(vars_),
        "requirements": len(reqs),
        "clean": clean_c,
        "low_conf": lc_c,
        "nulls": null_c,
        "suspicious": susp,
    }
    tot_reqs += len(reqs)
    tot_clean += clean_c
    tot_low_conf += lc_c
    tot_nulls += null_c
    tot_suspicious += susp

with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
    json.dump(snapshot, f, indent=2)

print(f"Saved BEFORE snapshot for {len(snapshot)} documents:")
print(f"  Total Requirements: {tot_reqs}")
print(f"  Total Clean:        {tot_clean}")
print(f"  Total Low Conf:     {tot_low_conf}")
print(f"  Total Nulls:        {tot_nulls}")
print(f"  Total Suspicious:   {tot_suspicious}")
