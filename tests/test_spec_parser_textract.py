"""
tests/test_spec_parser_textract.py — Unit tests for AWS Textract specification parser.
"""

import pytest
from services.spec_parser_textract import (
    clean_cell_text,
    extract_header_metadata,
    parse_numeric_with_tol,
    parse_physical_table_with_confidence,
    get_textract_client,
)


def test_clean_cell_text():
    assert clean_cell_text("  Hello \n  World  ") == "Hello World"
    assert clean_cell_text(None) == ""
    assert clean_cell_text("-") == "-"


def test_parse_numeric_with_tol():
    # Nominal with tolerance
    val, tol, upper, ltype = parse_numeric_with_tol("1.75 +/- 0.0625")
    assert val == 1.75
    assert tol == 0.0625
    assert ltype == "nominal"

    # Range
    val, tol, upper, ltype = parse_numeric_with_tol("0.040 - 0.070")
    assert val == 0.040
    assert upper == 0.070
    assert ltype == "range"

    # Minimum
    val, tol, upper, ltype = parse_numeric_with_tol("min 4000")
    assert val == 4000.0
    assert ltype == "minimum"

    # Nil / dash
    val, tol, upper, ltype = parse_numeric_with_tol("–")
    assert val is None


def test_confidence_threshold_gating_high_confidence():
    """Cells with confidence >= 90% must extract numerical values normally."""
    table_grid = [
        [("Parameter", 98.0), ("Type I", 98.0)],
        [("Breaking Strength (kgf)", 95.0), ("4000", 94.0)],
    ]
    variants, requirements = parse_physical_table_with_confidence(table_grid, confidence_threshold=90.0)

    assert len(variants) == 1
    assert len(requirements) == 1
    req = requirements[0]
    assert req["spec_value"] == 4000.0
    assert req["confidence_score"] == 94.0
    assert req["confidence_flag"] is None


def test_confidence_threshold_gating_low_confidence():
    """Cells with confidence < 90% must enforce zero guessing: spec_value is None."""
    table_grid = [
        [("Parameter", 98.0), ("Type I", 98.0)],
        [("Breaking Strength (kgf)", 95.0), ("4000", 82.5)],
    ]
    variants, requirements = parse_physical_table_with_confidence(table_grid, confidence_threshold=90.0)

    assert len(requirements) == 1
    req = requirements[0]
    assert req["spec_value"] is None
    assert req["confidence_score"] == 82.5
    assert "LOW_CONFIDENCE (82.5%)" in req["confidence_flag"]
    assert req["raw_extracted_text"] == "4000"


def test_extract_header_metadata_dmsrde():
    text = "DEFENCE MATERIALS & STORES RESEARCH & DEVELOPMENT ESTABLISHMENT (DMSRDE)\nSPECIFICATION NO: DMSRDE/TC/1981/6\nREV NO: 2"
    meta = extract_header_metadata(text, "1981_6.pdf")
    assert meta["issuing_body"] == "DMSRDE"
    assert "1981" in meta["spec_no"]
    assert meta["revision"] == "2"


def test_unsupported_region_fails_fast():
    with pytest.raises(ValueError) as exc:
        get_textract_client(region="invalid-region-1")
    assert "does not support AWS Textract" in str(exc.value)


def test_2011_3_table_mapping_real_blocks():
    """
    Asserts exact parameter, test_method, and requirement value mapping for 2011_3.pdf
    using the real Textract raw cell texts and confidence values:
    Columns: [SI No. | Test parameters | Method of test | Requirement]
    """
    table_grid = [
        [("SI No.", 98.5), ("Test parameters", 97.2), ("Method of test", 96.0), ("Requirement", 98.1)],
        [("1", 99.0), ("Roll length, m", 95.0), ("IS: 1954", 94.5), ("100 ± 5", 96.0)],
        [("2", 99.0), ("Mass, g/100m, max", 94.0), ("Part - II IS: 7071-1989", 93.0), ("180", 95.5)],
        [("3", 99.0), ("Breaking strength, kgf, min (Grip Distance 15 cm)", 96.0), ("Part - IV IS: 7071-1989", 95.0), ("150", 94.0)],
        [("4", 99.0), ("Extension at Break, %, min", 95.0), ("Part - IV", 94.0), ("20", 95.0)],
    ]
    variants, requirements = parse_physical_table_with_confidence(table_grid, confidence_threshold=90.0)

    assert len(variants) == 1
    assert len(requirements) == 4

    # Requirement 1: Roll length
    r1 = requirements[0]
    assert r1["parameter"] == "Roll length, m"
    assert r1["test_method"] == "IS: 1954"
    assert r1["spec_value"] == 100.0
    assert r1["tolerance"] == 5.0
    assert r1["limit_type"] == "nominal"

    # Requirement 2: Mass
    r2 = requirements[1]
    assert r2["parameter"] == "Mass, g/100m, max"
    assert r2["test_method"] == "Part - II IS: 7071-1989"
    assert r2["spec_value"] == 180.0
    assert r2["limit_type"] == "nominal"

    # Requirement 3: Breaking strength
    r3 = requirements[2]
    assert "Breaking strength" in r3["parameter"]
    assert r3["test_method"] == "Part - IV IS: 7071-1989"
    assert r3["spec_value"] == 150.0
    assert r3["is_critical"] is True

    # Requirement 4: Extension at Break
    r4 = requirements[3]
    assert r4["parameter"] == "Extension at Break, %, min"
    assert r4["test_method"] == "Part - IV"
    assert r4["spec_value"] == 20.0


