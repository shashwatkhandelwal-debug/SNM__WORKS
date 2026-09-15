import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
import asyncpg
import dotenv

dotenv.load_dotenv(r"C:\Users\ASUS\Downloads\SNM_WORKS\.env")

PROJECT_ROOT = Path(r"C:\Users\ASUS\Downloads\SNM_WORKS")
UPLOAD_BASE_DIR = PROJECT_ROOT / "static" / "uploads"
SCRATCH_DIR = Path(r"C:\Users\ASUS\.gemini\antigravity\brain\a8b42266-8761-4326-a6b5-788dd2c3d8b9\scratch")

CATEGORY_CONFIG = [
    ("Canvas (Cotton & Flax)", ["canvas"]),
    ("Fabrics, Drills & Ducks", ["drill", "duck", "calico", "ripstop", "rip", "plain weave", "blended fabric", "fabric", "cloth", "interlining", "sheeting", "cellular", "foulard", "flannel", "gabardine", "dosuti"]),
    ("Webbing & Belting", ["belt", "web belt", "webbing", "web"]),
    ("Tapes & Narrow Wovens", ["tape", "niwar", "newar", "ribbon", "binding", "slings"]),
    ("Cordage & Braids (Nylon & Synthetic)", ["cord nylon", "braided cord", "cord 1800", "cord 200", "cord 250", "cord 68", "cord 2940", "cord 3120", "cord 3600", "cord 12740", "cord", "rope", "braid", "twine", "line"]),
    ("Cordage (Cotton, Flax & Elastic)", ["cotton braided", "cord flax", "cord 1785 black", "elastic"]),
    ("Defence Standards (IND/TC & DMSRDE)", ["1981_6", "2000_55", "2011_3", "2018_6", "2019_2", "2020_02", "2021_2", "78(a)", "94(a)", "dmsrde", "ind/tc", "jss", "is:"]),
    ("Sewing Thread", ["thread", "aramid"]),
    ("Omnibus Standards & End-Item Manuals", ["cord 1785 white", "cord 2450n", "cord lock", "adrde 6 items", "bag kit", "assistance of", "cyq", "rucksack", "haversack", "assembly"]),
]

def assign_category(filename: str) -> str:
    lower = filename.lower()
    for cat_name, keywords in CATEGORY_CONFIG:
        for kw in keywords:
            if kw in lower:
                return cat_name
    return "Omnibus Standards & End-Item Manuals"

