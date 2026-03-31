import pandas as pd
from prophet import Prophet
from sklearn.metrics import mean_absolute_error
import numpy as np

def hybrid_forecast():

    df = pd.read_excel("final_sales_with_season_and_occasion_2022_.xlsx")

    df['ds'] = pd.to_datetime(df['date'])

    full_dates = pd.date_range(start=df['ds'].min(), end=df['ds'].max(), freq='D')

    results = []

    daily_sales = df.groupby(['name','ds'])['quantity'].sum().reset_index()
    avg_sales = daily_sales.groupby('name')['quantity'].mean()

    high_items = avg_sales[avg_sales >= 3].index
    low_items  = avg_sales[avg_sales < 3].index

    print("High-volume items:", len(high_items))
    print("Low-volume items:", len(low_items))

    overall_errors = []

  
    for product in high_items:

        product_df = df[df['name'] == product].copy()
        category = product_df['categ_EN'].iloc[0]

        product_df = product_df.groupby('ds')['quantity'].sum().reset_index()
        product_df = product_df.set_index('ds').reindex(full_dates, fill_value=0).reset_index()
        product_df.columns = ['ds','y']

        if len(product_df) < 10:
            continue

        split = int(len(product_df) * 0.8)
        train = product_df[:split]
        test  = product_df[split:]

        model = Prophet()
        model.fit(train)

        forecast_test = model.predict(test)

        y_true = test['y'].values
        y_pred = forecast_test['yhat'].values

      
        mask = y_true > 0
        y_true = y_true[mask]
        y_pred = y_pred[mask]

        mae = mean_absolute_error(y_true, y_pred)
        avg_actual = np.mean(y_true)
        error_percentage = (mae / avg_actual) * 100

        overall_errors.append(error_percentage)

        print(f"{product} MAE: {round(mae,2)}")
        print(f"{product} Error %: {round(error_percentage,1)}%\n")

      
        future = model.make_future_dataframe(periods=7)
        forecast = model.predict(future)

        future_preds = forecast[['ds','yhat','yhat_lower','yhat_upper']].tail(7)

        for _, row in future_preds.iterrows():
            results.append({
                "product": product,
                "category": category,
                "date": row['ds'],
                "predicted_sales": max(row['yhat'], 0),  # clip
                "lower_bound": max(row['yhat_lower'], 0),
                "upper_bound": max(row['yhat_upper'], 0),
                "type": "high_item_model",
                "MAE": mae,
                "Error_%": error_percentage
            })

  
    for category in df['categ_EN'].unique():

        cat_df = df[df['categ_EN'] == category].copy()
        cat_df = cat_df[cat_df['name'].isin(low_items)]

        if cat_df.empty:
            continue

        cat_daily = cat_df.groupby('ds')['quantity'].sum().reset_index()
        cat_daily = cat_daily.set_index('ds').reindex(full_dates, fill_value=0).reset_index()
        cat_daily.columns = ['ds','y']

        if len(cat_daily) < 10:
            continue

        split = int(len(cat_daily) * 0.8)
        train = cat_daily[:split]
        test  = cat_daily[split:]

        model = Prophet()
        model.fit(train)

        forecast_test = model.predict(test)

        y_true = test['y'].values
        y_pred = forecast_test['yhat'].values

     
        mask = y_true > 0
        y_true = y_true[mask]
        y_pred = y_pred[mask]

        mae = mean_absolute_error(y_true, y_pred)
        avg_actual = np.mean(y_true)
        error_percentage = (mae / avg_actual) * 100

        overall_errors.append(error_percentage)

        print(f"{category} MAE: {round(mae,2)}")
        print(f"{category} Error %: {round(error_percentage,1)}%\n")

        future = model.make_future_dataframe(periods=7)
        forecast = model.predict(future)

        future_preds = forecast[['ds','yhat','yhat_lower','yhat_upper']].tail(7)

        product_dist = cat_df.groupby('name')['quantity'].sum()
        product_dist = product_dist / product_dist.sum()

        for _, row in future_preds.iterrows():
            for product, ratio in product_dist.items():

                results.append({
                    "product": product,
                    "category": category,
                    "date": row['ds'],
                    "predicted_sales": max(row['yhat'] * ratio, 0),  # clip
                    "lower_bound": max(row['yhat_lower'], 0),
                    "upper_bound": max(row['yhat_upper'], 0),
                    "type": "category_distribution",
                    "MAE": mae,
                    "Error_%": error_percentage
                })


    final_df = pd.DataFrame(results)

    final_df = final_df.sort_values(by=['product','date']).reset_index(drop=True)

    final_df.to_csv("hybrid_forecast.csv", index=False)

    print("Saved: hybrid_forecast.csv")

    if overall_errors:
        overall_avg = sum(overall_errors) / len(overall_errors)
        print("\nOverall Error %:", round(overall_avg,1), "%")

    return final_df



results = hybrid_forecast()

summary = results.groupby('product')['predicted_sales'].sum().reset_index()
summary = summary.sort_values(by='predicted_sales', ascending=False)

print("\nTop products (7-day forecast):")
print(summary.head(10))

summary.to_csv("top_products_7days.csv", index=False)

print("\nSample predictions:")
print(results.head(10))