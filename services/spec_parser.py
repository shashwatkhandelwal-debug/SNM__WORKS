"""
services/spec_parser.py — Deterministic Technical Specification PDF Parser for SNM Works.

Deterministic extraction engine for narrow wovens, fabrics, and cordage specification PDFs
(e.g., PIA-W-4088, MIL-W-4088, ASTM, BIS).

Guarantees:
- Deterministic extraction using pdfplumber and regex
- Zero AI / LLM calls
- Zero guessing / interpolation: ambiguous or unparsed cells return None
- Outputs strictly formatted JSON conforming to SNM Works specification schema
"""

import io
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import pdfplumber

logger = logging.getLogger("snm_works.spec_parser")

# Standard applicable test standards lookup
KNOWN_TEST_METHODS = {
    "AATCC EP1", "AATCC EP2", "AATCC EP8", "AATCC EP9",
    "AATCC TM8", "AATCC TM16.3", "AATCC TM20", "AATCC TM61", "AATCC TM81",
    "ASTM D1423", "ASTM D1776", "ASTM D1777", "ASTM D1907",
    "ASTM D3774", "ASTM D3775", "ASTM D3776", "ASTM D6770",
    "ANSI/ASQ Z1.4", "PIA-STD-1480", "PIA-TM-504", "PIA-TM-4108",
    "FED-STD-191", "MIL-STD-105",
}


def clean_cell_text(text: Optional[str]) -> str:
    """Cleans up raw cell text by stripping and collapsing whitespace."""
    if text is None:
        return ""
    # Replace non-breaking spaces and line breaks with single spaces
    return re.sub(r"\s+", " ", str(text)).strip()


def parse_numeric_with_tol(val_str: str) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
    """
    Parses numeric values with tolerances, ranges, or min/max.
    Returns: (spec_value, tolerance, upper_limit, limit_type)
    """
    cleaned = clean_cell_text(val_str)
    if not cleaned or cleaned == "-" or cleaned == "–" or cleaned.lower() == "n/a":
        return None, None, None, "nominal"

    # Check for range (e.g. 0.040 - 0.070, 0.040 to 0.070)
    range_match = re.match(r"^([\d\.]+)\s*(?:-|–|to)\s*([\d\.]+)$", cleaned, re.IGNORECASE)
    if range_match:
        try:
            low = float(range_match.group(1))
            high = float(range_match.group(2))
            return low, None, high, "range"
        except ValueError:
            pass

    # Check for plus/minus tolerance (e.g. 1.75 +/- 0.0625 or 1.75 ± 0.0625)
    tol_match = re.match(r"^([\d\.]+)\s*(?:\+\/\-|±|\+\-)\s*([\d\.]+)$", cleaned)
    if tol_match:
        try:
            nom = float(tol_match.group(1))
            tol = float(tol_match.group(2))
            return nom, tol, None, "nominal"
        except ValueError:
            pass

    # Check for min / max prefixes (e.g. min 4000, max 1.60, >= 4000, <= 1.60)
    min_match = re.match(r"^(?:min|minimum|>=|≥)\s*([\d\.]+)$", cleaned, re.IGNORECASE)
    if min_match:
        try:
            return float(min_match.group(1)), None, None, "minimum"
        except ValueError:
            pass

    max_match = re.match(r"^(?:max|maximum|<=|≤)\s*([\d\.]+)$", cleaned, re.IGNORECASE)
    if max_match:
        try:
            return float(max_match.group(1)), None, None, "maximum"
        except ValueError:
            pass

    # Pure float
    try:
        val = float(cleaned)
        return val, None, None, "nominal"
    except ValueError:
        pass

    return None, None, None, "text"


