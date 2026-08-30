"""
textiles/cordage.py — Rope, Cord, and Braided Structures Calculations
Pure calculation library for round braids, ropes, and cordage.

Formulas:
- Sheath GPM = Carriers * Yarns/Carrier * Yarn Denier * (1 + Contraction% / 100) / 9000
- Core GPM = Core Yarns * Core Denier * (1 + Core Contraction% / 100) / 9000
- Total Cord GPM = Sheath GPM + Core GPM
- Theoretical Break (kgf) = Total Load-Bearing Ends * Denier * Tenacity * (Efficiency% / 100) / 1000
"""

from textiles.units import kgf_to_lbf, gpm_to_oz_yd


def braid_sheath_gpm(
    carriers: int,
    yarns_per_carrier: int,
    yarn_denier: float,
    contraction_pct: float = 0.0,
) -> float:
    """
    Calculates mass per metre of braided sheath / sleeve in grams (g/m).
    
    :param carriers: Number of braiding bobbin carriers (e.g. 16, 24, 32, 48)
    :param yarns_per_carrier: Number of yarn ends wound parallel on each carrier
    :param yarn_denier: Linear density of sheath yarn in Denier
    :param contraction_pct: Braiding take-up / helix contraction percentage (e.g. 15.0 for 15%)
    :return: Mass in grams per metre
    """
    if carriers <= 0 or yarns_per_carrier <= 0 or yarn_denier <= 0:
        return 0.0
    total_sheath_ends = carriers * yarns_per_carrier
    contraction_factor = 1.0 + (max(contraction_pct, 0.0) / 100.0)
    return (total_sheath_ends * yarn_denier * contraction_factor) / 9000.0


def braid_core_gpm(
    core_yarns: int,
    core_denier: float,
    core_contraction_pct: float = 0.0,
) -> float:
    """
    Calculates mass per metre of parallel or twisted core yarns in grams (g/m).
    
    :param core_yarns: Number of core ends running in the center of the braid
    :param core_denier: Linear density of core yarn in Denier
    :param core_contraction_pct: Core twist / crimp contraction percentage (e.g. 2.0 for 2%)
    :return: Mass in grams per metre
    """
    if core_yarns <= 0 or core_denier <= 0:
        return 0.0
    contraction_factor = 1.0 + (max(core_contraction_pct, 0.0) / 100.0)
    return (core_yarns * core_denier * contraction_factor) / 9000.0


def total_cord_gpm(
    sheath_gpm_val: float,
    core_gpm_val: float,
) -> float:
    """Calculates total cord mass per linear metre in grams (g/m)."""
    return max(sheath_gpm_val, 0.0) + max(core_gpm_val, 0.0)


def total_cord_oz_yd(total_gpm_val: float) -> float:
    """Converts cord GPM to ounces per linear yard."""
    return gpm_to_oz_yd(total_gpm_val)


def theoretical_cord_break_kgf(
    carriers: int,
    yarns_per_carrier: int,
    sheath_denier: float,
    sheath_tenacity: float,
    core_yarns: int = 0,
    core_denier: float = 0.0,
    core_tenacity: float = 0.0,
    braid_efficiency_pct: float = 75.0,
) -> float:
    """
    Calculates theoretical cord tensile breaking strength in kilograms-force (kgf).
    
    :param carriers: Braiding carriers
    :param yarns_per_carrier: Ends per carrier
    :param sheath_denier: Denier of sheath yarns
    :param sheath_tenacity: Tenacity of sheath yarn in g/denier
    :param core_yarns: Number of core yarns
    :param core_denier: Denier of core yarn
    :param core_tenacity: Tenacity of core yarn in g/denier
    :param braid_efficiency_pct: Helix angle translation efficiency percentage (typically 70-80%)
    :return: Predicted breaking strength in kgf
    """
    eff = max(braid_efficiency_pct, 0.0) / 100.0
    sheath_strength_g = (carriers * yarns_per_carrier) * sheath_denier * sheath_tenacity * eff
    core_strength_g = core_yarns * core_denier * core_tenacity * (0.95 if core_yarns > 0 else 0.0)
    total_g = sheath_strength_g + core_strength_g
    return total_g / 1000.0


def theoretical_cord_break_lbf(
    carriers: int,
    yarns_per_carrier: int,
    sheath_denier: float,
    sheath_tenacity: float,
    core_yarns: int = 0,
    core_denier: float = 0.0,
    core_tenacity: float = 0.0,
    braid_efficiency_pct: float = 75.0,
) -> float:
    """Calculates theoretical cord tensile breaking strength in pounds-force (lbf)."""
    kgf = theoretical_cord_break_kgf(
        carriers,
        yarns_per_carrier,
        sheath_denier,
        sheath_tenacity,
        core_yarns,
        core_denier,
        core_tenacity,
        braid_efficiency_pct,
    )
    return kgf_to_lbf(kgf)
