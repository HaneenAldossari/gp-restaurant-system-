# Modifications — 2026-04-14

Branch: `backend-v2` (not yet merged to `main`)

## Summary
Consolidated three separate backend fragments (`apii/main.py`, `apii/db_import/`,
`backend/`) into one unified FastAPI app under `/backend/`. All APIs now read
from and write to the PostgreSQL 8-table schema.

## What changed, by teammate

### Noura's model (`backend/prophet_model.py`)
- **Unchanged** as a standalone reference script.
- **Called by** `backend/routes_forecast.py` which contains an adapted copy of
  her `hybrid_forecast()` function. Only modification: reads from PostgreSQL
  instead of Excel. All Prophet params, 80/20 split, MAE calculation, and
  category-distribution logic for low-volume items are preserved verbatim.

### Arwa's upload (`apii/main.py`, `apii/db_import/import_csv_to_db.py`)
- `apii/` folder **deleted**.
- Her upload logic was ported into `backend/routes_upload.py` (same smart
  column detection, same DB-write pattern).
- Her `/analytics/*` endpoints were **removed** because they duplicate our
  Dashboard and Menu Engineering APIs.

### Schema (`backend/schema.sql`)
- Added sub-user support: `parent_id`, `permission` columns; `sub_user` role.
- Pushed to `main` on 2026-04-14 (commit `4d00784`).

### Files deleted
- `apii/` folder (all contents)
- `backend/data_loader.py` (replaced by `backend/data_loader_db.py`)
- `backend/utils/` (regressor-enrichment code no longer needed)
- Stale: `backend/Sales_2025.xlsx`, `backend/hybrid_forecast.csv`,
  `backend/final_sales_with_season_and_occasion_2022_.xlsx`, root `main.py`,
  root `Sales_2025.xlsx`

### Files added
| File | Purpose |
|---|---|
| `backend/db.py` | SQLAlchemy engine helper |
| `backend/data_loader_db.py` | SQL → DataFrame loader (shared by all routers) |
| `backend/routes_upload.py` | POST /api/upload (DB-backed) |
| `backend/routes_forecast.py` | GET /api/forecast/* (Noura's model on DB data) |
| `backend/README.md` | Setup + endpoint reference |
| `backend/requirements.txt` | Updated: adds sqlalchemy, psycopg2, prophet, etc. |
| `data/sample_sales_2022.xlsx` | 2022 sample data (moved from `apii/db_import/`) |
| `src/app/*/page.tsx` | Initial frontend pages (mock data) |
| `src/components/`, `src/lib/` | Shared components and API client |

## Backend instructions
See **`backend/README.md`**. Short version:

```bash
cd backend
pip install -r requirements.txt
createdb gp_restaurant
psql gp_restaurant < schema.sql
psql gp_restaurant -c "INSERT INTO users (name, email, password_hash) VALUES ('Demo Manager', 'demo@psau.sa', 'placeholder');"
uvicorn main:app --reload
# Open http://localhost:8000/docs
# Upload data/sample_sales_2022.xlsx via the /api/upload endpoint
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/upload` | Upload CSV/XLSX → PostgreSQL |
| GET | `/api/dashboard` | KPIs, revenue trends, top items |
| GET | `/api/menu-engineering` | Boston Matrix (Star/Plowhorse/Puzzle/Dog) |
| GET | `/api/forecast/item?target=X&period=7` | Single-item forecast |
| GET | `/api/forecast/category?target=X&period=7` | Category forecast |
| GET | `/api/forecast/total?period=7` | Grand-total forecast |
| GET | `/api/categories`, `/api/products` | Reference lists |
| GET | `/api/health` | Health check |

## Team action items
- **Arwa:** the `apii/` folder is gone on `backend-v2`. If you have local
  work there, export it before pulling. Upload logic is now at
  `backend/routes_upload.py`.
- **Noura:** your `prophet_model.py` drives the forecasting API. If you
  retrain or tune the model, update that file.
- **Norah:** dashboard router exists at `backend/routes_dashboard.py`.
- **Haneen:** owner of schema, data loader, and merge to `main`.
