#!/usr/bin/env python3
"""
scripts/bulk_textract_import.py — Bulk Technical Specification Ingestion Orchestrator.

Orchestrates batch extraction for technical specification PDFs in bulk_spec_import/
using AWS Textract AnalyzeDocument (TABLES).

Features:
- Structural mismatch pre-filtering: skips non-textiles, multi-item bundles, and assembly BOMs
- Cost tracking: computes actual page counts and running estimated AWS charges ($0.015/page)
- Family grouping: organizes outputs by military and commercial technical textile families
- Dry-run mode: generates complete preview manifest without making any AWS API calls
- Outputs standard staging JSON consumed by scripts/load_spec.py
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pypdfium2 as pdfium
from services.spec_parser_textract import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    SUPPORTED_TEXTRACT_REGIONS,
    get_textract_client,
    parse_spec_pdf_textract,
)

DEFAULT_INPUT_DIR = r"C:\Users\ASUS\Downloads\bulk_spec_import"
COST_PER_PAGE_USD = 0.015
USD_TO_INR_RATE = 84.0


def classify_document_family(filename: str) -> str:
    """Classifies specification into logical technical textile families."""
    lower = filename.lower()
    if any(lower.startswith(y) for y in ["19", "20"]) and "_" in filename:
        return "DEFENCE IND/TC & DMSRDE (Year_Serial)"
    if lower.startswith("78(") or lower.startswith("94("):
        return "DEFENCE IND/TC (Numbered Specs)"
    if "adrde" in lower:
        return "AERIAL DELIVERY R&D ESTABLISHMENT (ADRDE)"
    if "canvas" in lower:
        return "CANVAS (Cotton & Flax)"
    if any(k in lower for k in ("belt", "web belt")):
        return "WEBBING & BELTING"
    if any(k in lower for k in ("assistance", "rucksack", "bag kit")):
        return "EQUIPMENT & ASSEMBLY MANUALS (BOMs)"
    if any(k in lower for k in ("cloth", "drill", "duck", "calico", "ripstop", "fabric")):
        return "FABRICS, DRILLS & DUCKS"
    if any(k in lower for k in ("cord", "braided", "elastic", "nbl")):
        return "CORDAGE, BRAIDS & ELASTIC LOOPS"
    if "thread" in lower:
        return "SEWING THREAD"
    return "OTHER / UNCLASSIFIED"


def evaluate_structural_mismatch(filename: str, page_count: int, max_pages: int = 20) -> Tuple[bool, str, str]:
    """
    Evaluates whether a document matches the tabular specification schema.
    Returns: (is_mismatch, action_recommendation, reason)
    """
    lower = filename.lower()

    # 1. Unrelated non-textile file
    if "cyq" in lower:
        return True, "EXCLUDE", "Non-textile / unrelated college administrative document"

    # 2. Hardware / plastic injection component
    if "cord lock" in lower:
        return True, "MANUAL_ENTRY", "Non-textile polymer granule component (no yarn/weave properties)"

    # 3. Multi-item omnibus tender / standard
    if "adrde 6 items" in lower:
        return True, "MANUAL_ENTRY", "Multi-item tender bundle (6 distinct items in single PDF)"
    if "cord 1785 white" in lower:
        return True, "MANUAL_ENTRY", "Multi-item omnibus military standard (JSS 4020-09 covers 4 distinct cord products: 440N, 1785N, 3120N, 5335N)"

    # 4. Assembly & Manufacturing manuals (BOM / cutting schedules / stitch patterns)
    if any(k in lower for k in ("assistance", "rucksack", "bag kit")):
        return True, "MANUAL_ENTRY", "End-item fabrication manual (cutting schedules, BOM, hardware)"

    # 5. Page count ceiling (intentional trade-off)
    if page_count > max_pages:
        return True, "MANUAL_ENTRY", f"Exceeds {max_pages}-page threshold ({page_count} pages; complex standard/manual)"

    return False, "AUTO_EXTRACT", "Schema-aligned technical specification"


def scan_batch_inventory(input_dir: str, max_pages: int = 20) -> List[Dict[str, Any]]:
    """Inspects all PDFs in the input directory and prepares extraction plan."""
    p_dir = Path(input_dir)
    if not p_dir.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    files = sorted([f for f in os.listdir(p_dir) if f.lower().endswith(".pdf")])
    inventory: List[Dict[str, Any]] = []

    for fname in files:
        fpath = p_dir / fname
        size_kb = round(os.path.getsize(fpath) / 1024, 1)
        try:
            doc = pdfium.PdfDocument(str(fpath))
            pages = len(doc)
        except Exception:
            pages = 0

        family = classify_document_family(fname)
        is_mismatch, action, reason = evaluate_structural_mismatch(fname, pages, max_pages=max_pages)

        inventory.append({
            "filename": fname,
            "filepath": str(fpath),
            "pages": pages,
            "size_kb": size_kb,
            "family": family,
            "is_mismatch": is_mismatch,
            "action": action,
            "reason": reason,
        })

    return inventory


def run_bulk_pipeline(
    input_dir: str,
    output_dir: str,
    dry_run: bool = False,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    max_pages: int = 20,
    region: str = "ap-south-1",
    only_mismatches: bool = False,
    include_all: bool = False,
    target_filenames: Optional[List[str]] = None,
):
    """Executes the batch import pipeline with cost tracking and staging."""
    print("=" * 80)
    print("  SNM Works — Bulk Technical Specification Ingestion Pipeline (AWS Textract)")
    print(f"  Source Directory:        {input_dir}")
    print(f"  Staging Output:          {output_dir}")
    print(f"  Execution Mode:          {'DRY RUN (Zero AWS calls)' if dry_run else 'LIVE INGESTION'}")
    print(f"  Target AWS Region:       {region}")
    print(f"  Confidence Threshold:    {confidence_threshold}%")
    print("=" * 80)

    # Pre-flight region check
    if region not in SUPPORTED_TEXTRACT_REGIONS:
        print(f"\n[ERROR] AWS region '{region}' does not support Textract.", file=sys.stderr)
        print(f"Supported regions: {', '.join(sorted(SUPPORTED_TEXTRACT_REGIONS))}", file=sys.stderr)
        sys.exit(1)

    # Scan inventory
    inventory = scan_batch_inventory(input_dir, max_pages=max_pages)
    total_docs = len(inventory)
    total_pages = sum(item["pages"] for item in inventory)

    auto_docs = [it for it in inventory if not it["is_mismatch"]]
    manual_docs = [it for it in inventory if it["is_mismatch"]]

    if target_filenames:
        clean_targets = {os.path.basename(t).lower() for t in target_filenames}
        target_docs = [it for it in inventory if it["filename"].lower() in clean_targets]
        mode_desc = f"SELECTIVE SUBSET ({len(target_docs)} docs)"
    elif only_mismatches:
        target_docs = manual_docs
        mode_desc = "ONLY MISMATCHED / PRE-FILTERED FILES (10 docs)"
    elif include_all:
        target_docs = inventory
        mode_desc = "ALL FILES (including pre-filtered)"
    else:
        target_docs = auto_docs
        mode_desc = "AUTO-EXTRACT CANDIDATES ONLY"

    billable_pages = sum(it["pages"] for it in target_docs)
    skipped_pages = sum(it["pages"] for it in inventory if it not in target_docs)
    est_cost_usd = billable_pages * COST_PER_PAGE_USD
    est_cost_inr = est_cost_usd * USD_TO_INR_RATE

    unfiltered_cost_usd = total_pages * COST_PER_PAGE_USD

    print(f"\nDiscovered {total_docs} specification documents ({total_pages} total pages):")
    print(f"  - Target Documents for Pass:   {len(target_docs):>2} documents ({billable_pages:>3} billable pages)")
    print(f"  - Target Run Scope:            {mode_desc}")
    print(f"\nEstimated AWS Textract Charge:")
    print(f"  - This Pass Estimated Cost:    ${est_cost_usd:.2f} USD (~Rs. {est_cost_inr:.1f} INR)")

    # Print breakdown by family
    print("\n" + "-" * 80)
    print("  BATCH BREAKDOWN BY SPECIFICATION FAMILY")
    print("-" * 80)
    family_map: Dict[str, List[Dict[str, Any]]] = {}
    for it in inventory:
        family_map.setdefault(it["family"], []).append(it)

    for fam, doc_list in sorted(family_map.items()):
        f_pages = sum(d["pages"] for d in doc_list)
        f_auto = sum(1 for d in doc_list if not d["is_mismatch"])
        f_man = sum(1 for d in doc_list if d["is_mismatch"])
        print(f"\n[{fam}] — {len(doc_list)} docs ({f_pages} pages) | Auto: {f_auto}, Manual: {f_man}")
        for d in doc_list:
            flag_str = f"[{d['action']}]" if d["is_mismatch"] else "[AUTO]"
            reason_str = f" — {d['reason']}" if d["is_mismatch"] else ""
            print(f"   {flag_str:<15} {d['pages']:>2}p | {d['filename']}{reason_str}")

    # Output directory setup
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Write manual review CSV
    csv_path = out_path / "manual_review_needed.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f_csv:
        writer = csv.DictWriter(f_csv, fieldnames=["filename", "pages", "family", "action", "reason"])
        writer.writeheader()
        for d in manual_docs:
            writer.writerow({
                "filename": d["filename"],
                "pages": d["pages"],
                "family": d["family"],
                "action": d["action"],
                "reason": d["reason"],
            })
    print(f"\n[Artifact] Wrote manual review routing list: {csv_path}")

    # Manifest file
    manifest = {
        "summary": {
            "total_documents": total_docs,
            "total_pages": total_pages,
            "target_docs_count": len(target_docs),
            "billable_pages": billable_pages,
            "estimated_cost_usd": round(est_cost_usd, 2),
            "estimated_cost_inr": round(est_cost_inr, 1),
            "confidence_threshold": confidence_threshold,
            "aws_region": region,
            "mode": "dry-run" if dry_run else "live",
        },
        "documents": inventory,
    }

    manifest_filename = "import_manifest_preview.json" if dry_run else "import_manifest.json"
    manifest_path = out_path / manifest_filename
    with open(manifest_path, "w", encoding="utf-8") as f_mf:
        json.dump(manifest, f_mf, indent=2)
    print(f"[Artifact] Wrote batch manifest: {manifest_path}")

    if dry_run:
        print("\n" + "=" * 80)
        print("  DRY RUN COMPLETED: No AWS API calls were made.")
        print(f"  To proceed with live Textract ingestion, run without --dry-run.")
        print("=" * 80)
        return

    # Live Execution Pass
    print("\n" + "=" * 80)
    print("  COMMENCING LIVE AWS TEXTRACT INGESTION")
    print("=" * 80)
    client = get_textract_client(region=region)
    success_count = 0
    error_count = 0
    actual_spent_usd = 0.0

    for idx, item in enumerate(target_docs, 1):
        fn = item["filename"]
        fp = item["filepath"]
        pgs = item["pages"]
        doc_cost = pgs * COST_PER_PAGE_USD
        print(f"\n[{idx}/{len(target_docs)}] Processing: {fn} ({pgs} pages, est. ${doc_cost:.3f})...")

        try:
            spec_data = parse_spec_pdf_textract(
                fp,
                confidence_threshold=confidence_threshold,
                region=region,
                client=client,
            )
            stem = Path(fn).stem
            out_file = out_path / f"{stem}.json"
            with open(out_file, "w", encoding="utf-8") as f_out:
                json.dump(spec_data, f_out, indent=2)

            num_reqs = len(spec_data.get("requirements", []))
            num_vars = len(spec_data.get("variants", []))
            print(f"    ✓ Staged to {out_file.name} ({num_vars} variants, {num_reqs} requirements)")
            success_count += 1
            actual_spent_usd += doc_cost
        except Exception as exc:
            print(f"    ✗ FAILED to process {fn}: {exc}", file=sys.stderr)
            error_count += 1

    print("\n" + "=" * 80)
    print("  BATCH INGESTION COMPLETE")
    print(f"  Successfully Staged:       {success_count} specifications")
    print(f"  Failed:                    {error_count} specifications")
    print(f"  Actual Textract Charges:   ${actual_spent_usd:.2f} USD (~Rs. {actual_spent_usd * USD_TO_INR_RATE:.1f} INR)")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="Bulk technical specification ingestion using AWS Textract.")
    parser.add_argument("--input-dir", default=DEFAULT_INPUT_DIR, help="Directory containing PDF specifications.")
    parser.add_argument("--output-dir", default=None, help="Directory to save staging JSONs and manifests.")
    parser.add_argument("--dry-run", action="store_true", help="Preview cost and routing without calling AWS.")
    parser.add_argument("--confidence-threshold", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD, help="Confidence threshold (0-100). Below this, cell numeric is set to null.")
    parser.add_argument("--max-pages", type=int, default=20, help="Maximum pages for tabular spec. Above this, routed to manual.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "ap-south-1"), help="AWS region for Textract.")
    parser.add_argument("--only-mismatches", action="store_true", help="Process only the 10 files previously flagged by mismatch heuristics.")
    parser.add_argument("--include-all", action="store_true", help="Process all files in the directory.")
    parser.add_argument("--files", nargs="+", default=None, help="Specific filename(s) to process (relative to input-dir or basename).")

    args = parser.parse_args()
    out_dir = args.output_dir or os.path.join(args.input_dir, "parsed_staging")

    run_bulk_pipeline(
        input_dir=args.input_dir,
        output_dir=out_dir,
        dry_run=args.dry_run,
        confidence_threshold=args.confidence_threshold,
        max_pages=args.max_pages,
        region=args.region,
        only_mismatches=args.only_mismatches,
        include_all=args.include_all,
        target_filenames=args.files,
    )


if __name__ == "__main__":
    main()
