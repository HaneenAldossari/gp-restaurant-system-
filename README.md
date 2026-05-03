# Smart Sales Analytics & Forecasting System

A graduation project at **Prince Sattam Bin Abdulaziz University** — a full-stack analytics and forecasting platform built for Saudi cafés and restaurants. Turns raw POS exports into a live dashboard, a Saudi-aware demand forecaster, a Boston-matrix menu optimiser, and an export-ready report bundle.

> **Stack:** Next.js 16 · React 19 · TypeScript · Tailwind CSS 4 · FastAPI · PostgreSQL · Prophet · JWT
> **Deployment:** Render (backend + Postgres) · Vercel (frontend)
> **Status:** Phase 3 — models trained, end-to-end pipeline live, multi-user auth enabled.

---

## Why it exists

Off-the-shelf BI tools don't understand the Saudi café calendar. A generic forecaster trained on a year of Saudi data systematically under-predicts the days that actually drive revenue — Eid pre-shopping windows, Ramadan evening bumps, Saudi National Day, payday week — and over-predicts the days the cafe is closed for the holiday itself.

This project rebuilds the forecast around the **real local pattern**, then surfaces it through an interface designed for managers (not data scientists): one-line decisions, named occasions, no MBA jargon.

---

## Highlights

**Forecasting that actually fits Saudi data**
- Top-down Prophet model on daily aggregates, disaggregated per (product × season × weekday × time-of-day) to keep hot drinks heavy in winter and cold drinks heavy in summer.
- **Hijri-aware holiday calendar** with phase-split Eid windows (`pre` / `day1` / `bounce` / `post`) — captures the real "day 1 quiet, day 2-4 peak" pattern instead of averaging it into a flat lift.
- **Open-Meteo weather** (max temperature) injected as a Prophet regressor so summer cold-drink spikes and winter hot-drink lifts are physical, not just calendar-driven.
- **Post-fit holiday calibration** corrects Prophet's structural under-fit on rare-event holidays (one occurrence in a year of training data is too sparse for the prior); per-event scaling, capped to prevent runaway lifts.
- **Outlier filter + per-weekday p25 floor** stop early-close days from contaminating weekly seasonality and prevent the forecast from collapsing on far-future or sparse-data dates.

**Menu Insights (Boston Matrix)**
- Every item classified as **Hero / Tight-margin / Hidden gem / Underperformer** based on popularity vs profit margin, with a What-If simulator for live price/cost changes.
- **Direction-aware suggestions:** *raise / lower / hold*, with explicit classification transition pills ("Moves Tight-margin → Hero"), elasticity-projected demand, and a cost-cut bonus when supplier renegotiation compounds with the price change.
- "Not recommended" warnings when the user moves the slider against the model's strategy.

**Manager-focused dashboard**
- Live KPIs, day-of-week pattern, hour-of-day heatmap, top/bottom rankings.
- **"Sales around special days"** card: detects which Saudi occasions fall in the active window, computes lift% vs a normal-day baseline, and captions the peak day with its event ("July 6 → 3 days before Eid al-Adha — the biggest commercial event of the year for cafes").

**Multi-user auth & permissions**
- JWT-based login, manager-creates-sub-users flow, sub-users inherit the parent manager's data scope.
- Three permission levels: **Viewer** (read-only analytics), **Cashier** (upload-only), **Full access** (analytics + upload).
- Permission-aware sidebar + route guards so URL-typing can't escalate access.

**Export-ready reports**
- Per-page **CSV** (RFC 4180, sectioned for Excel) and **printable PDF** (browser print → Save as PDF) on Dashboard, Forecasting, and Menu Insights.
- A **combined report** in Settings rolls up Dashboard + Forecast + Menu Insights into one branded executive document.

---

## Models

| Model | MAE | Mean error | Selected |
|------|----:|-----------:|:-------:|
| **Prophet + regressors** | **780** | **21.5%** | ✅ |
| LSTM (2-layer, 64 hidden, 14-day window) | 791 | 33.5% | – |

We trained both for comparison. Prophet won on this dataset because (a) Saudi calendar effects are explicit holidays Prophet handles natively, (b) ~360 daily samples is too thin for an LSTM to learn the lunar-calendar lifts cleanly, and (c) Prophet's coefficients are inspectable, which mattered for the manager-facing UI.

---

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│                    Frontend (Vercel)                      │
│  Next.js 16 · React 19 · TypeScript · Tailwind 4          │
│  ─────────────────────────────────────────────────────    │
│  Login · Dashboard · Forecasting · Menu Insights          │
│  Settings · Upload · Team management                      │
└────────────────────────┬──────────────────────────────────┘
                         │  JWT bearer · REST/JSON
┌────────────────────────▼──────────────────────────────────┐
│                  Backend (Render free tier)               │
│  FastAPI · SQLAlchemy · Prophet · JWT (PyJWT + bcrypt)    │
│  ─────────────────────────────────────────────────────    │
│  /api/auth   /api/team   /api/upload                      │
│  /api/dashboard   /api/forecast/*   /api/menu-engineering │
│  Saudi calendar · Open-Meteo weather · pickle cache       │
└────────────────────────┬──────────────────────────────────┘
                         │
              ┌──────────▼────────────┐
              │  PostgreSQL (Render)  │
              │  8-table schema       │
              │  ~46K order_items     │
              └───────────────────────┘
```

The backend caches per-user Prophet predictions on disk (pickled) keyed by `(model_version, last_upload_id, item_count)` — re-uploading the file invalidates the cache automatically; same data + same model returns instantly.

---

## Repository layout

```
backend/
  prophet_model.py         # Prophet + Saudi holiday calendar + outlier filter + p25 floor
  routes_forecast.py       # forecast endpoints + post-fit holiday calibration
  routes_dashboard.py      # KPIs, daily/weekday aggregates, special-days impact
  routes_menu.py           # Boston Matrix classification + What-If price simulator
  routes_auth.py           # JWT login + /me
  routes_team.py           # manager-only sub-user CRUD
  routes_upload.py         # Excel ingest → Postgres
  weather.py               # Open-Meteo client with month-day climate fallback
  schema.sql               # 8-table relational schema
src/
  app/                     # Next.js routes (dashboard, forecasting, menu-engineering, settings, upload, login)
  components/              # Sidebar, TopBar, KPICard, ThemeProvider
  lib/                     # API client, report builders, Saudi calendar helper
data_pipeline/             # Cleaning + cost-imputation scripts
render.yaml                # one-click backend + Postgres deployment
```

---

## Quick start (local)

**Prerequisites:** Node 20+, Python 3.11+, PostgreSQL 14+.

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
psql -c "CREATE DATABASE gp_restaurant"
psql gp_restaurant < schema.sql
python seed_users.py                    # creates demo accounts
export DATABASE_URL=postgresql://localhost/gp_restaurant
uvicorn main:app --reload               # http://localhost:8000

# Frontend (in another terminal)
npm install
npm run dev                             # http://localhost:3000
```

Default login: `demo@psau.sa` / `demo1234` (manager). Upload a sales Excel from Settings → Upload Data; the Dashboard, Forecasting, and Menu Insights pages go live as soon as the import finishes.

API docs auto-generated at `http://localhost:8000/docs`.

---

## Team

- Arwa Alyami
- Haneen Aldossari
- Noura Aldossari
- Norah Aljuwayr

**Supervisor:** Dr. Mubarak Albathan
**Institution:** Prince Sattam Bin Abdulaziz University, College of Computer Engineering and Sciences, BSc Computer Science.

---

## License

Academic project. Dataset and supervisor materials are not included in this repository.
