"""
Seed a small, realistic sample sales dataset into a user's workspace so
they can immediately try the forecasting / dashboard / menu engineering
pages without uploading anything.

The data is generated programmatically (no CSV shipped in git) with a
fixed seed so every workspace gets the same baseline. It mirrors what
the real upload pipeline produces — including the season / occasion /
time_period enrichment — so forecasts and Boston Matrix classifications
work the same on it as on real uploads.

Idempotent: skipped if the user already has any uploads. To force a
re-seed, call `seed_sample_for_user(user_id, force=True)`.

Patterns baked into the synthetic data:
  - Saudi Fri/Sat weekend bump (~25% lift)
  - Payday spike: day 27 → next month's 5th (~40% lift over baseline)
  - Modest mid-month dip
  - Eight products across three categories with distinct elasticity-class
    flavours (so Stars / Plowhorses / Puzzles / Dogs all appear when the
    Menu Insights page runs the Boston Matrix split)

Run as a script: `python seed_sample_data.py` — seeds every user that
doesn't have data yet.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import text

from db import get_engine

SAMPLE_FILENAME = "sample_data_2022.csv (auto-loaded)"
SAMPLE_DAYS = 120  # ~4 months — enough history for Prophet + reliability tiers
SAMPLE_START = datetime(2022, 6, 1)


# (category_en, category_ar, sku, name_en, name_ar, unit_price, unit_cost)
PRODUCTS = [
    ("Hot Drinks",  "مشروبات ساخنة", "SKU001", "Espresso",       "إسبريسو",         8,  3),
    ("Hot Drinks",  "مشروبات ساخنة", "SKU002", "Cappuccino",     "كابتشينو",        12, 4),
    ("Hot Drinks",  "مشروبات ساخنة", "SKU003", "Spanish Latte",  "لاتيه إسباني",    14, 5),
    ("Cold Drinks", "مشروبات باردة", "SKU004", "Iced Latte",     "لاتيه مثلج",      16, 6),
    ("Cold Drinks", "مشروبات باردة", "SKU005", "Iced Mocha",     "موكا مثلجة",      18, 7),
    ("Pastries",    "معجنات",        "SKU006", "Croissant",      "كرواسون",         10, 4),
    ("Pastries",    "معجنات",        "SKU007", "Cheese Pastry",  "فطيرة جبن",       9,  4),
    ("Pastries",    "معجنات",        "SKU008", "Chocolate Cake", "كيك شوكولاتة",     22, 8),
]

# Per-product baseline order share (what fraction of orders include this item).
# Picked so the Boston Matrix classification surfaces all four bands.
PRODUCT_DEMAND_WEIGHT = [0.45, 0.55, 0.60, 0.40, 0.35, 0.30, 0.25, 0.10]


def _bucket_time_period(hour: int) -> str:
    if 5 <= hour <= 11:  return "morning"
    if 12 <= hour <= 16: return "Afternoon"
    if 17 <= hour <= 21: return "Evening"
    return "night"


def _compute_season(d: datetime) -> str:
    m = d.month
    if m in (12, 1, 2):  return "Winter"
    if m in (3, 4, 5):   return "Spring"
    if m in (6, 7, 8):   return "Summer"
    return "Autumn"


def _compute_occasion(d: datetime) -> str:
    if d.month == 9 and d.day == 23: return "Saudi National Day"
    if d.day >= 27 or d.day <= 5:    return "Payday"
    if d.weekday() in (4, 5):         return "Weekend"
    return "Normal Day"


def _orders_per_day(d: datetime) -> int:
    """Realistic daily order volume with weekend + payday lifts baked in."""
    base = 80
    if d.weekday() in (4, 5):
        base = int(base * 1.25)         # Fri/Sat bump
    if d.day >= 27 or d.day <= 5:
        base = int(base * 1.40)         # post-payday spending spike
    if 13 <= d.day <= 24:
        base = int(base * 0.85)         # mid-month dip
    return max(20, int(base * random.uniform(0.85, 1.15)))


def _sample_hour() -> int:
    # Coffee shop hours: morning rush, lunch dip, afternoon peak, late evening
    weights = [3, 5, 12, 18, 14, 10, 8, 9, 11, 13, 12, 10, 8, 6, 4, 3]
    return random.choices(list(range(7, 23)), weights=weights)[0]


def generate_sample_rows() -> list[dict]:
    """Build the deterministic synthetic dataset. Returns a list of
    dicts ready to be turned into orders / order_items inserts."""
    random.seed(42)
    rows: list[dict] = []
    order_seq = 1

    for day_offset in range(SAMPLE_DAYS):
        date = SAMPLE_START + timedelta(days=day_offset)
        n_orders = _orders_per_day(date)

        for _ in range(n_orders):
            order_ref = f"SAMPLE-{order_seq:06d}"
            hour = _sample_hour()
            order_dt = date.replace(hour=hour, minute=random.randint(0, 59))
            time_period = _bucket_time_period(hour)
            season = _compute_season(date)
            occasion = _compute_occasion(date)

            # 1–3 unique items per order, weighted by product popularity
            n_items = random.choices([1, 2, 3], weights=[60, 30, 10])[0]
            chosen_idxs = random.choices(
                range(len(PRODUCTS)),
                weights=PRODUCT_DEMAND_WEIGHT,
                k=n_items,
            )
            for idx in set(chosen_idxs):  # dedupe: an order doesn't list the same item twice
                cat_en, cat_ar, sku, name_en, name_ar, price, cost = PRODUCTS[idx]
                qty = random.choices([1, 2, 3], weights=[75, 20, 5])[0]
                rows.append({
                    "order_reference": order_ref,
                    "order_datetime": order_dt,
                    "customer_name": None,
                    "time_period": time_period,
                    "season": season,
                    "occasion": occasion,
                    "categ_en": cat_en,
                    "categ_ar": cat_ar,
                    "sku": sku,
                    "name_en": name_en,
                    "name_ar": name_ar,
                    "quantity": qty,
                    "unit_price": float(price),
                    "unit_cost": float(cost),
                })
            order_seq += 1

    return rows


def seed_sample_for_user(user_id: int, force: bool = False) -> dict | None:
    """Insert the synthetic sample dataset for `user_id`. No-op if they
    already have uploads (unless force=True). Returns a summary dict."""
    engine = get_engine()
    with engine.begin() as conn:
        if not force:
            has_data = conn.execute(
                text("SELECT 1 FROM uploads WHERE user_id = :uid LIMIT 1"),
                {"uid": user_id},
            ).scalar()
            if has_data:
                return None

        rows = generate_sample_rows()
        df = pd.DataFrame(rows)
        if df.empty:
            return None

        # 1. Categories — unique pairs from the dataset
        cats = df[["categ_ar", "categ_en"]].drop_duplicates()
        conn.execute(
            text("""
                INSERT INTO categories (name_ar, name_en) VALUES (:ar, :en)
                ON CONFLICT DO NOTHING
            """),
            [{"ar": r["categ_ar"], "en": r["categ_en"]} for _, r in cats.iterrows()],
        )
        cat_map = dict(conn.execute(text("SELECT name_en, id FROM categories")).all())

        # 2. Products
        prods = df[["sku", "name_ar", "name_en", "categ_en"]].drop_duplicates()
        conn.execute(
            text("""
                INSERT INTO products (sku, name_ar, name_en, category_id, is_active)
                VALUES (:sku, :ar, :en, :cid, TRUE)
                ON CONFLICT (sku) DO NOTHING
            """),
            [
                {
                    "sku": r["sku"],
                    "ar": r["name_ar"],
                    "en": r["name_en"],
                    "cid": cat_map.get(r["categ_en"]),
                }
                for _, r in prods.iterrows()
            ],
        )
        prod_map = dict(conn.execute(text("SELECT sku, id FROM products")).all())

        # 3. Uploads row — appears in the "Upload History" so the user can
        # delete it from the UI exactly like a real upload
        upload_id = conn.execute(
            text("""
                INSERT INTO uploads (user_id, filename, rows_imported, rows_skipped)
                VALUES (:uid, :fn, :n, 0)
                RETURNING id
            """),
            {"uid": user_id, "fn": SAMPLE_FILENAME, "n": len(df)},
        ).scalar()

        # 4. Orders — bulk
        orders = df[
            ["order_reference", "order_datetime", "customer_name",
             "time_period", "season", "occasion"]
        ].drop_duplicates(subset=["order_reference"])
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
        order_map = dict(conn.execute(text("SELECT order_reference, id FROM orders")).all())

        # 5. Order items — bulk in batches of 1000
        item_payload = []
        for _, r in df.iterrows():
            oid = order_map.get(r["order_reference"])
            pid = prod_map.get(r["sku"])
            if oid is None or pid is None:
                continue
            item_payload.append({
                "oid": oid, "pid": pid, "qty": int(r["quantity"]),
                "price": float(r["unit_price"]), "cost": float(r["unit_cost"]),
            })

        insert_items = text("""
            INSERT INTO order_items (order_id, product_id, quantity, unit_price, unit_cost)
            VALUES (:oid, :pid, :qty, :price, :cost)
        """)
        BATCH = 1000
        for i in range(0, len(item_payload), BATCH):
            conn.execute(insert_items, item_payload[i:i + BATCH])

        return {
            "user_id": user_id,
            "upload_id": upload_id,
            "rows": len(df),
            "items": len(item_payload),
            "orders": len(orders),
        }


def seed_all_users() -> None:
    """Seed sample data for every user that doesn't already have data."""
    engine = get_engine()
    with engine.connect() as conn:
        users = conn.execute(text("SELECT id, name FROM users ORDER BY id")).fetchall()
    for u in users:
        result = seed_sample_for_user(u[0])
        if result:
            print(f"Seeded sample for [{u[0]}] {u[1]}: "
                  f"{result['rows']} items / {result['orders']} orders")
        else:
            print(f"Skipped [{u[0]}] {u[1]} — already has data")


if __name__ == "__main__":
    seed_all_users()
