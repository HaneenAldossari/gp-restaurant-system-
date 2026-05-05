"""
Prophet forecast accuracy evaluation.

Trains the production model (`run_forecast` from `backend/prophet_model.py`)
on rolling-origin chronological holdouts and measures error against
REAL-day actuals only (imputed gap-fill days are inputs but never
validation targets — they have no ground truth).

Two model outputs are evaluated:
  • RAW PROPHET DAILY  — the `_build_prophet(...)` daily total, refit on
    the same training data and regressors as `run_forecast` uses internally.
    This is the "model" measurement.
  • SERVICE OUTPUT     — `run_forecast()` followed by the same baseline
    rescaler the route layer applies (`_bake_baseline_scale`).
    This is what end users see.

The split exists because the disaggregation step in `run_forecast` was
shown to inflate the per-(product × time_period) total by ~4× before
rescaling — so any per-product or per-time-period accuracy claim has
to be made against the rescaled service output, never the raw run_forecast.
See REPORT.md for evidence.

Outputs go to `backend/eval/`:
  - eval_daily_prophet.csv      : Prophet's daily yhat vs real-day y
  - eval_predictions.csv        : raw run_forecast (date × product × tp × yhat)
  - eval_daily_service.csv      : run_forecast → baseline-rescaled daily total
  - eval_time_period.csv        : daily-renormalized per-tp predictions
  - eval_per_product.csv        : daily-renormalized per-product predictions
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("FORECAST_MODE", "top_down")

warnings.filterwarnings("ignore")
import logging  # noqa: E402
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
logging.getLogger("prophet").setLevel(logging.WARNING)

from prophet_model import (  # noqa: E402
    run_forecast,
    compute_occasion,
    compute_season,
    build_saudi_holidays,
    _build_prophet,
    _attach_regressors,
    REGRESSOR_COLS,
)

EVAL_DIR = Path(__file__).resolve().parent
SOURCE_XLSX = BACKEND_DIR / "sample_data" / "orders_2022.xlsx"


# ─────────────────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────────────────
def load_source() -> pd.DataFrame:
    df = pd.read_excel(SOURCE_XLSX, engine="openpyxl")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"])
    keep_cols = {
        "date": "date",
        "time_period": "time_period",
        "name": "name",
        "categ_EN": "category",
        "quantity": "quantity",
        "is_imputed": "is_imputed",
    }
    out = df[list(keep_cols.keys())].rename(columns=keep_cols).copy()
    out["quantity"] = pd.to_numeric(out["quantity"], errors="coerce").fillna(0).astype(int)
    out["is_imputed"] = out["is_imputed"].fillna(False).astype(bool)
    out["season"] = out["date"].apply(compute_season)
    out["occasion"] = out["date"].apply(compute_occasion)
    out["dow"] = out["date"].dt.day_name()
    return out


# ─────────────────────────────────────────────────────────────────────────
# Splits — chronological holdouts (cutoff, horizon)
# ─────────────────────────────────────────────────────────────────────────
FOLDS: list[tuple[str, str, int]] = [
    ("F1_jul", "2022-06-30", 31),
    ("F2_aug", "2022-07-31", 31),
    ("F3_sep", "2022-08-31", 30),
    ("F4_oct", "2022-09-30", 31),
    ("F5_dec", "2022-11-30", 28),
]


# ─────────────────────────────────────────────────────────────────────────
# Direct Prophet daily fit — mirrors run_forecast's daily_aggregate path
# but skips disaggregation, returning the model's natural daily output.
# This is a duplicate of the head of `run_forecast` so the eval doesn't
# depend on internal refactors.
# ─────────────────────────────────────────────────────────────────────────
def fit_prophet_daily(train_rows: pd.DataFrame, horizon: int) -> pd.DataFrame:
    df = train_rows.copy()
    df["date"] = pd.to_datetime(df["date"])
    total = df.groupby("date", as_index=False)["quantity"].sum()
    total.columns = ["ds", "y"]
    # Same outlier mask the production code uses
    total["__dow"] = total["ds"].dt.day_name()
    dow_med = total.groupby("__dow")["y"].transform("median")
    outlier_mask = total["y"] < dow_med * 0.30
    total = total.loc[~outlier_mask].drop(columns=["__dow"]).reset_index(drop=True)
    total["season"] = total["ds"].apply(compute_season)
    total = _attach_regressors(total)
    holidays_df = build_saudi_holidays(
        total["ds"].min(), total["ds"].max() + pd.Timedelta(days=horizon)
    )
    m = _build_prophet(holidays_df)
    m.fit(total[["ds", "y"] + REGRESSOR_COLS])
    future = pd.DataFrame({
        "ds": pd.date_range(total["ds"].min(), total["ds"].max() + pd.Timedelta(days=horizon))
    })
    future["season"] = future["ds"].apply(compute_season)
    future = _attach_regressors(future)
    fc = m.predict(future[["ds"] + REGRESSOR_COLS])
    fc["yhat"] = fc["yhat"].clip(lower=0)
    # Same per-weekday p25 floor the route uses
    dow_p25 = total.groupby(total["ds"].dt.day_name())["y"].quantile(0.25).to_dict()
    fc["dow"] = fc["ds"].dt.day_name()
    fc["__floor"] = fc["dow"].map(dow_p25).fillna(0.0)
    fc["yhat_floored"] = fc[["yhat", "__floor"]].max(axis=1)
    return fc[["ds", "yhat", "yhat_floored", "trend", "weekly", "holidays", "additive_terms"]]


def baseline_rescaled(daily_disag: pd.Series, train_rows: pd.DataFrame, future_window: pd.Index) -> float:
    """Mimic _bake_baseline_scale — compute the single scale factor.

    Returns the multiplier (clamped to [0.1, 20]) that the route's rescaler
    would apply to all future yhats in this fold."""
    data_days = max(1, int((train_rows["date"].max() - train_rows["date"].min()).days) + 1)
    historical_menu_per_day = float(train_rows["quantity"].sum()) / data_days
    fut = daily_disag.loc[daily_disag.index.isin(future_window)]
    if fut.empty or historical_menu_per_day <= 0:
        return 1.0
    predicted_menu_per_day = float(fut.mean())
    if predicted_menu_per_day <= 0:
        return 1.0
    scale = historical_menu_per_day / predicted_menu_per_day
    scale = max(0.1, min(scale, 20.0))
    return scale if abs(scale - 1.0) >= 0.05 else 1.0


# ─────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 72)
    print(f"Loading source: {SOURCE_XLSX}")
    rows = load_source()
    real_days = rows[~rows["is_imputed"]]["date"].nunique()
    imp_days = rows[rows["is_imputed"]]["date"].nunique()
    print(f"  rows={len(rows):,}  days={rows['date'].nunique()}  "
          f"products={rows['name'].nunique()}  real={real_days}  imputed={imp_days}")

    daily_rows: list[dict] = []
    disag_rows: list[pd.DataFrame] = []

    for name, cutoff, horizon in FOLDS:
        cutoff_ts = pd.Timestamp(cutoff)
        train_rows = rows[rows["date"] <= cutoff_ts].copy()

        print(f"\n[{name}] train≤{cutoff_ts.date()}  rows={len(train_rows):,}  "
              f"days={train_rows['date'].nunique()}  horizon={horizon}d")

        # 1) Raw Prophet daily fit — the model's native daily output
        daily_fc = fit_prophet_daily(train_rows, horizon)
        # 2) Full run_forecast — disaggregation included, used for slicing
        train_model = train_rows[["name", "date", "quantity", "season", "occasion", "time_period"]]
        preds = run_forecast(train_model, save_csv=False, horizon_days=horizon)

        # Future window
        fut_lo = cutoff_ts + pd.Timedelta(days=1)
        fut_hi = cutoff_ts + pd.Timedelta(days=horizon)

        # Daily disaggregation total (the buggy ~4× inflated figure)
        disag_daily = (
            preds[(preds["ds"] >= fut_lo) & (preds["ds"] <= fut_hi)]
            .groupby("ds")["yhat"].sum()
        )
        scale_factor = baseline_rescaled(disag_daily, train_rows, disag_daily.index)

        # Real-day actuals
        actual_daily = (
            rows[(~rows["is_imputed"]) & (rows["date"] >= fut_lo) & (rows["date"] <= fut_hi)]
            .groupby("date")["quantity"].sum()
        )
        date_imputed = (
            rows.groupby("date")["is_imputed"].first()
            .reindex(pd.date_range(fut_lo, fut_hi)).fillna(True)  # missing date = closure → treat as imputed for filtering
        )

        for d in pd.date_range(fut_lo, fut_hi):
            yhat_prophet = float(daily_fc.loc[daily_fc["ds"] == d, "yhat"].iloc[0]) if (daily_fc["ds"] == d).any() else np.nan
            yhat_floored = float(daily_fc.loc[daily_fc["ds"] == d, "yhat_floored"].iloc[0]) if (daily_fc["ds"] == d).any() else np.nan
            yhat_disag = float(disag_daily.get(d, np.nan))
            yhat_service = yhat_disag * scale_factor if not np.isnan(yhat_disag) else np.nan
            y_real = int(actual_daily.get(d, 0))
            is_imp = bool(date_imputed.get(d, True))
            daily_rows.append({
                "fold": name, "cutoff": cutoff_ts, "ds": d,
                "horizon_day": (d - cutoff_ts).days,
                "yhat_prophet": yhat_prophet,
                "yhat_prophet_floored": yhat_floored,
                "yhat_disag": yhat_disag,
                "yhat_service": yhat_service,
                "y": y_real,
                "is_imputed_day": is_imp,
                "scale_factor": scale_factor,
            })

        # Per-(date, time_period, product) — for sliced eval
        sub = preds[(preds["ds"] >= fut_lo) & (preds["ds"] <= fut_hi)].copy()
        # Renormalize per-date so sum across (p, tp) equals Prophet's daily yhat_floored.
        # This removes the systematic ~4× inflation while preserving the relative
        # share among products & time-periods that the disaggregation produces.
        date_totals = sub.groupby("ds")["yhat"].sum().rename("disag_total")
        sub = sub.merge(date_totals, on="ds")
        sub = sub.merge(
            daily_fc[["ds", "yhat_floored"]].rename(columns={"yhat_floored": "prophet_daily"}),
            on="ds", how="left",
        )
        sub["yhat_renorm"] = np.where(
            sub["disag_total"] > 0,
            sub["yhat"] / sub["disag_total"] * sub["prophet_daily"],
            0.0,
        )
        sub["fold"] = name

        # Attach actuals
        actuals = (
            rows.groupby(["date", "time_period", "name"], as_index=False)
            .agg(y_real=("quantity", "sum"), is_imputed=("is_imputed", "first"))
        )
        sub = sub.merge(
            actuals.rename(columns={"date": "ds", "name": "product"}),
            on=["ds", "time_period", "product"], how="left",
        )
        sub["y_real"] = sub["y_real"].fillna(0).astype(int)
        # Mark whether the calendar date is observed at all (real or imputed
        # date); cells where the date is fully absent come from the bug
        # earlier — treat as imputed for filtering.
        observed_dates = rows.groupby("date")["is_imputed"].first()
        sub["date_observed"] = sub["ds"].map(observed_dates).notna()
        sub["date_imputed"] = sub["ds"].map(observed_dates).fillna(True).astype(bool)
        sub["is_imputed"] = sub["is_imputed"].fillna(sub["date_imputed"]).astype(bool)
        sub["horizon_day"] = (sub["ds"] - cutoff_ts).dt.days
        disag_rows.append(sub)

    daily_df = pd.DataFrame(daily_rows)
    daily_df["dow"] = daily_df["ds"].dt.day_name()
    daily_df["occasion"] = daily_df["ds"].apply(compute_occasion)
    daily_df["season"] = daily_df["ds"].apply(compute_season)
    daily_df["month"] = daily_df["ds"].dt.to_period("M").astype(str)
    daily_df.to_csv(EVAL_DIR / "eval_daily_prophet.csv", index=False)
    print(f"\nWrote {EVAL_DIR/'eval_daily_prophet.csv'}  ({len(daily_df):,} rows)")

    disag_full = pd.concat(disag_rows, ignore_index=True)
    disag_full = disag_full.rename(columns={"yhat": "yhat_raw"})
    disag_full.to_csv(EVAL_DIR / "eval_predictions.csv", index=False)
    print(f"Wrote {EVAL_DIR/'eval_predictions.csv'}  ({len(disag_full):,} rows)")

    # Per-time-period (sum across products) — use yhat_renorm so totals
    # match Prophet's daily output.
    real_only = disag_full[~disag_full["is_imputed"] & disag_full["date_observed"]].copy()
    tp_df = (
        real_only.groupby(["fold", "ds", "time_period"], as_index=False)
        .agg(yhat=("yhat_renorm", "sum"), y=("y_real", "sum"))
    )
    tp_df.to_csv(EVAL_DIR / "eval_time_period.csv", index=False)

    prod_df = (
        real_only.groupby(["fold", "ds", "product"], as_index=False)
        .agg(yhat=("yhat_renorm", "sum"), y=("y_real", "sum"))
    )
    prod_df.to_csv(EVAL_DIR / "eval_per_product.csv", index=False)
    print(f"Wrote eval_time_period.csv ({len(tp_df):,} rows), "
          f"eval_per_product.csv ({len(prod_df):,} rows)")


if __name__ == "__main__":
    main()
