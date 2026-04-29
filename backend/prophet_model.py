"""
Prophet Hybrid Forecasting Model — Noura Aldossari (revised by Haneen)

Saudi-specific calendar effects (Ramadan, Eid al-Fitr, Eid al-Adha, Saudi
National Day, Payday window) are passed via Prophet's `holidays` parameter
— the idiomatic way to model windowed point-in-time effects with their
own learnable strength. Day-of-week patterns (the Saudi Fri/Sat weekend)
are captured by Prophet's built-in `weekly_seasonality` with a strong
prior so they're not regularized away.

Why not the previous approach (one-hot is_weekend / is_ramadan
regressors on top of weekly_seasonality)? Those signals collinearly
encoded the same information as weekly_seasonality. Under L1
regularization Prophet split (or zeroed) the coefficients arbitrarily,
flattening Friday/Saturday in the forecast. Holidays + weekly_seasonality
gives each effect its own non-overlapping channel.

Public helpers (`compute_season`, `compute_occasion`, `is_payday`) are
preserved for the route layer's per-date labelling and the upload-time
enrichment.
"""
import pandas as pd
from prophet import Prophet
import numpy as np
from hijri_converter import Gregorian
from sklearn.metrics import mean_absolute_error


SEASONS = ["Winter", "Spring", "Summer", "Autumn"]
SEASON_COLS = [f"season_{s}" for s in SEASONS]

# Time-of-day kept as a single ordered integer regressor — Prophet handles
# it fine and the values have a natural order (morning < ... < night).
TIME_ORDER = ['morning', 'Afternoon', 'Evening', 'night']
TIME_MAPPING = {k: v for v, k in enumerate(TIME_ORDER)}


def is_payday(d) -> bool:
    """Saudi payday-week window. Salaries land on the 27th of each
    Gregorian month and the spending spike runs continuously from then
    through about day 5 of the following month — people don't only spend
    on payday itself, they spend over the days that follow. We treat
    days 27 → next-month-5 as one continuous "payday week" so a manager
    picking a week that contains the 27th sees the expected lift."""
    day = pd.Timestamp(d).day
    return day >= 27 or day <= 5


def compute_season(d) -> str:
    m = pd.Timestamp(d).month
    if m in (12, 1, 2):
        return "Winter"
    if m in (3, 4, 5):
        return "Spring"
    if m in (6, 7, 8):
        return "Summer"
    return "Autumn"


