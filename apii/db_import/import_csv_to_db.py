import os
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
EXCEL_PATH = os.getenv("EXCEL_PATH")

required = {
    "DB_HOST": DB_HOST,
    "DB_PORT": DB_PORT,
    "DB_NAME": DB_NAME,
    "DB_USER": DB_USER,
    "DB_PASSWORD": DB_PASSWORD,
    "EXCEL_PATH": EXCEL_PATH,
}

missing = [k for k, v in required.items() if not v]
if missing:
    raise RuntimeError(f"Missing in .env: {missing}. Put them in prophet/db_import/.env")

engine = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)


def pick_col(df, options):
    for c in options:
        if c in df.columns:
            return c
    return None


def classify_timezone(hour):
    if pd.isna(hour):
        return "unknown"

    hour = int(hour)

    if 5 <= hour <= 11:
        return "morning"
    elif 12 <= hour <= 16:
        return "afternoon"
    elif 17 <= hour <= 21:
        return "evening"
    else:
        return "night"


def build_best_datetime(df):
    """
    Build order_datetime intelligently:
    1) If separate date + time columns exist -> combine them
    2) Else fallback to datetime-like columns
    """
    col_time = pick_col(df, ["time", "Time", "order_time"])
    col_date = pick_col(df, ["date", "Date", "order_date"])
    col_created_at = pick_col(df, ["created_at", "order_datetime", "datetime"])

    # الأفضل: دمج التاريخ مع الوقت
    if col_date and col_time:
        combined = pd.to_datetime(
            df[col_date].astype(str).str.strip() + " " + df[col_time].astype(str).str.strip(),
            errors="coerce"
        )
        print("Using date + time columns:", col_date, "+", col_time)
        print("Unique hours from date+time:", sorted(combined.dt.hour.dropna().unique().tolist())[:24])
        return combined

    # fallback لو فيه عمود datetime كامل
    if col_created_at:
        dt = pd.to_datetime(df[col_created_at], errors="coerce")
        print("Using datetime column:", col_created_at)
        print("Unique hours from datetime column:", sorted(dt.dt.hour.dropna().unique().tolist())[:24])
        return dt

    raise RuntimeError("No valid date/time columns found.")


