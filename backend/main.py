"""FastAPI backend for GP Restaurant Sales System."""
import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from data_loader_db import load_data

log = logging.getLogger("gp")

from routes_dashboard import router as dashboard_router
from routes_menu import router as menu_router
from routes_forecast import router as forecast_router
from routes_upload import router as upload_router

app = FastAPI(
    title="Smart Sales Analytics & Forecasting System API",
    description=(
        "Backend API for the Smart Sales Analytics & Forecasting System.\n\n"
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


@app.on_event("startup")
def startup():
    """Pre-load data on startup."""
    load_data()


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


@app.get("/api/categories", tags=["Reference"], summary="List all product categories")
def categories():
    df = load_data()
    cats = sorted(df["Category"].unique().tolist())
    return {"categories": ["All"] + cats}


@app.get("/api/products", tags=["Reference"], summary="List products with totals")
def products(category: str | None = None):
    df = load_data()
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
