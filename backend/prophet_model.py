"""
Prophet Hybrid Forecasting Model — Noura Aldossari (top-down rewrite by Haneen)

This module trains a single Prophet model on the *aggregate* daily sales
total across the whole menu, then disaggregates the daily forecast back
into per-product, per-time-period predictions using each product's
historical share. This is the standard top-down approach used by retail
forecasting systems and gives us three things:

  1. Speed — one Prophet fit instead of 25-70. On Render's 0.1-CPU free
     tier this brings first-forecast time from ~12 minutes to ~30s.
  2. Accuracy at the aggregate level — cross-product noise averages out
     in the total, so the trend / weekly seasonality / holiday signals
     are much cleaner than what any individual product offers. The
     per-product results then inherit this cleaner signal.
  3. Sensible per-product behaviour — disaggregation uses each product's
     observed (day-of-week × time_period) share of the total, which
     preserves "Spanish Latte sells most on Saturday afternoons" without
     having to fit a separate model per product.

Saudi calendar effects (Ramadan, Eid al-Fitr, Eid al-Adha, National Day,
Payday week anchored on the 27th) are passed via Prophet's `holidays`
parameter so each gets its own learnable coefficient. Day-of-week
patterns (the Saudi Fri/Sat weekend) are captured by Prophet's built-in
`weekly_seasonality` with a strong prior so they're not regularized away.

Public helpers (`compute_season`, `compute_occasion`, `is_payday`) are
preserved for the route layer's per-date labelling and the upload-time
enrichment.
"""
from __future__ import annotations

import pandas as pd
from prophet import Prophet
import numpy as np
from hijri_converter import Gregorian
from sklearn.metrics import mean_absolute_error


SEASONS = ["Winter", "Spring", "Summer", "Autumn"]
SEASON_COLS = [f"season_{s}" for s in SEASONS]

TIME_ORDER = ['morning', 'Afternoon', 'Evening', 'night']
TIME_MAPPING = {k: v for v, k in enumerate(TIME_ORDER)}


def is_payday(d) -> bool:
    """Saudi payday-week window (day 27 → next-month-5)."""
    day = pd.Timestamp(d).day
    return day >= 27 or day <= 5


def compute_season(d) -> str:
    m = pd.Timestamp(d).month
    if m in (12, 1, 2):  return "Winter"
    if m in (3, 4, 5):   return "Spring"
    if m in (6, 7, 8):   return "Summer"
    return "Autumn"


def compute_occasion(d) -> str:
    """Display-only label used by the API to tell the manager *why* a
    date is special. The model itself does not consume this string —
    it consumes the holidays DataFrame from `build_saudi_holidays` and
    Prophet's weekly_seasonality."""
    ts = pd.Timestamp(d)
    try:
        h = Gregorian(ts.year, ts.month, ts.day).to_hijri()
        if h.month == 9:
            return "Ramadan"
        if h.month == 10 and 1 <= h.day <= 3:
            return "Eid al-Fitr"
        if h.month == 12 and 10 <= h.day <= 13:
            return "Eid al-Adha"
    except Exception:
        pass
    if ts.month == 9 and ts.day == 23:
        return "Saudi National Day"
    if is_payday(ts):
        return "Payday"
    if ts.weekday() in (4, 5):
        return "Weekend"
    return "Normal Day"


def _add_season_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for s in SEASONS:
        out[f"season_{s}"] = (out["season"] == s).astype(int)
    return out


def build_saudi_holidays(start, end) -> pd.DataFrame:
    """One continuous payday window from the 27th through next month's 5th
    (upper_window=9), plus the religious + national days. See git
    history for the data analysis that drove this calendar shape."""
    start_t = pd.Timestamp(start) - pd.Timedelta(days=35)
    end_t = pd.Timestamp(end) + pd.Timedelta(days=35)

    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    cur = start_t
    while cur <= end_t:
        try:
            h = Gregorian(cur.year, cur.month, cur.day).to_hijri()
            if h.month == 9 and h.day == 1:
                key = ("ramadan", cur.date().isoformat())
                if key not in seen:
                    rows.append({"holiday": "ramadan", "ds": cur,
                                 "lower_window": 0, "upper_window": 28})
                    seen.add(key)
            if h.month == 10 and h.day == 1:
                key = ("eid_fitr", cur.date().isoformat())
                if key not in seen:
                    rows.append({"holiday": "eid_fitr", "ds": cur,
                                 "lower_window": 0, "upper_window": 2})
                    seen.add(key)
            if h.month == 12 and h.day == 10:
                key = ("eid_adha", cur.date().isoformat())
                if key not in seen:
                    rows.append({"holiday": "eid_adha", "ds": cur,
                                 "lower_window": 0, "upper_window": 3})
                    seen.add(key)
        except Exception:
            pass

        if cur.month == 9 and cur.day == 23:
            rows.append({"holiday": "saudi_national_day", "ds": cur,
                         "lower_window": 0, "upper_window": 0})

        if cur.day == 27:
            rows.append({"holiday": "payday_week", "ds": cur,
                         "lower_window": 0, "upper_window": 9})

        cur += pd.Timedelta(days=1)

    if not rows:
        return pd.DataFrame(columns=["holiday", "ds", "lower_window", "upper_window"])
    return pd.DataFrame(rows)


