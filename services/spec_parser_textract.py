"""
services/spec_parser_textract.py — AWS Textract Technical Specification PDF Parser for SNM Works.

Alternate extraction engine for technical specification PDFs using AWS Textract AnalyzeDocument (TABLES).
Preserves the exact staging JSON schema expected by services.spec_loader and scripts.load_spec.

Guarantees:
- Pure Python rendering using pypdfium2 (zero external system binaries / no Poppler required)
- Strict cell-level confidence threshold gating (default 90.0%, tunable)
- Zero guessing rule: low-confidence (< threshold) numeric cells return null
- Preserves raw extracted text and low-confidence flags for human verification
- Pre-flight region verification against supported AWS Textract regions
- Exact schema compatibility with services.spec_loader.load_specification_document
"""

import io
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
import pypdfium2 as pdfium

load_dotenv()

logger = logging.getLogger("snm_works.spec_parser_textract")

SUPPORTED_TEXTRACT_REGIONS = {
    "ap-northeast-2",
    "ap-south-1",
    "ap-southeast-1",
    "ap-southeast-2",
    "ca-central-1",
    "eu-central-1",
    "eu-south-2",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
}

DEFAULT_CONFIDENCE_THRESHOLD = 90.0


def clean_cell_text(text: Optional[str]) -> str:
    """Cleans up raw cell text by stripping and collapsing whitespace."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def parse_numeric_with_tol(val_str: str) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
    """
    Parses numeric values with tolerances, ranges, or min/max prefixes/suffixes.
    Returns: (spec_value, tolerance, upper_limit, limit_type)
    """
    cleaned = clean_cell_text(val_str)
    if not cleaned or cleaned in ("-", "–", "—", "n/a", "N/A", "nil", "NIL"):
        return None, None, None, "nominal"

    c = re.sub(r"\.+$", "", cleaned).strip()

    # Guard against standards and specification citations masquerading as numbers or ranges
    # e.g., 'IS:764-79', 'IS: 1954', 'IS:2454', 'JSS:4020-9', '4472(PT.I)-1967', etc.
    if re.search(r"\b(?:IS|ISI|JSS|BS|DIN|ASTM|MIL|IND\/TC|DMSRDE)[\s:\-\.]*\d+", c, re.IGNORECASE):
        return None, None, None, "text"

    # 1. Minimum with prefix or suffix
    # e.g. 'min 4000', 'minimum 4000', '>= 4000'
    m_min_pre = re.match(r"^(?:min(?:imum)?|>=|≥)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:[A-Za-z\/\-\s]*)$", c, re.IGNORECASE)
    if m_min_pre:
        try:
            return float(m_min_pre.group(1)), None, None, "minimum"
        except ValueError:
            pass

    # e.g. '4000 (min)', '3600-N (MIN)', '250 Kg (Minimum)', '175m (Minimum)'
    m_min_suf = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*(?:[-–—A-Za-z\/\s]*?)\s*(?:\(\s*min(?:imum)?\s*\)|\bmin(?:imum)?\b)\.?$", c, re.IGNORECASE)
    if m_min_suf:
        try:
            return float(m_min_suf.group(1)), None, None, "minimum"
        except ValueError:
            pass

    # 2. Maximum with prefix or suffix
    # e.g. 'max 100', '<= 100'
    m_max_pre = re.match(r"^(?:max(?:imum)?|<=|≤)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:[A-Za-z\/\-\s]*)$", c, re.IGNORECASE)
    if m_max_pre:
        try:
            return float(m_max_pre.group(1)), None, None, "maximum"
        except ValueError:
            pass

    # e.g. '8.5 gms.(MAX)', '8.5 (max)'
    m_max_suf = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*(?:[-–—A-Za-z\/\s\.]*?)\s*(?:\(\s*max(?:imum)?\s*\)|\bmax(?:imum)?\b)\.?$", c, re.IGNORECASE)
    if m_max_suf:
        try:
            return float(m_max_suf.group(1)), None, None, "maximum"
        except ValueError:
            pass

    # 3. Range: '0.040 - 0.070', '0.040 to 0.070', '36/37'
    m_range = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*(?:[-–—to\/]|\s+to\s+)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:[A-Za-z\/\s]*)$", c, re.IGNORECASE)
    if m_range:
        try:
            low = float(m_range.group(1))
            high = float(m_range.group(2))
            # Guard: In valid physical ranges, low <= high. Citations like '764-79' have low > high.
            # Also, if high is a 2-digit year (e.g. '79', '72') and low is a standard number >= 100, reject as citation.
            if low <= high and not (len(m_range.group(2)) == 2 and low >= 100):
                return low, None, high, "range"
            else:
                return None, None, None, "text"
        except ValueError:
            pass

    # 4. Plus/minus tolerance: '1.75 +/- 0.0625', '1.75 ± 0.0625'
    m_tol = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*(?:\+\/\-|±|\+\-)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:[A-Za-z\/\s]*)$", c)
    if m_tol:
        try:
            return float(m_tol.group(1)), float(m_tol.group(2)), None, "nominal"
        except ValueError:
            pass

    # 5. Pure numeric (with optional trailing unit like '100 m', '840', '16')
    m_pure = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*(?:mm|cm|mtrs?|m|kgf?|kg|g(?:ms?)?|oz|in(?:ch(?:es)?)?|lbs?|%|denier)?\.?$", c, re.IGNORECASE)
    if m_pure:
        try:
            return float(m_pure.group(1)), None, None, "nominal"
        except ValueError:
            pass

    return None, None, None, "text"


def extract_header_metadata(full_text: str, filename_hint: str = "") -> Dict[str, Any]:
    """Extracts specification number, revision, and title from raw text and filename."""
    clean_fn = os.path.splitext(os.path.basename(filename_hint))[0].strip() if filename_hint else ""
    meta = {
        "spec_no": clean_fn or None,
        "revision": "R0",
        "title": None,
        "issuing_body": None,
        "notes": None,
    }

    # Detect issuing body
    upper_text = full_text.upper()
    if "DMSRDE" in upper_text:
        meta["issuing_body"] = "DMSRDE"
    elif "DGQA" in upper_text:
        meta["issuing_body"] = "DGQA"
    elif "ADRDE" in upper_text or "ADRDE" in filename_hint.upper():
        meta["issuing_body"] = "ADRDE"
    elif "JSS" in upper_text:
        meta["issuing_body"] = "DEFENCE (JSS)"
    elif "IAFS" in upper_text or "IAF" in upper_text:
        meta["issuing_body"] = "IAF (IAFS)"
    elif "IS:" in upper_text or "IS " in upper_text or "BIS" in upper_text:
        meta["issuing_body"] = "BIS / IS"
    elif "MIL-" in upper_text or "PIA-" in upper_text:
        meta["issuing_body"] = "MIL-SPEC / PIA"
    else:
        meta["issuing_body"] = "INDIAN DEFENCE"

    # Explicit spec number patterns:
    # Pre-normalize slashes with spaces: 'IND / TC / 3038' -> 'IND/TC/3038'
    norm_text = re.sub(r"[ \t]*/[ \t]*", "/", full_text)

    # 1. High-priority structured standard identifiers
    spec_patterns = [
        # Provisional e.g. Prov/S/2599/TC-1(a)/2022/01(b)
        r"\b(Prov(?:isional)?/S/[A-Za-z0-9\-\(\)/._]+)",
        # ADRDE e.g. ADRDE/SPECN/1992/36, ADRDE/SPECN/80
        r"\b(ADRDE/(?:SPECN/)?[A-Za-z0-9\-\/._]+(?:\s*[0-9]+)?)",
        # DMSRDE e.g. DMSRDE/TC/1234
        r"\b(DMSRDE/TC/[A-Za-z0-9\-\/._]+)",
        # IND/TC e.g. IND/TC/3038, IND/TC/2011/3
        r"\b(IND/TC/[A-Za-z0-9\-\(\)/._]+)",
        # IAFS e.g. IAFS 01002: 2007
        r"\b(IAFS\s+[0-9]{3,6}(?:\s*:\s*[0-9]{4})?)",
        # JSS e.g. JSS 4020-09: 2013
        r"\b(JSS\s*[:\-]?\s*[0-9]{4}[0-9A-Za-z\-:\s]*[0-9]{4})",
        # IS e.g. IS 1964: 2001
        r"\b(IS\s*[:\-]?\s*[0-9]{3,5}(?:\s*[\-:]\s*[0-9]{4})?)",
    ]
    found_spec = None
    for pat in spec_patterns:
        m = re.search(pat, norm_text, re.IGNORECASE)
        if m:
            cand_s = m.group(1).strip()
            cand_s = re.sub(r"[/.\-]+$", "", cand_s).strip()
            if len(cand_s) > 2 and cand_s.upper() not in ("CONTROLLED", "COPY"):
                found_spec = cand_s
                break

    # 2. General prefix matching e.g. "Specification No.: XYZ"
    if not found_spec:
        m_gen = re.search(
            r"(?:(?:Tech\.?\s*Part\.?\s*No\.?|Provisional\s*Specification\s*No\.?|Specification\s*No\.?|Specn\.?\s*No\.?|Doc\.?\s*No\.?)\s*[:\-]?\s*)([A-Za-z0-9\-\(\)/._\s]+)",
            norm_text,
            re.IGNORECASE,
        )
        if m_gen:
            cand = m_gen.group(1).strip()
            cand = re.split(r"[\r\n]", cand)[0].strip()
            cand = re.sub(r"^(?:Specification|Specn|Doc|No\.?)\s*[:\-]?\s*", "", cand, flags=re.IGNORECASE).strip()
            product_nouns = ("cloth", "fabric", "webbing", "tape", "cord", "cordage", "canvas", "cotton", "nylon", "polyester", "drill", "duck", "thread", "belt")
            tokens = cand.split()
            valid_tokens = []
            for t in tokens:
                if t.lower() in product_nouns:
                    break
                valid_tokens.append(t)
            cand = " ".join(valid_tokens).strip()
            cand = re.sub(r"[/.\-]+$", "", cand).strip()
            if len(cand) > 2 and cand.lower() not in product_nouns and cand.upper() not in ("CONTROLLED", "COPY"):
                found_spec = cand

    if found_spec:
        meta["spec_no"] = found_spec

    # Revision: enforce word boundaries (e.g. "REV NO: 2", "REVISION 1", "REV: A", "Third Revision")
    # Must NOT match "REVIEWED" or "REVALIDATED"
    m_rev = re.search(r"\b(?:REV(?:ISION)?\.?(?:\s+NO\.?)?)\s*[:\-]?\s*([0-9]+|[A-Z])\b(?!\w)", full_text, re.IGNORECASE)
    if m_rev:
        val = m_rev.group(1).strip()
        if val.upper() not in ("AND", "FOR", "OF", "THE", "DATE"):
            meta["revision"] = val
    else:
        m_ord = re.search(r"\b(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH)\s+REVISION\b", full_text, re.IGNORECASE)
        if m_ord:
            ord_map = {"FIRST": "1", "SECOND": "2", "THIRD": "3", "FOURTH": "4", "FIFTH": "5", "SIXTH": "6"}
            meta["revision"] = ord_map.get(m_ord.group(1).upper(), "R0")

    # Title extraction - prioritize real descriptive lines
    lines = [line.strip() for line in full_text.splitlines() if line.strip()]
    bad_title_patterns = (
        r"^(?:date|dated|dt\.?)\s*[:\-]?\s*\d+",
        r"^(?:certified\s+copy|controlled(?:\s+copy)?|copy(?:\s+no)?)\b",
        r"^(?:annexure|appendix|appendive)\b",
        r"^(?:government\s+of\s+india|ministry\s+of\s+defence)",
        r"^(?:confidential|restricted|secret)",
        r"^(?:page\s+\d+|table\s+[0-9ivx]+)",
        r"^drdo\b",
        r"^(?:tech\.?\s*part\.?\s*no\.?|specification\s*(?:ref|no\.?|reference)?\s*[:\-]|specn\.?\s*no\.?|drawing\s*no)",
        r"^l\s+pplitt",  # OCR artifact
        r"^vettec",      # OCR header artifact
        r"^code\b",      # Code header
        r"^(?:technical\s+)?specification\s*(?:for)?\s*[:\-]?\s*$",  # Preamble without item name
        r"^particulars\s*(?:for)?\s*[:\-]?\s*$",
    )

    # First pass: Look for explicit technical titles
    for line in lines:
        clean_line = line.strip()
        if any(re.search(pat, clean_line, re.IGNORECASE) for pat in bad_title_patterns):
            continue
        if re.search(r"\b(?:specification\s+for\s+\w+|particulars\s+for\s+\w+|canvas|cloth|cord|cordage|webbing|tape|drill|duck|thread|fabric|belt|belting|strap|braid)\b", clean_line, re.IGNORECASE):
            clean_title = re.sub(r"^(?:nomenclature|title)\s*[:\-]?\s*", "", clean_line, flags=re.IGNORECASE).strip()
            if len(clean_title) > 5 and not clean_title.isdigit():
                meta["title"] = clean_title
                break

    # Second pass: Any reasonable line > 5 chars if first pass didn't find technical keywords
    if not meta["title"]:
        for line in lines:
            clean_line = line.strip()
            if any(re.search(pat, clean_line, re.IGNORECASE) for pat in bad_title_patterns):
                continue
            if (
                len(clean_line) > 5
                and not re.match(r"^(?:sl|si|sr)\.?\s*no", clean_line, re.IGNORECASE)
                and "SPECIFICATION NO" not in clean_line.upper()
                and not clean_line.isdigit()
            ):
                meta["title"] = clean_line
                break

    if not meta["title"]:
        meta["title"] = clean_fn or (meta["spec_no"] or "Specification")

    return meta


def get_textract_client(region: Optional[str] = None):
    """
    Initializes a boto3 Textract client with pre-flight region verification.
    Reads credentials strictly from the environment.
    """
    target_region = region or os.getenv("AWS_REGION", "ap-south-1")
    if target_region not in SUPPORTED_TEXTRACT_REGIONS:
        raise ValueError(
            f"AWS region '{target_region}' does not support AWS Textract. "
            f"Supported regions are: {', '.join(sorted(SUPPORTED_TEXTRACT_REGIONS))}"
        )

    return boto3.client("textract", region_name=target_region)


def render_pdf_page_to_jpeg(pdf_doc: pdfium.PdfDocument, page_index: int, scale: float = 2.0) -> bytes:
    """Renders a single PDF page to JPEG bytes in memory using pypdfium2."""
    page = pdf_doc[page_index]
    image = page.render(scale=scale).to_pil()
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def extract_tables_and_lines_from_textract(response: Dict[str, Any]) -> Tuple[List[List[List[Tuple[str, float]]]], List[Tuple[str, float]]]:
    """
    Reconstructs tables and text lines from Textract AnalyzeDocument response blocks.
    Each cell is returned as (cell_text, effective_confidence).
    Each line is returned as (line_text, line_confidence).
    """
    blocks = response.get("Blocks", [])
    block_map = {b["Id"]: b for b in blocks}

    lines: List[Tuple[str, float]] = []
    tables: List[List[List[Tuple[str, float]]]] = []

    for block in blocks:
        b_type = block.get("BlockType")
        if b_type == "LINE":
            lines.append((block.get("Text", ""), block.get("Confidence", 100.0)))
        elif b_type == "TABLE":
            cell_blocks = []
            for rel in block.get("Relationships", []):
                if rel.get("Type") == "CHILD":
                    for cid in rel.get("Ids", []):
                        child = block_map.get(cid)
                        if child and child.get("BlockType") == "CELL":
                            cell_blocks.append(child)

            if not cell_blocks:
                continue

            max_row = max(c.get("RowIndex", 1) for c in cell_blocks)
            max_col = max(c.get("ColumnIndex", 1) for c in cell_blocks)

            grid: List[List[Tuple[str, float, float]]] = [[("", 100.0, 100.0) for _ in range(max_col)] for _ in range(max_row)]

            for c in cell_blocks:
                r_idx = c.get("RowIndex", 1) - 1
                c_idx = c.get("ColumnIndex", 1) - 1
                cell_conf = c.get("Confidence", 100.0)

                words: List[str] = []
                word_confs: List[float] = []
                for rel in c.get("Relationships", []):
                    if rel.get("Type") == "CHILD":
                        for wid in rel.get("Ids", []):
                            w_block = block_map.get(wid)
                            if w_block and w_block.get("BlockType") == "WORD":
                                words.append(w_block.get("Text", ""))
                                word_confs.append(w_block.get("Confidence", 100.0))

                cell_text = " ".join(words).strip()
                # Word-level OCR confidence if words present; fallback to cell_conf if empty
                effective_word_conf = min(word_confs) if word_confs else cell_conf
                grid[r_idx][c_idx] = (cell_text, effective_word_conf, cell_conf)

            tables.append(grid)

    return tables, lines


def extract_key_value_requirements(
    lines_with_conf: List[Tuple[str, float]],
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    variant_key: str = "Default",
) -> List[Dict[str, Any]]:
    """
    Extracts physical properties formatted as key-value lines without a table grid.
    Examples:
      - 'NO.OF PLAITS/dm ------------ 36/37'
      - 'WEIGHT/Mtr. ---------------- 8.5 gms.(MAX).'
      - 'Braking Strength   -   250 Kg (Minimum)'
      - 'Denier             -   840'
    Confidence gating: if line confidence < threshold, spec_value is set to None.
    Tags each requirement with source='key_value_list'.
    """
    # Regex: Parameter followed by at least one dash/colon/leader dots and a value
    kv_pattern = re.compile(
        r"^(?P<param>[A-Za-z0-9\.\/\s\(\)]+?)\s*(?:[-–—]{1,}|:{1,}|\.{3,})\s*(?P<val>[0-9]+(?:\.[0-9]+)?(?:\s*[-–—\/]\s*[0-9]+(?:\.[0-9]+)?)?.*)$"
    )

    reqs: List[Dict[str, Any]] = []
    sort_idx = 1

    for line_text, conf in lines_with_conf:
        lt = line_text.strip()
        if not lt:
            continue

        # Skip headers / introductory statements
        if re.search(r"\b(?:the\s+item|as\s+per\s+specification|dated|code|indent)\b", lt, re.IGNORECASE):
            continue

        # Skip narrative clauses (these belong to narrative override parser)
        if re.search(r"\b(?:shall|will|except|can\s+be\s+used|may\s+be)\b", lt, re.IGNORECASE):
            continue

        m = kv_pattern.match(lt)
        if not m:
            continue

        raw_param = m.group("param").strip()
        raw_val = m.group("val").strip()

        # Reject if param looks like pure metadata, serial no, or standard citation
        if len(raw_param) < 2 or raw_param.isdigit() or re.match(r"^(?:sl|si|sr)\.?\s*no\.?$", raw_param, re.IGNORECASE):
            continue
        if re.search(r"\b(?:spec(?:ification)?|drawing|drg|code|indent|per\s+is|as\s+per|dated|amdt|annexure|table|outer\s+dia|inner\s+dia|r\/n\s+no)\b", raw_param, re.IGNORECASE):
            continue
        if re.match(r"^(?:IS|ISI|JSS|BS|DIN|ASTM|MIL)[\s:\-\.]*\d*", raw_param, re.IGNORECASE):
            continue

        is_low_conf = conf < confidence_threshold
        val, tol, upper, l_type = parse_numeric_with_tol(raw_val)
        text_val = None
        if l_type == "text" and raw_val:
            text_val = raw_val
            val = None

        is_crit = any(k in raw_param.lower() for k in ("break", "strength", "b.s.", "width", "weight", "mass", "tenacity"))

        # Infer unit
        detected_unit = None
        raw_combined = f"{raw_param} {raw_val}".lower()
        if "width" in raw_combined:
            detected_unit = "mm" if "mm" in raw_combined else ("cm" if "cm" in raw_combined else "mm")
        elif "break" in raw_combined or "strength" in raw_combined or "b.s." in raw_combined:
            detected_unit = "N" if "newton" in raw_combined or " n" in raw_combined else ("kgf" if "kg" in raw_combined else "lb")
        elif "weight" in raw_combined or "mass" in raw_combined:
            detected_unit = "g/m" if "gm" in raw_combined or "g" in raw_combined else ("kg" if "kg" in raw_combined else None)
        elif "denier" in raw_combined:
            detected_unit = "denier"
        elif "length" in raw_combined and "kg" in raw_combined:
            detected_unit = "m/kg"

        reqs.append({
            "variant_keys": [variant_key],
            "parameter": raw_param,
            "unit": detected_unit,
            "limit_type": "nominal" if is_low_conf else l_type,
            "spec_value": None if is_low_conf else val,
            "tolerance": None if is_low_conf else tol,
            "upper_limit": None if is_low_conf else upper,
            "text_value": None if is_low_conf else text_val,
            "test_method": None,
            "clause_ref": "Key-Value List",
            "is_critical": is_crit,
            "sort_order": sort_idx,
            "notes": None,
            "source": "key_value_list",
            "confidence_score": round(conf, 1),
            "confidence_flag": f"LOW_CONFIDENCE ({conf:.1f}%)" if is_low_conf else None,
            "raw_extracted_text": raw_val,
        })
        sort_idx += 1

    return reqs


def extract_narrative_override_requirements(
    lines_with_conf: List[Tuple[str, float]],
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    variant_key: str = "Default",
) -> List[Dict[str, Any]]:
    """
    Extracts explicit parameter overrides stated in contractual/tender narrative clauses.
    Strict clause isolation: only parses explicit override statements (e.g. following 'EXCEPT THAT'
    or numbered/lettered override sub-clauses like '(i)', '(a)', '3)', 'A)', 'D)').
    Never infers requirements from document titles or product designations.
    Tags each requirement with source='narrative_override'.
    """
    # Filter out standalone vetting stamps or administrative headers that break up clauses
    filtered_lines = [
        (t, c) for t, c in lines_with_conf
        if not re.search(r"^(?:VETTED\s+FOR|TECHNICAL\s+PARTICULARS\s+ONLY|CERTIFIED\s+COPY|CONTROLLED\s+COPY)$", t.strip(), re.IGNORECASE)
    ]
    full_text = "\n".join([t for t, _ in filtered_lines])

    # Find where the override section begins
    except_idx = full_text.upper().find("EXCEPT")
    if except_idx == -1:
        # Check if there are numbered clauses like "1. TEARING STRENGTH..."
        if not re.search(r"\b(?:tearing|breaking)\s+strength\b", full_text, re.IGNORECASE):
            return []
        override_text = full_text
    else:
        override_text = full_text[except_idx:]

    reqs: List[Dict[str, Any]] = []
    sort_idx = 1
    seen_params: set = set()

    # Mapping line text to confidence for confidence lookup
    conf_map = {t.strip().lower(): c for t, c in lines_with_conf if t.strip()}

    def get_line_conf(snippet: str) -> float:
        snippet_lower = snippet.strip().lower()
        for lt, c in conf_map.items():
            if snippet_lower in lt or lt in snippet_lower:
                return c
        snip_words = set(re.findall(r"\w+", snippet_lower))
        best_match = (0.0, 95.0)
        for lt, c in conf_map.items():
            lt_words = set(re.findall(r"\w+", lt))
            common = snip_words.intersection(lt_words)
            if len(common) > 2:
                overlap_ratio = len(common) / len(snip_words)
                if overlap_ratio > best_match[0]:
                    best_match = (overlap_ratio, c)
        if best_match[0] > 0.3:
            return best_match[1]
        return 95.0

    # Pattern 1: Tearing / breaking strength overrides
    # e.g. "1. TEARING STRENGTH OF WARP WILL NOT BE LESS THAN 310N"
    # e.g. "2. TEARING STRENGTH OF WEFT WILL NOT BE LESS THAN 250N"
    p1 = re.compile(
        r"(?:(?:\([0-9ivxa-z]+\)|[0-9ivxa-z]+[\.\)])\s*)*(?P<param>(?:TEARING|BREAKING)\s+STRENGTH(?:\s+OF\s+[A-Za-z]+)?)\s+(?:WILL|SHALL)\s+NOT\s+BE\s+LESS\s+THAN\s+(?P<val>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>N|KGF|KG|LB)?\b",
        re.IGNORECASE,
    )
    for m in p1.finditer(override_text):
        raw_p = m.group("param").strip().title()
        if raw_p.lower() in seen_params:
            continue
        seen_params.add(raw_p.lower())
        val_str = m.group("val").strip()
        unit_str = (m.group("unit") or "N").strip().upper()
        num_val = float(val_str)
        c = get_line_conf(m.group(0))
        is_low = c < confidence_threshold

        reqs.append({
            "variant_keys": [variant_key],
            "parameter": raw_p,
            "unit": unit_str,
            "limit_type": "nominal" if is_low else "minimum",
            "spec_value": None if is_low else num_val,
            "tolerance": None,
            "upper_limit": None,
            "text_value": None,
            "test_method": None,
            "clause_ref": "Narrative Override",
            "is_critical": True,
            "sort_order": sort_idx,
            "notes": m.group(0).strip(),
            "source": "narrative_override",
            "confidence_score": round(c, 1),
            "confidence_flag": f"LOW_CONFIDENCE ({c:.1f}%)" if is_low else None,
            "raw_extracted_text": m.group(0).strip(),
        })
        sort_idx += 1

    # Pattern 2: Explicit dimension/property override: "EXCEPT [PARAM] SHALL BE [VAL] [UNIT]"
    # e.g. "EXCEPT WIDTH SHALL BE 138 CMS"
    p2 = re.compile(
        r"EXCEPT\s+(?P<param>WIDTH(?:[\s\w\(\)]*?)?)\s+SHALL\s+BE\s*(?:[A-Za-z\s]{0,30}?\s*)?(?P<val>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>CMS?|MM|M)\b",
        re.IGNORECASE,
    )
    for m in p2.finditer(override_text):
        raw_p = m.group("param").strip().title()
        if raw_p.lower() in seen_params:
            continue
        seen_params.add(raw_p.lower())
        val_str = m.group("val").strip()
        unit_str = m.group("unit").strip().lower()
        num_val = float(val_str)
        c = get_line_conf(m.group(0))
        is_low = c < confidence_threshold

        reqs.append({
            "variant_keys": [variant_key],
            "parameter": raw_p,
            "unit": unit_str,
            "limit_type": "nominal" if is_low else "minimum",
            "spec_value": None if is_low else num_val,
            "tolerance": None,
            "upper_limit": None,
            "text_value": None,
            "test_method": None,
            "clause_ref": "Narrative Override",
            "is_critical": True,
            "sort_order": sort_idx,
            "notes": m.group(0).strip(),
            "source": "narrative_override",
            "confidence_score": round(c, 1),
            "confidence_flag": f"LOW_CONFIDENCE ({c:.1f}%)" if is_low else None,
            "raw_extracted_text": m.group(0).strip(),
        })
        sort_idx += 1

    # Pattern 2b: Roll / piece length minimum
    # e.g. "A) FABRIC IN ROLL FORM SHALL NOT BE LESS THAN 40 METERS"
    p_roll_len = re.compile(
        r"(?:(?:\([0-9ivxa-z]+\)|[0-9ivxa-z]+[\.\)])\s*)*(?P<param>(?:FABRIC\s+IN\s+ROLL\s+FORM|ROLL\s+LENGTH|PIECE\s+LENGTH))\s+SHALL\s+NOT\s+BE\s+LESS\s+THAN\s+(?P<val>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>METERS?|METRES?|M|CMS?|YDS?)\b",
        re.IGNORECASE,
    )
    for m in p_roll_len.finditer(override_text):
        raw_p = m.group("param").strip().title()
        if raw_p.lower() in seen_params:
            continue
        seen_params.add(raw_p.lower())
        val_str = m.group("val").strip()
        unit_str = m.group("unit").strip().lower()
        if unit_str in ("meter", "meters", "metres", "m"):
            unit_str = "m"
        num_val = float(val_str)
        c = get_line_conf(m.group(0))
        is_low = c < confidence_threshold

        reqs.append({
            "variant_keys": [variant_key],
            "parameter": raw_p,
            "unit": unit_str,
            "limit_type": "nominal" if is_low else "minimum",
            "spec_value": None if is_low else num_val,
            "tolerance": None,
            "upper_limit": None,
            "text_value": None,
            "test_method": None,
            "clause_ref": "Narrative Override",
            "is_critical": False,
            "sort_order": sort_idx,
            "notes": m.group(0).strip(),
            "source": "narrative_override",
            "confidence_score": round(c, 1),
            "confidence_flag": f"LOW_CONFIDENCE ({c:.1f}%)" if is_low else None,
            "raw_extracted_text": m.group(0).strip(),
        })
        sort_idx += 1

    # Pattern 3: Maximum limit override: "[PARAM] SHALL NOT EXCEED [VAL] [UNIT]"
    # e.g. "(3) WIDTH OF SELVEDGES SHALL NOT EXCEED 8 MM"
    # e.g. "B) ROLL WEIGHT SHALL NOT EXCEED 50 KGS"
    p3 = re.compile(
        r"(?:(?:\([0-9ivxa-z]+\)|[0-9ivxa-z]+[\.\)])\s*)*(?P<param>(?:WIDTH\s+OF\s+SELVEDGES?|SELVEDGE\s+WIDTH|ROLL\s+WEIGHT|[A-Za-z\s]+?))\s+SHALL\s+NOT\s+EXCEED\s+(?P<val>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>MM|CMS?|M|KGS?|G|GMS?)\b",
        re.IGNORECASE,
    )
    for m in p3.finditer(override_text):
        raw_p = m.group("param").strip().title()
        if raw_p.lower() in seen_params:
            continue
        seen_params.add(raw_p.lower())
        val_str = m.group("val").strip()
        unit_str = m.group("unit").strip().lower()
        if unit_str in ("kgs", "kg"):
            unit_str = "kg"
        num_val = float(val_str)
        c = get_line_conf(m.group(0))
        is_low = c < confidence_threshold

        reqs.append({
            "variant_keys": [variant_key],
            "parameter": raw_p,
            "unit": unit_str,
            "limit_type": "nominal" if is_low else "maximum",
            "spec_value": None if is_low else num_val,
            "tolerance": None,
            "upper_limit": None,
            "text_value": None,
            "test_method": None,
            "clause_ref": "Narrative Override",
            "is_critical": False,
            "sort_order": sort_idx,
            "notes": m.group(0).strip(),
            "source": "narrative_override",
            "confidence_score": round(c, 1),
            "confidence_flag": f"LOW_CONFIDENCE ({c:.1f}%)" if is_low else None,
            "raw_extracted_text": m.group(0).strip(),
        })
        sort_idx += 1

    # Pattern 4: Fastness direct: "(c) FASTNESS TO WASHING - RATING 4 OR BETTER AS PER IS:764-79"
    p4_direct = re.compile(
        r"(?:(?:\([0-9ivxa-z]+\)|[0-9ivxa-z]+[\.\)])\s*)*(?P<param>(?:DYE\s+)?FASTNESS\s+TO\s+[A-Za-z]+)\s*[-–—:]\s*(?:RATING|CLASS)\s*(?P<val>[0-9])\s*(?:OR\s+BETTER)?(?:\s+AS\s+PER\s+(?:ISI\s+SPECIFICATION\s+NO\.?\s+)?(?P<meth>IS\s*[:\-]?\s*[0-9]+(?:[\-:][0-9]+)?))?",
        re.IGNORECASE,
    )
    for m in p4_direct.finditer(override_text):
        raw_p = m.group("param").strip().title()
        if raw_p.lower() in seen_params:
            continue
        seen_params.add(raw_p.lower())
        val_str = m.group("val").strip()
        meth_str = m.group("meth")
        num_val = float(val_str)
        c = get_line_conf(m.group(0))
        is_low = c < confidence_threshold

        reqs.append({
            "variant_keys": [variant_key],
            "parameter": raw_p,
            "unit": "Rating",
            "limit_type": "nominal" if is_low else "minimum",
            "spec_value": None if is_low else num_val,
            "tolerance": None,
            "upper_limit": None,
            "text_value": None,
            "test_method": meth_str.strip() if meth_str else None,
            "clause_ref": "Narrative Override",
            "is_critical": False,
            "sort_order": sort_idx,
            "notes": m.group(0).strip(),
            "source": "narrative_override",
            "confidence_score": round(c, 1),
            "confidence_flag": f"LOW_CONFIDENCE ({c:.1f}%)" if is_low else None,
            "raw_extracted_text": m.group(0).strip(),
        })
        sort_idx += 1

    # Pattern 4b: Fastness clause: "The dye fastness to ... shall be equal to class X or better"
    p4_clause = re.compile(
        r"(?:(?:\([0-9ivxa-z]+\)|[0-9ivxa-z]+[\.\)])\s*)*(?P<param>(?:DYE\s+)?FASTNESS\s+TO\s+[A-Za-z]+).*?(?:SHALL\s+BE\s+EQUAL\s+TO\s+)?(?:CLASS|RATING)\s*(?P<val>[0-9])\s*(?:OR\s+BETTER)?(?:\s+AS\s+PER\s+(?:ISI\s+SPECIFICATION\s+NO\.?\s+)?(?P<meth>IS\s*[:\-]?\s*[0-9]+(?:[\-:][0-9]+)?))?",
        re.IGNORECASE,
    )
    for m in p4_clause.finditer(override_text):
        raw_p = m.group("param").strip().title()
        if raw_p.lower() in seen_params:
            continue
        seen_params.add(raw_p.lower())
        val_str = m.group("val").strip()
        meth_str = m.group("meth")
        num_val = float(val_str)
        c = get_line_conf(m.group(0))
        is_low = c < confidence_threshold

        reqs.append({
            "variant_keys": [variant_key],
            "parameter": raw_p,
            "unit": "Class",
            "limit_type": "nominal" if is_low else "minimum",
            "spec_value": None if is_low else num_val,
            "tolerance": None,
            "upper_limit": None,
            "text_value": None,
            "test_method": meth_str.strip() if meth_str else None,
            "clause_ref": "Narrative Override",
            "is_critical": False,
            "sort_order": sort_idx,
            "notes": m.group(0).strip(),
            "source": "narrative_override",
            "confidence_score": round(c, 1),
            "confidence_flag": f"LOW_CONFIDENCE ({c:.1f}%)" if is_low else None,
            "raw_extracted_text": m.group(0).strip(),
        })
        sort_idx += 1

    # Pattern 5: Qualitative proofing agent option
    # e.g. "(a) COPPER OR ZINC NAPHTH-NATE CAN BE USED AS ROT PROOFING AGENT."
    p_proofing = re.compile(
        r"(?:(?:\([0-9ivxa-z]+\)|[0-9ivxa-z]+[\.\)])\s*)*(?P<val>[A-Za-z\s\-]+?)\s+CAN\s+BE\s+USED\s+AS\s+(?P<param>(?:ROT\s+)?PROOFING\s+AGENT)\b",
        re.IGNORECASE,
    )
    for m in p_proofing.finditer(override_text):
        raw_p = m.group("param").strip().title()
        if raw_p.lower() in seen_params:
            continue
        seen_params.add(raw_p.lower())
        val_text = m.group("val").strip().capitalize() + " can be used"
        c = get_line_conf(m.group(0))
        is_low = c < confidence_threshold

        reqs.append({
            "variant_keys": [variant_key],
            "parameter": raw_p,
            "unit": None,
            "limit_type": "text",
            "spec_value": None,
            "tolerance": None,
            "upper_limit": None,
            "text_value": None if is_low else val_text,
            "test_method": None,
            "clause_ref": "Narrative Override",
            "is_critical": False,
            "sort_order": sort_idx,
            "notes": m.group(0).strip(),
            "source": "narrative_override",
            "confidence_score": round(c, 1),
            "confidence_flag": f"LOW_CONFIDENCE ({c:.1f}%)" if is_low else None,
            "raw_extracted_text": m.group(0).strip(),
        })
        sort_idx += 1

    # Pattern 6: Nature of dye
    # e.g. "(b) NATURE OF DYE SHALL BE VAT DYE AS PER IS:4472(PT.I)-1967."
    p_dye = re.compile(
        r"(?:(?:\([0-9ivxa-z]+\)|[0-9ivxa-z]+[\.\)])\s*)*(?P<param>NATURE\s+OF\s+DYE)\s+SHALL\s+BE\s+(?P<val>.+?)(?:\s+AS\s+PER\s+(?P<meth>IS\s*[:\-]?\s*[0-9A-Za-z\(\)\.\-]+(?:\s*-\s*[0-9]+)?))?(?=\.|\n|$)",
        re.IGNORECASE,
    )
    for m in p_dye.finditer(override_text):
        raw_p = m.group("param").strip().title()
        if raw_p.lower() in seen_params:
            continue
        seen_params.add(raw_p.lower())
        val_text = m.group("val").strip().title()
        meth_str = m.group("meth")
        if meth_str:
            meth_str = meth_str.rstrip(".")
        c = get_line_conf(m.group(0))
        is_low = c < confidence_threshold

        reqs.append({
            "variant_keys": [variant_key],
            "parameter": raw_p,
            "unit": None,
            "limit_type": "text",
            "spec_value": None,
            "tolerance": None,
            "upper_limit": None,
            "text_value": None if is_low else val_text,
            "test_method": meth_str,
            "clause_ref": "Narrative Override",
            "is_critical": False,
            "sort_order": sort_idx,
            "notes": m.group(0).strip(),
            "source": "narrative_override",
            "confidence_score": round(c, 1),
            "confidence_flag": f"LOW_CONFIDENCE ({c:.1f}%)" if is_low else None,
            "raw_extracted_text": m.group(0).strip(),
        })
        sort_idx += 1

    # Pattern 7: Light fastness test method note (when no numeric class in clause)
    # e.g. "(i) DYE FASTNESS TO LIGHT MAY BE DETERMINED BY CARBON ARC METHOD ALSO IN CASE XENON ARC IS NOT AVAILABLE."
    p_light_meth = re.compile(
        r"(?:(?:\([0-9ivxa-z]+\)|[0-9ivxa-z]+[\.\)])\s*)*(?P<param>DYE\s+FASTNESS\s+TO\s+LIGHT)\s+MAY\s+BE\s+DETERMINED\s+BY\s+(?P<val>[A-Za-z\s]+?\s+METHOD(?:\s+ALSO\s+IN\s+CASE\s+[A-Za-z\s]+?\s+IS\s+NOT\s+AVAILABLE)?)\b",
        re.IGNORECASE,
    )
    for m in p_light_meth.finditer(override_text):
        raw_p = m.group("param").strip().title()
        if raw_p.lower() in seen_params:
            continue
        seen_params.add(raw_p.lower())
        val_text = m.group("val").strip().capitalize()
        c = get_line_conf(m.group(0))
        is_low = c < confidence_threshold

        reqs.append({
            "variant_keys": [variant_key],
            "parameter": raw_p,
            "unit": None,
            "limit_type": "text",
            "spec_value": None,
            "tolerance": None,
            "upper_limit": None,
            "text_value": None if is_low else val_text,
            "test_method": None,
            "clause_ref": "Narrative Override",
            "is_critical": False,
            "sort_order": sort_idx,
            "notes": m.group(0).strip(),
            "source": "narrative_override",
            "confidence_score": round(c, 1),
            "confidence_flag": f"LOW_CONFIDENCE ({c:.1f}%)" if is_low else None,
            "raw_extracted_text": m.group(0).strip(),
        })
        sort_idx += 1

    return reqs



def parse_physical_table_with_confidence(
    table_grid: List[List[Tuple[str, float]]],
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    table_name: str = "Table I",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Parses a physical properties table with strict confidence threshold gating.
    Zero-guessing rule: if confidence < threshold, numeric values are set to None.
    Schema matches services.spec_loader requirements.
    """
    if len(table_grid) < 2:
        return [], []

    # Detect whether Row 0 and Row 1 form a multi-line header
    # e.g. Row 0: ['', '', 'values', 'Test Method'], Row 1: ['SI No.', 'Test parameters', 'Specified between...', 'IS: 1954']
    start_data_row = 1
    combined_header = [c[0].strip().lower() for c in table_grid[0]]

    # Headerless table check: if Row 0 itself is already data (e.g. ['Braking Strength -', '250 Kg (Minimum)']), start_data_row should be 0
    if len(table_grid[0]) == 2:
        val0, _, _, ltype0 = parse_numeric_with_tol(table_grid[0][1][0])
        if val0 is not None or ltype0 in ("range", "minimum", "maximum"):
            start_data_row = 0

    if start_data_row != 0 and len(table_grid) > 2:
        row1_text = [c[0].strip().lower() for c in table_grid[1]]
        # If row 0 has empty cells or row 1 contains header indicators like 'si no', 'test parameter', merge them:
        if any(re.search(r"^(?:sl|si|sr)\.?\s*no\.?$", c) or "test parameter" in c or "parameter" in c for c in row1_text):
            start_data_row = 2
            merged = []
            for i in range(max(len(combined_header), len(row1_text))):
                h0 = combined_header[i] if i < len(combined_header) else ""
                h1 = row1_text[i] if i < len(row1_text) else ""
                merged.append(f"{h0} {h1}".strip())
            combined_header = merged

    header_row = combined_header
    variants: List[Dict[str, Any]] = []
    requirements: List[Dict[str, Any]] = []

    # Semantic Column Mapping
    sl_col_idx = None
    param_col_idx = None
    method_col_idx = None
    unit_col_idx = None
    val_col_idx = None
    variant_cols: List[Tuple[int, str]] = []

    for idx, h in enumerate(header_row):
        # Serial Number: sl no, si no, sr no, no
        if re.search(r"^(?:sl|si|sr)\.?\s*no\.?$", h) or h in ("no", "no.", "s.no", "sl.no", "si.no"):
            sl_col_idx = idx
        # Test method: "method of test", "test method", "method", "test standard", "procedure"
        elif any(k in h for k in ("method of test", "test method", "test standard", "method", "procedure")):
            method_col_idx = idx
        # Parameter: "test parameters", "parameter", "property", "particulars", "description", "characteristic", bounded "test" / "name of test"
        elif param_col_idx is None and (
            any(k in h for k in ("test parameter", "parameter", "property", "particular", "description", "characteristic"))
            or re.search(r"^(?:name\s+of\s+)?tests?$", h.strip())
        ):
            param_col_idx = idx
        # Unit: "unit", "uom"
        elif "unit" in h or "uom" in h:
            unit_col_idx = idx
        # Requirement / Value: "requirement", "specification", "specified", "value", "limit", "passing standards", "standard"
        elif any(k in h for k in ("requirement", "specification", "specified", "value", "limit", "passing standards", "standard")):
            val_col_idx = idx
        # Multi-variant columns: Type I, Class A, etc.
        elif any(k in h for k in ("type", "class", "grade", "var")):
            variant_cols.append((idx, table_grid[0][idx][0].strip()))

    # Fallback heuristics if columns not explicitly identified:
    if sl_col_idx is None:
        if len(table_grid) > start_data_row + 1 and all(table_grid[r][0][0].strip().isdigit() for r in range(start_data_row, len(table_grid))):
            sl_col_idx = 0

    if param_col_idx is None:
        if sl_col_idx == 0 and len(header_row) > 1:
            param_col_idx = 1
        else:
            param_col_idx = 0

    param_sort = 1

    # Check for Horizontal/Row-Variant Table structure:
    # Column 0 is a Variant Identifier (e.g. 'DIA (mm)', 'Size', 'Width', 'Variety', 'Vty', 'Dimension') and other columns are parameters
    # (e.g. 'Breaking Load', 'Linear Density', 'Mass/Coil', 'Thickness')
    col0_header = header_row[0] if len(header_row) > 0 else ""
    is_row_variant_table = (
        not variant_cols
        and any(k in col0_header for k in ("dia", "size", "width", "variety", "vty", "dimension"))
        and len(header_row) >= 3
        and any(any(p in h for p in ("break", "load", "strength", "mass", "density", "weight", "length", "thickness")) for h in header_row[1:])
    )

    if is_row_variant_table:
        for r_idx in range(start_data_row, len(table_grid)):
            row = table_grid[r_idx]
            if not row or len(row) < 2:
                continue
            var_cell_tuple = row[0]
            var_cell = var_cell_tuple[0]
            var_conf = var_cell_tuple[1]
            var_geom_conf = var_cell_tuple[2] if len(var_cell_tuple) > 2 else var_conf

            var_clean = var_cell.strip()
            if not var_clean or any(re.search(r"^(?:sl|si|sr)\.?\s*no\.?$", var_clean.lower()) for _ in [1]):
                continue
            if any(k in var_clean.lower() for k in ("toler", "method", "test", "is:", "is :")):
                continue

            v_title = f"{col0_header.split()[0].upper()} {var_clean}"
            v_key = re.sub(r"[^A-Za-z0-9]", "_", v_title).strip("_") or f"V{r_idx}"
            variants.append({
                "key": v_key,
                "designation": v_title,
                "class": "1",
                "sort_order": len(variants) + 1,
            })

            for c_idx in range(1, len(row)):
                col_name = header_row[c_idx] if c_idx < len(header_row) else f"Param_{c_idx}"
                val_cell_tuple = row[c_idx]
                val_cell = val_cell_tuple[0]
                val_conf = val_cell_tuple[1]
                val_geom_conf = val_cell_tuple[2] if len(val_cell_tuple) > 2 else val_conf

                if not val_cell.strip() or any(re.search(r"^(?:sl|si|sr)\.?\s*no\.?$", col_name.lower()) for _ in [1]):
                    continue

                eff_conf = min(var_conf, val_conf)
                is_low_conf = eff_conf < confidence_threshold
                min_geom = min(var_geom_conf, val_geom_conf)

                val, tol, upper, l_type = parse_numeric_with_tol(val_cell)
                text_val = val_cell if l_type == "text" else None
                is_crit = any(k in col_name.lower() for k in ("break", "strength", "width", "tenacity", "weight", "mass"))

                det_unit = None
                if "dia" in col_name.lower() or "width" in col_name.lower():
                    det_unit = "mm"
                elif "break" in col_name.lower() or "load" in col_name.lower():
                    det_unit = "kgf" if "kg" in col_name.lower() else ("N" if "n" in col_name.lower() else "lb")
                elif "mass" in col_name.lower() or "weight" in col_name.lower():
                    det_unit = "kg" if "kg" in col_name.lower() else ("g/m" if "g/m" in col_name.lower() else "g")
                elif "density" in col_name.lower():
                    det_unit = "g/m"

                conf_flag = None
                if is_low_conf:
                    conf_flag = f"LOW_CONFIDENCE ({eff_conf:.1f}%)"
                elif min_geom < 70.0:
                    conf_flag = f"LOW_STRUCTURE_CONFIDENCE ({min_geom:.1f}%)"

                requirements.append({
                    "variant_keys": [v_key],
                    "parameter": col_name.title(),
                    "unit": det_unit,
                    "limit_type": "nominal" if is_low_conf else l_type,
                    "spec_value": None if is_low_conf else val,
                    "tolerance": None if is_low_conf else tol,
                    "upper_limit": None if is_low_conf else upper,
                    "text_value": None if is_low_conf else text_val,
                    "test_method": None,
                    "clause_ref": table_name,
                    "is_critical": is_crit,
                    "sort_order": param_sort,
                    "notes": None,
                    "source": "table",
                    "confidence_score": round(eff_conf, 1),
                    "confidence_flag": conf_flag,
                    "raw_extracted_text": val_cell,
                })
                param_sort += 1

    elif variant_cols:
        # Multi-variant table (columns represent variants)
        for col_idx, v_title in variant_cols:
            v_key = re.sub(r"[^A-Za-z0-9]", "_", v_title).strip("_") or f"V{col_idx}"
            variants.append({
                "key": v_key,
                "designation": v_title,
                "class": "1",
                "sort_order": len(variants) + 1,
            })

            for r_idx in range(start_data_row, len(table_grid)):
                row = table_grid[r_idx]
                if param_col_idx >= len(row):
                    continue
                param_cell_tuple = row[param_col_idx]
                param_cell = param_cell_tuple[0]
                p_conf = param_cell_tuple[1]
                p_geom_conf = param_cell_tuple[2] if len(param_cell_tuple) > 2 else p_conf

                if not param_cell or (sl_col_idx is not None and param_col_idx == sl_col_idx):
                    continue
                p_strip = param_cell.strip()
                if any(re.search(r"^(?:sl|si|sr)\.?\s*no\.?$", p_strip.lower()) for _ in [1]):
                    continue

                # Guard against bare numbers, parenthetical letters/numbers, and standard citations masquerading as parameter names
                if re.match(r"^[0-9]+(?:\.[0-9]+)?$", p_strip):
                    continue
                if re.match(r"^(?:\([0-9a-zivx]+\)|[0-9a-zivx]+[\.\)])$", p_strip, re.IGNORECASE):
                    continue
                if re.match(r"^(?:IS|JSS|BS|DIN|ASTM|MIL|DEF|ADRDE|IND)[\s:\-\.]*\d*", p_strip, re.IGNORECASE):
                    continue
                if len(p_strip) <= 2 and p_strip.lower() not in ("ph",):
                    continue

                val_cell_tuple = row[col_idx] if col_idx < len(row) else ("", 0.0, 0.0)
                val_cell = val_cell_tuple[0]
                v_conf = val_cell_tuple[1]
                v_geom_conf = val_cell_tuple[2] if len(val_cell_tuple) > 2 else v_conf

                eff_conf = min(p_conf, v_conf)
                is_low_conf = eff_conf < confidence_threshold
                min_geom = min(p_geom_conf, v_geom_conf)

                val, tol, upper, l_type = parse_numeric_with_tol(val_cell)
                text_val = None
                if l_type == "text" and val_cell:
                    text_val = val_cell
                    val = None

                is_crit = any(k in param_cell.lower() for k in ("break", "strength", "width", "tenacity", "weight"))

                # Determine unit
                detected_unit = None
                if unit_col_idx is not None and unit_col_idx < len(row):
                    detected_unit = row[unit_col_idx][0]
                if not detected_unit:
                    if "width" in param_cell.lower():
                        detected_unit = "mm"
                    elif "break" in param_cell.lower() or "strength" in param_cell.lower():
                        detected_unit = "kgf" if "kg" in param_cell.lower() else ("N" if "n" in param_cell.lower() else "lb")

                # Determine test method
                detected_method = None
                if method_col_idx is not None and method_col_idx < len(row):
                    detected_method = row[method_col_idx][0]

                conf_flag = None
                if is_low_conf:
                    conf_flag = f"LOW_CONFIDENCE ({eff_conf:.1f}%)"
                elif min_geom < 70.0:
                    conf_flag = f"LOW_STRUCTURE_CONFIDENCE ({min_geom:.1f}%)"

                requirements.append({
                    "variant_keys": [v_key],
                    "parameter": param_cell,
                    "unit": detected_unit,
                    "limit_type": "nominal" if is_low_conf else l_type,
                    "spec_value": None if is_low_conf else val,
                    "tolerance": None if is_low_conf else tol,
                    "upper_limit": None if is_low_conf else upper,
                    "text_value": None if is_low_conf else text_val,
                    "test_method": detected_method,
                    "clause_ref": table_name,
                    "is_critical": is_crit,
                    "sort_order": param_sort,
                    "notes": None,
                    "source": "table",
                    "confidence_score": round(eff_conf, 1),
                    "confidence_flag": conf_flag,
                    "raw_extracted_text": val_cell,
                })
                param_sort += 1
    else:
        # Single-variant specification (rows represent parameters, one requirement value column)
        v_key = "Default"
        variants.append({
            "key": v_key,
            "designation": "Standard",
            "class": "1",
            "sort_order": 1,
        })

        if val_col_idx is None:
            # Pick first column not assigned to sl, param, method, or unit
            candidates = [i for i in range(len(header_row)) if i not in (sl_col_idx, param_col_idx, method_col_idx, unit_col_idx)]
            if candidates:
                val_col_idx = candidates[-1]
            else:
                val_col_idx = param_col_idx + 1

        for r_idx in range(start_data_row, len(table_grid)):
            row = table_grid[r_idx]
            if param_col_idx >= len(row) or val_col_idx >= len(row):
                continue
            param_cell_tuple = row[param_col_idx]
            param_cell = param_cell_tuple[0]
            p_conf = param_cell_tuple[1]
            p_geom_conf = param_cell_tuple[2] if len(param_cell_tuple) > 2 else p_conf

            val_cell_tuple = row[val_col_idx]
            val_cell = val_cell_tuple[0]
            v_conf = val_cell_tuple[1]
            v_geom_conf = val_cell_tuple[2] if len(val_cell_tuple) > 2 else v_conf

            if not param_cell or not val_cell:
                continue
            p_strip = param_cell.strip()
            if re.search(r"^(?:sl|si|sr)\.?\s*no\.?$", p_strip.lower()):
                continue

            # Guard against bare numbers, parenthetical letters/numbers, and standard citations masquerading as parameter names
            if re.match(r"^[0-9]+(?:\.[0-9]+)?$", p_strip):
                continue
            if re.match(r"^(?:\([0-9a-zivx]+\)|[0-9a-zivx]+[\.\)])$", p_strip, re.IGNORECASE):
                continue
            if re.match(r"^(?:IS|JSS|BS|DIN|ASTM|MIL|DEF|ADRDE|IND)[\s:\-\.]*\d*", p_strip, re.IGNORECASE):
                continue
            if len(p_strip) <= 2 and p_strip.lower() not in ("ph",):
                continue

            eff_conf = min(p_conf, v_conf)
            is_low_conf = eff_conf < confidence_threshold
            min_geom = min(p_geom_conf, v_geom_conf)

            val, tol, upper, l_type = parse_numeric_with_tol(val_cell)
            text_val = None
            if l_type == "text" and val_cell:
                text_val = val_cell
                val = None

            is_crit = any(k in param_cell.lower() for k in ("break", "strength", "width", "tenacity", "weight"))

            detected_unit = None
            if unit_col_idx is not None and unit_col_idx < len(row):
                detected_unit = row[unit_col_idx][0]
            if not detected_unit:
                if "width" in param_cell.lower():
                    detected_unit = "mm"
                elif "break" in param_cell.lower() or "strength" in param_cell.lower():
                    detected_unit = "kgf" if "kg" in param_cell.lower() else ("N" if "n" in param_cell.lower() else "lb")

            detected_method = None
            if method_col_idx is not None and method_col_idx < len(row):
                detected_method = row[method_col_idx][0]

            conf_flag = None
            if is_low_conf:
                conf_flag = f"LOW_CONFIDENCE ({eff_conf:.1f}%)"
            elif min_geom < 70.0:
                conf_flag = f"LOW_STRUCTURE_CONFIDENCE ({min_geom:.1f}%)"

            requirements.append({
                "variant_keys": [v_key],
                "parameter": param_cell,
                "unit": detected_unit,
                "limit_type": "nominal" if is_low_conf else l_type,
                "spec_value": None if is_low_conf else val,
                "tolerance": None if is_low_conf else tol,
                "upper_limit": None if is_low_conf else upper,
                "text_value": None if is_low_conf else text_val,
                "test_method": detected_method,
                "clause_ref": table_name,
                "is_critical": is_crit,
                "sort_order": param_sort,
                "notes": None,
                "source": "table",
                "confidence_score": round(eff_conf, 1),
                "confidence_flag": conf_flag,
                "raw_extracted_text": val_cell,
            })
            param_sort += 1

    return variants, requirements


