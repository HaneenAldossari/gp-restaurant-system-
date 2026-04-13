from fastapi import FastAPI, UploadFile, File, Query
import pandas as pd
import io
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Smart Analytics API 🔥",
    openapi_tags=[
        {"name": "1-Upload", "description": "Upload your data first"},
        {"name": "2-Analytics", "description": "All analytics endpoints"}
    ]
)

# 👇 لتفادي أخطاء CORS في واجهة Swagger
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===================================================
# GLOBAL STORAGE
# ===================================================
global_data = None

# ===================================================
# HELPER FUNCTIONS
# ===================================================
def smart_find_col(df, keywords):
    """محاولة ذكية للعثور على اسم عمود معين بناءً على كلمات مفتاحية"""
    extended_keys = keywords + ["product name", "اسم المنتج", "productname", "prod", "item", "coffee", "drink"]
    for col in df.columns:
        col_lower = str(col).lower()
        for key in extended_keys:
            if key in col_lower:
                return col
    return None


def clean_numeric(series):
    return pd.to_numeric(
        series.astype(str).str.replace(r"[^0-9.\-]", "", regex=True),
        errors="coerce"
    )


def parse_any_date(df, date_col=None, datetime_col=None):
    if datetime_col:
        return pd.to_datetime(df[datetime_col], errors="coerce", dayfirst=True)
    elif date_col:
        return pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
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


@app.post("/upload", tags=["1-Upload"])
async def upload(file: UploadFile = File(...)):
    global global_data

    try:
        content = await file.read()

        # ===================================================
        # 📂 قراءة الملف
        # ===================================================
        if file.filename.endswith(".csv"):
            df = pd.read_csv(io.StringIO(content.decode("utf-8")))
        else:
            df = pd.read_excel(io.BytesIO(content))

        df.columns = [str(c).strip() for c in df.columns]

        # ===================================================
        # 🔍 الأعمدة
        # ===================================================
        date_col = smart_find_col(df, ["date"])
        time_col = smart_find_col(df, ["time"])
        datetime_col = smart_find_col(df, ["datetime"])
        temp_col = smart_find_col(df, ["temp"])
        wind_col = smart_find_col(df, ["wind"])
        quantity_col = smart_find_col(df, ["qty", "quantity"])

        # ===================================================
        # 📅 DATE
        # ===================================================
        df["date"] = parse_any_date(df, date_col, datetime_col)

        # fallback لو فشل
        if df["date"].isna().all():
            df["date"] = pd.to_datetime("today")

        # ===================================================
        # ⏰ TIME
        # ===================================================
        if time_col:
            df["time"] = df[time_col].astype(str)
        else:
            df["time"] = "12:00:00"

        # ===================================================
        # 🧠 HOUR
        # ===================================================
        if datetime_col:
            df["hour"] = pd.to_datetime(df[datetime_col], errors="coerce").dt.hour
        elif date_col and time_col:
            combined = df[date_col].astype(str) + " " + df[time_col].astype(str)
            df["hour"] = pd.to_datetime(combined, errors="coerce").dt.hour
        else:
            df["hour"] = pd.to_datetime(df["time"], errors="coerce").dt.hour

        df["hour"] = df["hour"].fillna(12)

        # ===================================================
        # ⏰ TIME ZONE
        # ===================================================
        df["time_zone"] = df["hour"].apply(classify_timezone)

        # ===================================================
        # 🌡️ TEMPERATURE
        # ===================================================
        if temp_col:
            df["temperature"] = clean_numeric(df[temp_col]).fillna(25)
        else:
            df["temperature"] = 25

        # ===================================================
        # 💨 WIND SPEED
        # ===================================================
        if wind_col:
            df["wind_speed"] = clean_numeric(df[wind_col]).fillna(15)
        else:
            df["wind_speed"] = 15

        # ===================================================
        # 🔢 QUANTITY
        # ===================================================
        if quantity_col:
            df["quantity"] = clean_numeric(df[quantity_col]).fillna(1)
        else:
            df["quantity"] = 1

        # ===================================================
        # 🌤️ WEATHER (prediction)
        # ===================================================
        def predict_weather(temp):
            if temp >= 30:
                return "Hot"
            elif temp >= 20:
                return "Sunny"
            else:
                return "Cold"

        df["normalized_weather"] = df["temperature"].apply(predict_weather)

        # ===================================================
        # 🍂 SEASON
        # ===================================================
        def get_season(date):
            if pd.isna(date):
                return "Unknown"
            m = date.month
            if m in [12, 1, 2]:
                return "Winter"
            elif m in [3, 4, 5]:
                return "Spring"
            elif m in [6, 7, 8]:
                return "Summer"
            else:
                return "Autumn"

        df["season"] = df["date"].apply(get_season)

        # ===================================================
        # 🎉 OCCASION
        # ===================================================
        df["occasion"] = "Normal Day"

        # ===================================================
        # 💾 حفظ
        # ===================================================
        global_data = df.copy()

        # ===================================================
        # 📦 OUTPUT (10 rows)
        # ===================================================
        preview_cols = [
            "date",
            "time",
            "season",
            "occasion",
            "normalized_weather",
            "temperature",
            "wind_speed",
            "quantity",
            "time_zone"
        ]

        return {
            "sample_preview": df[preview_cols].head(10).to_dict(orient="records")
        }

    except Exception as e:
        return {
            "error": str(e),
            "columns_found": list(df.columns) if 'df' in locals() else []
        }

