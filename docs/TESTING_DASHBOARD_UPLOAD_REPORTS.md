# Testing Brief — Dashboard, Upload & Reports

You own three connected areas of the website:

1. **Dashboard** — KPIs, charts, special-days panel
2. **Upload** — does the website cleanly ingest a sales file?
3. **Reports / Exports** — per-page CSV / PDF + the combined report from Settings

Plus the basic login and sub-user permission checks that touch every page.
You don't need to change code — just use the deployed site and report
findings.

## Live URL
**https://gp-restaurant-frontend.vercel.app**

Log in with any team account (password `demo1234` for all):
`haneen@psau.sa` · `arwa@psau.sa` · `noura@psau.sa` · `norah@psau.sa`

## How each area works (3 minutes)

### Dashboard
The landing page after login. KPI cards, daily revenue chart, sales by
category (donut + bar), top products, day-of-week pattern, hour-of-day
heatmap, and the **"Sales around special days"** panel that overlays the
Saudi calendar (Ramadan, Eid, National Day, Founding Day) with lift %
vs a normal day. Filters at the top (date range + category) apply to
everything on the page.

### Upload
Settings → Upload Data accepts an Excel sales export. Backend parses the
file, inserts rows into Postgres, and invalidates the forecast cache.
Once the upload finishes, Dashboard / Forecasting / Menu Insights all
reflect the new data immediately.

### Reports / Exports
- **Per-page Export** — top-right of Dashboard, Forecasting, Menu Insights. Drops a CSV (sectioned for Excel, UTF-8 BOM so Arabic renders) or opens a printable PDF report with cover header + sectioned tables.
- **Combined Report** — Settings → Data & Export → Generate Report. Pulls Dashboard + Forecast (next 30 days) + Menu Insights into a single document.

## Reference: what the original 2022 dataset says

Use these as ground truth.

| Metric | Expected value |
|---|---|
| Total revenue | **920,243 SAR** |
| Total orders | **23,382** |
| Average order value | ~**39.36 SAR** |
| Average daily revenue | ~**2,556 SAR** |
| Best seller | **Spanish latte** (5,039 units) |
| Busiest day of week | **Friday** (~4,168 SAR avg) |
| Slowest day of week | **Sunday** |
| Peak single day | **2022-09-23** (Friday, National Day) — 5,810 SAR |
| Date range | 2022-01-01 → 2022-12-27 |
| Active days | 277 |

## Test scenarios

### Section A — Dashboard accuracy

#### A1. KPI numbers match the source
- Log in fresh, open Dashboard with no filters
- **Expected**: total revenue ≈ 920,243 SAR, orders ≈ 23,382, AOV ≈ 39 SAR
- **Red flag**: any KPI off by more than 1 %

#### A2. Best / worst / busiest values
- **Expected**: Best seller card shows "Spanish latte"; busiest day card shows "Friday"
- **Red flag**: card shows a different product or weekday

#### A3. Filter — single category
- Set filter to "Hot Drinks". Don't change date.
- **Expected**: revenue drops to that category's slice only; donut chart shows only Hot Drinks; top products are all Hot Drinks
- **Red flag**: revenue stays the same (filter ignored)

