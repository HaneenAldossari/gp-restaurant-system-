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
    """Return the first column that exists from a list of possible names."""
    for c in options:
        if c in df.columns:
            return c
    return None

def main():
    print("Reading Excel:", EXCEL_PATH)
    df = pd.read_excel(EXCEL_PATH)
    df.columns = [c.strip() for c in df.columns]

    col_order_ref = pick_col(df, ["order_reference", "order_id", "order_ref"])
    col_sku = pick_col(df, ["sku", "SKU", "product_sku"])
    col_qty = pick_col(df, ["quantity", "qty", "Quantity"])
    col_price = pick_col(df, ["unit_price", "price", "Unit Price"])
    col_cost = pick_col(df, ["unit_cost", "cost", "Unit Cost"])
    col_cat_en = pick_col(df, ["categ_EN", "category_en", "category", "Category"])
    col_cat_ar = pick_col(df, ["categ_AR", "category_ar"])
    col_name_en = pick_col(df, ["name", "name_en", "product_name"])
    col_name_ar = pick_col(df, ["name_localized", "name_ar", "arabic_name"])
    col_created_at = pick_col(df, ["created_at", "order_datetime", "date", "datetime"])
    col_customer = pick_col(df, ["customer_name", "customer", "Customer"])

    must = {
        "order_reference": col_order_ref,
        "sku": col_sku,
        "quantity": col_qty,
        "unit_price": col_price,
        "unit_cost": col_cost,
        "categ_EN": col_cat_en,
        "created_at/date": col_created_at,
    }
    missing_cols = [k for k, v in must.items() if v is None]
    if missing_cols:
        raise RuntimeError(f"Excel is missing required columns: {missing_cols}\nFound columns: {list(df.columns)}")

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
    norm["order_datetime"] = pd.to_datetime(df[col_created_at], errors="coerce")

    norm = norm[
        (norm["order_reference"] != "") &
        (norm["sku"] != "") &
        (norm["quantity"] > 0) &
        (norm["order_datetime"].notna())
    ].copy()

    print(" Rows to import:", len(norm))

    with engine.begin() as conn:
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

        # 3) products
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

        orders_df = norm[["order_reference", "order_datetime", "customer_name"]].drop_duplicates()
        for _, r in orders_df.iterrows():
            conn.execute(text("""
                INSERT INTO orders (upload_id, order_reference, order_datetime, customer_name)
                VALUES (:upload_id, :oref, :odt, :cname)
                ON CONFLICT (order_reference) DO NOTHING;
            """), {
                "upload_id": upload_id,
                "oref": r["order_reference"],
                "odt": r["order_datetime"],
                "cname": r["customer_name"]
            })

        order_map = dict(conn.execute(text("SELECT order_reference, id FROM orders")).all())

        for _, r in norm[["order_reference", "sku", "quantity", "unit_price", "unit_cost"]].iterrows():
            conn.execute(text("""
                INSERT INTO order_items (order_id, product_id, quantity, unit_price, unit_cost)
                VALUES (:oid, :pid, :qty, :price, :cost);
            """), {
                "oid": order_map.get(r["order_reference"]),
                "pid": prod_map.get(r["sku"]),
                "qty": int(r["quantity"]),
                "price": float(r["unit_price"]),
                "cost": float(r["unit_cost"]),
            })

    print(" DONE: Imported into categories/products/orders/order_items")

if __name__ == "__main__":
    main()