# ===================================================
# 📊 2) ANALYTICS HELPERS
# ===================================================
def filter_by_date(df, start_date, end_date):
    if start_date:
        df = df[df["parsed_date"] >= pd.to_datetime(start_date)]
    if end_date:
        df = df[df["parsed_date"] <= pd.to_datetime(end_date)]
    return df


# ===================================================
# 📈 ANALYTICS
# ===================================================
@app.get("/analytics/total-revenue", tags=["2-Analytics"])
def total_revenue(
    start_date: str = Query(None),
    end_date: str = Query(None)
):
    if global_data is None:
        return {"error": "Upload first"}

    try:
        df = global_data.copy()

        # ===================================================
        # 📅 التاريخ
        # ===================================================
        date_col = None
        if "parsed_date" in df.columns:
            date_col = "parsed_date"
        elif "date" in df.columns:
            df["parsed_date"] = pd.to_datetime(df["date"], errors="coerce")
            date_col = "parsed_date"

        if date_col:
            if start_date:
                df = df[df[date_col] >= pd.to_datetime(start_date)]
            if end_date:
                df = df[df[date_col] <= pd.to_datetime(end_date)]

        # ===================================================
        # 🔢 الكمية
        # ===================================================
        if "quantity" not in df.columns:
            df["quantity"] = 1

        # ===================================================
        # 💰 السعر
        # ===================================================
        if "unit_price" not in df.columns:
            price_col = smart_find_col(df, ["price", "amount", "money"])

            if price_col:
                df["unit_price"] = clean_numeric(df[price_col]).fillna(10)
            else:
                df["unit_price"] = 10

        # ===================================================
        # 💰 الإيراد
        # ===================================================
        total = (df["quantity"] * df["unit_price"]).sum()

        return {"total_revenue": float(total)}

    except Exception as e:
        return {"error": str(e), "columns": list(global_data.columns)}


