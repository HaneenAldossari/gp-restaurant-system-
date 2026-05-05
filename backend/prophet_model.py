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

import os

import pandas as pd
from prophet import Prophet
import numpy as np
from hijri_converter import Gregorian
from sklearn.metrics import mean_absolute_error


# FORECAST_MODE switch:
#   "top_down"    — one Prophet on the daily aggregate; fast, good for
#                   free-tier hosting; per-product peaks get diluted
#                   because they're averaged into the menu total before
#                   training. (default — safe everywhere)
#   "per_product" — one Prophet per top-N products; slower (~30s on a
#                   modern laptop, minutes on Render free tier) but
#                   captures item-specific Eid/Ramadan/payday spikes
#                   that are washed out at the aggregate level.
#                   Recommended on localhost where speed isn't a
#                   constraint and you need accurate per-item peaks.
FORECAST_MODE = os.getenv("FORECAST_MODE", "top_down").lower()
# Cap on how many products get their own Prophet in per_product mode.
# The long tail (sparse, low-volume items) falls back to top-down
# disaggregation so we don't waste minutes fitting models for items
# with 5 data points.
PER_PRODUCT_TOP_N = int(os.getenv("FORECAST_PER_PRODUCT_TOP_N", "30"))


SEASONS = ["Winter", "Spring", "Summer", "Autumn"]
SEASON_COLS = [f"season_{s}" for s in SEASONS]

TIME_ORDER = ['morning', 'Afternoon', 'Evening', 'night']
TIME_MAPPING = {k: v for v, k in enumerate(TIME_ORDER)}


def is_payday(d) -> bool:
    """Saudi payday-week window (day 27 → next-month-5)."""
    day = pd.Timestamp(d).day
    return day >= 27 or day <= 5


def compute_season(d) -> str:
    """Season mapping calibrated to Saudi cafe consumption, not the
    astronomical calendar. Riyadh's May (~32 °C) and September (~33 °C)
    behave like summer for cold-drink demand, even though they fall in
    Spring / Autumn under the standard mapping. Reviewer testing
    flagged that the May-June forecast under-predicted Cold Coffee
    Drinks (forecast 1050 vs actual 1746 in May-Jun 2022) because the
    standard mapping pulled May into 'Spring' and the disaggregation
    share reflected the cooler March-April-May average. Moving May
    and September into Summer aligns the share with what cafe sales
    actually do in those months.
        Winter:  Dec, Jan, Feb        (Riyadh ~15 °C — hot drinks dominant)
        Spring:  Mar, Apr             (warming, 25-30 °C — transition)
        Summer:  May, Jun, Jul, Aug, Sep   (hot, 32-40 °C — cold drinks dominant)
        Autumn:  Oct, Nov             (cooling, 25-30 °C — transition)
    Asymmetric but defensible in the Saudi context.
    """
    m = pd.Timestamp(d).month
    if m in (12, 1, 2):           return "Winter"
    if m in (3, 4):               return "Spring"
    if m in (5, 6, 7, 8, 9):      return "Summer"
    return "Autumn"


def compute_occasion(d) -> str:
    """Display-only label used by the API to tell the manager *why* a
    date is special. The model itself does not consume this string —
    it consumes the holidays DataFrame from `build_saudi_holidays` and
    Prophet's weekly_seasonality.

    Distinguishes the anchor day (the 27th of each Gregorian month —
    when civil-servant salaries land) from the spillover spending
    window that follows. The model treats both as one continuous
    `payday_week` holiday under the hood; the label split is purely
    so the UI can say "Payday: Apr 27" rather than "Payday: Apr 30"
    for a forecast that starts mid-window."""
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
    if ts.month == 2 and ts.day == 22:
        return "Saudi Founding Day"
    if ts.day == 27:
        return "Payday"
    if is_payday(ts):
        # day 28-31 of this month or day 1-5 of next month
        return "Post-payday spending"
    if ts.weekday() in (4, 5):
        return "Weekend"
    return "Normal Day"


def _add_season_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for s in SEASONS:
        out[f"season_{s}"] = (out["season"] == s).astype(int)
    return out


