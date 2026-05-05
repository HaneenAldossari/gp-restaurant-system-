"""
Compute sliced metrics and generate plots from the eval CSVs.

Inputs (from eval_prophet.py):
  - eval_daily_prophet.csv   (Prophet's direct daily output vs real-day y)
  - eval_predictions.csv     (raw run_forecast output, per (date, tp, product))
  - eval_time_period.csv     (renormalized per-tp daily totals)
  - eval_per_product.csv     (renormalized per-product daily totals)

Outputs:
  - metrics_overall.csv, metrics_dow.csv, metrics_occasion.csv,
    metrics_horizon.csv, metrics_time_period.csv, metrics_per_product.csv,
    metrics_disaggregation_inflation.csv
  - PNG plots: actual_vs_pred_<fold>.png, residuals_overall.png,
    error_by_dow.png, error_by_occasion.png, error_by_horizon.png,
    disaggregation_inflation.png, error_by_time_period.png,
    top_products_error.png
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
from prophet_model import compute_occasion, compute_season  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────
# Metric helpers
# ─────────────────────────────────────────────────────────────────────────
def metrics(y: np.ndarray, yhat: np.ndarray) -> dict:
    y = np.asarray(y, dtype=float)
    yhat = np.asarray(yhat, dtype=float)
    if y.size == 0:
        return dict(n=0, mae=np.nan, rmse=np.nan, wape=np.nan,
                    mape=np.nan, mbe=np.nan, y_mean=np.nan, yhat_mean=np.nan)
    err = yhat - y
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    abs_y = np.sum(np.abs(y))
    wape = float(np.sum(np.abs(err)) / abs_y * 100) if abs_y > 0 else np.nan
    nz = y > 0
    mape = float(np.mean(np.abs(err[nz] / y[nz])) * 100) if nz.any() else np.nan
    return dict(
        n=int(y.size),
        mae=round(mae, 2),
        rmse=round(rmse, 2),
        wape=round(wape, 1),
        mape=round(mape, 1),
        mbe=round(float(np.mean(err)), 2),
        y_mean=round(float(np.mean(y)), 1),
        yhat_mean=round(float(np.mean(yhat)), 1),
    )


def slice_metrics(df: pd.DataFrame, group_cols: list[str], yhat_col: str, y_col: str) -> pd.DataFrame:
    rows = []
    for keys, sub in df.groupby(group_cols):
        rec = dict(zip(group_cols, keys if isinstance(keys, tuple) else (keys,)))
        rec.update(metrics(sub[y_col].values, sub[yhat_col].values))
        rows.append(rec)
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────
# Load eval data
# ─────────────────────────────────────────────────────────────────────────
daily = pd.read_csv(EVAL_DIR / "eval_daily_prophet.csv", parse_dates=["ds", "cutoff"])
daily["ds"] = pd.to_datetime(daily["ds"])
# Filter to REAL DAYS ONLY for accuracy claims
daily_real = daily[~daily["is_imputed_day"]].copy()
# Drop fully-closed/missing dates that fell into the eval window
daily_real = daily_real[daily_real["y"] > 0].copy()
daily_real["dow"] = daily_real["ds"].dt.day_name()
daily_real["occasion"] = daily_real["ds"].apply(compute_occasion)
daily_real["season"] = daily_real["ds"].apply(compute_season)
daily_real["month"] = daily_real["ds"].dt.to_period("M").astype(str)
daily_real["horizon_bucket"] = pd.cut(
    daily_real["horizon_day"],
    bins=[0, 7, 14, 21, 31],
    labels=["1-7d", "8-14d", "15-21d", "22-31d"],
    right=True,
)
# Map a coarse occasion family
def _occ_family(o: str) -> str:
    if o in ("Eid al-Fitr", "Eid al-Adha"):  return "Eid"
    if o in ("Saudi National Day", "Saudi Founding Day"): return "Solar holiday"
    if o == "Ramadan": return "Ramadan"
    if o == "Payday": return "Payday anchor"
    if o == "Post-payday spending": return "Post-payday"
    if o == "Weekend": return "Weekend (Fri/Sat)"
    return "Normal"
daily_real["occ_family"] = daily_real["occasion"].apply(_occ_family)


# ─────────────────────────────────────────────────────────────────────────
# 1. Overall — Prophet daily vs. service output
# ─────────────────────────────────────────────────────────────────────────
overall = pd.DataFrame([
    {"model": "Prophet daily (raw)",      **metrics(daily_real["y"].values, daily_real["yhat_prophet"].values)},
    {"model": "Prophet daily (floored)",  **metrics(daily_real["y"].values, daily_real["yhat_prophet_floored"].values)},
    {"model": "Disaggregation (raw)",     **metrics(daily_real["y"].values, daily_real["yhat_disag"].values)},
    {"model": "Service (rescaled)",       **metrics(daily_real["y"].values, daily_real["yhat_service"].values)},
    {"model": "Naive: training mean",     **metrics(
        daily_real["y"].values,
        np.full(len(daily_real), daily_real["y"].mean()),
    )},
    {"model": "Naive: same DOW mean",     **metrics(
        daily_real["y"].values,
        daily_real.groupby("dow")["y"].transform("mean").values,
    )},
])
overall.to_csv(EVAL_DIR / "metrics_overall.csv", index=False)
print("\n=== Overall daily-total accuracy (REAL days only) ===")
print(overall.to_string(index=False))


# Per-fold for the headline (Prophet daily floored)
per_fold = slice_metrics(daily_real, ["fold"], "yhat_prophet_floored", "y")
per_fold.to_csv(EVAL_DIR / "metrics_per_fold.csv", index=False)
print("\n=== Per-fold (Prophet floored) ===")
print(per_fold.to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────
# 2. Sliced metrics on Prophet (floored) daily output
# ─────────────────────────────────────────────────────────────────────────
m_dow = slice_metrics(daily_real, ["dow"], "yhat_prophet_floored", "y")
m_dow["dow_order"] = m_dow["dow"].map({"Monday": 0, "Tuesday": 1, "Wednesday": 2,
                                        "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6})
m_dow = m_dow.sort_values("dow_order").drop(columns="dow_order")
m_dow.to_csv(EVAL_DIR / "metrics_dow.csv", index=False)
print("\n=== Error by day-of-week ===")
print(m_dow.to_string(index=False))

m_occ = slice_metrics(daily_real, ["occ_family"], "yhat_prophet_floored", "y").sort_values("wape", ascending=False)
m_occ.to_csv(EVAL_DIR / "metrics_occasion.csv", index=False)
print("\n=== Error by occasion family ===")
print(m_occ.to_string(index=False))

m_horizon = slice_metrics(daily_real, ["horizon_bucket"], "yhat_prophet_floored", "y")
m_horizon.to_csv(EVAL_DIR / "metrics_horizon.csv", index=False)
print("\n=== Error by horizon-distance ===")
print(m_horizon.to_string(index=False))

m_month = slice_metrics(daily_real, ["month"], "yhat_prophet_floored", "y")
m_month.to_csv(EVAL_DIR / "metrics_month.csv", index=False)
print("\n=== Error by month ===")
print(m_month.to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────
# 3. Per-time-period (using renormalized service output that matches
#    Prophet's daily totals — see eval_prophet.py for the rationale)
# ─────────────────────────────────────────────────────────────────────────
tp = pd.read_csv(EVAL_DIR / "eval_time_period.csv", parse_dates=["ds"])
m_tp = slice_metrics(tp, ["time_period"], "yhat", "y")
m_tp.to_csv(EVAL_DIR / "metrics_time_period.csv", index=False)
print("\n=== Error by time_period (renormalized) ===")
print(m_tp.to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────
# 4. Top products
# ─────────────────────────────────────────────────────────────────────────
prod = pd.read_csv(EVAL_DIR / "eval_per_product.csv", parse_dates=["ds"])
top_products = (
    prod.groupby("product")["y"].sum().sort_values(ascending=False).head(15).index.tolist()
)
m_prod = slice_metrics(prod[prod["product"].isin(top_products)], ["product"], "yhat", "y")
m_prod = m_prod.sort_values("y_mean", ascending=False)
m_prod.to_csv(EVAL_DIR / "metrics_per_product.csv", index=False)
print("\n=== Per-product (top-15 by volume) ===")
print(m_prod.to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────
# 5. Disaggregation inflation diagnostic
# ─────────────────────────────────────────────────────────────────────────
infl = daily_real.copy()
infl["inflation_factor"] = infl["yhat_disag"] / infl["yhat_prophet_floored"]
infl_summary = (
    infl.groupby("fold")["inflation_factor"]
    .agg(["mean", "median", "min", "max"])
    .round(2)
    .reset_index()
)
infl_summary.to_csv(EVAL_DIR / "metrics_disaggregation_inflation.csv", index=False)
print("\n=== Disaggregation inflation (yhat_disag / yhat_prophet_floored) ===")
print(infl_summary.to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────
# Plots
# ─────────────────────────────────────────────────────────────────────────
plt.rcParams.update({"figure.dpi": 100, "savefig.dpi": 110,
                     "axes.grid": True, "grid.alpha": 0.3})


def save_plot(fig, fname):
    fig.tight_layout()
    fig.savefig(EVAL_DIR / fname)
    plt.close(fig)
    print(f"  wrote {fname}")


# Plot: actual vs predicted timeline per fold
fig, axes = plt.subplots(len(daily_real["fold"].unique()), 1,
                         figsize=(11, 2.6 * len(daily_real["fold"].unique())),
                         sharex=False)
fold_names = sorted(daily_real["fold"].unique())
if len(fold_names) == 1:
    axes = [axes]
for ax, fold in zip(axes, fold_names):
    sub = daily_real[daily_real["fold"] == fold].sort_values("ds")
    ax.plot(sub["ds"], sub["y"], "o-", label="Actual (real)", color="#1f6f3f", linewidth=1.6)
    ax.plot(sub["ds"], sub["yhat_prophet_floored"], "s--", label="Prophet daily",
            color="#1f4ea0", linewidth=1.4, alpha=0.85)
    ax.plot(sub["ds"], sub["yhat_service"], "^:", label="Service (rescaled)",
            color="#a04020", linewidth=1.2, alpha=0.85)
    ax.set_title(f"{fold}  (cutoff {sub['cutoff'].iloc[0].date()})")
    ax.set_ylabel("Daily units")
    ax.legend(loc="upper left", fontsize=8)
    ax.tick_params(axis="x", rotation=30)
save_plot(fig, "actual_vs_pred_all_folds.png")


# Plot: residual histogram
fig, ax = plt.subplots(figsize=(8, 4.5))
res = daily_real["yhat_prophet_floored"] - daily_real["y"]
ax.hist(res, bins=30, color="#3a6dab", edgecolor="white")
ax.axvline(0, color="black", linewidth=1, linestyle="--")
ax.axvline(res.mean(), color="red", linewidth=1.4, linestyle="-",
           label=f"mean residual = {res.mean():.1f}")
ax.set_xlabel("yhat - y  (positive = over-prediction)")
ax.set_ylabel("Frequency")
ax.set_title("Residual distribution: Prophet daily forecast vs real-day actuals")
ax.legend()
save_plot(fig, "residuals_overall.png")


# Plot: error by day-of-week
fig, ax = plt.subplots(figsize=(9, 4.5))
order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
m_dow_plot = m_dow.set_index("dow").reindex(order)
xs = np.arange(len(order))
w = 0.35
ax.bar(xs - w / 2, m_dow_plot["mae"], w, label="MAE (units)", color="#3a6dab")
ax.bar(xs + w / 2, m_dow_plot["mbe"], w, label="Bias (signed)", color="#aa5f3a")
ax.set_xticks(xs)
ax.set_xticklabels(order, rotation=20)
ax.set_ylabel("Units")
ax.set_title("Daily forecast error by day-of-week (Prophet, real days only)")
ax.axhline(0, color="black", linewidth=0.7)
ax.legend()
save_plot(fig, "error_by_dow.png")


# Plot: error by occasion family
fig, ax = plt.subplots(figsize=(9, 4.5))
m_occ_plot = m_occ.set_index("occ_family").sort_values("wape", ascending=False)
xs = np.arange(len(m_occ_plot))
ax.bar(xs, m_occ_plot["wape"], color="#7a4f99")
for i, (mae, n) in enumerate(zip(m_occ_plot["mae"], m_occ_plot["n"])):
    ax.text(i, m_occ_plot["wape"].iloc[i] + 0.5, f"MAE {mae}\nn={n}",
            ha="center", va="bottom", fontsize=8)
ax.set_xticks(xs)
ax.set_xticklabels(m_occ_plot.index, rotation=20)
ax.set_ylabel("WAPE (%)")
ax.set_title("Daily forecast error by occasion family (Prophet, real days only)")
save_plot(fig, "error_by_occasion.png")


# Plot: error by horizon
fig, ax = plt.subplots(figsize=(8, 4.5))
xs = np.arange(len(m_horizon))
ax.bar(xs, m_horizon["wape"], color="#3a8b6d")
for i, mae in enumerate(m_horizon["mae"]):
    ax.text(i, m_horizon["wape"].iloc[i] + 0.5, f"MAE {mae}",
            ha="center", va="bottom", fontsize=9)
ax.set_xticks(xs)
ax.set_xticklabels(m_horizon["horizon_bucket"].astype(str))
ax.set_ylabel("WAPE (%)")
ax.set_title("Daily forecast error by horizon distance from cutoff")
save_plot(fig, "error_by_horizon.png")


# Plot: disaggregation inflation
fig, ax = plt.subplots(figsize=(8, 4.5))
ax.boxplot([infl[infl["fold"] == f]["inflation_factor"] for f in fold_names],
           labels=fold_names)
ax.axhline(1.0, color="green", linewidth=1.3, linestyle="--", label="ideal = 1.0")
ax.set_ylabel("yhat_disag / yhat_prophet")
ax.set_title("Disaggregation inflation factor per fold\n"
             "(yhat from sum-over-(product, time_period) divided by Prophet's daily yhat)")
ax.legend()
save_plot(fig, "disaggregation_inflation.png")


# Plot: error by time_period
fig, ax = plt.subplots(figsize=(7, 4.5))
xs = np.arange(len(m_tp))
ax.bar(xs - 0.18, m_tp["mae"], 0.36, label="MAE", color="#3a6dab")
ax.bar(xs + 0.18, m_tp["mbe"], 0.36, label="Bias", color="#aa5f3a")
ax.set_xticks(xs)
ax.set_xticklabels(m_tp["time_period"])
ax.set_ylabel("Units")
ax.set_title("Per-time-period error (renormalized service output)")
ax.axhline(0, color="black", linewidth=0.7)
ax.legend()
save_plot(fig, "error_by_time_period.png")


# Plot: top-product MAE/WAPE
fig, ax = plt.subplots(figsize=(11, 5.5))
xs = np.arange(len(m_prod))
ax.bar(xs, m_prod["wape"], color="#5e7a99")
for i, (mae, ymean) in enumerate(zip(m_prod["mae"], m_prod["y_mean"])):
    ax.text(i, m_prod["wape"].iloc[i] + 1, f"MAE {mae}\ny={ymean}",
            ha="center", va="bottom", fontsize=7.5)
ax.set_xticks(xs)
ax.set_xticklabels(m_prod["product"], rotation=35, ha="right", fontsize=8)
ax.set_ylabel("WAPE (%)")
ax.set_title("Top-15 products by volume — daily WAPE (renormalized service output)")
save_plot(fig, "top_products_error.png")


# ─────────────────────────────────────────────────────────────────────────
# 6. Worst-day diagnostic: top 10 over-predictions and 10 under-predictions
# ─────────────────────────────────────────────────────────────────────────
diag = daily_real.copy()
diag["err"] = diag["yhat_prophet_floored"] - diag["y"]
diag["abs_err"] = diag["err"].abs()
worst_over = diag.sort_values("err", ascending=False).head(10)
worst_under = diag.sort_values("err", ascending=True).head(10)
worst_over[["fold", "ds", "dow", "occasion", "y", "yhat_prophet_floored", "err"]].to_csv(
    EVAL_DIR / "worst_over_predictions.csv", index=False
)
worst_under[["fold", "ds", "dow", "occasion", "y", "yhat_prophet_floored", "err"]].to_csv(
    EVAL_DIR / "worst_under_predictions.csv", index=False
)
print("\n=== Top 10 over-predictions ===")
print(worst_over[["fold", "ds", "dow", "occasion", "y", "yhat_prophet_floored", "err"]].to_string(index=False))
print("\n=== Top 10 under-predictions ===")
print(worst_under[["fold", "ds", "dow", "occasion", "y", "yhat_prophet_floored", "err"]].to_string(index=False))

print("\nDone. All metrics + plots written to", EVAL_DIR)