#### A4. Filter — date range
- Set start = 2022-01-01, end = 2022-01-31. Click Apply.
- **Expected**: revenue ~30 K SAR (one month's worth), daily chart shows only January
- **Red flag**: chart still shows the full year

#### A5. Filter — combined
- Set both Hot Drinks AND a date range covering Ramadan 2022 (April 2 → May 1)
- **Expected**: small revenue (one category × one month), special-days panel mentions Ramadan
- **Red flag**: special-days panel ignores Ramadan

### Section B — Special-days panel (the differentiator)

#### B1. Peak day caption
- Look at the "Sales around special days" card on the default Dashboard view (full year)
- **Expected**: peak day caption mentions a specific Saudi event (e.g. "X days before Eid al-Adha") with a description sentence
- **Red flag**: peak day shown but no occasion caption, or caption says wrong event

#### B2. Each occasion shows a sensible lift
- The card lists each Saudi occasion in 2022 with avg revenue + lift %
- **Expected**: Eid al-Adha shows positive lift (cafe was open and busy); Eid al-Fitr shows ~–100 % (cafe was closed); National Day shows strong positive lift
- **Red flag**: closure days show as positive lift, or peak holiday shows no lift

#### B3. Filter changes the panel
- Filter Dashboard to just July 2022. The panel should reflect only Eid al-Adha.
- **Expected**: only the events overlapping the active window are shown
- **Red flag**: panel always shows all events regardless of filter

### Section C — Login & sub-user permissions

#### C1. Login works for all four team accounts
- Log out, then log in as each of the four manager accounts in turn
- **Expected**: each lands on Dashboard with their own data scope
- **Red flag**: any account fails, or data looks identical across accounts in a way that suggests they're sharing a workspace by accident

#### C2. Logout
- Click logout (sidebar or profile menu)
- **Expected**: redirected to login screen, can't navigate to Dashboard via direct URL anymore
- **Red flag**: still see Dashboard after logout, or back-button shows it

#### C3. Sub-user — Viewer permission
- Log in as a manager → Settings → Team → Add a sub-user with **Viewer** permission
- Log out, log in as that sub-user
- **Expected**: sidebar shows Dashboard, Forecasting, Menu Insights ONLY. No Settings, no Upload.
- **Red flag**: sub-user can reach Settings or Upload

#### C4. Sub-user — Cashier permission
- Add a sub-user with **Cashier** permission. Log in as them.
- **Expected**: sidebar shows ONLY "Upload Data". No Dashboard, no Forecasting, no Menu Insights.
- **Red flag**: cashier sees any of the analytics tabs

#### C5. Sub-user — Full access permission
- Add a sub-user with **Full access**. Log in as them.
- **Expected**: same view as a manager EXCEPT no "Team" tab in Settings (sub-users can't manage other sub-users)
- **Red flag**: full-access sub-user can create their own sub-users

### Section D — Upload flow

#### D1. Fresh upload — does revenue match?
- Settings → Upload Data → drag the original `260226_Orders_Items.xlsx`
- Wait for confirmation. Open Dashboard.
- **Expected**: total revenue ≈ **920,243 SAR**, total orders ≈ **23,382**
- **Red flag**: very different totals (e.g. 818K, suggests data was lost during ingest)

#### D2. Re-upload the same file
- Upload the same file twice in a row
- **Expected**: behavior is one of two clean options:
  - Detected as duplicate ("file already imported"), OR
  - Replaces the previous upload (Dashboard totals stay at 920K, not 1.84M)
- **Red flag**: revenue doubles (orders accumulated instead of replaced)

#### D3. Delete the upload
- Settings → Upload Data → find the upload in the history list → Delete
- **Expected**: Dashboard, Forecasting, Menu Insights all switch back to empty state ("upload your data to begin")
- **Red flag**: Dashboard still shows old data after delete (cache not invalidated)

### Section E — Reports & Exports

#### E1. Per-page CSV from Dashboard
- Click Export (top-right of Dashboard) → CSV
- Open the file in Excel
- **Expected**: opens cleanly with sections separated by blank rows, Arabic product names readable (UTF-8). Includes KPIs, top products, category breakdown, daily revenue.
- **Red flag**: garbled Arabic, missing sections, totals don't match the Dashboard

#### E2. Per-page PDF report
- Click Export → PDF Report on any of the three pages
- A new tab opens with a printable HTML document
- Use browser **Print → Save as PDF**
- **Expected**: looks like a real document — cover header with project name and generated date, sectioned tables, totals rows. Doesn't look like a screenshot.
- **Red flag**: tables overflow the page, missing data, dark-mode colors leak into the PDF

#### E3. Combined report (Settings → Data & Export → Generate Report)
- **Expected**: one document covering Dashboard summary + Forecast (next 30 days) + Menu Insights. Numbers match what those pages show individually.
- **Red flag**: forecast section in the combined report shows different numbers than the Forecasting page

#### E4. CSV with active filters
- On Dashboard, set Category = "Hot Drinks" and a date range
- Click Export → CSV
- **Expected**: file's metadata header says "Category: Hot Drinks, Date range: …", and totals reflect the filter (not the full year)
- **Red flag**: filter ignored, exports the full unfiltered dataset

### Section F — Pre-defense sweep (run the week before the defense)

In one 30-minute session, walk the full happy path. This is what the
supervisor will probably do when they open the link.

1. Open the URL in a fresh browser tab
2. Click your name on the login screen → Sign In
3. Dashboard loads — note the load time, screenshot the KPIs
4. Click each sidebar item, screenshot each page rendered
5. Set a date filter on Dashboard (last quarter), confirm it propagates
6. Generate a forecast for next 90 days
7. Click an item in Menu Insights, slide both sliders, watch the verdict update
8. Settings → Team → confirm the four teammate accounts all exist
9. Settings → Upload Data → re-upload the dataset, confirm Dashboard refreshes
10. Settings → Data & Export → Generate Combined Report → Save as PDF
11. Open the PDF — does it look like a real report (cover, sections, totals)?
12. Logout → log in as a different teammate → confirm data switches

Each step → either ✅ pass or 📝 finding written.

## Report format

For every finding, paste this block:

```
Section / scenario:    A2 — best seller card
What I saw:            "Hot Spanish latte" (5,034 units)
What I expected:       "Spanish latte" (5,039 units, all variants combined)
Severity:              Low — minor labeling issue
Verdict:               KPI groups variants but card label uses just one
```

## Out of scope for this role

- Don't tune the forecast model — separate role
- Don't change the Boston Matrix math — separate role
- Don't fix the underlying dataset (zero-cost rows, missing days) — that's a separate piece of work happening in parallel

You're testing **what the user actually experiences on Dashboard, Upload,
and Reports**. Findings about model accuracy → tag the Forecasting tester.
Findings about menu equations → tag the Menu Insights tester.

## Where findings go

The team tracker. One row per finding with the section/scenario number so
the implementer knows exactly which test failed.