def _attach_regressors(df: pd.DataFrame) -> pd.DataFrame:
    """Add season one-hot columns AND the daily max-temperature column
    that Prophet uses as a real-weather regressor. df must have a 'ds'
    datetime column and a 'season' string column.

    Temperature is fetched from Open-Meteo's archive (cached locally).
    For dates beyond historical (i.e. future forecasts), a climate
    average for the same month-day is used. Any remaining gaps are
    filled with the dataset mean so Prophet never sees NaN."""
    out = _add_season_columns(df)
    out['temp_max'] = float('nan')

    if not out.empty and 'ds' in out.columns:
        try:
            from weather import get_daily_temperatures
            weather = get_daily_temperatures(out['ds'].min(), out['ds'].max())
            if not weather.empty:
                # Drop our placeholder NaN column before merge
                out = out.drop(columns=['temp_max']).merge(
                    weather[['ds', 'temp_max']], on='ds', how='left',
                )
        except Exception as e:
            # Don't break training if weather fetch fails — fall back
            # to a constant temperature column (Prophet treats it as
            # a no-op regressor with zero learnable signal).
            print(f"[weather] Skipped: {type(e).__name__}: {e}")

    if out['temp_max'].isna().all():
        # Total network failure — use a safe Saudi default so Prophet
        # has a non-NaN regressor column. Acts as a constant offset
        # which Prophet will absorb into the intercept.
        out['temp_max'] = 28.0
    elif out['temp_max'].isna().any():
        # Partial gaps (e.g. very-far-future dates with no climate
        # average yet) — fill with the available mean.
        out['temp_max'] = out['temp_max'].fillna(out['temp_max'].mean())

    return out


def build_saudi_holidays(start, end) -> pd.DataFrame:
    """Saudi holiday calendar with realistic, phase-aware windows.

    Real Saudi café behaviour around Eid is a 3-phase pattern, NOT a
    single uniform lift:
      • Pre-Eid (3 days before)   — strong spike: shopping, gifts,
                                     family preparations
      • Eid days themselves       — modest or slight dip: families at
                                     home, places of worship busy
      • Post-Eid bounce (1-3 after) — strong spike: visits, gatherings

    Splitting Eid into three holidays gives each phase its own
    coefficient instead of averaging them into one mushy "Eid window"
    that under-predicts the pre/post peaks AND over-predicts the Eid-day
    flatness.

    Other holidays:
      • Ramadan whole month — single window (devotional pattern dominates)
      • Saudi National Day — 1 day before + the day + 1 after
      • Payday week — anchor day 27 + spillover through next-month-5
    """
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
                    # 2-phase split (was 4 — pre/day1/bounce/post). With
                    # one year of training data each phase had a single
                    # observation, which Prophet's MAP fit cannot pin
                    # a confident +88 % coefficient against. Keeping
                    # only `day1` (suppressed: most people at home) and
                    # `bounce` (anchor +1, days 2-4 — the actual spike)
                    # concentrates the signal into two coefficients
                    # with one sample each, instead of four.
                    bounce_anchor = cur + pd.Timedelta(days=1)
                    rows.append({"holiday": "eid_fitr_day1", "ds": cur,
                                 "lower_window": 0, "upper_window": 0})
                    rows.append({"holiday": "eid_fitr_bounce", "ds": bounce_anchor,
                                 "lower_window": 0, "upper_window": 3})
                    seen.add(key)
            if h.month == 12 and h.day == 10:
                key = ("eid_adha", cur.date().isoformat())
                if key not in seen:
                    # Same 2-phase split. Verified against the 2022
                    # data: Eid Al Adha day 1 (Sat Jul 9) = 158 orders
                    # (LOW), day 2 (Sun Jul 10) = 328 (PEAK +88 %),
                    # days 3-4 = 252/244 (still elevated, captured by
                    # the bounce window), day 5+ = back to baseline.
                    bounce_anchor = cur + pd.Timedelta(days=1)
                    rows.append({"holiday": "eid_adha_day1", "ds": cur,
                                 "lower_window": 0, "upper_window": 0})
                    rows.append({"holiday": "eid_adha_bounce", "ds": bounce_anchor,
                                 "lower_window": 0, "upper_window": 3})
                    seen.add(key)
        except Exception:
            pass

        # Saudi National Day — single day. Earlier we used a -1/+1
        # window thinking it captured "long weekend" lift, but in real
        # data the day BEFORE is just a normal weekday and the day
        # AFTER is often an early-close (e.g. Sep 24, 2022 had only
        # 77 orders vs Sep 23's 270). Pooling those into one holiday
        # coefficient drags the National Day lift toward zero or even
        # negative. Single-day window keeps the coefficient pure.
        if cur.month == 9 and cur.day == 23:
            rows.append({"holiday": "saudi_national_day", "ds": cur,
                         "lower_window": 0, "upper_window": 0})

        # Saudi Founding Day — Feb 22, established 2022. Same reasoning:
        # in 2022 the cafe was closed for the entire surrounding week,
        # so a -1/+1 window pulls the holiday coefficient toward 0.
        # Single-day keeps the signal isolated.
        if cur.month == 2 and cur.day == 22:
            rows.append({"holiday": "saudi_founding_day", "ds": cur,
                         "lower_window": 0, "upper_window": 0})

        # Payday split into two phases — same reasoning as Eid:
        # the late window (days 27-31) shows +25% lift in the data
        # while the early window (days 1-5) shows +50%. A single
        # window anchored on day 27 averaged these into a uniform
        # +35% that under-predicted day 1-5 peaks AND over-predicted
        # day 27-31. Two coefficients lets each phase calibrate.
        if cur.day == 27:
            rows.append({"holiday": "payday_late", "ds": cur,
                         "lower_window": 0, "upper_window": 4})
        if cur.day == 1:
            rows.append({"holiday": "payday_early", "ds": cur,
                         "lower_window": 0, "upper_window": 4})

        cur += pd.Timedelta(days=1)

    if not rows:
        return pd.DataFrame(columns=["holiday", "ds", "lower_window", "upper_window"])
    return pd.DataFrame(rows)