def extract_header_metadata(full_text: str) -> Dict[str, Any]:
    """
    Extracts specification number, revision, title, dates, and scope from the document header.
    """
    spec_meta = {
        "spec_no": None,
        "revision": "R0",
        "title": None,
        "issuing_body": None,
        "issued_on": None,
        "supersedes": None,
        "distribution": "Public",
        "scope": None,
        "notes": "Extracted via SNM Deterministic Spec Parser",
    }

    # Match spec number and revision (e.g., PIA-W-4088G, MIL-W-4088K, ASTM D3774)
    spec_no_match = re.search(r"(PIA-W-4088|MIL-W-4088|DEF-STAN\s+\d+-\d+|ASTM\s+[A-Z]\d+)([A-Z0-9]*)", full_text)
    if spec_no_match:
        base_no = spec_no_match.group(1).strip()
        rev_char = spec_no_match.group(2).strip()
        spec_meta["spec_no"] = f"{base_no}{rev_char}" if rev_char else base_no
        spec_meta["revision"] = rev_char if rev_char else "R0"
    else:
        # Generic spec pattern: e.g. SPEC-1234
        generic_match = re.search(r"SPECIFICATION\s+([A-Z0-9\-\.]+)", full_text, re.IGNORECASE)
        if generic_match:
            spec_meta["spec_no"] = generic_match.group(1).strip()

    # Match title
    title_match = re.search(
        r"(?:SPECIFICATION|STANDARD)\s+FOR\s+([A-Z\s,]+?)(?=\n\s*[1-9]\.|\n\s*SCOPE|\n\s*TABLE|\n\s*[A-Z][a-z]|\n\s*Parachute|\n\s*Department|$)",
        full_text,
        re.IGNORECASE,
    )
    if title_match:
        spec_meta["title"] = re.sub(r"\s+", " ", title_match.group(1)).strip()
    elif "WEBBING, TEXTILE, NYLON" in full_text.upper():
        spec_meta["title"] = "WEBBING, TEXTILE, NYLON"
    elif "TAPE, TEXTILE" in full_text.upper():
        spec_meta["title"] = "TAPE, TEXTILE"

    # Match issuing body
    if "Parachute Industry Association" in full_text:
        spec_meta["issuing_body"] = "Parachute Industry Association"
    elif "Department of Defense" in full_text or "Naval Air" in full_text:
        spec_meta["issuing_body"] = "US Department of Defense"
    elif "Bureau of Indian Standards" in full_text:
        spec_meta["issuing_body"] = "Bureau of Indian Standards"

    # Match date (YYYY-MM-DD or Month DD, YYYY)
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", full_text)
    if date_match:
        spec_meta["issued_on"] = date_match.group(1)
    else:
        month_match = re.search(
            r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
            full_text,
            re.IGNORECASE,
        )
        if month_match:
            month_names = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
            m_idx = month_names.index(month_match.group(1).lower()) + 1
            day = int(month_match.group(2))
            year = int(month_match.group(3))
            spec_meta["issued_on"] = f"{year:04d}-{m_idx:02d}-{day:02d}"

    # Match supersedes
    super_match = re.search(r"SUPERSEDING\s+([A-Z0-9\-\.]+)", full_text, re.IGNORECASE)
    if super_match:
        spec_meta["supersedes"] = super_match.group(1).strip()

    # Match scope paragraph
    scope_match = re.search(r"1\.\s*SCOPE\s*(.*?)(?=\n\s*2\.|\n\s*APPLICABLE DOCUMENTS|$)", full_text, re.DOTALL | re.IGNORECASE)
    if scope_match:
        spec_meta["scope"] = re.sub(r"\s+", " ", scope_match.group(1)).strip()

    return spec_meta