def test_aramid_sewing_thread_table_mapping_real_blocks():
    """
    Asserts exact parameter and requirement value mapping for ARAMID SEWING THREAD.pdf
    using the real Textract raw cell texts:
    Columns: [Sl. No. | Parameter | Specification]
    """
    table_grid = [
        [("Sl. No.", 99.0), ("Parameter", 98.5), ("Specification", 98.0)],
        [("1", 99.0), ("Yarn", 97.0), ("220 D para-aramid", 92.0)],
        [("2", 99.0), ("Braiding", 96.0), ("16 spindle braid", 90.8)],
        [("3", 99.0), ("Diameter of braid", 95.0), ("0.8 - 0.9 mm", 93.5)],
        [("4", 99.0), ("Tensile strength", 96.0), ("30 kg (minimum)", 90.6)],
    ]
    variants, requirements = parse_physical_table_with_confidence(table_grid, confidence_threshold=90.0)

    assert len(variants) == 1
    assert len(requirements) == 4

    r1 = requirements[0]
    assert r1["parameter"] == "Yarn"
    assert r1["text_value"] == "220 D para-aramid"
    assert r1["limit_type"] == "text"
    assert r1["test_method"] is None

    r2 = requirements[1]
    assert r2["parameter"] == "Braiding"
    assert r2["text_value"] == "16 spindle braid"
    assert r2["limit_type"] == "text"

    r3 = requirements[2]
    assert r3["parameter"] == "Diameter of braid"
    assert r3["spec_value"] == 0.8
    assert r3["upper_limit"] == 0.9
    assert r3["limit_type"] == "range"

    r4 = requirements[3]
    assert r4["parameter"] == "Tensile strength"
    assert r4["spec_value"] == 30.0
    assert r4["limit_type"] == "minimum"
    assert r4["is_critical"] is True


def test_title_extraction_filtering():
    """
    Verifies that date stamps, certified copy stamps, and confidential markers
    are ignored in favor of the real specification title.
    """
    # 2011_3 scenario
    raw_text_2011 = """
    Date: 09/09/2011
    CONFIDENTIAL
    SPECIFICATION FOR CORD NYLON 1800 KG
    SPECIFICATION NO: IND/TC/2011/3
    Table 1: Test parameters
    """
    meta_2011 = extract_header_metadata(raw_text_2011, "2011_3.pdf")
    assert meta_2011["title"] == "SPECIFICATION FOR CORD NYLON 1800 KG"

    # cord 12740n scenario
    raw_text_cord = """
    Certified Copy
    CONTROLLED COPY
    SPECIFICATION NO: ADRDE/SPECN/CORD/12740N
    TECHNICAL SPECIFICATION FOR CORD 12740N
    Table I
    """
    meta_cord = extract_header_metadata(raw_text_cord, "cord 12740n.pdf")
    assert meta_cord["title"] == "TECHNICAL SPECIFICATION FOR CORD 12740N"
    assert meta_cord["spec_no"] == "ADRDE/SPECN/CORD/12740N"


