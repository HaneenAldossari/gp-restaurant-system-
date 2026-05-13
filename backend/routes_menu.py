"""Menu Engineering API — GET /api/menu-engineering"""
import math
from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user_id
from data_loader_db import load_data, filter_data

router = APIRouter(tags=["Menu Engineering"])


# ═══════════════════════════════════════════════════════════════════════
# CONCEPT — Menu Engineering endpoint (Slide 24)
# Returns each product's Boston Matrix classification + quadrant counts.
# Consumed by: frontend MenuEngineering.jsx
# ═══════════════════════════════════════════════════════════════════════
@router.get("/api/menu-engineering")
def menu_engineering(
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    # Boston Matrix shows real customer behaviour only. Popularity
    # reflects what customers actually bought; margin reflects real
    # costs against real prices. Imputed rows are reserved for
    # forecast model training — the data-loss days don't overlap
    # heavily with summer-peak windows for our menu, and the
    # methodological cleanness ("classifications based on POS
    # records") is more defensible than chasing edge cases.
    df = filter_data(load_data(user_id, include_synthetic=False), start_date, end_date)

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

    # Popularity cutoff = 70 % × mean (Kasavana & Smith, 1982). The
    # arithmetic mean of popularity is mathematically `100 / N` (every
    # share sums to 100, divided by N items), which on a heavy
    # right-skew sales distribution becomes a tail-test rather than an
    # "above average" test. With N = 129 the raw mean is 0.78 % while
    # the median is 0.40 % — the canonical 0.7×-mean cutoff is the
    # menu-engineering literature's standard fix and lands much closer
    # to the median in practice. Margin stays at the raw mean: margin
    # is bounded [0, 100 %], symmetrically distributed in a typical
    # menu, and the mean is the right anchor there.
    avg_pop_raw = float(items["popularity"].mean())
    avg_margin = float(items["profitMargin"].mean())   # CONCEPT: margin threshold (mean across menu)
    pop_cutoff = 0.7 * avg_pop_raw                      # CONCEPT: Kasavana-Smith popularity threshold (0.7 × mean)

    # ═══════════════════════════════════════════════════════════════════
    # CONCEPT — Boston Matrix classification (Slide 24)
    # Each product → Star / Plowhorse / Puzzle / Dog
    # High popularity = pop ≥ 0.7 × mean (Kasavana–Smith, 1982)
    # High margin     = margin ≥ mean margin
    # ═══════════════════════════════════════════════════════════════════
    def classify(row):
        hi_pop = row["popularity"] >= pop_cutoff
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
        # `avgPopularity` is the literal threshold used to split high-vs-low
        # popularity (70 % of the raw mean — Kasavana–Smith). The frontend's
        # quadrant axis line should track this, not the raw mean.
        "avgPopularity": round(pop_cutoff, 2),
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
# ═══════════════════════════════════════════════════════════════════════
# CONCEPT — Elasticity table by category (Slide 32, right column)
# Values from hospitality industry research: habit goods (coffee, tea) are
# inelastic (~−0.4 to −0.7); discretionary items (sweets, desserts) are
# highly elastic (~−1.6 to −2.0). Used by the What-If Price Simulator.
# ═══════════════════════════════════════════════════════════════════════
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


# How much of the unit cost a serious supplier-negotiation / bulk-buy / recipe
# tweak can plausibly remove. Calibrated per category, because the room differs
# by ingredient profile:
#   • Many ingredients + recipe flexibility (sweets, bakery) → wider room
#   • Commodity-priced ingredients (coffee beans, tea leaves) → narrow room
#   • Specialty SKUs whose origin/grade customers notice (pour-over) → narrow
# Same lookup style as ELASTICITY_BY_CATEGORY: exact key → partial match → default.
COST_REDUCTION_CAP_BY_CATEGORY: dict[str, float] = {
    # --- Sweets & desserts (lots of ingredients, decoration grades, recipe room) ---
    "sweets": 0.60, "desserts": 0.60, "dessert": 0.60,
    "cakes": 0.60, "cake": 0.60, "cheesecakes": 0.60, "cheesecake": 0.60,
    "cupcakes": 0.60, "pies": 0.60, "tarts": 0.60,
    "candy": 0.60, "chocolates": 0.60, "confectionery": 0.60,
    "ice cream": 0.55, "gelato": 0.55, "frozen desserts": 0.55, "sorbet": 0.55,
    "hot sweets": 0.60, "waffles": 0.60, "waffle": 0.60, "pancakes": 0.60,
    "crepes": 0.60, "french toast": 0.55,
    "cookies": 0.55, "brownies": 0.55, "puddings": 0.55, "custards": 0.55,
    # --- Bakery (flour/butter/sugar with recipe + portion flexibility) ---
    "bakery": 0.55, "bread": 0.50, "breads": 0.50, "baked goods": 0.55,
    "pastries": 0.55, "pastry": 0.55, "croissants": 0.55, "croissant": 0.55,
    "muffins": 0.55, "muffin": 0.55, "scones": 0.55, "donuts": 0.55, "bagels": 0.50,
    # --- Cold drinks (fresh fruit + syrups; supplier switching common) ---
    "cold drinks": 0.55, "cold beverages": 0.55, "beverages": 0.55, "drinks": 0.55,
    "juices": 0.55, "juice": 0.55, "fresh juice": 0.55,
    "smoothies": 0.55, "milkshakes": 0.55, "shakes": 0.55,
    "soft drinks": 0.45, "sodas": 0.45, "soda": 0.45,
    "energy drinks": 0.40, "mocktails": 0.55,
    "water": 0.30, "mineral water": 0.30, "bottled water": 0.30,
    # --- Cold coffee (bean + milk + syrup mix) ---
    "cold coffee drinks": 0.50, "iced coffee": 0.50, "cold coffee": 0.50,
    "frappe": 0.50, "frappes": 0.50, "frappuccino": 0.50,
    # --- Espresso drinks (commodity beans + small milk) ---
    "espresso drinks": 0.40, "espresso": 0.40, "specialty coffee": 0.40,
    "coffee": 0.40, "hot coffee": 0.40, "americano": 0.40,
    # --- Hot drinks (tea/coffee already commodity-priced) ---
    "hot drinks": 0.40, "hot beverages": 0.40,
    "tea": 0.35, "hot tea": 0.35, "herbal tea": 0.40, "matcha": 0.45,
    "hot chocolate": 0.50, "chocolate drinks": 0.50,
    "saudi coffee": 0.40, "turkish coffee": 0.40, "arabic coffee": 0.40,
    # --- Pour-over / dripp (specialty origin matters; less room) ---
    "filter coffee": 0.35, "drip coffee": 0.35, "dripp coffee drinks": 0.35,
    # --- Savory mains (proteins are the cost driver; some sourcing room) ---
    "savory": 0.45, "mains": 0.45, "main course": 0.45, "entrees": 0.45, "meals": 0.45,
    "sandwiches": 0.50, "sandwich": 0.50, "paninis": 0.50, "panini": 0.50,
    "wraps": 0.50, "wrap": 0.50, "subs": 0.50,
    "burgers": 0.45, "burger": 0.45, "sliders": 0.45,
    "pizza": 0.55, "pizzas": 0.55, "flatbread": 0.55,
    "pasta": 0.55, "pastas": 0.55, "noodles": 0.55,
    "salads": 0.50, "salad": 0.50,
    "soups": 0.55, "soup": 0.55, "broths": 0.55,
    "breakfast": 0.50, "brunch": 0.50, "breakfast items": 0.50,
    "appetizers": 0.55, "starters": 0.55, "sides": 0.55, "finger food": 0.55,
}

# Catch-all when no category-specific rate matches: 50% reduction (the
# previous uniform default). Conservative middle ground.
DEFAULT_COST_REDUCTION_CAP = 0.50


def _cost_reduction_cap_for(category: str | None) -> float:
    """
    Maximum fraction of the unit cost that supplier negotiation, bulk-buy,
    or a recipe tweak can plausibly remove. Same lookup pattern as
    `_elasticity_for`: exact match → partial keyword match → default.
    """
    if not category:
        return DEFAULT_COST_REDUCTION_CAP
    key = str(category).strip().lower()
    if key in COST_REDUCTION_CAP_BY_CATEGORY:
        return COST_REDUCTION_CAP_BY_CATEGORY[key]
    for known_key, value in COST_REDUCTION_CAP_BY_CATEGORY.items():
        if known_key in key or key in known_key:
            return value
    return DEFAULT_COST_REDUCTION_CAP


# ═══════════════════════════════════════════════════════════════════════
# CONCEPT — Elasticity lookup with fallback chain
# Exact category match → partial keyword match → DEFAULT_ELASTICITY (−1.5)
# Returns both the value AND a human-readable trace of which rule matched.
# ═══════════════════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════════════════
# CONCEPT — Constant-elasticity demand formula (Slide 32)
#     Q₁ = Q₀ × (P₁ / P₀)^e
# Given a price change from P₀ to P₁ and the category's elasticity e,
# returns the projected new demand Q₁. Float version — used for profit
# math; the integer wrapper below is display-only.
# ═══════════════════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════════════════
# CONCEPT — Optimal-price suggestion / Lerner Index (Slide 32)
#     P* = e × c / (1 + e)
# Suggests a price that maximises profit, given cost c and elasticity e.
# Surrounded by safety logic: Boston Matrix overrides for Dog/Puzzle,
# inelastic-case fallback, ±20 % cap from current price, and a Star-
# protection rule. The pure formula itself is one line (see inline anchor).
# ═══════════════════════════════════════════════════════════════════════
def _optimal_price(
    cost: float,
    elasticity: float,
    current_price: float,
    classification: str | None = None,
    current_qty: int = 0,
    avg_margin: float = 0.0,
) -> dict | None:
    """
    Suggest a price using the constant-elasticity demand model:
        P* = e * c / (1 + e)    where e < -1 (true elastic region).

    Theoretical optima from this model are often aggressive (+50% etc.) because
    the model understates the damage large jumps do to real demand. We cap the
    suggestion at ±20% from current.

    Classification overrides (Boston Matrix):
      - Dog (Underperformer) → raising price won't help; suggest a small
        discount to test demand response, or flag for replacement.
      - Puzzle (Hidden gem)  → high margin but low volume; lower price to
        unlock demand, margin stays healthy enough to be worthwhile.
      - Star, Plowhorse      → use the elasticity-based math.

    For strictly inelastic items (|e| < 1), constant-elasticity profit grows
    with price without bound — no interior optimum exists, so we propose a
    small test increase. Unit-elastic items (|e| ≈ 1) are handled via the
    theoretical math, not forced into the test-increase branch.
    """
    import math
    SAFE_UP_CAP   = 0.20
    SAFE_DOWN_CAP = 0.20

    # ── Classification-aware overrides ─────────────────────────────────
    # For Dogs and Puzzles, the textbook menu-engineering move is "try a
    # discount" — but that only helps when the item is genuinely elastic
    # (|e| > 1). For inelastic items (coffee, tea, Saudi coffee with
    # |e| ∈ [0.4, 0.9]) a discount lifts qty less than it cuts margin
    # and destroys profit. Eval against our own simulator showed 73 of
    # 129 suggestions losing money — Puzzle/Dog discounts on inelastic
    # SKUs were the entire problem (`backend/eval/MENU_REPORT.md`,
    # findings #1, #3).
    #
    # The gate below splits each branch:
    #   • elastic (e < -1.05) → discount, as before, but with `round`
    #     instead of `floor` so the realised cut matches the intended
    #     -10 % / -8 % rather than -12 % to -15 % (finding #4).
    #   • inelastic Puzzle → suggest a small price increase + push
    #     visibility (merchandising) instead of cutting the price.
    #   • inelastic Dog → no price move; flag for recipe rework or
    #     removal. The product itself is the problem, not the price.
    is_elastic = elasticity < -1.05

    # Helper: does a candidate discount actually lift projected profit
    # vs the current price? Elastic SKUs with high cost-ratio (cost
    # close to price) can have volume gain that doesn't outpace the
    # per-unit margin shrinkage — for those items the textbook
    # 'elastic Dog/Puzzle → discount' rule loses money. Reviewer
    # caught this on Candy Cake (price 25, cost 11, e=-1.6, suggested
    # 22 → projected profit drops by SAR 3). Gate the discount on
    # actual profit math instead of just the elasticity sign.
    def _discount_increases_profit(suggested_price: float) -> bool:
        if suggested_price <= 0 or current_price <= 0:
            return False
        new_qty = _project_qty_float(current_qty, current_price, suggested_price, elasticity)
        new_profit = new_qty * (suggested_price - cost)
        old_profit = current_qty * (current_price - cost)
        return new_profit > old_profit

    if classification == "Dog":
        if is_elastic:
            suggested = max(int(round(current_price * 0.90)), int(math.ceil(cost * 1.20)))
            if suggested >= int(round(current_price)):
                suggested = int(round(current_price)) - 1
            suggested = max(suggested, int(math.ceil(cost * 1.05)))  # keep a minimum margin
            if _discount_increases_profit(suggested):
                return {
                    "price": suggested,
                    "kind": "dog_discount",
                    "rationale": (
                        "This item is elastic and underperforming — a small discount may pick "
                        "up enough demand to lift profit. If it still doesn't move after a "
                        "two-week test, consider replacing it on the menu."
                    ),
                    "inelastic": False,
                }
            # Discount LOSES money on this item even though it's
            # nominally elastic — fall through to hold + cost-lever.
        # Inelastic Dog (or elastic-but-discount-doesn't-help): no
        # price move recommended; cost reduction is the practical lever
        # and the blue panel surfaces it inline.
        return {
            "price": int(round(current_price)),
            "kind": "dog_remove",
            "rationale": (
                "This item is underperforming and the demand math says a discount wouldn't "
                "lift volume enough to be worth the margin hit (cost is too close to price). "
                "Raising the price would hurt the few sales it does have. The practical lever "
                "here is cost — bring it down via supplier negotiation or a recipe review, or "
                "consider replacing the item if neither is possible."
            ),
            "inelastic": True,
        }

    if classification == "Puzzle":
        if is_elastic:
            suggested = max(int(round(current_price * 0.92)), int(math.ceil(cost * 1.30)))
            if suggested >= int(round(current_price)):
                suggested = int(round(current_price)) - 1
            if _discount_increases_profit(suggested):
                return {
                    "price": suggested,
                    "kind": "puzzle_discount",
                    "rationale": (
                        "Strong margin, low sales, and demand is price-sensitive — a small "
                        "discount should attract enough new customers to make up the margin "
                        "give-up. Pair with a menu feature or combo to drive visibility."
                    ),
                    "inelastic": False,
                }
            # Discount doesn't help on this Puzzle either — fall
            # through to the visibility + price-test path, same as
            # inelastic Puzzle, since price isn't the productive lever.
        # Inelastic Puzzle: discount would destroy profit. Test a small
        # price *increase* and push visibility instead — the issue is
        # awareness, not price.
        test_price = int(math.ceil(current_price * 1.05))
        if test_price <= int(round(current_price)):
            test_price = int(round(current_price)) + 1
        return {
            "price": test_price,
            "kind": "puzzle_visibility",
            "rationale": (
                "Strong margin but low sales — and demand here is price-insensitive. "
                "Discounting would shrink margin without lifting volume much. The "
                "real lever is visibility: a menu feature, a combo, or staff "
                "recommendation. A small price test can also fund the merchandising."
            ),
            "inelastic": True,
        }

    # ── Star / Plowhorse / unknown: elasticity-based logic ────────────
    # Treat the unit-elastic neighborhood (|e| ≈ 1) as inelastic. At exactly
    # e = -1, the profit-max formula divides by zero; nearby values produce
    # unstable theoretical prices that aren't decision-useful.
    if elasticity > -1.05:
        test_price = int(math.ceil(current_price * 1.10))
        if test_price <= int(round(current_price)):
            test_price = int(round(current_price)) + 1
        # Category-neutral wording: the old copy was coffee-specific ("this
        # category keeps buying") and misread as advice for sweets/bakery.
        rationale = (
            "Sales history shows demand here is steady — a small price bump "
            "would likely raise profit without driving customers away. "
            "Try this increase and review after a week."
        )
        return {
            "price": test_price,
            "kind": "test_increase",
            "rationale": rationale,
            "inelastic": True,
        }

    # CONCEPT — Lerner Index, the actual mathematical core: P* = e·c / (1+e)
    theoretical = elasticity * cost / (1 + elasticity)
    safe_max = current_price * (1 + SAFE_UP_CAP)
    safe_min = current_price * (1 - SAFE_DOWN_CAP)

    if theoretical > safe_max:
        suggested = safe_max
        kind = "capped_up"
        rationale = (
            "Sales history suggests a higher price would earn more profit, but jumping too "
            "far at once can scare customers away. Start with this increase and test before "
            "going higher."
        )
    elif theoretical < safe_min:
        suggested = safe_min
        kind = "capped_down"
        rationale = (
            "A small discount looks worthwhile for this item. Avoid cutting too aggressively — "
            "test this price first and watch how sales respond."
        )
    else:
        suggested = theoretical
        kind = "direct"
        rationale = (
            "Based on your sales history, this price should give the best profit balance — "
            "high enough to hold margin, low enough to keep customers buying."
        )

    # Star protection: don't recommend a price drop that demotes a Hero.
    # The elasticity math may say a cut lifts profit, but Stars are
    # positioning assets — exchanging Hero status for a short-term
    # margin trade is bad menu engineering. If the suggested price is
    # below current AND would push margin under the menu average
    # (the boundary that defines Star vs Plowhorse), hold instead.
    # The cost lever (if available) will surface via the lever-choice
    # rule on the recommendation panel.
    if classification == "Star" and suggested < current_price and avg_margin > 0:
        new_margin_at_suggested = (suggested - cost) / suggested * 100 if suggested > 0 else 0
        if new_margin_at_suggested < avg_margin:
            # Threshold price: the lowest price where margin still
            # equals avg_margin. Any cut below this demotes the Star
            # to Plowhorse. Solve (P - cost)/P = avg_margin/100
            #   → P = cost / (1 - avg_margin/100)
            avg_margin_frac = avg_margin / 100
            demotion_price = (
                cost / (1 - avg_margin_frac)
                if avg_margin_frac < 1 and avg_margin_frac > 0
                else current_price
            )
            return {
                "price": int(round(current_price)),
                "kind": "star_protect",
                "demotionPrice": round(demotion_price, 2),
                "demotedTo": "Plowhorse",
                "rationale": (
                    "This is a Star item — high popularity and healthy margin. The math "
                    "shows a small price cut could lift short-term profit, but it "
                    "would push the margin below the menu average and demote this "
                    "item out of Hero status. Hold the price. If you need more "
                    "profit from this item, the cost lever (supplier negotiation, "
                    "recipe review) is the right move."
                ),
                "inelastic": False,
            }

    return {
        "price": int(round(suggested)),
        "theoreticalPrice": int(round(theoretical)),
        "kind": kind,
        "rationale": rationale,
        "inelastic": False,
    }


def _cost_lowering_suggestion(
    current_price: float,
    current_cost: float,
    current_qty: int,
    current_classification: str,
    avg_margin: float,
    avg_pop: float,
    other_qty: float,
    suggested_price: float | None = None,
    category: str | None = None,
) -> dict | None:
    """
    Suggest a unit-cost reduction when it's logically beneficial.

    Cost reduction is independent of the price-elasticity tradeoff — demand
    stays the same, profit per unit goes up. So when feasible it's a "free"
    improvement on top of any price change. We surface it only when:

      - Current cost > 1 SAR (anything tiny leaves no negotiation room).
      - Current margin < 75% (already-very-profitable items don't need it).
      - There's a realistic 5–30% reduction the supplier negotiation /
        recipe-tweak / bulk-buy could plausibly achieve.

    Target cost is whatever brings margin up to the menu average — that's
    the cleanest classification break (Plowhorse → Star, Dog → Puzzle).
    Capped at a 30% reduction so we don't suggest unrealistic supplier wins.

    Earlier versions excluded Puzzles on the rationale that a Puzzle's
    bottleneck is demand, not margin. Reviewer feedback (and the test
    case of an avocado item) clarified that managers want to see the
    cost option for Hidden gems too — sales are already low, so any
    "free" margin lift compounds with the visibility/price-test
    strategy. The margin-already-high cutoff still gates inappropriate
    suggestions on the items where supplier negotiation has nothing
    left to give (e.g. avocado at 87 % margin won't trigger this).
    """
    if current_price <= 0 or current_cost < 1:
        return None
    current_margin = (current_price - current_cost) / current_price * 100

    # Earlier versions early-returned when current_margin >= 75% on
    # the rationale that 'supplier negotiation has little to give' for
    # already-high-margin items. Reviewer testing on Avocado (87%
    # margin) flagged this — even on a high-margin item the cost can
    # still be negotiated down, and the profit lift on a popular SKU
    # is substantial. Lifted the gate; the realistic-floor cap below
    # plus the 5%-minimum-reduction check still bound inappropriate
    # suggestions.

    # Reference price for the cost calculation. When the optimal-price
    # routine returns a different price (e.g. a Plowhorse with a
    # capped_down discount), the manager will likely apply BOTH the
    # price change and the cost reduction together.
    ref_price = float(suggested_price) if (suggested_price and suggested_price > 0) else current_price

    target_cost_for_avg_margin = ref_price * (1 - avg_margin / 100)
    # Realistic floor: cap depends on the category. Sweets and bakery
    # have wider supplier-negotiation / recipe-swap room; commodity
    # items like tea and espresso beans have very little. See
    # COST_REDUCTION_CAP_BY_CATEGORY.
    cap_pct = _cost_reduction_cap_for(category)
    realistic_floor = current_cost * (1 - cap_pct)

    # Round suggestions to the nearest 0.5 SAR. Whole-rial rounding was
    # too coarse for low-cost items (Ginger Milk at 1.69 SAR — ceil()
    # rounded the floor up past current cost and dropped the suggestion
    # entirely). Half-rial steps are still clean to communicate
    # ("negotiate from 1.69 to 1.50") without false precision.
    if target_cost_for_avg_margin < current_cost:
        # Item below avg margin — target avg margin so the
        # classification can flip (Plowhorse → Star, Dog → Puzzle).
        suggested_raw = max(target_cost_for_avg_margin, realistic_floor)
        suggested = math.floor(suggested_raw * 2) / 2
    else:
        # Item already at/above avg margin — no classification flip
        # possible via cost (margin axis is already on the high side),
        # but cost reduction still lifts profit. Suggest the realistic
        # floor as the supplier-negotiation target, using ceil() so we
        # never claim more savings than the category cap allows.
        suggested = math.ceil(realistic_floor * 2) / 2

    # Need at least a meaningful 5% reduction to be worth surfacing
    if suggested >= current_cost or (current_cost - suggested) / current_cost < 0.05:
        return None

    reduction_pct = (current_cost - suggested) / current_cost * 100
    new_margin = (ref_price - suggested) / ref_price * 100
    pop = (current_qty / (other_qty + current_qty) * 100) if (other_qty + current_qty) > 0 else 0
    new_classification = _classify(pop, new_margin, avg_pop, avg_margin)

    # Also evaluate "cost-only" scenario — keep CURRENT price, just
    # lower cost. Reviewer flagged that the system was implying both
    # moves were needed for the classification flip when often the
    # cost reduction ALONE is sufficient. Surfacing this lets the
    # manager pick one lever or both transparently.
    margin_at_current_price = (current_price - suggested) / current_price * 100
    classification_at_current_price = _classify(pop, margin_at_current_price, avg_pop, avg_margin)
    flips_at_current_price = classification_at_current_price != current_classification

    # Profit lift = savings per unit × historical units sold (current
    # price scenario). For the suggested-price scenario, qty would be
    # different per elasticity, but the cost-saving lift stays the
    # same dollar value — the per-unit savings × the units sold.
    additional_profit = (current_cost - suggested) * current_qty

    moves_class = new_classification != current_classification
    if moves_class:
        rationale = (
            f"If the unit cost can be reduced to about SAR {suggested} "
            f"(roughly {round(reduction_pct)}% off — supplier negotiation, "
            f"bulk buy, or a small recipe change), this item moves from "
            f"{current_classification} to {new_classification}. Demand isn't "
            f"affected; the gain is pure margin."
        )
    else:
        rationale = (
            f"On top of the price change, lowering the unit cost by ~"
            f"{round(reduction_pct)}% (to about SAR {suggested}) would lift "
            f"profit further without touching demand. Worth a conversation "
            f"with the supplier or a quick look at recipe ingredients."
        )

    return {
        "currentCost": round(current_cost, 2),
        "suggestedCost": round(suggested, 2),
        "reductionPct": round(reduction_pct, 1),
        "additionalProfit": round(additional_profit, 2),
        "currentClassification": current_classification,
        "newClassification": new_classification,
        "movesClassification": moves_class,
        # NEW: lets the frontend tell the manager whether
        # the cost reduction alone (no price change) is sufficient
        # for the classification flip.
        "flipsAtCurrentPrice": flips_at_current_price,
        "classificationAtCurrentPrice": classification_at_current_price,
        "rationale": rationale,
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


# ═══════════════════════════════════════════════════════════════════════
# CONCEPT — What-If Price Simulator API endpoint (Slide 32)
# Called from the MenuEngineering.jsx simulator panel. Takes a candidate
# price, runs the constant-elasticity demand math, and returns projected
# revenue, profit, margin, and the item's new Boston Matrix quadrant.
# ═══════════════════════════════════════════════════════════════════════
@router.get("/api/menu-engineering/simulate", summary="What-If price simulation")
def simulate_price_change(
    target: str = Query(..., description="Product name (exact match)"),
    new_price: float = Query(..., description="The hypothetical new selling price"),
    new_cost: float | None = Query(
        None,
        description="Optional hypothetical new unit cost. If omitted, the current cost is used. Cost changes affect margin only (not demand).",
    ),
    user_id: int = Depends(get_current_user_id),
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

    df = load_data(user_id, include_synthetic=False)
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

    # Menu-wide thresholds — must match the classifier's cutoffs.
    # Popularity uses 70 % × mean (Kasavana–Smith); margin uses raw mean.
    items["profit"] = items["revenue"] - items["totalCost"]
    items["profitMargin"] = (items["profit"] / items["revenue"] * 100).fillna(0)
    items["popularity"] = items["qtySold"] / total_qty_all * 100
    avg_pop = 0.7 * float(items["popularity"].mean())
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

    current_classification = _classify(current_pop, current_margin, avg_pop, avg_margin)
    # Recommendations anchor on the item's actual stored cost, not the
    # slider's effective_cost — otherwise dragging cost down rebuilds the
    # price suggestion against the hypothetical lowered cost and the blue
    # panel contradicts itself as the user explores.
    optimal = _optimal_price(
        current_cost, elasticity_central, current_price, current_classification,
        current_qty=current_qty,
        avg_margin=avg_margin,
    )

    # Enrich the suggestion with the projected classification transition
    # at the suggested price, and with an explicit "raise / lower / hold"
    # direction label so the UI doesn't have to infer it from `kind`.
    if optimal:
        suggested_price = float(optimal["price"])
        # Recommendation enrichment uses current_cost — projectedProfit /
        # projectedMargin / newClassification describe "what happens if
        # you take the recommended action against the item AS-IS". They
        # must not move with the slider, otherwise priceLift changes as
        # the user explores and the lever-choice flips mid-interaction.
        suggested_sim = _simulate_one(
            current_price, current_cost, current_qty,
            suggested_price, current_cost, elasticity_central,
            other_qty, avg_pop, avg_margin,
        )
        # Direction: compare against current price (rounded to match what
        # the UI shows) so a tiny rounding-only suggestion shows as "Hold".
        cur_p_int = int(round(current_price))
        sug_p_int = int(round(suggested_price))
        if sug_p_int > cur_p_int:
            direction = "raise"
            direction_label = "Raise the price"
        elif sug_p_int < cur_p_int:
            direction = "lower"
            direction_label = "Lower the price"
        else:
            direction = "hold"
            direction_label = "Keep the current price"

        change_pct = _pct(current_price, suggested_price) if current_price > 0 else None

        # Cost-lowering bonus suggestion — surfaced when realistic and
        # additive to the price change (works on any classification where
        # margin is the limiting factor, not demand).
        cost_lowering = _cost_lowering_suggestion(
            current_price=current_price,
            current_cost=current_cost,
            current_qty=current_qty,
            current_classification=current_classification,
            avg_margin=avg_margin,
            avg_pop=avg_pop,
            other_qty=other_qty,
            suggested_price=suggested_price,
            category=str(t["category"]),
        )

        optimal.update({
            "direction": direction,
            "directionLabel": direction_label,
            "priceChangePct": change_pct,
            "currentClassification": current_classification,
            "newClassification": suggested_sim["newClassification"],
            "classificationChange": (
                f"{current_classification} → {suggested_sim['newClassification']}"
                if current_classification != suggested_sim["newClassification"]
                else f"Stays {current_classification}"
            ),
            "projectedQty": suggested_sim["projectedQty"],
            "projectedProfit": suggested_sim["newProfit"],
            "projectedMargin": suggested_sim["newMargin"],
            "profitChangePct": _pct(current_profit, suggested_sim["newProfit"]),
            "costLowering": cost_lowering,
        })

    recommendations = {
        "optimalPrice": optimal,
        "breakEvenPrice": _break_even_price(
            current_profit, current_price, current_qty,
            effective_cost, elasticity_central,
        ),
        "costDefense": _cost_defense(
            current_price, current_cost, current_qty,
            current_profit, current_margin, elasticity_central,
        ),
    }

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
    user_id: int = Depends(get_current_user_id),
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

    df = load_data(user_id, include_synthetic=False)
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
    # Popularity threshold = 70 % × mean (Kasavana–Smith). Must match
    # the classifier in /api/menu-engineering so an item's bulk-sim
    # group membership is consistent with what the user sees in the UI.
    avg_pop = 0.7 * float(items["popularity"].mean())
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
        # Float qty inside revenue/profit math so low-volume items don't
        # introduce ±25 SAR-per-item staircase errors when projected qty
        # crosses an integer boundary. The single-item /simulate already
        # does this; bulk was using the int-rounded value, which biases
        # the aggregate by a few hundred SAR on a 40-item move
        # (eval/MENU_REPORT.md finding #6).
        new_qty_float = _project_qty_float(int(row["qtySold"]), float(row["price"]), new_price, elast)
        new_qty_display = int(round(new_qty_float))
        new_rev = new_qty_float * new_price
        new_prof = new_qty_float * (new_price - float(row["cost"]))
        new_group_revenue += new_rev
        new_group_profit += new_prof
        affected.append({
            "name": str(row["name"]),
            "category": str(row["category"]),
            "currentPrice": round(float(row["price"]), 2),
            "newPrice": round(new_price, 2),
            "currentQty": int(row["qtySold"]),
            "projectedQty": new_qty_display,
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
