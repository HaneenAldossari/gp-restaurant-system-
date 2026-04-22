"""
Forecasting API — /api/forecast/item, /api/forecast/category, /api/forecast/total

Calls Noura's run_forecast() function from prophet_model.py with PostgreSQL data.
The ML logic itself is untouched in prophet_model.py — this file only:
  - Loads sales rows from PostgreSQL via data_loader_db
  - Reshapes them into the columns Noura's function expects
  - Caches the resulting predictions DataFrame in memory AND on disk
    (cache/predictions_user_{uid}.pkl) so server restarts don't trigger retraining
  - Persists one summary row per run to the `forecasts` table
  - Slices the predictions per endpoint (item / category / total)

Per-user isolation:
  Every cache dict is keyed by user_id. Each user gets its own pickle file.
  Helpers thread a `user_id` parameter through; route handlers read the header
  via the `get_current_user_id` dependency.

Cache invalidation:
  - The pickle stores a "data signature" (latest upload id + row count) scoped
    to that user.
  - On startup or first request, we compare against the current DB signature.
  - If they match → load from disk, no retraining.
  - If they differ → retrain and overwrite the pickle.
"""

import json
import pickle
import threading
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from auth import get_current_user_id
from data_loader_db import load_data
from db import get_engine
from prophet_model import run_forecast

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _cache_file(user_id: int) -> Path:
    return CACHE_DIR / f"predictions_user_{user_id}.pkl"


router = APIRouter(tags=["Forecasting"])


# ─────────────────────────────────────────────────────────────────────────
# Regressor helpers — mirror Noura's compute_season / compute_occasion so
# we can label each forecast date with the values her model used as inputs.
# ─────────────────────────────────────────────────────────────────────────
def _season_for(dt) -> str:
    m = pd.Timestamp(dt).month
    if m in (12, 1, 2): return "Winter"
    if m in (3, 4, 5):  return "Spring"
    if m in (6, 7, 8):  return "Summer"
    return "Autumn"


def _occasion_for(dt) -> str:
    d = pd.Timestamp(dt).date()
    try:
        from hijri_converter import Gregorian
        h = Gregorian(d.year, d.month, d.day).to_hijri()
        if h.month == 9:                         return "Ramadan"
        if h.month == 10 and 1 <= h.day <= 3:    return "Eid al-Fitr"
        if h.month == 12 and 10 <= h.day <= 13:  return "Eid al-Adha"
    except Exception:
        pass
    if d.month == 9 and d.day == 23: return "Saudi National Day"
    if d.weekday() in (4, 5):        return "Weekend"
    return "Normal Day"


def _regressors_for_dates(dates: list) -> list[dict]:
    """Return per-date regressor values (season + occasion) for a forecast window."""
    out = []
    for d in dates:
        out.append({
            "date": str(pd.Timestamp(d).date()),
            "season": _season_for(d),
            "occasion": _occasion_for(d),
        })
    return out


# ── Per-user caches ──────────────────────────────────────────────────────
_predictions_cache: dict[int, pd.DataFrame] = {}
_category_lookup: dict[int, dict[str, str]] = {}       # user_id -> (product -> category)
_data_end_date: dict[int, pd.Timestamp] = {}            # user_id -> last actual sale date
_price_cache: dict[int, dict[str, tuple[float, float]]] = {}  # user_id -> (product -> (unit_price, unit_cost))
_baseline_cache: dict[int, dict[str, float]] = {}       # user_id -> (product -> historical units/day)
_cache_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────
# Cache management
# ─────────────────────────────────────────────────────────────────────────
def _build_input_frame(user_id: int) -> tuple[pd.DataFrame, dict[str, str]]:
    """
    Pull sales from PostgreSQL (scoped to this user) and shape them for
    Noura's run_forecast(). Returns (model_input_df, product_to_category_map).
    """
    df = load_data(user_id)

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


