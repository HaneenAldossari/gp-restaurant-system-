import pandas as pd
from prophet import Prophet

def run_prophet_model():
    print("Model is runing...")
    df = pd.read_excel("daily_sales.xlsx") 



    df['ds'] = pd.to_datetime(df['date'])
    df['y'] = df['revenue']

    df = df[['ds','y',
         'max_temp',
         'min_temp',
         'precipitation',
         'windspeed',
         'is_closed']]
    df=df.sort_values('ds').reset_index(drop=True)

    TEST_DAYS = 30

    cutoff = df['ds'].max() - pd.Timedelta(days=TEST_DAYS)

    train = df[df['ds'] <= cutoff].copy()
    test  = df[df['ds'] >  cutoff].copy()

    model = Prophet(
    yearly_seasonality=True,
    weekly_seasonality=True,
    daily_seasonality=False,
    seasonality_mode='multiplicative',
    changepoint_prior_scale=0.1
)

    model.add_seasonality(
    name='monthly',
    period=30.5,
    fourier_order=5
)


    model.add_regressor('max_temp')
    model.add_regressor('min_temp')
    model.add_regressor('precipitation')
    model.add_regressor('windspeed')
    model.add_regressor('is_closed')

    model.fit(train)

    forecast_test = model.predict(test)

    predictions = forecast_test[['ds','yhat']]
    print("Training Done ")

 

    

    from sklearn.metrics import mean_absolute_error, mean_squared_error
    import numpy as np


    y_true = test['y'].values
    y_pred = predictions['yhat'].values

    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))

    print("MAE:", mae)
    print("RMSE:", rmse)

    import matplotlib.pyplot as plt

# رسم التوقع الكامل
    future = model.make_future_dataframe(periods=30)

    future = future.merge(
    df[['ds','max_temp','min_temp','precipitation','windspeed','is_closed']],
    on='ds',
    how='left'
)

    forecast_full = model.predict(future)

    import matplotlib.pyplot as plt

# ناخذ جزء التاريخ الكامل مع المستقبل
    forecast_full = model.predict(future)

    plt.figure(figsize=(14,6))



# النقاط الحقيقية
    plt.scatter(train['ds'], train['y'], 
            color='blue', 
            s=20, 
            label='Actual(train)')

# خط التوقع
    plt.plot(forecast_full['ds'], 
         forecast_full['yhat'], 
         color='darkblue', 
         label='Forecast ')

# منطقة عدم اليقين
    plt.fill_between(
    forecast_full['ds'],
    forecast_full['yhat_lower'],
    forecast_full['yhat_upper'],
    color='orange',
    alpha=0.3,
    label='Uncertainty'
)

    plt.xlabel("Date (ds)")
    plt.ylabel("Revenue")
    plt.title("Prophet Forecast (next 30 days) ")
    plt.legend()


    import matplotlib.dates as mdates
    from matplotlib.ticker import ScalarFormatter

    plt.gca().xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    plt.xticks(rotation=45)

    from matplotlib.ticker import FormatStrFormatter

    ax = plt.gca()
    ax.yaxis.set_major_formatter(FormatStrFormatter('%.0f'))

    plt.tight_layout()


    plt.show()

# مقارنة آخر 30 يوم
    plt.figure(figsize=(10,5))
    plt.plot(test['ds'], test['y'], label='Actual')
    plt.plot(predictions['ds'], predictions['yhat'], label='Predicted')
    plt.legend()
    plt.title("Actual vs Predicted (Last 30 Days)")

    plt.show()

    return predictions
results =run_prophet_model()
print(results )