"""
Seed each workspace with the bundled 2022 orders dataset so the deployed
app feels alive the moment a tester picks their workspace — no upload
needed. The file (`backend/sample_data/orders_2022.xlsx`) is the same
real export the forecasting model was trained on, so anyone testing the
deployed app sees results that match the local development experience.

The sample shows up in Upload History as "orders_2022.xlsx
(auto-loaded)". Testers can:
  - Delete it from Settings → Upload Data and replace with their own file
  - Add another upload on top (incremental — orders accumulate by reference)
  - Use "Clear all data" to wipe; the next backend startup re-seeds it

Column detection / normalization / season-occasion-time-period
enrichment all reuse the helpers from `routes_upload.py` so the seed
behaves identically to a real upload.
"""
from __future__ import annotations

import io
import threading
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from db import get_engine

SAMPLE_FILE = Path(__file__).parent / "sample_data" / "orders_2022.xlsx"
# Single upload row in the UI. We previously split the seed into two
# rows (real + imputed) to support per-surface filtering, but every
# surface (Dashboard, Menu Insights, Forecast) ended up using the
# full dataset, so the split was just visual noise in Upload History.
# is_synthetic stays on the schema as a future-proofing column but
# is unused by the current seed.
SAMPLE_FILENAME_LABEL = "orders_2022.xlsx (auto-loaded)"
# Legacy two-row labels kept only so the migration in main.py can
# identify and drop them on existing deployments for clean re-seed.
SAMPLE_FILENAME_LEGACY_REAL = "orders_2022.xlsx (auto-loaded — real days)"
SAMPLE_FILENAME_LEGACY_IMPUTED = "orders_2022.xlsx (auto-loaded — imputed days)"

# Per-user lock so concurrent seed calls (e.g. two near-simultaneous
# logins or a startup-seed racing with a login-seed) don't both run
# the inserts and produce duplicate order_items. The first caller
# acquires the lock and seeds; subsequent callers no-op until the
# first finishes, by which point the user already has data.
_seed_locks: dict[int, threading.Lock] = {}
_seed_lock_factory_lock = threading.Lock()


def _get_seed_lock(user_id: int) -> threading.Lock:
    with _seed_lock_factory_lock:
        if user_id not in _seed_locks:
            _seed_locks[user_id] = threading.Lock()
        return _seed_locks[user_id]


def _normalize_dataframe(df: pd.DataFrame, user_id: int) -> tuple[pd.DataFrame, int, int]:
    """Match the upload route's column detection and enrichment in one place
    so the seed and the real upload produce identical rows."""
    # Lazy import to avoid circular dependency at module import time
    from routes_upload import (
        _pick_col, _build_datetime, _bucket_time_period,
        _compute_season, _compute_occasion,
    )

    df.columns = [str(c).strip() for c in df.columns]

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
        raise RuntimeError(f"Sample data missing required columns: {missing}")

    norm = pd.DataFrame()
    # Namespace by user_id so all four teammates get an independent copy
    # under the global UNIQUE (order_reference) constraint.
    #
    # When the order_reference is missing (typical of auto-imputed
    # rows), synthesise a stable ref from the row's date + time_period
    # so all imputed items in the same bucket batch into one synthetic
    # "order" — mirrors how real cafes group multiple line items per
    # visit. Without this, all NaN refs collapse to the same key and
    # the unique constraint blows up.
    col_date_for_ref = _pick_col(df, ["date", "Date", "order_date", "created_at"])
    # Float-NaN, string "nan", and other empties — all need to trip the
    # synthetic-ref fallback.
    is_missing_ref = (
        df[col_order_ref].isna()
        | df[col_order_ref].astype(str).str.strip().isin(["nan", "NaT", "None", ""])
    )
    ref_str = df[col_order_ref].astype(str).str.strip()
    if is_missing_ref.any():
        if col_date_for_ref:
            date_part = pd.to_datetime(df[col_date_for_ref], errors="coerce").dt.strftime("%Y-%m-%d").fillna("nodate")
        else:
            date_part = pd.Series("nodate", index=df.index)
        if col_time_period:
            tp_part = df[col_time_period].astype(str).str.strip().replace({"nan": "all", "": "all", "NaT": "all"})
        else:
            tp_part = pd.Series("all", index=df.index)
        # Include row index too so per-row uniqueness holds even when
        # multiple imputed line items share the same (date, period).
        # We dedupe later in the orders-table insert via
        # drop_duplicates(["order_reference"]); having a unique ref per
        # ROW means each line item lands in its own order which mirrors
        # the real data shape (most cafe orders have 1-2 line items).
        idx_part = pd.Series(df.index.astype(str), index=df.index)
        synth = "syn:" + date_part + ":" + tp_part + ":r" + idx_part
        ref_str = ref_str.where(~is_missing_ref, synth)
    norm["order_reference"] = f"u{user_id}:" + ref_str
    norm["sku"] = (
        df[col_sku].astype(str).str.strip()
        if col_sku
        else (df[col_name_en].astype(str).str.strip() if col_name_en else norm["order_reference"])
    )
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
    # Propagate is_imputed when the dataset has it (the curated
    # real+filled file does, the raw POS export doesn't). Used later
    # to split the seed into two upload rows: one real, one synthetic.
    if "is_imputed" in df.columns:
        norm["is_imputed"] = df["is_imputed"].fillna(False).astype(bool)
    else:
        norm["is_imputed"] = False

    norm["order_datetime"] = _build_datetime(df)

    # Drop rows with no datetime — Prophet can't use them, dashboard breaks
    keep = norm["order_datetime"].notna()
    skipped = int((~keep).sum())
    norm = norm[keep].copy()

    # Enrich season / occasion / time_period if the export didn't include them
    def _is_blank(v) -> bool:
        return v is None or (isinstance(v, str) and not v.strip()) or pd.isna(v)

    if col_time_period is None or norm["time_period"].apply(_is_blank).any():
        mask = norm["time_period"].apply(_is_blank) if col_time_period else pd.Series(True, index=norm.index)
        norm.loc[mask, "time_period"] = norm.loc[mask, "order_datetime"].dt.hour.apply(_bucket_time_period)

    if col_season is None or norm["season"].apply(_is_blank).any():
        mask = norm["season"].apply(_is_blank) if col_season else pd.Series(True, index=norm.index)
        norm.loc[mask, "season"] = norm.loc[mask, "order_datetime"].apply(_compute_season)

    if col_occasion is None or norm["occasion"].apply(_is_blank).any():
        mask = norm["occasion"].apply(_is_blank) if col_occasion else pd.Series(True, index=norm.index)
        norm.loc[mask, "occasion"] = norm.loc[mask, "order_datetime"].apply(_compute_occasion)

    return norm, int(len(norm)), skipped


