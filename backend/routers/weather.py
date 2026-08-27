from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import Session, delete, select

from db import get_session
from models import HistoricalAnnualYield, HistoricalHarvest, WeatherHistory
from security import get_current_admin
from weather import (HISTORY_START_DATE, farm_coords, fetch_historical_hourly, fetch_weather,
                      parse_hourly_rows, sync_recent_weather)

router = APIRouter(prefix="/api/weather", tags=["weather"])


@router.get("/current")
def current_weather(session: Session = Depends(get_session)):
    """Live conditions at the farm, for the header strip on every screen.

    This route used to carry its own hardcoded coordinate pair, marked as
    "for testing" and deliberately not tied to SystemSetting. That made the
    temperature in every header permanently describe one particular farm -
    not as a fallback that a correctly configured install would grow out of,
    but always, even after Settings had been filled in properly.

    It reads the configured location like everything else now, and says so
    when there isn't one rather than showing somebody else's weather.
    """
    coords = farm_coords(session)
    if coords is None:
        return {"no_location": True}
    return fetch_weather(*coords)


# ---------------------------------------------------------------------------
# Weather tab: daily-aggregated history, admin-JWT-gated. The Owner View's
# token-gated equivalent (routers/owner_view.py) calls build_weather_history()
# directly, same split as analysis.py/build_analysis_summary().
# ---------------------------------------------------------------------------

# key -> (source column on WeatherHistory, aggregation, unit, decimals).
# agg is one of "mean"/"sum"/"max", applied over a calendar day's hourly
# rows. uv_index uses the day's peak (not a mean - "how strong did it get")
# and sunshine_duration_s is summed then converted seconds->hours for a
# legible unit. weather_code/condition are categorical, not chartable as a
# line, so they're deliberately left out of this registry.
_METRICS = [
    {"key": "temp_c", "label": "Temperature", "source": "temp_c", "agg": "mean", "unit": "°C", "decimals": 1},
    {"key": "humidity_pct", "label": "Humidity", "source": "humidity_pct", "agg": "mean", "unit": "%", "decimals": 0},
    {"key": "dew_point_c", "label": "Dew Point", "source": "dew_point_c", "agg": "mean", "unit": "°C", "decimals": 1},
    {"key": "precipitation_mm", "label": "Precipitation", "source": "precipitation_mm", "agg": "sum", "unit": "mm", "decimals": 1},
    {"key": "wind_speed_kmh", "label": "Wind Speed", "source": "wind_speed_kmh", "agg": "mean", "unit": "km/h", "decimals": 1},
    {"key": "soil_temp_6cm_c", "label": "Soil Temp (6cm)", "source": "soil_temp_6cm_c", "agg": "mean", "unit": "°C", "decimals": 1},
    {"key": "uv_index", "label": "UV Index", "source": "uv_index", "agg": "max", "unit": "", "decimals": 1},
    {"key": "sunshine_hours", "label": "Sunshine", "source": "sunshine_duration_s", "agg": "sum", "unit": "hrs",
     "decimals": 1, "scale": 1 / 3600},
]


def _metrics_public() -> list:
    return [{"key": m["key"], "label": m["label"], "unit": m["unit"], "decimals": m["decimals"]} for m in _METRICS]


def build_weather_history(session: Session) -> dict:
    """Daily-aggregated WeatherHistory for the Weather tab - see _METRICS
    for per-metric aggregation. Grouped by plain calendar year (1 Jan -
    31 Dec), deliberately NOT the Aug-anchored harvest season used
    elsewhere in this app (analysis.py's _season_day) - weather doesn't
    follow the picking season the way harvest data does, and "what was the
    weather like in 2023" naturally means the calendar year. current_year
    is simply today's calendar year - the one bucket that, being still in
    progress, only covers 1 Jan through whatever's been synced so far
    rather than a full year.

    The day-grouping is done in SQL, not by reading the table into Python.
    That matters more than it looks: WeatherHistory reaches back to 1987
    (see scripts/import_historical_weather_archive.py), so hydrating every
    hourly row here meant ~350k ORM objects and ~11s per tab open - past
    the frontend's own 8s deadline (Boord.NETWORK_TIMEOUT_MS in
    shared/api.js), so the tab aborted the request and showed itself as
    offline while the server was still working. routers/risk.py bounds its
    own WeatherHistory read for the same reason; this one can't bound by
    date (the chart legitimately spans the whole record), so it aggregates
    in the database instead. SQL's aggregates skip NULLs and return NULL
    for an all-NULL day, which is exactly what the previous Python did -
    soil_temp_6cm_c and uv_index are NULL for every pre-2020 row."""
    day = func.date(WeatherHistory.timestamp).label("day")
    aggregates = []
    for m in _METRICS:
        col = getattr(WeatherHistory, m["source"])
        agg = {"mean": func.avg, "sum": func.sum, "max": func.max}[m["agg"]]
        aggregates.append(agg(col))
    rows = session.exec(select(day, *aggregates).group_by(day).order_by(day)).all()

    points = []
    for row in rows:
        d = date.fromisoformat(row[0])
        point = {"date": row[0], "year": d.year, "day_of_year": d.timetuple().tm_yday}
        for m, value in zip(_METRICS, row[1:]):
            point[m["key"]] = None if value is None else round(value * m.get("scale", 1), m["decimals"])
        points.append(point)

    last_synced = session.exec(select(func.max(WeatherHistory.timestamp))).one()
    return {
        "metrics": _metrics_public(),
        "years": sorted({p["year"] for p in points}),
        "current_year": date.today().year,
        "last_synced": last_synced.isoformat() if last_synced else None,
        "points": points,
    }