@app.get("/analytics/top-products", tags=["2-Analytics"])
def top_products(
    start_date: str = Query(None),
    end_date: str = Query(None)
):
    if global_data is None:
        return {"error": "Upload first"}

    try:
        df = global_data.copy()

        # ===================================================
        # 📅 فلترة بالتاريخ
        # ===================================================
        if "parsed_date" in df.columns:
            if start_date:
                df = df[df["parsed_date"] >= pd.to_datetime(start_date)]
            if end_date:
                df = df[df["parsed_date"] <= pd.to_datetime(end_date)]

        # ===================================================
        # 🧠 Smart Product Detection (نفس المنيو)
        # ===================================================
        product_col = None

        # 1) محاولة بالاسم (ذكية)
        priority_keywords = ["product", "item", "name", "coffee", "drink"]

        for col in df.columns:
            col_lower = str(col).lower()

            if any(x in col_lower for x in ["customer", "invoice", "id"]):
                continue

            if any(k in col_lower for k in priority_keywords):
                product_col = col
                break

        # 2) تحليل القيم
        if not product_col:
            best_score = 0

            for col in df.columns:
                if df[col].dtype == "object":

                    unique_ratio = df[col].nunique() / len(df)

                    if unique_ratio > 0.9:
                        continue

                    if unique_ratio < 0.01:
                        continue

                    numeric_ratio = df[col].astype(str).str.contains(r'\d').mean()
                    if numeric_ratio > 0.6:
                        continue

                    score = 1 - unique_ratio

                    if score > best_score:
                        best_score = score
                        product_col = col

        # 3) fallback
        if not product_col:
            for col in df.columns:
                if df[col].dtype == "object" and df[col].nunique() > 1:
                    product_col = col
                    break

        if not product_col:
            return {
                "error": "Could not detect product column ❌",
                "columns_found": list(df.columns)
            }

        df["product_name"] = df[product_col].astype(str)

        # ===================================================
        # 🔢 الكمية
        # ===================================================
        if "quantity" not in df.columns:
            df["quantity"] = 1

        # ===================================================
        # 📊 Top Products
        # ===================================================
        result = (
            df.groupby("product_name")["quantity"]
            .sum()
            .sort_values(ascending=False)
            .head(10)
            .reset_index()
        )

        return [
            {
                "product_name": row["product_name"],
                "popularity": int(row["quantity"])
            }
            for _, row in result.iterrows()
        ]

    except Exception as e:
        return {
            "error": str(e),
            "columns_found": list(global_data.columns)
        }


@app.get("/analytics/revenue-by-day", tags=["2-Analytics"])
def revenue_by_day(
    start_date: str = Query(None),
    end_date: str = Query(None)
):
    if global_data is None:
        return {"error": "Upload first"}

    try:
        df = global_data.copy()

        # تاريخ
        if "parsed_date" not in df.columns:
            if "date" in df.columns:
                df["parsed_date"] = pd.to_datetime(df["date"], errors="coerce")
            else:
                return {"error": "No date column found ❌"}

        if start_date:
            df = df[df["parsed_date"] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df["parsed_date"] <= pd.to_datetime(end_date)]

        # كمية
        if "quantity" not in df.columns:
            df["quantity"] = 1

        # سعر
        if "unit_price" not in df.columns:
            price_col = smart_find_col(df, ["price", "amount", "money"])
            if price_col:
                df["unit_price"] = clean_numeric(df[price_col]).fillna(10)
            else:
                df["unit_price"] = 10

        df["revenue"] = df["quantity"] * df["unit_price"]

        result = (
            df.groupby(df["parsed_date"].dt.date)["revenue"]
            .sum()
            .reset_index()
        )

        return result.to_dict(orient="records")

    except Exception as e:
        return {"error": str(e), "columns": list(global_data.columns)}

