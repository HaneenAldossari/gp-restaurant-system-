# Testing Brief — Dashboard & Overall Website QA

You own the **Dashboard page** and the cross-cutting quality checks across the
whole website (login, permissions, mobile, edge cases, the final pre-defense
sweep). Your job is the user's overall experience — does the site feel solid
the first time someone (especially the supervisor) opens it?

You don't need to change code — just use the deployed site and report
findings.

## Live URL
**https://gp-restaurant-frontend.vercel.app**

Log in with any team account (password `demo1234` for all):
`haneen@psau.sa` · `arwa@psau.sa` · `noura@psau.sa` · `norah@psau.sa`

Each manager has their own data scope, plus a shared `demo@psau.sa` workspace.

## How the Dashboard works (1 minute)

The Dashboard is the landing page after login. It shows:

- **Headline KPIs** — total revenue, total orders, average order value, average daily revenue, best/worst seller, busiest day of week, 30-day vs previous-30-day deltas
- **Daily revenue chart** — area chart of revenue over time
- **"Sales around special days" panel** — Saudi calendar overlay (Ramadan, Eid, National Day, Founding Day) with lift % vs normal day, peak day caption ("July 6 → 3 days before Eid al-Adha")
- **Sales by category** — donut (units sold) + bar chart (revenue)
- **Top products** — by revenue and by quantity
- **Day-of-week pattern** — orders + revenue per day
- **Hour-of-day heatmap** — when customers buy (if time data is in the upload)
- **Filters** — date range + category, applied to everything on the page
- **Per-page export** — Export button (top-right) → CSV or printable PDF report

## Reference: what the original 2022 dataset says

Use these as ground truth. If Dashboard shows different numbers, it's a finding.

| Metric | Expected value |
|---|---|
| Total revenue | **920,243 SAR** |
| Total orders | **23,382** |
| Average order value | ~**39.36 SAR** |
| Average daily revenue | ~**2,556 SAR** |
| Best seller | **Spanish latte** (5,039 units) |
| Worst seller | items with 1-2 sales (e.g. Morning Waffle = 1) |
| Busiest day of week | **Friday** (~4,168 SAR average) |
| Slowest day of week | **Sunday** |
| Peak single day | **2022-09-23** (Friday, National Day) — 5,810 SAR |
| Slowest single day | many ~$0 closure days (Feb 20–25, May 2–4, Sep 25 onwards) |

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
- **Expected**: "Top day: Fri Jul 8" or similar pre-Eid date — caption mentions "X days before Eid al-Adha" with a description sentence
- **Red flag**: peak day shown but no occasion caption, or caption says wrong event

#### B2. Each occasion shows the correct lift
- The card lists each Saudi occasion in 2022 with avg revenue + lift %
- **Expected**: Eid al-Adha shows positive lift (cafe was open and busy); Eid al-Fitr shows ~–100 % (cafe was closed); National Day shows strong positive lift
- **Red flag**: closure days show as positive lift (data quality bug)

#### B3. Filter changes the panel
- Filter to just July 2022. The panel should reflect only Eid al-Adha.
- **Expected**: only the events overlapping the window are shown
- **Red flag**: panel always shows all events regardless of filter

### Section C — Auth & permissions

#### C1. Login persistence
- Log in. Close the browser tab. Reopen the URL.
- **Expected**: still logged in (token in localStorage)
- **Red flag**: bounced back to login

#### C2. Logout
- Click logout (sidebar or profile menu)
- **Expected**: redirected to login screen, can't navigate to Dashboard via direct URL anymore
- **Red flag**: still see Dashboard after logout, or back-button shows it

#### C3. Sub-user — Viewer permission
- Log in as a manager → Settings → Team → Add a sub-user with **Viewer** permission
- Log out, log in as that sub-user
- **Expected**: sidebar shows Dashboard, Forecasting, Menu Insights ONLY. No Settings, no Upload. Typing `/settings` in the URL bounces back to Dashboard.
- **Red flag**: sub-user can reach Settings or Upload