REGRESSOR_COLS = SEASON_COLS + ['temp_max']


def _build_prophet(holidays_df: pd.DataFrame, with_yearly: bool = False) -> Prophet:
    """Tuned Prophet config for Saudi cafe data.

    yearly_seasonality is OFF — with only one year of training data
    Prophet's yearly Fourier component oscillates uncontrollably past
    the first repetition cycle (we observed Dec 2024 onward collapsing
    to zero with it on). Year-of-data effects are instead handled by:
      - the season one-hot regressor (Winter/Spring/Summer/Autumn)
      - the holidays mechanism (Ramadan, Eid, payday, etc.)
      - the season-aware disaggregation share (so winter forecasts
        skew hot drinks, summer forecasts skew cold drinks)
    The trend without yearly_seasonality stays close to the training
    mean (changepoint_prior_scale=0.01 is conservative); the global
    baseline_scale post-step normalises the scale to historical levels."""
    m = Prophet(
        daily_seasonality=False,
        weekly_seasonality=False,  # added manually below
        yearly_seasonality=with_yearly,
        holidays=holidays_df if not holidays_df.empty else None,
        seasonality_prior_scale=25.0,
        holidays_prior_scale=500.0,
        # growth='flat' disables Prophet's linear/logistic trend
        # entirely. With only one year of training data, the trend
        # component picked up the positive Q4 drift from the December
        # surge and extrapolated it upward across the full forecast
        # horizon — Prophet's raw 1095-day mean inflated to ~465/day
        # at any changepoint setting, vs the historical 195/day. The
        # global baseline rescaler then under-corrected the near-
        # future window (forecast mean was 280/day vs real 206 — 35 %
        # over, reviewer-flagged). With growth='flat', Prophet emits
        # a single intercept plus seasonality + holidays + regressors;
        # weekly cycle and Eid lifts are preserved, multi-year trend
        # extrapolation is gone, and the rescaler only needs minor
        # adjustment. Restoring trend would need 2+ years of data so
        # year-over-year growth signal can be separated from one-off
        # late-year noise.
        growth='flat',
    )
    # Weekly seasonality at moderate strength (prior=25, fourier=6).
    # Previously 100/10 produced a near-deterministic weekly cycle
    # (lag-7 autocorr 0.94 vs real ~0). Pure-default 10/4 went the
    # other way — the Fri/Mon lift in 2022 actuals is 1.40× but the
    # forecast collapsed to 1.15×, which a reviewer flagged as
    # "no weekend pattern." 25/6 lands in between: visible Fri/Sat
    # lift, lag-7 still well below the deterministic 0.94, and the
    # day-to-day pattern is no longer carbon-copy across weeks.
    m.add_seasonality(name='weekly', period=7, fourier_order=6, prior_scale=25.0)
    for col in REGRESSOR_COLS:
        m.add_regressor(col)
    return m


