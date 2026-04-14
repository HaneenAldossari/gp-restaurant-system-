"""
Prophet Hybrid Forecasting Model — Noura Aldossari

Core ML logic unchanged. Only modifications vs the original standalone script:
  * The data source is a DataFrame parameter instead of pd.read_excel(...)
  * Everything is wrapped in run_forecast(df) so the API can call it
  * CSV writes are controlled by a `save_csv` flag (default False)
  * Running this file directly (python prophet_model.py) still reads the Excel
    and writes the CSVs, exactly like before.
"""
import pandas as pd
from prophet import Prophet
import numpy as np


def run_forecast(df: pd.DataFrame, save_csv: bool = False) -> pd.DataFrame:
    """
    Run Noura's hybrid Prophet forecast.

    Expected input columns: name, date, quantity, season, occasion, time_period.
    """
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])

    # =========================
    # ترتيب المنتجات (الأكثر مبيعًا)
    # =========================
    product_sales = df.groupby('name')['quantity'].sum().sort_values(ascending=False)
    top_product_names = product_sales.index.tolist()

    # =========================
    # ترتيب الفترات
    # =========================
    time_order = ['morning', 'Afternoon', 'Evening', 'night']
    time_mapping = {k: v for v, k in enumerate(time_order)}

    all_predictions = []

    # =========================
    # Loop
    # =========================
    for product in top_product_names:

        print(f"Processing: {product}")

        product_df = df[df['name'] == product]

        # =========================
        # تجميع
        # =========================
        data = product_df.groupby(['date', 'time_period'], as_index=False).agg({
            'quantity': 'sum',
            'season': 'first',
            'occasion': 'first'
        })

        data.columns = ['ds', 'time_period', 'y', 'season', 'occasion']

        # =========================
        # إنشاء كل الفترات لكل يوم
        # =========================
        all_dates = pd.date_range(data['ds'].min(), data['ds'].max())

        full_index = pd.MultiIndex.from_product(
            [all_dates, time_order],
            names=['ds', 'time_period']
        )

        data = data.drop_duplicates(subset=['ds', 'time_period'])
        data = data.set_index(['ds', 'time_period']).reindex(full_index).reset_index()

        # =========================
        # 🔥 تنظيف البيانات (أهم شيء)
        # =========================
        data['y'] = data['y'].fillna(0)

        data[['season', 'occasion']] = data[['season', 'occasion']].ffill().bfill()

        # حذف أي NaN متبقي
        data = data.dropna(subset=['season', 'occasion'])

        # =========================
        # تحويل للمودل
        # =========================
        data['season'] = data['season'].astype('category').cat.codes
        data['occasion'] = data['occasion'].astype('category').cat.codes

        data['time_period_code'] = data['time_period'].map(time_mapping)

        # حذف أي صف فيه مشكلة
        data = data.dropna(subset=['y', 'season', 'occasion', 'time_period_code'])

        # =========================
        # تأكد البيانات كافية
        # =========================
        if len(data) < 10:
            print(f"❌ Skipped {product} (data too small)")
            continue

        # =========================
        # Model (🔥 مستقر)
        # =========================
        model = Prophet(
            daily_seasonality=True,
            weekly_seasonality=True,
            yearly_seasonality=False
        )

        model.add_regressor('season')
        model.add_regressor('occasion')
        model.add_regressor('time_period_code')

        try:
            model.fit(data[['ds', 'y', 'season', 'occasion', 'time_period_code']])
        except Exception:
            print(f"❌ Model failed for {product}")
            continue

        # =========================
        # Future
        # =========================
      # 🔥 نفس فكرة training (date × time_period)
        future_dates = pd.date_range(
          start=data['ds'].min(),
          end=data['ds'].max() + pd.Timedelta(days=30)
    )

        future = pd.MultiIndex.from_product(
          [future_dates, time_order],
          names=['ds', 'time_period']
         ).to_frame(index=False)

        future['time_period_code'] = future['time_period'].map(time_mapping)
        future['season'] = data['season'].iloc[-1]
        future['occasion'] = data['occasion'].iloc[-1]

        # =========================
        # Predict
        # =========================
        forecast = model.predict(future)
        forecast['time_period'] = future['time_period']

        # =========================
        # Merge
        # =========================
        merged = forecast.merge(
            data[['ds', 'time_period', 'y']],
            on=['ds', 'time_period'],
            how='left'
        )

        merged['product'] = product

        merged['type'] = merged['y'].apply(
            lambda x: 'actual' if pd.notna(x) else 'future'
        )

        merged['yhat'] = merged['yhat'].round().astype(int)

        percentage_error = (
            abs(merged['y'] - merged['yhat']) / merged['y']
        ) * 100

        percentage_error = percentage_error.replace([np.inf, -np.inf], np.nan)

        merged['percentage_error'] = percentage_error.round(0).astype('Int64').astype(str) + '%'

        merged = merged.drop_duplicates(subset=['ds', 'product', 'time_period'])

        all_predictions.append(
            merged[['ds', 'yhat', 'y', 'product', 'type', 'percentage_error', 'time_period']]
        )

    # =========================
    # Save
    # =========================
    predictions_df = pd.concat(all_predictions)

    predictions_df['time_period'] = pd.Categorical(
        predictions_df['time_period'],
        categories=time_order,
        ordered=True
    )

    predictions_df['product'] = pd.Categorical(
        predictions_df['product'],
        categories=top_product_names,
        ordered=True
    )

    predictions_df = predictions_df.sort_values(
        by=['product', 'ds', 'time_period']
    )
    # =========================
    # 🔥 إضافة MAE داخل الملف
    # =========================

    # نحسب MAE من البيانات الفعلية فقط
    actual_data = predictions_df[predictions_df['type'] == 'actual']

    mae_df = actual_data.groupby('product').apply(
        lambda x: abs(x['y'] - x['yhat']).mean()
    ).reset_index()

    mae_df.columns = ['product', 'MAE']

    # تقريب
    mae_df['MAE'] = mae_df['MAE'].round(2)

    # دمجه مع الملف الأساسي
    predictions_df = predictions_df.merge(mae_df, on='product', how='left')

    if save_csv:
        predictions_df.to_csv("all_products_predictions.csv", index=False)
        print("✅ Done 100% بدون Errors 🔥")

        # =========================
        # 🔥 Best Time per Product
        # =========================

        future_data = predictions_df[predictions_df['type'] == 'future']

        best_time = future_data.groupby(['product', 'time_period'])['yhat'].mean().reset_index()

        best_time = best_time.sort_values(['product', 'yhat'], ascending=[True, False])

        best_time = best_time.drop_duplicates('product')

        # ✅ التقريب هنا
        best_time['yhat'] = best_time['yhat'].round().astype(int)

        best_time.to_csv("best_time_per_product.csv", index=False)

        print("✅ Best time file created!")

        # =========================
        # 🔥 Future Predictions Only
        # =========================

        # نأخذ فقط التنبؤات (future)
        future_only = predictions_df[predictions_df['type'] == 'future']

        # اختيار الأعمدة المهمة
        future_only = future_only[['ds', 'product', 'time_period', 'yhat']]

        # إعادة تسمية العمود
        future_only.rename(columns={'yhat': 'predicted_quantity'}, inplace=True)

        # تقريب القيم
        future_only['predicted_quantity'] = future_only['predicted_quantity'].round().astype(int)

        # ترتيب البيانات (🔥 مهم)
        future_only['time_period'] = pd.Categorical(
            future_only['time_period'],
            categories=['morning', 'Afternoon', 'Evening', 'night'],
            ordered=True
        )

        future_only = future_only.sort_values(by=['product', 'ds', 'time_period'])

        # حفظ الملف
        future_only.to_csv("future_predictions_only.csv", index=False)

        print("✅ Future predictions file created!")

        # =========================
        # 🔥 Best Month per Product
        # =========================

        # نستخدم التنبؤ فقط
        future_data = predictions_df[predictions_df['type'] == 'future']

        # استخراج الشهر من التاريخ
        future_data['month'] = pd.to_datetime(future_data['ds']).dt.month

        # حساب مجموع المبيعات لكل شهر لكل منتج
        monthly_sales = future_data.groupby(['product', 'month'])['yhat'].sum().reset_index()

        # ترتيب من الأعلى
        monthly_sales = monthly_sales.sort_values(['product', 'yhat'], ascending=[True, False])

        # اختيار أفضل شهر لكل منتج
        best_month = monthly_sales.drop_duplicates('product')

        # تقريب القيم
        best_month['yhat'] = best_month['yhat'].round().astype(int)

        # تغيير أسماء الأعمدة
        best_month.rename(columns={
            'month': 'best_month',
            'yhat': 'total_predicted_sales'
        }, inplace=True)

        # حفظ الملف
        best_month.to_csv("best_month_per_product.csv", index=False)

        print("✅ Best month file created!")

    return predictions_df


# =========================
# Standalone execution (original behaviour: read Excel, write CSVs)
# =========================
if __name__ == "__main__":
    df = pd.read_excel("final_sales_with_season_and_occasion_2022_.xlsx")
    run_forecast(df, save_csv=True)
