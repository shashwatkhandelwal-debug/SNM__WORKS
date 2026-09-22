"""
services/spec_diff.py — Specification Diffing & Comparison Engine.

Provides clean comparison utilities for:
1. Variant vs Variant requirement sets (effective parameter matrix with field diffs).
2. Revision vs Revision master specification diffs (metadata header diff, variant additions/removals, requirement diffs).
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional


def _normalize_val(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (Decimal, float, int)):
        return float(val)
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, bool):
        return val
    return val


def _field_equal(v1: Any, v2: Any) -> bool:
    n1 = _normalize_val(v1)
    n2 = _normalize_val(v2)
    return n1 == n2


COMPARE_FIELDS = [
    "limit_type",
    "spec_value",
    "tolerance",
    "upper_limit",
    "unit",
    "text_value",
    "test_method",
    "is_critical",
]


def compare_variant_requirements(
    reqs_a: List[Dict[str, Any]],
    reqs_b: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Performs full outer join by parameter on two effective requirement sets.
    Returns sorted list of parameter comparison items.
    """
    map_a: Dict[str, Dict[str, Any]] = {}
    for r in reqs_a:
        key = (r.get("parameter") or "").strip().lower()
        if key:
            map_a[key] = dict(r)

    map_b: Dict[str, Dict[str, Any]] = {}
    for r in reqs_b:
        key = (r.get("parameter") or "").strip().lower()
        if key:
            map_b[key] = dict(r)

    all_keys = sorted(set(map_a.keys()) | set(map_b.keys()))
    results: List[Dict[str, Any]] = []

    for key in all_keys:
        item_a = map_a.get(key)
        item_b = map_b.get(key)

        param_display = (item_a.get("parameter") if item_a else None) or (item_b.get("parameter") if item_b else key)
        sort_order = (item_a.get("sort_order") if item_a and item_a.get("sort_order") is not None else 999)
        if item_b and item_b.get("sort_order") is not None and (item_a is None or item_a.get("sort_order") is None):
            sort_order = item_b.get("sort_order")

        if item_a and not item_b:
            results.append({
                "parameter": param_display,
                "status": "only_in_a",
                "a": item_a,
                "b": None,
                "diff_fields": [],
                "sort_order": sort_order,
            })
        elif item_b and not item_a:
            results.append({
                "parameter": param_display,
                "status": "only_in_b",
                "a": None,
                "b": item_b,
                "diff_fields": [],
                "sort_order": sort_order,
            })
        else:
            diff_fields = []
            for field in COMPARE_FIELDS:
                if not _field_equal(item_a.get(field), item_b.get(field)):
                    diff_fields.append(field)

            status = "same" if not diff_fields else "changed"
            results.append({
                "parameter": param_display,
                "status": status,
                "a": item_a,
                "b": item_b,
                "diff_fields": diff_fields,
                "sort_order": sort_order,
            })

    results.sort(key=lambda x: (x["sort_order"] if x["sort_order"] is not None else 999, x["parameter"]))
    return results


HEADER_FIELDS = [
    ("title", "Title"),
    ("issuing_body", "Issuing Authority"),
    ("issued_on", "Date of Issue"),
    ("supersedes", "Supersedes"),
    ("distribution", "Distribution"),
    ("scope", "Scope"),
    ("notes", "Notes"),
    ("active", "Active Status"),
]


def compare_specification_revisions(
    spec_a: Dict[str, Any],
    spec_b: Dict[str, Any],
    variants_a: List[Dict[str, Any]],
    variants_b: List[Dict[str, Any]],
    reqs_a: List[Dict[str, Any]],
    reqs_b: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Compares two revisions of a specification:
    1. Header metadata field diffs.
    2. Variant additions / removals / common.
    3. Requirement comparisons.
    """
    header_diffs = []
    for field_key, field_label in HEADER_FIELDS:
        val_a = spec_a.get(field_key)
        val_b = spec_b.get(field_key)
        is_diff = not _field_equal(val_a, val_b)
        header_diffs.append({
            "field": field_key,
            "label": field_label,
            "val_a": val_a,
            "val_b": val_b,
            "changed": is_diff,
        })

    var_map_a = {(v.get("designation") or "").strip().lower(): dict(v) for v in variants_a}
    var_map_b = {(v.get("designation") or "").strip().lower(): dict(v) for v in variants_b}

    all_var_keys = sorted(set(var_map_a.keys()) | set(var_map_b.keys()))
    variant_diffs = []
    for vk in all_var_keys:
        va = var_map_a.get(vk)
        vb = var_map_b.get(vk)
        desig = (va.get("designation") if va else None) or (vb.get("designation") if vb else vk)
        if va and not vb:
            variant_diffs.append({"designation": desig, "status": "removed_in_b", "a": va, "b": None})
        elif vb and not va:
            variant_diffs.append({"designation": desig, "status": "added_in_b", "a": None, "b": vb})
        else:
            variant_diffs.append({"designation": desig, "status": "common", "a": va, "b": vb})

    req_diffs = compare_variant_requirements(reqs_a, reqs_b)

    return {
        "header_diffs": header_diffs,
        "variant_diffs": variant_diffs,
        "req_diffs": req_diffs,
    }