def _train_one_product(
    product_df: pd.DataFrame,
    holidays_df: pd.DataFrame,
    horizon_days: int,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Train one Prophet on a single product's daily totals and return
    its forecast plus the historical time-of-day distribution. Used by
    the per_product path.

    Returns (forecast_df, tp_ratio_dict) where forecast_df has columns
    ds and yhat (clipped to >= 0)."""
    daily = product_df.groupby('date', as_index=False)['quantity'].sum()
    daily.columns = ['ds', 'y']
    if daily.empty:
        return pd.DataFrame(columns=['ds', 'yhat']), {tp: 0.25 for tp in TIME_ORDER}

    # Train only on days with actual sales — store-closure days
    # (zero rows) shouldn't be filled with 0s and fed to Prophet
    # because the model would learn "this date type = no demand"
    # rather than "no signal". Prophet handles non-continuous
    # date series natively (the trend interpolates).
    daily['season'] = daily['ds'].apply(compute_season)
    daily = _attach_regressors(daily)

    # Per-product time-of-day historical share so we can split the daily
    # forecast back into morning/afternoon/evening/night the same way
    # top-down mode does.
    tp_totals = product_df.groupby('time_period')['quantity'].sum()
    tp_total_sum = float(tp_totals.sum()) or 1.0
    tp_ratio = {
        tp: float(tp_totals.get(tp, 0)) / tp_total_sum if tp_total_sum > 0 else 0.25
        for tp in TIME_ORDER
    }

    model = _build_prophet(holidays_df)
    model.fit(daily[['ds', 'y'] + REGRESSOR_COLS])

    future_dates = pd.date_range(
        start=daily['ds'].min(),
        end=daily['ds'].max() + pd.Timedelta(days=horizon_days),
    )
    future = pd.DataFrame({'ds': future_dates})
    future['season'] = future['ds'].apply(compute_season)
    future = _attach_regressors(future)
    fc = model.predict(future[['ds'] + REGRESSOR_COLS])
    fc['yhat'] = fc['yhat'].clip(lower=0)
    return fc[['ds', 'yhat']], tp_ratio


def _run_per_product(df: pd.DataFrame, save_csv: bool, horizon_days: int) -> pd.DataFrame:
    """Per-product Prophet fits for the top-N best-selling products,
    plus a top-down fallback for the long tail. Captures item-specific
    holiday spikes (e.g. desserts during Eid) that get averaged away in
    pure top-down mode."""
    product_volume = df.groupby('name')['quantity'].sum().sort_values(ascending=False)
    top_products = product_volume.head(PER_PRODUCT_TOP_N).index.tolist()
    long_tail = [n for n in product_volume.index if n not in set(top_products)]

    holidays_df = build_saudi_holidays(
        df['date'].min(),
        df['date'].max() + pd.Timedelta(days=horizon_days),
    )

    print(f"[per_product] Training {len(top_products)} individual Prophets; "
          f"{len(long_tail)} long-tail items use top-down fallback")

    # ── Per-product Prophet fits for the top N ──────────────────────────
    rows: list[pd.DataFrame] = []
    for product in top_products:
        product_df = df[df['name'] == product]
        try:
            forecast, tp_ratio = _train_one_product(product_df, holidays_df, horizon_days)
        except Exception as e:
            print(f"  · {product}: skipped ({type(e).__name__}: {e})")
            continue
        # Expand daily forecast across the 4 time_period buckets using
        # this product's own historical distribution.
        ds_repeated = np.tile(forecast['ds'].values, len(TIME_ORDER))
        tp_repeated = np.repeat(TIME_ORDER, len(forecast))
        yhat_repeated = np.concatenate([
            forecast['yhat'].values * tp_ratio[tp] for tp in TIME_ORDER
        ])
        rows.append(pd.DataFrame({
            'ds': ds_repeated,
            'time_period': tp_repeated,
            'product': product,
            'yhat': yhat_repeated.clip(min=0),
        }))

    # ── Long-tail: re-use the top-down disaggregation on the same total ──
    if long_tail:
        long_tail_df = df[df['name'].isin(long_tail)].copy()
        if not long_tail_df.empty:
            tail_predictions = _run_top_down_for_tail(
                long_tail_df, holidays_df, horizon_days,
            )
            if not tail_predictions.empty:
                rows.append(tail_predictions)

    if not rows:
        return pd.DataFrame(columns=[
            'ds', 'yhat', 'y', 'product', 'type', 'percentage_error', 'time_period'
        ])

    pred_df = pd.concat(rows, ignore_index=True)

    # Attach observed values + actual/future labels (same as top_down)
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
    product_order = product_volume.index.tolist()
    pred_df['product'] = pd.Categorical(pred_df['product'], categories=product_order, ordered=True)
    pred_df['time_period'] = pd.Categorical(pred_df['time_period'], categories=TIME_ORDER, ordered=True)
    pred_df = pred_df.sort_values(by=['product', 'ds', 'time_period'])

    out = pred_df[['ds', 'yhat', 'y', 'product', 'type', 'percentage_error', 'time_period']]
    if save_csv:
        out.to_csv("all_products_predictions.csv", index=False)
    return out


def _run_top_down_for_tail(
    df_tail: pd.DataFrame,
    holidays_df: pd.DataFrame,
    horizon_days: int,
) -> pd.DataFrame:
    """Run the top-down logic but only on a subset of products (the long
    tail). Reuses the same machinery as run_forecast's top-down path so
    the output schema is identical and concatenation is clean."""
    df_tail = df_tail.copy()
    df_tail['dow'] = df_tail['date'].dt.day_name()

    total_daily = df_tail.groupby('date', as_index=False)['quantity'].sum()
    total_daily.columns = ['ds', 'y']
    if total_daily.empty:
        return pd.DataFrame()
    # Skip closure days (no fillna) — see _train_one_product comment.
    total_daily['season'] = total_daily['ds'].apply(compute_season)
    total_daily = _attach_regressors(total_daily)

    model = _build_prophet(holidays_df)
    try:
        model.fit(total_daily[['ds', 'y'] + REGRESSOR_COLS])
    except Exception as e:
        print(f"  · tail aggregate fit skipped ({type(e).__name__}: {e})")
        return pd.DataFrame()

    future_dates = pd.date_range(
        start=total_daily['ds'].min(),
        end=total_daily['ds'].max() + pd.Timedelta(days=horizon_days),
    )
    future = pd.DataFrame({'ds': future_dates})
    future['season'] = future['ds'].apply(compute_season)
    future = _attach_regressors(future)
    forecast = model.predict(future[['ds'] + REGRESSOR_COLS])
    forecast['yhat'] = forecast['yhat'].clip(lower=0)
    forecast = forecast[['ds', 'yhat']]
    forecast['dow'] = forecast['ds'].dt.day_name()

    # Disaggregate same as the main top-down path
    pivot_num = (
        df_tail.groupby(['name', 'dow', 'time_period'])['quantity'].sum()
        .reset_index().rename(columns={'quantity': 'product_qty'})
    )
    pivot_den = (
        df_tail.groupby(['dow', 'time_period'])['quantity'].sum()
        .reset_index().rename(columns={'quantity': 'total_qty'})
    )
    share = pivot_num.merge(pivot_den, on=['dow', 'time_period'])
    share['fraction'] = np.where(
        share['total_qty'] > 0, share['product_qty'] / share['total_qty'], 0.0,
    )

    total_qty = float(df_tail['quantity'].sum()) or 1.0
    overall_product_share = (
        df_tail.groupby('name')['quantity'].sum() / total_qty
    ).reset_index().rename(columns={'quantity': 'overall_product_share'})

    tp_totals = df_tail.groupby('time_period')['quantity'].sum()
    tp_total_sum = float(tp_totals.sum()) or 1.0
    overall_tp_share = pd.DataFrame({
        'time_period': TIME_ORDER,
        'overall_tp_share': [float(tp_totals.get(tp, 0)) / tp_total_sum for tp in TIME_ORDER],
    })

    full_grid = forecast[['ds', 'dow', 'yhat']].merge(overall_product_share, how='cross')
    full_grid = full_grid.merge(overall_tp_share, how='cross')
    full_grid = full_grid.rename(columns={'name': 'product'})
    full_grid = full_grid.merge(
        share[['name', 'dow', 'time_period', 'fraction']].rename(columns={'name': 'product'}),
        on=['product', 'dow', 'time_period'], how='left',
    )
    fallback = full_grid['overall_product_share'] * full_grid['overall_tp_share']
    full_grid['fraction'] = full_grid['fraction'].fillna(0.0)
    full_grid['fraction'] = np.where(full_grid['fraction'] > 0, full_grid['fraction'], fallback)
    full_grid['yhat_value'] = (full_grid['yhat'] * full_grid['fraction']).clip(lower=0)
    return full_grid[['ds', 'time_period', 'product', 'yhat_value']].rename(columns={'yhat_value': 'yhat'})


def run_forecast(df: pd.DataFrame, save_csv: bool = False, horizon_days: int = 30) -> pd.DataFrame:
    """
    Returns a DataFrame with columns:
        ds, yhat, y, product, type, percentage_error, time_period

    Behaviour depends on FORECAST_MODE:
      - "top_down" (default) — one Prophet on the daily total, then
        disaggregate via historical (product × dow × time_period) share.
        Fast (~1s on a laptop, 10s on free tier).
      - "per_product" — one Prophet per top-N product, long tail uses
        top-down. Slower (~30s on a laptop, minutes on free tier) but
        captures item-specific Eid/Ramadan peaks that get diluted at
        the menu-aggregate level.
    """
    np.random.seed(42)

    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df['season'] = df['date'].apply(compute_season)
    df['dow'] = df['date'].dt.day_name()

    if FORECAST_MODE == "per_product":
        return _run_per_product(df, save_csv, horizon_days)

    # ── Aggregate daily total across the whole menu ──────────────────────
    total_daily = df.groupby('date', as_index=False)['quantity'].sum()
    total_daily.columns = ['ds', 'y']
    if total_daily.empty:
        return pd.DataFrame(columns=[
            'ds', 'yhat', 'y', 'product', 'type', 'percentage_error', 'time_period'
        ])

    # Per-day is_imputed flag (True iff every row that day was imputed).
    # Used below to (a) leave imputed days in the training set with
    # reduced weight, and (b) leave imputation alone in the share
    # calculation. If the input df has no `is_imputed` column (older
    # callers, eval harness without the flag), default to all-real.
    if 'is_imputed' in df.columns:
        day_imp = df.groupby('date')['is_imputed'].agg(lambda s: bool(s.all()))
        total_daily['is_imputed'] = (
            total_daily['ds'].dt.normalize().map(day_imp.to_dict()).fillna(False).astype(bool)
        )
    else:
        total_daily['is_imputed'] = False

    # Drop early-close / partial-day outliers before training. Real
    # closure days (qty == 0) never appear in the join; what DOES make
    # it through are days where the cafe opened briefly — e.g. Sep 24,
    # 2022 had 77 orders before shutting for the National Day break.
    # Including those as "normal Saturdays" pulls the weekly Saturday
    # coefficient down by 30-40%, suppressing every future Saturday
    # forecast (and the one before/after each holiday because of the
    # closure-stretch pattern).
    #
    # Per-weekday threshold: anything below 30% of that weekday's
    # MEDIAN is treated as a partial-day outlier. Median (not mean)
    # so a single low day can't drag the threshold down with it.
    #
    # IMPORTANT: holiday days are exempt. A genuine low-demand holiday
    # (e.g. Eid al-Fitr day 1, when most of Riyadh is at home) looks
    # like an outlier by this rule but is signal, not noise. Dropping
    # it leaves the holiday regressor with no observation to fit
    # against, so the coefficient stays near its prior and Eid family
    # forecasts under-predict by ~50 %.
    total_daily['__dow'] = total_daily['ds'].dt.day_name()
    total_daily['__occ'] = total_daily['ds'].apply(compute_occasion)
    dow_median = total_daily.groupby('__dow')['y'].transform('median')
    outlier_mask = (
        (total_daily['y'] < (dow_median * 0.30))
        & (total_daily['__occ'] == 'Normal')
    )
    if outlier_mask.any():
        n = int(outlier_mask.sum())
        sample = total_daily.loc[outlier_mask, ['ds', 'y']].head(5).to_dict('records')
        print(f"[outlier] dropping {n} early-close days from training: {sample}")
        total_daily = total_daily.loc[~outlier_mask].reset_index(drop=True)
    total_daily = total_daily.drop(columns=['__dow', '__occ'])

    total_daily['season'] = total_daily['ds'].apply(compute_season)
    total_daily = _attach_regressors(total_daily)

    holidays_df = build_saudi_holidays(
        total_daily['ds'].min(),
        total_daily['ds'].max() + pd.Timedelta(days=horizon_days),
    )

    # Weight imputed days less. Imputed rows have ¼ the variance of
    # real days (std 21 vs 86 in the v4 dataset) — Prophet weighting
    # them equally pulled the trend and weekly seasonality toward a
    # flat 170-251 band, washing out genuine signal. Prophet doesn't
    # accept per-row weights directly, so we encode weights as row
    # multiplicity: real days appear 3× per fit, imputed days 1×. The
    # ratio is the report's recommended 0.3 (1/3 ≈ 0.33).
    def _weighted(daily: pd.DataFrame) -> pd.DataFrame:
        reps = np.where(daily['is_imputed'].values, 1, 3)
        return daily.loc[daily.index.repeat(reps)].reset_index(drop=True)

    fit_cols = ['ds', 'y'] + REGRESSOR_COLS

    # ── 80/20 split for MAE diagnostic only ──────────────────────────────
    split = int(len(total_daily) * 0.8)
    if split < len(total_daily):
        try:
            eval_model = _build_prophet(holidays_df)
            eval_model.fit(_weighted(total_daily.iloc[:split])[fit_cols])
            test_pred = eval_model.predict(total_daily.iloc[split:][['ds'] + REGRESSOR_COLS])
            mae = mean_absolute_error(total_daily.iloc[split:]['y'], test_pred['yhat'])
            print(f"MAE (Test) — total daily sales: {round(mae, 2)}")
        except Exception as e:
            print(f"MAE eval skipped: {e}")

    # ── Production model: fit on FULL data (weighted) ───────────────────
    model = _build_prophet(holidays_df)
    model.fit(_weighted(total_daily)[fit_cols])

    # Daily residual std — drives the 80% prediction band shown on
    # the chart. Prophet's mean prediction is intentionally smooth
    # (it captures systematic patterns; stochastic noise is excluded
    # by design); the band represents the spread of realisations
    # around the expected value. We use the actual training residuals
    # rather than Prophet's MCMC-derived intervals because:
    #   - mcmc_samples=0 (the default) makes Prophet's lower/upper a
    #     Laplace approximation that's narrow on this dataset
    #   - residual std reflects the noise the model genuinely couldn't
    #     explain on the data we trained on — directly comparable to
    #     what a manager sees when realised days swing around the line
    # We compute it on the unweighted real-day-only training subset
    # so the band reflects realistic variance, not the imputed-day
    # band's artificially small std.
    train_pred = model.predict(total_daily[['ds'] + REGRESSOR_COLS])
    real_only = total_daily[~total_daily['is_imputed']]
    if len(real_only) >= 5:
        real_pred = train_pred.iloc[real_only.index]
        residuals = real_only['y'].values - real_pred['yhat'].values
        residual_std = float(np.std(residuals))
        train_mean = float(real_only['y'].mean()) or 1.0
    else:
        residuals = total_daily['y'].values - train_pred['yhat'].values
        residual_std = float(np.std(residuals))
        train_mean = float(total_daily['y'].mean()) or 1.0

    future_dates = pd.date_range(
        start=total_daily['ds'].min(),
        end=total_daily['ds'].max() + pd.Timedelta(days=horizon_days),
    )
    future = pd.DataFrame({'ds': future_dates})
    future['season'] = future['ds'].apply(compute_season)
    future = _attach_regressors(future)
    forecast = model.predict(future[['ds'] + REGRESSOR_COLS])
    forecast['yhat'] = forecast['yhat'].clip(lower=0)
    forecast = forecast[['ds', 'yhat']]
    forecast['dow'] = forecast['ds'].dt.day_name()
    forecast['season'] = forecast['ds'].apply(compute_season)

    # No per-weekday floor. We previously lifted any prediction below
    # the 25th percentile of that weekday's history toward the floor,
    # then weakened it to p5; both squashed the day-to-day variance
    # well below reality (compression ratio ≈ 0.10 at p25 and ≈ 0.31
    # at p5 — real std ≈ 86 in the v4 dataset). Manager-facing review
    # called the chart unnaturally smooth and questioned the model's
    # honesty when the dashboard's actuals showed clear ups-and-downs.
    #
    # Trusting Prophet's natural output instead — `clip(lower=0)`
    # above keeps the trend non-negative on far-future dates, the
    # holiday-gated outlier filter (line 591) keeps genuine low days
    # in training so the model knows what "low" looks like, and
    # _bake_baseline_scale in the route layer handles level
    # adjustment without compressing variance.
    forecast['yhat'] = forecast['yhat'].clip(lower=0)

    # ── Per-product, per-(season, dow, time_period) historical share ─────
    # CRITICAL: keying the share by SEASON is what lets the disaggregation
    # shift hot drinks ↑ in winter and cold drinks ↑ in summer. Without the
    # season key, the share for each product was fixed across the whole
    # year, so a December forecast got the same product mix as an August
    # one — which is why Hot Spanish Latte (a winter staple in Saudi
    # cafés) was getting near-zero predictions in December.
    # Denominator groups by (season, dow) — NOT (season, dow, time_period)
    # — so the share represents "fraction of (season, dow) demand for
    # this (product, time_period) cell". Summing share across the joint
    # (product × time_period) axis equals 1.0; multiplying by Prophet's
    # daily yhat then disaggregating produces a daily total that matches
    # yhat instead of inflating it 4× (one per time-period bucket).
    pivot_num = (
        df.groupby(['name', 'season', 'dow', 'time_period'])['quantity']
        .sum()
        .reset_index()
        .rename(columns={'quantity': 'product_qty'})
    )
    pivot_den = (
        df.groupby(['season', 'dow'])['quantity']
        .sum()
        .reset_index()
        .rename(columns={'quantity': 'total_qty'})
    )
    share = pivot_num.merge(pivot_den, on=['season', 'dow'])
    share['fraction'] = np.where(
        share['total_qty'] > 0,
        share['product_qty'] / share['total_qty'],
        0.0,
    )

    # Fallback share for (product, dow, time_period) buckets that have
    # zero history: product's overall share × overall tp share. Built as
    # a separate frame so we can fillna against it after the main merge.
    total_qty = float(df['quantity'].sum()) or 1.0
    overall_product_share = (
        df.groupby('name')['quantity'].sum() / total_qty
    ).reset_index().rename(columns={'quantity': 'overall_product_share'})

    tp_totals = df.groupby('time_period')['quantity'].sum()
    tp_total_sum = float(tp_totals.sum()) or 1.0
    overall_tp_share = pd.DataFrame({
        'time_period': TIME_ORDER,
        'overall_tp_share': [float(tp_totals.get(tp, 0)) / tp_total_sum for tp in TIME_ORDER],
    })

    product_names = list(overall_product_share['name'])
    dows = forecast['dow'].unique()

    # ── Disaggregate the daily forecast — fully vectorized ───────────────
    # Build the Cartesian product (forecast_day × product × time_period)
    # via pandas merges instead of a Python triple-loop. On the free
    # tier this drops disaggregation from ~30-60s to a few seconds.
    full_grid = forecast[['ds', 'dow', 'season', 'yhat']].merge(
        overall_product_share, how='cross'
    )
    full_grid = full_grid.merge(overall_tp_share, how='cross')
    full_grid = full_grid.rename(columns={'name': 'product'})

    # Attach the per-(product, season, dow, time_period) share. Season
    # axis is what makes hot drinks dominate winter forecasts and cold
    # drinks dominate summer forecasts.
    full_grid = full_grid.merge(
        share[['name', 'season', 'dow', 'time_period', 'fraction']]
        .rename(columns={'name': 'product'}),
        on=['product', 'season', 'dow', 'time_period'],
        how='left',
    )

    # Where (product, dow, tp) had zero history, fall back to
    # overall_product_share × overall_tp_share. Otherwise use the
    # observed fraction.
    fallback = full_grid['overall_product_share'] * full_grid['overall_tp_share']
    full_grid['fraction'] = full_grid['fraction'].fillna(0.0)
    full_grid['fraction'] = np.where(
        full_grid['fraction'] > 0,
        full_grid['fraction'],
        fallback,
    )

    # Normalize per date so the (product × time_period) cells for any
    # given forecast day sum to exactly 1.0. The observed share now
    # sums to 1.0 within (season, dow), but mixing observed cells with
    # fallback cells on the same date can leave the per-day total off
    # by ~10-20 %. This guarantees Σ yhat_value = Prophet's daily yhat.
    day_totals = full_grid.groupby('ds')['fraction'].transform('sum')
    full_grid['fraction'] = np.where(
        day_totals > 0,
        full_grid['fraction'] / day_totals,
        full_grid['fraction'],
    )

    full_grid['yhat_value'] = (full_grid['yhat'] * full_grid['fraction']).clip(lower=0)
    pred_df = full_grid[['ds', 'time_period', 'product', 'yhat_value']].rename(
        columns={'yhat_value': 'yhat'}
    )

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

    # Attach the daily residual stats so the route layer can render
    # an 80% prediction band on the chart without re-fitting the
    # model. `attrs` survives most pandas ops; the route caches the
    # values explicitly anyway in case `attrs` gets dropped on a
    # downstream copy / merge.
    out.attrs['daily_residual_std'] = float(residual_std)
    out.attrs['daily_train_mean'] = float(train_mean)

    if save_csv:
        out.to_csv("all_products_predictions.csv", index=False)
        print(" Done  ")

    return out


if __name__ == "__main__":
    df = pd.read_excel("data/sample_sales_2022.xlsx")
    run_forecast(df, save_csv=True)
