"""
tests/test_textiles.py — Comprehensive Pytest Suite for Pure Textiles Library
Tests against known standard textile reference values, MIL-W-4088K specifications,
jamming limits, and boundary conditions.
"""

import math
import pytest

from textiles.units import (
    oz_yd_to_gpm,
    gpm_to_oz_yd,
    oz_yd2_to_gsm,
    gsm_to_oz_yd2,
    in_to_mm,
    mm_to_in,
    in_to_cm,
    cm_to_in,
    kgf_to_lbf,
    lbf_to_kgf,
    kgf_to_n,
    n_to_kgf,
    lbf_to_n,
    n_to_lbf,
    tex_to_denier,
    denier_to_tex,
    dtex_to_denier,
    denier_to_dtex,
    picks_per_cm_to_ppi,
    ppi_to_picks_per_cm,
)

from textiles.narrow import (
    warp_gpm,
    weft_gpm,
    total_narrow_gpm,
    total_narrow_oz_yd,
    theoretical_break_kgf,
    theoretical_break_lbf,
)

from textiles.fabric import (
    warp_gsm,
    weft_gsm,
    total_fabric_gsm,
    total_fabric_oz_yd2,
    cover_factor_warp,
    cover_factor_weft,
    cover_factor_total,
    check_cover_factor_jamming,
)

from textiles.cordage import (
    braid_sheath_gpm,
    braid_core_gpm,
    total_cord_gpm,
    total_cord_oz_yd,
    theoretical_cord_break_kgf,
    theoretical_cord_break_lbf,
)

from textiles.limits import (
    LimitKind,
    verdict,
)


# ============================================================================
# 1. UNIT CONVERSIONS: KNOWN REFERENCE STANDARDS & SYMMETRY
# ============================================================================

def test_unit_conversions_against_standard_reference_values():
    """
    Verifies unit conversions against known published physical standard values:
    - 1.60 oz/yd (MIL-W-4088K Type VIII max linear density) = 49.60558 g/m
    - 10.00 oz/yd² (technical canvas) = 339.05747 g/m²
    - 840 Denier (Type VIII Class 1 yarn) = 93.3333 Tex = 933.333 Decitex
    - 1.71875 in (Type VIII nominal width: 1-23/32 in) = 43.65625 mm
    - 4,000 lbf (Type VIII breaking strength floor) = 1,814.36948 kgf = 17,792.886 N
    """
    # 1. Linear density: 1.60 oz/yd -> g/m
    gpm = oz_yd_to_gpm(1.60)
    assert pytest.approx(gpm, rel=1e-4) == 49.6056

    # 2. Areal density: 10.0 oz/yd² -> g/m²
    gsm = oz_yd2_to_gsm(10.0)
    assert pytest.approx(gsm, rel=1e-4) == 339.0575

    # 3. Direct yarn numbering: 840 denier -> tex & dtex
    tex = denier_to_tex(840.0)
    assert pytest.approx(tex, rel=1e-5) == 93.33333
    dtex = denier_to_dtex(840.0)
    assert pytest.approx(dtex, rel=1e-5) == 933.3333

    tex_back = tex_to_denier(93.333333)
    assert pytest.approx(tex_back, rel=1e-4) == 840.0

    # 4. Length: 1.71875 inches -> mm
    mm = in_to_mm(1.71875)
    assert pytest.approx(mm, rel=1e-5) == 43.65625

    # 5. Force / Strength: 4,000 lbf -> kgf & N
    kgf = lbf_to_kgf(4000.0)
    assert pytest.approx(kgf, rel=1e-5) == 1814.3695
    newtons = lbf_to_n(4000.0)
    assert pytest.approx(newtons, rel=1e-5) == 17792.8864

    # 6. Weave density: 7.087 picks/cm = 18.0 picks/inch
    ppcm = ppi_to_picks_per_cm(18.0)
    assert pytest.approx(ppcm, rel=1e-4) == 7.0866
    assert pytest.approx(picks_per_cm_to_ppi(ppcm), rel=1e-4) == 18.0


@pytest.mark.parametrize(
    "val,to_fn,from_fn",
    [
        (1.60, oz_yd_to_gpm, gpm_to_oz_yd),
        (350.0, oz_yd2_to_gsm, gsm_to_oz_yd2),
        (45.0, in_to_mm, mm_to_in),
        (12.5, in_to_cm, cm_to_in),
        (1500.0, kgf_to_lbf, lbf_to_kgf),
        (5000.0, kgf_to_n, n_to_kgf),
        (4448.0, lbf_to_n, n_to_lbf),
        (840.0, denier_to_tex, tex_to_denier),
        (840.0, denier_to_dtex, dtex_to_denier),
        (18.0, ppi_to_picks_per_cm, picks_per_cm_to_ppi),
    ],
)
def test_unit_conversions_roundtrip_symmetry(val, to_fn, from_fn):
    """
    Proves round-trip symmetry across all unit conversion functions.
    """
    converted = to_fn(val)
    restored = from_fn(converted)
    assert pytest.approx(restored, rel=1e-7) == val


