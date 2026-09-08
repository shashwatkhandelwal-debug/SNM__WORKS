#!/usr/bin/env python3
"""
scripts/audit_staging_jsons.py

Systematic diagnostic audit across all staged JSONs in bulk_spec_import/parsed_staging/
for the auto-extract document set.

Checks per document:
1. Requirement count (flag count == 0)
2. Populated vs null rate (% of requirements with non-null value)
3. Row-number leakage (parameters that are pure digits or 'si no' / 'sl no')
4. Header metadata sanity (stamps/garbage like 'controlled', 'certified', 'confidential', 'reviewed', short/numeric)
5. Wrong-table signal (parameters mentioning sampling/AQL terms: 'acceptance no', 'sample size', 'lot size')
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List

STAGING_DIR = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import\parsed_staging")
MANUAL_CSV = STAGING_DIR / "manual_review_needed.csv"

# Load manual review files so we exclude them from the 45 auto-extract set
manual_files = set()
if MANUAL_CSV.exists():
    import csv
    with open(MANUAL_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fn = row.get("Filename", "")
            manual_files.add(Path(fn).stem)

# Known 10 manual/excluded stems just in case:
KNOWN_MANUAL_STEMS = {
    "ADRDE 6 ITEMS SPEC",
    "ASSISTANCE OF BAG KIT UNIVERSAL",
    "ASSISTANCE OF RUCKSACK 90",
    "BAG KIT UNIVERSAL MK2",
    "CORD 2450N UD",
    "CORD LOCK FOR TAPE",
    "CyQ",
    "2000_55",
    "assistance of web belt bdu",
    "assistance of web belt deep blue",
}
manual_files.update(KNOWN_MANUAL_STEMS)


def is_stamp_or_garbage(text: str | None) -> bool:
    if not text:
        return True
    t = text.strip().lower()
    if len(t) <= 2:
        return True
    if any(w in t for w in ("controlled", "certified", "confidential", "reviewed")):
        return True
    return False


def is_wrong_table_param(param: str) -> bool:
    p = param.strip().lower()
    return any(k in p for k in ("acceptance no", "sample size", "lot size", "lots size", "aql"))


def is_row_number_leak(param: str) -> bool:
    p = param.strip().lower()
    if re.fullmatch(r"^\d{1,3}\.?$", p):
        return True
    if re.fullmatch(r"^(?:s[\.li1]\.?\s*no\.?|sr\.?\s*no\.?|sl\.?\s*no\.?|no\.?)$", p):
        return True
    return False


def run_audit():
    json_files = sorted(STAGING_DIR.glob("*.json"))
    
    records = []
    
    for jf in json_files:
        if "manifest" in jf.name.lower():
            continue
        stem = jf.stem
        if stem in manual_files:
            continue
            
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            records.append({
                "filename": jf.name,
                "req_count": 0,
                "pct_populated": 0.0,
                "flags": [f"READ_ERROR: {e}"]
            })
            continue

        spec = data.get("specification", {})
        reqs = data.get("requirements", [])
        
        req_count = len(reqs)
        populated_count = 0
        row_leak_found = []
        wrong_table_found = []
        
        for r in reqs:
            l_type = r.get("limit_type", "nominal")
            val = r.get("spec_value") if l_type in ("nominal", "range", "minimum", "maximum") else r.get("text_value")
            if val is not None or r.get("upper_limit") is not None or r.get("text_value") is not None:
                populated_count += 1
                
            param = r.get("parameter", "")
            if is_row_number_leak(param):
                row_leak_found.append(param)
            if is_wrong_table_param(param):
                wrong_table_found.append(param)
                
        pct_pop = (populated_count / req_count * 100.0) if req_count > 0 else 0.0
        
        flags = []
        if req_count == 0:
            flags.append("ZERO_REQUIREMENTS")
        elif pct_pop == 0.0:
            flags.append("100%_NULL_VALUES")
            
        if row_leak_found:
            sample = list(dict.fromkeys(row_leak_found))[:3]
            flags.append(f"ROW_NUM_LEAK({', '.join(sample)})")
            
        if wrong_table_found:
            sample = list(dict.fromkeys(wrong_table_found))[:2]
            flags.append(f"WRONG_TABLE({', '.join(sample)})")
            
        # Metadata checks
        spec_no = spec.get("spec_no")
        title = spec.get("title")
        rev = spec.get("revision")
        
        meta_issues = []
        if is_stamp_or_garbage(spec_no):
            meta_issues.append(f"spec_no='{spec_no}'")
        if is_stamp_or_garbage(title):
            meta_issues.append(f"title='{title}'")
        if rev and rev.strip().lower() in ("reviewed", "iewed"):
            meta_issues.append(f"revision='{rev}'")
            
        if meta_issues:
            flags.append(f"META_SUSPECT({', '.join(meta_issues)})")
            
        records.append({
            "filename": jf.name,
            "req_count": req_count,
            "pct_populated": pct_pop,
            "flags": flags
        })
        
    return records


if __name__ == "__main__":
    recs = run_audit()
    print(f"Total files audited: {len(recs)}")
    print(f"| # | Filename | Req Count | % Populated | Flags |")
    print(f"|---|---|---|---|---|")
    for idx, r in enumerate(recs, 1):
        f_str = ", ".join(r["flags"]) if r["flags"] else "None (Clean)"
        print(f"| {idx} | `{r['filename']}` | {r['req_count']} | {r['pct_populated']:.1f}% | {f_str} |")