@router.get("/history")
def weather_history(session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Admin-JWT-gated Weather tab data - syncs the latest hours from
    Open-Meteo first (best-effort, see weather.sync_recent_weather) then
    returns the full daily-aggregated history."""
    sync_recent_weather(session)
    return build_weather_history(session)


@router.post("/history/backfill")
def backfill_weather_history(session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Pull the whole weather record for the farm's location, 2020 to today.

    Same job as scripts/import_historical_weather.py, reachable from the
    browser - the setup wizard offers it once GPS has been entered, because
    a new customer has no shell on the server and no reason to know that
    script exists. The ordering is the point: this cannot run before the
    location step, so the "imported weather for the wrong place" failure
    that farm_coords() refuses to allow never gets a chance to arise.

    Deletes and reinserts HISTORY_START_DATE onward only, exactly like the
    script, so it still composes with the 1987-2019 archive backfill
    (scripts/import_historical_weather_archive.py) in either order and
    however many times either runs.

    Slow by nature - six years of hourly rows in one request - so callers
    must use Boord.UPLOAD_TIMEOUT_MS, not the 8s default.

    Known gap, unchanged by this: WeatherHistory carries no location of its
    own, so rows fetched for one place are indistinguishable from another's.
    A farm that MOVES its GPS and re-runs this ends up with two locations'
    weather in one table. Fixing that needs a lat/lon column and a
    cache-invalidating migration - non-additive, so it waits for Alembic
    (NEXT_STEPS.md §4).
    """
    coords = farm_coords(session)
    if coords is None:
        # Not an error: the wizard offers this button before the location
        # step is necessarily done, and "set your location first" is the
        # honest answer rather than a 400.
        return {"no_location": True, "imported": 0}
    lat, lon = coords
    end_date = date.today().isoformat()
    try:
        data = fetch_historical_hourly(lat, lon, HISTORY_START_DATE, end_date)
        rows = [WeatherHistory(**r) for r in parse_hourly_rows(data)]
    except Exception as e:
        # The farm server's internet is genuinely unreliable. Say so and
        # leave the existing history alone - a half-deleted table would be
        # worse than no import.
        raise HTTPException(502, f"Could not reach the weather service ({type(e).__name__}). "
                                  f"Nothing was changed - try again later.")
    session.exec(delete(WeatherHistory).where(
        WeatherHistory.timestamp >= date.fromisoformat(HISTORY_START_DATE)))
    session.add_all(rows)
    session.commit()
    return {"imported": len(rows), "start_date": HISTORY_START_DATE, "end_date": end_date,
            "lat": lat, "lon": lon,
            "archive_gap": _archive_gap(session)}


def _archive_gap(session: Session) -> Optional[int]:
    """The earliest harvest season this farm has imported, if that is before
    the weather this endpoint can reach - otherwise None.

    Worth reporting rather than leaving to be discovered. routers/risk.py
    scores every reference season from REFERENCE_START_YEAR (2012) onward
    that has yield data, and it needs weather for each one: a farm that
    imports season totals back to, say, 2013 and then fills weather only
    from 2020 gets a Risk indicator that raises "no weather data for
    reference season 2013" instead of a score. Nothing warns them, because
    each half looks like it worked.

    The older range comes from a different Open-Meteo API and is fetched in
    chunks over several minutes (see
    scripts/import_historical_weather_archive.py), which is more than one
    browser request should hold open - so this reports the gap and points
    at update_server.bat rather than trying to close it here.
    """
    # Aggregated in SQL: HistoricalHarvest is one row per block per day and
    # reaches back years, and this runs on a request that has already spent
    # a minute or two on the network.
    earliest = min(
        (y for y in (session.exec(select(func.min(HistoricalHarvest.season_year))).one(),
                     session.exec(select(func.min(HistoricalAnnualYield.season_year))).one())
         if y is not None),
        default=None,
    )
    if earliest is None:
        return None
    return earliest if earliest < date.fromisoformat(HISTORY_START_DATE).year else None
