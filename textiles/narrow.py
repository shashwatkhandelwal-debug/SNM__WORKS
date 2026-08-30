"""
textiles/narrow.py — Narrow Wovens (Webbing, Tape, Slings) Calculations
Pure calculation library for narrow fabrics. No external framework imports.

Formulas:
- 1 end of D denier weighs D/9000 g/m.
- Warp GPM = Ends * Warp Denier * (1 + Warp Crimp / 100) / 9000
- Weft GPM = Picks/cm * 100 * (Width_mm / 1000) * Weft Denier * (1 + Weft Crimp / 100) / 9000
- Theoretical Break (kgf) = Ends * Denier * Tenacity (g/den) * (Efficiency % / 100) / 1000
- Theoretical Break (lbf) = Break (kgf) * 2.20462262
"""

from typing import Optional
from textiles.units import kgf_to_lbf, gpm_to_oz_yd


def warp_gpm(ends: int, warp_denier: float, warp_crimp_pct: float = 0.0) -> float:
    """
    Calculates warp yarn consumption in grams per linear metre (g/m).
    
    :param ends: Total number of warp ends in the cross-section
    :param warp_denier: Linear density of warp yarn in Denier (g/9000m)
    :param warp_crimp_pct: Warp crimp / take-up percentage (e.g. 5.0 for 5%)
    :return: Mass of warp per metre in grams
    """
    if ends <= 0 or warp_denier <= 0:
        return 0.0
    crimp_factor = 1.0 + (max(warp_crimp_pct, 0.0) / 100.0)
    return (ends * warp_denier * crimp_factor) / 9000.0


def weft_gpm(
    picks_per_cm: float,
    width_mm: float,
    weft_denier: float,
    weft_crimp_pct: float = 0.0,
) -> float:
    """
    Calculates weft (filling) yarn consumption in grams per linear metre (g/m).
    
    :param picks_per_cm: Number of weft picks inserted per centimetre of tape length
    :param width_mm: Fabric tape width in millimetres
    :param weft_denier: Linear density of weft yarn in Denier (g/9000m)
    :param weft_crimp_pct: Weft crimp / width shrinkage percentage (e.g. 3.0 for 3%)
    :return: Mass of weft per linear metre in grams
    """
    if picks_per_cm <= 0 or width_mm <= 0 or weft_denier <= 0:
        return 0.0
    width_m = width_mm / 1000.0
    picks_per_m = picks_per_cm * 100.0
    crimp_factor = 1.0 + (max(weft_crimp_pct, 0.0) / 100.0)
    return (picks_per_m * width_m * weft_denier * crimp_factor) / 9000.0


def total_narrow_gpm(
    warp_gpm_val: float,
    weft_gpm_val: float,
    selvedge_gpm_val: float = 0.0,
) -> float:
    """Calculates total weight per linear metre in grams (g/m)."""
    return max(warp_gpm_val, 0.0) + max(weft_gpm_val, 0.0) + max(selvedge_gpm_val, 0.0)


def total_narrow_oz_yd(total_gpm_val: float) -> float:
    """Converts total narrow fabric weight to ounces per linear yard."""
    return gpm_to_oz_yd(total_gpm_val)


def theoretical_break_kgf(
    ends: int,
    denier: float,
    tenacity_g_per_den: float,
    efficiency_pct: float = 85.0,
) -> float:
    """
    Calculates theoretical warp tensile breaking strength in kilograms-force (kgf).
    
    :param ends: Total number of load-bearing warp ends
    :param denier: Linear density of yarn in Denier
    :param tenacity_g_per_den: Single yarn tenacity in grams-force per denier (e.g. 8.5)
    :param efficiency_pct: Fabric conversion efficiency percentage (e.g. 85.0 for 85%)
    :return: Predicted breaking strength in kgf
    """
    if ends <= 0 or denier <= 0 or tenacity_g_per_den <= 0:
        return 0.0
    eff_factor = max(efficiency_pct, 0.0) / 100.0
    # Total yarn strength in grams = ends * denier * tenacity
    total_g = ends * denier * tenacity_g_per_den * eff_factor
    return total_g / 1000.0


def theoretical_break_lbf(
    ends: int,
    denier: float,
    tenacity_g_per_den: float,
    efficiency_pct: float = 85.0,
) -> float:
    """Calculates theoretical warp tensile breaking strength in pounds-force (lbf)."""
    kgf = theoretical_break_kgf(ends, denier, tenacity_g_per_den, efficiency_pct)
    return kgf_to_lbf(kgf)