def _current_signature(user_id: int) -> dict[str, Any]:
    """A fingerprint of this user's data — used to invalidate the disk cache."""
    with get_engine().connect() as conn:
        latest = conn.execute(text("""
            SELECT
                COALESCE(MAX(uploads.id), 0)  AS upload_id,
                COALESCE((
                    SELECT COUNT(*) FROM order_items oi
                    JOIN orders o ON oi.order_id = o.id
                    JOIN uploads u ON o.upload_id = u.id
                    WHERE u.user_id = :uid
                ), 0) AS item_count
            FROM uploads
            WHERE uploads.user_id = :uid
        """), {"uid": user_id}).fetchone()
    return {"upload_id": int(latest[0]), "item_count": int(latest[1])}


def _try_load_from_disk(user_id: int) -> tuple[pd.DataFrame, dict[str, str], pd.Timestamp] | None:
    """Load cached predictions from disk if signature still matches."""
    cache_file = _cache_file(user_id)
    if not cache_file.exists():
        return None
    try:
        with cache_file.open("rb") as f:
            payload = pickle.load(f)
        if payload.get("signature") != _current_signature(user_id):
            return None
        return payload["predictions"], payload["category_lookup"], payload["data_end_date"]
    except Exception:
        return None


def _save_to_disk(
    user_id: int,
    predictions: pd.DataFrame,
    cat_map: dict[str, str],
    end_date: pd.Timestamp,
) -> None:
    payload = {
        "signature": _current_signature(user_id),
        "predictions": predictions,
        "category_lookup": cat_map,
        "data_end_date": end_date,
    }
    with _cache_file(user_id).open("wb") as f:
        pickle.dump(payload, f)


def _get_predictions(user_id: int) -> pd.DataFrame:
    """Return cached predictions for this user: in-memory → disk → train fresh."""
    with _cache_lock:
        cached = _predictions_cache.get(user_id)
        if cached is not None:
            return cached

        # Try the disk cache first (survives server restarts)
        loaded = _try_load_from_disk(user_id)
        if loaded is not None:
            preds, cat_map, end_date = loaded
            _predictions_cache[user_id] = preds
            _category_lookup[user_id] = cat_map
            _data_end_date[user_id] = end_date
            return preds

        # Train from scratch
        model_df, cat_map = _build_input_frame(user_id)
        if model_df.empty:
            raise RuntimeError(
                "No data available with season/occasion/time_period populated. "
                "Re-upload the sales file via /api/upload."
            )

        end_date = pd.to_datetime(model_df["date"]).max()
        # Always train with a 365-day horizon so the API can serve any
        # forecast window up to a year ahead. The reliability tiers are
        # computed from data length in the /api/data-range endpoint so the
        # UI can flag predictions that go beyond what the data supports.
        predictions = run_forecast(model_df, save_csv=False, horizon_days=365)
        _predictions_cache[user_id] = predictions
        _category_lookup[user_id] = cat_map
        _data_end_date[user_id] = end_date
        _persist_run(user_id, predictions)
        _save_to_disk(user_id, predictions, cat_map, end_date)
        return predictions


def _price_lookup(user_id: int) -> dict[str, tuple[float, float]]:
    cached = _price_cache.get(user_id)
    if cached:
        return cached
    df = load_data(user_id)
    agg = df.groupby("Product").agg(price=("Unit Price", "first"), cost=("unit_cost", "first"))
    result = {name: (float(r.price), float(r.cost)) for name, r in agg.iterrows()}
    _price_cache[user_id] = result
    return result


def _baseline_daily_per_product(user_id: int) -> dict[str, float]:
    """
    Historical average units sold per day for each product (over this user's
    full loaded window). Used as a defensive floor when Prophet under-predicts
    low-volume items — the model's extrapolation can produce near-zero
    forecasts that contradict consistent historical sales.
    """
    cached = _baseline_cache.get(user_id)
    if cached:
        return cached
    df = load_data(user_id)
    if df.empty:
        return {}
    data_days = max(1, int((df["Order Date"].max() - df["Order Date"].min()).days) + 1)
    sums = df.groupby("Product")["Quantity"].sum()
    result = {name: float(v) / data_days for name, v in sums.items()}
    _baseline_cache[user_id] = result
    return result


