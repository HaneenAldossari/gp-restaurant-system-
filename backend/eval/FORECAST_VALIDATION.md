# Forecast Validation Report

Source data: `backend/sample_data/orders_2022.xlsx` (60,368 rows, 339 days — 277 real, 62 imputed).
Model: Prophet top-down (`backend/prophet_model.py`).

**Headline:** 7/9 required checks PASS

## Summary

Rows tagged *(info)* are descriptive — they do not gate the script's exit code. Everything else is a strict PASS threshold from the brief.

| Surface | Metric | Threshold | Measured | Status |
|---|---|---|---|---|
| 1. Heatmap | Pearson r (168 cells) | ≥ 0.85 | 0.984 | PASS |
| 1. Heatmap | Peak-cell match (info) | exact | pred=Thursday 22:00 vs real=Friday 21:00 | FAIL |
| 1. Heatmap | Top-5 Jaccard | ≥ 0.6 | 1.000 | PASS |
| 2. Daily level | Forecast mean vs trailing-8wk mean | within ±20% | 26.2% | FAIL |
| 3. Best earning days | Spearman ρ | ≥ 0.7 | 0.964 | PASS |
| 3. Best earning days | Top-2 exact match (info) | match | ['Friday', 'Thursday'] vs ['Friday', 'Saturday'] | FAIL |
| 4. Top categories | Top-3 Jaccard | ≥ 0.66 | 1.000 | PASS |
| 4. Top categories | Spearman ρ | ≥ 0.6 | 0.976 | PASS |
| B. Ablation | Full beats naive-DOW | full < baseline | 119.94 vs 116.48 | FAIL |
| C. Date labels | All rows match | exact | 8/8 | PASS |

## 1. Heatmap (typical-week pattern)

- Pearson correlation across 168 (dow × hour) cells: **0.984** (threshold ≥ 0.85)
- Peak cell — predicted: `Thursday 22:00`, real: `Friday 21:00` (mismatch)
- Top-5 cell Jaccard: **1.000** (predicted top-5 = ['Friday 21:00', 'Friday 22:00', 'Thursday 21:00', 'Thursday 22:00', 'Wednesday 21:00'], real = ['Friday 21:00', 'Friday 22:00', 'Thursday 21:00', 'Thursday 22:00', 'Wednesday 21:00'])
- Difference grid PNG: `forecast_validation_heatmap_diff.png` (predicted minus real, blue = forecast under-predicts, red = forecast over-predicts).

## 2. Daily forecast level

- Trailing 8-week real-day mean (2022-11-02 → 2022-12-27): **288.1 units/day**
- Next-30-day forecast mean (2022-12-28 → 2023-01-26): **212.7 units/day** (ratio 0.74, deviation 26.2%)

Note: rolling-origin daily MAE is covered by the existing eval — see `backend/eval/REPORT.md` for the full per-fold table.

## 3. Best earning days

| Day | Predicted revenue / occurrence | Real revenue / occurrence | Pred rank | Real rank |
|---|---|---|---|---|
| Monday | 2892.96 | 2797.86 | 7 | 7 |
| Tuesday | 3238.75 | 3062.80 | 5 | 5 |
| Wednesday | 3169.44 | 2890.23 | 6 | 6 |
| Thursday | 3650.77 | 3439.93 | 2 | 3 |
| Friday | 3867.01 | 4167.93 | 1 | 1 |
| Saturday | 3616.87 | 3696.60 | 3 | 2 |
| Sunday | 3259.81 | 3139.66 | 4 | 4 |

- Spearman ρ across all 7 days: **0.964** (threshold ≥ 0.70)
- Top-2 predicted: `['Friday', 'Thursday']` vs real: `['Friday', 'Saturday']` — **mismatch**

## 4. Top categories

- Top-3 predicted: `['Espresso Drinks', 'Hot Sweets', 'Sweets']`
- Top-3 real: `['Espresso Drinks', 'Hot Sweets', 'Sweets']`
- Top-3 Jaccard: **1.000** (threshold ≥ 0.66)
- Spearman ρ across all categories: **0.976** (threshold ≥ 0.60)

| Category | Predicted revenue (next 30d) | Real revenue (last 30 real days) |
|---|---|---|
| Bakery | 2,442.46 | 3,331.00 |
| Cold Coffee Drinks | 13,070.33 | 21,293.00 |
| Cold Drinks | 6,210.94 | 7,991.00 |
| Dripp Coffee Drinks | 7,183.41 | 9,213.00 |
| Espresso Drinks | 22,412.16 | 30,365.00 |
| Hot Drinks | 15,179.54 | 19,819.00 |
| Hot Sweets | 16,901.09 | 24,835.00 |
| Sweets | 18,202.75 | 26,520.13 |

## A. Active-regressor check

| Component | Non-zero anywhere? |
|---|---|
| `trend` | YES |
| `weekly` | YES |
| `holidays` | YES |
| `temp_max` | YES |
| `season_Winter` | YES |
| `season_Spring` | YES |
| `season_Summer` | YES |
| `season_Autumn` | YES |

**Spot checks**

