"""
Forecasting API — /api/forecast/item, /api/forecast/category, /api/forecast/total

Uses Noura's hybrid Prophet model from backend/prophet_model.py verbatim.
The ONLY modification is the data source: PostgreSQL instead of Excel.

Noura's original logic is preserved exactly:
  - High-volume items (avg daily qty >= 3): per-product Prophet with 80/20 split
  - Low-volume items (avg daily qty < 3): per-category Prophet, distributed
    across products by historical sales share
  - 7-day forecast horizon extending from the end of the training data

Results are cached in-memory after first computation AND persisted to the
`forecasts` table so repeat calls return instantly.
"""

import json
import threading
from datetime import date

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query
from prophet import Prophet
from sklearn.metrics import mean_absolute_error
from sqlalchemy import text

from data_loader_db import load_data
from db import get_engine

router = APIRouter(tags=["Forecasting"])

DEFAULT_USER_ID = 1
_cache: dict[int, pd.DataFrame] = {}  # periods -> results DataFrame
_cache_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────
# Noura's hybrid_forecast() — adapted to read from PostgreSQL (DataFrame
# input) instead of pd.read_excel(). Logic is otherwise identical.
# ─────────────────────────────────────────────────────────────────────────
def hybrid_forecast(df: pd.DataFrame, periods: int = 7) -> pd.DataFrame:
    """
    Run Noura's hybrid Prophet forecasting.

    `df` must contain columns: name, ds, quantity, categ_EN. In this system
    the DataFrame comes from PostgreSQL via data_loader_db.load_data(); the
    columns are renamed before calling this function.
    """
    df = df.copy()
    df["ds"] = pd.to_datetime(df["ds"])

    full_dates = pd.date_range(start=df["ds"].min(), end=df["ds"].max(), freq="D")

    results = []

    daily_sales = df.groupby(["name", "ds"])["quantity"].sum().reset_index()
    avg_sales = daily_sales.groupby("name")["quantity"].mean()

    high_items = avg_sales[avg_sales >= 3].index
    low_items = avg_sales[avg_sales < 3].index

    print("High-volume items:", len(high_items))
    print("Low-volume items:", len(low_items))

    overall_errors = []

    # ── High-volume items: per-product Prophet ──
    for product in high_items:
        product_df = df[df["name"] == product].copy()
        category = product_df["categ_EN"].iloc[0]

        product_df = product_df.groupby("ds")["quantity"].sum().reset_index()
        product_df = product_df.set_index("ds").reindex(full_dates, fill_value=0).reset_index()
        product_df.columns = ["ds", "y"]

        if len(product_df) < 10:
            continue

        split = int(len(product_df) * 0.8)
        train = product_df[:split]
        test = product_df[split:]

        model = Prophet()
        model.fit(train)

        forecast_test = model.predict(test)

        y_true = test["y"].values
        y_pred = forecast_test["yhat"].values

        mask = y_true > 0
        y_true = y_true[mask]
        y_pred = y_pred[mask]

        if len(y_true) == 0:
            continue

        mae = mean_absolute_error(y_true, y_pred)
        avg_actual = np.mean(y_true)
        error_percentage = (mae / avg_actual) * 100 if avg_actual > 0 else 0

        overall_errors.append(error_percentage)

        future = model.make_future_dataframe(periods=periods)
        forecast = model.predict(future)
        future_preds = forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(periods)

        for _, row in future_preds.iterrows():
            results.append({
                "product": product,
                "category": category,
                "date": row["ds"],
                "predicted_sales": max(row["yhat"], 0),
                "lower_bound": max(row["yhat_lower"], 0),
                "upper_bound": max(row["yhat_upper"], 0),
                "type": "high_item_model",
                "MAE": float(mae),
                "Error_%": float(error_percentage),
            })

    # ── Low-volume items: per-category Prophet, distributed by product share ──
    for category in df["categ_EN"].unique():
        cat_df = df[df["categ_EN"] == category].copy()
        cat_df = cat_df[cat_df["name"].isin(low_items)]

        if cat_df.empty:
            continue

        cat_daily = cat_df.groupby("ds")["quantity"].sum().reset_index()
        cat_daily = cat_daily.set_index("ds").reindex(full_dates, fill_value=0).reset_index()
        cat_daily.columns = ["ds", "y"]

        if len(cat_daily) < 10:
            continue

        split = int(len(cat_daily) * 0.8)
        train = cat_daily[:split]
        test = cat_daily[split:]

        model = Prophet()
        model.fit(train)

        forecast_test = model.predict(test)

        y_true = test["y"].values
        y_pred = forecast_test["yhat"].values

        mask = y_true > 0
        y_true = y_true[mask]
        y_pred = y_pred[mask]

        if len(y_true) == 0:
            continue

        mae = mean_absolute_error(y_true, y_pred)
        avg_actual = np.mean(y_true)
        error_percentage = (mae / avg_actual) * 100 if avg_actual > 0 else 0

        overall_errors.append(error_percentage)

        future = model.make_future_dataframe(periods=periods)
        forecast = model.predict(future)
        future_preds = forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(periods)

        product_dist = cat_df.groupby("name")["quantity"].sum()
        product_dist = product_dist / product_dist.sum()

        for _, row in future_preds.iterrows():
            for product, ratio in product_dist.items():
                results.append({
                    "product": product,
                    "category": category,
                    "date": row["ds"],
                    "predicted_sales": max(row["yhat"] * ratio, 0),
                    "lower_bound": max(row["yhat_lower"], 0),
                    "upper_bound": max(row["yhat_upper"], 0),
                    "type": "category_distribution",
                    "MAE": float(mae),
                    "Error_%": float(error_percentage),
                })

    final_df = pd.DataFrame(results)
    if not final_df.empty:
        final_df = final_df.sort_values(by=["product", "date"]).reset_index(drop=True)

    if overall_errors:
        overall_avg = sum(overall_errors) / len(overall_errors)
        print("Overall Error %:", round(overall_avg, 1), "%")

    return final_df


