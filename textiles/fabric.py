"""
textiles/fabric.py -- Broad Technical Fabrics Calculations
Pure calculation library for broad woven fabrics.

Formulas:
- Ends per metre = EPI / 0.0254 = EPI * 39.37007874
- Warp GSM = EPI * 39.37007874 * Warp Denier * (1 + Warp Crimp / 100) / 9000
- Weft GSM = PPI * 39.37007874 * Weft Denier * (1 + Weft Crimp / 100) / 9000
- Total GSM = Warp GSM + Weft GSM
- Cover Factor (Peirce Formula in Denier system):
    Kw = EPI * sqrt(Warp Denier) / 28
    Kf = PPI * sqrt(Weft Denier) / 28
    Total Cover = Kw + Kf - (Kw * Kf) / 28
- Physical jamming limit: Cover factor > 28.0 in one direction is physically impossible.
"""

import math
from typing import Tuple
from textiles.units import gsm_to_oz_yd2


def warp_gsm(epi: float, warp_denier: float, warp_crimp_pct: float = 0.0) -> float:
    """
    Calculates warp areal density in grams per square metre (g/m²).
    
    :param epi: Warp ends per inch
    :param warp_denier: Warp yarn count in Denier
    :param warp_crimp_pct: Warp crimp percentage
    :return: Mass of warp in g/m²
    """
    if epi <= 0 or warp_denier <= 0:
        return 0.0
    crimp_factor = 1.0 + (max(warp_crimp_pct, 0.0) / 100.0)
    ends_per_m = epi * (1.0 / 0.0254)
    return (ends_per_m * warp_denier * crimp_factor) / 9000.0


def weft_gsm(ppi: float, weft_denier: float, weft_crimp_pct: float = 0.0) -> float:
    """
    Calculates weft areal density in grams per square metre (g/m²).
    
    :param ppi: Weft picks per inch
    :param weft_denier: Weft yarn count in Denier
    :param weft_crimp_pct: Weft crimp percentage
    :return: Mass of weft in g/m²
    """
    if ppi <= 0 or weft_denier <= 0:
        return 0.0
    crimp_factor = 1.0 + (max(weft_crimp_pct, 0.0) / 100.0)
    picks_per_m = ppi * (1.0 / 0.0254)
    return (picks_per_m * weft_denier * crimp_factor) / 9000.0


def total_fabric_gsm(
    warp_gsm_val: float,
    weft_gsm_val: float,
    finish_pickup_pct: float = 0.0,
) -> float:
    """
    Calculates total broad fabric weight in grams per square metre (g/m²).
    
    :param warp_gsm_val: Warp GSM
    :param weft_gsm_val: Weft GSM
    :param finish_pickup_pct: Chemical finish / coating add-on percentage (e.g. 5.0 for 5%)
    :return: Total finished GSM
    """
    base = max(warp_gsm_val, 0.0) + max(weft_gsm_val, 0.0)
    return base * (1.0 + (max(finish_pickup_pct, 0.0) / 100.0))


def total_fabric_oz_yd2(total_gsm_val: float) -> float:
    """Converts fabric GSM to ounces per square yard (oz/yd²)."""
    return gsm_to_oz_yd2(total_gsm_val)


def cover_factor_warp(epi: float, warp_denier: float) -> float:
    """
    Calculates fractional cover factor for warp direction (Kw).
    """
    if epi <= 0 or warp_denier <= 0:
        return 0.0
    return (epi * math.sqrt(warp_denier)) / 28.0


def cover_factor_weft(ppi: float, weft_denier: float) -> float:
    """
    Calculates fractional cover factor for weft direction (Kf).
    """
    if ppi <= 0 or weft_denier <= 0:
        return 0.0
    return (ppi * math.sqrt(weft_denier)) / 28.0


def cover_factor_total(
    epi: float,
    ppi: float,
    warp_denier: float,
    weft_denier: float,
) -> float:
    """
    Calculates total fabric cover factor (Kt) combining warp and weft.
    Kt = Kw + Kf - (Kw * Kf) / 28
    """
    kw = cover_factor_warp(epi, warp_denier)
    kf = cover_factor_weft(ppi, weft_denier)
    if kw == 0.0 and kf == 0.0:
        return 0.0
    return kw + kf - ((kw * kf) / 28.0)


def check_cover_factor_jamming(
    epi: float,
    ppi: float,
    warp_denier: float,
    weft_denier: float,
) -> Tuple[bool, str]:
    """
    Checks whether warp or weft cover factor exceeds the physical jamming limit of 28.0.
    
    :return: (is_jammed, message)
    """
    kw = cover_factor_warp(epi, warp_denier)
    kf = cover_factor_weft(ppi, weft_denier)
    
    if kw > 28.0:
        return True, f"Warp cover factor ({kw:.2f}) exceeds physical limit of 28.0. Yarn jamming prevents flat weaving."
    if kf > 28.0:
        return True, f"Weft cover factor ({kf:.2f}) exceeds physical limit of 28.0. Yarn jamming prevents flat weaving."
    return False, "Cover factor within physically constructible limits."
