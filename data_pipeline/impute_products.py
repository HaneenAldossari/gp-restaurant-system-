import pandas as pd
import numpy as np
import requests
import warnings
warnings.filterwarnings('ignore')

df = pd.read_excel('final_sales_with_season_and_occasion_2022_.xlsx')
df['date'] = pd.to_datetime(df['date'])
df['day_of_week'] = df['date'].dt.day_name()
df['is_imputed'] = False
df['imputation_type'] = 'Original'
df.drop(columns=['Unnamed: 22'], inplace=True, errors='ignore')

closure_dates = (
    [f'2022-02-{d:02d}' for d in range(20, 29)] +
    [f'2022-05-{d:02d}' for d in range(2, 11)]
)
missing_dates = (
    [f'2022-05-{d:02d}' for d in range(29, 32)] +
    [f'2022-07-{d:02d}' for d in range(25, 32)] +
    [f'2022-08-{d:02d}' for d in range(23, 32)] +
    [f'2022-09-{d:02d}' for d in range(25, 31)] +
    [f'2022-10-{d:02d}' for d in range(1, 17)]  +
    [f'2022-11-{d:02d}' for d in range(1, 26)]
)
all_missing = closure_dates + missing_dates

def assign_season(date):
    m = date.month
    if m in [12,1,2]:  return 'Winter'
    elif m in [3,4,5]: return 'Spring'
    elif m in [6,7,8]: return 'Summer'
    else:              return 'Autumn'

def assign_occasion(date):
    if date.day_name() in ['Friday','Saturday']:
        return 'Weekend'
    return 'Normal Day'

product_info = df.groupby('name').agg(
    sku            = ('sku',            'first'),
    name_localized = ('name_localized', 'first'),
    categ_EN       = ('categ_EN',       'first'),
    categ_AR       = ('categ_AR',       'first'),
).reset_index()
product_info_dict = product_info.set_index('name').to_dict('index')

print("Fetching weather data...")
try:
    url    = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": 24.6877, "longitude": 46.7219,
        "start_date": "2022-01-01", "end_date": "2022-12-27",
        "daily": ["temperature_2m_max","temperature_2m_min",
                  "precipitation_sum","windspeed_10m_max","weathercode"],
        "timezone": "Asia/Riyadh"
    }
    r   = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    raw = r.json()['daily']
    wmo = {
        0:'Clear Sky', 1:'Mainly Clear', 2:'Partly Cloudy', 3:'Overcast',
        51:'Light Drizzle', 53:'Moderate Drizzle', 55:'Dense Drizzle',
        61:'Slight Rain', 63:'Moderate Rain', 65:'Heavy Rain', 95:'Thunderstorm'
    }
    weather_df = pd.DataFrame({
        'date':              pd.to_datetime(raw['time']),
        'max_temp':          raw['temperature_2m_max'],
        'min_temp':          raw['temperature_2m_min'],
        'precipitation':     raw['precipitation_sum'],
        'windspeed':         raw['windspeed_10m_max'],
        'weather_condition': [wmo.get(int(c), f'Code {int(c)}') for c in raw['weathercode']]
    })
    weather_dict = weather_df.set_index('date').to_dict('index')
    print("  Done")
except Exception as e:
    print(f"  Failed: {e}")
    weather_dict = {}

agg = df.groupby(['date','name','time_period','occasion','season','day_of_week','unit_price','unit_cost']).agg(
    quantity    = ('quantity',    'sum'),
    total_price = ('total_price', 'sum'),
    total_cost  = ('total_cost',  'sum'),
).reset_index()

time_periods  = ['morning', 'Afternoon', 'Evening', 'night']
products      = df['name'].unique()
imputed_rows  = []
skipped       = 0
imputed_count = 0

print(f"Products: {len(products)} | Missing days: {len(all_missing)} | Time periods: {len(time_periods)}")
print("Processing...")

for date_str in all_missing:
    target_date   = pd.to_datetime(date_str)
    target_occ    = assign_occasion(target_date)
    target_dow    = target_date.day_name()
    target_season = assign_season(target_date)
    imp_type      = 'Planned Closure' if date_str in closure_dates else 'Missing Data'
    w             = weather_dict.get(target_date, {})

    for product in products:
        prod_data = agg[agg['name'] == product]
        if prod_data.empty:
            continue
        unit_price = prod_data['unit_price'].iloc[-1]
        unit_cost  = prod_data['unit_cost'].iloc[-1]
        pinfo      = product_info_dict.get(product, {})

        for tp in time_periods:
            available = agg[
                (agg['name']        == product) &
                (agg['time_period'] == tp) &
                (agg['occasion']    == target_occ) &
                (agg['day_of_week'] == target_dow)
            ]
            before = available[available['date'] < target_date]
            after  = available[available['date'] > target_date]

            if before.empty and after.empty:
                skipped += 1
                continue
            elif before.empty:
                qty = after.iloc[0]['quantity']
            elif after.empty:
                qty = before.iloc[-1]['quantity']
            else:
                qty = round((before.iloc[-1]['quantity'] + after.iloc[0]['quantity']) / 2, 0)

            qty = int(qty)

            imputed_rows.append({
                'order_reference':   None,
                'customer_name':     None,
                'sku':               pinfo.get('sku'),
                'name':              product,
                'name_localized':    pinfo.get('name_localized'),
                'quantity':          qty,
                'categ_EN':          pinfo.get('categ_EN'),
                'categ_AR':          pinfo.get('categ_AR'),
                'unit_price':        unit_price,
                'unit_cost':         unit_cost,
                'total_price':       round(qty * unit_price, 2),
                'total_cost':        round(qty * unit_cost,  4),
                'created_at':        None,
                'time':              None,
                'date':              target_date,
                'season':            target_season,
                'occasion':          target_occ,
                'max_temp':          w.get('max_temp'),
                'min_temp':          w.get('min_temp'),
                'precipitation':     w.get('precipitation'),
                'windspeed':         w.get('windspeed'),
                'weather_condition': w.get('weather_condition'),
                'time_period':       tp,
                'day_of_week':       target_dow,
                'is_imputed':        True,
                'imputation_type':   imp_type,
            })
            imputed_count += 1

print(f"Imputed: {imputed_count} | Skipped: {skipped}")

imputed_df = pd.DataFrame(imputed_rows)
final_df   = pd.concat([df, imputed_df], ignore_index=True)
final_df   = final_df.sort_values(['date','name','time_period']).reset_index(drop=True)

print(f"\nOriginal rows  : {len(df)}")
print(f"Imputed rows   : {len(imputed_df)}")
print(f"Total rows     : {len(final_df)}")
print(f"Unique days    : {final_df['date'].nunique()}")
print(f"Null quantity  : {final_df['quantity'].isna().sum()}")
print(f"Null max_temp  : {final_df['max_temp'].isna().sum()}")
print(f"Price mismatch : {((final_df['total_price'] - (final_df['quantity'] * final_df['unit_price'])).abs() > 0.01).sum()}")

final_df.to_excel('product_sales_imputed_FINAL.xlsx', index=False)
print("Saved: product_sales_imputed_FINAL.xlsx")
