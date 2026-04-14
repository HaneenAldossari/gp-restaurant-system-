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
