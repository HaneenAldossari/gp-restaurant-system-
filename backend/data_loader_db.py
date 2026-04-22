"""Load sales data from PostgreSQL — drop-in replacement for data_loader.py (Excel).

Per-user workspace isolation: each user gets its own DataFrame cache, keyed
by user_id. Rows are filtered via `uploads.user_id` so users never see each
other's uploads.
"""
import pandas as pd
from sqlalchemy import text

from db import get_engine

# Per-user in-memory cache — keyed by user_id.
_df_by_user: dict[int, pd.DataFrame] = {}

_QUERY = """
SELECT
    o.order_datetime      AS "Order Datetime",
    oi.quantity           AS "Quantity",
    oi.unit_price         AS "Unit Price",
    oi.unit_cost          AS unit_cost,
    p.name_en             AS "Product",
    c.name_en             AS "Category",
    o.order_reference     AS "Order ID",
    o.time_period         AS time_period,
    o.season              AS season,
    o.occasion            AS occasion
FROM order_items oi
JOIN orders     o ON oi.order_id    = o.id
JOIN uploads    u ON o.upload_id    = u.id
JOIN products   p ON oi.product_id  = p.id
JOIN categories c ON p.category_id  = c.id
WHERE u.user_id = :user_id
"""


def load_data(user_id: int = 1) -> pd.DataFrame:
    """Run one big JOIN (scoped to this user) and shape the result to match
    the legacy Excel DataFrame."""
    cached = _df_by_user.get(user_id)
    if cached is not None:
        return cached

    df = pd.read_sql(text(_QUERY), get_engine(), params={"user_id": user_id})
    df["Order Datetime"] = pd.to_datetime(df["Order Datetime"])
    df["Order Date"]  = df["Order Datetime"].dt.normalize()
    df["Order Time"]  = df["Order Datetime"].dt.time
    df["hour"]        = df["Order Datetime"].dt.hour
    df["day_name"]    = df["Order Date"].dt.day_name()

    df["Quantity"]     = df["Quantity"].astype(int)
    df["Unit Price"]   = df["Unit Price"].astype(float)
    df["unit_cost"]    = df["unit_cost"].astype(float)
    df["Total Price"]  = (df["Quantity"] * df["Unit Price"]).round(2)
    df["Product Cost"] = (df["Quantity"] * df["unit_cost"]).round(2)
    df["profit"]       = (df["Total Price"] - df["Product Cost"]).round(2)
    df["margin_pct"]   = ((df["profit"] / df["Total Price"]) * 100).round(2)

    _df_by_user[user_id] = df
    return df


def reload_data(user_id: int | None = None) -> None:
    """Invalidate cache(s) — call after a new upload.

    If `user_id` is given, drop only that user's cache. Otherwise clear every
    user's cache (useful after cross-cutting admin actions).
    """
    if user_id is None:
        _df_by_user.clear()
    else:
        _df_by_user.pop(user_id, None)


def filter_data(
    df: pd.DataFrame,
    start_date: str | None = None,
    end_date: str | None = None,
    category: str | None = None,
) -> pd.DataFrame:
    out = df
    if start_date:
        out = out[out["Order Date"] >= pd.to_datetime(start_date)]
    if end_date:
        out = out[out["Order Date"] <= pd.to_datetime(end_date)]
    if category and category.lower() != "all":
        out = out[out["Category"] == category]
    return out
