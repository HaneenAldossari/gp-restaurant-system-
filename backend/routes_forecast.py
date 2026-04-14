"""
Forecasting API — /api/forecast/item, /api/forecast/category, /api/forecast/total

Calls Noura's run_forecast() function from prophet_model.py with PostgreSQL data.
The ML logic itself is untouched in prophet_model.py — this file only:
  - Loads sales rows from PostgreSQL via data_loader_db
  - Reshapes them into the columns Noura's function expects
  - Caches the resulting predictions DataFrame in memory + persists once to
    the `forecasts` table
  - Slices the predictions per endpoint (item / category / total)
"""

import json
import threading
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query
from sqlalchemy import text

from data_loader_db import load_data
from db import get_engine
from prophet_model import run_forecast

router = APIRouter(tags=["Forecasting"])

DEFAULT_USER_ID = 1
_predictions_cache: pd.DataFrame | None = None
_category_lookup: dict[str, str] = {}  # product -> category
_data_end_date: pd.Timestamp | None = None  # last actual sale date in the data
_cache_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────
# Cache management
# ─────────────────────────────────────────────────────────────────────────
def _build_input_frame() -> tuple[pd.DataFrame, dict[str, str]]:
    """
    Pull sales from PostgreSQL and shape them for Noura's run_forecast().
    Returns (model_input_df, product_to_category_map).
    """
    df = load_data()

    # Drop rows missing the columns Noura's model needs
    needed = df.dropna(subset=["time_period", "season", "occasion"]).copy()

    model_df = pd.DataFrame({
        "name": needed["Product"],
        "date": needed["Order Date"],
        "quantity": needed["Quantity"],
        "season": needed["season"],
        "occasion": needed["occasion"],
        "time_period": needed["time_period"],
    })

    cat_map = (
        df[["Product", "Category"]]
        .drop_duplicates()
        .set_index("Product")["Category"]
        .to_dict()
    )
    return model_df, cat_map


def _get_predictions() -> pd.DataFrame:
    """Return cached predictions, computing them on first call."""
    global _predictions_cache, _category_lookup, _data_end_date
    with _cache_lock:
        if _predictions_cache is not None:
            return _predictions_cache

        model_df, cat_map = _build_input_frame()
        if model_df.empty:
            raise RuntimeError(
                "No data available with season/occasion/time_period populated. "
                "Re-upload the sales file via /api/upload."
            )

        _data_end_date = pd.to_datetime(model_df["date"]).max()
        predictions = run_forecast(model_df, save_csv=False)
        _predictions_cache = predictions
        _category_lookup = cat_map
        _persist_run(predictions)
        return predictions


def _slice_future(preds: pd.DataFrame, period: int) -> pd.DataFrame:
    """Take only future predictions starting AFTER the global data end date."""
    if _data_end_date is None:
        return preds[preds["type"] == "future"].copy()
    fut = preds[preds["ds"] > _data_end_date].copy()
    if period:
        cutoff = _data_end_date + pd.Timedelta(days=period)
        fut = fut[fut["ds"] <= cutoff]
    return fut


def _persist_run(predictions: pd.DataFrame) -> None:
    """Save one summary row to the forecasts table for audit/history."""
    if predictions.empty:
        return
    metrics = {
        "n_products": int(predictions["product"].nunique()),
        "n_rows": int(len(predictions)),
        "mean_mae": float(predictions["MAE"].dropna().mean()) if "MAE" in predictions else None,
    }
    q = text("""
        INSERT INTO forecasts
            (user_id, target_type, target_id, train_start, train_end,
             horizon_days, model_used, metrics_json, result_json)
        VALUES
            (:uid, 'total', NULL, :ts, :te, 30, 'Prophet+regressors',
             CAST(:m AS JSONB), CAST('{}' AS JSONB))
    """)
    with get_engine().begin() as conn:
        conn.execute(q, {
            "uid": DEFAULT_USER_ID,
            "ts": predictions["ds"].min(),
            "te": predictions["ds"].max(),
            "m": json.dumps(metrics),
        })


def invalidate_cache() -> None:
    """Clear the cache. Called by the upload endpoint after new data lands."""
    global _predictions_cache, _category_lookup, _data_end_date
    with _cache_lock:
        _predictions_cache = None
        _category_lookup = {}
        _data_end_date = None


# ─────────────────────────────────────────────────────────────────────────
# Helpers to slice predictions into API responses
# ─────────────────────────────────────────────────────────────────────────
def _serialize_rows(rows: pd.DataFrame, only_future: bool = True) -> list[dict[str, Any]]:
    if only_future:
        rows = rows[rows["type"] == "future"]
    return [
        {
            "date": str(r["ds"].date() if hasattr(r["ds"], "date") else r["ds"])[:10],
            "time_period": str(r["time_period"]),
            "predicted_quantity": int(r["yhat"]),
        }
        for _, r in rows.iterrows()
    ]