async def run_audit():
    db_url = os.environ.get("LOCAL_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/snm_test_db")
    conn = await asyncpg.connect(db_url)
    
    # Query all 305 bulk_textract_batch uploads
    rows = await conn.fetch("""
        SELECT id, original_filename, parsed_json_path, parsed_json_sha256, status, source
        FROM spec_pdf_uploads
        WHERE source = 'bulk_textract_batch'
        ORDER BY original_filename ASC;
    """)
    
    print(f"Loaded {len(rows)} documents from spec_pdf_uploads (source='bulk_textract_batch').")
    
    audit_results: List[Dict[str, Any]] = []
    
    total_docs = len(rows)
    total_reqs = 0
    total_clean = 0
    total_ls = 0
    total_nulls = 0
    flagged_docs = []
    
    for r in rows:
        upload_id = str(r["id"])
        fn = r["original_filename"]
        json_rel = r["parsed_json_path"] or ""
        json_path = UPLOAD_BASE_DIR / json_rel.lstrip("/\\")
        
        has_file = json_path.exists()
        spec_data = {}
        if has_file:
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    spec_data = json.load(f)
            except Exception as e:
                spec_data = {"error": str(e)}
                
        header = spec_data.get("specification", {})
        spec_no = (header.get("spec_no") or "").strip()
        title = (header.get("title") or "").strip()
        issuing_body = (header.get("issuing_body") or "").strip()
        header_non_empty = bool(spec_no or title)
        
        reqs = spec_data.get("requirements", [])
        req_count = len(reqs)
        
        clean_c = sum(1 for req in reqs if req.get("confidence_flag") is None)
        ls_c = sum(1 for req in reqs if req.get("confidence_flag") and "LOW_STRUCTURE_CONFIDENCE" in req.get("confidence_flag"))
        null_c = sum(1 for req in reqs if req.get("spec_value") is None and req.get("text_value") is None)
        
        # Clean + Low-structure-confidence populated values
        populated_c = clean_c + ls_c
        clean_ls_ratio = (populated_c / req_count) if req_count > 0 else 0.0
        clean_ls_pct = clean_ls_ratio * 100.0
        
        # Condition: Flag anything under 20% clean+low-structure-confidence as needing manual attention
        # Also flag if header is empty or requirements == 0
        needs_manual = (req_count == 0) or (clean_ls_pct < 20.0)
        
        flag_reasons = []
        if not header_non_empty:
            flag_reasons.append("EMPTY_HEADER")
        if req_count == 0:
            flag_reasons.append("ZERO_REQUIREMENTS")
        elif clean_ls_pct < 20.0:
            flag_reasons.append(f"LOW_STRUCTURE_POPULATION_{clean_ls_pct:.1f}%")
            
        category = assign_category(fn)
        
        item_audit = {
            "upload_id": upload_id,
            "filename": fn,
            "category": category,
            "header_non_empty": header_non_empty,
            "spec_no": spec_no or "—",
            "title": title or "—",
            "issuing_body": issuing_body or "—",
            "req_count": req_count,
            "clean_count": clean_c,
            "low_structure_count": ls_c,
            "populated_count": populated_c,
            "null_count": null_c,
            "clean_ls_pct": round(clean_ls_pct, 1),
            "needs_manual": needs_manual,
            "flag_reasons": flag_reasons,
            "review_url": f"/skus/standalone/spec-review/{upload_id}",
            "pdf_url": f"/skus/standalone/spec-uploads/{upload_id}/pdf",
        }
        
        total_reqs += req_count
        total_clean += clean_c
        total_ls += ls_c
        total_nulls += null_c
        
        if needs_manual:
            flagged_docs.append(item_audit)
            
        audit_results.append(item_audit)
        
    await conn.close()
    
    # Save full audit JSON
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    audit_json_path = SCRATCH_DIR / "structural_audit_305.json"
    with open(audit_json_path, "w", encoding="utf-8") as f:
        json.dump({
            "total_documents": total_docs,
            "total_requirements": total_reqs,
            "total_clean_requirements": total_clean,
            "total_low_structure_confidence": total_ls,
            "total_populated": total_clean + total_ls,
            "total_null_values": total_nulls,
            "total_flagged_for_manual": len(flagged_docs),
            "flagged_percentage": round(len(flagged_docs) / total_docs * 100, 1),
            "documents": audit_results,
        }, f, indent=2)
        
    print(f"Saved JSON audit to {audit_json_path}")
    
    # Generate combined review_queue.html
    html_content = generate_combined_queue_html(audit_results)
    
    dest_paths = [
        PROJECT_ROOT / "templates" / "review_queue.html",
        Path(r"C:\Users\ASUS\Downloads\bulk_spec_import\parsed_staging\review_queue.html"),
        Path(r"C:\Users\ASUS\Downloads\bulk_spec_import_batch2\parsed_staging\review_queue.html"),
    ]
    for dp in dest_paths:
        dp.parent.mkdir(parents=True, exist_ok=True)
        dp.write_text(html_content, encoding="utf-8")
        print(f"Wrote updated review queue to {dp}")
        
    return audit_results, flagged_docs