def test_metadata_extraction_edge_cases():
    """
    Verifies that spec numbers with spaces/colons (IAFS, JSS), provisional specifications,
    slashed numbering (IND/TC/3038, ADRDE/SPECN/1992/36), and product noun collisions
    ('Specification No. Cloth...') are correctly disambiguated.
    """
    # 1. BELT DEEP BLUE: IAFS spec number and belt title
    raw_belt = """
    POLYESTER MIXED WITH RUBBER BELT DEEP DARK BLUE WIDTH 33MM
    Specification: Specification no. IAFS 01002: 2007
    SPECIFICATION FOR
    """
    meta_belt = extract_header_metadata(raw_belt, "BELT DEEP BLUE.pdf")
    assert meta_belt["spec_no"] == "IAFS 01002: 2007"
    assert meta_belt["title"] == "POLYESTER MIXED WITH RUBBER BELT DEEP DARK BLUE WIDTH 33MM"

    # 2. CLOTH RIPSTOP: Cloth noun after 'Specification No.' before real provisional spec
    raw_ripstop = """
    Nomenclature Cloth Rip-stop Polyester & Cotton (20:80) 190 GSM Digital Print width 152 cm. (min.)
    Specification No. Cloth Rip-stop Polyester & Cotton (20:80) 190 GSM Digital Print width 152 cm. (min.)
    Provisional Specification No. Prov/S/2599/TC-1(a)/2022/01(b)
    """
    meta_ripstop = extract_header_metadata(raw_ripstop, "CLOTH RIPSTOP PLOYESTER COTTON.pdf")
    assert meta_ripstop["spec_no"] == "Prov/S/2599/TC-1(a)/2022/01(b)"
    assert "Cloth Rip-stop" in meta_ripstop["title"]

    # 3. BLENDED FABRIC: Spaced IND / TC / 3038
    raw_blended = """
    Specification No. IND / TC / 3038
    MASTER COPY
    GOVERNMENT OF INDIA
    MINISTRY OF DEFENCE
    SPECIFICATION ON
    BLENDED FABRIC POLYESTER & COTTON (67:33)
    Undyed/Dyed
    """
    meta_blended = extract_header_metadata(raw_blended, "BLENDED FABRIC POLYESTER COTTON.pdf")
    assert meta_blended["spec_no"] == "IND/TC/3038"
    assert meta_blended["title"] == "BLENDED FABRIC POLYESTER & COTTON (67:33)"

    # 4. CORD COTTON BRAIDED: ADRDE with year and suffix
    raw_cotton_cord = """
    MASTER COPY
    ADRDE / SPECN / 1992 / 36
    SPECIFICATION FOR
    CORD COTTON BRAIDED VARIOUS
    """
    meta_cotton_cord = extract_header_metadata(raw_cotton_cord, "CORD COTTON BRAIDED 125 KG.pdf")
    assert meta_cotton_cord["spec_no"] == "ADRDE/SPECN/1992/36"
    assert meta_cotton_cord["title"] == "CORD COTTON BRAIDED VARIOUS"

    # 5. CLOTH DRILL COTTON: JSS standard and ordinal revision
    raw_drill = """
    Item Specification
    Cloth Drill Cotton Disruptive Pattern(Vat Printed)
    JSS 8315-08:2021 (Third Revision)
    """
    meta_drill = extract_header_metadata(raw_drill, "CLOTH DRILL COTTON.pdf")
    assert meta_drill["spec_no"] == "JSS 8315-08:2021"
    assert meta_drill["revision"] == "3"
    assert "Cloth Drill Cotton" in meta_drill["title"]



def test_sampling_plan_table_filtering():
    """
    Verifies that sampling plan / AQL tables (e.g. from CLOTH DRILL COTTON 142 CM.pdf)
    are recognized as sampling tables and not ingested into physical requirements.
    """
    table_grid = [
        [("", 90.0), ("Visual Parameters", 90.0), ("Physical Parameters", 90.0)],
        [("Lots size in Bolts", 90.0), ("Sample Size", 90.0), ("Acceptance No.", 90.0)],
        [("up to 150", 90.0), ("3", 90.0), ("0", 90.0)],
    ]
    t_str = " ".join([c[0] for row in table_grid for c in row if c[0]]).lower()
    is_sampling = (
        any(k in t_str for k in ("sample size", "acceptance no", "acceptance number", "aql", "lots size in bolts", "sampling plan"))
        and not any(k in t_str for k in ("breaking strength", "breaking load", "tensile strength", "ends per", "picks per", "mass, g", "weight in kg"))
    )
    assert is_sampling is True


