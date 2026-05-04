# Testing Brief — Upload, Data Quality & Reports

You own the **Upload page**, the **dataset itself**, and the **Reports / Export
features**. Three connected jobs:

1. **Upload flow** — does the website ingest sales files cleanly?
2. **Data fixing** — is the seeded 2022 dataset correct? Find and patch issues
   like missing days, zero-cost items, duplicate line items.
3. **Reports / Exports** — do the per-page exports and the combined report in
   Settings produce polished, accurate documents?

You don't need to change frontend code — your work is in the data + the
backend's data loader (with the implementer's help).

## Live URL
**https://gp-restaurant-frontend.vercel.app**

Log in with any team account (password `demo1234`). Each account has its own
isolated data scope.

## Reference: the original 2022 dataset

| Property | Value |
|---|---|
| Source file | `260226_Orders_Items.xlsx` (raw POS export) |
| Total rows | 49,102 line items |
| Unique orders | 23,382 |
| Date range | 2022-01-01 → 2022-12-27 |
| Active days | 277 (so 88 days had no sales — closures or data gaps) |
| Total revenue | **920,243 SAR** |
| Total cost | covered by `unit_cost` column; 1,828 rows have cost = 0 (3.7 %) |
| Categories | 8 (Bakery, Cold Coffee Drinks, Cold Drinks, Dripp Coffee Drinks, Espresso Drinks, Hot Drinks, Hot Sweets, Sweets) |
| Distinct products | 129 |
| Best seller | Spanish latte (5,039 units, mostly hot variant) |
| Known closure stretches | Feb 20–25 (Founding Day week), May 2–4 (Eid al-Fitr), Sep 25 onwards (post-National-Day break) |

These numbers are your "ground truth." Anything that doesn't match needs a
finding written.

## Test scenarios

### Section A — Upload flow

#### A1. Fresh upload — does revenue match?
- Settings → Upload Data → drag the original `260226_Orders_Items.xlsx`
- Wait for confirmation. Open Dashboard.
- **Expected**: total revenue ≈ **920,243 SAR**, total orders ≈ **23,382**
- **Red flag**: revenue 818K (the broken cost-fixed file de-dup bug) or any other large gap

#### A2. Re-upload the same file
- Upload the same file twice in a row
- **Expected**: behavior should be one of two clean options:
  - either it detects the duplicate and refuses ("file already imported"), OR
  - it cleanly replaces the previous upload (Dashboard totals stay at 920K, not 1.84M)
- **Red flag**: revenue doubles to ~1.84M (orders accumulated instead of replaced)

#### A3. Upload an empty Excel
- Open Excel, save a blank file as `.xlsx`, upload it
- **Expected**: friendly error message ("file appears to be empty" or similar)
- **Red flag**: server crash, scary stack trace, or silently accepting it

#### A4. Upload a file with missing columns
- Take the original file, delete the `unit_cost` column, save as a new file, upload
- **Expected**: error message identifying the missing column, no partial import
- **Red flag**: imports anyway with cost = 0 everywhere

#### A5. Delete the upload
- Settings → Upload Data → find the upload in the list → Delete
- **Expected**: Dashboard, Forecasting, Menu Insights all switch back to empty state ("upload your data to begin")
- **Red flag**: Dashboard still shows old data after delete (cache not invalidated)

### Section B — Data quality / fixing

#### B1. Zero-cost rows
- Run this query on the local Postgres after seeding:
  ```sql
  SELECT COUNT(*) FROM order_items WHERE unit_cost = 0;
  ```
- **Expected from raw**: **1,828** rows
- These are real sales but the POS didn't record cost. Margin calculations on
  Menu Insights for these items will be misleading (showing 100 % margin).
- **Action**: propose plausible per-category cost defaults (e.g. Hot Drinks
  cost = 1.5 SAR, Sweets cost = 4 SAR) and write a SQL patch that updates only
  the zero rows. Don't fabricate full new transactions.

#### B2. Missing dates / closures
- Walk the calendar 2022-01-01 → 2022-12-31 and label each missing day:
  - **Closure** (cafe was deliberately closed): keep as a gap. Prophet
    handles missing dates natively.
  - **Data loss** (cafe was open but POS didn't export): may need imputation.
- **Action**: produce a CSV with three columns: `date`, `reason`
  (closure / data_loss), `notes`. Hand to the implementer.

#### B3. The "complete" file — is it real data or imputed?
- A file `final_sales_complete.xlsx` was added to the repo (78,199 rows,
  1.69 M SAR). 
- **Expected check**: ~44 % of rows are tagged `is_imputed=True` and have
  `created_at = NaT` (no timestamp). Total revenue includes ~877 K of
  synthetic data.
- **Action**: confirm that the deployed app does NOT use this file as the
  seed (it should still use `orders_2022.xlsx` from `backend/sample_data/`).
  If it does, raise a finding.

#### B4. Duplicate line items vs proper consolidation
- Look at order #61160 in the raw file. It has 8 separate rows for
  `Chocolate waffle` (each `quantity=1`). The "fixed" file collapsed these
  to 1 row, losing 7 waffles' worth of revenue (~126 SAR).
- **Expected**: any de-duplication script should use
  `groupby(['order_reference','sku',...]).agg(quantity='sum', total_price='sum')`,
  not `drop_duplicates()`. Revenue should be preserved.

### Section C — Reports & Exports

#### C1. Per-page CSV exports
- On Dashboard, click Export → CSV. Open the file in Excel.
- **Expected**: opens cleanly with sections separated by blank rows. Arabic
  product names readable (UTF-8 BOM at start). Includes KPIs, top products,
  category breakdown, daily revenue.
- Repeat on Forecasting and Menu Insights.
- **Red flag**: garbled Arabic, missing sections, totals don't match Dashboard

#### C2. Per-page PDF reports
- On any of the three pages, click Export → PDF Report
- A new tab opens with a printable HTML document
- Use browser **Print → Save as PDF**
- **Expected**: looks like a real document — cover header with project name + generated date, sectioned tables, totals rows. Doesn't look like a screenshot of the screen.
- **Red flag**: tables overflow the page, missing data, dark-mode colors leaking into PDF

#### C3. Combined report (Settings → Data & Export → Generate Report)
- Settings → Data & Export → Generate Report (PDF)
- **Expected**: one document covering Dashboard summary + Forecast (next 30 days) + Menu Insights all in one. Numbers match what those pages show individually.
- **Red flag**: forecast section shows different numbers than the Forecasting page

#### C4. CSV with active filters
- On Dashboard, set Category = "Hot Drinks" and a date range
- Click Export → CSV
- **Expected**: file's metadata header says "Category: Hot Drinks, Date range: …", and totals reflect the filter (not the full year)
- **Red flag**: filter ignored, exports the full unfiltered dataset

## Report format

For every finding, paste this block:

```
Section / scenario:    A2 — re-upload duplicate
What I did:            Uploaded original file twice
What I saw:            Dashboard revenue jumped from 920K to 1.84M
What I expected:       Either rejected as duplicate, or replaced (still 920K)
Severity:              High — corrupts every analytic
Verdict:               Backend doesn't dedupe by upload signature
```

## Out of scope for this role

- Don't tune the forecast model or menu equations — separate roles
- Don't change UI layout — your scope is data quality + ingest + exports
- Don't fabricate transactions to "fill" closure days; treat them honestly

## Where findings go

The team tracker, one row per finding. For data fixes, also attach the
proposed SQL patch or the corrected CSV directly to the row.
