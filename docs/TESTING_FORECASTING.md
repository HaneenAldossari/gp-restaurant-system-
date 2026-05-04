# Testing Brief — Forecasting

You own the **Forecasting page**. Your job is to evaluate whether the model's
predictions are realistic against the actual 2022 dataset. You don't need to
read or change code — just use the deployed site and report findings.

## Live URL
**https://gp-restaurant-frontend.vercel.app**

Log in with any of the four team accounts (password `demo1234` for all):
`haneen@psau.sa` · `arwa@psau.sa` · `noura@psau.sa` · `norah@psau.sa`

Each account has its own copy of the same 2022 sales data (49,102 line items
covering Jan 1 → Dec 27, 2022, total revenue 920,243 SAR).

## How the model works (1 minute)

- **Prophet** trained on daily totals, then disaggregated per (product × season × weekday × time-of-day) using each item's historical share.
- **Saudi calendar** is built in: Ramadan, Eid al-Fitr (4 phases: pre / day 1 / bounce / post), Eid al-Adha (same 4 phases), Saudi National Day (Sept 23), Saudi Founding Day (Feb 22), payday week (split into late + early phases).
- **Weather** — daily max temperature is fetched from Open-Meteo and used as a regressor so summer cold-drink spikes and winter hot-drink lifts are physical.
- **Calibration** — a post-fit scaler corrects Prophet's chronic under-fit on rare-event holidays. Eid is capped at 2× scale; one-shot solar holidays (National Day, Founding Day) at 3.5×.
- First request per workspace takes ~12 seconds (training); after that, every call is < 200 ms (cached on disk).

## Reference data — what the original dataset says

Use these as your "ground truth" benchmarks. If the model deviates a lot from these, that's a finding.

| Reference point | Expected from 2022 data |
|---|---|
| Saudi National Day 2022 (Fri Sept 23) | **5,810 SAR**, 270 orders — peak day of the year |
| Day before National Day (Thu Sept 22) | 4,499 SAR — normal Thursday |
| Eid al-Adha 2022 day-by-day pattern | Day 1: **158** orders (low) → Day 2: **328** (peak) → Day 3: 252 → Day 4: 244 |
| Best-selling product | **Spanish latte** (5,039 units sold across the year) |
| Busiest weekday | **Friday** — average ~4,168 SAR vs Sunday ~1,380 SAR |
| Closure stretches | Feb 20–25 (Founding Day week), May 2–4 (Eid al-Fitr), Sep 25 onwards (post-National-Day break) |
| Average daily revenue | 2,556 SAR (across 361 active days) |

## Test scenarios

Run these in order. For each, paste the result in the team tracker.

### 1. Total forecast — National Day 2026 (Wed Sept 23)
- Click Forecasting → set period to **next 90 days** or **next 6 months**
- Find Sept 23, 2026 in the daily chart
- Read the predicted revenue
- **Expected**: between 4,900 SAR and 6,700 SAR (within ±15% of the historical 5,810)
- **Red flag**: under 3,500 SAR or over 8,000 SAR

### 2. Eid al-Adha 2026 (May 26–29)
- Forecast next 90 days
- Find each of the 4 days in the chart
- **Expected shape**: Day 1 lower than Day 2; Day 2 highest; Days 3-4 elevated above average. Roughly: ratio Day 2 : Day 1 should be ~2:1.
- **Red flag**: all 4 days flat, or Day 1 highest

### 3. Single item — Spanish Latte, 30 days
- Click Forecasting → switch scope to "Item" → pick "Spanish latte" → period 30 days
- **Expected**: Friday is the weekly peak; weekday baseline ~12-18 units/day; weekly pattern stable across the month
- **Red flag**: zero predictions, completely flat line, or Sunday > Friday

### 4. Hot Drinks category — winter vs summer
- Forecast Hot Drinks for **30 days starting Dec 1, 2026**
- Note the average daily prediction
- Forecast Hot Drinks for **30 days starting Jul 1, 2026**
- Note the average
- **Expected**: winter average noticeably higher than summer (roughly 30-50% lift)
- **Red flag**: summer ≥ winter (the season regressor isn't working)

### 5. Low-volume item
- Pick something rare (e.g. Cheddar cheese — sold only 12 times in 2022)
- Forecast 30 days
- **Expected**: small but non-zero predictions (e.g. 0–2 units/day distributed across the month, total maybe 5–10)
- **Red flag**: all zero predictions for 30 days

### 6. Ramadan 2026 (Feb 18 → Mar 19)
- Forecast covering that window
- **Expected**: daily total revenue lower than non-Ramadan baseline (Saudi cafes typically dip during fasting hours, but evening sales spike — net effect is usually a modest dip in total). Look at the "Special days" panel — should mention Ramadan.
- **Red flag**: Ramadan totals match or exceed normal weeks (model isn't picking up the holiday)

### 7. Special-days panel
- Open Forecasting → look at the "Special days in this period" card
- Check that for any window touching Eid, Ramadan, payday, National Day, or Founding Day, the panel lists them with date ranges
- **Red flag**: window contains Eid but the panel says "no special days"

## Report format

For every scenario above, paste this block in the team tracker:

```
Scenario #:           [1-7]
Date / period:        2026-09-23
Item / scope:         total
Forecast value:       4,200 SAR
Expected (from data): ~5,810 SAR (historical 2022-09-23, Friday National Day)
Gap:                  -28% (below acceptable range)
Verdict:              under-predicting national-day lift
```

Five lines. Don't paraphrase. The structured form lets the implementer go
straight to the right code — no clarifying questions needed.

## Out of scope for this role

- Don't open GitHub or read code
- Don't try to fix the model
- Don't worry about menu insights or upload — those are separate roles

## Where findings go

The team tracker (Google Sheet / Notion). One row per finding. Wait for the
implementer to mark "fixed in commit X" before re-testing the same scenario.