# ===================================================
# 🧠 MENU ENGINEERING
# ===================================================
@app.get("/analytics/menu-engineering", tags=["2-Analytics"])
def menu_engineering(
    start_date: str = Query(None),
    end_date: str = Query(None)
):
    if global_data is None:
        return {"error": "Upload first"}

    try:
        df = global_data.copy()

        # ===================================================
        # 📅 فلترة بالتاريخ
        # ===================================================
        if "parsed_date" in df.columns:
            if start_date:
                df = df[df["parsed_date"] >= pd.to_datetime(start_date)]
            if end_date:
                df = df[df["parsed_date"] <= pd.to_datetime(end_date)]

        # ===================================================
        # 🧠 Smart Product Detection (ذكي جدًا)
        # ===================================================
        product_col = None

        # 1) محاولة بالاسم (لكن نستبعد customer)
        priority_keywords = ["product", "item", "name", "coffee", "drink"]

        for col in df.columns:
            col_lower = str(col).lower()

            if any(x in col_lower for x in ["customer", "invoice", "id"]):
                continue

            if any(k in col_lower for k in priority_keywords):
                product_col = col
                break

        # 2) تحليل القيم
        if not product_col:
            best_score = 0

            for col in df.columns:
                if df[col].dtype == "object":

                    unique_ratio = df[col].nunique() / len(df)

                    if unique_ratio > 0.9:
                        continue

                    if unique_ratio < 0.01:
                        continue

                    numeric_ratio = df[col].astype(str).str.contains(r'\d').mean()
                    if numeric_ratio > 0.6:
                        continue

                    score = 1 - unique_ratio

                    if score > best_score:
                        best_score = score
                        product_col = col

        # 3) fallback
        if not product_col:
            for col in df.columns:
                if df[col].dtype == "object" and df[col].nunique() > 1:
                    product_col = col
                    break

        if not product_col:
            return {
                "error": "Could not detect product column ❌",
                "columns_found": list(df.columns)
            }

        df["product_name"] = df[product_col].astype(str)

        # ===================================================
        # 🔢 الكمية
        # ===================================================
        if "quantity" not in df.columns:
            df["quantity"] = 1

        # ===================================================
        # 💰 السعر
        # ===================================================
        price_col = smart_find_col(df, ["price", "amount", "money"])

        if price_col:
            df["unit_price"] = clean_numeric(df[price_col]).fillna(10)
        else:
            df["unit_price"] = 10

        # ===================================================
        # 📊 الربح
        # ===================================================
        df["profit"] = df["quantity"] * df["unit_price"]

        summary = (
            df.groupby("product_name")
            .agg({
                "quantity": "sum",
                "profit": "sum"
            })
            .reset_index()
        )

        # ===================================================
        # ⭐ المتوسطات
        # ===================================================
        avg_profit = summary["profit"].mean()
        avg_quantity = summary["quantity"].mean()

        # ===================================================
        # 🧠 التصنيف
        # ===================================================
        def classify(row):
            if row["profit"] >= avg_profit and row["quantity"] >= avg_quantity:
                return "Star"
            elif row["profit"] >= avg_profit:
                return "Puzzle"
            elif row["quantity"] >= avg_quantity:
                return "Horse"
            else:
                return "Dog"

        summary["category"] = summary.apply(classify, axis=1)

        # ===================================================
        # 📦 الإخراج
        # ===================================================
        result = [
            {
                "product_name": row["product_name"],
                "popularity": int(row["quantity"]),
                "profit": round(float(row["profit"]), 2),
                "category": row["category"]
            }
            for _, row in summary.iterrows()
        ]

        return result

    except Exception as e:
        return {
            "error": str(e),
            "columns_found": list(global_data.columns)
        }

@app.get("/analytics/timezone", tags=["2-Analytics"])
def revenue_by_timezone():
    if global_data is None:
        return {"error": "Upload first"}

    try:
        df = global_data.copy()

        # ===================================================
        # ⏰ الساعة
        # ===================================================
        if "hour" not in df.columns:
            if "time" in df.columns:
                df["hour"] = pd.to_datetime(df["time"], errors="coerce").dt.hour
            elif "date" in df.columns:
                df["hour"] = pd.to_datetime(df["date"], errors="coerce").dt.hour
            else:
                df["hour"] = 12

        df["hour"] = df["hour"].fillna(12)

        def classify(hour):
            if 5 <= hour <= 11:
                return "morning"
            elif 12 <= hour <= 16:
                return "afternoon"
            elif 17 <= hour <= 21:
                return "evening"
            else:
                return "night"

        df["time_zone"] = df["hour"].apply(classify)

        # كمية
        if "quantity" not in df.columns:
            df["quantity"] = 1

        # سعر
        if "unit_price" not in df.columns:
            price_col = smart_find_col(df, ["price"])
            if price_col:
                df["unit_price"] = clean_numeric(df[price_col]).fillna(10)
            else:
                df["unit_price"] = 10

        df["revenue"] = df["quantity"] * df["unit_price"]

        result = (
            df.groupby("time_zone")["revenue"]
            .sum()
            .reset_index()
            .sort_values("revenue", ascending=False)
        )

        return result.to_dict(orient="records")

    except Exception as e:
        return {"error": str(e), "columns": list(global_data.columns)}