def seed_sample_for_user(user_id: int, force: bool = False) -> dict | None:
    """Insert the bundled sample dataset for `user_id`. No-op if they
    already have ACTUAL data (uploads with linked order_items), unless
    force=True. Cleans up any orphan upload rows (entries with no
    linked orders — left behind by a previous failed import) before
    seeding so the Upload History card stays clean.

    Concurrency-safe via a per-user lock — two near-simultaneous calls
    won't both run the inserts and double up the data."""
    if not SAMPLE_FILE.exists():
        return None

    lock = _get_seed_lock(user_id)
    if not lock.acquire(blocking=False):
        # Another thread is already seeding this user — let it finish
        # rather than racing it. The other thread's transaction will
        # commit before any subsequent caller sees the result.
        return None

    try:
        engine = get_engine()
        with engine.begin() as conn:
            # Drop orphan upload rows: a row in `uploads` whose
            # corresponding orders / order_items never landed (failed
            # mid-insert in the past). They show up in Upload History
            # as "0 line items / —" and confuse users.
            conn.execute(text("""
                DELETE FROM uploads
                WHERE user_id = :uid
                  AND id NOT IN (SELECT DISTINCT upload_id FROM orders WHERE upload_id IS NOT NULL)
            """), {"uid": user_id})

            if not force:
                # The right "already has data" check is on order_items,
                # not on uploads — an upload row with zero items is the
                # bug we just cleaned, so we should still seed in that
                # case.
                has_data = conn.execute(text("""
                    SELECT 1 FROM order_items oi
                    JOIN orders o ON oi.order_id = o.id
                    JOIN uploads u ON o.upload_id = u.id
                    WHERE u.user_id = :uid
                    LIMIT 1
                """), {"uid": user_id}).scalar()
                if has_data:
                    return None

            df_raw = pd.read_excel(SAMPLE_FILE, engine="openpyxl")
            norm, to_import, skipped = _normalize_dataframe(df_raw, user_id)

            # 1. Categories
            cats = norm[["categ_AR", "categ_EN"]].drop_duplicates()
            if not cats.empty:
                conn.execute(
                    text("""
                        INSERT INTO categories (name_ar, name_en) VALUES (:ar, :en)
                        ON CONFLICT DO NOTHING
                    """),
                    [{"ar": r["categ_AR"], "en": r["categ_EN"]} for _, r in cats.iterrows()],
                )
            cat_map = dict(conn.execute(text("SELECT name_en, id FROM categories")).all())

            # 2. Products
            prods = norm[["sku", "name_ar", "name_en", "categ_EN"]].drop_duplicates()
            if not prods.empty:
                conn.execute(
                    text("""
                        INSERT INTO products (sku, name_ar, name_en, category_id, is_active)
                        VALUES (:sku, :ar, :en, :cid, TRUE)
                        ON CONFLICT (sku) DO NOTHING
                    """),
                    [
                        {
                            "sku": r["sku"],
                            "ar": str(r["name_ar"])[:100],
                            "en": str(r["name_en"])[:100],
                            "cid": cat_map.get(r["categ_EN"]),
                        }
                        for _, r in prods.iterrows()
                    ],
                )
            prod_map = dict(conn.execute(text("SELECT sku, id FROM products")).all())

            # One unified upload row regardless of whether the source
            # file has imputed rows. The is_synthetic column on
            # `uploads` stays available for future per-surface filters
            # but isn't used by the current seed — every surface
            # (Dashboard, Menu Insights, Forecast) consumes the full
            # dataset.
            batches: list[tuple[str, pd.DataFrame, bool]] = [
                (SAMPLE_FILENAME_LABEL, norm, False),
            ]

            total_items = 0
            total_orders = 0
            first_upload_id = None
            for filename, batch_norm, is_synthetic in batches:
                # 3. Uploads row — appears in Upload History so user can
                #    delete it. is_synthetic flag drives the dashboard /
                #    menu-insights filter.
                upload_id = conn.execute(
                    text("""
                        INSERT INTO uploads (user_id, filename, rows_imported, rows_skipped, is_synthetic)
                        VALUES (:uid, :fn, :ri, :rs, :syn)
                        RETURNING id
                    """),
                    {
                        "uid": user_id, "fn": filename,
                        "ri": int(len(batch_norm)),
                        "rs": skipped if not is_synthetic else 0,
                        "syn": bool(is_synthetic),
                    },
                ).scalar()
                if first_upload_id is None:
                    first_upload_id = upload_id

                # 4. Orders — bulk
                orders = batch_norm[
                    ["order_reference", "order_datetime", "customer_name",
                     "time_period", "season", "occasion"]
                ].drop_duplicates(subset=["order_reference"])
                if not orders.empty:
                    conn.execute(
                        text("""
                            INSERT INTO orders (upload_id, order_reference, order_datetime, customer_name,
                                                time_period, season, occasion)
                            VALUES (:uid, :oref, :odt, :cname, :tp, :sn, :oc)
                            ON CONFLICT (order_reference) DO NOTHING
                        """),
                        [
                            {
                                "uid": upload_id,
                                "oref": r["order_reference"],
                                "odt": r["order_datetime"],
                                "cname": r["customer_name"],
                                "tp": r["time_period"],
                                "sn": r["season"],
                                "oc": r["occasion"],
                            }
                            for _, r in orders.iterrows()
                        ],
                    )
                order_map = dict(
                    conn.execute(text(
                        "SELECT order_reference, id FROM orders WHERE upload_id = :uid"
                    ), {"uid": upload_id}).all()
                )

                # 5. Order items — bulk in batches
                item_payload: list[dict] = []
                for _, r in batch_norm[["order_reference", "sku", "quantity", "unit_price", "unit_cost"]].iterrows():
                    oid = order_map.get(r["order_reference"])
                    pid = prod_map.get(r["sku"])
                    if oid is None or pid is None:
                        continue
                    item_payload.append({
                        "oid": oid, "pid": pid, "qty": int(r["quantity"]),
                        "price": float(r["unit_price"]), "cost": float(r["unit_cost"]),
                    })

                if item_payload:
                    insert_items = text("""
                        INSERT INTO order_items (order_id, product_id, quantity, unit_price, unit_cost)
                        VALUES (:oid, :pid, :qty, :price, :cost)
                    """)
                    BATCH = 1000
                    for i in range(0, len(item_payload), BATCH):
                        conn.execute(insert_items, item_payload[i:i + BATCH])

                total_items += len(item_payload)
                total_orders += len(orders)

            return {
                "user_id": user_id,
                "upload_id": first_upload_id,
                "rows": to_import,
                "items": total_items,
                "orders": total_orders,
                "skipped": skipped,
                "batches": [(b[0], len(b[1]), b[2]) for b in batches],
            }
    finally:
        lock.release()


def seed_all_users() -> None:
    """Seed sample data for every user that doesn't already have data.
    Per-user errors are isolated — one failure does not stop the loop,
    so a transient timeout / memory pressure on one user's seed leaves
    the others intact. Failures are logged; on the next backend startup
    the seed is retried (idempotent)."""
    if not SAMPLE_FILE.exists():
        print(f"Sample file not found at {SAMPLE_FILE}, skipping.")
        return
    engine = get_engine()
    with engine.connect() as conn:
        users = conn.execute(text("SELECT id, name FROM users ORDER BY id")).fetchall()
    for u in users:
        try:
            result = seed_sample_for_user(u[0])
            if result:
                print(f"Seeded sample for [{u[0]}] {u[1]}: "
                      f"{result['rows']} items / {result['orders']} orders "
                      f"(skipped {result['skipped']})")
            else:
                print(f"Skipped [{u[0]}] {u[1]} — already has data")
        except Exception as e:
            print(f"FAILED to seed [{u[0]}] {u[1]}: {type(e).__name__}: {e}")
            # continue to next user instead of bubbling up


if __name__ == "__main__":
    seed_all_users()
