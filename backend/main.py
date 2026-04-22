"""FastAPI backend for GP Restaurant Sales System."""
import logging
import traceback

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

# CORS — allow Next.js dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register route modules
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

    The model always produces 365 days of predictions, so the UI can offer
    any horizon up to 1 year. The tiers let the UI flag quality.
    """
    df = load_data(user_id)
    if df.empty:
        return {"hasData": False}
    import pandas as _pd
    earliest = df["Order Date"].min()
    latest = df["Order Date"].max()
    total_days = int((latest - earliest).days) + 1
    forecast_start = latest + _pd.Timedelta(days=1)
    max_horizon = 365  # hard cap from the Prophet model
    forecast_end_max = latest + _pd.Timedelta(days=max_horizon)
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
