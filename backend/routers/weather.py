from fastapi import APIRouter, Depends
from sqlmodel import Session

from db import get_session
from weather import farm_coords, fetch_weather

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
