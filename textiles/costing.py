"""
Textiles Costing Calculation Engine — SNM Works
=============================================================================
Pure calculation functions for unit manufacturing costs, yarn consumption,
process conversion, and factory overheads.

NO framework imports, purely testable arithmetic.
=============================================================================
"""
from typing import Any, Dict, Optional


def calculate_yarn_cost(
    yarn_consumption_gpm: float,
    yarn_rate_per_kg: float,
    wastage_pct: float = 0.0
) -> float:
    """
    Computes raw yarn cost per meter (or unit).
    consumption is in grams per meter (gpm), rate is in INR per kg.
    Yarn Cost = (gpm / 1000) * (rate/kg) * (1 + wastage% / 100)
    """
    if not yarn_consumption_gpm or not yarn_rate_per_kg:
        return 0.0
    if yarn_consumption_gpm <= 0 or yarn_rate_per_kg <= 0:
        return 0.0
    return (float(yarn_consumption_gpm) / 1000.0) * float(yarn_rate_per_kg) * (1.0 + (float(wastage_pct or 0.0) / 100.0))


def calculate_manufacturing_cost(
    yarn_cost: float,
    dyeing: float = 0.0,
    coating: float = 0.0,
    labour: float = 0.0,
    overhead: float = 0.0,
    packing: float = 0.0,
    freight: float = 0.0
) -> Dict[str, float]:
    """
    Itemizes manufacturing cost breakdown into components:
    - yarn_cost
    - process_cost = dyeing + coating + labour
    - overhead_cost = overhead + packing + freight
    - total_manufacturing_cost = yarn_cost + process_cost + overhead_cost
    """
    yarn_val = float(yarn_cost or 0.0)
    process_cost = float(dyeing or 0.0) + float(coating or 0.0) + float(labour or 0.0)
    overhead_cost = float(overhead or 0.0) + float(packing or 0.0) + float(freight or 0.0)
    total_cost = yarn_val + process_cost + overhead_cost
    return {
        "yarn_cost": round(yarn_val, 4),
        "process_cost": round(process_cost, 4),
        "overhead_cost": round(overhead_cost, 4),
        "total_manufacturing_cost": round(total_cost, 4),
    }


def calculate_selling_price(
    manufacturing_cost: float,
    margin_pct: float,
    method: Optional[str] = None
) -> Dict[str, Any]:
    """
    Selling price computation stub.
    Per user directive: Withholds calculation and returns an explicit pending sentinel
    until the commercial pricing formula (margin on selling price vs markup on cost)
    is formally confirmed by management.
    """
    if method is None:
        return {
            "status": "pending_formula",
            "message": "Pricing formula pending confirmation",
            "selling_price": None,
            "margin_amount": None,
        }

    # Parameterized formula implementation for future activation
    if method == "margin_on_price":
        if margin_pct >= 100.0:
            raise ValueError("Margin percentage must be less than 100%")
        price = manufacturing_cost / (1.0 - (margin_pct / 100.0))
        return {
            "status": "calculated",
            "selling_price": round(price, 4),
            "margin_amount": round(price - manufacturing_cost, 4),
        }
    elif method == "markup_on_cost":
        price = manufacturing_cost * (1.0 + (margin_pct / 100.0))
        return {
            "status": "calculated",
            "selling_price": round(price, 4),
            "margin_amount": round(price - manufacturing_cost, 4),
        }
    else:
        raise NotImplementedError(f"Unknown pricing formula method: {method}")
