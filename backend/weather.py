import json as _json
import threading
import time as _time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from models import SystemSetting, WeatherHistory

_WMO_CONDITION = {
    0: "Clear", 1: "Partly Cloudy", 2: "Partly Cloudy", 3: "Overcast",
    45: "Foggy", 48: "Foggy",
    51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
    61: "Rain", 63: "Rain", 65: "Heavy Rain",
    71: "Snow", 73: "Snow", 75: "Heavy Snow",
    80: "Showers", 81: "Showers", 82: "Heavy Showers",
    95: "Storm", 96: "Storm", 99: "Storm",
}


def fetch_weather(lat: float, lon: float) -> dict:
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,weather_code"
        )
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = _json.loads(resp.read())
        curr = data.get("current", {})
        code = int(curr.get("weather_code", 0))
        condition = _WMO_CONDITION.get(code, "Cloudy")
        return {
            "temp": curr.get("temperature_2m"),
            "humidity": curr.get("relative_humidity_2m"),
            "condition": condition,
        }
    except Exception:
        return {}


# A field device syncs a whole batch of crates at once and every crate gets
# stamped with the conditions (routers/sync.py), so an uncached lookup would
# mean one HTTP round trip per crate - hundreds on a busy morning, each one
# holding up the sync. The upstream service only refreshes every ~15 minutes,
# so a short cache costs nothing in accuracy. Failures are cached briefly too,
# so a dropped link doesn't stall every following crate on a 5s timeout.
_CACHE_TTL_SECONDS = 600
_CACHE_TTL_ON_FAILURE_SECONDS = 60
_cache: dict = {}
_cache_lock = threading.Lock()


def fetch_weather_cached(lat: float, lon: float) -> dict:
    key = (round(lat, 4), round(lon, 4))
    now = _time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now < hit[0]:
            return hit[1]

    weather = fetch_weather(lat, lon)

    ttl = _CACHE_TTL_SECONDS if weather else _CACHE_TTL_ON_FAILURE_SECONDS
    with _cache_lock:
        _cache[key] = (now + ttl, weather)
    return weather


# ---------------------------------------------------------------------------
# Historical weather (hourly backfill + Weather tab). Shared by
# scripts/import_historical_weather.py (wholesale replace, run by hand) and
# sync_recent_weather() below (append-only, run as a side effect of opening
# the Weather tab) so both go through one fetch/parse implementation.
# ---------------------------------------------------------------------------

HISTORY_START_DATE = "2020-01-01"
HOURLY_FIELDS = ",".join([
    "temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation",
    "weather_code", "wind_speed_10m", "soil_temperature_6cm", "uv_index",
    "sunshine_duration",
])

def farm_coords(session: Session) -> Optional[tuple]:
    """The farm's GPS position from Settings, or None if it isn't set yet.

    This used to fall back to a fixed pair of coordinates when Settings was
    blank. That was survivable while there was one farm, because the fallback
    WAS that farm. As a product it is a silent correctness bug: a farm that
    hasn't filled in its location gets a different farm's weather, and since
    the Risk indicator and Harvest Forecast are computed from that weather,
    they produce confident scores describing somewhere else entirely. Nothing
    errors, nothing looks wrong, and the numbers are simply about the wrong
    place.

    So there is no fallback. Every caller has to decide what to do with no
    location, and none of them is allowed to invent one.

    Note the `is not None` checks: a plain truthiness test treats latitude 0
    (the equator) and longitude 0 (Greenwich) as "unset".
    """
    settings = session.exec(select(SystemSetting)).first()
    if settings and settings.gps_lat is not None and settings.gps_lon is not None:
        return settings.gps_lat, settings.gps_lon
    return None


def fetch_historical_hourly(lat: float, lon: float, start_date: str, end_date: str, timeout: int = 120) -> dict:
    url = (
        "https://historical-forecast-api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&start_date={start_date}&end_date={end_date}"
        f"&hourly={HOURLY_FIELDS}&timezone=auto"
    )
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return _json.loads(resp.read())


# Sibling of fetch_historical_hourly() above, for dates before that API's own
# 2016-01-01 floor (HISTORY_START_DATE / scripts/import_historical_weather.py
# only ever asks it for 2020 onward, so that floor has never mattered until
# now). Hits Open-Meteo's separate reanalysis-based archive instead, which
# reaches back to 1940 - but doesn't carry soil_temperature_6cm or uv_index
# at any date (confirmed by hand: both come back all-null even for recent
# dates), so ARCHIVE_HOURLY_FIELDS omits them rather than requesting fields
# that can never be filled. Those two columns are simply NULL for any row
# this fetches - parse_hourly_rows() already pads missing hourly series with
# None, so no other change was needed to reuse it here.
ARCHIVE_HOURLY_FIELDS = ",".join([
    "temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation",
    "weather_code", "wind_speed_10m", "sunshine_duration",
])