# ─────────────────────────────────────────────────────────────────────────
# Cache — load Noura's predictions once, reuse for all endpoints
# ─────────────────────────────────────────────────────────────────────────
def _load_db_frame() -> pd.DataFrame:
    """Pull sales from PostgreSQL and shape it for hybrid_forecast()."""
    df = load_data()
    return pd.DataFrame({
        "ds": df["Order Date"],
        "name": df["Product"],
        "quantity": df["Quantity"],
        "categ_EN": df["Category"],
    })


def _get_predictions(periods: int) -> pd.DataFrame:
    """Return the full predictions DataFrame for a horizon; compute if missing."""
    with _cache_lock:
        if periods in _cache:
            return _cache[periods]

        df = _load_db_frame()
        predictions = hybrid_forecast(df, periods=periods)
        _cache[periods] = predictions
        _persist_cache(predictions, periods)
        return predictions


def _persist_cache(predictions: pd.DataFrame, periods: int) -> None:
    """Write a single 'total' entry into the forecasts table for audit/history."""
    if predictions.empty:
        return
    payload = predictions.assign(date=predictions["date"].astype(str)).to_dict(orient="records")
    metrics = {
        "mean_error_pct": float(predictions["Error_%"].mean()) if "Error_%" in predictions else None,
        "n_rows": int(len(predictions)),
    }
    q = text("""
        INSERT INTO forecasts
            (user_id, target_type, target_id, train_start, train_end,
             horizon_days, model_used, metrics_json, result_json)
        VALUES
            (:uid, 'total', NULL, :ts, :te, :h, 'Prophet', CAST(:m AS JSONB), CAST(:r AS JSONB))
    """)
    with get_engine().begin() as conn:
        conn.execute(q, {
            "uid": DEFAULT_USER_ID,
            "ts": predictions["date"].min(),
            "te": predictions["date"].max(),
            "h": periods,
            "m": json.dumps(metrics),
            "r": json.dumps(payload, default=str),
        })


def invalidate_cache() -> None:
    """Clear cached predictions (call after a new upload)."""
    with _cache_lock:
        _cache.clear()


