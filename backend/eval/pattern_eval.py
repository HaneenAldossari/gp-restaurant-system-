"""
Pattern fidelity vs replication risk evaluation for the Prophet forecast.

Question being answered:
  Does the forecast follow the rhythm of real sales (weekly seasonality,
  weekend lift, holiday effects, day-to-day variance) WITHOUT just
  memorising training values?

How:
  • Train on Jan 1 – May 31 2022 (the production `run_forecast` path).
  • Predict Jun 1 – Aug 30 2022 (90 days, all Summer, NOT in training).
  • Aggregate the disaggregated output by category (8 categs).
  • Compare per-category daily forecast against real-day actuals in the
    same window (imputed days are training inputs, never validation
    targets).
  • Score each category on:
      Pattern fidelity vs actuals
        – lag-1 autocorr
        – lag-7 autocorr
        – weekday-effect strength (range of weekday means / overall std)
        – Spearman rank match of weekday-of-week means
        – variance ratio (forecast std / actuals std)
        – FFT amplitude ratio at the 7-day frequency
      Replication risk
        – similarity to a naive weekday-mean baseline built from the LAST
          4 weeks of training (May 2022). If the model just outputs that
          baseline it's memorising; if it deviates with regressor-driven
          structure it's generalising.
        – measured as Mean Absolute Percentage Difference (MAPD) — high
          MAPD ⇒ model is doing real work; low MAPD (< 5–10 %) ⇒ memo
          risk.

Outputs:
  backend/eval/PATTERN_REPORT.md
  backend/eval/pattern_metrics.csv
  backend/eval/pattern_<category>.png  (forecast vs actuals overlay per cat)

Read-only against backend/. Uses prophet_model.run_forecast directly.
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
import matplotlib.pyplot as plt

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("FORECAST_MODE", "top_down")
warnings.filterwarnings("ignore")
import logging  # noqa: E402
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
logging.getLogger("prophet").setLevel(logging.WARNING)

from prophet_model import run_forecast, compute_season, compute_occasion  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
SOURCE_XLSX = BACKEND_DIR / "sample_data" / "orders_2022.xlsx"

TRAIN_END = pd.Timestamp("2022-05-31")
FUT_LO = pd.Timestamp("2022-06-01")
FUT_HI = pd.Timestamp("2022-08-30")
HORIZON = (FUT_HI - TRAIN_END).days  # 91

CATEGORIES = [
    "Espresso Drinks", "Hot Drinks", "Cold Coffee Drinks", "Sweets",
    "Hot Sweets", "Dripp Coffee Drinks", "Cold Drinks", "Bakery",
]


# ─────────────────────────────────────────────────────────────────────────
def load_data() -> pd.DataFrame:
    df = pd.read_excel(SOURCE_XLSX, engine="openpyxl")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"])
    out = df[[
        "date", "time_period", "name", "categ_EN", "quantity",
        "is_imputed",
    ]].rename(columns={"categ_EN": "category"}).copy()
    out["quantity"] = pd.to_numeric(out["quantity"], errors="coerce").fillna(0).astype(int)
    out["is_imputed"] = out["is_imputed"].fillna(False).astype(bool)
    out["season"] = out["date"].apply(compute_season)
    out["occasion"] = out["date"].apply(compute_occasion)
    return out


def train_and_forecast(rows: pd.DataFrame) -> pd.DataFrame:
    """Run the production forecast on rows ≤ TRAIN_END, return the
    full disaggregated output (ds × time_period × product × yhat × y)."""
    train = rows[rows["date"] <= TRAIN_END].copy()
    # Match what run_forecast expects
    train_in = train[[
        "name", "date", "quantity", "season", "occasion", "time_period",
        "is_imputed",
    ]]
    print(f"Training rows: {len(train_in):,}  days: {train_in['date'].nunique()}  "
          f"horizon: {HORIZON}d")
    pred = run_forecast(train_in, save_csv=False, horizon_days=HORIZON)
    return pred


# ─────────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────────
def safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=float); b = np.asarray(b, dtype=float)
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 3:
        return float("nan")
    a = a[mask]; b = b[mask]
    if np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def autocorr(x: np.ndarray, lag: int) -> float:
    x = np.asarray(x, dtype=float)
    mask = np.isfinite(x)
    if mask.sum() < lag + 3:
        return float("nan")
    x = x[mask]
    if len(x) <= lag:
        return float("nan")
    a, b = x[:-lag], x[lag:]
    if np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def weekday_strength(series: pd.Series) -> tuple[float, dict[str, float]]:
    """Range of weekday means / overall standard deviation.

    > 1.0 means weekday systematic differences exceed within-weekday noise.
    < 0.5 means the day-of-week signal is weak relative to noise.
    """
    if series.empty:
        return float("nan"), {}
    by_dow = series.groupby(series.index.day_name()).mean()
    overall_std = float(series.std()) or 1e-9
    rng = float(by_dow.max() - by_dow.min())
    return rng / overall_std, by_dow.to_dict()


def fft_7day_amp(series: pd.Series) -> float:
    """Amplitude of the FFT bin closest to the 7-day frequency,
    normalised by the series mean. Robust comparison metric."""
    x = series.values.astype(float)
    x = x[np.isfinite(x)]
    if len(x) < 14:
        return float("nan")
    x_demean = x - x.mean()
    freqs = np.fft.rfftfreq(len(x_demean), d=1.0)
    fft_vals = np.abs(np.fft.rfft(x_demean))
    target = 1.0 / 7.0
    idx = int(np.argmin(np.abs(freqs - target)))
    mean = x.mean() or 1e-9
    return float(fft_vals[idx] / (len(x) * mean))


def spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman rank correlation between two same-keyed series."""
    common = a.index.intersection(b.index)
    if len(common) < 3:
        return float("nan")
    return safe_corr(a.loc[common].rank().values, b.loc[common].rank().values)


