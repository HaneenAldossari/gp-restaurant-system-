"""
Upload API — POST /api/upload

Accepts an Excel (.xlsx) or CSV file, parses the raw sales data, and writes the
rows into the PostgreSQL 8-table schema (categories, products, orders, order_items,
uploads). This is the proper DB-backed upload.

The detection + normalisation logic follows Arwa's import_csv_to_db.py. After
insertion, the forecasting data cache is invalidated so subsequent forecast
calls use the newly uploaded data.
"""

import io
import os
from typing import Any

import pandas as pd
from fastapi import APIRouter, File, UploadFile
from sqlalchemy import text

from db import get_engine
import data_loader_db

router = APIRouter(tags=["Upload"])

DEFAULT_USER_ID = 1  # Demo Manager seeded at setup


def _pick_col(df: pd.DataFrame, options: list[str]) -> str | None:
    for c in options:
        if c in df.columns:
            return c
    return None


def _build_datetime(df: pd.DataFrame) -> pd.Series:
    col_time = _pick_col(df, ["time", "Time", "order_time"])
    col_date = _pick_col(df, ["date", "Date", "order_date"])
    col_dt = _pick_col(df, ["created_at", "order_datetime", "datetime"])
    if col_date and col_time:
        return pd.to_datetime(
            df[col_date].astype(str).str.strip() + " " + df[col_time].astype(str).str.strip(),
            errors="coerce",
        )
    if col_dt:
        return pd.to_datetime(df[col_dt], errors="coerce")
    if col_date:
        return pd.to_datetime(df[col_date], errors="coerce")
    raise RuntimeError("No usable date/time columns found")