def _apply_baseline_floor(user_id: int, fut: pd.DataFrame, period: int) -> pd.DataFrame:
    """
    For each product in the future slice, if Prophet's predicted total for
    the window is less than 30% of what the product's historical daily
    average would produce, replace its rows with a uniform baseline
    projection. Keeps low-volume items from collapsing to zero.
    """
    if fut.empty or period <= 0:
        return fut
    baselines = _baseline_daily_per_product(user_id)
    if not baselines:
        return fut

    # Cast yhat to float so fractional baseline values don't fail the
    # int-only column type that comes out of Prophet's round().astype(int).
    fut = fut.copy()
    fut["yhat"] = fut["yhat"].astype(float)

    # Build per-product totals first, identify which to floor, then apply
    # in one pass. This avoids any in-loop mutation confusion on the
    # groupby iteration.
    per_product_total = fut.groupby("product", observed=True)["yhat"].sum().to_dict()
    per_product_rows  = fut.groupby("product", observed=True)["ds"].apply(lambda s: s.dt.normalize().nunique()).to_dict()

    to_floor: dict[str, float] = {}  # product -> per-row floor value
    for product, actual_total in per_product_total.items():
        daily_baseline = baselines.get(product, 0.0)
        if daily_baseline <= 0:
            continue
        window_days = per_product_rows.get(product, 0)
        expected_total = daily_baseline * window_days
        if expected_total <= 0:
            continue
        if actual_total < 0.30 * expected_total:
            n_rows = int((fut["product"] == product).sum())
            if n_rows > 0:
                to_floor[product] = (daily_baseline * window_days) / n_rows

    if to_floor:
        floor_mask = fut["product"].isin(list(to_floor.keys()))
        fut.loc[floor_mask, "yhat"] = fut.loc[floor_mask, "product"].map(to_floor).astype(float)
    return fut


def _revenue_profit(user_id: int, df_future: pd.DataFrame) -> tuple[float, float]:
    """Given a future-predictions slice with product + yhat, sum SAR revenue and profit."""
    if df_future.empty:
        return 0.0, 0.0
    prices = _price_lookup(user_id)
    rev, prof = 0.0, 0.0
    for product, qty in df_future.groupby("product")["yhat"].sum().items():
        p, c = prices.get(product, (0.0, 0.0))
        rev += float(qty) * p
        prof += float(qty) * (p - c)
    return rev, prof


def _notable_events(regressors: list[dict]) -> list[dict]:
    """Flag notable occasions in the forecast window (non-"Normal Day" days)."""
    events = []
    for r in regressors:
        if r["occasion"] != "Normal Day":
            events.append({"date": r["date"], "event": r["occasion"]})
    return events


def _summarize_for_manager(regressors: list[dict], total_qty: int, peak_day: dict | None, events: list[dict]) -> list[str]:
    """Produce short, plain-English action tips — the 'what should I do' section."""
    tips: list[str] = []

    if peak_day:
        tips.append(
            f"Peak day is {peak_day['dayLabel']} with about {peak_day['qty']:,} units — "
            f"schedule extra staff and prep inventory in advance."
        )

    has_ramadan = any(e["event"] == "Ramadan" for e in events)
    has_eid = any(e["event"].startswith("Eid") for e in events)
    has_national = any(e["event"] == "Saudi National Day" for e in events)
    weekend_count = sum(1 for e in events if e["event"] == "Weekend")

    if has_ramadan:
        tips.append("Ramadan falls in this window — expect lower morning sales and strong evening demand (iftar and suhoor).")
    if has_eid:
        tips.append("Eid is in this window — plan for a big spike and increase specialty items and desserts.")
    if has_national:
        tips.append("Saudi National Day is in this window — prepare for heavier foot traffic and consider a themed promotion.")
    if weekend_count >= 2:
        tips.append(f"{weekend_count} weekend days in this period — Fridays and Saturdays are typically 30% busier.")

    if not tips:
        tips.append("No special events in this period — a typical operating week. Stock to match historical averages.")

    return tips


