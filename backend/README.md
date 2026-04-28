# Backend — GP Restaurant Smart Sales System

FastAPI + PostgreSQL backend. Prophet-based sales forecasting (Noura's hybrid
model), dashboard KPIs, menu engineering, and file upload.

## Requirements
- Python 3.10+
- PostgreSQL 14+ (local or remote)

## One-time setup

```bash
# 1. Install Python dependencies
cd backend
pip install -r requirements.txt

# 2. Create the database
createdb gp_restaurant

# 3. Apply the schema (8 tables + indexes)
psql gp_restaurant < schema.sql

# 4. Seed one user so FKs work (Demo Manager — id=1)
psql gp_restaurant -c "INSERT INTO users (name, email, password_hash) VALUES ('Demo Manager', 'demo@psau.sa', 'placeholder_hash');"

# 5. (Optional) Load the sample 2022 dataset
#    The API upload endpoint (/api/upload) does the same thing at runtime.
python - <<'PY'
import os
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://localhost/gp_restaurant")
# Easiest path: start the server then POST the file to /api/upload via Swagger.
# See below.
PY
```

## Running the server

```bash
cd backend
uvicorn main:app --reload
```

Open Swagger UI: http://localhost:8000/docs

## Loading sample data
1. Start the server.
2. Open `/docs` → `POST /api/upload` → `Try it out` → pick `data/sample_sales_2022.xlsx`.
3. After a successful response you have ~43,000 order items to work with.

## Endpoints

| Tag | Method | Path | Purpose |
|---|---|---|---|
| Upload | POST | `/api/upload` | Upload CSV/XLSX → PostgreSQL |
| Dashboard | GET | `/api/dashboard` | KPIs, revenue trends, top items |
| Menu Engineering | GET | `/api/menu-engineering` | Boston Matrix classification |
| Forecasting | GET | `/api/forecast/item` | Per-item 7-day forecast |
| Forecasting | GET | `/api/forecast/category` | Per-category forecast |
| Forecasting | GET | `/api/forecast/total` | Grand total forecast |
| Reference | GET | `/api/categories` | List categories |
| Reference | GET | `/api/products` | List products |
| Health | GET | `/api/health` | Liveness |

## Configuration (optional env vars)

| Variable | Default |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://<user>@localhost:5432/gp_restaurant` |
| `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` / `DB_NAME` | Split parts used when `DATABASE_URL` is not set |

## Architecture

```
main.py
├── routes_upload.py     → PostgreSQL insert (categories, products, orders, order_items, uploads)
├── routes_dashboard.py  → read-only SQL aggregations
├── routes_menu.py       → Boston Matrix on aggregated metrics
└── routes_forecast.py   → Noura's hybrid Prophet (prophet_model.py) running on DB data

data_loader_db.py        → Single SQL JOIN → pandas DataFrame (shared by all routers)
db.py                    → SQLAlchemy engine helper
prophet_model.py         → Noura's original standalone script (reference / can be run alone)
schema.sql               → 8 tables + sub-user support
```

## Team ownership

- **Haneen** — schema, forecasting, data loader
- **Arwa** — upload endpoint logic
- **Noura** — Prophet hybrid model
- **Norah** — dashboard endpoints (future)
