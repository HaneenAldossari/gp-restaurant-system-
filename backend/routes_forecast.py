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
from prophet_model import run_forecast, compute_season, compute_occasion

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# Bump this whenever the model's input encoding or regressor list changes —
# old pickles from a previous schema will fail the signature check and be
# retrained automatically.
#   v2 = one-hot occasion + payday regressor (collinear with weekly_seasonality)
#   v3 = Prophet `holidays` mechanism + stronger weekly_seasonality prior
#   v4 = daily_seasonality=False + manual weekly with fourier_order=10
#   v5 = daily aggregation per product + post-hoc time_period split
#   v6 = train production model on full data (split is for MAE only) +
#        tighter changepoint prior so trend doesn't extrapolate negative
#   v7 = separated early_payday (days 1-5) from late_payday (28-31) —
#        the early window carries most of the lift in real Saudi data
#   v8 = unified payday window: anchor day 27, upper_window=9 (covers
#        day 27 through next month's 5th as one continuous holiday)
#   v9 = holidays_prior_scale bumped to 100 so payday/Eid/Ramadan
#        coefficients aren't regularized down to noise
#   v10 = top-down: one Prophet on the daily aggregate, disaggregate
#         per-product via historical (dow × time_period) share. ~10×
#         faster training on the free tier with equal/better aggregate
#         accuracy (cross-product noise averages out at the total).
#   v11 = FORECAST_MODE switch: per_product trains one Prophet for the
#         top-N items (catches item-specific Eid/Ramadan spikes that
#         get washed out at the aggregate level) + extended Eid windows
#         with pre-Eid shopping rush + weak yearly seasonality.
#   v12 = baseline scaling moved from per-request to one-shot at
#         training time. Eliminates window-dependent rescaling bug
#         (same date returning different values across "Next Week" vs
#         "Next Month").
#   v13 = season-aware disaggregation. Top-down disaggregation share
#         keyed by (product × season × dow × time_period) so winter
#         forecasts allocate more share to hot drinks and summer
#         forecasts to cold drinks. Default mode back to top_down
#         (per_product was misallocating sparse-history items to ~0).
#   v14 = real Saudi temperature regressor via Open-Meteo archive
#         (cached locally). The model finally uses actual weather
#         instead of just the season one-hot, satisfying the report's
#         claim about weather as a model input.
#   v15 = keep yhat as float through the cache. Per-cell int rounding
#         was zeroing out the long tail (a 19-units-a-year item floors
#         to 0 in every cell, summing to 0 for the entire window).
#   v16 = split payday_week into payday_late (days 27-31) and
#         payday_early (days 1-5). Single window averaged a strong
#         +50% early-month lift with a modest +25% late-month lift
#         into a flat +35%, hiding the post-payday spending spike.
#   v17 = Eid 4-phase (pre/day1/bounce/post) — verified against the
#         user's 2022 data showing Eid Al Adha day 1 was DEPRESSED
#         (-50%) while day 2 was the PEAK (+88% over avg). Single
#         day-coefficient was averaging both. Plus added Saudi
#         Founding Day (Feb 22) which was missing entirely.
#   v18 = drop store-closure days (zero-revenue) from training
#         instead of feeding 0s to Prophet. With 84/361 closure
#         days in the dataset (Eid Al Fitr week, summer break,
#         Sept-Nov gap) Prophet was learning "Eid al-Fitr = no
#         demand" and suppressing every future Eid al-Fitr to zero.
#         Closures now treated as missing data; calibration target
#         excludes them too.
MODEL_VERSION = "v20"  # Drop early-close partial-day outliers from training + per-weekday p25 floor on daily forecast


def _cache_file(user_id: int) -> Path:
    return CACHE_DIR / f"predictions_user_{user_id}.pkl"


router = APIRouter(tags=["Forecasting"])


# ─────────────────────────────────────────────────────────────────────────
# Regressor labelling — reuses the same compute_* functions Prophet trains
# on so the UI can display exactly which signals (Weekend / Payday / Eid)
# the model saw for each forecast date.
# ─────────────────────────────────────────────────────────────────────────
def _regressors_for_dates(dates: list) -> list[dict]:
    """Return per-date regressor values (season + occasion) for a forecast window."""
    out = []
    for d in dates:
        out.append({
            "date": str(pd.Timestamp(d).date()),
            "season": compute_season(d),
            "occasion": compute_occasion(d),
        })
    return out


# ── Per-user caches ──────────────────────────────────────────────────────
_predictions_cache: dict[int, pd.DataFrame] = {}
_category_lookup: dict[int, dict[str, str]] = {}       # user_id -> (product -> category)
_data_end_date: dict[int, pd.Timestamp] = {}            # user_id -> last actual sale date
_horizon_cache: dict[int, int] = {}                     # user_id -> days of cached future
_price_cache: dict[int, dict[str, tuple[float, float]]] = {}  # user_id -> (product -> (unit_price, unit_cost))
_baseline_cache: dict[int, dict[str, float]] = {}       # user_id -> (product -> historical units/day)
_seasonal_cache: dict[int, dict[str, dict]] = {}        # user_id -> (product -> seasonality profile)
# Daily residual stats from training — used to draw the 80% prediction
# band on the chart. `std` is the sample std of (actual − predicted) on
# real-day training observations; `mean` is the real-day mean of y. The
# coefficient of variation (std / mean) lets us scale the band for any
# subset (category / item) by the subset's predicted magnitude.
_residual_stats: dict[int, dict[str, float]] = {}
_cache_lock = threading.Lock()


# Minimum forecast horizon. The actual horizon used is computed
# dynamically from `data_end` and today's date — see `_default_horizon_days`
# below. The hard floor of 1095 covers fresh datasets where today is close
# to data_end; in stale-data demos (e.g. 2022 data viewed in 2026) the
# dynamic calculation extends well past this so requests for "next 7 days"
# actually hit the cache instead of triggering a retrain on every click.
DEFAULT_HORIZON_DAYS = 1095  # ~3 years (floor)


def _default_horizon_days(data_end: pd.Timestamp) -> int:
    """Compute the horizon the cache should cover so the most common
    user request ("next 7 / 30 / 90 days from today") lands inside the
    cached predictions.

    Bounded to a tight window past today (90 days) because the
    disaggregation step on Render's 0.1-CPU / 512MB free tier needs to
    materialize one row per (date × product × time_period). With a
    1500-day horizon that's ~800k rows and tens of seconds even on the
    vectorized path; with 90 days past today it's well under 100k rows
    and predictably fast."""
    today = pd.Timestamp.now().normalize()
    days_since_data = max(0, int((today - pd.Timestamp(data_end)).days))
    # Cover today + 90 days. Adding the gap from data_end to today is
    # required because the cache stores predictions from data_end onward;
    # without it the cache wouldn't reach today and every "next 7 days"
    # request would invalidate and retrain from scratch.
    return days_since_data + 90


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
        # Pass through so the model can down-weight imputed days when
        # fitting Prophet. Defaults to False if the column wasn't
        # selected (older DB connections).
        "is_imputed": needed["is_imputed"].fillna(False).astype(bool)
        if "is_imputed" in needed.columns else False,
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
    return {
        "model_version": MODEL_VERSION,
        "upload_id": int(latest[0]),
        "item_count": int(latest[1]),
    }