# ============================================================================
# 2. NARROW WOVENS & MIL-W-4088K TYPE VIII VERIFICATION
# ============================================================================

def test_mil_w_4088k_type_viii_theoretical_break_calculation():
    """
    AGENTS.md mandatory test:
    MIL-W-4088K Type VIII:
      - 320 ends total (160 Face + 160 Back in double herringbone twill)
      - 840 denier Class 1 Nylon yarn
      - 8.5 g/denier high-tenacity yarn
      - 85.0% loom structure efficiency
    Expected result: approximately 4,282 lbf (>= 4,000 lbf floor specification).
    """
    ends = 320
    denier = 840.0
    tenacity = 8.5
    efficiency = 85.0

    break_kgf = theoretical_break_kgf(ends, denier, tenacity, efficiency)
    break_lbf = theoretical_break_lbf(ends, denier, tenacity, efficiency)

    # 320 * 840 * 8.5 * 0.85 = 1,942,080 g = 1,942.08 kgf
    assert pytest.approx(break_kgf, rel=1e-4) == 1942.08
    # 1,942.08 kgf * 2.20462262 = 4,281.55 lbf
    assert pytest.approx(break_lbf, rel=1e-3) == 4281.55
    assert break_lbf >= 4000.0, "Theoretical break must exceed 4,000 lbf minimum spec!"


def test_narrow_wovens_weight_per_metre():
    """
    Verifies warp, weft and total GPM for narrow webbing:
    - 320 ends of 840 denier with 5% warp crimp
    - 18 picks/inch (7.0866 picks/cm), width 43.66 mm, 840 denier weft with 3% crimp
    """
    w_gpm = warp_gpm(ends=320, warp_denier=840.0, warp_crimp_pct=5.0)
    # (320 * 840 * 1.05) / 9000 = 31.36 g/m
    assert pytest.approx(w_gpm, rel=1e-4) == 31.36

    ppcm = ppi_to_picks_per_cm(18.0)
    f_gpm = weft_gpm(picks_per_cm=ppcm, width_mm=43.65625, weft_denier=840.0, weft_crimp_pct=3.0)
    # Picks/m = 708.66; width_m = 0.043656; weft_m = 30.938 m/m of tape; mass = 30.938 * 840 * 1.03 / 9000 = 2.973 g/m
    assert pytest.approx(f_gpm, rel=1e-3) == 2.973

    total_gpm = total_narrow_gpm(w_gpm, f_gpm)
    assert pytest.approx(total_gpm, rel=1e-3) == 34.333
    total_oz_yd = total_narrow_oz_yd(total_gpm)
    assert total_oz_yd <= 1.60  # Below 1.60 oz/yd Type VIII limit


# ============================================================================
# 3. BROAD TECHNICAL FABRICS & COVER FACTOR JAMMING
# ============================================================================

def test_broad_fabric_gsm_and_cover_factor():
    """
    Tests broad fabric GSM and Peirce cover factor:
    - 60 EPI, 50 PPI, 210 Denier nylon, 4% crimp warp and weft
    """
    w_gsm = warp_gsm(epi=60.0, warp_denier=210.0, warp_crimp_pct=4.0)
    f_gsm = weft_gsm(ppi=50.0, weft_denier=210.0, weft_crimp_pct=4.0)
    total_gsm = total_fabric_gsm(w_gsm, f_gsm)

    # EPI 60 = 2362.2 ends/m; 2362.2 * 210 * 1.04 / 9000 = 57.31 g/m²
    assert pytest.approx(w_gsm, rel=1e-3) == 57.311
    # PPI 50 = 1968.5 picks/m; 1968.5 * 210 * 1.04 / 9000 = 47.76 g/m²
    assert pytest.approx(f_gsm, rel=1e-3) == 47.759
    assert pytest.approx(total_gsm, rel=1e-3) == 105.070

    # Cover factors
    kw = cover_factor_warp(60.0, 210.0)
    kf = cover_factor_weft(50.0, 210.0)
    kt = cover_factor_total(60.0, 50.0, 210.0, 210.0)

    # Kw = 60 * sqrt(210) / 28 = 60 * 14.49137 / 28 = 31.05 -> Jammed!
    assert pytest.approx(kw, rel=1e-3) == 31.053
    assert pytest.approx(kf, rel=1e-3) == 25.877
    assert kt > 28.0

    is_jammed, msg = check_cover_factor_jamming(60.0, 50.0, 210.0, 210.0)
    assert is_jammed is True
    assert "exceeds physical limit of 28.0" in msg


def test_cover_factor_jamming_detection():
    """
    AGENTS.md requirement: Cover factor above 28 in one direction flags physically impossible.
    """
    # Physically constructible fabric (e.g. 28 EPI x 24 PPI x 150 Denier)
    is_jammed_valid, _ = check_cover_factor_jamming(28.0, 24.0, 150.0, 150.0)
    assert is_jammed_valid is False

    # Jammed warp: 100 EPI x 840 Denier -> Kw = 100 * sqrt(840) / 28 = 103.5 > 28
    is_jammed_warp, msg_w = check_cover_factor_jamming(100.0, 20.0, 840.0, 840.0)
    assert is_jammed_warp is True
    assert "Warp cover factor" in msg_w

    # Jammed weft: 20 EPI x 100 PPI x 840 Denier -> Kf = 100 * sqrt(840) / 28 = 103.5 > 28
    is_jammed_weft, msg_f = check_cover_factor_jamming(20.0, 100.0, 840.0, 840.0)
    assert is_jammed_weft is True
    assert "Weft cover factor" in msg_f


