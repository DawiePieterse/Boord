import json as _json
import re as _re
import threading
import time as _time
import urllib.request
from typing import Optional

from sqlmodel import Session, select

from models import SystemSetting

# iweathar.co.za's robots.txt disallows the default urllib User-Agent
# ("python-urllib/x.y" - it names that exact string), and rightly so given
# what a script with no UA at all usually is. Identify ourselves properly
# instead of impersonating a browser.
_IWEATHAR_USER_AGENT = "BoordFarmWeather/1.0 (+https://github.com/dawiepieterse/boord)"

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


def _ttl_cached(key, fetch) -> dict:
    now = _time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now < hit[0]:
            return hit[1]

    weather = fetch()

    ttl = _CACHE_TTL_SECONDS if weather else _CACHE_TTL_ON_FAILURE_SECONDS
    with _cache_lock:
        _cache[key] = (now + ttl, weather)
    return weather


def fetch_weather_cached(lat: float, lon: float) -> dict:
    return _ttl_cached((round(lat, 4), round(lon, 4)), lambda: fetch_weather(lat, lon))


def _iweathar_number(html: str, label: str) -> Optional[float]:
    """The first number in the numbers-styled cell following a field label
    on an iWeathar station display page, e.g. label="Temperature:" pulls the
    20 out of "...Temperature:...class='numbers'>20<font...". The page has
    no machine-readable API (see the display page's own "contact us for API
    access" notice) - this is screen-scraping a legacy HTML table, so it
    only asks for the handful of fields Boord actually uses and tolerates
    the rest of the markup changing around them."""
    match = _re.search(
        _re.escape(label) + r".*?class=['\"]numbers['\"][^>]*>\s*(-?[\d.]+)",
        html, _re.S)
    return float(match.group(1)) if match else None


def fetch_iweathar(station_id: str) -> dict:
    """Live conditions from an iWeathar (iweathar.co.za) station's public
    display page - a farm's own station, not a regional forecast.

    iWeathar doesn't publish a sky condition, only measurements, so
    "condition" here is a rough stand-in derived from today's rainfall
    rather than a real observation. Good enough for the header icon; not a
    substitute for the WMO code fetch_weather() gets from actual forecast
    data.
    """
    try:
        url = f"https://iweathar.co.za/display?s_id={station_id}"
        req = urllib.request.Request(url, headers={"User-Agent": _IWEATHAR_USER_AGENT})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode("iso-8859-1", errors="replace")
        temp = _iweathar_number(html, "Temperature:")
        humidity = _iweathar_number(html, "Humidity:")
        rain_today = _iweathar_number(html, "Rainfall Today:")
        condition = "Rain" if rain_today and rain_today > 0 else "Clear"
        return {"temp": temp, "humidity": humidity, "condition": condition}
    except Exception:
        return {}


def fetch_iweathar_cached(station_id: str) -> dict:
    return _ttl_cached(("iweathar", station_id), lambda: fetch_iweathar(station_id))


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
    return _coords_of(session.exec(select(SystemSetting)).first())


def _coords_of(settings: Optional[SystemSetting]) -> Optional[tuple]:
    if settings and settings.gps_lat is not None and settings.gps_lon is not None:
        return settings.gps_lat, settings.gps_lon
    return None


def weather_station_id(session: Session) -> Optional[str]:
    """The farm's iWeathar station id from Settings, or None if it isn't
    set. Blank/whitespace counts as unset, same as a station never having
    been configured."""
    return _station_of(session.exec(select(SystemSetting)).first())


def _station_of(settings: Optional[SystemSetting]) -> Optional[str]:
    station_id = settings.weather_station_id if settings else None
    return station_id.strip() if station_id and station_id.strip() else None


def farm_weather_configured(session: Session) -> bool:
    settings = session.exec(select(SystemSetting)).first()
    return _station_of(settings) is not None or _coords_of(settings) is not None


def current_farm_weather(session: Session) -> dict:
    """The farm's live weather from whichever source Settings configures.

    A real on-farm station is what a farm actually gets when it goes to the
    trouble of buying and registering one, so it wins over the GPS-based
    regional forecast whenever both are set - the forecast only kicks in as
    a fallback for a farm with no station of its own.
    """
    settings = session.exec(select(SystemSetting)).first()
    station_id = _station_of(settings)
    if station_id:
        return fetch_iweathar_cached(station_id)
    coords = _coords_of(settings)
    if coords:
        return fetch_weather_cached(*coords)
    return {}