def _try_load_from_disk(user_id: int) -> tuple[pd.DataFrame, dict[str, str], pd.Timestamp, int] | None:
    """Load cached predictions from disk if signature still matches."""
    cache_file = _cache_file(user_id)
    if not cache_file.exists():
        return None
    try:
        with cache_file.open("rb") as f:
            payload = pickle.load(f)
        if payload.get("signature") != _current_signature(user_id):
            return None
        # Older caches predate the horizon field — fall back to 365 to stay
        # compatible. New caches save the actual horizon so we can decide
        # when to retrain.
        horizon = int(payload.get("horizon_days", 365))
        return (
            payload["predictions"],
            payload["category_lookup"],
            payload["data_end_date"],
            horizon,
        )
    except Exception:
        return None


def _save_to_disk(
    user_id: int,
    predictions: pd.DataFrame,
    cat_map: dict[str, str],
    end_date: pd.Timestamp,
    horizon_days: int,
) -> None:
    payload = {
        "signature": _current_signature(user_id),
        "predictions": predictions,
        "category_lookup": cat_map,
        "data_end_date": end_date,
        "horizon_days": int(horizon_days),
    }
    with _cache_file(user_id).open("wb") as f:
        pickle.dump(payload, f)


def _get_predictions(user_id: int, required_horizon_days: int | None = None) -> pd.DataFrame:
    """Return cached predictions for this user: in-memory → disk → train fresh.

    `required_horizon_days` lets callers ask for a minimum forecast window.
    If the cached predictions don't extend that far the cache is dropped
    and we retrain with a horizon big enough to cover the request (plus a
    margin so adjacent calls don't trigger another retrain). When omitted,
    any cached predictions are returned as-is.
    """
    with _cache_lock:
        cached = _predictions_cache.get(user_id)
        cached_horizon = _horizon_cache.get(user_id, 0)
        if cached is not None and (
            required_horizon_days is None or cached_horizon >= required_horizon_days
        ):
            return cached

        # Try the disk cache first (survives server restarts)
        if cached is None:
            loaded = _try_load_from_disk(user_id)
            if loaded is not None:
                preds, cat_map, end_date, disk_horizon = loaded
                if required_horizon_days is None or disk_horizon >= required_horizon_days:
                    _predictions_cache[user_id] = preds
                    _category_lookup[user_id] = cat_map
                    _data_end_date[user_id] = end_date
                    _horizon_cache[user_id] = disk_horizon
                    return preds

        # Train from scratch (or retrain with a longer horizon)
        model_df, cat_map = _build_input_frame(user_id)
        if model_df.empty:
            raise RuntimeError(
                "No data available with season/occasion/time_period populated. "
                "Re-upload the sales file via /api/upload."
            )

        end_date = pd.to_datetime(model_df["date"]).max()
        # Use the dynamic default that accounts for "today vs data_end".
        # Without this, stale-data demos invalidate the cache on every
        # request because DEFAULT_HORIZON_DAYS=1095 doesn't reach today.
        target_horizon = max(
            _default_horizon_days(end_date),
            (required_horizon_days or 0) + 30,  # small buffer to absorb adjacent requests
        )
        predictions = run_forecast(model_df, save_csv=False, horizon_days=target_horizon)
        # Capture the residual stats BEFORE downstream ops that may
        # drop `attrs` on copy/merge. Used by the chart endpoints to
        # draw the 80% prediction band.
        _residual_stats[user_id] = {
            'std': float(predictions.attrs.get('daily_residual_std', 0.0)),
            'mean': float(predictions.attrs.get('daily_train_mean', 1.0)),
        }
        # One-shot baseline scaling — applied here at training time, not
        # per-request, so the same date returns the same value regardless
        # of whether the caller asks for "next 7 days" or "next 30 days".
        # The previous per-request scaling caused window-dependent values
        # (different days included → different actual_total → different
        # scale factor → different per-day output) which surfaced as
        # contradictory forecasts for the same date across windows.
        predictions = _bake_baseline_scale(predictions, model_df, end_date)
        # Holiday calibration AFTER baseline scaling — corrects
        # Prophet's chronic under-fit on rare-event holidays
        # (one-Eid-per-year-of-data is too sparse for the prior to
        # learn the full lift).
        predictions = _calibrate_holidays(predictions, model_df, end_date)
        _predictions_cache[user_id] = predictions
        _category_lookup[user_id] = cat_map
        _data_end_date[user_id] = end_date
        _horizon_cache[user_id] = target_horizon
        _persist_run(user_id, predictions)
        _save_to_disk(user_id, predictions, cat_map, end_date, target_horizon)
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


def _add_prediction_band(rows: list[dict], user_id: int, *value_keys: str) -> None:
    """Mutate `rows` in place — add `<key>_low` / `<key>_high` for each
    requested value key, representing the 80 % prediction interval
    around the point forecast.

    The band uses the coefficient of variation (std / mean) of the
    daily training residuals captured at fit time. CV is dimensionless
    so it survives the route layer's `_bake_baseline_scale` multiplier
    without re-derivation, and the same CV applies to any subset
    (category / item) by scaling the half-width to that subset's
    predicted magnitude per day.

    Defended in the thesis as: 'Prophet's point forecast is the
    expected daily realisation; the 80% prediction interval is
    derived from the training residual coefficient of variation,
    representing the day-to-day spread the model could not explain
    systematically.' The chart shows both so a viewer doesn't
    mistake a smooth pattern for a deterministic prediction.
    """
    stats = _residual_stats.get(user_id) or {}
    sigma = stats.get('std', 0.0)
    mu = stats.get('mean', 1.0)
    if sigma <= 0 or mu <= 0:
        return
    cv = sigma / mu
    # z = 1.282 corresponds to a two-sided 80 % normal interval.
    # Reasonable for a model with hundreds of training residuals;
    # the underlying distribution is roughly normal after the
    # holiday-gated outlier filter.
    z = 1.282
    for r in rows:
        for key in value_keys:
            v = r.get(key)
            if v is None:
                continue
            half = abs(v) * cv * z
            r[f'{key}_low'] = max(0, round(v - half))
            r[f'{key}_high'] = round(v + half)