def test_extract_key_value_requirements_cord_3600_n():
    """
    Unit test for key-value list parser using real verbatim lines from CORD 3600 N.pdf:
      NO.OF SPINDLES ---------------- 16
      NO. OF ENDS ------------------ 16
      NO.OF PLAITS/dm ------------ 36/37
      WEIGHT/Mtr. ---------------- 8.5 gms.(MAX).
      TEX OF YARN ---------------- 93.4 X 4.
      B.S. IN NEWTON ------------- 3600-N (MIN).
    """
    from services.spec_parser_textract import extract_key_value_requirements

    raw_lines = [
        ("The item:- Cordage Nylon Braided BS 3600 N Undyed is as per SPECIFICATION No. :-", 98.0),
        ("NO.OF SPINDLES ---------------- 16", 96.0),
        ("NO. OF ENDS ------------------ 16", 95.0),
        ("NO.OF PLAITS/dm ------------ 36/37", 94.0),
        ("WEIGHT/Mtr. ---------------- 8.5 gms.(MAX).", 95.0),
        ("TEX OF YARN ---------------- 93.4 X 4.", 93.0),
        ("B.S. IN NEWTON ------------- 3600-N (MIN).", 97.0),
    ]

    reqs = extract_key_value_requirements(raw_lines, confidence_threshold=90.0)
    assert len(reqs) == 6

    # Verify B.S. IN NEWTON
    bs_req = next(r for r in reqs if "B.S." in r["parameter"])
    assert bs_req["spec_value"] == 3600.0
    assert bs_req["unit"] == "N"
    assert bs_req["limit_type"] == "minimum"
    assert bs_req["is_critical"] is True
    assert bs_req["source"] == "key_value_list"

    # Verify WEIGHT/Mtr.
    wt_req = next(r for r in reqs if "WEIGHT" in r["parameter"])
    assert wt_req["spec_value"] == 8.5
    assert wt_req["limit_type"] == "maximum"
    assert wt_req["source"] == "key_value_list"

    # Verify confidence gating on low confidence line
    low_conf_lines = [("B.S. IN NEWTON ------------- 3600-N (MIN).", 82.0)]
    low_reqs = extract_key_value_requirements(low_conf_lines, confidence_threshold=90.0)
    assert len(low_reqs) == 1
    assert low_reqs[0]["spec_value"] is None
    assert low_reqs[0]["confidence_flag"] == "LOW_CONFIDENCE (82.0%)"
    assert low_reqs[0]["source"] == "key_value_list"


def test_extract_key_value_requirements_cord_250kg_core():
    """
    Unit test for key-value list parser using real verbatim lines from CORD 250KG  CORE.pdf:
      Braking Strength - 250 Kg (Minimum)
      No. Of Spindle - 16
      No. Of End - 48
      Plaits/DM - 37/48
      Denier - 840
      Length/Kg - 175m (Minimum)
    """
    from services.spec_parser_textract import extract_key_value_requirements

    raw_lines = [
        ("The item:- Cordage Nylon Coreless (BS 250 Kg) is as per SPECIFICATION :-", 98.0),
        ("Braking Strength - 250 Kg (Minimum)", 96.0),
        ("No. Of Spindle - 16", 95.0),
        ("No. Of End - 48", 95.0),
        ("Plaits/DM - 37/48", 94.0),
        ("Denier - 840", 97.0),
        ("Length/Kg - 175m (Minimum)", 95.0),
    ]

    reqs = extract_key_value_requirements(raw_lines, confidence_threshold=90.0)
    assert len(reqs) == 6

    brk_req = next(r for r in reqs if "Braking" in r["parameter"])
    assert brk_req["spec_value"] == 250.0
    assert brk_req["unit"] == "kgf"
    assert brk_req["limit_type"] == "minimum"
    assert brk_req["source"] == "key_value_list"

    den_req = next(r for r in reqs if "Denier" in r["parameter"])
    assert den_req["spec_value"] == 840.0
    assert den_req["source"] == "key_value_list"


