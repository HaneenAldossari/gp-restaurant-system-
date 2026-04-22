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
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import text

from auth import get_current_user_id
from db import get_engine
import data_loader_db

router = APIRouter(tags=["Upload"])


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


# ─────────────────────────────────────────────────────────────────────────
# Auto-enrichment — compute time_period, season, occasion when the upload
# file doesn't contain them. This is the "Enrich Data" step from the
# system use case diagram.
# ─────────────────────────────────────────────────────────────────────────
def _bucket_time_period(hour) -> str | None:
    if pd.isna(hour):
        return None
    h = int(hour)
    if 5 <= h <= 11: return "morning"
    if 12 <= h <= 16: return "Afternoon"
    if 17 <= h <= 21: return "Evening"
    return "night"


def _compute_season(dt) -> str | None:
    if pd.isna(dt):
        return None
    m = pd.Timestamp(dt).month
    if m in (12, 1, 2): return "Winter"
    if m in (3, 4, 5):  return "Spring"
    if m in (6, 7, 8):  return "Summer"
    return "Autumn"


def _compute_occasion(dt) -> str:
    """
    Determine occasion from the date:
      - Saudi National Day (Sep 23)
      - Ramadan (whole Hijri month 9)
      - Eid al-Fitr (Hijri 10/1 - 10/3)
      - Eid al-Adha (Hijri 12/10 - 12/13)
      - Weekend (Friday/Saturday in Saudi Arabia)
      - Normal Day (default)
    """
    if pd.isna(dt):
        return "Normal Day"
    d = pd.Timestamp(dt).date()

    # National Day
    if d.month == 9 and d.day == 23:
        return "National Day"

    # Hijri-based holidays
    try:
        from hijri_converter import Gregorian
        h = Gregorian(d.year, d.month, d.day).to_hijri()
        if h.month == 9:
            return "Ramadan"
        if h.month == 10 and h.day in (1, 2, 3):
            return "Eid al-Fitr"
        if h.month == 12 and h.day in (10, 11, 12, 13):
            return "Eid al-Adha"
    except Exception:
        pass

    # Saudi weekend = Friday (4) and Saturday (5)
    if d.weekday() in (4, 5):
        return "Weekend"

    return "Normal Day"


@router.get("/api/uploads", summary="List every uploaded file and its stats")
def list_uploads(user_id: int = Depends(get_current_user_id)) -> dict[str, Any]:
    """
    Return every upload for the current user in chronological order
    (newest first), with the number of orders and line items imported from
    each. Used by the Upload page to show a management history.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT
                u.id,
                u.filename,
                u.uploaded_at,
                u.rows_imported,
                u.rows_skipped,
                COALESCE((SELECT COUNT(*) FROM orders o WHERE o.upload_id = u.id), 0) AS orders,
                COALESCE((
                    SELECT COUNT(*) FROM order_items oi
                    JOIN orders o ON oi.order_id = o.id
                    WHERE o.upload_id = u.id
                ), 0) AS items,
                COALESCE((
                    SELECT SUM(oi.quantity * oi.unit_price) FROM order_items oi
                    JOIN orders o ON oi.order_id = o.id
                    WHERE o.upload_id = u.id
                ), 0) AS revenue
            FROM uploads u
            WHERE u.user_id = :uid
            ORDER BY u.uploaded_at DESC
        """), {"uid": user_id}).fetchall()
    return {
        "uploads": [
            {
                "id": int(r[0]),
                "filename": r[1],
                "uploadedAt": r[2].isoformat() if r[2] else None,
                "rowsImported": int(r[3]),
                "rowsSkipped": int(r[4] or 0),
                "orders": int(r[5]),
                "items": int(r[6]),
                "revenue": round(float(r[7]), 2),
            }
            for r in rows
        ]
    }