def _bake_baseline_scale(
    predictions: pd.DataFrame,
    model_df: pd.DataFrame,
    data_end: pd.Timestamp,
) -> pd.DataFrame:
    """One-shot global scale applied at training time.

    Computes a SINGLE multiplier = (historical menu daily average) /
    (predicted-future menu daily average) and multiplies every yhat in
    the future window by it. This:

      • Preserves Prophet's shape — every relative variation
        (weekend lift, Eid spikes, payday peaks, mid-month dip) keeps
        its proportional magnitude.
      • Restores realistic absolute scale — per-product Prophets on
        sparse 1-year data systematically under-predict by 2-4×;
        this snaps the menu total back to the historical average.
      • Same value for the same date no matter which window the caller
        slices — fixes the per-request rescaling bug where "Thu Apr 30
        = 171 in Next Week" but "= 158 in Next Month" for the same
        forecast.
    """
    if predictions.empty:
        return predictions

    data_days = (
        max(1, int((model_df["date"].max() - model_df["date"].min()).days) + 1)
    )
    historical_menu_per_day = float(model_df["quantity"].sum()) / data_days

    fut = predictions[predictions["ds"] > data_end]
    if fut.empty or historical_menu_per_day <= 0:
        return predictions

    daily_totals = fut.groupby("ds")["yhat"].sum()
    if daily_totals.empty:
        return predictions
    predicted_menu_per_day = float(daily_totals.mean())
    if predicted_menu_per_day <= 0:
        return predictions

    global_scale = historical_menu_per_day / predicted_menu_per_day
    # Clamp the global scale to a wide band — Prophet without yearly
    # seasonality can drift to 10× either side of the historical mean,
    # so the rescue needs reach. The 20× cap still rules out catastrophic
    # noise (e.g. predicted=1 unit/day on broken training).
    global_scale = max(0.1, min(global_scale, 20.0))

    if abs(global_scale - 1.0) < 0.05:
        return predictions  # already within ±5% of historical, leave alone

    print(f"[baseline_scale] global={global_scale:.2f} "
          f"(hist={historical_menu_per_day:.1f}/day, "
          f"predicted={predicted_menu_per_day:.1f}/day)")

    out = predictions.copy()
    # Keep yhat as float in the cache. Rounding to int at this stage
    # zeros out the long tail (e.g. Banana Split Waffle = 0.05/day floors
    # to 0, then summing 90 zeros gives a 0-unit forecast for an item
    # that genuinely sold 19 units the prior year). Final rounding
    # happens at the route-handler level via _round_daily_to_total once
    # values are summed for display.
    out["yhat"] = (out["yhat"].astype(float) * global_scale).clip(lower=0)
    return out