def test_extract_narrative_override_real_clauses():
    """
    Unit test for narrative override parser using real verbatim clauses from:
    1. CLOTH DUCK POLYESTER BLENDED.pdf:
       "1.TEARING STRENGTH OF WARP WILL NOT BE LESS THAN 310N."
       "2.TEARING STRENGTH OF WEFT WILL NOT BE LESS THAN 250N."
    2. CLOTH PLAIN WEAVE KHAKI.pdf:
       "EXCEPT WIDTH SHALL BE 138 CMS"
       "WIDTH OF SELVEDGES SHALL NOT EXCEED 8 MM"
       "ROLL WEIGHT SHALL NOT EXCEED 50 KGS"
    3. CORD 3120 NBL OG.pdf:
       "1. The dye fastness to light ... shall be equal to class 5 or better ... IS:2454"
       "2. The dye fastness to washing ... shall be equal to class 4 or better"
    """
    from services.spec_parser_textract import extract_narrative_override_requirements

    # 1. Cloth Duck Polyester Blended
    lines_duck = [
        ("Specification: SPECN.NO.IS:13510-2000,V.NO.1 FIRST REVISION", 98.0),
        ("EXCEPT THE FOLLOWNG:-", 96.0),
        ("1.TEARING STRENGTH OF WARP WILL NOT BE LESS THAN 310N.", 95.0),
        ("2.TEARING STRENGTH OF WEFT WILL NOT BE LESS THAN 250N.", 95.0),
    ]
    reqs_duck = extract_narrative_override_requirements(lines_duck, confidence_threshold=90.0)
    assert len(reqs_duck) == 2
    warp_req = next(r for r in reqs_duck if "Warp" in r["parameter"])
    assert warp_req["spec_value"] == 310.0
    assert warp_req["unit"] == "N"
    assert warp_req["limit_type"] == "minimum"
    assert warp_req["source"] == "narrative_override"

    weft_req = next(r for r in reqs_duck if "Weft" in r["parameter"])
    assert weft_req["spec_value"] == 250.0
    assert weft_req["unit"] == "N"
    assert weft_req["limit_type"] == "minimum"
    assert weft_req["source"] == "narrative_override"

    # 2. Cloth Plain Weave Khaki
    lines_plain = [
        ("CLOTH PLAIN WEAVE POLYESTER & VISCOSE DOPE DYED KHAKI 138CM", 98.0),
        ("SPECIFICATION NO.IND/TC/0048 (g) EXCEPT WIDTH SHALL BE 138 CMS", 97.0),
        ("(3) WIDTH OF SELVEDGES SHALL NOT EXCEED 8 MM", 95.0),
        ("B) ROLL WEIGHT SHALL NOT EXCEED 50 KGS", 94.0),
    ]
    reqs_plain = extract_narrative_override_requirements(lines_plain, confidence_threshold=90.0)
    assert len(reqs_plain) == 3
    w_req = next(r for r in reqs_plain if r["parameter"] == "Width")
    assert w_req["spec_value"] == 138.0
    assert w_req["unit"] == "cms"
    assert w_req["source"] == "narrative_override"

    selv_req = next(r for r in reqs_plain if "Selvedges" in r["parameter"])
    assert selv_req["spec_value"] == 8.0
    assert selv_req["limit_type"] == "maximum"
    assert selv_req["unit"] == "mm"
    assert selv_req["source"] == "narrative_override"

    # 3. Cord 3120 NBL OG
    lines_cord = [
        ("CORD NYLON 3120 NBL O.G.", 98.0),
        ("Specification : JSS:4020 -9:2013 (Rev.No.3) except that", 96.0),
        ("1. The dye fastness to light when determined by exposure ... shall be equal to class 5 or better as per ISI specification no. IS:2454.", 95.0),
        ("2. The dye fastness to washing when determined as per ISI specification No. 764-1979 shall be equal to class 4 or better.", 95.0),
    ]
    reqs_cord = extract_narrative_override_requirements(lines_cord, confidence_threshold=90.0)
    assert len(reqs_cord) == 2
    light_req = next(r for r in reqs_cord if "Light" in r["parameter"])
    assert light_req["spec_value"] == 5.0
    assert light_req["limit_type"] == "minimum"
    assert light_req["source"] == "narrative_override"
    assert light_req["test_method"] == "IS:2454"


def test_parse_numeric_standard_citation_guards():
    """
    Verifies that standard citations (IS:764-79, 764-79, IS:1954, IS:2454)
    are treated as text/non-numeric and NEVER as numeric measurement ranges.
    Also verifies that real measurement ranges (0.040 - 0.070, 36/37, 10 - 40)
    continue to parse correctly as ranges.
    """
    from services.spec_parser_textract import parse_numeric_with_tol

    # False-positive citation cases must be treated as text
    citations = [
        "IS:764-79",
        "764-79.",
        "IS: 1954",
        "IS:2454",
        "4472(PT.I)-1967",
        "JSS:4020-9:2013",
        "IS:1422-1983",
        "6803-72",
    ]
    for cit in citations:
        v, tol, upper, l_type = parse_numeric_with_tol(cit)
        assert l_type == "text", f"Expected '{cit}' to be classified as 'text', got '{l_type}' (val={v}, upper={upper})"
        assert v is None
        assert upper is None

    # Genuine physical measurement ranges must still parse as range
    ranges = [
        ("0.040 - 0.070", 0.040, 0.070),
        ("0.040 to 0.070", 0.040, 0.070),
        ("36/37", 36.0, 37.0),
        ("10 - 40", 10.0, 40.0),
    ]
    for r_str, exp_low, exp_high in ranges:
        v, tol, upper, l_type = parse_numeric_with_tol(r_str)
        assert l_type == "range", f"Expected '{r_str}' to be 'range', got '{l_type}'"
        assert v == exp_low
        assert upper == exp_high