# ─────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────
@router.get("/api/forecast/item", summary="Forecast a single menu item")
def forecast_item(
    target: str = Query(..., description="Product name (exact match)"),
    period: int = Query(7, description="Forecast horizon in days"),
):
    """Forecast daily sales quantity for a specific item using Noura's hybrid model."""
    preds = _get_predictions(period)
    rows = preds[preds["product"] == target]
    if rows.empty:
        return {"error": f"Product '{target}' not found in forecast output"}

    category = rows["category"].iloc[0]
    daily = [
        {
            "date": str(r["date"].date() if hasattr(r["date"], "date") else r["date"])[:10],
            "predicted_sales": round(float(r["predicted_sales"]), 2),
            "lower_bound": round(float(r["lower_bound"]), 2),
            "upper_bound": round(float(r["upper_bound"]), 2),
        }
        for _, r in rows.iterrows()
    ]

    return {
        "scope": "item",
        "target": target,
        "category": category,
        "period": period,
        "model": rows["type"].iloc[0],
        "mae": round(float(rows["MAE"].iloc[0]), 2),
        "errorPct": round(float(rows["Error_%"].iloc[0]), 2),
        "totalPredictedSales": round(float(rows["predicted_sales"].sum()), 2),
        "dailyPredictions": daily,
    }


@router.get("/api/forecast/category", summary="Forecast a category")
def forecast_category(
    target: str = Query(..., description="Category name (e.g. 'Hot Drinks')"),
    period: int = Query(7, description="Forecast horizon in days"),
):
    """Aggregate every item in the category, sort by predicted revenue."""
    preds = _get_predictions(period)
    rows = preds[preds["category"] == target]
    if rows.empty:
        return {"error": f"Category '{target}' not found in forecast output"}

    per_product = rows.groupby("product").agg(
        totalPredictedSales=("predicted_sales", "sum"),
        modelType=("type", "first"),
    ).reset_index().sort_values("totalPredictedSales", ascending=False)

    daily_totals = rows.groupby("date")["predicted_sales"].sum().reset_index()

    return {
        "scope": "category",
        "target": target,
        "period": period,
        "itemCount": int(per_product.shape[0]),
        "totalPredictedSales": round(float(rows["predicted_sales"].sum()), 2),
        "items": [
            {
                "name": r["product"],
                "totalPredictedSales": round(float(r["totalPredictedSales"]), 2),
                "model": r["modelType"],
            }
            for _, r in per_product.iterrows()
        ],
        "chartData": [
            {
                "date": str(r["date"].date() if hasattr(r["date"], "date") else r["date"])[:10],
                "predicted": round(float(r["predicted_sales"]), 2),
            }
            for _, r in daily_totals.iterrows()
        ],
    }


@router.get("/api/forecast/total", summary="Forecast total restaurant sales")
def forecast_total(period: int = Query(7, description="Forecast horizon in days")):
    """Grand total across all products."""
    preds = _get_predictions(period)
    if preds.empty:
        return {"error": "No forecast data available"}

    per_category = preds.groupby("category").agg(
        totalPredictedSales=("predicted_sales", "sum"),
        itemCount=("product", "nunique"),
    ).reset_index().sort_values("totalPredictedSales", ascending=False)

    daily_totals = preds.groupby("date")["predicted_sales"].sum().reset_index()

    return {
        "scope": "total",
        "period": period,
        "totalPredictedSales": round(float(preds["predicted_sales"].sum()), 2),
        "categoryCount": int(per_category.shape[0]),
        "itemCount": int(preds["product"].nunique()),
        "categories": [
            {
                "name": r["category"],
                "totalPredictedSales": round(float(r["totalPredictedSales"]), 2),
                "itemCount": int(r["itemCount"]),
            }
            for _, r in per_category.iterrows()
        ],
        "chartData": [
            {
                "date": str(r["date"].date() if hasattr(r["date"], "date") else r["date"])[:10],
                "predicted": round(float(r["predicted_sales"]), 2),
            }
            for _, r in daily_totals.iterrows()
        ],
    }