def main():
    print("Reading Excel:", EXCEL_PATH)
    df = pd.read_excel(EXCEL_PATH)
    df.columns = [str(c).strip() for c in df.columns]

    col_order_ref = pick_col(df, ["order_reference", "order_id", "order_ref"])
    col_sku = pick_col(df, ["sku", "SKU", "product_sku"])
    col_qty = pick_col(df, ["quantity", "qty", "Quantity"])
    col_price = pick_col(df, ["unit_price", "price", "Unit Price"])
    col_cost = pick_col(df, ["unit_cost", "cost", "Unit Cost"])
    col_cat_en = pick_col(df, ["categ_EN", "category_en", "category", "Category"])
    col_cat_ar = pick_col(df, ["categ_AR", "category_ar"])
    col_name_en = pick_col(df, ["name", "name_en", "product_name"])
    col_name_ar = pick_col(df, ["name_localized", "name_ar", "arabic_name"])
    col_customer = pick_col(df, ["customer_name", "customer", "Customer"])
    col_timezone = pick_col(df, ["time_zone", "timezone", "timeZone"])

    must = {
        "order_reference": col_order_ref,
        "sku": col_sku,
        "quantity": col_qty,
        "unit_price": col_price,
        "unit_cost": col_cost,
        "categ_EN": col_cat_en,
    }

    missing_cols = [k for k, v in must.items() if v is None]
    if missing_cols:
        raise RuntimeError(
            f"Excel is missing required columns: {missing_cols}\nFound columns: {list(df.columns)}"
        )

    norm = pd.DataFrame()
    norm["order_reference"] = df[col_order_ref].astype(str).str.strip()
    norm["sku"] = df[col_sku].astype(str).str.strip()

    norm["quantity"] = pd.to_numeric(df[col_qty], errors="coerce").fillna(0).astype(int)
    norm["unit_price"] = pd.to_numeric(df[col_price], errors="coerce").fillna(0.0)
    norm["unit_cost"] = pd.to_numeric(df[col_cost], errors="coerce").fillna(0.0)

    norm["categ_EN"] = df[col_cat_en].astype(str).str.strip()
    norm["categ_AR"] = df[col_cat_ar].astype(str).str.strip() if col_cat_ar else norm["categ_EN"]

    norm["name_en"] = df[col_name_en].astype(str).str.strip() if col_name_en else norm["sku"]
    norm["name_ar"] = df[col_name_ar].astype(str).str.strip() if col_name_ar else norm["name_en"]

    norm["customer_name"] = df[col_customer] if col_customer else None

    # ✅ هنا الحل الحقيقي
    norm["order_datetime"] = build_best_datetime(df)

    print(norm["order_datetime"].head(10))
    print("Unique hours:", sorted(norm["order_datetime"].dt.hour.dropna().unique().tolist())[:24])

    if col_timezone:
        norm["time_zone"] = df[col_timezone].astype(str).str.strip().str.lower()
        norm["time_zone"] = norm["time_zone"].replace({"": None, "nan": None, "none": None})
    else:
        norm["time_zone"] = None

    auto_mask = norm["time_zone"].isna()
    norm.loc[auto_mask, "time_zone"] = (
        norm.loc[auto_mask, "order_datetime"].dt.hour.apply(classify_timezone)
    )

    norm = norm[
        (norm["order_reference"] != "") &
        (norm["sku"] != "") &
        (norm["quantity"] > 0) &
        (norm["order_datetime"].notna())
    ].copy()

    norm = norm.drop_duplicates(
        subset=["order_reference", "sku", "quantity", "unit_price", "unit_cost"]
    )

    print("Rows to import:", len(norm))

    with engine.begin() as conn:
        conn.execute(text("""
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS time_zone VARCHAR(20);
        """))

        user_id = conn.execute(text("""
            INSERT INTO users (name, email, password_hash, role)
            VALUES ('Admin', 'admin@example.com', 'temp_hash', 'admin')
            ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
            RETURNING id;
        """)).scalar()

        upload_id = conn.execute(text("""
            INSERT INTO uploads (user_id, filename, rows_imported, rows_skipped)
            VALUES (:user_id, :filename, :rows_imported, 0)
            RETURNING id;
        """), {
            "user_id": user_id,
            "filename": os.path.basename(EXCEL_PATH),
            "rows_imported": int(len(norm))
        }).scalar()

        cats = norm[["categ_AR", "categ_EN"]].drop_duplicates()
        for _, r in cats.iterrows():
            conn.execute(text("""
                INSERT INTO categories (name_ar, name_en)
                VALUES (:ar, :en)
                ON CONFLICT DO NOTHING;
            """), {"ar": r["categ_AR"], "en": r["categ_EN"]})

        cat_map = dict(conn.execute(text("SELECT name_en, id FROM categories")).all())

        prods = norm[["sku", "name_ar", "name_en", "categ_EN"]].drop_duplicates()
        for _, r in prods.iterrows():
            conn.execute(text("""
                INSERT INTO products (sku, name_ar, name_en, category_id, is_active)
                VALUES (:sku, :name_ar, :name_en, :cat_id, TRUE)
                ON CONFLICT (sku) DO NOTHING;
            """), {
                "sku": r["sku"],
                "name_ar": str(r["name_ar"])[:100],
                "name_en": str(r["name_en"])[:100],
                "cat_id": cat_map.get(r["categ_EN"])
            })

        prod_map = dict(conn.execute(text("SELECT sku, id FROM products")).all())

        orders_df = norm[["order_reference", "order_datetime", "customer_name", "time_zone"]].drop_duplicates()
        for _, r in orders_df.iterrows():
            conn.execute(text("""
                INSERT INTO orders (upload_id, order_reference, order_datetime, customer_name, time_zone)
                VALUES (:upload_id, :oref, :odt, :cname, :time_zone)
                ON CONFLICT (order_reference)
                DO UPDATE SET
                    order_datetime = EXCLUDED.order_datetime,
                    customer_name = EXCLUDED.customer_name,
                    time_zone = EXCLUDED.time_zone;
            """), {
                "upload_id": upload_id,
                "oref": r["order_reference"],
                "odt": r["order_datetime"],
                "cname": r["customer_name"],
                "time_zone": r["time_zone"]
            })

        order_map = dict(conn.execute(text("SELECT order_reference, id FROM orders")).all())

        for _, r in norm[["order_reference", "sku", "quantity", "unit_price", "unit_cost"]].iterrows():
            order_id = order_map.get(r["order_reference"])
            product_id = prod_map.get(r["sku"])

            if order_id is None or product_id is None:
                continue

            exists = conn.execute(text("""
                SELECT 1
                FROM order_items
                WHERE order_id = :oid
                  AND product_id = :pid
                  AND quantity = :qty
                  AND unit_price = :price
                  AND unit_cost = :cost
                LIMIT 1;
            """), {
                "oid": order_id,
                "pid": product_id,
                "qty": int(r["quantity"]),
                "price": float(r["unit_price"]),
                "cost": float(r["unit_cost"]),
            }).scalar()

            if not exists:
                conn.execute(text("""
                    INSERT INTO order_items (order_id, product_id, quantity, unit_price, unit_cost)
                    VALUES (:oid, :pid, :qty, :price, :cost);
                """), {
                    "oid": order_id,
                    "pid": product_id,
                    "qty": int(r["quantity"]),
                    "price": float(r["unit_price"]),
                    "cost": float(r["unit_cost"]),
                })

    print("DONE: Imported into categories/products/orders/order_items with automatic time_zone")


if __name__ == "__main__":
    main()