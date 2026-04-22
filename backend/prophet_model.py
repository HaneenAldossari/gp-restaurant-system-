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
from hijri_converter import Gregorian
from sklearn.metrics import mean_absolute_error


def run_forecast(df: pd.DataFrame, save_csv: bool = False, horizon_days: int = 30) -> pd.DataFrame:

    #  تثبيت النتائج
    np.random.seed(42)

    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])

    # =========================
    # Season & Occasion
    # =========================
    def compute_season(d):
        m = d.month
        if m in [12, 1, 2]:
            return "Winter"
        elif m in [3, 4, 5]:
            return "Spring"
        elif m in [6, 7, 8]:
            return "Summer"
        else:
            return "Autumn"

    def compute_occasion(d):
        hijri = Gregorian(d.year, d.month, d.day).to_hijri()

        if hijri.month == 9:
            return "Ramadan"
        if hijri.month == 10 and 1 <= hijri.day <= 3:
            return "Eid al-Fitr"
        if hijri.month == 12 and 10 <= hijri.day <= 13:
            return "Eid al-Adha"
        if d.month == 9 and d.day == 23:
            return "Saudi National Day"
        if d.weekday() in [4, 5]:
            return "Weekend"

        return "Normal Day"

    # =========================
    # ترتيب المنتجات
    # =========================
    product_sales = df.groupby('name')['quantity'].sum().sort_values(ascending=False)
    top_product_names = product_sales.index.tolist()

    time_order = ['morning', 'Afternoon', 'Evening', 'night']
    time_mapping = {k: v for v, k in enumerate(time_order)}

    all_predictions = []

    # =========================
    # Loop
    # =========================
    for product in top_product_names:

        print(f"Processing: {product}")

        product_df = df[df['name'] == product]

        data = product_df.groupby(['date', 'time_period'], as_index=False).agg({
            'quantity': 'sum',
            'season': 'first',
            'occasion': 'first'
        })

        data.columns = ['ds', 'time_period', 'y', 'season', 'occasion']

        all_dates = pd.date_range(data['ds'].min(), data['ds'].max())

        full_index = pd.MultiIndex.from_product(
            [all_dates, time_order],
            names=['ds', 'time_period']
        )

        data = data.drop_duplicates(subset=['ds', 'time_period'])
        data = data.set_index(['ds', 'time_period']).reindex(full_index).reset_index()

        # =========================
        # تنظيف
        # =========================
        data['y'] = data['y'].fillna(0)
        data[['season', 'occasion']] = data[['season', 'occasion']].ffill().bfill()
        data = data.dropna(subset=['season', 'occasion'])

        # =========================
        # Encoding
        # =========================
        data['season'] = data['season'].astype('category').cat.codes
        data['occasion'] = data['occasion'].astype('category').cat.codes
        data['time_period_code'] = data['time_period'].map(time_mapping)

        data = data.dropna(subset=['y', 'season', 'occasion', 'time_period_code'])

        if len(data) < 10:
            print(f" Skipped {product} (data too small)")
            continue

        # =========================
        #  Train/Test Split
        # =========================
        split = int(len(data) * 0.8)
        train = data.iloc[:split].copy()
        test = data.iloc[split:].copy()

        # =========================
        # Model
        # =========================
        model = Prophet(
            daily_seasonality=True,
            weekly_seasonality=True,
            yearly_seasonality=False
        )

        model.add_regressor('season')
        model.add_regressor('occasion')
        model.add_regressor('time_period_code')

        model.fit(train[['ds', 'y', 'season', 'occasion', 'time_period_code']])

        # =========================
        #  MAE 
        # =========================
        test_forecast = model.predict(
            test[['ds', 'season', 'occasion', 'time_period_code']]
        )

        mae_test = mean_absolute_error(test['y'], test_forecast['yhat'])
        print(f"MAE (Test) for {product}: {round(mae_test, 2)}")

        # =========================
        # Future
        # =========================
        future_dates = pd.date_range(
            start=data['ds'].min(),
            end=data['ds'].max() + pd.Timedelta(days=horizon_days)
        )

        future = pd.MultiIndex.from_product(
            [future_dates, time_order],
            names=['ds', 'time_period']
        ).to_frame(index=False)

        future['time_period_code'] = future['time_period'].map(time_mapping)

        #  Dynamic features
        future['season'] = future['ds'].apply(compute_season)
        future['occasion'] = future['ds'].apply(compute_occasion)

        future['season'] = future['season'].astype('category').cat.codes
        future['occasion'] = future['occasion'].astype('category').cat.codes

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

    if save_csv:
        predictions_df.to_csv("all_products_predictions.csv", index=False)
        print(" Done  ")

    return predictions_df


if __name__ == "__main__":
    df = pd.read_excel("data/sample_sales_2022.xlsx")
    run_forecast(df, save_csv=True)
