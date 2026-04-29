"""
Daily weather lookup for forecasting regressors.

Fetches historical daily temperature for the configured Saudi city
(default: Riyadh) from Open-Meteo's free archive API and caches it
locally. Used by the Prophet pipeline so predictions can incorporate
real temperature as a regressor — colder days correlate with more hot
drinks, hotter days with more cold drinks.

For future forecast dates (where actual temperatures aren't known),
we fall back to the historical day-of-year average across whatever
data we've fetched. This is a reasonable proxy in Saudi where
day-to-day temperature variance within a season is small.

No external dependencies beyond `requests` (already installed for
psycopg2-binary's deps). API is rate-limited to 10k calls/day per IP,
which is fine for our use case (one fetch per training run).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger("gp.weather")

# Riyadh by default — change via WEATHER_LATITUDE / WEATHER_LONGITUDE
# env vars if your data is from a different Saudi city.
import os

LATITUDE = float(os.getenv("WEATHER_LATITUDE", "24.7136"))
LONGITUDE = float(os.getenv("WEATHER_LONGITUDE", "46.6753"))
CITY_LABEL = os.getenv("WEATHER_CITY", "Riyadh")

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_FILE = CACHE_DIR / f"weather_{CITY_LABEL}.json"


def _fetch_archive(start: str, end: str) -> dict | None:
    """Hit Open-Meteo's archive API. Returns the full JSON or None if
    the call fails (we don't want a transient network blip to break
    training — the route layer falls back to no-temperature mode)."""
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "start_date": start,
        "end_date": end,
        "daily": "temperature_2m_max,temperature_2m_min,temperature_2m_mean",
        "timezone": "Asia/Riyadh",
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("Open-Meteo fetch failed: %s", e)
        return None


def get_daily_temperatures(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Return a DataFrame with one row per date in [start, end] and a
    `temp_max` column. Uses the on-disk cache when possible; only hits
    the network when dates are missing.

    Returns columns: ds (datetime), temp_max (float, °C). Empty if the
    fetch fails entirely — the caller should handle this gracefully."""
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    needed = pd.date_range(start_ts, end_ts)

    cached: dict[str, float] = {}
    if CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text())
        except Exception:
            cached = {}

    # Decide what to fetch — anything in `needed` we don't have cached.
    missing = [d for d in needed if d.strftime("%Y-%m-%d") not in cached]
    # Open-Meteo only has historical data up to ~5 days ago. For dates
    # past today, we'll synthesise from a climate average (see below).
    today = pd.Timestamp.now().normalize()
    fetchable = [d for d in missing if d <= today - pd.Timedelta(days=5)]

    if fetchable:
        fetch_start = min(fetchable).strftime("%Y-%m-%d")
        fetch_end = max(fetchable).strftime("%Y-%m-%d")
        data = _fetch_archive(fetch_start, fetch_end)
        if data and "daily" in data:
            dates = data["daily"].get("time", [])
            tmax = data["daily"].get("temperature_2m_max", [])
            for d, t in zip(dates, tmax):
                if t is not None:
                    cached[d] = float(t)
            try:
                CACHE_FILE.write_text(json.dumps(cached))
            except Exception as e:
                log.warning("Could not cache weather data: %s", e)

    # Build the output frame. For dates we couldn't fetch (future),
    # fall back to the climate average for that month-day computed
    # from whatever historical data we DO have cached.
    if cached:
        cached_df = pd.DataFrame([
            {"ds": pd.Timestamp(d), "temp_max": v} for d, v in cached.items()
        ])
        cached_df["month_day"] = cached_df["ds"].dt.strftime("%m-%d")
        climate_avg = cached_df.groupby("month_day")["temp_max"].mean().to_dict()
    else:
        climate_avg = {}

    rows = []
    for d in needed:
        key = d.strftime("%Y-%m-%d")
        md_key = d.strftime("%m-%d")
        if key in cached:
            temp = cached[key]
        elif md_key in climate_avg:
            temp = climate_avg[md_key]
        else:
            temp = None  # caller must handle
        rows.append({"ds": d, "temp_max": temp})

    return pd.DataFrame(rows)