def naive_weekday_baseline(train_real: pd.Series, future_index: pd.DatetimeIndex,
                           lookback_days: int = 28) -> pd.Series:
    """Build a naive forecast for `future_index` = the mean of the last
    `lookback_days` of training, grouped by weekday. This is the
    'memorisation' yardstick — if the model output is within ~5–10% of
    this, it's just outputting the recent weekday averages."""
    if train_real.empty:
        return pd.Series(0.0, index=future_index)
    cutoff = train_real.index.max() - pd.Timedelta(days=lookback_days - 1)
    recent = train_real.loc[train_real.index >= cutoff]
    if recent.empty:
        recent = train_real
    by_dow = recent.groupby(recent.index.day_name()).mean()
    out = pd.Series(
        [by_dow.get(d.day_name(), float(recent.mean())) for d in future_index],
        index=future_index,
    )
    return out


def mapd(a: np.ndarray, b: np.ndarray) -> float:
    """Mean absolute percentage difference, symmetric in spirit but
    anchored on `b` (the baseline). nan-safe."""
    a = np.asarray(a, dtype=float); b = np.asarray(b, dtype=float)
    mask = np.isfinite(a) & np.isfinite(b) & (b > 1e-9)
    if mask.sum() == 0:
        return float("nan")
    return float(np.mean(np.abs(a[mask] - b[mask]) / b[mask]) * 100.0)