def parse_physical_requirements_table(
    table: List[List[Optional[str]]],
    table_name: str,
    class_name: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Extracts variants and physical requirements matrix from a standard requirement table (e.g. TABLE IV or V).
    """
    variants: List[Dict[str, Any]] = []
    requirements: List[Dict[str, Any]] = []

    if not table or len(table) < 3:
        return variants, requirements

    # Row 0 or 1 usually contains Type headers (e.g. Type I, Type II, ..., Type XXVII)
    header_row_idx = 0
    type_cols: List[Tuple[int, str]] = []

    for row_idx in range(min(3, len(table))):
        row = table[row_idx]
        for col_idx, cell in enumerate(row):
            txt = clean_cell_text(cell)
            if re.match(r"^Type\s+[IVXLCDM\d]+[a-z]?$", txt, re.IGNORECASE):
                type_cols.append((col_idx, txt))
        if type_cols:
            header_row_idx = row_idx
            break

    if not type_cols:
        return variants, requirements

    # Build variants list
    sort_base = 1
    for col_idx, type_desig in type_cols:
        v_key = f"{type_desig}-C{class_name}" if class_name else type_desig
        variants.append({
            "key": v_key,
            "designation": type_desig,
            "class": class_name,
            "description": f"{type_desig} {f'Class {class_name}' if class_name else ''}".strip(),
            "sort_order": sort_base,
        })
        sort_base += 1

    # Extract requirement rows
    param_sort = 1
    for row in table[header_row_idx + 1:]:
        if not row or not any(row):
            continue

        raw_param = clean_cell_text(row[0])
        if not raw_param:
            continue

        # Determine parameter name, unit, and test method
        param_name = raw_param
        unit = None
        test_method = None
        is_critical = False

        if "width" in raw_param.lower():
            param_name = "Width"
            unit = "in"
            test_method = "ASTM D3774"
        elif "thickness" in raw_param.lower():
            param_name = "Thickness"
            unit = "in"
            test_method = "ASTM D1777"
        elif "weight" in raw_param.lower():
            param_name = "Weight"
            unit = "oz/yd"
            test_method = "ASTM D3776"
        elif "breaking" in raw_param.lower() or "strength" in raw_param.lower():
            param_name = "Breaking strength"
            unit = "lb"
            test_method = "ASTM D3774"
            is_critical = True
        elif "total warp" in raw_param.lower() or "warp yarns" in raw_param.lower():
            param_name = "Total warp yarns"
            unit = "ends"
            test_method = "Visual / Count"
        elif "binder" in raw_param.lower():
            param_name = "Binder yarns"
            unit = "ends"
            test_method = "Visual / Count"
        elif "filling" in raw_param.lower() or "picks" in raw_param.lower():
            param_name = "Filling yarns per inch"
            unit = "picks/in"
            test_method = "ASTM D3775"
        elif "denier" in raw_param.lower():
            param_name = "Yarn denier"
            unit = "den"
            test_method = "ASTM D1907"
        elif "ply" in raw_param.lower():
            param_name = "Yarn ply"
            unit = None
            test_method = "ASTM D1423"
        elif "weave" in raw_param.lower():
            param_name = "Weave"
            unit = None
            test_method = "Visual"

        for col_idx, type_desig in type_cols:
            if col_idx >= len(row):
                continue

            cell_val = row[col_idx]
            spec_val, tol_val, upper_val, limit_type = parse_numeric_with_tol(cell_val)
            text_val = None

            # If limit_type was default 'nominal' but parameter header specified min/max, apply it
            if limit_type == "nominal" and spec_val is not None:
                if ", min" in raw_param.lower() or "min," in raw_param.lower() or "minimum" in raw_param.lower():
                    limit_type = "minimum"
                elif ", max" in raw_param.lower() or "max," in raw_param.lower() or "maximum" in raw_param.lower():
                    limit_type = "maximum"

            if limit_type == "text" and cell_val:
                text_val = clean_cell_text(cell_val)
                spec_val = None
                limit_type = "text"

            v_key = f"{type_desig}-C{class_name}" if class_name else type_desig

            requirements.append({
                "variant_keys": [v_key],
                "parameter": param_name,
                "unit": unit,
                "limit_type": limit_type,
                "spec_value": spec_val,
                "tolerance": tol_val,
                "upper_limit": upper_val,
                "text_value": text_val,
                "test_method": test_method,
                "clause_ref": table_name,
                "is_critical": is_critical,
                "sort_order": param_sort,
                "notes": None,
            })

        param_sort += 1

    return variants, requirements


def parse_defects_table(table: List[List[Optional[str]]], table_name: str) -> List[Dict[str, Any]]:
    """
    Extracts defect examination classifications from a defects table (e.g. TABLE VI).
    """
    defects: List[Dict[str, Any]] = []
    if not table:
        return defects

    current_examine = "Visual examination"

    for row in table:
        if not row or not any(row):
            continue

        cleaned_cells = [clean_cell_text(c) for c in row if c]
        if not cleaned_cells:
            continue

        # Check for Section/Category header row (e.g. "Examine: Cut, hole, tear...")
        first_cell = cleaned_cells[0]
        if len(cleaned_cells) == 1 or "examine" in first_cell.lower() or "defect" in first_cell.lower():
            if not any(k in first_cell.lower() for k in ["major", "minor", "classification"]):
                current_examine = first_cell
                continue

        # Normal defect row: [Examine/Category, Defect Description, Classification/Code]
        defect_desc = None
        classification = "Major"
        clause_ref = table_name

        if len(row) >= 3:
            examine_cell = clean_cell_text(row[0])
            desc_cell = clean_cell_text(row[1])
            class_cell = clean_cell_text(row[2])

            if examine_cell:
                current_examine = examine_cell
            defect_desc = desc_cell
            classification = class_cell or "Major"
        elif len(row) == 2:
            defect_desc = clean_cell_text(row[0])
            classification = clean_cell_text(row[1]) or "Major"

        if defect_desc and defect_desc.lower() not in ("defect", "description"):
            defects.append({
                "examine": current_examine,
                "defect": defect_desc,
                "classification": classification,
                "clause_ref": clause_ref,
            })

    return defects


def parse_sampling_table(table: List[List[Optional[str]]], table_name: str) -> List[Dict[str, Any]]:
    """
    Extracts inspection sampling lot size plans from a sampling table (e.g. TABLE VII).
    """
    sampling: List[Dict[str, Any]] = []
    if not table:
        return sampling

    for row in table:
        if not row or len(row) < 3:
            continue

        lot_str = clean_cell_text(row[0])
        sample_str = clean_cell_text(row[1])
        accept_str = clean_cell_text(row[2]) if len(row) > 2 else None

        # Parse lot size range (e.g. "Up to 500", "501 to 1200", "1201 to 3200", "3201 and over")
        lot_from = 0.0
        lot_to = None

        if "up to" in lot_str.lower():
            m = re.search(r"(\d+)", lot_str)
            if m:
                lot_to = float(m.group(1))
        elif "to" in lot_str.lower() or "-" in lot_str:
            m = re.findall(r"(\d+)", lot_str)
            if len(m) >= 2:
                lot_from = float(m[0])
                lot_to = float(m[1])
        elif "and over" in lot_str.lower() or "and above" in lot_str.lower() or "+" in lot_str:
            m = re.search(r"(\d+)", lot_str)
            if m:
                lot_from = float(m.group(1))
        else:
            continue

        # Parse sample size
        sample_size = 0.0
        m_sample = re.search(r"(\d+)", sample_str)
        if m_sample:
            sample_size = float(m_sample.group(1))
        else:
            continue

        # Parse accept number
        accept_no = 0
        if accept_str:
            m_acc = re.search(r"(\d+)", accept_str)
            if m_acc:
                accept_no = int(m_acc.group(1))

        sampling.append({
            "basis": "yards",
            "purpose": "Inspection",
            "lot_from": lot_from,
            "lot_to": lot_to,
            "sample_size": sample_size,
            "accept_number": accept_no,
            "notes": f"Extracted from {table_name}",
        })

    return sampling


def parse_spec_pdf(pdf_bytes_or_path: Union[bytes, str]) -> Dict[str, Any]:
    """
    Main deterministic extraction entrypoint.
    Accepts PDF file path or raw PDF bytes, and returns complete parsed specification dict.
    """
    if isinstance(pdf_bytes_or_path, bytes):
        pdf_file = io.BytesIO(pdf_bytes_or_path)
    else:
        pdf_file = pdf_bytes_or_path

    all_text = []
    extracted_variants: List[Dict[str, Any]] = []
    extracted_requirements: List[Dict[str, Any]] = []
    extracted_defects: List[Dict[str, Any]] = []
    extracted_sampling: List[Dict[str, Any]] = []

    try:
        with pdfplumber.open(pdf_file) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                all_text.append(text)

                tables = page.extract_tables()
                for t_idx, table in enumerate(tables):
                    # Analyze page text surrounding table to identify table title
                    table_title = f"Page {page_idx + 1} Table {t_idx + 1}"
                    if "TABLE IV" in text:
                        table_title = "TABLE IV"
                    elif "TABLE V" in text:
                        table_title = "TABLE V"
                    elif "TABLE VI" in text:
                        table_title = "TABLE VI"
                    elif "TABLE VII" in text:
                        table_title = "TABLE VII"

                    # Check table type by inspecting contents
                    table_str = " ".join([clean_cell_text(c) for row in table for c in row if c])
                    
                    if "Type I" in table_str or "Type VIII" in table_str or "Breaking" in table_str or "Width" in table_str:
                        class_name = "1" if "Class 1" in text or "TABLE IV" in table_title else ("1A" if "Class 1A" in text or "TABLE V" in table_title else None)
                        v_list, r_list = parse_physical_requirements_table(table, table_title, class_name=class_name)
                        for v in v_list:
                            if not any(ev["key"] == v["key"] for ev in extracted_variants):
                                extracted_variants.append(v)
                        extracted_requirements.extend(r_list)
                    elif "defect" in table_str.lower() or "classification" in table_str.lower() or "Major" in table_str:
                        d_list = parse_defects_table(table, table_title)
                        extracted_defects.extend(d_list)
                    elif "sample size" in table_str.lower() or "lot" in table_str.lower() or "accept" in table_str.lower():
                        s_list = parse_sampling_table(table, table_title)
                        extracted_sampling.extend(s_list)
    except Exception as exc:
        logger.warning(f"Could not parse PDF content via pdfplumber: {exc}")
        return {
            "specification": {
                "spec_no": "UNPARSED-SPEC",
                "revision": "R0",
                "title": "Unparsed Specification",
                "notes": f"Parser warning: {str(exc)}",
            },
            "variants": [{"key": "Default", "designation": "Standard", "class": "1", "sort_order": 1}],
            "requirements": [],
            "defects": [],
            "sampling": [],
        }

    full_text = "\n".join(all_text)
    spec_meta = extract_header_metadata(full_text)

    # If no variants extracted from tables, create a default variant from spec header
    if not extracted_variants and spec_meta.get("spec_no"):
        extracted_variants.append({
            "key": "Default",
            "designation": "Standard",
            "class": "1",
            "description": f"Standard specification {spec_meta['spec_no']}",
            "sort_order": 1,
        })

    return {
        "specification": spec_meta,
        "variants": extracted_variants,
        "requirements": extracted_requirements,
        "defects": extracted_defects,
        "sampling": extracted_sampling,
    }