def generate_combined_queue_html(items: List[Dict[str, Any]]) -> str:
    # Sort by Category then filename
    sorted_items = sorted(items, key=lambda x: (x["category"], x["filename"]))
    
    rows_html = ""
    curr_cat = None
    
    for it in sorted_items:
        if it["category"] != curr_cat:
            curr_cat = it["category"]
            cat_count = sum(1 for x in sorted_items if x["category"] == curr_cat)
            cat_reqs = sum(x["req_count"] for x in sorted_items if x["category"] == curr_cat)
            rows_html += f"""
            <tr style="background: #E9E5DA; font-weight: 700;">
                <td colspan="7" style="padding: 10px 12px; color: #1B2017; font-size: 0.95rem;">
                    {curr_cat} <span style="font-weight: 400; font-size: 0.8rem; color: #555;">({cat_count} specs, {cat_reqs} reqs)</span>
                </td>
            </tr>
            """
            
        if it["needs_manual"]:
            tag_badge = f'<span style="background: #A82914; color: #fff; padding: 2px 6px; border-radius: 3px; font-size: 0.75rem; font-family: monospace; font-weight: 700;">MANUAL</span>'
            row_bg = "#FFF9F8"
        else:
            tag_badge = f'<span style="background: #3F6B34; color: #fff; padding: 2px 6px; border-radius: 3px; font-size: 0.75rem; font-family: monospace; font-weight: 700;">AUTO ({it["clean_ls_pct"]}%)</span>'
            row_bg = "#FFFFFF"
            
        reasons_badge = ""
        if it["flag_reasons"]:
            reasons_badge = f'<br/><span style="color: #A82914; font-size: 0.75rem; font-family: monospace;">{" ".join(it["flag_reasons"])}</span>'
            
        rows_html += f"""
        <tr style="background: {row_bg}; border-bottom: 1px solid #CFC8B6;">
            <td style="padding: 8px 10px;">{tag_badge}</td>
            <td style="padding: 8px 10px; font-weight: 600;">{it['filename']}{reasons_badge}</td>
            <td style="padding: 8px 10px; font-family: monospace; font-size: 0.8rem;">{it['spec_no']}</td>
            <td style="padding: 8px 10px; font-size: 0.82rem;">{it['title']}</td>
            <td style="padding: 8px 10px; text-align: right; font-family: monospace; font-weight: 600;">{it['req_count']}</td>
            <td style="padding: 8px 10px; text-align: right; font-family: monospace; font-size: 0.8rem;">{it['clean_count']}+{it['low_structure_count']} / {it['null_count']}</td>
            <td style="padding: 8px 10px; text-align: right;">
                <a href="{it['review_url']}" style="display: inline-block; background: #474B2F; color: #fff; padding: 4px 10px; border-radius: 3px; text-decoration: none; font-size: 0.8rem; font-weight: 600;">Review & Load →</a>
            </td>
        </tr>
        """

    total_specs = len(items)
    total_reqs = sum(it["req_count"] for it in items)
    manual_count = sum(1 for it in items if it["needs_manual"])
    auto_count = total_specs - manual_count

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Specification Review Queue — Swadeshi Niwar Mills</title>
    <style>
        body {{ font-family: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #F6F4EE; color: #1B2017; margin: 2rem; }}
        h1 {{ font-family: 'Barlow Condensed', sans-serif; letter-spacing: 0.04em; margin-bottom: 0.25rem; font-size: 2.2rem; }}
        .stats-bar {{ display: flex; gap: 1rem; margin: 1rem 0; }}
        .stat-card {{ background: #fff; border: 1px solid #CFC8B6; padding: 12px 18px; border-radius: 6px; }}
        .stat-val {{ font-size: 1.5rem; font-weight: 700; font-family: monospace; color: #1B2017; }}
        .stat-lbl {{ font-size: 0.75rem; color: #666; text-transform: uppercase; letter-spacing: 0.05em; }}
        table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #CFC8B6; margin-top: 1rem; font-size: 0.85rem; }}
        th {{ background: #1B2017; color: #fff; text-align: left; padding: 10px 12px; font-size: 0.8rem; letter-spacing: 0.05em; }}
    </style>
</head>
<body>
    <h1>SPECIFICATION REVIEW QUEUE ({total_specs} DOCUMENTS)</h1>
    <p style="color: #666; font-size: 0.9rem; margin-top: 0;">
        Combined Batches 1 & 2 • Technical textile families • Split-screen verification against source military and industrial standards.
    </p>

    <div class="stats-bar">
        <div class="stat-card">
            <div class="stat-val">{total_specs}</div>
            <div class="stat-lbl">Total Documents</div>
        </div>
        <div class="stat-card">
            <div class="stat-val" style="color: #3F6B34;">{auto_count}</div>
            <div class="stat-lbl">High-Structure Ready</div>
        </div>
        <div class="stat-card">
            <div class="stat-val" style="color: #A82914;">{manual_count}</div>
            <div class="stat-lbl">Manual Attention (<20%)</div>
        </div>
        <div class="stat-card">
            <div class="stat-val">{total_reqs}</div>
            <div class="stat-lbl">Extracted Reqs</div>
        </div>
    </div>

    <table>
        <thead>
            <tr>
                <th style="width: 100px;">Mode</th>
                <th>Source Document</th>
                <th style="width: 160px;">Spec Number</th>
                <th>Extracted / Document Title</th>
                <th style="width: 70px; text-align: right;">Reqs</th>
                <th style="width: 110px; text-align: right;">Clean+LS / Null</th>
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

if __name__ == "__main__":
    asyncio.run(run_audit())