# ─────────────────────────────────────────────────────────────────────────
def per_category_daily(pred: pd.DataFrame, rows: pd.DataFrame,
                       category: str, train_real: pd.Series,
                       actual_daily: pd.Series, future_dates: pd.DatetimeIndex,
                       ) -> dict:
    """Compute all metrics for one category. Returns a dict ready for the
    metrics table + a daily forecast/actuals series for plotting."""
    # Build product → category map from rows
    prod_to_cat = rows.groupby("name")["category"].first()
    cat_products = prod_to_cat[prod_to_cat == category].index.tolist()

    # Forecast for this category over the future window
    cat_pred = pred[
        (pred["product"].isin(cat_products))
        & (pred["ds"] >= FUT_LO)
        & (pred["ds"] <= FUT_HI)
    ].copy()
    fc_daily = cat_pred.groupby("ds")["yhat"].sum().reindex(future_dates).fillna(0.0)

    # Actual daily for this category — REAL DAYS ONLY
    cat_actual = actual_daily.reindex(future_dates)  # NaN on imputed/missing
    real_mask = cat_actual.notna()

    # Pair forecast/actual on real days only for metrics
    fc_real = fc_daily[real_mask]
    ac_real = cat_actual[real_mask]

    # Pattern fidelity
    lag1_fc = autocorr(fc_real.values, 1)
    lag1_ac = autocorr(ac_real.values, 1)
    lag7_fc = autocorr(fc_real.values, 7)
    lag7_ac = autocorr(ac_real.values, 7)

    wk_str_fc, wk_means_fc = weekday_strength(fc_real)
    wk_str_ac, wk_means_ac = weekday_strength(ac_real)

    wk_fc_s = pd.Series(wk_means_fc)
    wk_ac_s = pd.Series(wk_means_ac)
    spearman_dow = spearman(wk_fc_s, wk_ac_s)

    var_fc = float(fc_real.std() or 0.0)
    var_ac = float(ac_real.std() or 0.0)
    var_ratio = (var_fc / var_ac) if var_ac > 1e-9 else float("nan")

    fft_fc = fft_7day_amp(fc_real)
    fft_ac = fft_7day_amp(ac_real)
    fft_ratio = (fft_fc / fft_ac) if (fft_ac and np.isfinite(fft_ac)) else float("nan")

    # Replication risk — similarity to naive weekday baseline (May 2022)
    naive = naive_weekday_baseline(train_real, future_dates, lookback_days=28)
    mapd_fc_naive = mapd(fc_daily.values, naive.values)
    naive_var = float(naive.std() or 0.0)
    fc_var = float(fc_daily.std() or 0.0)
    var_over_naive = (fc_var / naive_var) if naive_var > 1e-9 else float("nan")

    # Same-weekday training-mean forecast value for THIS exact window
    # (one number per future date — closer to the "Jul training value
    # for same weekday" thought experiment in the brief).
    train_dow_mean = train_real.groupby(train_real.index.day_name()).mean()
    train_lookup = pd.Series(
        [train_dow_mean.get(d.day_name(), float(train_real.mean()))
         for d in future_dates],
        index=future_dates,
    )
    mapd_fc_trainlookup = mapd(fc_daily.values, train_lookup.values)

    # Forecast accuracy vs actuals (informational, not graded)
    mae = float(np.mean(np.abs(fc_real.values - ac_real.values))) if len(fc_real) else float("nan")
    mape = float(
        np.mean(np.abs(fc_real.values - ac_real.values) / np.maximum(ac_real.values, 1e-9))
    ) * 100 if len(fc_real) else float("nan")
    mean_actual = float(ac_real.mean()) if len(ac_real) else 0.0

    return {
        "category": category,
        "real_days": int(real_mask.sum()),
        "mean_actual": round(mean_actual, 1),
        "mean_forecast": round(float(fc_real.mean()) if len(fc_real) else 0.0, 1),
        "mae": round(mae, 1),
        "mape_%": round(mape, 1),
        # pattern fidelity
        "lag1_actual": round(lag1_ac, 3),
        "lag1_forecast": round(lag1_fc, 3),
        "lag7_actual": round(lag7_ac, 3),
        "lag7_forecast": round(lag7_fc, 3),
        "wk_strength_actual": round(wk_str_ac, 3),
        "wk_strength_forecast": round(wk_str_fc, 3),
        "spearman_dow_match": round(spearman_dow, 3),
        "var_ratio_fc_over_ac": round(var_ratio, 3),
        "fft7_actual": round(fft_ac, 4),
        "fft7_forecast": round(fft_fc, 4),
        "fft7_ratio": round(fft_ratio, 3),
        # replication risk
        "mapd_vs_naive_28d_%": round(mapd_fc_naive, 1),
        "mapd_vs_trainlookup_%": round(mapd_fc_trainlookup, 1),
        "var_ratio_fc_over_naive": round(var_over_naive, 3),
        # series cache for plotting
        "_fc_daily": fc_daily,
        "_ac_daily": cat_actual,
        "_naive": naive,
    }


