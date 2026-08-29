import json as _json
import threading
import time as _time
import urllib.error
import urllib.request
from typing import Optional

from sqlmodel import Session, select

from models import SystemSetting

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


def farm_coords(session: Session) -> Optional[tuple]:
    """The farm's GPS position from Settings, or None if it isn't set yet.

    This used to fall back to a fixed pair of coordinates when Settings was
    blank. That was survivable while there was one farm, because the fallback
    WAS that farm. As a product it is a silent correctness bug: a farm that
    hasn't filled in its location gets a different farm's weather - in the
    header, and stamped onto every crate it dispatches. Nothing errors,
    nothing looks wrong, and the readings are simply about the wrong place.

    So there is no fallback. Every caller has to decide what to do with no
    location, and none of them is allowed to invent one.

    Note the `is not None` checks: a plain truthiness test treats latitude 0
    (the equator) and longitude 0 (Greenwich) as "unset".
    """
    settings = session.exec(select(SystemSetting)).first()
    if settings and settings.gps_lat is not None and settings.gps_lon is not None:
        return settings.gps_lat, settings.gps_lon
    return None