def _slice_future(user_id: int, preds: pd.DataFrame, period: int) -> pd.DataFrame:
    """
    Take only future predictions starting AFTER this user's global data end date.

    Post-processing in order:
      1. Clamp `yhat` to >= 0 (Prophet can produce negative extrapolations,
         but sales can't go negative).
      2. Apply a historical-baseline floor for each product — low-volume
         items get predictions that collapse toward zero because Prophet
         has almost no signal. For those, fall back to a uniform projection
         based on the item's historical average units/day.
    """
    end_date = _data_end_date.get(user_id)
    if end_date is None:
        df = preds[preds["type"] == "future"].copy()
        df["yhat"] = df["yhat"].clip(lower=0)
        return _apply_baseline_floor(user_id, df, period)
    fut = preds[preds["ds"] > end_date].copy()
    if period:
        cutoff = end_date + pd.Timedelta(days=period)
        fut = fut[fut["ds"] <= cutoff]
    fut["yhat"] = fut["yhat"].clip(lower=0)
    return _apply_baseline_floor(user_id, fut, period)


def _persist_run(user_id: int, predictions: pd.DataFrame) -> None:
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
            "uid": user_id,
            "ts": predictions["ds"].min(),
            "te": predictions["ds"].max(),
            "m": json.dumps(metrics),
        })


def invalidate_cache(user_id: int | None = None) -> None:
    """Clear in-memory and on-disk caches. Called after a new upload.

    If `user_id` is given, clear only that user's caches and pickle file.
    Otherwise clear every user's cache and delete all predictions_user_*.pkl
    files. Useful after admin wipes.
    """
    with _cache_lock:
        if user_id is None:
            _predictions_cache.clear()
            _category_lookup.clear()
            _data_end_date.clear()
            _price_cache.clear()
            _baseline_cache.clear()
            try:
                for p in CACHE_DIR.glob("predictions_user_*.pkl"):
                    p.unlink(missing_ok=True)
            except Exception:
                pass
        else:
            _predictions_cache.pop(user_id, None)
            _category_lookup.pop(user_id, None)
            _data_end_date.pop(user_id, None)
            _price_cache.pop(user_id, None)
            _baseline_cache.pop(user_id, None)
            try:
                _cache_file(user_id).unlink(missing_ok=True)
            except Exception:
                pass


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
    period: int = Query(7, description="Forecast horizon in days (1-365)"),
    user_id: int = Depends(get_current_user_id),
):
    """
    Per-item forecast using Noura's hybrid Prophet model. Returns predictions
    aggregated daily plus the time_period breakdown (morning/afternoon/evening/night).
    """
    try:
        preds = _get_predictions(user_id)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    item_rows = preds[preds["product"] == target]
    if item_rows.empty:
        raise HTTPException(status_code=404, detail=f"Product '{target}' not in forecast output")

    future = _slice_future(user_id, item_rows, period)

    daily = (
        future.groupby(future["ds"].astype(str))["yhat"]
        .sum()
        .reset_index()
        .rename(columns={"ds": "date", "yhat": "predicted_quantity"})
    )

    daily_dates = daily["date"].tolist() if "date" in daily.columns else daily.iloc[:, 0].tolist()
    regressors = _regressors_for_dates(daily_dates)
    revenue, profit = _revenue_profit(user_id, future)

    # Peak day for action tips
    peak_row = daily.loc[daily["predicted_quantity"].idxmax()] if not daily.empty else None
    peak_info = (
        {
            "date": peak_row["date"],
            "dayLabel": pd.Timestamp(peak_row["date"]).strftime("%a, %b %-d"),
            "qty": int(peak_row["predicted_quantity"]),
        } if peak_row is not None else None
    )
    events = _notable_events(regressors)

    return {
        "scope": "item",
        "target": target,
        "category": _category_lookup.get(user_id, {}).get(target),
        "period": period,
        "model": "Prophet with season, occasion, and time-of-day regressors",
        "totalPredictedQuantity": int(future["yhat"].sum()),
        "totalPredictedRevenue": round(revenue, 2),
        "totalPredictedProfit": round(profit, 2),
        "peakDay": peak_info,
        "dailyPredictions": [
            {"date": r["date"], "predicted_quantity": int(r["predicted_quantity"])}
            for _, r in daily.iterrows()
        ],
        "timePeriodBreakdown": _serialize_rows(future, only_future=False),
        "regressorsUsed": regressors,
        "notableEvents": events,
        "managerTips": _summarize_for_manager(regressors, int(future["yhat"].sum()), peak_info, events),
    }