@router.post("/api/upload", summary="Upload sales data and save to PostgreSQL")
async def upload_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """
    Upload a CSV/XLSX file. Rows are parsed, deduplicated, and inserted into the
    PostgreSQL schema. The forecasting cache is invalidated automatically.

    **Expected columns** (any common alias works — order_reference/order_id,
    sku, quantity/qty, unit_price/price, unit_cost/cost, categ_EN/category,
    name/product_name, and either date+time or a single datetime column).
    """
    ext = os.path.splitext(file.filename or "")[1].lower()
    content = await file.read()

    try:
        if ext == ".csv":
            df = pd.read_csv(io.BytesIO(content))
        else:
            df = pd.read_excel(io.BytesIO(content), engine="openpyxl")
    except Exception as e:
        return {"success": False, "error": f"Could not read file: {e}"}

    df.columns = [str(c).strip() for c in df.columns]
    original_cols = list(df.columns)

    col_order_ref = _pick_col(df, ["order_reference", "order_id", "order_ref", "Order ID"])
    col_sku = _pick_col(df, ["sku", "SKU", "product_sku"])
    col_qty = _pick_col(df, ["quantity", "qty", "Quantity"])
    col_price = _pick_col(df, ["unit_price", "price", "Unit Price"])
    col_cost = _pick_col(df, ["unit_cost", "cost", "Unit Cost"])
    col_cat_en = _pick_col(df, ["categ_EN", "category_en", "category", "Category"])
    col_cat_ar = _pick_col(df, ["categ_AR", "category_ar"])
    col_name_en = _pick_col(df, ["name", "name_en", "product_name", "Product"])
    col_name_ar = _pick_col(df, ["name_localized", "name_ar", "arabic_name"])
    col_customer = _pick_col(df, ["customer_name", "customer", "Customer"])

    must = {"order_reference": col_order_ref, "quantity": col_qty,
            "unit_price": col_price, "unit_cost": col_cost, "categ_EN": col_cat_en}
    missing = [k for k, v in must.items() if v is None]
    if missing:
        return {
            "success": False,
            "error": f"Missing required columns: {missing}",
            "columnsFound": original_cols,
        }

    norm = pd.DataFrame()
    norm["order_reference"] = df[col_order_ref].astype(str).str.strip()
    norm["sku"] = df[col_sku].astype(str).str.strip() if col_sku else df[col_name_en].astype(str).str.strip() if col_name_en else norm["order_reference"]
    norm["quantity"] = pd.to_numeric(df[col_qty], errors="coerce").fillna(0).astype(int)
    norm["unit_price"] = pd.to_numeric(df[col_price], errors="coerce").fillna(0.0)
    norm["unit_cost"] = pd.to_numeric(df[col_cost], errors="coerce").fillna(0.0)
    norm["categ_EN"] = df[col_cat_en].astype(str).str.strip()
    norm["categ_AR"] = df[col_cat_ar].astype(str).str.strip() if col_cat_ar else norm["categ_EN"]
    norm["name_en"] = df[col_name_en].astype(str).str.strip() if col_name_en else norm["sku"]
    norm["name_ar"] = df[col_name_ar].astype(str).str.strip() if col_name_ar else norm["name_en"]
    norm["customer_name"] = df[col_customer] if col_customer else None

    try:
        norm["order_datetime"] = _build_datetime(df)
    except Exception as e:
        return {"success": False, "error": str(e), "columnsFound": original_cols}

    # Filter out invalid rows
    total_rows = len(norm)
    norm = norm[
        (norm["order_reference"] != "")
        & (norm["sku"] != "")
        & (norm["quantity"] > 0)
        & (norm["order_datetime"].notna())
    ].copy()
    skipped = total_rows - len(norm)

    norm = norm.drop_duplicates(
        subset=["order_reference", "sku", "quantity", "unit_price", "unit_cost"]
    )
    to_import = len(norm)

    engine = get_engine()
    with engine.begin() as conn:
        upload_id = conn.execute(
            text("""
                INSERT INTO uploads (user_id, filename, rows_imported, rows_skipped)
                VALUES (:uid, :fn, :ri, :rs)
                RETURNING id
            """),
            {"uid": DEFAULT_USER_ID, "fn": file.filename or "upload", "ri": to_import, "rs": skipped},
        ).scalar()

        # Upsert categories
        for _, r in norm[["categ_AR", "categ_EN"]].drop_duplicates().iterrows():
            conn.execute(
                text("""
                    INSERT INTO categories (name_ar, name_en) VALUES (:ar, :en)
                    ON CONFLICT DO NOTHING
                """),
                {"ar": r["categ_AR"], "en": r["categ_EN"]},
            )
        cat_map = dict(conn.execute(text("SELECT name_en, id FROM categories")).all())

        # Upsert products
        prods = norm[["sku", "name_ar", "name_en", "categ_EN"]].drop_duplicates()
        for _, r in prods.iterrows():
            conn.execute(
                text("""
                    INSERT INTO products (sku, name_ar, name_en, category_id, is_active)
                    VALUES (:sku, :ar, :en, :cid, TRUE)
                    ON CONFLICT (sku) DO NOTHING
                """),
                {
                    "sku": r["sku"],
                    "ar": str(r["name_ar"])[:100],
                    "en": str(r["name_en"])[:100],
                    "cid": cat_map.get(r["categ_EN"]),
                },
            )
        prod_map = dict(conn.execute(text("SELECT sku, id FROM products")).all())

        # Orders
        orders_df = norm[["order_reference", "order_datetime", "customer_name"]].drop_duplicates()
        for _, r in orders_df.iterrows():
            conn.execute(
                text("""
                    INSERT INTO orders (upload_id, order_reference, order_datetime, customer_name)
                    VALUES (:uid, :oref, :odt, :cname)
                    ON CONFLICT (order_reference) DO UPDATE SET
                        order_datetime = EXCLUDED.order_datetime,
                        customer_name = EXCLUDED.customer_name
                """),
                {"uid": upload_id, "oref": r["order_reference"], "odt": r["order_datetime"], "cname": r["customer_name"]},
            )
        order_map = dict(conn.execute(text("SELECT order_reference, id FROM orders")).all())

        # Order items (skip exact-duplicate rows already in DB)
        inserted = 0
        for _, r in norm[["order_reference", "sku", "quantity", "unit_price", "unit_cost"]].iterrows():
            oid = order_map.get(r["order_reference"])
            pid = prod_map.get(r["sku"])
            if oid is None or pid is None:
                continue
            exists = conn.execute(
                text("""
                    SELECT 1 FROM order_items
                    WHERE order_id = :oid AND product_id = :pid
                      AND quantity = :qty AND unit_price = :price AND unit_cost = :cost
                    LIMIT 1
                """),
                {"oid": oid, "pid": pid, "qty": int(r["quantity"]),
                 "price": float(r["unit_price"]), "cost": float(r["unit_cost"])},
            ).scalar()
            if not exists:
                conn.execute(
                    text("""
                        INSERT INTO order_items (order_id, product_id, quantity, unit_price, unit_cost)
                        VALUES (:oid, :pid, :qty, :price, :cost)
                    """),
                    {"oid": oid, "pid": pid, "qty": int(r["quantity"]),
                     "price": float(r["unit_price"]), "cost": float(r["unit_cost"])},
                )
                inserted += 1

    # Invalidate caches so forecast/dashboard/menu see the new data
    data_loader_db.reload_data()
    from routes_forecast import invalidate_cache
    invalidate_cache()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM forecasts"))

    return {
        "success": True,
        "fileName": file.filename,
        "rowsInFile": total_rows,
        "rowsImported": to_import,
        "rowsSkipped": skipped,
        "orderItemsInserted": inserted,
        "uploadId": upload_id,
        "columnsFound": original_cols,
    }
