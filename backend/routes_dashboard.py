"""Dashboard API — GET /api/dashboard"""
from fastapi import APIRouter, HTTPException, Query
from data_loader_db import load_data, filter_data

router = APIRouter(tags=["Dashboard"])

# Hand-picked preferences for categories we know about; anything else falls
# through to the rotating palette below so every slice gets its own color.
CATEGORY_COLOR_HINTS = {
    "Hot Drinks":         "#ef4444",   # red
    "Cold Drinks":        "#06b6d4",   # cyan
    "Espresso Drinks":    "#6366f1",   # indigo
    "Cold Coffee Drinks": "#0ea5e9",   # sky
    "Bakery":             "#f59e0b",   # amber
    "Sweets":             "#f43f5e",   # rose
    "Hot Sweets":         "#f97316",   # orange
    "Savory":             "#10b981",   # emerald
    # legacy names kept as aliases so older datasets keep their colors
    "Cold Beverages":     "#06b6d4",
    "Main Course":        "#f59e0b",
    "Burgers":            "#ef4444",
    "Pasta":              "#8b5cf6",
    "Seafood":            "#0ea5e9",
    "Salads":             "#10b981",
    "Appetizers":         "#f97316",
    "Desserts":           "#f43f5e",
}

# Fallback palette used for categories not in the hint map. Picks cycle so
# charts always have distinct slices regardless of dataset.
_PALETTE = [
    "#6366f1", "#06b6d4", "#f59e0b", "#ef4444", "#8b5cf6",
    "#0ea5e9", "#10b981", "#f97316", "#f43f5e", "#14b8a6",
    "#a855f7", "#eab308", "#22c55e", "#ec4899", "#3b82f6",
]


def _color_for(name: str, used: set[str]) -> str:
    """Return the hinted color if available, else the next unused palette color."""
    if name in CATEGORY_COLOR_HINTS:
        return CATEGORY_COLOR_HINTS[name]
    for c in _PALETTE:
        if c not in used:
            return c
    # If the palette is exhausted, wrap around (rare — would need >15 categories)
    return _PALETTE[len(used) % len(_PALETTE)]


@router.get("/api/dashboard")
def dashboard(
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    category: str | None = Query(None),
):
    df = filter_data(load_data(), start_date, end_date, category)

    if df.empty:
        raise HTTPException(status_code=404, detail="No data for the selected filters")

    total_revenue = round(float(df["Total Price"].sum()), 2)
    total_orders = int(df["Order ID"].nunique())
    avg_order = round(total_revenue / total_orders, 2) if total_orders else 0
    num_days = max(int((df["Order Date"].max() - df["Order Date"].min()).days), 1)
    avg_daily = round(total_revenue / num_days, 2)

    # Best / worst seller by revenue
    product_rev = df.groupby("Product")["Total Price"].sum()
    best_seller = product_rev.idxmax()
    best_qty = int(df[df["Product"] == best_seller]["Quantity"].sum())
    worst_seller = product_rev.idxmin()
    worst_qty = int(df[df["Product"] == worst_seller]["Quantity"].sum())

    # Busiest day
    day_rev = df.groupby("day_name")["Total Price"].sum()
    busiest_day = day_rev.idxmax()
    busiest_avg = round(float(day_rev.max()) / max(df[df["day_name"] == busiest_day]["Order Date"].nunique(), 1), 0)

    # --- Revenue trend (daily) ---
    daily = df.groupby(df["Order Date"].dt.date).agg(
        revenue=("Total Price", "sum"),
        orders=("Order ID", "nunique"),
    ).reset_index()
    daily.columns = ["date", "revenue", "orders"]
    daily["date"] = daily["date"].astype(str)
    daily_revenue = daily.sort_values("date").to_dict("records")

    # --- Revenue trend (monthly) ---
    monthly = df.groupby(df["Order Date"].dt.to_period("M")).agg(
        revenue=("Total Price", "sum"),
    ).reset_index()
    monthly.columns = ["period", "revenue"]
    monthly["month"] = monthly["period"].astype(str)
    monthly["revenue"] = monthly["revenue"].round(2)
    monthly["growth"] = monthly["revenue"].pct_change().fillna(0).mul(100).round(1)
    monthly_revenue = monthly[["month", "revenue", "growth"]].to_dict("records")

    # --- Sales by category ---
    cat_rev = df.groupby("Category")["Total Price"].sum().reset_index()
    cat_rev.columns = ["name", "value"]
    cat_rev["value"] = cat_rev["value"].round(2)

    # Assign a distinct color to each category — hinted first, then cycle
    # through a palette so no two slices share the same fallback gray.
    used: set[str] = set()
    colors: list[str] = []
    for name in cat_rev["name"]:
        c = _color_for(name, used)
        colors.append(c)
        used.add(c)
    cat_rev["color"] = colors
    sales_by_category = cat_rev.sort_values("value", ascending=False).to_dict("records")

    # --- Top 10 items ---
    item_stats = df.groupby("Product").agg(
        revenue=("Total Price", "sum"),
        qtySold=("Quantity", "sum"),
        category=("Category", "first"),
    ).reset_index()
    item_stats.columns = ["name", "revenue", "qtySold", "category"]
    top_by_revenue = item_stats.nlargest(10, "revenue")[["name", "revenue"]].to_dict("records")
    top_by_qty = item_stats.nlargest(10, "qtySold")[["name", "qtySold"]].to_dict("records")

    # --- Heatmap (day x hour) ---
    days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_short = {"Monday": "Mon", "Tuesday": "Tue", "Wednesday": "Wed", "Thursday": "Thu", "Friday": "Fri", "Saturday": "Sat", "Sunday": "Sun"}
    hour_labels = {h: f"{h % 12 or 12}{'AM' if h < 12 else 'PM'}" for h in range(9, 23)}

    heatmap_raw = df.groupby(["day_name", "hour"]).size().reset_index(name="value")
    heatmap_data = []
    for day_full in days_order:
        for h in range(9, 23):
            row = heatmap_raw[(heatmap_raw["day_name"] == day_full) & (heatmap_raw["hour"] == h)]
            val = int(row["value"].iloc[0]) if len(row) else 0
            heatmap_data.append({"day": day_short[day_full], "hour": hour_labels[h], "value": val})

    return {
        "kpis": {
            "totalRevenue": total_revenue,
            "totalOrders": total_orders,
            "avgOrderValue": avg_order,
            "avgDailyRevenue": avg_daily,
            "bestSeller": {"name": best_seller, "qty": best_qty},
            "worstSeller": {"name": worst_seller, "qty": worst_qty},
            "busiestDay": {"name": busiest_day, "avgRevenue": busiest_avg},
        },
        "dailyRevenue": daily_revenue,
        "monthlyRevenue": monthly_revenue,
        "salesByCategory": sales_by_category,
        "topByRevenue": top_by_revenue,
        "topByQty": top_by_qty,
        "heatmapData": heatmap_data,
        "categories": ["All"] + sorted(df["Category"].unique().tolist()),
    }