def parse_spec_pdf_textract(
    pdf_path: str,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    region: Optional[str] = None,
    client: Any = None,
    pages_to_process: Optional[List[int]] = None,
    skip_blank_pages: bool = True,
    cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extracts specification data from a local PDF using AWS Textract AnalyzeDocument (TABLES).
    Outputs the standard staging JSON schema matching services.spec_loader.
    If cache_dir is specified, raw Textract responses are cached/loaded per page to avoid redundant OCR calls.
    """
    if client is None:
        client = get_textract_client(region=region)

    filename = os.path.basename(pdf_path)
    pdf_doc = pdfium.PdfDocument(pdf_path)
    total_pages = len(pdf_doc)

    doc_fitz = None
    if skip_blank_pages:
        try:
            import fitz
            doc_fitz = fitz.open(pdf_path)
        except Exception:
            doc_fitz = None

    all_lines: List[Tuple[str, float]] = []
    extracted_variants: List[Dict[str, Any]] = []
    extracted_requirements: List[Dict[str, Any]] = []
    extracted_defects: List[Dict[str, Any]] = []
    extracted_sampling: List[Dict[str, Any]] = []

    target_pages = pages_to_process if pages_to_process is not None else list(range(total_pages))

    for page_idx in target_pages:
        if page_idx >= total_pages:
            continue
        if skip_blank_pages and doc_fitz is not None:
            try:
                p = doc_fitz[page_idx]
                if len(p.get_text().strip()) == 0 and len(p.get_images()) == 0:
                    pix = p.get_pixmap(matrix=fitz.Matrix(0.25, 0.25))
                    if min(pix.samples) == 255:
                        logger.info(f"Skipping truly blank page {page_idx+1} in {filename}")
                        continue
            except Exception:
                pass

        resp = None
        cache_path = None
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, f"{filename}_p{page_idx+1}.json")
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as f_c:
                        resp = json.load(f_c)
                    logger.info(f"Loaded cached Textract response for {filename} page {page_idx+1}")
                except Exception as c_err:
                    logger.warning(f"Failed to load cache {cache_path}: {c_err}")
                    resp = None

        if resp is None:
            jpeg_bytes = render_pdf_page_to_jpeg(pdf_doc, page_idx, scale=2.0)
            try:
                resp = client.analyze_document(
                    Document={"Bytes": jpeg_bytes},
                    FeatureTypes=["TABLES"],
                )
            except ClientError as exc:
                logger.error(f"Textract API error on {filename} page {page_idx+1}: {exc}")
                raise

            if cache_path:
                try:
                    with open(cache_path, "w", encoding="utf-8") as f_c:
                        json.dump(resp, f_c)
                except Exception as c_err:
                    logger.warning(f"Failed to write cache {cache_path}: {c_err}")

        tables, lines = extract_tables_and_lines_from_textract(resp)
        all_lines.extend(lines)

        for t_idx, table_grid in enumerate(tables):
            table_title = f"Page {page_idx+1} Table {t_idx+1}"
            t_str = " ".join([c[0] for row in table_grid for c in row if c[0]]).lower()

            # Detect if table is an AQL / inspection / sampling plan table
            is_sampling_table = (
                any(k in t_str for k in ("sample size", "acceptance no", "acceptance number", "aql", "lots size in bolts", "sampling plan"))
                and not any(k in t_str for k in ("breaking strength", "breaking load", "tensile strength", "ends per", "picks per", "mass, g", "weight in kg"))
            )

            if is_sampling_table:
                # Capture as sampling plan, not physical requirements
                extracted_sampling.append({
                    "clause_ref": table_title,
                    "table_name": table_title,
                    "rows_detected": len(table_grid),
                })
                continue

            # Detect if table is a referenced standards / related specifications list
            is_reference_table = (
                any(k in t_str for k in (
                    "reference is made in this specification", "related specifications",
                    "referenced documents", "list of referred standards", "referenced standards",
                ))
                or (
                    ("method for determination" in t_str or "glossary of terms" in t_str or "methods of physical test" in t_str)
                    and any(k in t_str for k in ("is :", "is:", "jss:", "jss :", "ind/tc/"))
                    and not any(k in t_str for k in ("specified", "requirement", "tolerance", "min.", "max."))
                )
            )

            if is_reference_table:
                continue

            if any(k in t_str for k in (
                "break", "brak", "width", "weight", "thickness", "type i",
                "ends", "picks", "yarn", "parameter", "particular", "density",
                "property", "properties", "mass", "gsm", "composition", "characteristics",
            )):
                v_list, r_list = parse_physical_table_with_confidence(table_grid, confidence_threshold, table_name=table_title)
                for v in v_list:
                    if not any(ev["key"] == v["key"] for ev in extracted_variants):
                        extracted_variants.append(v)
                extracted_requirements.extend(r_list)

    full_text = "\n".join([t for t, _ in all_lines])
    spec_meta = extract_header_metadata(full_text, filename_hint=filename)

    # Requirements extraction fallback routing:
    # Check if document has narrative override indicators ('EXCEPT', 'AMDT', etc.)
    has_narrative_markers = bool(
        re.search(r"\b(?:EXCEPT|AMDT|AMENDMENT)\b", full_text, re.IGNORECASE)
    )

    if not extracted_requirements:
        if has_narrative_markers:
            # Prioritize narrative overrides when exception markers are present
            narrative_reqs = extract_narrative_override_requirements(all_lines, confidence_threshold=confidence_threshold)
            if narrative_reqs:
                extracted_requirements.extend(narrative_reqs)
            else:
                kv_reqs = extract_key_value_requirements(all_lines, confidence_threshold=confidence_threshold)
                if kv_reqs:
                    extracted_requirements.extend(kv_reqs)
        else:
            # Standard order: key-value first, then narrative fallback
            kv_reqs = extract_key_value_requirements(all_lines, confidence_threshold=confidence_threshold)
            if kv_reqs:
                extracted_requirements.extend(kv_reqs)
            else:
                narrative_reqs = extract_narrative_override_requirements(all_lines, confidence_threshold=confidence_threshold)
                if narrative_reqs:
                    extracted_requirements.extend(narrative_reqs)

    if not extracted_variants:
        extracted_variants.append({
            "key": "Default",
            "designation": "Standard",
            "class": "1",
            "description": f"Standard specification {spec_meta.get('spec_no') or filename}",
            "sort_order": 1,
        })

    if doc_fitz is not None:
        try:
            doc_fitz.close()
        except Exception:
            pass

    return {
        "specification": spec_meta,
        "variants": extracted_variants,
        "requirements": extracted_requirements,
        "defects": extracted_defects,
        "sampling": extracted_sampling,
    }
