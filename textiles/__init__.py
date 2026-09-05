"""
textiles -- Pure Textile Calculation and Units Library for Swadeshi Niwar Mills
No framework or database imports. All mathematical calculations are pure functions.
"""

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

__all__ = [
    # Units
    "oz_yd_to_gpm",
    "gpm_to_oz_yd",
    "oz_yd2_to_gsm",
    "gsm_to_oz_yd2",
    "in_to_mm",
    "mm_to_in",
    "in_to_cm",
    "cm_to_in",
    "kgf_to_lbf",
    "lbf_to_kgf",
    "kgf_to_n",
    "n_to_kgf",
    "lbf_to_n",
    "n_to_lbf",
    "tex_to_denier",
    "denier_to_tex",
    "dtex_to_denier",
    "denier_to_dtex",
    "picks_per_cm_to_ppi",
    "ppi_to_picks_per_cm",
    # Narrow
    "warp_gpm",
    "weft_gpm",
    "total_narrow_gpm",
    "total_narrow_oz_yd",
    "theoretical_break_kgf",
    "theoretical_break_lbf",
    # Fabric
    "warp_gsm",
    "weft_gsm",
    "total_fabric_gsm",
    "total_fabric_oz_yd2",
    "cover_factor_warp",
    "cover_factor_weft",
    "cover_factor_total",
    "check_cover_factor_jamming",
    # Cordage
    "braid_sheath_gpm",
    "braid_core_gpm",
    "total_cord_gpm",
    "total_cord_oz_yd",
    "theoretical_cord_break_kgf",
    "theoretical_cord_break_lbf",
    # Limits
    "LimitKind",
    "verdict",
]