@router.get("/api/forecast/category", summary="Forecast all items in a category")
def forecast_category(
    target: str = Query(..., description="Category name (e.g. 'Hot Drinks')"),
    period: int = Query(7, description="Forecast horizon in days (1-365)"),
    user_id: int = Depends(get_current_user_id),
):
    """Aggregate every item in the category, sort by predicted quantity."""
    try:
        preds = _get_predictions(user_id)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    cat_map = _category_lookup.get(user_id, {})
    products_in_cat = [p for p, c in cat_map.items() if c == target]
    if not products_in_cat:
        raise HTTPException(status_code=404, detail=f"Category '{target}' not found")

    cat_rows = _slice_future(user_id, preds[preds["product"].isin(products_in_cat)], period)

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

    regressors = _regressors_for_dates(daily_totals["date"].tolist())
    revenue, profit = _revenue_profit(user_id, cat_rows)
    peak_row = daily_totals.loc[daily_totals["predicted_quantity"].idxmax()] if not daily_totals.empty else None
    peak_info = (
        {
            "date": peak_row["date"],
            "dayLabel": pd.Timestamp(peak_row["date"]).strftime("%a, %b %-d"),
            "qty": int(peak_row["predicted_quantity"]),
        } if peak_row is not None else None
    )
    events = _notable_events(regressors)

    return {
        "scope": "category",
        "target": target,
        "period": period,
        "model": "Prophet with season, occasion, and time-of-day regressors",
        "itemCount": int(per_product.shape[0]),
        "totalPredictedQuantity": int(cat_rows["yhat"].sum()),
        "totalPredictedRevenue": round(revenue, 2),
        "totalPredictedProfit": round(profit, 2),
        "peakDay": peak_info,
        "items": [
            {"name": r["product"], "totalPredictedQuantity": int(r["totalPredictedQuantity"])}
            for _, r in per_product.iterrows()
        ],
        "chartData": [
            {"date": r["date"], "predicted": int(r["predicted_quantity"])}
            for _, r in daily_totals.iterrows()
        ],
        "regressorsUsed": regressors,
        "notableEvents": events,
        "managerTips": _summarize_for_manager(regressors, int(cat_rows["yhat"].sum()), peak_info, events),
    }


@router.get("/api/forecast/total", summary="Forecast total restaurant sales")
def forecast_total(
    period: int = Query(7, description="Forecast horizon in days (1-365)"),
    user_id: int = Depends(get_current_user_id),
):
    """Grand total across every product."""
    try:
        preds = _get_predictions(user_id)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    future = _slice_future(user_id, preds, period)
    future["category"] = future["product"].map(_category_lookup.get(user_id, {}))

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

    regressors = _regressors_for_dates(daily_totals["date"].tolist())
    revenue, profit = _revenue_profit(user_id, future)
    peak_row = daily_totals.loc[daily_totals["predicted_quantity"].idxmax()] if not daily_totals.empty else None
    peak_info = (
        {
            "date": peak_row["date"],
            "dayLabel": pd.Timestamp(peak_row["date"]).strftime("%a, %b %-d"),
            "qty": int(peak_row["predicted_quantity"]),
        } if peak_row is not None else None
    )
    events = _notable_events(regressors)

    return {
        "scope": "total",
        "period": period,
        "model": "Prophet with season, occasion, and time-of-day regressors",
        "totalPredictedQuantity": int(future["yhat"].sum()),
        "totalPredictedRevenue": round(revenue, 2),
        "totalPredictedProfit": round(profit, 2),
        "categoryCount": int(per_category.shape[0]),
        "itemCount": int(future["product"].nunique()),
        "peakDay": peak_info,
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
        "regressorsUsed": regressors,
        "notableEvents": events,
        "managerTips": _summarize_for_manager(regressors, int(future["yhat"].sum()), peak_info, events),
    }