# ============================================================================
# 4. CORDAGE & BRAIDED STRUCTURES
# ============================================================================

def test_cordage_braid_gpm_and_strength():
    """
    Tests 16-carrier round braid with 3 ends/carrier and 4 core yarns:
    - Sheath: 16 carriers * 3 ends = 48 ends of 1000 Denier, 15% contraction
    - Core: 4 ends of 1000 Denier, 2% contraction
    """
    s_gpm = braid_sheath_gpm(carriers=16, yarns_per_carrier=3, yarn_denier=1000.0, contraction_pct=15.0)
    # (48 * 1000 * 1.15) / 9000 = 6.133 g/m
    assert pytest.approx(s_gpm, rel=1e-3) == 6.133

    c_gpm = braid_core_gpm(core_yarns=4, core_denier=1000.0, core_contraction_pct=2.0)
    # (4 * 1000 * 1.02) / 9000 = 0.4533 g/m
    assert pytest.approx(c_gpm, rel=1e-3) == 0.4533

    tot_gpm = total_cord_gpm(s_gpm, c_gpm)
    assert pytest.approx(tot_gpm, rel=1e-3) == 6.5867

    # Strength with 8.0 g/den tenacity and 75% braid efficiency
    break_kgf = theoretical_cord_break_kgf(
        carriers=16,
        yarns_per_carrier=3,
        sheath_denier=1000.0,
        sheath_tenacity=8.0,
        core_yarns=4,
        core_denier=1000.0,
        core_tenacity=8.0,
        braid_efficiency_pct=75.0,
    )
    # Sheath: 48 * 1000 * 8 * 0.75 / 1000 = 288 kgf; Core: 4 * 1000 * 8 * 0.95 / 1000 = 30.4 kgf -> Total = 318.4 kgf
    assert pytest.approx(break_kgf, rel=1e-3) == 318.4

    break_lbf = theoretical_cord_break_lbf(
        carriers=16,
        yarns_per_carrier=3,
        sheath_denier=1000.0,
        sheath_tenacity=8.0,
        core_yarns=4,
        core_denier=1000.0,
        core_tenacity=8.0,
        braid_efficiency_pct=75.0,
    )
    assert pytest.approx(break_lbf, rel=1e-3) == 701.95


# ============================================================================
# 5. THE FOUR LIMIT KINDS & BOUNDARY CONDITIONS
# ============================================================================

def test_verdict_limit_minimum():
    """LimitKind.minimum: actual >= spec."""
    assert verdict(LimitKind.minimum, spec=4000.0, actual=4000.0) == "PASS"  # Exact boundary
    assert verdict(LimitKind.minimum, spec=4000.0, actual=4000.1) == "PASS"
    assert verdict(LimitKind.minimum, spec=4000.0, actual=3999.9) == "FAIL"


def test_verdict_limit_maximum():
    """LimitKind.maximum: actual <= spec."""
    assert verdict(LimitKind.maximum, spec=1.60, actual=1.60) == "PASS"    # Exact boundary
    assert verdict(LimitKind.maximum, spec=1.60, actual=1.599) == "PASS"
    assert verdict(LimitKind.maximum, spec=1.60, actual=1.6001) == "FAIL"


def test_verdict_limit_range():
    """LimitKind.range_: spec <= actual <= upper."""
    assert verdict(LimitKind.range_, spec=40.0, actual=40.0, upper=45.0) == "PASS"  # Lower boundary
    assert verdict(LimitKind.range_, spec=40.0, actual=45.0, upper=45.0) == "PASS"  # Upper boundary
    assert verdict(LimitKind.range_, spec=40.0, actual=42.5, upper=45.0) == "PASS"  # Inside
    assert verdict(LimitKind.range_, spec=40.0, actual=39.99, upper=45.0) == "FAIL"
    assert verdict(LimitKind.range_, spec=40.0, actual=45.01, upper=45.0) == "FAIL"


def test_verdict_limit_nominal():
    """LimitKind.nominal: abs(actual - spec) <= tolerance."""
    assert verdict(LimitKind.nominal, spec=43.65, actual=43.65, tolerance=1.50) == "PASS"
    assert verdict(LimitKind.nominal, spec=43.65, actual=45.15, tolerance=1.50) == "PASS"  # +tol boundary
    assert verdict(LimitKind.nominal, spec=43.65, actual=42.15, tolerance=1.50) == "PASS"  # -tol boundary
    assert verdict(LimitKind.nominal, spec=43.65, actual=45.16, tolerance=1.50) == "FAIL"
    assert verdict(LimitKind.nominal, spec=43.65, actual=42.14, tolerance=1.50) == "FAIL"