def _build_prophet(holidays_df: pd.DataFrame) -> Prophet:
    """Tuned Prophet config for Saudi cafe data. Documented elsewhere why
    each prior is what it is."""
    m = Prophet(
        daily_seasonality=False,
        weekly_seasonality=False,  # added manually below for stronger weight
        yearly_seasonality=False,
        holidays=holidays_df if not holidays_df.empty else None,
        seasonality_prior_scale=25.0,
        holidays_prior_scale=100.0,
        changepoint_prior_scale=0.01,
    )
    m.add_seasonality(name='weekly', period=7, fourier_order=10, prior_scale=50.0)
    for col in SEASON_COLS:
        m.add_regressor(col)
    return m


def run_forecast(df: pd.DataFrame, save_csv: bool = False, horizon_days: int = 30) -> pd.DataFrame:
    """
    Top-down: fit one Prophet on the daily total, disaggregate to
    per-product per-time_period using historical share. Returns a
    DataFrame with the same columns the route layer expects:
        ds, yhat, y, product, type, percentage_error, time_period
    """
    np.random.seed(42)

    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df['season'] = df['date'].apply(compute_season)
    df['dow'] = df['date'].dt.day_name()

    # ── Aggregate daily total across the whole menu ──────────────────────
    total_daily = df.groupby('date', as_index=False)['quantity'].sum()
    total_daily.columns = ['ds', 'y']
    if total_daily.empty:
        return pd.DataFrame(columns=[
            'ds', 'yhat', 'y', 'product', 'type', 'percentage_error', 'time_period'
        ])

    all_dates = pd.date_range(total_daily['ds'].min(), total_daily['ds'].max())
    total_daily = (
        total_daily.set_index('ds')
        .reindex(all_dates)
        .fillna(0.0)
        .rename_axis('ds')
        .reset_index()
    )
    total_daily['season'] = total_daily['ds'].apply(compute_season)
    total_daily = _add_season_columns(total_daily)

    holidays_df = build_saudi_holidays(
        total_daily['ds'].min(),
        total_daily['ds'].max() + pd.Timedelta(days=horizon_days),
    )

    # ── 80/20 split for MAE diagnostic only ──────────────────────────────
    split = int(len(total_daily) * 0.8)
    if split < len(total_daily):
        try:
            eval_model = _build_prophet(holidays_df)
            eval_model.fit(total_daily.iloc[:split][['ds', 'y'] + SEASON_COLS])
            test_pred = eval_model.predict(total_daily.iloc[split:][['ds'] + SEASON_COLS])
            mae = mean_absolute_error(total_daily.iloc[split:]['y'], test_pred['yhat'])
            print(f"MAE (Test) — total daily sales: {round(mae, 2)}")
        except Exception as e:
            print(f"MAE eval skipped: {e}")

    # ── Production model: fit on FULL data ───────────────────────────────
    model = _build_prophet(holidays_df)
    model.fit(total_daily[['ds', 'y'] + SEASON_COLS])

    future_dates = pd.date_range(
        start=total_daily['ds'].min(),
        end=total_daily['ds'].max() + pd.Timedelta(days=horizon_days),
    )
    future = pd.DataFrame({'ds': future_dates})
    future['season'] = future['ds'].apply(compute_season)
    future = _add_season_columns(future)
    forecast = model.predict(future[['ds'] + SEASON_COLS])
    forecast['yhat'] = forecast['yhat'].clip(lower=0)
    forecast = forecast[['ds', 'yhat']]
    forecast['dow'] = forecast['ds'].dt.day_name()

    # ── Per-product, per-(day-of-week, time_period) historical share ─────
    # Numerator: this product's qty within (dow, time_period). Denominator:
    # total qty within the same (dow, time_period). Result: fraction of the
    # daily total that historically went to (product, time_period) on that
    # day-of-week. Captures "Spanish Latte sells most on Sat afternoons"
    # without needing a per-product Prophet.
    pivot_num = (
        df.groupby(['name', 'dow', 'time_period'])['quantity']
        .sum()
        .reset_index()
        .rename(columns={'quantity': 'product_qty'})
    )
    pivot_den = (
        df.groupby(['dow', 'time_period'])['quantity']
        .sum()
        .reset_index()
        .rename(columns={'quantity': 'total_qty'})
    )
    share = pivot_num.merge(pivot_den, on=['dow', 'time_period'])
    share['fraction'] = share.apply(
        lambda r: r['product_qty'] / r['total_qty'] if r['total_qty'] > 0 else 0.0,
        axis=1,
    )

    # Backstop: when a (dow, time_period) bucket has zero total in history,
    # fall back to the product's overall share so the forecast isn't blank
    # on that bucket.
    overall_per_product = (
        df.groupby('name')['quantity'].sum() / max(df['quantity'].sum(), 1)
    ).to_dict()
    overall_tp_ratio = {tp: 0.0 for tp in TIME_ORDER}
    tp_totals = df.groupby('time_period')['quantity'].sum()
    tp_total_sum = float(tp_totals.sum()) or 1.0
    for tp in TIME_ORDER:
        overall_tp_ratio[tp] = float(tp_totals.get(tp, 0)) / tp_total_sum

    product_names = list(overall_per_product.keys())
    share_lookup: dict[tuple[str, str, str], float] = {
        (r['name'], r['dow'], r['time_period']): float(r['fraction'])
        for _, r in share.iterrows()
    }

    # ── Disaggregate the daily forecast ──────────────────────────────────
    rows = []
    for _, fc in forecast.iterrows():
        ds = fc['ds']
        dow = fc['dow']
        daily_total = float(fc['yhat'])
        for product in product_names:
            for tp in TIME_ORDER:
                frac = share_lookup.get((product, dow, tp))
                if frac is None or frac <= 0:
                    # Fallback: product's overall share × overall tp share
                    frac = overall_per_product.get(product, 0.0) * overall_tp_ratio[tp]
                yhat = daily_total * frac
                rows.append({
                    'ds': ds,
                    'time_period': tp,
                    'yhat': int(round(max(0.0, yhat))),
                    'product': product,
                })
    pred_df = pd.DataFrame(rows)

    # Attach observed values where they exist so the route layer can
    # filter actual vs future rows correctly.
    observed = (
        df.groupby(['date', 'time_period', 'name'], as_index=False)['quantity']
        .sum()
        .rename(columns={'date': 'ds', 'name': 'product', 'quantity': 'y'})
    )
    pred_df = pred_df.merge(observed, on=['ds', 'time_period', 'product'], how='left')
    pred_df['type'] = pred_df['y'].apply(lambda x: 'actual' if pd.notna(x) else 'future')

    percentage_error = (abs(pred_df['y'] - pred_df['yhat']) / pred_df['y']) * 100
    percentage_error = percentage_error.replace([np.inf, -np.inf], np.nan)
    pred_df['percentage_error'] = (
        percentage_error.round(0).astype('Int64').astype(str) + '%'
    )

    pred_df = pred_df.drop_duplicates(subset=['ds', 'product', 'time_period'])

    # Order products by total volume (matches what the legacy code did)
    product_order = (
        df.groupby('name')['quantity'].sum().sort_values(ascending=False).index.tolist()
    )
    pred_df['product'] = pd.Categorical(pred_df['product'], categories=product_order, ordered=True)
    pred_df['time_period'] = pd.Categorical(pred_df['time_period'], categories=TIME_ORDER, ordered=True)
    pred_df = pred_df.sort_values(by=['product', 'ds', 'time_period'])

    out = pred_df[['ds', 'yhat', 'y', 'product', 'type', 'percentage_error', 'time_period']]

    if save_csv:
        out.to_csv("all_products_predictions.csv", index=False)
        print(" Done  ")

    return out


if __name__ == "__main__":
    df = pd.read_excel("data/sample_sales_2022.xlsx")
    run_forecast(df, save_csv=True)
