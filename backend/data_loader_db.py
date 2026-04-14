"""Load sales data from PostgreSQL — drop-in replacement for data_loader.py (Excel)."""
import pandas as pd
from db import get_engine

_df: pd.DataFrame | None = None

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
JOIN products   p ON oi.product_id  = p.id
JOIN categories c ON p.category_id  = c.id
"""


def load_data() -> pd.DataFrame:
    """Run one big JOIN and shape the result to match the legacy Excel DataFrame."""
    global _df
    if _df is not None:
        return _df

    df = pd.read_sql(_QUERY, get_engine())
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

    _df = df
    return _df


def reload_data() -> pd.DataFrame:
    """Invalidate the cache — call after a new upload."""
    global _df
    _df = None
    return load_data()


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
