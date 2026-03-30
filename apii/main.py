from fastapi import FastAPI
from sqlalchemy import create_engine, text

app = FastAPI(title="Smart Sales Analytics API")

DB_URL = "postgresql+psycopg2://postgres:123456@localhost:5432/gp_restaurant_db"
engine = create_engine(DB_URL)


@app.get("/total-revenue")
def total_revenue():
    sql = text("""
        SELECT COALESCE(SUM(quantity * unit_price), 0) AS total_revenue
        FROM sales_raw;
    """)
    with engine.connect() as conn:
        total = conn.execute(sql).scalar()
    return {"total_revenue": float(total)}


@app.get("/top-products")
def top_products(limit: int = 10):
    sql = text("""
        SELECT
            name,
            SUM(quantity) AS total_quantity,
            SUM(quantity * unit_price) AS revenue
        FROM sales_raw
        GROUP BY name
        ORDER BY total_quantity DESC
        LIMIT :limit;
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"limit": limit}).mappings().all()
    return rows


@app.get("/revenue-by-category")
def revenue_by_category():
    sql = text("""
        SELECT
            "categ_EN" AS category,
            SUM(quantity * unit_price) AS revenue
        FROM sales_raw
        GROUP BY "categ_EN"
        ORDER BY revenue DESC;
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()
    return rows


@app.get("/revenue-by-month")
def revenue_by_month():
    sql = text("""
        SELECT
            DATE_TRUNC('month', created_at)::date AS month,
            SUM(quantity * unit_price) AS revenue
        FROM sales_raw
        GROUP BY month
        ORDER BY month;
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()
    return rows


@app.get("/menu-engineering/items")
def menu_engineering_items():
    sql = text("""
        WITH item_metrics AS (
            SELECT
                name,
                SUM(quantity) AS popularity,
                SUM((unit_price - unit_cost) * quantity) AS profit
            FROM sales_raw
            GROUP BY name
        ),
        thresholds AS (
            SELECT
                AVG(popularity) AS avg_popularity,
                AVG(profit) AS avg_profit
            FROM item_metrics
        )
        SELECT
            im.name,
            im.popularity,
            im.profit,
            CASE
                WHEN im.popularity >= t.avg_popularity AND im.profit >= t.avg_profit THEN 'Star'
                WHEN im.popularity >= t.avg_popularity AND im.profit < t.avg_profit THEN 'Plowhorse'
                WHEN im.popularity < t.avg_popularity AND im.profit >= t.avg_profit THEN 'Puzzle'
                ELSE 'Dog'
            END AS category
        FROM item_metrics im
        CROSS JOIN thresholds t
        ORDER BY category, im.profit DESC;
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()
    return rows