# ─────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────
@router.get("/api/forecast/item", summary="Forecast a single menu item")
def forecast_item(
    target: str = Query(..., description="Product name (exact match)"),
    period: int = Query(7, description="Forecast horizon in days (1-30, model produces 30)"),
):
    """
    Per-item forecast using Noura's hybrid Prophet model. Returns predictions
    aggregated daily plus the time_period breakdown (morning/afternoon/evening/night).
    """
    try:
        preds = _get_predictions()
    except RuntimeError as e:
        return {"error": str(e)}

    item_rows = preds[preds["product"] == target]
    if item_rows.empty:
        return {"error": f"Product '{target}' not in forecast output"}

    future = _slice_future(item_rows, period)

    daily = (
        future.groupby(future["ds"].astype(str))["yhat"]
        .sum()
        .reset_index()
        .rename(columns={"ds": "date", "yhat": "predicted_quantity"})
    )

    return {
        "scope": "item",
        "target": target,
        "category": _category_lookup.get(target),
        "period": period,
        "model": "Prophet+regressors",
        "mae": float(item_rows["MAE"].iloc[0]) if pd.notna(item_rows["MAE"].iloc[0]) else None,
        "totalPredictedQuantity": int(future["yhat"].sum()),
        "dailyPredictions": [
            {"date": r["date"], "predicted_quantity": int(r["predicted_quantity"])}
            for _, r in daily.iterrows()
        ],
        "timePeriodBreakdown": _serialize_rows(future, only_future=False),
    }


@router.get("/api/forecast/category", summary="Forecast all items in a category")
def forecast_category(
    target: str = Query(..., description="Category name (e.g. 'Hot Drinks')"),
    period: int = Query(7, description="Forecast horizon in days (1-30)"),
):
    """Aggregate every item in the category, sort by predicted quantity."""
    try:
        preds = _get_predictions()
    except RuntimeError as e:
        return {"error": str(e)}

    products_in_cat = [p for p, c in _category_lookup.items() if c == target]
    if not products_in_cat:
        return {"error": f"Category '{target}' not found"}

    cat_rows = _slice_future(preds[preds["product"].isin(products_in_cat)], period)

    per_product = (
        cat_rows.groupby("product")["yhat"]
        .sum()
        .reset_index()
        .sort_values("yhat", ascending=False)
        .rename(columns={"yhat": "totalPredictedQuantity"})
    )

    daily_totals = (
        cat_rows.groupby(cat_rows["ds"].astype(str))["yhat"]
        .sum()
        .reset_index()
        .rename(columns={"ds": "date", "yhat": "predicted_quantity"})
    )

    return {
        "scope": "category",
        "target": target,
        "period": period,
        "itemCount": int(per_product.shape[0]),
        "totalPredictedQuantity": int(cat_rows["yhat"].sum()),
        "items": [
            {"name": r["product"], "totalPredictedQuantity": int(r["totalPredictedQuantity"])}
            for _, r in per_product.iterrows()
        ],
        "chartData": [
            {"date": r["date"], "predicted": int(r["predicted_quantity"])}
            for _, r in daily_totals.iterrows()
        ],
    }


@router.get("/api/forecast/total", summary="Forecast total restaurant sales")
def forecast_total(period: int = Query(7, description="Forecast horizon in days (1-30)")):
    """Grand total across every product."""
    try:
        preds = _get_predictions()
    except RuntimeError as e:
        return {"error": str(e)}

    future = _slice_future(preds, period)
    future["category"] = future["product"].map(_category_lookup)

    per_category = (
        future.groupby("category")
        .agg(
            totalPredictedQuantity=("yhat", "sum"),
            itemCount=("product", "nunique"),
        )
        .reset_index()
        .sort_values("totalPredictedQuantity", ascending=False)
    )

    daily_totals = (
        future.groupby(future["ds"].astype(str))["yhat"]
        .sum()
        .reset_index()
        .rename(columns={"ds": "date", "yhat": "predicted_quantity"})
    )

    return {
        "scope": "total",
        "period": period,
        "totalPredictedQuantity": int(future["yhat"].sum()),
        "categoryCount": int(per_category.shape[0]),
        "itemCount": int(future["product"].nunique()),
        "categories": [
            {
                "name": r["category"],
                "totalPredictedQuantity": int(r["totalPredictedQuantity"]),
                "itemCount": int(r["itemCount"]),
            }
            for _, r in per_category.iterrows()
        ],
        "chartData": [
            {"date": r["date"], "predicted": int(r["predicted_quantity"])}
            for _, r in daily_totals.iterrows()
        ],
    }
