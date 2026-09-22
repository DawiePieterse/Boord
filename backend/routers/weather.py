from fastapi import APIRouter, Depends
from sqlmodel import Session

from db import get_session
from weather import current_farm_weather, farm_weather_configured

router = APIRouter(prefix="/api/weather", tags=["weather"])


@router.get("/current")
def current_weather(session: Session = Depends(get_session)):
    """Live conditions at the farm, for the header strip on every screen.

    This route used to carry its own hardcoded coordinate pair, marked as
    "for testing" and deliberately not tied to SystemSetting. That made the
    temperature in every header permanently describe one particular farm -
    not as a fallback that a correctly configured install would grow out of,
    but always, even after Settings had been filled in properly.

    It reads the configured source (an on-farm station, or else GPS) like
    everything else now, and says so when neither is set rather than
    showing somebody else's weather.
    """
    if not farm_weather_configured(session):
        return {"no_location": True}
    return current_farm_weather(session)