def test_extract_narrative_override_cloth_plain_weave_khaki():
    """
    Verifies narrative override extraction on real verbatim lines from CLOTH PLAIN WEAVE KHAKI.pdf:
    - Width: 138 cms (minimum)
    - Selvedge width: 8 mm (maximum)
    - Roll length: 40 meters (minimum)
    - Roll weight: 50 kgs (maximum)
    """
    from services.spec_parser_textract import extract_narrative_override_requirements

    verbatim_lines = [
        ("CLOTH PLAIN WEAVE POLYESTER & VISCOSE DOPE DYED KHAKI 138CM MINIMUM (EXCLUDING SELVEDGES)", 98.0),
        ("SPECIFICATION NO.IND/TC/0048 (g) EXCEPT WIDTH SHALL BE 138 CMS AND THAT COLOUR DEFINITION CONDITIONS...", 97.0),
        ("(1) FIRM SHALL GET NAME WOVEN...", 95.0),
        ("(2) SELVEDGE SHALL BE FIRM...", 95.0),
        ("(3) WIDTH OF SELVEDGES SHALL NOT EXCEED 8 MM", 96.0),
        ("(4) PACKING & MARKING...", 95.0),
        ("5) PACKING & MARKING... A) FABRIC IN ROLL FORM SHALL NOT BE LESS THAN 40 METERS... B) ROLL WEIGHT SHALL NOT EXCEED 50 KGS.", 96.0),
        ("D) DIMENSIONS FOR PAPER TUBES OF FABRIC ROLL: OUTER DIA-3.5 CM, INNER DIA-2.5 CM, THICKNESS-0.5 CM.", 94.0),
        ("7) SHORT LENGTH PIECES BETWEEN 10M TO 40M SHALL BE SUPPLIED / ACCEPTED TO THE EXTENT OF 5%...", 93.0),
    ]

    reqs = extract_narrative_override_requirements(verbatim_lines, confidence_threshold=90.0)
    assert len(reqs) == 4

    w_req = next(r for r in reqs if r["parameter"] == "Width")
    assert w_req["spec_value"] == 138.0
    assert w_req["unit"] == "cms"
    assert w_req["limit_type"] == "minimum"
    assert w_req["source"] == "narrative_override"

    selv_req = next(r for r in reqs if "Selvedges" in r["parameter"])
    assert selv_req["spec_value"] == 8.0
    assert selv_req["unit"] == "mm"
    assert selv_req["limit_type"] == "maximum"
    assert selv_req["source"] == "narrative_override"

    roll_len_req = next(r for r in reqs if "Roll" in r["parameter"] and r["unit"] == "m")
    assert roll_len_req["spec_value"] == 40.0
    assert roll_len_req["limit_type"] == "minimum"
    assert roll_len_req["source"] == "narrative_override"

    roll_wt_req = next(r for r in reqs if "Weight" in r["parameter"])
    assert roll_wt_req["spec_value"] == 50.0
    assert roll_wt_req["unit"] == "kg"
    assert roll_wt_req["limit_type"] == "maximum"
    assert roll_wt_req["source"] == "narrative_override"


