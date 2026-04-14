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
PRICE_ELASTICITY = -1.5  # Hardcoded for v1. Negative because demand drops as price rises.


def _classify(popularity: float, margin: float, avg_pop: float, avg_margin: float) -> str:
    hi_pop = popularity >= avg_pop
    hi_mar = margin >= avg_margin
    if hi_pop and hi_mar: return "Star"
    if hi_pop and not hi_mar: return "Plowhorse"
    if not hi_pop and hi_mar: return "Puzzle"
    return "Dog"


@router.get("/api/menu-engineering/simulate", summary="What-If price simulation")
def simulate_price_change(
    target: str = Query(..., description="Product name (exact match)"),
    new_price: float = Query(..., description="The hypothetical new selling price"),
):
    """
    Simulate moving a product's price up or down and see:
      - The projected new quantity sold (using -1.5 price elasticity)
      - New revenue, profit, margin, popularity
      - Whether it changes Boston Matrix classification (Star/Plowhorse/Puzzle/Dog)

    Elasticity formula: pct_qty_change = elasticity × pct_price_change
    With elasticity = -1.5, a +10% price hike projects an -15% drop in quantity.
    """
    if new_price <= 0:
        raise HTTPException(status_code=400, detail="new_price must be positive")

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

    # Apply constant-elasticity demand model:
    #   new_qty = old_qty * (new_price / old_price) ^ elasticity
    # This is safer than linear elasticity for large price changes (always
    # stays positive, asymptotes nicely instead of clipping to zero).
    if current_price > 0:
        ratio = new_price / current_price
        projected_qty = max(0, int(round(current_qty * (ratio ** PRICE_ELASTICITY))))
    else:
        projected_qty = current_qty

    new_revenue = projected_qty * new_price
    new_total_cost = projected_qty * current_cost
    new_profit = new_revenue - new_total_cost
    new_margin = (new_profit / new_revenue * 100) if new_revenue else 0

    # Recompute popularity against the rest of the menu (other items unchanged)
    other_qty = total_qty_all - current_qty
    new_total_qty = other_qty + projected_qty
    new_pop = (projected_qty / new_total_qty * 100) if new_total_qty else 0

    # Classification thresholds based on the current menu (we treat the rest
    # of the menu as fixed — only the target item moved).
    items["profit"] = items["revenue"] - items["totalCost"]
    items["profitMargin"] = (items["profit"] / items["revenue"] * 100).fillna(0)
    items["popularity"] = items["qtySold"] / total_qty_all * 100
    avg_pop = float(items["popularity"].mean())
    avg_margin = float(items["profitMargin"].mean())

    current_classification = _classify(current_pop, current_margin, avg_pop, avg_margin)
    new_classification = _classify(new_pop, new_margin, avg_pop, avg_margin)

    def _pct(old, new):
        if old == 0:
            return None
        return round((new - old) / old * 100, 1)

    return {
        "elasticity": PRICE_ELASTICITY,
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
        "simulated": {
            "newPrice": round(new_price, 2),
            "projectedQty": projected_qty,
            "newRevenue": round(new_revenue, 2),
            "newProfit": round(new_profit, 2),
            "newMargin": round(new_margin, 2),
            "newPopularity": round(new_pop, 2),
            "newClassification": new_classification,
        },
        "delta": {
            "priceChangePct": _pct(current_price, new_price),
            "qtyChangePct": _pct(current_qty, projected_qty),
            "revenueChangePct": _pct(current_revenue, new_revenue),
            "profitChangePct": _pct(current_profit, new_profit),
            "classificationChange": (
                f"{current_classification} → {new_classification}"
                if current_classification != new_classification
                else f"Stays {current_classification}"
            ),
        },
    }