| Check | Expected | Observed | Status |
|---|---|---|---|
| Eid al-Fitr window holidays effect | negative | 2022-05-01=+36.62, 2022-05-02=+54.19 | FAIL |
| Payday-week dates holidays effect | positive (majority) | 2022-04-27=-72.48, 2022-04-30=-78.32, 2022-05-03=+60.42, 2022-05-27=+21.47, 2022-06-01=+29.73 | FAIL |
| temp_max contributes variance | std > 0 | std = 29.0109 | PASS |
| Weekly peak DOW | Friday | Friday | PASS |

**Component sample (10 rows on spot dates)**

| ds | trend | weekly | holidays | temp_max | season_Winter | season_Spring | season_Summer | season_Autumn |
|---|---|---|---|---|---|---|---|---|
| 2022-04-15 | 205.525 | 39.726 | -83.483 | 0.282 | 0.0 | -19.077 | 0.0 | 0.0 |
| 2022-05-01 | 205.525 | -9.797 | 36.62 | 13.602 | 0.0 | 0.0 | -37.287 | 0.0 |
| 2022-05-02 | 205.525 | -32.911 | 54.19 | 5.238 | 0.0 | 0.0 | -37.287 | 0.0 |
| 2022-05-04 | 205.525 | -17.328 | 83.158 | 19.177 | 0.0 | 0.0 | -37.287 | 0.0 |
| 2022-04-27 | 205.525 | -17.328 | -72.478 | 4.309 | 0.0 | -19.077 | 0.0 | 0.0 |
| 2022-05-03 | 205.525 | -16.078 | 60.418 | 7.407 | 0.0 | 0.0 | -37.287 | 0.0 |
| 2022-07-10 | 205.525 | -9.797 | 191.568 | 25.992 | 0.0 | 0.0 | -37.287 | 0.0 |
| 2022-09-23 | 205.525 | 39.726 | 87.71 | 17.319 | 0.0 | 0.0 | -37.287 | 0.0 |
| 2022-10-12 | 205.525 | -17.328 | 0.0 | 5.238 | 0.0 | 0.0 | 0.0 | 3.401 |
| 2022-12-23 | 205.525 | 39.726 | 0.0 | -31.932 | 51.893 | 0.0 | 0.0 | 0.0 |

## B. Ablation

Train ≤ 2022-11-30 | held-out horizon = 28 days (matches `eval_prophet.FOLDS` last fold).

| Config | MAE (units/day) | WAPE | n real days | Δ vs full |
|---|---|---|---|---|
| full | 119.94 | 39.6% | 27 | — |
| no_holidays | 122.01 | 40.3% | 27 | +1.7% |
| no_weather | 119.60 | 39.5% | 27 | -0.3% |
| no_seasons | 116.40 | 38.4% | 27 | -2.9% |
| baseline_naive_dow | 116.48 | 38.4% | 27 | -2.9% |

**Per-regressor impact (informational — flagged if removal moves MAE ≥ 5%):**

- `no_holidays`: MAE 122.01 (+1.7% vs full) — **decorative**
- `no_weather`: MAE 119.60 (-0.3% vs full) — **decorative**
- `no_seasons`: MAE 116.40 (-3.0% vs full) — **decorative**

## C. Date spot-checks

| Date | `compute_occasion` | expected ⊃ | `is_payday` | expected | OK? |
|---|---|---|---|---|---|
| 2022-07-27 | Payday | Payday | True | True | ✓ |
| 2022-05-02 | Eid al-Fitr | Eid al-Fitr | True | True | ✓ |
| 2022-04-15 | Ramadan | Ramadan | False | False | ✓ |
| 2022-09-23 | Saudi National Day | National Day | False | False | ✓ |
| 2022-02-22 | Saudi Founding Day | Founding | False | False | ✓ |
| 2022-08-15 | Normal Day | Normal | False | False | ✓ |
| 2022-07-09 | Eid al-Adha | Eid al-Adha | False | False | ✓ |
| 2022-05-30 | Post-payday spending | Post-payday | True | True | ✓ |

## Verdict

Prophet (full) does NOT beat the naive-DOW baseline (MAE 119.9 vs 116.5) — the regressor stack isn't earning its keep on this fold. Regressors whose removal does NOT move MAE by ≥5% on this fold: no_holidays, no_weather, no_seasons — they may still contribute to *interpretability* (holiday/season callouts in the UI) but they aren't earning the forecast MAE they cost. On the user-facing surfaces the picture is mixed: the heatmap correlates with real-day shape at r=0.98 (top-5 cells identical to the historical busy windows), the DOW revenue ranking matches at Spearman ρ=0.96, and the top-3 category overlap is Jaccard=1.00 — shape-of-the-week and product-mix surfaces are trustworthy. The level surface is not: the next-30-day daily mean lands 26.2% below the trailing 8-week real mean, because Prophet runs with `growth='flat'` and reverts to a single intercept (~205 units) while the trailing 8 weeks contain the December surge (~288 units). Managers reading the absolute daily numbers will see lower forecasts than recent weeks would suggest; the dashboard's `_bake_baseline_scale` step in `routes_forecast.py` exists precisely to mask this gap, so the raw model deviation here is louder than the user-visible deviation.

---

Generated by `backend/eval/forecast_validation.py`. Re-run with `cd backend && python3 eval/forecast_validation.py`. Raw metrics in `forecast_validation.json`.