@router.delete("/api/uploads/{upload_id}", summary="Remove one upload and its data")
def delete_upload(
    upload_id: int,
    user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    """
    Delete a single upload owned by the current user. Its orders and
    order_items are removed via the ON DELETE CASCADE foreign keys. Products
    and categories are shared across users so only orphans are pruned.
    Invalidates this user's forecasting cache.
    """
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT filename, rows_imported FROM uploads WHERE id = :id AND user_id = :uid"),
            {"id": upload_id, "uid": user_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found")
        conn.execute(
            text("DELETE FROM uploads WHERE id = :id AND user_id = :uid"),
            {"id": upload_id, "uid": user_id},
        )
        # Prune orphan products/categories that now have no line items at all,
        # so the filters stay clean after deletion.
        conn.execute(text("""
            DELETE FROM products
            WHERE id NOT IN (SELECT DISTINCT product_id FROM order_items)
        """))
        conn.execute(text("""
            DELETE FROM categories
            WHERE id NOT IN (SELECT DISTINCT category_id FROM products)
        """))
        # Drop this user's forecast history so stale rows aren't served.
        conn.execute(text("DELETE FROM forecasts WHERE user_id = :uid"), {"uid": user_id})
    # Invalidate caches that depend on this user's data
    data_loader_db.reload_data(user_id)
    from routes_forecast import invalidate_cache
    invalidate_cache(user_id)
    return {
        "success": True,
        "deletedId": upload_id,
        "deletedFilename": row[0],
        "deletedRows": int(row[1]),
    }


@router.delete("/api/data", summary="Clear ALL sales data for the current user")
def clear_all_data(user_id: int = Depends(get_current_user_id)) -> dict[str, Any]:
    """
    Wipe every upload owned by the current user and its data (orders, line
    items, forecasts). Products and categories are shared across users so
    only orphans are pruned. Use for a fresh start in this workspace.
    """
    engine = get_engine()
    with engine.begin() as conn:
        counts = conn.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM order_items oi
                 JOIN orders o ON oi.order_id = o.id
                 JOIN uploads u ON o.upload_id = u.id
                 WHERE u.user_id = :uid) AS items,
                (SELECT COUNT(*) FROM orders o
                 JOIN uploads u ON o.upload_id = u.id
                 WHERE u.user_id = :uid) AS orders,
                (SELECT COUNT(*) FROM uploads WHERE user_id = :uid) AS uploads
        """), {"uid": user_id}).fetchone()
        # forecasts first (no cascade from uploads), then uploads (cascades to
        # orders → order_items for this user only). Products/categories stay.
        conn.execute(text("DELETE FROM forecasts WHERE user_id = :uid"), {"uid": user_id})
        conn.execute(text("DELETE FROM uploads WHERE user_id = :uid"), {"uid": user_id})
        # Prune orphan products/categories (no line items pointing to them).
        conn.execute(text("""
            DELETE FROM products
            WHERE id NOT IN (SELECT DISTINCT product_id FROM order_items)
        """))
        conn.execute(text("""
            DELETE FROM categories
            WHERE id NOT IN (SELECT DISTINCT category_id FROM products)
        """))
    data_loader_db.reload_data(user_id)
    from routes_forecast import invalidate_cache
    invalidate_cache(user_id)
    return {
        "success": True,
        "cleared": {
            "items": int(counts[0]),
            "orders": int(counts[1]),
            "uploads": int(counts[2]),
        },
    }


@router.post("/api/upload", summary="Upload sales data and save to PostgreSQL")
async def upload_file(
    file: UploadFile = File(...),
    replace_all: bool = Query(
        False,
        description=(
            "If true, WIPES THIS USER's existing uploads/orders/line-items and "
            "forecast history before importing this file. Other users' workspaces "
            "and the shared products/categories tables are not affected. Use this "
            "for testing with isolated datasets; for incremental real-world "
            "uploads, leave it false."
        ),
    ),
    user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    """
    Upload a CSV/XLSX file. Rows are parsed and inserted into the PostgreSQL
    schema. The forecasting cache is invalidated automatically.

    **Default mode (replace_all=false)** — incremental: orders with a matching
    `order_reference` have their line items replaced; new orders are added.
    Previous uploads stay in the DB.

    **Reset mode (replace_all=true)** — wipes all existing sales data first,
    then imports only this file. Useful when testing with a specific dataset.

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
        raise HTTPException(status_code=400, detail=f"Could not read file: {e}")

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
    col_season = _pick_col(df, ["season", "Season"])
    col_occasion = _pick_col(df, ["occasion", "Occasion"])
    col_time_period = _pick_col(df, ["time_period", "timePeriod", "time_zone"])

    must = {"order_reference": col_order_ref, "quantity": col_qty,
            "unit_price": col_price, "unit_cost": col_cost, "categ_EN": col_cat_en}
    missing = [k for k, v in must.items() if v is None]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Missing required columns: {missing}",
                "columnsFound": original_cols,
            },
        )

    norm = pd.DataFrame()
    # Namespace order_reference by user_id so two teammates uploading the same
    # CSV don't collide on the global UNIQUE (order_reference) constraint.
    # This is a workaround for the testing-only multi-user mode; when we move
    # to single-tenant / proper auth, the prefix can be dropped.
    norm["order_reference"] = (
        f"u{user_id}:" + df[col_order_ref].astype(str).str.strip()
    )
    norm["sku"] = df[col_sku].astype(str).str.strip() if col_sku else df[col_name_en].astype(str).str.strip() if col_name_en else norm["order_reference"]
    norm["quantity"] = pd.to_numeric(df[col_qty], errors="coerce").fillna(0).astype(int)
    norm["unit_price"] = pd.to_numeric(df[col_price], errors="coerce").fillna(0.0)
    norm["unit_cost"] = pd.to_numeric(df[col_cost], errors="coerce").fillna(0.0)
    norm["categ_EN"] = df[col_cat_en].astype(str).str.strip()
    norm["categ_AR"] = df[col_cat_ar].astype(str).str.strip() if col_cat_ar else norm["categ_EN"]
    norm["name_en"] = df[col_name_en].astype(str).str.strip() if col_name_en else norm["sku"]
    norm["name_ar"] = df[col_name_ar].astype(str).str.strip() if col_name_ar else norm["name_en"]
    norm["customer_name"] = df[col_customer] if col_customer else None
    norm["season"] = df[col_season].astype(str).str.strip() if col_season else None
    norm["occasion"] = df[col_occasion].astype(str).str.strip() if col_occasion else None
    norm["time_period"] = df[col_time_period].astype(str).str.strip() if col_time_period else None

    try:
        norm["order_datetime"] = _build_datetime(df)
    except Exception as e:
        raise HTTPException(status_code=400, detail={"message": str(e), "columnsFound": original_cols})

    # ── Auto-enrichment: fill any missing season / occasion / time_period
    #    by computing them from order_datetime. Always overwrites obviously
    #    invalid values like the literal strings "nan" / empty / None.
    def _is_blank(v):
        return v is None or (isinstance(v, str) and v.strip().lower() in ("", "nan", "none", "null"))

    enriched_counts = {"time_period": 0, "season": 0, "occasion": 0}

    if col_time_period is None or norm["time_period"].apply(_is_blank).any():
        mask = norm["time_period"].apply(_is_blank) if col_time_period else pd.Series(True, index=norm.index)
        norm.loc[mask, "time_period"] = norm.loc[mask, "order_datetime"].dt.hour.apply(_bucket_time_period)
        enriched_counts["time_period"] = int(mask.sum())

    if col_season is None or norm["season"].apply(_is_blank).any():
        mask = norm["season"].apply(_is_blank) if col_season else pd.Series(True, index=norm.index)
        norm.loc[mask, "season"] = norm.loc[mask, "order_datetime"].apply(_compute_season)
        enriched_counts["season"] = int(mask.sum())

    if col_occasion is None or norm["occasion"].apply(_is_blank).any():
        mask = norm["occasion"].apply(_is_blank) if col_occasion else pd.Series(True, index=norm.index)
        norm.loc[mask, "occasion"] = norm.loc[mask, "order_datetime"].apply(_compute_occasion)
        enriched_counts["occasion"] = int(mask.sum())

    # Filter out invalid rows. Keep every valid row — including repeated
    # (order_reference, sku, qty, price, cost) tuples, because in real POS
    # data those represent separate purchases of the same item on the same
    # order (e.g. two taps on the Cappuccino button = two line items of
    # quantity 1 each, not one line of quantity 2). Our re-upload logic
    # already deletes the existing order_items for touched orders before
    # re-inserting, so row-level dedup here would silently lose real sales.
    total_rows = len(norm)
    norm = norm[
        (norm["order_reference"] != "")
        & (norm["sku"] != "")
        & (norm["quantity"] > 0)
        & (norm["order_datetime"].notna())
    ].copy()
    skipped = total_rows - len(norm)
    to_import = len(norm)

    engine = get_engine()
    reset_stats = None
    with engine.begin() as conn:
        if replace_all:
            # Wipe only THIS user's sales data so the import starts from
            # empty for them. Other users' workspaces are untouched.
            before = conn.execute(text("""
                SELECT
                    (SELECT COUNT(*) FROM order_items oi
                     JOIN orders o ON oi.order_id = o.id
                     JOIN uploads u ON o.upload_id = u.id
                     WHERE u.user_id = :uid) AS items,
                    (SELECT COUNT(*) FROM orders o
                     JOIN uploads u ON o.upload_id = u.id
                     WHERE u.user_id = :uid) AS orders,
                    (SELECT COUNT(*) FROM uploads WHERE user_id = :uid) AS uploads
            """), {"uid": user_id}).fetchone()
            conn.execute(text("DELETE FROM forecasts WHERE user_id = :uid"), {"uid": user_id})
            conn.execute(text("DELETE FROM uploads WHERE user_id = :uid"), {"uid": user_id})
            # Prune orphan products/categories (shared tables — only drop
            # rows no longer referenced anywhere after this user's wipe).
            conn.execute(text("""
                DELETE FROM products
                WHERE id NOT IN (SELECT DISTINCT product_id FROM order_items)
            """))
            conn.execute(text("""
                DELETE FROM categories
                WHERE id NOT IN (SELECT DISTINCT category_id FROM products)
            """))
            reset_stats = {
                "itemsWiped": int(before[0]),
                "ordersWiped": int(before[1]),
                "uploadsWiped": int(before[2]),
            }

        upload_id = conn.execute(
            text("""
                INSERT INTO uploads (user_id, filename, rows_imported, rows_skipped)
                VALUES (:uid, :fn, :ri, :rs)
                RETURNING id
            """),
            {"uid": user_id, "fn": file.filename or "upload", "ri": to_import, "rs": skipped},
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

        # Orders — track which references existed before so we can wipe their items
        existing_refs = set(
            r[0] for r in conn.execute(
                text("SELECT order_reference FROM orders WHERE order_reference = ANY(:refs)"),
                {"refs": list(norm["order_reference"].unique())},
            ).fetchall()
        )

        orders_df = norm[["order_reference", "order_datetime", "customer_name",
                          "time_period", "season", "occasion"]].drop_duplicates(subset=["order_reference"])
        for _, r in orders_df.iterrows():
            conn.execute(
                text("""
                    INSERT INTO orders (upload_id, order_reference, order_datetime, customer_name,
                                        time_period, season, occasion)
                    VALUES (:uid, :oref, :odt, :cname, :tp, :sn, :oc)
                    ON CONFLICT (order_reference) DO UPDATE SET
                        upload_id = EXCLUDED.upload_id,
                        order_datetime = EXCLUDED.order_datetime,
                        customer_name = EXCLUDED.customer_name,
                        time_period = EXCLUDED.time_period,
                        season = EXCLUDED.season,
                        occasion = EXCLUDED.occasion
                """),
                {"uid": upload_id, "oref": r["order_reference"], "odt": r["order_datetime"],
                 "cname": r["customer_name"], "tp": r["time_period"],
                 "sn": r["season"], "oc": r["occasion"]},
            )
        order_map = dict(conn.execute(text("SELECT order_reference, id FROM orders")).all())

        # Order items — re-upload semantics: wipe existing items on touched orders,
        # then bulk insert the new ones. No more float-precision dedup bug.
        replaced_refs = [r for r in orders_df["order_reference"] if r in existing_refs]
        if replaced_refs:
            replaced_ids = [order_map[r] for r in replaced_refs if r in order_map]
            if replaced_ids:
                conn.execute(
                    text("DELETE FROM order_items WHERE order_id = ANY(:ids)"),
                    {"ids": replaced_ids},
                )

        inserted = 0
        for _, r in norm[["order_reference", "sku", "quantity", "unit_price", "unit_cost"]].iterrows():
            oid = order_map.get(r["order_reference"])
            pid = prod_map.get(r["sku"])
            if oid is None or pid is None:
                continue
            conn.execute(
                text("""
                    INSERT INTO order_items (order_id, product_id, quantity, unit_price, unit_cost)
                    VALUES (:oid, :pid, :qty, :price, :cost)
                """),
                {"oid": oid, "pid": pid, "qty": int(r["quantity"]),
                 "price": float(r["unit_price"]), "cost": float(r["unit_cost"])},
            )
            inserted += 1

    # Invalidate this user's caches so forecast/dashboard/menu see the new data
    data_loader_db.reload_data(user_id)
    from routes_forecast import invalidate_cache
    invalidate_cache(user_id)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM forecasts WHERE user_id = :uid"), {"uid": user_id})

    return {
        "success": True,
        "fileName": file.filename,
        "rowsInFile": total_rows,
        "rowsImported": to_import,
        "rowsSkipped": skipped,
        "orderItemsInserted": inserted,
        "uploadId": upload_id,
        "columnsFound": original_cols,
        "enriched": enriched_counts,   # rows auto-filled per column
        "replaceAll": bool(replace_all),
        "resetStats": reset_stats,     # null unless replace_all=True
    }
