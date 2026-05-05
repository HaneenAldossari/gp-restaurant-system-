"""FastAPI backend for GP Restaurant Sales System."""
import logging
import os
import traceback
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from auth import get_current_user_id
from data_loader_db import load_data
from db import get_engine

log = logging.getLogger("gp")
logging.basicConfig(level=logging.INFO)

from routes_dashboard import router as dashboard_router
from routes_menu import router as menu_router
from routes_forecast import router as forecast_router
from routes_upload import router as upload_router
from routes_auth import router as auth_router
from routes_team import router as team_router


def _fast_schema_setup() -> None:
    """Cheap, fast operations that MUST run before traffic is served:
    create tables on a fresh DB, run idempotent column migrations, and
    seed the teammate user accounts. Total cost: a few hundred ms.

    The slow stuff (auto-seeding the 60K-row sample dataset for each
    workspace) is deferred to a background thread by `_async_seed`
    below — that work would otherwise take 2–3 minutes and exceed
    Render's port-detection timeout, causing the deploy to be
    cancelled even though the application is healthy.
    """
    engine = get_engine()
    schema_path = Path(__file__).parent / "schema.sql"
    try:
        with engine.connect() as conn:
            existing = conn.execute(
                text("SELECT to_regclass('public.users')")
            ).scalar()
        if existing is None and schema_path.exists():
            log.info("Bootstrapping empty database from schema.sql")
            sql = schema_path.read_text()
            with engine.begin() as conn:
                for statement in [s.strip() for s in sql.split(";")]:
                    if statement:
                        conn.execute(text(statement))
            log.info("Schema created.")

        # Idempotent migrations — quick. Adds columns, drops orphan
        # legacy upload rows. Completes in milliseconds even on a
        # populated database.
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    ALTER TABLE uploads
                    ADD COLUMN IF NOT EXISTS is_synthetic BOOLEAN NOT NULL DEFAULT FALSE
                """))
                conn.execute(text("""
                    DELETE FROM uploads
                    WHERE filename IN (
                        'orders_2022.xlsx (auto-loaded)',
                        'orders_2022.xlsx (auto-loaded — real days)',
                        'orders_2022.xlsx (auto-loaded — imputed days)'
                    )
                """))
        except Exception as e:
            log.warning("Schema migration skipped: %s", e)

        # Seed teammate accounts — also fast (5 ON-CONFLICT inserts).
        try:
            from seed_users import seed as _seed
            _seed()
        except Exception as e:
            log.warning("Skipping user seed: %s", e)
    except Exception as e:
        log.error("Schema setup failed: %s", e)


def _async_seed() -> None:
    """Slow auto-seed of the 5.7 MB sample dataset for every workspace
    that doesn't already have data. Runs in a daemon thread spawned
    AFTER the FastAPI app reports ready, so Render's port scanner sees
    the port bound immediately and the deploy succeeds. The seed
    typically finishes within 2–3 minutes; until then, any user that
    logs in early just sees an empty dashboard for a moment.

    Idempotent — `seed_sample_for_user` short-circuits on workspaces
    that already have order_items, so this thread is also safe to run
    on a normal restart with data already in the DB.
    """
    try:
        from seed_sample_data import seed_all_users as _seed_samples
        log.info("Background sample-data seed: starting")
        _seed_samples()
        log.info("Background sample-data seed: complete")
    except Exception as e:
        log.warning("Background sample-data seed failed: %s", e)


def _ensure_schema_and_seed() -> None:
    """Synchronous wrapper called once during FastAPI startup. Does the
    fast work inline and kicks the slow work off in a background daemon
    thread. The startup hook returns in milliseconds so Render's port
    scanner detects the bound port immediately.
    """
    _fast_schema_setup()
    import threading
    threading.Thread(target=_async_seed, daemon=True, name="sample-seed").start()


app = FastAPI(
    title="Smart Sales Analytics & Forecasting System API",
    description=(
        "Backend API for the Smart Sales Analytics & Forecasting System.\n\n"
        "**Workspace isolation:** every request carries `X-User-Id: <int>` "
        "and all data reads/writes are scoped to that user's workspace. "
        "Requests without the header default to user 1 for demo purposes.\n\n"
        "**Modules:**\n"
        "- **Upload** — Upload and validate sales data (Excel/CSV)\n"
        "- **Dashboard** — Sales KPIs, top products, revenue breakdowns\n"
        "- **Menu Engineering** — Boston Matrix classification (Star, Plowhorse, Puzzle, Dog)\n"
        "- **Forecasting** — Prophet-based sales forecasting with weather and occasion regressors\n\n"
        "Built for the GP Restaurant Sales System graduation project."
    ),
    version="1.0.0",
    contact={
        "name": "GP Team — Prince Sattam Bin Abdulaziz University",
    },
    openapi_tags=[
        {"name": "Health", "description": "API health and status checks."},
        {"name": "Workspace", "description": "Workspace / user-management helpers for the demo."},
        {"name": "Upload", "description": "Upload sales data files (Excel/CSV) for processing."},
        {"name": "Dashboard", "description": "Sales KPIs, revenue trends, and product breakdowns."},
        {"name": "Menu Engineering", "description": "Boston Matrix classification of menu items."},
        {"name": "Forecasting", "description": "Sales forecasts at item, category, and total levels using Prophet."},
        {"name": "Reference", "description": "Reference data — categories and product lists."},
    ],
)

# CORS — comma-separated origins via env, plus the local dev URLs by default.
# Set CORS_ORIGINS="https://your-app.vercel.app" on Render to lock down to
# the deployed frontend; "*" is fine for staging since there are no cookies.
_default_origins = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,*"
_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", _default_origins).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _prewarm_forecasts() -> None:
    """Trigger Prophet training for each seeded user in a background
    thread. By the time a tester clicks the Forecasting page, the
    predictions DataFrame is already cached, so the page loads instantly
    instead of blocking for 5-12 minutes on the free-tier CPU.

    Errors here are logged and swallowed — the app still works without
    pre-warming, the first forecast just takes the long path."""
    import threading

    def _warm():
        try:
            from routes_forecast import _get_predictions
            with get_engine().connect() as conn:
                user_ids = [r[0] for r in conn.execute(text("SELECT id FROM users ORDER BY id")).fetchall()]
            for uid in user_ids:
                try:
                    log.info("Pre-warming forecast cache for user %s", uid)
                    _get_predictions(uid)
                    log.info("Pre-warm complete for user %s", uid)
                except Exception as e:
                    log.warning("Pre-warm skipped for user %s: %s", uid, e)
        except Exception as e:
            log.warning("Forecast pre-warm thread failed: %s", e)

    threading.Thread(target=_warm, daemon=True, name="prophet-prewarm").start()


@app.on_event("startup")
def _startup() -> None:
    """Bootstrap the database on first boot. Idempotent."""
    _ensure_schema_and_seed()
    # Kick off Prophet training in the background. Don't block startup —
    # health checks need to respond immediately.
    # Pre-warm DEFAULT OFF — on Render's 0.1-CPU / 512MB free tier the
    # background training thread was getting OOM-killed mid-fit, leaving
    # the cache lock held by a dead thread and causing every subsequent
    # forecast request to block forever. Better to take the one-time
    # ~30s wait on the first user click than risk the deadlock.
    # Set PREWARM_FORECASTS=true on Render only when on a paid plan.
    if os.getenv("PREWARM_FORECASTS", "false").lower() in ("1", "true", "yes"):
        _prewarm_forecasts()


# Register route modules
app.include_router(auth_router)
app.include_router(team_router)
app.include_router(dashboard_router)
app.include_router(menu_router)
app.include_router(forecast_router)
app.include_router(upload_router)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """Catch-all so unexpected errors return a clean 500 instead of HTML stack traces."""
    log.error("Unhandled %s on %s: %s", type(exc).__name__, request.url, exc)
    log.debug(traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
    )


@app.get("/api/health", tags=["Health"], summary="Health check")
def health():
    """Returns `{status: ok}` if the API is running."""
    return {"status": "ok"}


@app.get("/api/workspace/users", tags=["Workspace"], summary="List seeded workspace users")
def list_workspace_users():
    """Return every user in the `users` table — used by the demo UI to let
    a tester pick which workspace to act as."""
    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT id, name, email FROM users ORDER BY id")).fetchall()
    return {"users": [{"id": r[0], "name": r[1], "email": r[2]} for r in rows]}


@app.get("/api/data-range", tags=["Reference"], summary="Report the range of the uploaded data")
def data_range(user_id: int = Depends(get_current_user_id)):
    """
    Return the earliest and latest dates in the loaded sales data plus the
    forecast window and reliability tiers.

    Reliability tiers (rule of thumb for time-series forecasting):
      - **reliable**     — horizon ≤ 30% of training window. Model has seen
                           comparable past patterns; predictions are solid.
      - **directional**  — horizon between 30% and 100% of training window.
                           Useful for direction/magnitude; accuracy softer.
      - **extrapolation** — horizon beyond the training window. Numbers are
                            produced but unsupported by the data. Treat as a
                            rough guess, not a forecast.

    The forecast horizon is no longer hard-capped — users can pick any
    future date even if the data is years old. The reliability tiers
    let the UI surface confidence so a 2-year-out prediction is clearly
    flagged as extrapolation rather than presented like a near-term
    forecast. The backend retrains with a longer horizon on demand when
    a request asks for a window beyond what's currently cached.
    """
    df = load_data(user_id)
    if df.empty:
        return {"hasData": False}
    import pandas as _pd
    earliest = df["Order Date"].min()
    latest = df["Order Date"].max()
    total_days = int((latest - earliest).days) + 1
    # Forecasts are anchored on TODAY, not on the dataset's last date — so
    # a 2022 dataset opened in 2026 still produces a "next 7 days" forecast
    # for next week. forecastStart is the earliest date the UI's date
    # pickers should allow.
    today = _pd.Timestamp.now().normalize()
    forecast_start = max(today + _pd.Timedelta(days=1), latest + _pd.Timedelta(days=1))
    # Generous upper bound for date pickers — at least 5 years past today
    # (or past the data, whichever is later). Just a UI hint; the backend
    # retrains on demand for any window the user actually requests.
    max_horizon = max(1825, total_days * 5)
    forecast_end_max = forecast_start + _pd.Timedelta(days=max_horizon)
    reliable_days = max(7, int(total_days * 0.30))
    directional_days = total_days  # extrapolation starts past training size
    return {
        "hasData": True,
        "earliest": earliest.date().isoformat(),
        "latest": latest.date().isoformat(),
        "totalDays": total_days,
        "totalRows": int(len(df)),
        "uniqueOrders": int(df["Order ID"].nunique()),
        "forecastStart": forecast_start.date().isoformat(),
        "forecastEndMax": forecast_end_max.date().isoformat(),
        "forecastMaxDays": max_horizon,
        "reliabilityTiers": {
            "reliableDays": reliable_days,
            "directionalDays": directional_days,
        },
    }


@app.get("/api/categories", tags=["Reference"], summary="List all product categories")
def categories(user_id: int = Depends(get_current_user_id)):
    df = load_data(user_id)
    cats = sorted(df["Category"].unique().tolist())
    return {"categories": ["All"] + cats}


@app.get("/api/products", tags=["Reference"], summary="List products with totals")
def products(
    category: str | None = None,
    user_id: int = Depends(get_current_user_id),
):
    df = load_data(user_id)
    if category and category.lower() != "all":
        df = df[df["Category"] == category]
    items = df.groupby("Product").agg(
        category=("Category", "first"),
        qtySold=("Quantity", "sum"),
        revenue=("Total Price", "sum"),
    ).reset_index()
    items.columns = ["name", "category", "qtySold", "revenue"]
    return {"products": items.to_dict("records")}


# (Upload endpoint moved to routes_upload.py — now writes to PostgreSQL)