def test_extract_narrative_override_cloth_duck_cotton_475():
    """
    Verifies narrative override extraction on real verbatim lines from CLOTH DUCK COTTON 475 GRM BLK.pdf:
    - Fastness to Washing: Rating 4 or better as per IS:764-79
    - Rot Proofing Agent: Copper or zinc naphth-nate can be used
    - Nature of Dye: Vat dye as per IS:4472(PT.I)-1967
    - Dye Fastness to Light: Carbon arc method also in case xenon arc is not available
    """
    from services.spec_parser_textract import extract_narrative_override_requirements

    verbatim_lines = [
        ("CLOTH DUCK COTTON 475 GRM. BLACK W.P, R.P. (VAT DYED) 91 CMS.", 98.0),
        ("Specification:IS:1422-1983 V.NO.3 WATER PROOFNESS TO IS:6803 LATEST EXCEPT THAT:-", 97.0),
        ("(i) DYE FASTNESS TO LIGHT MAY BE DETERMINED BY CARBON ARC METHOD ALSO IN CASE XENON ARC IS NOT AVAILABLE.", 95.0),
        ("(ii) IN APPEARANCE SHADE, FINISH AND IN OTHER RESPECT NOT DEF-INED IN ISI STANDARD, THE MATL. SHALL CONFORM TO THE SEALEDPATT.HELD IN THE CUSTODY OF CONTROLLER,CQA(T&C),KANPUR.", 94.0),
        ("(iii) B.L. SHRINKAGE, PROOFING CONTENT AND ROT PROOFNESS SHALL BE AS PER IS:6803-72. EXCEPT THAT (a) COPPER OR ZINC NAPHTH-NATE CAN BE USED AS ROT PROOFING AGENT. (b) NATURE OF DYE SHALL BE VAT DYE AS PER IS:4472(PT.I)-1967. (c) FASTNESS TO WASHING - RATING 4 OR BETTER AS PER IS:764-79.", 96.0),
        ("(iv) PACKING & MARKING: AS PER SCH.NO. CQA(T&C)/PMS/35(d) AND IN ADDITION THE FIRM SHALL GET THEIR NAME AND/OR TRADEMARK WOVEN ALONG SELVEDGE USING SAME COLOUR OF THREAD AS THAT OF THE FABRIC IN RUNNING LENGTH NOT EXCEEDING A DISTANCE OF 30 CMS BETWEEN TWO MARKINGS.", 93.0),
    ]

    reqs = extract_narrative_override_requirements(verbatim_lines, confidence_threshold=90.0)
    assert len(reqs) == 4

    wash_req = next(r for r in reqs if "Washing" in r["parameter"])
    assert wash_req["spec_value"] == 4.0
    assert wash_req["unit"] == "Rating"
    assert wash_req["limit_type"] == "minimum"
    assert wash_req["test_method"] == "IS:764-79"
    assert wash_req["source"] == "narrative_override"

    rot_req = next(r for r in reqs if "Proofing" in r["parameter"])
    assert rot_req["limit_type"] == "text"
    assert "Copper or zinc" in rot_req["text_value"]
    assert rot_req["source"] == "narrative_override"

    dye_req = next(r for r in reqs if "Dye" in r["parameter"] and "Nature" in r["parameter"])
    assert dye_req["limit_type"] == "text"
    assert "Vat" in dye_req["text_value"]
    assert "4472" in dye_req["test_method"]
    assert dye_req["source"] == "narrative_override"

    light_req = next(r for r in reqs if "Light" in r["parameter"])
    assert light_req["limit_type"] == "text"
    assert "Carbon arc" in light_req["text_value"]
    assert light_req["source"] == "narrative_override"


def test_reference_table_with_breaking_load_rejected():
    """
    Regression test: Related specifications list (e.g. pp rope 6mm og.pdf page 2)
    contains standard citations and phrases like 'breaking load' in the standard title,
    but must be recognized as a reference table and rejected from physical requirements.
    """
    table_grid = [
        [("(a)", 95.0), ("IS : 764 - 1979", 95.0), ("Method for determination of colour fastness of textile materials to washing.", 94.0)],
        [("(b)", 95.0), ("IS : 1912 - 1984", 95.0), ("Country Jute Twine.", 94.0)],
        [("(j)", 95.0), ("IS : 7071 - 1986 Pt IV", 95.0), ("Method for physical test for Ropes & Cordages breaking load and elongation at break.", 96.0)],
        [("(m)", 95.0), ("IND/TC/2123(b)", 94.0), ("Laminated Cloth Hessian.", 93.0)],
    ]
    t_str = " ".join([c[0] for row in table_grid for c in row if c[0]]).lower()
    
    # Check reference table filter rule
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
    assert is_reference_table is True


def test_bare_number_parameter_rejected():
    """
    Regression test: Grids where row values or serial numbers are parsed as parameters
    (e.g. '3.70', '1', '(a)', 'IS: 7071') must be rejected by parameter validation guards.
    """
    table_grid = [
        [("Param", 95.0), ("Value", 95.0)],
        [("3.70", 95.0), ("600", 95.0)],
        [("(a)", 95.0), ("Standard title", 95.0)],
        [("IS: 7071", 95.0), ("Test method title", 95.0)],
        [("pH", 95.0), ("5.5", 95.0)],
        [("Breaking Strength", 95.0), ("450", 95.0)],
    ]
    variants, requirements = parse_physical_table_with_confidence(table_grid, confidence_threshold=90.0)

    # Bare numbers, parenthetical letters, and standard citations must NOT become requirements
    param_names = [r["parameter"] for r in requirements]
    assert "3.70" not in param_names
    assert "(a)" not in param_names
    assert "IS: 7071" not in param_names

    # Legitimate parameters like 'pH' and 'Breaking Strength' must be retained
    assert "pH" in param_names
    assert "Breaking Strength" in param_names
    assert len(requirements) == 2