def grade(row: dict) -> tuple[int, str]:
    """Combine pattern fidelity + replication risk into 1–5.

    Two axes (each weighted equally):
      A) Pattern fidelity score (0–5)
         - +1 if Spearman weekday rank match ≥ 0.6
         - +1 if variance ratio in [0.5, 1.5]   (not too smooth, not over-noisy)
         - +1 if lag-7 autocorr sign matches actuals (both positive or
                 both negative) AND magnitude within 0.2
         - +1 if weekday-strength ratio (forecast/actuals) in [0.5, 1.5]
         - +1 if FFT-7d amplitude ratio in [0.5, 1.5]
      B) Anti-replication score (0–5)
         - 5 → MAPD vs naive ≥ 30%      (model deviates substantially)
         - 4 → 20–30%
         - 3 → 12–20%
         - 2 → 6–12%
         - 1 → < 6%                       (essentially memorising)
       AND var ratio fc/naive must be ≥ 1.05 to score above 2 (otherwise
       the deviation is just a level shift, not added structure).
    Final grade = round((A + B) / 2), justification reflects which axis
    held back the score."""
    A = 0; reasonsA: list[str] = []
    s = row["spearman_dow_match"]
    if np.isfinite(s) and s >= 0.6: A += 1; reasonsA.append("dow rank ✓")
    else: reasonsA.append(f"dow rank weak (ρ={s:.2f})")

    vr = row["var_ratio_fc_over_ac"]
    if np.isfinite(vr) and 0.5 <= vr <= 1.5: A += 1; reasonsA.append("variance match ✓")
    else: reasonsA.append(f"variance off (ratio={vr:.2f})")

    l7f, l7a = row["lag7_forecast"], row["lag7_actual"]
    if np.isfinite(l7f) and np.isfinite(l7a):
        same_sign = (l7f * l7a) >= 0
        close = abs(l7f - l7a) <= 0.25
        if same_sign and close: A += 1; reasonsA.append("lag-7 ✓")
        else: reasonsA.append(f"lag-7 mismatch ({l7f:.2f} vs {l7a:.2f})")

    wsf, wsa = row["wk_strength_forecast"], row["wk_strength_actual"]
    if wsa > 1e-6 and 0.5 <= (wsf / wsa) <= 1.5:
        A += 1; reasonsA.append("weekday strength ✓")
    else:
        reasonsA.append(f"weekday strength off ({wsf:.2f} vs {wsa:.2f})")

    fr = row["fft7_ratio"]
    if np.isfinite(fr) and 0.5 <= fr <= 1.5: A += 1; reasonsA.append("FFT-7d ✓")
    else: reasonsA.append(f"FFT-7d off (ratio={fr:.2f})")

    mapd_naive = row["mapd_vs_naive_28d_%"]
    var_over = row["var_ratio_fc_over_naive"]
    if mapd_naive >= 30: B = 5
    elif mapd_naive >= 20: B = 4
    elif mapd_naive >= 12: B = 3
    elif mapd_naive >= 6: B = 2
    else: B = 1
    if np.isfinite(var_over) and var_over < 1.05 and B > 2:
        B = 2
        replication_note = "structural deviation flat — close to weekday-mean output"
    else:
        replication_note = f"MAPD-vs-naive={mapd_naive:.1f}% (var ratio {var_over:.2f})"

    grade_val = int(round((A + B) / 2))
    grade_val = max(1, min(5, grade_val))
    justification = (
        f"pattern={A}/5 [{', '.join(reasonsA)}]; "
        f"anti-replication={B}/5 [{replication_note}]"
    )
    return grade_val, justification