#### C4. Sub-user — Cashier permission
- Add a sub-user with **Cashier** permission. Log in as them.
- **Expected**: sidebar shows ONLY "Upload Data". No Dashboard, no Forecasting, no Menu Insights, no Settings.
- **Red flag**: cashier sees any of the analytics tabs

#### C5. Sub-user — Full access permission
- Add a sub-user with **Full access**. Log in as them.
- **Expected**: same view as a manager EXCEPT no "Team" tab in Settings (sub-users can't manage other sub-users)
- **Red flag**: full-access sub-user can create their own sub-users

#### C6. Wrong password
- Try logging in with a wrong password
- **Expected**: friendly error "Invalid email or password"
- **Red flag**: the error reveals whether the email exists ("user not found" vs "wrong password" — both should say the same thing)

### Section D — Edge cases

#### D1. Empty workspace
- Create a brand-new manager (or use a fresh demo account that's never had an upload)
- **Expected**: Dashboard, Forecasting, Menu Insights all show a friendly empty state ("Upload your sales file to begin")
- **Red flag**: red error box "Couldn't load dashboard" or stack trace

#### D2. Token expiry simulation
- Log in. Open browser DevTools → Application → Local Storage → delete the auth token
- Click any sidebar item
- **Expected**: bounced to login cleanly
- **Red flag**: blank screen, infinite spinner, or scary error

#### D3. Slow network
- DevTools → Network tab → throttle to "Slow 3G"
- Reload Dashboard
- **Expected**: loading spinners shown for each section while data fetches; eventually loads
- **Red flag**: no spinner, just blank cards forever

### Section E — Mobile / responsive

Open the deployed URL on:
- iPhone Safari (or Chrome DevTools iPhone emulation)
- Android Chrome (or DevTools Pixel emulation)
- iPad / tablet width

For each:

- **E1. Login screen** — fits the viewport, inputs aren't cut off
- **E2. Sidebar** — collapses to a hamburger menu, opens with tap
- **E3. Dashboard charts** — Recharts should resize; no horizontal scrolling
- **E4. Forecasting / Menu Insights** — sliders and tables remain usable
- **E5. Tap targets** — buttons are at least 44 × 44 pixels (Apple HIG minimum)

### Section F — Concurrent multi-user

This is a one-off team test, scheduled.

- All four teammates log in at the same time, each on their own laptop
- Everyone clicks Forecasting → Generate Forecast in the same minute
- Then everyone clicks Dashboard → applies different filters
- Then everyone exports a CSV
- **Expected**: no 502s, no "Failed to fetch" errors, all pages load in under 10s for everyone
- **Red flag**: any teammate sees errors during the test → immediate finding

### Section G — Final pre-defense sweep (run the week before)

In one 30-minute session, walk the full happy path:

1. Open the URL on a fresh incognito window
2. Land on login → click "Haneen" → Sign In
3. Dashboard loads — note the load time, screenshot KPIs
4. Click each sidebar item, screenshot each page rendered
5. Set a date filter (last quarter), confirm it propagates
6. Generate Forecast for next 90 days
7. Click an item in Menu Insights, slide both sliders
8. Settings → Team → verify the four teammate accounts all exist
9. Settings → Data & Export → Generate Combined Report (PDF)
10. Save the PDF, confirm it looks like a real document
11. Logout → log in as a different account → confirm data switches correctly
12. Open DevTools Console → no red errors → screenshot
13. DevTools Network → no 4xx/5xx → screenshot
14. Open the URL on a phone → confirm sidebar collapses

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

- Don't tune the forecast model — that's a separate role
- Don't change the Boston Matrix math — separate role
- Don't fix data quality issues at the SQL level — separate role

You're testing **what the user actually experiences**, not the underlying
math. Findings about model accuracy → tag the Forecasting tester. Findings
about menu equations → tag the Menu Insights tester. Findings about file
upload → tag the Upload tester. You stay broad.

## Where findings go

The team tracker. One row per finding with the section/scenario number so
the implementer knows exactly which test failed.