def fetch_archive_hourly(lat: float, lon: float, start_date: str, end_date: str, timeout: int = 120) -> dict:
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}&start_date={start_date}&end_date={end_date}"
        f"&hourly={ARCHIVE_HOURLY_FIELDS}&timezone=auto"
    )
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return _json.loads(resp.read())


# Sibling of fetch_historical_hourly() above, for routers/risk.py's Harvest
# Forecast - but hits the REAL forecast host (not the historical-forecast
# one). Raises on failure rather than swallowing it, like
# fetch_historical_hourly() and unlike fetch_weather() below - the caller
# (build_harvest_forecast) decides the fallback, same split
# sync_recent_weather() already keeps around fetch_historical_hourly().
#
# `days` is Open-Meteo's forecast_days, capped at 16 and COUNTING TODAY -
# so days=16 returns today plus only 15 future days. Callers wanting N
# days ahead must ask for N+1 and must not assume the last requested day
# came back; routers/risk.py keeps that distinction explicit as
# FORECAST_API_DAYS vs FORECAST_HORIZON_DAYS.
def fetch_forecast_hourly(lat: float, lon: float, days: int = 16, timeout: int = 30) -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&forecast_days={days}"
        f"&hourly={HOURLY_FIELDS}&timezone=auto"
    )
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return _json.loads(resp.read())


def parse_hourly_rows(data: dict) -> list:
    """Open-Meteo's hourly response -> plain dicts shaped like WeatherHistory
    columns (not ORM objects), so callers can choose wholesale-replace
    (the import script) or dedupe-and-append (sync_recent_weather)."""
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])

    def series(name):
        values = hourly.get(name, [])
        return values + [None] * (len(times) - len(values))

    temp = series("temperature_2m")
    humidity = series("relative_humidity_2m")
    dew_point = series("dew_point_2m")
    precipitation = series("precipitation")
    weather_code = series("weather_code")
    wind_speed = series("wind_speed_10m")
    soil_temp = series("soil_temperature_6cm")
    uv_index = series("uv_index")
    sunshine = series("sunshine_duration")

    rows = []
    for i, t in enumerate(times):
        code = weather_code[i]
        rows.append({
            "timestamp": datetime.fromisoformat(t),
            "temp_c": temp[i],
            "humidity_pct": humidity[i],
            "dew_point_c": dew_point[i],
            "precipitation_mm": precipitation[i],
            "weather_code": int(code) if code is not None else None,
            "condition": _WMO_CONDITION.get(int(code), "Cloudy") if code is not None else "",
            "wind_speed_kmh": wind_speed[i],
            "soil_temp_6cm_c": soil_temp[i],
            "uv_index": uv_index[i],
            "sunshine_duration_s": sunshine[i],
        })
    return rows


def sync_recent_weather(session: Session) -> dict:
    """Best-effort catch-up: fetches whatever hours are missing since the
    last stored row and appends them (never replaces). Called as a side
    effect of loading the Weather tab, so a network hiccup here must never
    stop the tab from rendering whatever history is already stored - same
    "never block the caller" tone as fetch_weather() above.

    Data is hourly, so once the latest stored row already falls in the
    current hour there is nothing new to fetch - that's the whole throttle,
    no extra cache/state needed to stop repeat tab-opens hammering the API.

    Uses a short timeout, not fetch_historical_hourly()'s 120s default: this
    runs synchronously inside the Weather/Risk tab's request, which the
    frontend abandons after Boord.NETWORK_TIMEOUT_MS (8s, see shared/api.js) -
    a slow/dead connection must fail fast enough here that the endpoint can
    still return the already-stored data within that budget, rather than
    the tab hanging past it and reading as fully offline."""
    try:
        latest = session.exec(
            select(WeatherHistory.timestamp).order_by(WeatherHistory.timestamp.desc())
        ).first()
        now = datetime.now()
        if latest and latest >= now.replace(minute=0, second=0, microsecond=0):
            return {"synced": 0}

        start_date = latest.date().isoformat() if latest else HISTORY_START_DATE
        coords = farm_coords(session)
        if coords is None:
            # No location set: append nothing rather than guess. Callers
            # surface this as "set your farm location", not as an error.
            return {"synced": 0, "no_location": True}
        lat, lon = coords
        data = fetch_historical_hourly(lat, lon, start_date, now.date().isoformat(), timeout=3)
        rows = parse_hourly_rows(data)
        new_rows = [WeatherHistory(**r) for r in rows if latest is None or r["timestamp"] > latest]
        if new_rows:
            session.add_all(new_rows)
            session.commit()
        return {"synced": len(new_rows)}
    except Exception:
        session.rollback()
        return {"synced": 0, "error": True}
