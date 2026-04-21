import pandas as pd
from prophet import Prophet
import numpy as np
import warnings
warnings.filterwarnings('ignore')

df = pd.read_excel("product_sales_imputed_FINAL.xlsx")
df['date'] = pd.to_datetime(df['date'])
df.drop(columns=['day_of_week'], inplace=True, errors='ignore')

product_sales = df.groupby('name')['quantity'].sum().sort_values(ascending=False)
top_product_names = product_sales.index.tolist()

time_order   = ['morning', 'Afternoon', 'Evening', 'night']
time_mapping = {k: v for v, k in enumerate(time_order)}

all_predictions = []

for product in top_product_names:
    print(f"Processing: {product}")

    product_df = df[df['name'] == product]

    data = product_df.groupby(['date', 'time_period'], as_index=False).agg({
        'quantity': 'sum',
        'season':   'first',
        'occasion': 'first'
    })
    data.columns = ['ds', 'time_period', 'y', 'season', 'occasion']

    all_dates  = pd.date_range(data['ds'].min(), data['ds'].max())
    full_index = pd.MultiIndex.from_product(
        [all_dates, time_order],
        names=['ds', 'time_period']
    )

    data = data.drop_duplicates(subset=['ds', 'time_period'])
    data = data.set_index(['ds', 'time_period']).reindex(full_index).reset_index()

    data['y'] = data['y'].fillna(0)
    data[['season', 'occasion']] = data[['season', 'occasion']].ffill().bfill()
    data = data.dropna(subset=['season', 'occasion'])

    data['season']           = data['season'].astype('category').cat.codes
    data['occasion']         = data['occasion'].astype('category').cat.codes
    data['time_period_code'] = data['time_period'].map(time_mapping)
    data = data.dropna(subset=['y', 'season', 'occasion', 'time_period_code'])

    if len(data) < 10:
        print(f"Skipped {product} (not enough data)")
        continue

    model = Prophet(
        daily_seasonality  = True,
        weekly_seasonality = True,
        yearly_seasonality = False
    )
    model.add_regressor('season')
    model.add_regressor('occasion')
    model.add_regressor('time_period_code')

    try:
        model.fit(data[['ds', 'y', 'season', 'occasion', 'time_period_code']])
    except:
        print(f"Model failed for {product}")
        continue

    future_dates = pd.date_range(
        start = data['ds'].min(),
        end   = data['ds'].max() + pd.Timedelta(days=30)
    )
    future = pd.MultiIndex.from_product(
        [future_dates, time_order],
        names=['ds', 'time_period']
    ).to_frame(index=False)

    future['time_period_code'] = future['time_period'].map(time_mapping)
    future['season']           = data['season'].iloc[-1]
    future['occasion']         = data['occasion'].iloc[-1]

    forecast = model.predict(future)
    forecast['time_period'] = future['time_period']

    merged = forecast.merge(
        data[['ds', 'time_period', 'y']],
        on=['ds', 'time_period'],
        how='left'
    )
    merged['product'] = product
    merged['type']    = merged['y'].apply(lambda x: 'actual' if pd.notna(x) else 'future')
    merged['yhat']    = merged['yhat'].round().astype(int)

    pct_error = (abs(merged['y'] - merged['yhat']) / merged['y']) * 100
    pct_error = pct_error.replace([np.inf, -np.inf], np.nan)
    merged['percentage_error'] = pct_error.round(0).astype('Int64').astype(str) + '%'

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
predictions_df = predictions_df.sort_values(by=['product', 'ds', 'time_period'])

actual_data = predictions_df[predictions_df['type'] == 'actual']
mae_df = actual_data.groupby('product').apply(
    lambda x: abs(x['y'] - x['yhat']).mean()
).reset_index()
mae_df.columns = ['product', 'MAE']
mae_df['MAE']  = mae_df['MAE'].round(2)

predictions_df = predictions_df.merge(mae_df, on='product', how='left')
predictions_df.to_csv("all_products_predictions_imputed.csv", index=False)
print("Saved: all_products_predictions_imputed.csv")

overall_mae  = actual_data.apply(lambda x: abs(x['y'] - x['yhat']), axis=1).mean()
actual_nonzero = actual_data[actual_data['y'] > 0]
overall_mape = (abs(actual_nonzero['y'] - actual_nonzero['yhat']) / actual_nonzero['y'] * 100).mean()

print(f"\nOverall MAE  : {overall_mae:.2f}")
print(f"Overall MAPE : {overall_mape:.2f}%")
print("Done!")
