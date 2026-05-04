# Testing Briefs — pick a role

The team is split into three testing roles. Each brief is one self-contained
page with system explanation, test scenarios, expected results, and the
report format. Pick the one that matches what you want to own.

| Role | Brief | What you test |
|---|---|---|
| 🔮 Forecasting | [TESTING_FORECASTING.md](TESTING_FORECASTING.md) | Prophet predictions vs the actual 2022 dataset |
| 🍽️ Menu Insights | [TESTING_MENU_INSIGHTS.md](TESTING_MENU_INSIGHTS.md) | Boston Matrix classification + What-If price/cost simulator (incl. elasticity equations) |
| 📊 Dashboard, Upload & Reports | [TESTING_DASHBOARD_UPLOAD_REPORTS.md](TESTING_DASHBOARD_UPLOAD_REPORTS.md) | Dashboard accuracy, login & permissions, Excel upload flow, CSV/PDF exports, combined report |

## Common ground rules

- **Live site**: https://gp-restaurant-frontend.vercel.app
- **Login**: pick your name on the login screen, password is `demo1234` for all team accounts
- **Don't open the code on GitHub** — use the deployed site
- **Don't try to fix anything** — file the finding, the implementer handles it
- **Use the structured report format** in your brief — paste each finding into the team tracker (Google Sheet / Notion). Vague findings ("looks weird") get bumped back.

## Reference data — the original 2022 dataset

| Property | Value |
|---|---|
| Total revenue | 920,243 SAR |
| Total orders | 23,382 |
| Date range | 2022-01-01 → 2022-12-27 |
| Active days | 277 |
| Best seller | Spanish latte (5,039 units) |
| Busiest day | Friday (~4,168 SAR avg) |
| Peak day | Sept 23, 2022 (Saudi National Day, 5,810 SAR) |
| Categories | 8 |
| Distinct products | 129 |