def compute_occasion(d) -> str:
    """Display-only label used by the API to tell the manager *why* a date
    is special. The model itself does not consume this string — it
    consumes the holidays DataFrame from `build_saudi_holidays` and
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
    """Season one-hot — kept as a regressor (rather than a holiday) because
    it spans months and we want Prophet to learn smooth seasonal shifts on
    top of any holidays that fall inside it."""
    out = df.copy()
    for s in SEASONS:
        out[f"season_{s}"] = (out["season"] == s).astype(int)
    return out


def build_saudi_holidays(start, end) -> pd.DataFrame:
    """Build a Prophet-compatible holidays DataFrame covering every Ramadan,
    Eid, National Day, and Payday window between `start` and `end`. Each
    holiday has its own learnable coefficient, and the windowed ones (e.g.
    Ramadan = ~30 days) get the same coefficient applied across the window
    rather than fighting with weekly_seasonality.
    """
    start_t = pd.Timestamp(start) - pd.Timedelta(days=35)
    end_t = pd.Timestamp(end) + pd.Timedelta(days=35)

    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()  # (holiday_name, iso_date) — dedup
    cur = start_t
    while cur <= end_t:
        # Hijri-based events
        try:
            h = Gregorian(cur.year, cur.month, cur.day).to_hijri()
            if h.month == 9 and h.day == 1:
                key = ("ramadan", cur.date().isoformat())
                if key not in seen:
                    rows.append({
                        "holiday": "ramadan",
                        "ds": cur,
                        "lower_window": 0,
                        "upper_window": 28,  # ~Ramadan length
                    })
                    seen.add(key)
            if h.month == 10 and h.day == 1:
                key = ("eid_fitr", cur.date().isoformat())
                if key not in seen:
                    rows.append({
                        "holiday": "eid_fitr",
                        "ds": cur,
                        "lower_window": 0,
                        "upper_window": 2,  # 3-day Eid
                    })
                    seen.add(key)
            if h.month == 12 and h.day == 10:
                key = ("eid_adha", cur.date().isoformat())
                if key not in seen:
                    rows.append({
                        "holiday": "eid_adha",
                        "ds": cur,
                        "lower_window": 0,
                        "upper_window": 3,  # 4-day Eid
                    })
                    seen.add(key)
        except Exception:
            pass

        # Saudi National Day — Sep 23 each year
        if cur.month == 9 and cur.day == 23:
            rows.append({
                "holiday": "saudi_national_day",
                "ds": cur,
                "lower_window": 0,
                "upper_window": 0,
            })

        # Saudi payday window — one continuous holiday from the 27th
        # (when salaries land for civil servants) through the 5th of the
        # following month (when the post-payday spending spike fully
        # plays out). Anchored on day 27 with upper_window=9 so the same
        # coefficient applies whether the date is the day of payday or
        # six days later when people are still out spending.
        if cur.day == 27:
            rows.append({
                "holiday": "payday_week",
                "ds": cur,
                "lower_window": 0,
                "upper_window": 9,  # day 27 → next month's 5th
            })

        cur += pd.Timedelta(days=1)

    if not rows:
        return pd.DataFrame(columns=["holiday", "ds", "lower_window", "upper_window"])
    return pd.DataFrame(rows)


def run_forecast(df: pd.DataFrame, save_csv: bool = False, horizon_days: int = 30) -> pd.DataFrame:
    np.random.seed(42)

    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])

    # Re-derive season locally so older uploads (which may not have set it)
    # still get a value. Occasion strings are *not* used as a regressor any
    # more — they're surfaced only as labels in the API response.
    df['season'] = df['date'].apply(compute_season)

    product_sales = df.groupby('name')['quantity'].sum().sort_values(ascending=False)
    top_product_names = product_sales.index.tolist()

    # Build the Saudi holidays calendar once for the entire training +
    # forecast window, then reuse for every product model.
    holidays_df = build_saudi_holidays(
        df['date'].min(),
        df['date'].max() + pd.Timedelta(days=horizon_days),
    )

    # Long-tail items (< 100 total units sold across the entire history)
    # don't have enough signal for Prophet to learn anything useful, and
    # fitting one model per item dominates the wall-clock time on slow
    # CPUs. Use a flat historical-average baseline for them instead — the
    # forecast is honest about what we know.
    LOW_VOLUME_THRESHOLD = 100
    low_volume_names = [
        n for n, v in product_sales.items() if int(v) < LOW_VOLUME_THRESHOLD
    ]
    main_names = [n for n in top_product_names if n not in set(low_volume_names)]
    if low_volume_names:
        print(f"Long-tail fallback: {len(low_volume_names)} products with <{LOW_VOLUME_THRESHOLD} units → flat baseline")

    all_predictions = []

    # Helper: build a deterministic flat-baseline prediction frame so the
    # downstream slicing / aggregation logic doesn't need to know whether
    # a product was Prophet-fit or fallback-projected.
    def _baseline_predictions(product_df: pd.DataFrame, product: str) -> pd.DataFrame:
        daily = product_df.groupby('date', as_index=False)['quantity'].sum()
        daily.columns = ['ds', 'y']
        if daily.empty:
            return pd.DataFrame()
        all_dates = pd.date_range(daily['ds'].min(), daily['ds'].max())
        daily = daily.set_index('ds').reindex(all_dates).fillna(0.0).rename_axis('ds').reset_index()
        avg_per_day = float(daily['y'].mean())

        future_dates = pd.date_range(
            start=daily['ds'].min(),
            end=daily['ds'].max() + pd.Timedelta(days=horizon_days),
        )

        tp_totals = product_df.groupby('time_period')['quantity'].sum()
        tp_total_sum = float(tp_totals.sum())
        tp_ratio = (
            {tp: float(tp_totals.get(tp, 0)) / tp_total_sum for tp in TIME_ORDER}
            if tp_total_sum > 0
            else {tp: 1.0 / len(TIME_ORDER) for tp in TIME_ORDER}
        )

        rows = []
        for ds in future_dates:
            for tp in TIME_ORDER:
                rows.append({
                    'ds': ds,
                    'time_period': tp,
                    'yhat': round(avg_per_day * tp_ratio[tp]),
                })
        out = pd.DataFrame(rows)
        observed = product_df.groupby(['date', 'time_period'], as_index=False)['quantity'].sum()
        observed.columns = ['ds', 'time_period', 'y']
        out = out.merge(observed, on=['ds', 'time_period'], how='left')
        out['product'] = product
        out['type'] = out['y'].apply(lambda x: 'actual' if pd.notna(x) else 'future')
        out['yhat'] = out['yhat'].astype(int)
        out['percentage_error'] = pd.Series([pd.NA] * len(out), dtype='Int64').astype(str) + '%'
        return out[['ds', 'yhat', 'y', 'product', 'type', 'percentage_error', 'time_period']]

    for product in low_volume_names:
        product_df = df[df['name'] == product]
        baseline = _baseline_predictions(product_df, product)
        if not baseline.empty:
            all_predictions.append(baseline)

    for product in main_names:
        print(f"Processing: {product}")

        product_df = df[df['name'] == product]

        # ── Aggregate to daily totals per product ────────────────────────
        # Prophet is fit on one row per date (not per time_period) so the
        # weekly_seasonality has a clean signal to learn. Mixing four
        # collinear rows per date — as the previous version did — let the
        # time_period regressor absorb most variance and pushed
        # weekly_seasonality coefficients toward zero, which is why
        # Friday/Saturday looked identical to weekdays in the forecast.
        daily = product_df.groupby('date', as_index=False)['quantity'].sum()
        daily.columns = ['ds', 'y']

        all_dates = pd.date_range(daily['ds'].min(), daily['ds'].max())
        daily = daily.set_index('ds').reindex(all_dates).fillna(0.0).rename_axis('ds').reset_index()
        daily['season'] = daily['ds'].apply(compute_season)
        daily = _add_season_columns(daily)

        if len(daily) < 14:
            print(f" Skipped {product} (data too small)")
            continue

        # Historical time-of-day distribution for this product — used to
        # split the daily forecast back into morning/afternoon/evening/night.
        # Falls back to an even split for products with no time signal.
        tp_totals = product_df.groupby('time_period')['quantity'].sum()
        tp_total_sum = float(tp_totals.sum())
        if tp_total_sum > 0:
            tp_ratio = {tp: float(tp_totals.get(tp, 0)) / tp_total_sum for tp in TIME_ORDER}
        else:
            tp_ratio = {tp: 1.0 / len(TIME_ORDER) for tp in TIME_ORDER}

        regressor_cols = SEASON_COLS

        def _build_model() -> Prophet:
            # weekly_seasonality is added manually so we can crank
            # prior_scale and fourier_order well above Prophet's defaults
            # — the Fri/Sat lift in Saudi data is the most actionable
            # pattern for a manager. changepoint_prior_scale lowered to
            # 0.01 (default 0.05) to keep the trend stable; restaurant
            # sales rarely change direction sharply, and a flexible
            # trend tends to extrapolate to negative values past the
            # data end (which then get clipped to zero, flattening the
            # forecast).
            m = Prophet(
                daily_seasonality=False,
                weekly_seasonality=False,
                yearly_seasonality=False,
                holidays=holidays_df if not holidays_df.empty else None,
                seasonality_prior_scale=25.0,
                # holidays_prior_scale governs the regularization on
                # holiday coefficients. The default (10) and even our
                # prior bump (25) shrink the payday lift down to ~6%
                # while the data shows a ~25% lift. Loosening to 100
                # lets Prophet allocate the full effect to the holiday
                # coefficient instead of regularizing it away.
                holidays_prior_scale=100.0,
                changepoint_prior_scale=0.01,
            )
            m.add_seasonality(
                name='weekly',
                period=7,
                fourier_order=10,
                prior_scale=50.0,
            )
            for col in regressor_cols:
                m.add_regressor(col)
            return m

        # ── Test MAE (80/20 split, diagnostic only) ──────────────────────
        # The split-trained model is used purely to log MAE so we can spot
        # regressions. The actual forecast is produced by a second model
        # fit on the FULL dataset; otherwise the trend extrapolated from
        # 80% of the data tends to drift away from where the data ended,
        # producing negative future predictions that get clipped to zero
        # (the bug that flattened every product to a constant value).
        split = int(len(daily) * 0.8)
        if split < len(daily):
            try:
                eval_model = _build_model()
                eval_model.fit(daily.iloc[:split][['ds', 'y'] + regressor_cols])
                test_pred = eval_model.predict(daily.iloc[split:][['ds'] + regressor_cols])
                mae_test = mean_absolute_error(daily.iloc[split:]['y'], test_pred['yhat'])
                print(f"MAE (Test) for {product}: {round(mae_test, 2)}")
            except Exception as exc:
                print(f"MAE eval skipped for {product}: {exc}")

        # ── Production model: fit on FULL data ───────────────────────────
        model = _build_model()
        model.fit(daily[['ds', 'y'] + regressor_cols])

        # ── Future window (daily) ────────────────────────────────────────
        future_dates = pd.date_range(
            start=daily['ds'].min(),
            end=daily['ds'].max() + pd.Timedelta(days=horizon_days),
        )
        future = pd.DataFrame({'ds': future_dates})
        future['season'] = future['ds'].apply(compute_season)
        future = _add_season_columns(future)

        forecast = model.predict(future[['ds'] + regressor_cols])
        forecast['yhat'] = forecast['yhat'].clip(lower=0)

        # ── Split daily yhat across time_periods using historical ratios ─
        # The downstream API still wants one row per (date, time_period),
        # so we expand each daily prediction by multiplying by the product's
        # historical time-of-day distribution.
        expanded_rows = []
        for _, row in forecast[['ds', 'yhat']].iterrows():
            for tp in TIME_ORDER:
                expanded_rows.append({
                    'ds': row['ds'],
                    'time_period': tp,
                    'yhat': float(row['yhat']) * tp_ratio[tp],
                })
        expanded = pd.DataFrame(expanded_rows)

        # Attach observed values where we have them (for the 'actual' label
        # used by the route layer to filter to future-only rows).
        observed = product_df.groupby(['date', 'time_period'], as_index=False)['quantity'].sum()
        observed.columns = ['ds', 'time_period', 'y']
        merged = expanded.merge(observed, on=['ds', 'time_period'], how='left')
        merged['product'] = product
        merged['type'] = merged['y'].apply(lambda x: 'actual' if pd.notna(x) else 'future')
        merged['yhat'] = merged['yhat'].round().astype(int)

        percentage_error = (
            abs(merged['y'] - merged['yhat']) / merged['y']
        ) * 100
        percentage_error = percentage_error.replace([np.inf, -np.inf], np.nan)
        merged['percentage_error'] = (
            percentage_error.round(0).astype('Int64').astype(str) + '%'
        )

        merged = merged.drop_duplicates(subset=['ds', 'product', 'time_period'])

        all_predictions.append(
            merged[['ds', 'yhat', 'y', 'product', 'type', 'percentage_error', 'time_period']]
        )

    predictions_df = pd.concat(all_predictions)

    predictions_df['time_period'] = pd.Categorical(
        predictions_df['time_period'],
        categories=TIME_ORDER,
        ordered=True,
    )
    predictions_df['product'] = pd.Categorical(
        predictions_df['product'],
        categories=top_product_names,
        ordered=True,
    )

    predictions_df = predictions_df.sort_values(by=['product', 'ds', 'time_period'])

    if save_csv:
        predictions_df.to_csv("all_products_predictions.csv", index=False)
        print(" Done  ")

    return predictions_df


if __name__ == "__main__":
    df = pd.read_excel("data/sample_sales_2022.xlsx")
    run_forecast(df, save_csv=True)
