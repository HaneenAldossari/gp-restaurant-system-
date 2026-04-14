"""Dashboard API — GET /api/dashboard"""
from fastapi import APIRouter, Query
from data_loader_db import load_data, filter_data

router = APIRouter(tags=["Dashboard"])

CATEGORY_COLORS = {
    "Hot Drinks": "#6366f1",
    "Cold Beverages": "#06b6d4",
    "Main Course": "#f59e0b",
    "Burgers": "#ef4444",
    "Pasta": "#8b5cf6",
    "Seafood": "#0ea5e9",
    "Salads": "#10b981",
    "Appetizers": "#f97316",
    "Desserts": "#f43f5e",
}


@router.get("/api/dashboard")
def dashboard(
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    category: str | None = Query(None),
):
    df = filter_data(load_data(), start_date, end_date, category)

    if df.empty:
        return {"error": "No data for the selected filters"}

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
    cat_rev["color"] = cat_rev["name"].map(CATEGORY_COLORS).fillna("#94a3b8")
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
