"""
textiles/units.py -- Textile Unit Conversions
Pure calculation library with exact scientific conversion constants.

Standard Reference Constants:
- 1 ounce (avoirdupois) = 28.349523125 grams (NIST SP 811)
- 1 yard = 0.9144 metres (International Yard and Pound Agreement 1959)
- 1 inch = 25.4 millimetres = 0.0254 metres
- 1 pound-force (lbf) = 4.4482216152605 Newtons
- 1 kilogram-force (kgf) = 9.80665 Newtons (Standard acceleration of gravity)
- Direct yarn systems: Denier = g / 9000m; Tex = g / 1000m; Decitex = g / 10000m
"""

# Mass per unit length
OZ_PER_YD_TO_GPM: float = 28.349523125 / 0.9144          # ~31.003487888 g/m per oz/yd
GPM_TO_OZ_PER_YD: float = 0.9144 / 28.349523125          # ~0.032254435 oz/yd per g/m

# Areal density
OZ_PER_YD2_TO_GSM: float = 28.349523125 / (0.9144 ** 2)  # ~33.90574744 g/m² per oz/yd²
GSM_TO_OZ_PER_YD2: float = (0.9144 ** 2) / 28.349523125  # ~0.029493525 oz/yd² per g/m²

# Length & Dimension
IN_TO_MM: float = 25.4
MM_TO_IN: float = 1.0 / 25.4
IN_TO_CM: float = 2.54
CM_TO_IN: float = 1.0 / 2.54

# Force / Breaking Strength
KGF_TO_LBF: float = 9.80665 / 4.4482216152605            # ~2.20462262185 lbf per kgf
LBF_TO_KGF: float = 4.4482216152605 / 9.80665            # ~0.45359237 kgf per lbf
KGF_TO_N: float = 9.80665
N_TO_KGF: float = 1.0 / 9.80665
LBF_TO_N: float = 4.4482216152605
N_TO_LBF: float = 1.0 / 4.4482216152605

# Yarn Count
TEX_TO_DENIER_FACTOR: float = 9.0
DTEX_TO_DENIER_FACTOR: float = 0.9


def oz_yd_to_gpm(oz_yd: float) -> float:
    """Converts linear density from ounces per yard to grams per metre."""
    return oz_yd * OZ_PER_YD_TO_GPM


def gpm_to_oz_yd(gpm: float) -> float:
    """Converts linear density from grams per metre to ounces per yard."""
    return gpm * GPM_TO_OZ_PER_YD


def oz_yd2_to_gsm(oz_yd2: float) -> float:
    """Converts areal density from ounces per square yard to grams per square metre."""
    return oz_yd2 * OZ_PER_YD2_TO_GSM


def gsm_to_oz_yd2(gsm: float) -> float:
    """Converts areal density from grams per square metre to ounces per square yard."""
    return gsm * GSM_TO_OZ_PER_YD2


def in_to_mm(inches: float) -> float:
    """Converts inches to millimetres."""
    return inches * IN_TO_MM


def mm_to_in(mm: float) -> float:
    """Converts millimetres to inches."""
    return mm * MM_TO_IN


def in_to_cm(inches: float) -> float:
    """Converts inches to centimetres."""
    return inches * IN_TO_CM


def cm_to_in(cm: float) -> float:
    """Converts centimetres to inches."""
    return cm * CM_TO_IN


def kgf_to_lbf(kgf: float) -> float:
    """Converts breaking strength from kilograms-force to pounds-force."""
    return kgf * KGF_TO_LBF


def lbf_to_kgf(lbf: float) -> float:
    """Converts breaking strength from pounds-force to kilograms-force."""
    return lbf * LBF_TO_KGF


def kgf_to_n(kgf: float) -> float:
    """Converts kilograms-force to Newtons."""
    return kgf * KGF_TO_N


def n_to_kgf(n: float) -> float:
    """Converts Newtons to kilograms-force."""
    return n * N_TO_KGF


def lbf_to_n(lbf: float) -> float:
    """Converts pounds-force to Newtons."""
    return lbf * LBF_TO_N


def n_to_lbf(n: float) -> float:
    """Converts Newtons to pounds-force."""
    return n * N_TO_LBF


def tex_to_denier(tex: float) -> float:
    """Converts linear density from Tex (g/1000m) to Denier (g/9000m)."""
    return tex * TEX_TO_DENIER_FACTOR


def denier_to_tex(denier: float) -> float:
    """Converts linear density from Denier (g/9000m) to Tex (g/1000m)."""
    return denier / TEX_TO_DENIER_FACTOR


def dtex_to_denier(dtex: float) -> float:
    """Converts linear density from Decitex (g/10000m) to Denier (g/9000m)."""
    return dtex * DTEX_TO_DENIER_FACTOR


def denier_to_dtex(denier: float) -> float:
    """Converts linear density from Denier (g/9000m) to Decitex (g/10000m)."""
    return denier / DTEX_TO_DENIER_FACTOR


def picks_per_cm_to_ppi(ppcm: float) -> float:
    """Converts picks per centimetre to picks per inch."""
    return ppcm * 2.54


def ppi_to_picks_per_cm(ppi: float) -> float:
    """Converts picks per inch to picks per centimetre."""
    return ppi / 2.54
