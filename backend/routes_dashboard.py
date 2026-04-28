"""Dashboard API — GET /api/dashboard"""
from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user_id
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
    user_id: int = Depends(get_current_user_id),
):
    df = filter_data(load_data(user_id), start_date, end_date, category)

    if df.empty:
        raise HTTPException(status_code=404, detail="No data for the selected filters")

    total_revenue = round(float(df["Total Price"].sum()), 2)
    total_orders = int(df["Order ID"].nunique())
    avg_order = round(total_revenue / total_orders, 2) if total_orders else 0
    num_days = max(int((df["Order Date"].max() - df["Order Date"].min()).days), 1)
    avg_daily = round(total_revenue / num_days, 2)

    # ── Month-over-month deltas ─────────────────────────────────────────
    # Compare the last 30 days of data to the 30 days before that. We use
    # rolling windows (not calendar months) so the answer stays meaningful
    # regardless of when in the month the data ends.
    import pandas as _pd
    latest = df["Order Date"].max()
    window_end = latest
    window_start = latest - _pd.Timedelta(days=29)   # inclusive 30-day window
    prev_end = window_start - _pd.Timedelta(days=1)
    prev_start = prev_end - _pd.Timedelta(days=29)

    cur = df[(df["Order Date"] >= window_start) & (df["Order Date"] <= window_end)]
    prev = df[(df["Order Date"] >= prev_start) & (df["Order Date"] <= prev_end)]

    def _pct(now: float, before: float) -> float:
        if before <= 0:
            return 0.0
        return round(((now - before) / before) * 100, 1)

    cur_rev = round(float(cur["Total Price"].sum()), 2)
    prev_rev = round(float(prev["Total Price"].sum()), 2)
    cur_ord = int(cur["Order ID"].nunique())
    prev_ord = int(prev["Order ID"].nunique())
    cur_aov = round(cur_rev / cur_ord, 2) if cur_ord else 0.0
    prev_aov = round(prev_rev / prev_ord, 2) if prev_ord else 0.0

    revenue_change = _pct(cur_rev, prev_rev)
    orders_change = _pct(cur_ord, prev_ord)
    avg_order_change = _pct(cur_aov, prev_aov)

    # Whether we actually have enough history to compute a comparison.
    # If the uploaded data is shorter than 60 days, the "previous 30 days"
    # window falls partly or entirely outside the data — the % would be
    # noise. The frontend uses this flag to hide the trend arrow.
    has_comparison = (
        prev["Order Date"].min() >= df["Order Date"].min()
        if not prev.empty else False
    ) and prev_rev > 0

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

    # ── Day-of-week stats (orders + units) ──────────────────────────────
    # Only includes days that actually occurred in the selected window.
    # A 5-day window like Jan 28 → Feb 1 spans Fri/Sat/Sun/Mon/Tue only;
    # emitting Wed/Thu as "0 orders" would wrongly imply the café was
    # closed on those days.
    #
    # Days that DID occur but had no sales under the active category filter
    # still show with value 0 (legitimate "open but no sales" signal).
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_short_map = {"Monday": "Mon", "Tuesday": "Tue", "Wednesday": "Wed", "Thursday": "Thu",
                     "Friday": "Fri", "Saturday": "Sat", "Sunday": "Sun"}
    # Which day names actually appeared in the filtered date range? We use
    # date arithmetic (not groupby) so the answer is correct even when the
    # category filter removes all sales on one of the covered days.
    window_lo = df["Order Date"].min()
    window_hi = df["Order Date"].max()
    days_in_window = set(_pd.date_range(window_lo, window_hi).day_name())

    dow_grouped = df.groupby("day_name").agg(
        orders=("Order ID", "nunique"),
        units=("Quantity", "sum"),
        revenue=("Total Price", "sum"),
    )
    day_of_week_stats = []
    for d in day_order:
        if d not in days_in_window:
            continue
        day_of_week_stats.append({
            "name": day_short_map[d],
            "orders":  int(dow_grouped.loc[d, "orders"])  if d in dow_grouped.index else 0,
            "units":   int(dow_grouped.loc[d, "units"])   if d in dow_grouped.index else 0,
            "revenue": round(float(dow_grouped.loc[d, "revenue"]), 2) if d in dow_grouped.index else 0.0,
        })

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
    # `value` is kept as revenue for the "Revenue by Category" bar chart;
    # `units` is added so the donut can show units sold per category
    # instead of SAR.
    cat_rev = df.groupby("Category").agg(
        value=("Total Price", "sum"),
        units=("Quantity", "sum"),
    ).reset_index().rename(columns={"Category": "name"})
    cat_rev["value"] = cat_rev["value"].round(2)
    cat_rev["units"] = cat_rev["units"].astype(int)

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

    # --- All products ranked ---
    # Return every product, sorted highest-first. The frontend shows the
    # top 10 by default with an "expand" toggle to reveal the rest.
    item_stats = df.groupby("Product").agg(
        revenue=("Total Price", "sum"),
        qtySold=("Quantity", "sum"),
        category=("Category", "first"),
    ).reset_index()
    item_stats.columns = ["name", "revenue", "qtySold", "category"]
    item_stats["revenue"] = item_stats["revenue"].round(2)
    top_by_revenue = item_stats.sort_values("revenue", ascending=False)[["name", "revenue", "category"]].to_dict("records")
    top_by_qty = item_stats.sort_values("qtySold", ascending=False)[["name", "qtySold", "category"]].to_dict("records")

    # --- Heatmap (day x hour) ---
    # The hour range reflects the actual operating window of the uploaded
    # data: the first hour of the day the earliest order lands in, through
    # the last hour any order was recorded. Previously this was hardcoded
    # 9AM–10PM which didn't match cafés that open earlier/close later and
    # produced empty cells for any out-of-window activity.
    days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_short = {"Monday": "Mon", "Tuesday": "Tue", "Wednesday": "Wed", "Thursday": "Thu",
                 "Friday": "Fri", "Saturday": "Sat", "Sunday": "Sun"}

    # Saudi café operating hours: 6 AM → 2 AM (next day), wrapping past
    # midnight. Cells outside this window are hidden; closed hours within
    # the range still show as empty (value=0) because they're part of the
    # café's "open day" even if no orders landed there.
    OPEN_HOUR = 6
    CLOSE_HOUR = 2
    if CLOSE_HOUR < OPEN_HOUR:
        hour_sequence = list(range(OPEN_HOUR, 24)) + list(range(0, CLOSE_HOUR + 1))
    else:
        hour_sequence = list(range(OPEN_HOUR, CLOSE_HOUR + 1))

    hour_labels = {
        h: f"{h % 12 or 12}{'AM' if h < 12 else 'PM'}"
        for h in hour_sequence
    }
    heatmap_raw = df.groupby(["day_name", "hour"]).size().reset_index(name="value")
    heatmap_data = []
    for day_full in days_order:
        for h in hour_sequence:
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
            # Last-30-days values — shown as the headline KPI so the
            # percentage comparison below refers to the same window.
            "last30Revenue": cur_rev,
            "last30Orders": cur_ord,
            "last30AvgOrderValue": cur_aov,
            "last30Start": window_start.date().isoformat(),
            "last30End": window_end.date().isoformat(),
            # MoM deltas (last 30 days vs previous 30 days). Frontend hides
            # the trend when hasComparison is false.
            "revenueChange": revenue_change,
            "ordersChange": orders_change,
            "avgOrderChange": avg_order_change,
            "hasComparison": has_comparison,
        },
        "dailyRevenue": daily_revenue,
        "monthlyRevenue": monthly_revenue,
        "salesByCategory": sales_by_category,
        "topByRevenue": top_by_revenue,
        "topByQty": top_by_qty,
        "heatmapData": heatmap_data,
        "dayOfWeekStats": day_of_week_stats,
        "categories": ["All"] + sorted(df["Category"].unique().tolist()),
    }