def _apply_baseline_floor(user_id: int, fut: pd.DataFrame, period: int) -> pd.DataFrame:
    """
    Defensive correction for products where Prophet severely under-predicts.

    Strategy: PROPORTIONALLY SCALE rather than replace. The previous
    implementation overwrote every row with a single uniform value, which
    flattened weekend / payday / holiday peaks Prophet had correctly
    predicted in shape but underestimated in magnitude. Scaling preserves
    the daily pattern (Saturday > Tuesday) while making the totals match
    historical reality.

    Trigger: only when Prophet's total is < 15% of what the product's
    historical average would produce — a much higher bar than before so
    we don't override the model's normal output. Uniform replacement is
    only used as a last resort when Prophet predicted exactly zero.
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

    per_product_total = fut.groupby("product", observed=True)["yhat"].sum().to_dict()
    per_product_days  = fut.groupby("product", observed=True)["ds"].apply(
        lambda s: s.dt.normalize().nunique()
    ).to_dict()

    for product, actual_total in per_product_total.items():
        daily_baseline = baselines.get(product, 0.0)
        if daily_baseline <= 0:
            continue
        window_days = per_product_days.get(product, 0)
        expected_total = daily_baseline * window_days
        if expected_total <= 0:
            continue

        # Only intervene when Prophet's total is severely off (<15% of
        # historical expectation). For everything else we trust the model.
        if actual_total < 0.15 * expected_total:
            mask = fut["product"] == product
            if actual_total > 0:
                # Scale proportionally — preserves day-of-week, weekend, and
                # event spikes in Prophet's output while restoring magnitude.
                scale = expected_total / actual_total
                fut.loc[mask, "yhat"] = fut.loc[mask, "yhat"] * scale
            else:
                # Prophet predicted exactly zero — last-resort uniform fill.
                n_rows = int(mask.sum())
                if n_rows > 0:
                    fut.loc[mask, "yhat"] = expected_total / n_rows
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


def _daily_revenue(user_id: int, df_future: pd.DataFrame) -> dict[str, float]:
    """
    Per-date revenue: for every (date, product) cell in the future slice,
    multiply predicted yhat by that product's unit price and sum by date.

    Returned as {date_str: SAR} — the caller zips this with its daily
    quantity series to build the chart's revenue line.
    """
    if df_future.empty:
        return {}
    prices = _price_lookup(user_id)
    df = df_future.copy()
    df["__price"] = df["product"].map(lambda p: prices.get(p, (0.0, 0.0))[0]).astype(float)
    df["__rev"] = df["yhat"] * df["__price"]
    out = df.groupby(df["ds"].astype(str))["__rev"].sum()
    return {str(k): float(v) for k, v in out.items()}


def _forecast_heatmap(user_id: int, df_future: pd.DataFrame) -> tuple[list[dict], dict | None]:
    """Build a (weekday × hour) heatmap from FORECAST quantities, plus
    the peak (weekday, hour) cell for an above-the-chart callout.

    Why this isn't just a copy of the dashboard heatmap: the cell
    *values* come from Prophet's predicted yhat for the active forecast
    window, NOT from historical orders. So when Eid, payday, or any
    weekly-seasonality lift bumps a specific weekday in the forecast,
    that weekday's row glows brighter than it would in a historical
    snapshot. The within-day hour distribution still uses historical
    hour-share inside each (weekday, time_period) bucket — Prophet
    predicts at time-period granularity, not hour, so we borrow the
    hour shape from history while letting the volume scale with the
    forecast.

    Output format matches the dashboard heatmap exactly so the existing
    HeatmapChart component can render it without changes.
    """
    if df_future.empty:
        return [], None

    # Real-day-only history for the hour share. Reviewer found that the
    # heatmap mis-located the peak (showed Wed 10 PM as darkest when
    # 2022 actuals have Fri 9 PM as the busiest hour). One driver was
    # the hour distribution being computed from real + imputed rows;
    # imputed days are bucketed by time_period and don't have a real
    # within-period hour distribution, which biased certain
    # (weekday, time_period, hour) cells.
    df_hist = load_data(user_id, include_synthetic=False)
    if df_hist.empty or "hour" not in df_hist.columns or "day_name" not in df_hist.columns:
        return [], None

    # Hour share within each (weekday, time_period) — fraction of
    # historical UNITS (not order count) that landed in each hour.
    # Switched from .size() to summing Quantity so the share reflects
    # actual demand-weight per hour, which is what the forecast yhat
    # is also denominated in.
    src = df_hist.dropna(subset=["day_name", "time_period", "hour"]).copy()
    if src.empty:
        return [], None
    src["hour"] = src["hour"].astype(int)
    counts = (
        src.groupby(["day_name", "time_period", "hour"])["Quantity"]
        .sum().reset_index().rename(columns={"Quantity": "n"})
    )
    bucket_totals = counts.groupby(["day_name", "time_period"])["n"].transform("sum")
    counts["share"] = counts["n"] / bucket_totals.where(bucket_totals > 0, 1)

    # Aggregate forecast by (weekday, time_period), then divide by the
    # number of OCCURRENCES of that weekday in the window so the cell
    # value represents an AVERAGE per occurrence rather than a sum
    # over the window. Without this, a 30-day forecast's heatmap reads
    # 4-5× the value of a 7-day forecast's heatmap, with the same
    # underlying pattern — confusing managers comparing periods.
    # Per-occurrence is also directly comparable to historical per-
    # day averages the manager already knows.
    fut = df_future.copy()
    fut["day_name"] = pd.to_datetime(fut["ds"]).dt.day_name()
    # Ramadan correction: Saudi cafes don't operate during fasting hours.
    # Iftar lands ~6-7 PM, so morning + afternoon time periods are
    # closed throughout the holy month. Without this, the heatmap
    # smeared morning hour shares (built from non-Ramadan history)
    # onto Ramadan forecast days and made it look like Mar 1-19, 2026
    # had 6 AM - 6 PM activity.
    fut["__occasion"] = pd.to_datetime(fut["ds"]).apply(compute_occasion)
    ramadan_closed = (
        (fut["__occasion"] == "Ramadan")
        & fut["time_period"].isin(["morning", "Afternoon"])
    )
    fut.loc[ramadan_closed, "yhat"] = 0
    occur = (
        fut.groupby("day_name")["ds"].nunique().rename("n_occur").reset_index()
    )
    fut_agg = (
        fut.groupby(["day_name", "time_period"])["yhat"].sum()
        .reset_index().rename(columns={"yhat": "fc_qty_total"})
    )
    fut_agg = fut_agg.merge(occur, on="day_name", how="left")
    fut_agg["fc_qty"] = fut_agg["fc_qty_total"] / fut_agg["n_occur"].where(fut_agg["n_occur"] > 0, 1)

    merged = fut_agg.merge(
        counts[["day_name", "time_period", "hour", "share"]],
        on=["day_name", "time_period"],
        how="left",
    )
    merged["hour_qty"] = merged["fc_qty"] * merged["share"].fillna(0)
    grid = merged.groupby(["day_name", "hour"])["hour_qty"].sum().reset_index()

    # Format identically to dashboard heatmap output so the existing
    # HeatmapChart component renders without modification.
    days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_short = {"Monday": "Mon", "Tuesday": "Tue", "Wednesday": "Wed", "Thursday": "Thu",
                 "Friday": "Fri", "Saturday": "Sat", "Sunday": "Sun"}
    OPEN_HOUR, CLOSE_HOUR = 6, 2
    if CLOSE_HOUR < OPEN_HOUR:
        hour_seq = list(range(OPEN_HOUR, 24)) + list(range(0, CLOSE_HOUR + 1))
    else:
        hour_seq = list(range(OPEN_HOUR, CLOSE_HOUR + 1))
    hour_labels = {h: f"{h % 12 or 12}{'AM' if h < 12 else 'PM'}" for h in hour_seq}

    out: list[dict] = []
    peak_value = -1.0
    peak_cell: dict | None = None
    avg_total = 0.0
    avg_count = 0
    for d in days_order:
        for h in hour_seq:
            row = grid[(grid["day_name"] == d) & (grid["hour"] == h)]
            val_f = float(row["hour_qty"].iloc[0]) if len(row) else 0.0
            val_int = int(round(val_f))
            out.append({"day": day_short[d], "hour": hour_labels[h], "value": val_int})
            if val_f > 0:
                avg_total += val_f
                avg_count += 1
            if val_f > peak_value:
                peak_value = val_f
                peak_cell = {"day": day_short[d], "hour": hour_labels[h], "value": val_int}

    if peak_cell and avg_count > 0:
        avg = avg_total / avg_count
        peak_cell["vsAverage"] = round((peak_value - avg) / avg * 100) if avg > 0 else None

    return out, peak_cell


def _notable_events(regressors: list[dict]) -> list[dict]:
    """Flag notable occasions in the forecast window.

    A single date can carry MULTIPLE labels — `compute_occasion` only
    returns the highest-priority one (e.g. Ramadan over Post-payday),
    but for the UI we want to surface every active band so a manager
    sees that Mar 1-5 is BOTH Ramadan AND post-payday spending. We
    re-derive the secondary tags here from the date itself instead of
    threading them through compute_occasion.
    """
    events = []
    for r in regressors:
        date = pd.Timestamp(r["date"])
        primary = r["occasion"]

        # Primary label (the highest-priority event compute_occasion picked)
        if primary != "Normal Day":
            events.append({"date": r["date"], "event": primary})

        # Secondary: post-payday spending — surfaces independently of
        # whatever the primary tag was (Ramadan, Eid bounce, weekend, etc).
        # Day 27 itself is "Payday"; days 28-31 + 1-5 are spillover.
        is_post_payday_window = (date.day >= 28 or date.day <= 5)
        is_payday_anchor = date.day == 27
        if (is_payday_anchor and primary != "Payday") or (
            is_post_payday_window and primary != "Post-payday spending"
        ):
            label = "Payday" if is_payday_anchor else "Post-payday spending"
            events.append({"date": r["date"], "event": label})

    return events


def _summarize_for_manager(regressors: list[dict], total_qty: int, peak_day: dict | None, events: list[dict]) -> list[str]:
    """
    Produce short, plain-English action tips — the "what should I do" section.

    The tips adapt to the scale of the forecast. For a near-zero forecast
    (e.g. a low-volume item), recommending "schedule extra staff" is
    nonsense; we surface that the item is barely moving and suggest
    promotion or menu review instead. Event tips (Ramadan, Eid, etc.) are
    only surfaced when total demand is meaningful enough for them to act
    as a useful pattern signal.
    """
    tips: list[str] = []
    window_days = max(len(regressors), 1)
    daily_avg = total_qty / window_days

    # ── Near-zero demand: everything else is noise ───────────────────────
    if daily_avg < 0.5:
        tips.append(
            "Expected demand is very low across this window — barely any sales predicted. "
            "Consider a promotion, price change, or reviewing the item's place on the menu."
        )
        return tips

    # ── Peak-day advice scaled to actual magnitude ───────────────────────
    if peak_day:
        peak_qty = peak_day.get("qty", 0)
        if peak_qty >= 20:
            tips.append(
                f"Peak day is {peak_day['dayLabel']} with about {peak_qty:,} units — "
                f"schedule extra staff and prep inventory in advance."
            )
        elif peak_qty >= 5:
            tips.append(
                f"Busiest day is {peak_day['dayLabel']} (~{peak_qty} units). "
                f"Top up stock; no special staffing needed for this scale."
            )
        else:
            tips.append(
                f"Busiest day is {peak_day['dayLabel']} with only ~{peak_qty} unit"
                f"{'s' if peak_qty != 1 else ''} — demand is modest; keep minimum stock ready."
            )

    # ── Event tips — only meaningful for non-trivial volumes ────────────
    if daily_avg >= 1.0:
        has_ramadan = any(e["event"] == "Ramadan" for e in events)
        has_eid = any(e["event"].startswith("Eid") for e in events)
        has_national = any(e["event"] == "Saudi National Day" for e in events)
        weekend_count = sum(1 for e in events if e["event"] == "Weekend")

        if has_ramadan:
            tips.append("Ramadan falls in this window — expect lower morning sales and stronger evening demand (iftar and suhoor).")
        if has_eid:
            tips.append("Eid is in this window — plan for a spike and increase specialty items and desserts.")
        if has_national:
            tips.append("Saudi National Day is in this window — prepare for heavier foot traffic.")
        if weekend_count >= 2 and daily_avg >= 3:
            tips.append(f"{weekend_count} weekend days in this period — Fridays and Saturdays are typically 30% busier.")

    if not tips:
        tips.append("Typical operating period — stock to match historical averages.")

    return tips


def _slice_future(
    user_id: int,
    preds: pd.DataFrame,
    period: int,
    start_date: str | None = None,
    end_date_str: str | None = None,
) -> pd.DataFrame:
    """
    Take only future predictions starting from TODAY.

    Forecasts are always anchored on the current date — "next 7 days" means
    the seven days starting tomorrow, not the seven days after the upload's
    last sale date. When the dataset is from a past year (e.g. 2022 sales
    looked at in 2026), the in-between window is ignored: managers care
    about what's happening this coming week, not what would have happened
    three years ago.

    Slicing precedence:
      1. Both `start_date` and `end_date_str` given → return predictions in
         that inclusive window (clamped to >= tomorrow so a stale UI can't
         request a past-dated forecast).
      2. Otherwise → return the first `period` days from tomorrow.

    Post-processing:
      1. Clamp `yhat` to >= 0.
      2. Apply the historical-baseline floor (proportional scaling for
         severe under-predictions only).
    """
    data_end = _data_end_date.get(user_id)
    today = pd.Timestamp.now().normalize()
    # Anchor: tomorrow, or right after data_end if the upload extends past
    # today (shouldn't normally happen, but keeps the logic safe).
    if data_end is None:
        anchor = today + pd.Timedelta(days=1)
    else:
        anchor = max(today + pd.Timedelta(days=1), data_end + pd.Timedelta(days=1))

    fut = preds[preds["ds"] >= anchor].copy()

    if start_date and end_date_str:
        lo = pd.Timestamp(start_date)
        hi = pd.Timestamp(end_date_str)
        if hi < lo:
            lo, hi = hi, lo
        lo = max(lo, anchor)
        fut = fut[(fut["ds"] >= lo) & (fut["ds"] <= hi)]
    elif period:
        cutoff = anchor + pd.Timedelta(days=period - 1)
        fut = fut[fut["ds"] <= cutoff]

    fut["yhat"] = fut["yhat"].clip(lower=0)
    # Baseline scaling is now applied ONCE at training time
    # (`_bake_baseline_scale`) so the cached predictions are pre-scaled.
    # Slicing returns those values directly — no per-request rescaling,
    # no window-dependent contradictions for the same date.
    return fut


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


def _data_end_for(user_id: int) -> pd.Timestamp | None:
    """Return the user's last sale date — checking the in-memory cache,
    then the disk pickle, then the database. Cheap so it's safe to call
    before deciding whether the prediction cache covers today."""
    cached = _data_end_date.get(user_id)
    if cached is not None:
        return cached
    # Peek at the on-disk pickle without loading the full predictions.
    cache_file = _cache_file(user_id)
    if cache_file.exists():
        try:
            with cache_file.open("rb") as f:
                payload = pickle.load(f)
            end = payload.get("data_end_date")
            if end is not None:
                return pd.Timestamp(end)
        except Exception:
            pass
    # Fall back to a small DB query.
    with get_engine().connect() as conn:
        row = conn.execute(text("""
            SELECT MAX(o.order_datetime)
            FROM orders o
            JOIN uploads u ON o.upload_id = u.id
            WHERE u.user_id = :uid
        """), {"uid": user_id}).fetchone()
    return pd.Timestamp(row[0]).normalize() if row and row[0] else None


def _calibrate_holidays(
    predictions: pd.DataFrame,
    model_df: pd.DataFrame,
    data_end: pd.Timestamp,
) -> pd.DataFrame:
    """Post-fit calibration: bring under-fit holiday days up to their
    historical observed lift.

    Why this exists: Prophet's MAP estimation with only ONE occurrence
    of each major holiday (one Eid al-Adha, one National Day, etc. in
    a single year of training data) is structurally conservative — it
    won't fit a +42% lift coefficient confidently from a single sample,
    no matter how loose the prior is. The user's spreadsheet showed
    Eid al-Adha had +42% revenue lift while the model captured only
    +7-10%. We close the gap by scaling each labeled holiday's future
    predictions to match the same per-day average the historical data
    showed for that holiday.

    Conservative: only scales UP (never down) and only when the
    historical lift is meaningfully larger than the fitted lift.
    Capped at 3× so a single noisy historical day doesn't blow up
    the future forecast."""
    if predictions.empty:
        return predictions

    # Lazy import to avoid circular at module load
    from prophet_model import compute_occasion

    work = model_df.copy()
    work["occasion"] = work["date"].apply(compute_occasion)
    hist_daily = work.groupby(["date", "occasion"], as_index=False)["quantity"].sum()
    # Exclude store-closure days (qty == 0) from the historical avg.
    # In our 2022 dataset Eid al-Fitr coincided with a multi-day
    # closure — leaving those zeros in would target a near-zero
    # predicted value, which would then suppress every future
    # Eid al-Fitr to zero. We treat closures as missing data
    # (the manager may or may not close again), so the calibration
    # target reflects what an OPEN Eid al-Fitr looked like in the
    # data. Holidays with no open historical days at all simply
    # skip calibration (Prophet's coefficient is whatever it is).
    hist_open_only = hist_daily[hist_daily["quantity"] > 0]
    hist_per_occasion = hist_open_only.groupby("occasion")["quantity"].mean().to_dict()

    out = predictions.copy()
    out["__occ"] = out["ds"].apply(compute_occasion)

    fut_mask = out["ds"] > data_end
    fut = out[fut_mask]
    if fut.empty:
        out = out.drop(columns=["__occ"])
        return out

    # Calibrate each meaningful holiday family. Per-event scaling
    # (not pooled across years) — we identify each contiguous block
    # of one occasion type in the future and scale that block's avg
    # toward the historical avg. Pooling across multiple future years
    # of Eid would let one year's low prediction yank up another
    # year's already-fine prediction.
    #
    # Per-occasion scale cap. Eid stays at 2× because we have multiple
    # in-year phases of data (pre / day1 / bounce / post) — Prophet's
    # baseline fit is already close, so a tight cap is right. Solar
    # one-shot holidays (National Day, Founding Day) only have ONE
    # historical sample and the day-of-week of the future occurrence
    # often differs from the historical one (e.g. 2022 Sep 23 was
    # Friday, 2026 will be Wednesday) — that weekly-seasonality gap
    # alone can be 1.5×, so we need a wider cap to land near the
    # observed lift instead of stalling halfway there.
    CALIBRATE_OCCASIONS = (
        "Ramadan", "Eid al-Fitr", "Eid al-Adha",
        "Saudi National Day", "Saudi Founding Day",
    )
    SCALE_CAP = {
        "Saudi National Day": 3.5,
        "Saudi Founding Day": 3.5,
    }
    for occ in CALIBRATE_OCCASIONS:
        target = hist_per_occasion.get(occ)
        if not target:
            continue
        occ_dates = sorted(fut[fut["__occ"] == occ]["ds"].unique())
        if not occ_dates:
            continue

        # Group consecutive occ days into events (each event is one
        # year's Eid / one National Day, etc.) and scale each event
        # independently.
        events: list[list] = []
        for d in occ_dates:
            d = pd.Timestamp(d)
            if events and (d - pd.Timestamp(events[-1][-1])).days <= 1:
                events[-1].append(d)
            else:
                events.append([d])

        cap = SCALE_CAP.get(occ, 2.0)
        for event_dates in events:
            event_mask = fut_mask & out["ds"].isin(event_dates)
            event_per_day = (
                out[event_mask].groupby("ds")["yhat"].sum().mean()
            )
            if event_per_day <= 0:
                continue
            # Skip when predicted is already within 5% of historical
            # target. We previously left a 10% slack — that masked
            # under-predictions on Saudi National Day (predicted 290
            # vs target 313 = 7% gap; user-visible revenue gap was
            # larger because holiday customers buy higher-priced
            # items, but the calibrator runs on units). Tighter band
            # closes the unit gap; the residual mix-shift is a
            # separate architectural issue.
            if target <= event_per_day * 1.05:
                continue
            scale = min(target / event_per_day, cap)
            out.loc[event_mask, "yhat"] = out.loc[event_mask, "yhat"] * scale
            print(f"[calibrate] {occ} {event_dates[0].date()}–{event_dates[-1].date()}: "
                  f"pred {event_per_day:.0f} → target {target:.0f} → scale {scale:.2f} (cap {cap})")

    out = out.drop(columns=["__occ"])
    return out


def _seasonality_profile(user_id: int) -> dict[str, dict]:
    """For each product, summarise its historical seasonal concentration.

    Returns {product_name: {isSeasonal, peakSeasons, concentrationPct, label}}.
    A product is flagged seasonal when ≥70% of its sales fall in 1-2
    seasons. Caches the result in `_seasonal_cache` so the heavy
    groupby happens once per user."""
    cached = _seasonal_cache.get(user_id)
    if cached is not None:
        return cached

    df = load_data(user_id)
    if df.empty:
        _seasonal_cache[user_id] = {}
        return {}

    def _season_of(month: int) -> str:
        if month in (12, 1, 2):  return "Winter"
        if month in (3, 4, 5):   return "Spring"
        if month in (6, 7, 8):   return "Summer"
        return "Autumn"

    work = df[["Product", "Order Date", "Quantity"]].copy()
    work["season"] = work["Order Date"].dt.month.apply(_season_of)
    by_product_season = (
        work.groupby(["Product", "season"])["Quantity"].sum().reset_index()
    )
    totals = work.groupby("Product")["Quantity"].sum()

    out: dict[str, dict] = {}
    for product, total in totals.items():
        if total <= 0:
            continue
        seasons = by_product_season[by_product_season["Product"] == product]
        # Sort by qty descending; pick top 1, then check if top 2 covers
        # 70% — if yes and they're "adjacent" seasons (e.g. Autumn-Winter),
        # treat as a 2-season concentration.
        seasons = seasons.sort_values("Quantity", ascending=False)
        if seasons.empty:
            continue
        top1_pct = seasons.iloc[0]["Quantity"] / total * 100
        top2_pct = (
            seasons.iloc[:2]["Quantity"].sum() / total * 100
            if len(seasons) >= 2 else top1_pct
        )

        adjacent = {
            ("Winter", "Autumn"), ("Autumn", "Winter"),
            ("Winter", "Spring"), ("Spring", "Winter"),
            ("Spring", "Summer"), ("Summer", "Spring"),
            ("Summer", "Autumn"), ("Autumn", "Summer"),
        }

        is_seasonal = False
        peak_seasons: list[str] = []
        concentration = 0.0

        if top1_pct >= 70:
            is_seasonal = True
            peak_seasons = [seasons.iloc[0]["season"]]
            concentration = top1_pct
        elif len(seasons) >= 2:
            s1 = seasons.iloc[0]["season"]
            s2 = seasons.iloc[1]["season"]
            if top2_pct >= 70 and (s1, s2) in adjacent:
                is_seasonal = True
                # Order seasons in calendar sense (Spring < Summer etc.)
                order = ["Winter", "Spring", "Summer", "Autumn"]
                peak_seasons = sorted([s1, s2], key=order.index)
                concentration = top2_pct

        if is_seasonal:
            label = "–".join(peak_seasons)  # e.g. "Autumn–Winter"
            out[str(product)] = {
                "isSeasonal": True,
                "peakSeasons": peak_seasons,
                "concentrationPct": round(concentration, 1),
                "label": label,
            }

    _seasonal_cache[user_id] = out
    return out


def _required_horizon_for(
    user_id: int,
    period: int,
    start_date: str | None,
    end_date_str: str | None,
) -> int:
    """How far past the training data does this request need predictions?

    Forecasts are anchored on TODAY (see `_slice_future`), so the cache
    must reach at least `today + period`. When the data is from a past
    year, the gap between data_end and today is added to the requested
    horizon — otherwise asking for "next 7 days" against 2022 data in
    2026 would slice an empty window.
    """
    today = pd.Timestamp.now().normalize()
    data_end = _data_end_for(user_id)
    if data_end is None:
        # No data uploaded — return the requested period as a placeholder;
        # _get_predictions will raise a clean error downstream.
        if start_date and end_date_str:
            hi = pd.Timestamp(max(start_date, end_date_str))
            return max(period, 30, int((hi - today).days) + 1)
        return period
    if start_date and end_date_str:
        hi = pd.Timestamp(max(start_date, end_date_str))
        return max(period, int((hi - data_end).days) + 1)
    # Preset N days from today: forecast end = today + period, so horizon
    # past data_end = (today - data_end) + period.
    forecast_end = max(today, data_end) + pd.Timedelta(days=period)
    return max(period, int((forecast_end - data_end).days) + 1)


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
            _horizon_cache.clear()
            _price_cache.clear()
            _baseline_cache.clear()
            _seasonal_cache.clear()
            try:
                for p in CACHE_DIR.glob("predictions_user_*.pkl"):
                    p.unlink(missing_ok=True)
            except Exception:
                pass
        else:
            _predictions_cache.pop(user_id, None)
            _category_lookup.pop(user_id, None)
            _data_end_date.pop(user_id, None)
            _horizon_cache.pop(user_id, None)
            _price_cache.pop(user_id, None)
            _baseline_cache.pop(user_id, None)
            _seasonal_cache.pop(user_id, None)
            try:
                _cache_file(user_id).unlink(missing_ok=True)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────
# Helpers to slice predictions into API responses
def _round_daily_to_total(values: list[float]) -> list[int]:
    """
    Round a list of daily float forecasts to integers such that their sum
    equals the rounded total. Uses the largest-remainder method: floor
    every value, then hand the leftover units to the days with the biggest
    fractional parts.

    Why: for low-volume items Prophet emits ~0.86 units/day over 7 days.
    Truncating each with int() gives [0, 0, 0, 0, 0, 0, 0] — a peak of 0
    with a total of 6, which looks broken to the manager. This preserves
    the total and surfaces which days actually carry the predicted units.
    """
    if not values:
        return []
    target = max(0, round(sum(values)))
    floors = [max(0, int(v)) for v in values]
    leftover = target - sum(floors)
    if leftover <= 0:
        return floors
    order = sorted(range(len(values)), key=lambda i: values[i] - floors[i], reverse=True)
    for i in order[:leftover]:
        floors[i] += 1
    return floors


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
    period: int = Query(7, description="Forecast horizon in days"),
    start_date: str | None = Query(None, description="Optional ISO date, e.g. 2023-04-17"),
    end_date: str | None = Query(None, description="Optional ISO date, e.g. 2023-04-22"),
    user_id: int = Depends(get_current_user_id),
):
    """
    Per-item forecast using the hybrid Prophet model. Returns predictions
    aggregated daily plus the time_period breakdown (morning/afternoon/evening/night).
    """
    required = _required_horizon_for(user_id, period, start_date, end_date)
    try:
        preds = _get_predictions(user_id, required_horizon_days=required)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    item_rows = preds[preds["product"] == target]
    if item_rows.empty:
        raise HTTPException(status_code=404, detail=f"Product '{target}' not in forecast output")

    # Determine whether the uploaded data actually has meaningful time-of-day
    # signal. If every historical order collapsed to a single bucket (e.g.
    # all "night" because the Excel lacked a time column), the model's
    # morning/afternoon/evening predictions are unreliable extrapolations
    # — Prophet never saw those buckets, so it's guessing outside its
    # training distribution. In that case we suppress the breakdown.
    df_hist = load_data(user_id)
    hist_tps = df_hist["time_period"].dropna().astype(str).str.lower().unique() if not df_hist.empty else []
    time_of_day_available = len([tp for tp in hist_tps if str(tp).strip() and tp != "nan"]) >= 2

    future = _slice_future(user_id, item_rows, period, start_date, end_date)

    daily = (
        future.groupby(future["ds"].astype(str))["yhat"]
        .sum()
        .reset_index()
        .rename(columns={"ds": "date", "yhat": "predicted_quantity"})
    )

    daily_dates = daily["date"].tolist() if "date" in daily.columns else daily.iloc[:, 0].tolist()
    regressors = _regressors_for_dates(daily_dates)
    revenue, profit = _revenue_profit(user_id, future)

    # Round daily floats so their ints sum to the rounded total — avoids
    # the "7 days of 0 summing to 6" display bug on low-volume items.
    daily_ints = _round_daily_to_total(daily["predicted_quantity"].tolist())
    total_qty = sum(daily_ints)

    # Peak day is whichever day has the largest rounded int (so the chart,
    # peak tip, and KPI card all tell the same story).
    peak_info = None
    if daily_ints:
        peak_idx = max(range(len(daily_ints)), key=lambda i: daily_ints[i])
        peak_row = daily.iloc[peak_idx]
        peak_info = {
            "date": peak_row["date"],
            "dayLabel": pd.Timestamp(peak_row["date"]).strftime("%a, %b %-d"),
            "qty": int(daily_ints[peak_idx]),
        }
    events = _notable_events(regressors)

    rev_map = _daily_revenue(user_id, future)
    daily_predictions = [
        {
            "date": daily.iloc[i]["date"],
            "predicted_quantity": daily_ints[i],
            "predicted_revenue": round(rev_map.get(daily.iloc[i]["date"], 0.0), 2),
        }
        for i in range(len(daily_ints))
    ]
    _add_prediction_band(daily_predictions, user_id, "predicted_quantity", "predicted_revenue")

    return {
        "scope": "item",
        "target": target,
        "category": _category_lookup.get(user_id, {}).get(target),
        "period": period,
        "model": "Prophet with season, occasion, and time-of-day regressors",
        "totalPredictedQuantity": total_qty,
        "totalPredictedRevenue": round(revenue, 2),
        "totalPredictedProfit": round(profit, 2),
        "peakDay": peak_info,
        "dailyPredictions": daily_predictions,
        "timeOfDayAvailable": time_of_day_available,
        "timePeriodBreakdown": _serialize_rows(future, only_future=False) if time_of_day_available else [],
        # Pre-aggregated per-time-period totals across the whole window.
        # Computed from float yhats and then rounded so low-volume items
        # still show ≥1 in the time buckets where they actually sell —
        # avoids the "all four buckets show 0 even though daily total is
        # 7" display bug that comes from int-flooring per-cell yhats
        # before summing.
        "timePeriodTotals": (lambda f: [
            {"time_period": tp, "predicted_quantity": int(round(max(0.0, f[f["time_period"] == tp]["yhat"].sum())))}
            for tp in ["morning", "Afternoon", "Evening", "night"]
        ])(future) if time_of_day_available else [],
        "regressorsUsed": regressors,
        "notableEvents": events,
        "managerTips": _summarize_for_manager(regressors, total_qty, peak_info, events),
    }


@router.get("/api/forecast/category", summary="Forecast all items in a category")
def forecast_category(
    target: str = Query(..., description="Category name (e.g. 'Hot Drinks')"),
    period: int = Query(7, description="Forecast horizon in days"),
    start_date: str | None = Query(None, description="Optional ISO date, e.g. 2023-04-17"),
    end_date: str | None = Query(None, description="Optional ISO date, e.g. 2023-04-22"),
    user_id: int = Depends(get_current_user_id),
):
    """Aggregate every item in the category, sort by predicted quantity."""
    required = _required_horizon_for(user_id, period, start_date, end_date)
    try:
        preds = _get_predictions(user_id, required_horizon_days=required)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    cat_map = _category_lookup.get(user_id, {})
    products_in_cat = [p for p, c in cat_map.items() if c == target]
    if not products_in_cat:
        raise HTTPException(status_code=404, detail=f"Category '{target}' not found")

    cat_rows = _slice_future(user_id, preds[preds["product"].isin(products_in_cat)], period, start_date, end_date)

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

    daily_ints = _round_daily_to_total(daily_totals["predicted_quantity"].tolist())
    total_qty = sum(daily_ints)
    peak_info = None
    if daily_ints:
        peak_idx = max(range(len(daily_ints)), key=lambda i: daily_ints[i])
        peak_row = daily_totals.iloc[peak_idx]
        peak_info = {
            "date": peak_row["date"],
            "dayLabel": pd.Timestamp(peak_row["date"]).strftime("%a, %b %-d"),
            "qty": int(daily_ints[peak_idx]),
        }
    events = _notable_events(regressors)
    heatmap_data, heatmap_peak = _forecast_heatmap(user_id, cat_rows)

    rev_map = _daily_revenue(user_id, cat_rows)
    chart_data = [
        {
            "date": daily_totals.iloc[i]["date"],
            "predicted": daily_ints[i],
            "predicted_revenue": round(rev_map.get(daily_totals.iloc[i]["date"], 0.0), 2),
        }
        for i in range(len(daily_ints))
    ]
    _add_prediction_band(chart_data, user_id, "predicted", "predicted_revenue")

    return {
        "scope": "category",
        "target": target,
        "period": period,
        "heatmapData": heatmap_data,
        "heatmapPeak": heatmap_peak,
        "model": "Prophet with season, occasion, and time-of-day regressors",
        "itemCount": int(per_product.shape[0]),
        "totalPredictedQuantity": total_qty,
        "totalPredictedRevenue": round(revenue, 2),
        "totalPredictedProfit": round(profit, 2),
        "peakDay": peak_info,
        "items": [
            {"name": r["product"], "totalPredictedQuantity": int(r["totalPredictedQuantity"])}
            for _, r in per_product.iterrows()
        ],
        "chartData": chart_data,
        "regressorsUsed": regressors,
        "notableEvents": events,
        "managerTips": _summarize_for_manager(regressors, total_qty, peak_info, events),
    }


@router.get("/api/forecast/total", summary="Forecast total restaurant sales")
def forecast_total(
    period: int = Query(7, description="Forecast horizon in days"),
    start_date: str | None = Query(None, description="Optional ISO date, e.g. 2023-04-17"),
    end_date: str | None = Query(None, description="Optional ISO date, e.g. 2023-04-22"),
    user_id: int = Depends(get_current_user_id),
):
    """Grand total across every product."""
    required = _required_horizon_for(user_id, period, start_date, end_date)
    try:
        preds = _get_predictions(user_id, required_horizon_days=required)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    future = _slice_future(user_id, preds, period, start_date, end_date)
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

    # Per-item totals so the UI can show top/bottom 5 items across the
    # whole menu. Category is kept so the frontend can display it inline.
    per_item = (
        future.groupby("product")
        .agg(totalPredictedQuantity=("yhat", "sum"))
        .reset_index()
        .sort_values("totalPredictedQuantity", ascending=False)
    )
    per_item["category"] = per_item["product"].map(_category_lookup.get(user_id, {}))

    # Pull historical per-product totals so the watch list only shows
    # items that are genuinely on the active menu. Without this filter,
    # the list surfaces products that sold once or twice in the entire
    # training year (e.g., 'Morning Waffle' = 1 unit/year, 'Black Tea
    # 1L pot' = 1 unit/year) — those are essentially-discontinued
    # SKUs and showing them as "predicted 1 unit" reads as a bug to
    # the manager. Threshold of 30 units/year (~1 every 12 days) marks
    # the boundary between "active slow mover" and "barely on menu".
    hist_df = load_data(user_id, include_synthetic=False)
    hist_totals = (
        hist_df.groupby("Product")["Quantity"].sum().to_dict()
        if not hist_df.empty else {}
    )
    per_item["historicalUnits"] = per_item["product"].map(hist_totals).fillna(0).astype(int)
    HIST_MIN_UNITS = 30

    seasonality = _seasonality_profile(user_id)

    def _enrich(row) -> dict:
        name = row["product"]
        seasonal = seasonality.get(name)
        return {
            "name": name,
            "category": row["category"] or "—",
            "totalPredictedQuantity": int(row["totalPredictedQuantity"]),
            "historicalUnits": int(row.get("historicalUnits", 0)),
            "seasonal": seasonal,  # None or {isSeasonal, peakSeasons, label, ...}
        }

    top_items = [_enrich(r) for _, r in per_item.head(5).iterrows()]
    # Bottom 5 — slow movers worth watching. Filter requires BOTH:
    # (a) forecast >= 1 unit so we don't surface zero-prediction
    #     rounding artefacts, AND
    # (b) historical total >= HIST_MIN_UNITS so we don't surface
    #     barely-sold-ever items that look like model error.
    bottom_candidates = per_item[
        (per_item["totalPredictedQuantity"] >= 1.0)
        & (per_item["historicalUnits"] >= HIST_MIN_UNITS)
    ]
    bottom_items = [_enrich(r) for _, r in bottom_candidates.tail(5).iloc[::-1].iterrows()]

    daily_totals = (
        future.groupby(future["ds"].astype(str))["yhat"]
        .sum()
        .reset_index()
        .rename(columns={"ds": "date", "yhat": "predicted_quantity"})
    )

    regressors = _regressors_for_dates(daily_totals["date"].tolist())
    revenue, profit = _revenue_profit(user_id, future)

    daily_ints = _round_daily_to_total(daily_totals["predicted_quantity"].tolist())
    total_qty = sum(daily_ints)
    peak_info = None
    if daily_ints:
        peak_idx = max(range(len(daily_ints)), key=lambda i: daily_ints[i])
        peak_row = daily_totals.iloc[peak_idx]
        peak_info = {
            "date": peak_row["date"],
            "dayLabel": pd.Timestamp(peak_row["date"]).strftime("%a, %b %-d"),
            "qty": int(daily_ints[peak_idx]),
        }
    events = _notable_events(regressors)
    heatmap_data, heatmap_peak = _forecast_heatmap(user_id, future)

    rev_map = _daily_revenue(user_id, future)
    chart_data = [
        {
            "date": daily_totals.iloc[i]["date"],
            "predicted": daily_ints[i],
            "predicted_revenue": round(rev_map.get(daily_totals.iloc[i]["date"], 0.0), 2),
        }
        for i in range(len(daily_ints))
    ]
    _add_prediction_band(chart_data, user_id, "predicted", "predicted_revenue")

    return {
        "scope": "total",
        "period": period,
        "model": "Prophet with season, occasion, and time-of-day regressors",
        "heatmapData": heatmap_data,
        "heatmapPeak": heatmap_peak,
        "totalPredictedQuantity": total_qty,
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
        "topItems": top_items,
        "bottomItems": bottom_items,
        "chartData": chart_data,
        "regressorsUsed": regressors,
        "notableEvents": events,
        "managerTips": _summarize_for_manager(regressors, total_qty, peak_info, events),
    }
