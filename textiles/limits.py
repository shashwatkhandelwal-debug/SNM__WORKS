"""
textiles/limits.py -- Verdict Logic for the Four Limit Kinds
Pure verdict evaluation against engineering and standard specifications.

The Four Limit Kinds:
1. minimum: actual >= spec
2. maximum: actual <= spec
3. range: spec <= actual <= upper
4. nominal: abs(actual - spec) <= tolerance
"""

from enum import Enum
from typing import Optional


class LimitKind(str, Enum):
    nominal = "nominal"
    minimum = "minimum"
    maximum = "maximum"
    range_ = "range"


def verdict(
    kind: LimitKind | str,
    spec: float,
    actual: float,
    tolerance: float = 0.0,
    upper: Optional[float] = None,
) -> str:
    """
    Computes a PASS or FAIL verdict according to standard textile limit definitions.
    
    :param kind: LimitKind enum or string ('minimum', 'maximum', 'range', 'nominal')
    :param spec: Lower limit or nominal target value
    :param actual: Observed specimen or batch reading
    :param tolerance: Symmetric absolute tolerance for nominal limits (+/- tolerance)
    :param upper: Upper bound required when kind is 'range'
    :return: 'PASS' or 'FAIL'
    """
    if isinstance(kind, str):
        kind_str = kind.lower().strip()
        if kind_str in ("min", "minimum"):
            kind_enum = LimitKind.minimum
        elif kind_str in ("max", "maximum"):
            kind_enum = LimitKind.maximum
        elif kind_str in ("range", "range_"):
            kind_enum = LimitKind.range_
        else:
            kind_enum = LimitKind.nominal
    else:
        kind_enum = kind

    if kind_enum == LimitKind.minimum:
        return "PASS" if actual >= spec else "FAIL"

    if kind_enum == LimitKind.maximum:
        return "PASS" if actual <= spec else "FAIL"

    if kind_enum == LimitKind.range_:
        upper_limit = upper if upper is not None else spec
        return "PASS" if (spec <= actual <= upper_limit) else "FAIL"

    # Nominal: actual within [spec - tolerance, spec + tolerance]
    return "PASS" if abs(actual - spec) <= abs(tolerance) else "FAIL"
