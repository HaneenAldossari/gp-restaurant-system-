"""Menu Engineering API — GET /api/menu-engineering"""
from fastapi import APIRouter, HTTPException, Query
from data_loader_db import load_data, filter_data

router = APIRouter(tags=["Menu Engineering"])


@router.get("/api/menu-engineering")
def menu_engineering(
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    df = filter_data(load_data(), start_date, end_date)

    if df.empty:
        raise HTTPException(status_code=404, detail="No data for the selected filters")

    # Per-product metrics
    items = df.groupby("Product").agg(
        category=("Category", "first"),
        qtySold=("Quantity", "sum"),
        revenue=("Total Price", "sum"),
        totalCost=("Product Cost", "sum"),
        unitPrice=("Unit Price", "first"),
        unitCost=("unit_cost", "first"),
    ).reset_index()
    items.columns = ["name", "category", "qtySold", "revenue", "totalCost", "price", "cost"]

    items["profit"] = (items["revenue"] - items["totalCost"]).round(2)
    items["profitMargin"] = ((items["revenue"] - items["totalCost"]) / items["revenue"] * 100).round(2)
    items["revenue"] = items["revenue"].round(2)
    items["cost"] = items["cost"].round(2)
    items["price"] = items["price"].round(2)

    total_qty = int(items["qtySold"].sum())
    items["popularity"] = (items["qtySold"] / total_qty * 100).round(2)

    avg_pop = float(items["popularity"].mean())
    avg_margin = float(items["profitMargin"].mean())

    def classify(row):
        hi_pop = row["popularity"] >= avg_pop
        hi_mar = row["profitMargin"] >= avg_margin
        if hi_pop and hi_mar:
            return "Star"
        elif hi_pop and not hi_mar:
            return "Plowhorse"
        elif not hi_pop and hi_mar:
            return "Puzzle"
        else:
            return "Dog"

    items["classification"] = items.apply(classify, axis=1)

    items_list = items[[
        "name", "category", "qtySold", "revenue", "totalCost", "price", "cost",
        "profit", "profitMargin", "popularity", "classification",
    ]].to_dict("records")

    # Quadrant summaries
    quadrants = {}
    for cls in ["Star", "Plowhorse", "Puzzle", "Dog"]:
        group = items[items["classification"] == cls]
        quadrants[cls] = {
            "count": int(len(group)),
            "revenue": round(float(group["revenue"].sum()), 2),
            "items": group["name"].tolist(),
        }

    return {
        "items": items_list,
        "quadrants": quadrants,
        "avgPopularity": round(avg_pop, 2),
        "avgMargin": round(avg_margin, 2),
    }


# ─────────────────────────────────────────────────────────────────────────
# What-If Simulator
# ─────────────────────────────────────────────────────────────────────────

# Per-category price elasticity of demand. Values come from published hospitality
# research (e.g. Kotler, menu engineering literature, industry reports). Items
# like coffee and water are inelastic (customers keep buying as prices rise);
# premium desserts and specialty cuisines are highly elastic.
#
# Keys are lowercased. Lookup order: exact match → partial keyword match →
# fallback DEFAULT_ELASTICITY.
ELASTICITY_BY_CATEGORY: dict[str, float] = {
    # --- Hot & specialty coffee (inelastic — habit-driven) ---
    "espresso drinks": -0.5, "espresso": -0.5, "specialty coffee": -0.5, "coffee": -0.5,
    "hot coffee": -0.5, "americano": -0.5, "filter coffee": -0.6, "drip coffee": -0.6,
    "saudi coffee": -0.4, "turkish coffee": -0.6, "arabic coffee": -0.4,
    # --- Hot drinks ---
    "hot drinks": -0.7, "hot beverages": -0.7, "tea": -0.6, "hot tea": -0.6,
    "herbal tea": -0.8, "matcha": -0.9, "hot chocolate": -0.8, "chocolate drinks": -0.8,
    # --- Cold coffee ---
    "cold coffee drinks": -0.8, "iced coffee": -0.8, "cold coffee": -0.8,
    "frappe": -1.0, "frappes": -1.0, "frappuccino": -1.0,
    # --- Cold drinks / beverages ---
    "cold drinks": -1.0, "cold beverages": -1.0, "beverages": -1.0, "drinks": -1.0,
    "soft drinks": -1.1, "sodas": -1.1, "soda": -1.1,
    "juices": -1.1, "juice": -1.1, "fresh juice": -1.0,
    "smoothies": -1.2, "milkshakes": -1.2, "shakes": -1.2,
    "water": -0.3, "mineral water": -0.3, "bottled water": -0.3,
    "energy drinks": -1.0, "mocktails": -1.3,
    # --- Bakery & breads (semi-inelastic, meal-like) ---
    "bakery": -1.0, "bread": -0.9, "breads": -0.9, "baked goods": -1.0,
    "pastries": -1.2, "pastry": -1.2, "croissants": -1.2, "croissant": -1.2,
    "muffins": -1.3, "muffin": -1.3, "scones": -1.2, "donuts": -1.3, "bagels": -1.1,
    # --- Sweets & desserts (elastic — discretionary) ---
    "sweets": -1.8, "desserts": -1.8, "dessert": -1.8,
    "cakes": -1.7, "cake": -1.7, "cheesecakes": -1.7, "cheesecake": -1.7,
    "cupcakes": -1.6, "pies": -1.6, "tarts": -1.6,
    "ice cream": -1.6, "gelato": -1.6, "frozen desserts": -1.6, "sorbet": -1.6,
    "hot sweets": -1.6, "waffles": -1.6, "waffle": -1.6, "pancakes": -1.6,
    "crepes": -1.6, "french toast": -1.5,
    "cookies": -1.5, "brownies": -1.5, "puddings": -1.6, "custards": -1.6,
    "candy": -1.7, "chocolates": -1.7, "confectionery": -1.7,
    # --- Savory mains (medium elasticity) ---
    "savory": -1.3, "mains": -1.3, "main course": -1.3, "entrees": -1.3, "meals": -1.3,
    "sandwiches": -1.1, "sandwich": -1.1, "paninis": -1.1, "panini": -1.1,
    "wraps": -1.1, "wrap": -1.1, "subs": -1.1,
    "burgers": -1.2, "burger": -1.2, "sliders": -1.3,
    "pizza": -1.3, "pizzas": -1.3, "flatbread": -1.3,
    "pasta": -1.4, "pastas": -1.4, "noodles": -1.3,
    "salads": -1.5, "salad": -1.5,
    "soups": -1.0, "soup": -1.0, "broths": -1.0,
    "breakfast": -0.9, "brunch": -1.0, "breakfast items": -0.9,
    "appetizers": -1.6, "starters": -1.6, "sides": -1.4, "finger food": -1.5,
    # --- Specific proteins ---
    "steaks": -1.5, "steak": -1.5, "grills": -1.5, "grill": -1.5, "grilled": -1.5,
    "bbq": -1.6, "barbecue": -1.6,
    "seafood": -1.7, "fish": -1.6, "shrimp": -1.8, "lobster": -2.0, "crab": -1.9,
    "chicken": -1.2, "poultry": -1.2, "wings": -1.3,
    "beef": -1.4, "lamb": -1.4, "meat": -1.3,
    # --- Cuisines ---
    "sushi": -2.0, "japanese": -1.8, "ramen": -1.4,
    "asian": -1.5, "chinese": -1.3, "thai": -1.4, "korean": -1.5, "vietnamese": -1.3,
    "italian": -1.4, "french": -1.8,
    "mexican": -1.4, "tex-mex": -1.4, "latin": -1.4,
    "middle eastern": -1.1, "arabic": -1.1, "arabic food": -1.1, "saudi": -1.0,
    "shawarma": -0.9, "falafel": -0.9, "kebabs": -1.0, "kebab": -1.0,
    "shish": -1.0, "mandi": -1.0, "kabsa": -1.0, "mandy": -1.0, "madfoon": -1.0,
    "indian": -1.3, "curry": -1.3, "biryani": -1.2,
    "mediterranean": -1.2, "greek": -1.3, "lebanese": -1.1, "turkish": -1.2,
    # --- Other ---
    "kids menu": -0.8, "kids": -0.8, "children's menu": -0.8,
    "catering": -1.5, "combos": -1.0, "meal deals": -1.0, "family meals": -0.9,
    "healthy": -1.3, "vegan": -1.5, "vegetarian": -1.3, "gluten-free": -1.4,
}

# Catch-all for categories we don't recognize. Moderately elastic.
DEFAULT_ELASTICITY = -1.5

# Multiplier range used to derive a confidence band around the central estimate.
# Real elasticity values have significant uncertainty; we show the range rather
# than pretending a single point is exact.
ELASTICITY_UNCERTAINTY = 0.30  # ±30%


def _elasticity_for(category: str | None) -> tuple[float, str]:
    """
    Return (elasticity, human_readable_source) for the given category name.
    Matches are case-insensitive and fall back to partial keyword matching
    before using DEFAULT_ELASTICITY.
    """
    if not category:
        return DEFAULT_ELASTICITY, f"Unknown category → default ({DEFAULT_ELASTICITY})"
    key = str(category).strip().lower()
    if key in ELASTICITY_BY_CATEGORY:
        return ELASTICITY_BY_CATEGORY[key], f"{category} → {ELASTICITY_BY_CATEGORY[key]}"
    # Partial keyword match — e.g., "Iced Coffee & Smoothies" matches "cold coffee"
    for known_key, value in ELASTICITY_BY_CATEGORY.items():
        if known_key in key or key in known_key:
            return value, f"{category} (matched '{known_key}') → {value}"
    return DEFAULT_ELASTICITY, f"{category} (unmatched) → default ({DEFAULT_ELASTICITY})"


def _classify(popularity: float, margin: float, avg_pop: float, avg_margin: float) -> str:
    hi_pop = popularity >= avg_pop
    hi_mar = margin >= avg_margin
    if hi_pop and hi_mar: return "Star"
    if hi_pop and not hi_mar: return "Plowhorse"
    if not hi_pop and hi_mar: return "Puzzle"
    return "Dog"


def _project_qty_float(current_qty: int, current_price: float, new_price: float, elasticity: float) -> float:
    """
    Constant-elasticity demand as a smooth (unrounded) value.
    Kept as float so profit/revenue calculations don't produce staircase
    discontinuities when projected qty crosses an integer boundary.
    """
    if current_price <= 0:
        return float(current_qty)
    ratio = new_price / current_price
    return max(0.0, float(current_qty) * (ratio ** elasticity))


def _project_qty(current_qty: int, current_price: float, new_price: float, elasticity: float) -> int:
    """Display-only integer projection (rounded). Do NOT use in profit math."""
    return int(round(_project_qty_float(current_qty, current_price, new_price, elasticity)))


def _simulate_one(
    current_price: float, current_cost: float, current_qty: int,
    new_price: float, effective_cost: float, elasticity: float,
    other_qty: float, avg_pop: float, avg_margin: float,
) -> dict:
    """Run the demand math for one (price, cost, elasticity) triple."""
    # Use the smooth (float) qty in the math — rounding only for display —
    # otherwise low-volume items produce "staircase" profit jumps when the
    # rounded customer count steps across an integer boundary.
    qty_f = _project_qty_float(current_qty, current_price, new_price, elasticity)
    qty_display = int(round(qty_f))
    revenue = qty_f * new_price
    profit = qty_f * (new_price - effective_cost)
    margin = (profit / revenue * 100) if revenue > 0 else 0
    new_total = other_qty + qty_f
    pop = (qty_f / new_total * 100) if new_total > 0 else 0
    classification = _classify(pop, margin, avg_pop, avg_margin)
    return {
        "projectedQty": qty_display,
        "newRevenue": round(revenue, 2),
        "newProfit": round(profit, 2),
        "newMargin": round(margin, 2),
        "newPopularity": round(pop, 2),
        "newClassification": classification,
    }


def _optimal_price(cost: float, elasticity: float, current_price: float) -> dict | None:
    """
    For constant-elasticity demand with unit cost c, profit-max price is:
        P* = e * c / (1 + e)    where e is elasticity (negative, |e| > 1).

    The raw theoretical optimum often implies dramatic price jumps (e.g. +50%)
    that would cause real customers to walk away — the constant-elasticity
    model understates demand loss at large jumps. So we return a *safe*
    incremental suggestion capped at ±20% of current price. The theoretical
    target is exposed separately for reference.

    For inelastic categories (|e| <= 1), profit grows monotonically with
    price — no interior optimum — so we suggest a small test increase.
    """
    SAFE_UP_CAP   = 0.20   # don't suggest more than +20% above current
    SAFE_DOWN_CAP = 0.20   # or more than −20% below current

    if elasticity >= -1.0:
        # Inelastic: no mathematical optimum — recommend a small test increase
        test_price = round(current_price * 1.10)
        return {
            "price": int(test_price),
            "kind": "test_increase",
            "rationale": (
                "Customers for this category keep buying even when the price "
                "goes up a bit. Try raising slightly to test, then raise more "
                "if customers don't drop off."
            ),
            "inelastic": True,
        }

    theoretical = elasticity * cost / (1 + elasticity)
    safe_max = current_price * (1 + SAFE_UP_CAP)
    safe_min = current_price * (1 - SAFE_DOWN_CAP)

    if theoretical > safe_max:
        suggested = safe_max
        kind = "capped_up"
        rationale = (
            "Your sales history suggests a higher price would give more profit, "
            "but jumping too far at once can scare customers away. Start with "
            "this increase and test customer response before going higher."
        )
    elif theoretical < safe_min:
        suggested = safe_min
        kind = "capped_down"
        rationale = (
            "A small discount looks worthwhile. Avoid cutting too aggressively — "
            "test this price first."
        )
    else:
        suggested = theoretical
        kind = "direct"
        rationale = (
            "Based on your sales history, this price should give you the best profit."
        )

    return {
        "price": int(round(suggested)),
        "theoreticalPrice": int(round(theoretical)),
        "kind": kind,
        "rationale": rationale,
        "inelastic": False,
    }


def _break_even_price(
    current_profit: float, current_price: float, current_qty: int,
    effective_cost: float, elasticity: float,
) -> float | None:
    """
    Find the price P (≠ current_price) at which profit equals current_profit.
    Numerical search (profit is non-monotonic — may have two solutions).
    Returns the nearest root above current_price if a cost increase, below if decrease.
    """
    if current_qty <= 0 or current_price <= 0:
        return None
    # Scan a wide range and pick the closest root. Use the smooth (float) qty
    # so the search lands on a continuous curve, not the rounded staircase.
    import numpy as np
    candidates = np.linspace(max(0.01, current_price * 0.3), current_price * 3.0, 400)
    best_p, best_diff = None, float("inf")
    for p in candidates:
        qty = _project_qty_float(current_qty, current_price, float(p), elasticity)
        profit = qty * (p - effective_cost)
        diff = abs(profit - current_profit)
        if diff < best_diff:
            best_diff = diff
            best_p = float(p)
    return round(best_p, 2) if best_p is not None else None


def _cost_defense(
    current_price: float, current_cost: float, current_qty: int,
    current_profit: float, current_margin: float, elasticity: float,
    cost_rise_pct: float = 20.0,
) -> dict:
    """
    If supplier cost rises by `cost_rise_pct`%, what price change is needed to:
      a) keep margin %  — just raise price by same pct (margin-preserving)
      b) keep absolute profit the same
    """
    new_cost = current_cost * (1 + cost_rise_pct / 100)

    # (a) Margin-preserving: keep (P - c)/P constant → P_new = P_old * (c_new / c_old)
    margin_pres_price = current_price * (new_cost / current_cost) if current_cost > 0 else current_price
    margin_pres_change = (margin_pres_price - current_price) / current_price * 100 if current_price > 0 else 0

    # (b) Profit-preserving: find price where qty(P) * (P - new_cost) == current_profit
    import numpy as np
    candidates = np.linspace(current_price, current_price * 3.0, 400)
    profit_pres_price = None
    best_diff = float("inf")
    for p in candidates:
        qty = _project_qty_float(current_qty, current_price, float(p), elasticity)
        profit = qty * (p - new_cost)
        diff = abs(profit - current_profit)
        if diff < best_diff:
            best_diff = diff
            profit_pres_price = float(p)
    profit_pres_change = (
        (profit_pres_price - current_price) / current_price * 100
        if profit_pres_price and current_price > 0 else None
    )

    return {
        "costRisePct": cost_rise_pct,
        "newCost": round(new_cost, 2),
        "toKeepMargin": {
            "newPrice": round(margin_pres_price, 2),
            "priceChangePct": round(margin_pres_change, 1),
        },
        "toKeepProfit": {
            "newPrice": round(profit_pres_price, 2) if profit_pres_price else None,
            "priceChangePct": round(profit_pres_change, 1) if profit_pres_change else None,
        },
    }


@router.get("/api/menu-engineering/simulate", summary="What-If price simulation")
def simulate_price_change(
    target: str = Query(..., description="Product name (exact match)"),
    new_price: float = Query(..., description="The hypothetical new selling price"),
    new_cost: float | None = Query(
        None,
        description="Optional hypothetical new unit cost. If omitted, the current cost is used. Cost changes affect margin only (not demand).",
    ),
):
    """
    Simulate moving a product's price (and optionally cost) and return:

    - **Central estimate** (`simulated`): projected qty, revenue, profit, margin,
      popularity, and new Boston Matrix classification.
    - **Confidence band** (`confidence`): low/high estimates reflecting ±30%
      uncertainty in the elasticity parameter.
    - **Recommendations** (`recommendations`): profit-optimal price, break-even
      price, and cost-increase defense guidance.
    - **Scenarios** (`scenarios`): projections at fixed price deltas (−20% …
      +30%) so the manager can compare alternatives side-by-side.
    - **Elasticity source** (`elasticitySource`): which category match was used.

    Demand model: constant elasticity `new_qty = old_qty × (new/old)^elasticity`.
    Elasticity varies by category (see `ELASTICITY_BY_CATEGORY` in source);
    unknown categories fall back to -1.5.
    """
    if new_price <= 0:
        raise HTTPException(status_code=400, detail="new_price must be positive")
    if new_cost is not None and new_cost < 0:
        raise HTTPException(status_code=400, detail="new_cost cannot be negative")

    df = load_data()
    if df.empty:
        raise HTTPException(status_code=404, detail="No sales data available")

    items = df.groupby("Product").agg(
        category=("Category", "first"),
        qtySold=("Quantity", "sum"),
        revenue=("Total Price", "sum"),
        totalCost=("Product Cost", "sum"),
        price=("Unit Price", "first"),
        cost=("unit_cost", "first"),
    ).reset_index()
    items.columns = ["name", "category", "qtySold", "revenue", "totalCost", "price", "cost"]

    target_row = items[items["name"] == target]
    if target_row.empty:
        raise HTTPException(status_code=404, detail=f"Product '{target}' not found")

    t = target_row.iloc[0]
    current_price = float(t["price"])
    current_cost = float(t["cost"])
    current_qty = int(t["qtySold"])
    current_revenue = float(t["revenue"])
    current_profit = current_revenue - float(t["totalCost"])
    current_margin = (current_profit / current_revenue * 100) if current_revenue else 0
    total_qty_all = float(items["qtySold"].sum())
    current_pop = (current_qty / total_qty_all * 100) if total_qty_all else 0
    other_qty = total_qty_all - current_qty

    # Per-category elasticity + confidence band
    elasticity_central, elasticity_source = _elasticity_for(str(t["category"]))
    elasticity_low = elasticity_central * (1 + ELASTICITY_UNCERTAINTY)   # more elastic
    elasticity_high = elasticity_central * (1 - ELASTICITY_UNCERTAINTY)  # less elastic

    # Menu-wide thresholds (kept constant — treats other items as fixed)
    items["profit"] = items["revenue"] - items["totalCost"]
    items["profitMargin"] = (items["profit"] / items["revenue"] * 100).fillna(0)
    items["popularity"] = items["qtySold"] / total_qty_all * 100
    avg_pop = float(items["popularity"].mean())
    avg_margin = float(items["profitMargin"].mean())

    effective_cost = float(new_cost) if new_cost is not None else current_cost

    central = _simulate_one(
        current_price, current_cost, current_qty,
        new_price, effective_cost, elasticity_central,
        other_qty, avg_pop, avg_margin,
    )
    sim_low = _simulate_one(
        current_price, current_cost, current_qty,
        new_price, effective_cost, elasticity_low,
        other_qty, avg_pop, avg_margin,
    )
    sim_high = _simulate_one(
        current_price, current_cost, current_qty,
        new_price, effective_cost, elasticity_high,
        other_qty, avg_pop, avg_margin,
    )

    def _minmax(key):
        return sorted([sim_low[key], sim_high[key]])

    central["newPrice"] = round(new_price, 2)
    central["newCost"] = round(effective_cost, 2)

    def _pct(old, new):
        if old == 0:
            return None
        return round((new - old) / old * 100, 1)

    # Scenarios table — fixed price deltas around current
    scenarios = []
    for delta_pct in (-20, -10, 0, 10, 20, 30):
        p = current_price * (1 + delta_pct / 100)
        s = _simulate_one(
            current_price, current_cost, current_qty,
            p, effective_cost, elasticity_central,
            other_qty, avg_pop, avg_margin,
        )
        scenarios.append({
            "priceChangePct": delta_pct,
            "newPrice": round(p, 2),
            "projectedQty": s["projectedQty"],
            "newRevenue": s["newRevenue"],
            "newProfit": s["newProfit"],
            "newMargin": s["newMargin"],
            "newClassification": s["newClassification"],
        })

    recommendations = {
        "optimalPrice": _optimal_price(effective_cost, elasticity_central, current_price),
        "breakEvenPrice": _break_even_price(
            current_profit, current_price, current_qty,
            effective_cost, elasticity_central,
        ),
        "costDefense": _cost_defense(
            current_price, current_cost, current_qty,
            current_profit, current_margin, elasticity_central,
        ),
    }

    current_classification = _classify(current_pop, current_margin, avg_pop, avg_margin)

    return {
        "elasticity": elasticity_central,
        "elasticitySource": elasticity_source,
        "confidenceRange": {
            "elasticityLow": round(elasticity_low, 2),
            "elasticityHigh": round(elasticity_high, 2),
            "uncertaintyPct": int(ELASTICITY_UNCERTAINTY * 100),
        },
        "thresholds": {
            "avgPopularity": round(avg_pop, 2),
            "avgMargin": round(avg_margin, 2),
        },
        "current": {
            "name": str(t["name"]),
            "category": str(t["category"]),
            "price": round(current_price, 2),
            "cost": round(current_cost, 2),
            "qtySold": current_qty,
            "revenue": round(current_revenue, 2),
            "profit": round(current_profit, 2),
            "margin": round(current_margin, 2),
            "popularity": round(current_pop, 2),
            "classification": current_classification,
        },
        "simulated": central,
        "confidence": {
            "projectedQty": {"low": _minmax("projectedQty")[0], "high": _minmax("projectedQty")[1]},
            "newRevenue": {"low": _minmax("newRevenue")[0], "high": _minmax("newRevenue")[1]},
            "newProfit": {"low": _minmax("newProfit")[0], "high": _minmax("newProfit")[1]},
            "newMargin": {"low": _minmax("newMargin")[0], "high": _minmax("newMargin")[1]},
        },
        "delta": {
            "priceChangePct": _pct(current_price, new_price),
            "costChangePct": _pct(current_cost, effective_cost),
            "qtyChangePct": _pct(current_qty, central["projectedQty"]),
            "revenueChangePct": _pct(current_revenue, central["newRevenue"]),
            "profitChangePct": _pct(current_profit, central["newProfit"]),
            "classificationChange": (
                f"{current_classification} → {central['newClassification']}"
                if current_classification != central["newClassification"]
                else f"Stays {current_classification}"
            ),
        },
        "scenarios": scenarios,
        "recommendations": recommendations,
    }


@router.get(
    "/api/menu-engineering/simulate-bulk",
    summary="Apply a uniform price change to every item in one recommendation group",
)
def simulate_bulk(
    classification: str = Query(
        ...,
        description="Which recommendation bucket to target: Star, Plowhorse, Puzzle, or Dog",
    ),
    price_change_pct: float = Query(
        ...,
        description="Percentage price change to apply to every item in the group (e.g. 10 for +10%, -5 for -5%)",
    ),
):
    """
    Apply the same percentage price change to every product in a given
    classification bucket and return the aggregate impact on the menu:
    total revenue, profit, and margin before vs after.

    Useful for strategy questions like 'what happens if I raise prices on
    every Plowhorse by 10%?' without adjusting each item individually.
    """
    if classification not in ("Star", "Plowhorse", "Puzzle", "Dog"):
        raise HTTPException(
            status_code=400,
            detail="classification must be one of: Star, Plowhorse, Puzzle, Dog",
        )

    df = load_data()
    if df.empty:
        raise HTTPException(status_code=404, detail="No sales data available")

    items = df.groupby("Product").agg(
        category=("Category", "first"),
        qtySold=("Quantity", "sum"),
        revenue=("Total Price", "sum"),
        totalCost=("Product Cost", "sum"),
        price=("Unit Price", "first"),
        cost=("unit_cost", "first"),
    ).reset_index()
    items.columns = ["name", "category", "qtySold", "revenue", "totalCost", "price", "cost"]
    total_qty_all = float(items["qtySold"].sum())
    items["profit"] = items["revenue"] - items["totalCost"]
    items["profitMargin"] = (items["profit"] / items["revenue"] * 100).fillna(0)
    items["popularity"] = items["qtySold"] / total_qty_all * 100
    avg_pop = float(items["popularity"].mean())
    avg_margin = float(items["profitMargin"].mean())
    items["classification"] = items.apply(
        lambda r: _classify(r["popularity"], r["profitMargin"], avg_pop, avg_margin),
        axis=1,
    )
    target_items = items[items["classification"] == classification].copy()

    if target_items.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No items classified as '{classification}'",
        )

    affected = []
    new_group_revenue = 0.0
    new_group_profit = 0.0
    for _, row in target_items.iterrows():
        elast, _ = _elasticity_for(str(row["category"]))
        new_price = float(row["price"]) * (1 + price_change_pct / 100)
        new_qty = _project_qty(int(row["qtySold"]), float(row["price"]), new_price, elast)
        new_rev = new_qty * new_price
        new_prof = new_qty * (new_price - float(row["cost"]))
        new_group_revenue += new_rev
        new_group_profit += new_prof
        affected.append({
            "name": str(row["name"]),
            "category": str(row["category"]),
            "currentPrice": round(float(row["price"]), 2),
            "newPrice": round(new_price, 2),
            "currentQty": int(row["qtySold"]),
            "projectedQty": new_qty,
            "elasticityUsed": elast,
        })

    current_group_revenue = float(target_items["revenue"].sum())
    current_group_profit = float(target_items["profit"].sum())
    menu_total_revenue = float(items["revenue"].sum())
    menu_total_profit = float(items["profit"].sum())

    def _pct(old, new):
        if old == 0:
            return None
        return round((new - old) / old * 100, 1)

    return {
        "classification": classification,
        "priceChangePct": price_change_pct,
        "itemCount": int(len(target_items)),
        "groupImpact": {
            "currentRevenue": round(current_group_revenue, 2),
            "newRevenue": round(new_group_revenue, 2),
            "revenueChangePct": _pct(current_group_revenue, new_group_revenue),
            "currentProfit": round(current_group_profit, 2),
            "newProfit": round(new_group_profit, 2),
            "profitChangePct": _pct(current_group_profit, new_group_profit),
        },
        "menuImpact": {
            "currentTotalRevenue": round(menu_total_revenue, 2),
            "newTotalRevenue": round(
                menu_total_revenue - current_group_revenue + new_group_revenue, 2
            ),
            "menuRevenueChangePct": _pct(
                menu_total_revenue,
                menu_total_revenue - current_group_revenue + new_group_revenue,
            ),
            "currentTotalProfit": round(menu_total_profit, 2),
            "newTotalProfit": round(
                menu_total_profit - current_group_profit + new_group_profit, 2
            ),
            "menuProfitChangePct": _pct(
                menu_total_profit,
                menu_total_profit - current_group_profit + new_group_profit,
            ),
        },
        "affectedItems": affected,
    }