def test_horizontal_variant_rope_table():
    """
    Regression test: Grid matching pp rope 6mm og.pdf page 3 (horizontal/row-variant structure)
    where Column 0 is DIA (mm) and subsequent columns are physical parameters.
    Must extract variants for each diameter and map columns to correct parameters and values.
    """
    table_grid = [
        [("DIA (mm)", 98.0), ("Mass/Coil (kg)", 95.0), ("Linear Density (g/m)", 96.0), ("Breaking Load (Kgf)", 97.0)],
        [("6", 95.0), ("3.70", 95.0), ("17", 95.0), ("600", 95.0)],
        [("8", 95.0), ("6.60", 95.0), ("30", 95.0), ("1060", 95.0)],
        [("32", 95.0), ("101.00", 95.0), ("460", 95.0), ("13500", 95.0)],
    ]
    variants, requirements = parse_physical_table_with_confidence(table_grid, confidence_threshold=90.0)

    # Must extract 3 variants corresponding to the 3 diameter rows
    assert len(variants) == 3
    var_keys = [v["key"] for v in variants]
    assert "DIA_6" in var_keys
    assert "DIA_8" in var_keys
    assert "DIA_32" in var_keys

    # Each variant must have requirements for the parameter columns (Mass/Coil, Linear Density, Breaking Load)
    assert len(requirements) == 9

    # Check DIA 6 breaking load
    r_6_bl = next(r for r in requirements if "DIA_6" in r["variant_keys"] and "Breaking" in r["parameter"])
    assert r_6_bl["spec_value"] == 600.0
    assert r_6_bl["unit"] == "kgf"

    # Check DIA 32 breaking load
    r_32_bl = next(r for r in requirements if "DIA_32" in r["variant_keys"] and "Breaking" in r["parameter"])
    assert r_32_bl["spec_value"] == 13500.0
    assert r_32_bl["unit"] == "kgf"

    # Check DIA 6 Mass
    r_6_mass = next(r for r in requirements if "DIA_6" in r["variant_keys"] and "Mass" in r["parameter"])
    assert r_6_mass["spec_value"] == 3.70


def test_word_confidence_with_low_structure_confidence_flag():
    """
    Regression test for Fix 2:
    When cell geometry confidence is low (<70%) but word OCR confidence is high (>=90%),
    the value must be populated (NOT nullified as LOW_CONFIDENCE), but flagged with
    LOW_STRUCTURE_CONFIDENCE for human review.
    """
    # 3-tuple format: (text, word_conf, cell_geom_conf)
    table_grid = [
        [("Parameter", 99.0, 95.0), ("Requirement", 99.0, 95.0)],
        # Case 1: High word conf (98%), Low cell geometry conf (55%) -> POPULATED + LOW_STRUCTURE_CONFIDENCE
        [("Breaking Strength", 98.0, 92.0), ("450", 98.0, 55.0)],
        # Case 2: Both high -> POPULATED + NO FLAG
        [("Mass", 98.0, 95.0), ("120", 97.0, 94.0)],
        # Case 3: Low word conf (65%) -> LOW_CONFIDENCE + NULL VALUE
        [("Thickness", 98.0, 95.0), ("1.2", 65.0, 90.0)],
    ]
    variants, requirements = parse_physical_table_with_confidence(table_grid, confidence_threshold=90.0)

    assert len(requirements) == 3

    # Case 1: Breaking Strength
    bs_req = next(r for r in requirements if r["parameter"] == "Breaking Strength")
    assert bs_req["spec_value"] == 450.0
    assert bs_req["confidence_score"] == 98.0
    assert bs_req["confidence_flag"] == "LOW_STRUCTURE_CONFIDENCE (55.0%)"

    # Case 2: Mass
    mass_req = next(r for r in requirements if r["parameter"] == "Mass")
    assert mass_req["spec_value"] == 120.0
    assert mass_req["confidence_score"] == 97.0
    assert mass_req["confidence_flag"] is None

    # Case 3: Thickness
    thick_req = next(r for r in requirements if r["parameter"] == "Thickness")
    assert thick_req["spec_value"] is None
    assert thick_req["confidence_score"] == 65.0
    assert "LOW_CONFIDENCE" in thick_req["confidence_flag"]