# ─────────────────────────────────────────────────────────────────────────
def plot_overlay(category: str, fc_daily: pd.Series, ac_daily: pd.Series,
                 naive: pd.Series, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(fc_daily.index, fc_daily.values, color="C0", lw=1.6,
            label="Forecast (Prophet, disaggregated)")
    ax.plot(naive.index, naive.values, color="grey", lw=1.0, ls="--", alpha=0.7,
            label="Naive baseline (28-day weekday mean)")
    real_mask = ac_daily.notna()
    ax.scatter(ac_daily.index[real_mask], ac_daily.values[real_mask],
               color="C3", s=22, label="Actual (real days)", zorder=5)
    ax.set_title(f"{category} — forecast vs actuals (Jun 1 – Aug 30 2022)")
    ax.set_ylabel("Daily quantity")
    ax.set_xlabel("Date")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 72)
    rows = load_data()
    print(f"Loaded {len(rows):,} rows  "
          f"({rows['date'].nunique()} dates, {rows['name'].nunique()} products, "
          f"{rows['category'].nunique()} categories)")

    pred = train_and_forecast(rows)
    print(f"Forecast rows: {len(pred):,}")

    future_dates = pd.date_range(FUT_LO, FUT_HI)

    # Pre-compute training real-day series & actuals per category
    train_rows = rows[rows["date"] <= TRAIN_END]
    actuals_in_window = rows[
        (rows["date"] >= FUT_LO) & (rows["date"] <= FUT_HI) & (~rows["is_imputed"])
    ]

    metric_rows: list[dict] = []
    series_cache: dict[str, dict] = {}

    for cat in CATEGORIES:
        # Real-day-only training daily series for this category
        tr_cat = train_rows[(train_rows["category"] == cat) & (~train_rows["is_imputed"])]
        train_real = tr_cat.groupby("date")["quantity"].sum()

        # Real-day actuals in future window
        ac_cat = actuals_in_window[actuals_in_window["category"] == cat]
        actual_daily_full = ac_cat.groupby("date")["quantity"].sum()
        # Map missing real days to NaN by reindexing
        actual_daily = actual_daily_full

        result = per_category_daily(
            pred, rows, cat, train_real, actual_daily, future_dates,
        )
        g, just = grade(result)
        result["grade"] = g
        result["justification"] = just
        series_cache[cat] = {
            "fc": result.pop("_fc_daily"),
            "ac": result.pop("_ac_daily"),
            "naive": result.pop("_naive"),
        }
        metric_rows.append(result)
        print(f"  {cat:25s}  grade={g}  {just[:90]}…")

    metrics = pd.DataFrame(metric_rows)
    metrics_csv = EVAL_DIR / "pattern_metrics.csv"
    metrics.to_csv(metrics_csv, index=False)
    print(f"\nWrote {metrics_csv}")

    # Plots
    plot_paths: dict[str, Path] = {}
    for cat in CATEGORIES:
        sl = cat.lower().replace(" ", "_")
        out = EVAL_DIR / f"pattern_{sl}.png"
        plot_overlay(cat, series_cache[cat]["fc"], series_cache[cat]["ac"],
                     series_cache[cat]["naive"], out)
        plot_paths[cat] = out
        print(f"  plot → {out.name}")

    # Combined small-multiples figure for the report header
    fig, axes = plt.subplots(4, 2, figsize=(14, 16), sharex=True)
    axes = axes.flatten()
    for ax, cat in zip(axes, CATEGORIES):
        fc = series_cache[cat]["fc"]; ac = series_cache[cat]["ac"]
        naive = series_cache[cat]["naive"]
        ax.plot(fc.index, fc.values, color="C0", lw=1.4, label="Forecast")
        ax.plot(naive.index, naive.values, color="grey", lw=0.9, ls="--",
                alpha=0.7, label="Naive 28d")
        m = ac.notna()
        ax.scatter(ac.index[m], ac.values[m], color="C3", s=14, label="Actual")
        g = next(r["grade"] for r in metric_rows if r["category"] == cat)
        ax.set_title(f"{cat}  (grade {g}/5)", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.set_ylabel("qty/day")
    axes[0].legend(loc="upper left", fontsize=8)
    fig.suptitle(
        "Forecast vs actuals vs naive baseline — Jun 1 – Aug 30 2022",
        fontsize=12,
    )
    fig.autofmt_xdate()
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    grid_path = EVAL_DIR / "pattern_grid.png"
    fig.savefig(grid_path, dpi=110)
    plt.close(fig)
    print(f"  plot → {grid_path.name}")

    # Save metric_rows + plot path index for the report writer
    import json
    (EVAL_DIR / "_pattern_metrics.json").write_text(
        json.dumps({
            "metrics": [
                {k: (v if isinstance(v, (int, float, str)) else str(v))
                 for k, v in r.items()}
                for r in metric_rows
            ],
            "plot_grid": str(grid_path.name),
            "plot_per_category": {c: p.name for c, p in plot_paths.items()},
        }, indent=2, default=str)
    )
    print("Done.")
    return metric_rows, series_cache


if __name__ == "__main__":
    main()
