"""
tests/test_spec_parser.py — Tests for Deterministic Spec PDF Parser.

Verifies:
1. Header metadata extraction (spec_no, revision, title, date, scope)
2. Tabular physical requirements extraction (Width, Thickness, Weight, Breaking strength, etc.)
3. Defect classification parsing (Major, Minor with defect codes preserved in clause_ref)
4. Inspection sampling plans extraction
5. Ambiguous or missing fields are strictly None/null (no guessing or hallucinations)
"""

import io
import pytest
from services.spec_parser import (
    clean_cell_text,
    extract_header_metadata,
    parse_numeric_with_tol,
    parse_physical_requirements_table,
    parse_defects_table,
    parse_sampling_table,
)


def test_clean_cell_text():
    assert clean_cell_text("  hello \n world  ") == "hello world"
    assert clean_cell_text(None) == ""
    assert clean_cell_text("1.750 ± 0.0625") == "1.750 ± 0.0625"


def test_parse_numeric_with_tol():
    # Range
    s, t, u, l = parse_numeric_with_tol("0.040 - 0.070")
    assert s == 0.040
    assert u == 0.070
    assert l == "range"

    # Tolerance
    s, t, u, l = parse_numeric_with_tol("1.750 ± 0.0625")
    assert s == 1.750
    assert t == 0.0625
    assert l == "nominal"

    # Minimum
    s, t, u, l = parse_numeric_with_tol("min 4000")
    assert s == 4000.0
    assert l == "minimum"

    # Maximum
    s, t, u, l = parse_numeric_with_tol("max 1.60")
    assert s == 1.60
    assert l == "maximum"

    # Blank / Ambiguous returns None
    s, t, u, l = parse_numeric_with_tol("-")
    assert s is None
    assert l == "nominal"


def test_extract_header_metadata():
    sample_header = """
    PIA-W-4088G
    March 1, 2024
    SUPERSEDING PIA-W-4088F
    
    SPECIFICATION FOR WEBBING, TEXTILE, NYLON
    
    Parachute Industry Association
    
    1. SCOPE
    This specification covers untreated and treated nylon webbing.
    2. APPLICABLE DOCUMENTS
    """
    meta = extract_header_metadata(sample_header)
    assert meta["spec_no"] == "PIA-W-4088G"
    assert meta["revision"] == "G"
    assert meta["title"] == "WEBBING, TEXTILE, NYLON"
    assert meta["issuing_body"] == "Parachute Industry Association"
    assert meta["issued_on"] == "2024-03-01"
    assert meta["supersedes"] == "PIA-W-4088F"
    assert "This specification covers" in meta["scope"]


def test_parse_physical_requirements_table():
    sample_table = [
        ["Physical property", "Type IV", "Type VIII", "Type IX"],
        ["Width, in", "1.750 ± 0.0625", "1.71875 ± 0.0625", "1.750 ± 0.0625"],
        ["Thickness, in", "0.035 - 0.055", "0.040 - 0.070", "0.040 - 0.060"],
        ["Weight, max, oz/yd", "1.15", "1.60", "1.80"],
        ["Breaking strength, min, lb", "1800", "4000", "4500"],
    ]
    variants, requirements = parse_physical_requirements_table(sample_table, "TABLE IV", class_name="1")

    assert len(variants) == 3
    assert variants[1]["key"] == "Type VIII-C1"
    assert variants[1]["designation"] == "Type VIII"
    assert variants[1]["class"] == "1"

    # Check Breaking strength for Type VIII
    t8_break = next(r for r in requirements if r["variant_keys"] == ["Type VIII-C1"] and r["parameter"] == "Breaking strength")
    assert t8_break["spec_value"] == 4000.0
    assert t8_break["unit"] == "lb"
    assert t8_break["limit_type"] == "minimum"
    assert t8_break["is_critical"] is True


def test_parse_defects_table():
    sample_defects = [
        ["Examine", "Defect", "Classification"],
        ["Visual examination", "Cut, hole, tear, or smash", "Major-101"],
        ["Visual examination", "Abrasion mark or chafed yarn", "Minor-201"],
        ["Cleanliness", "Grease, oil, or stain", "Major-102"],
    ]
    defects = parse_defects_table(sample_defects, "TABLE VI")
    assert len(defects) == 3
    assert defects[0]["examine"] == "Visual examination"
    assert defects[0]["defect"] == "Cut, hole, tear, or smash"
    assert defects[0]["classification"] == "Major-101"
    assert defects[0]["clause_ref"] == "TABLE VI"


def test_parse_sampling_table():
    sample_sampling = [
        ["Lot size (yards)", "Sample size", "Acceptance number"],
        ["Up to 500", "8", "0"],
        ["501 to 1200", "32", "0"],
        ["1201 to 3200", "50", "1"],
        ["3201 and over", "80", "2"],
    ]
    plans = parse_sampling_table(sample_sampling, "TABLE VII")
    assert len(plans) == 4
    assert plans[0]["lot_from"] == 0.0
    assert plans[0]["lot_to"] == 500.0
    assert plans[0]["sample_size"] == 8.0
    assert plans[0]["accept_number"] == 0

    assert plans[1]["lot_from"] == 501.0
    assert plans[1]["lot_to"] == 1200.0
    assert plans[1]["sample_size"] == 32.0
    assert plans[1]["accept_number"] == 0
