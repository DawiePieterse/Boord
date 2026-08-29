"""First-run setup wizard state.

A fresh database is deliberately blank: no blocks, no wage rate, no farm
name, no GPS (see db.seed_defaults). That is right - none of those are
*a* farm's, they are *this* farm's - but it left a new customer hunting
through Settings and Master Data for five unrelated fields in an order
nothing states. This router is what the wizard reads to know which of
those are still outstanding.

Everything here is DERIVED from the data itself rather than from a
checklist the admin ticked, so a step done the ordinary way (in Settings,
before or after the wizard) counts as done, and a step undone later shows
up as undone.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from db import get_session
from models import (Block, Device, HarvestRecord, RateSetting, SetupState,
                    Supplier, SystemSetting, Worker)
from security import get_current_admin

router = APIRouter(prefix="/api/setup", tags=["setup"])

# The supplier name db.seed_defaults() writes for the farm's own fruit.
# Still holding it means nobody has said whose farm this is.
PLACEHOLDER_OWN_SUPPLIER = "Own Farm"

# The station names db.seed_defaults() writes for the eight device slots.
# A device still carrying one has not been told where it actually stands, so
# these count as unset rather than as named - the same reading as "Own Farm"
# above. Keep in step with the seeding loop in db.seed_defaults().
PLACEHOLDER_STATIONS = (
    {f"Field Station {i}" for i in range(1, 6)}
    | {"Packhouse Receiving 1", "Packhouse Receiving 2", "Pack house office"}
)


def _setup_state_row(session: Session) -> SetupState:
    """The single SetupState row, created on demand. Not seeded in
    db.seed_defaults(): its absence is meaningful, and is how a database
    written before this table existed is told apart from one that has been
    offered the wizard."""
    state = session.exec(select(SetupState)).first()
    if state is None:
        state = SetupState()
        session.add(state)
    return state


def _count(session: Session, model) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


def build_setup_state(session: Session) -> dict:
    """Per-step status, plus whether the wizard should be shown at all.

    `required` is the one thing here that can do real damage if it is
    wrong, and it can be wrong in two opposite directions.

    Send an ESTABLISHED farm through the wizard and it stares at an empty
    form instead of its own dashboard. SetupState is a table created for
    the first time by this release, so every farm that upgrades into it has
    no row at all - the marker alone cannot be the test. Two independent
    signs of an already-working farm have to agree before the wizard is
    offered to a database that has never seen it: the farm has never named
    itself, and not one crate has ever been captured. A farm that has been
    running for a season fails both. Same shape of
    reasoning as seed_demo.refuse_unless_safe(), and for the same reason -
    the check has to ask "is this database somebody's?", not "does a flag
    say so".

    Drop a HALF-CONFIGURED farm out of the wizard and it is stranded in the
    opposite way. Those two signs are not stable across the wizard itself:
    its very first step writes the farm name, so on the next page load the
    farm looks named and the remaining seven steps become unreachable. That
    is what SetupState.started_at is for. Once the wizard has been shown, it
    keeps being shown until it is finished, whatever the data now looks
    like - which is also what makes closing the tab half way through safe.
    """
    settings = session.exec(select(SystemSetting)).first()
    own = session.exec(select(Supplier).where(Supplier.is_own_farm == True)).first()  # noqa: E712
    rate = session.exec(
        select(RateSetting).order_by(RateSetting.effective_date.desc(), RateSetting.id.desc())
    ).first()

    farm_name = (settings.farm_name or "").strip() if settings else ""
    has_location = bool(settings and settings.gps_lat is not None and settings.gps_lon is not None)
    own_name = (own.name or "").strip() if own else ""
    crates = _count(session, HarvestRecord)
    blocks = _count(session, Block)
    workers = _count(session, Worker)
    named_devices = sum(
        1 for d in session.exec(select(Device)).all()
        if (d.station or "").strip() and d.station not in PLACEHOLDER_STATIONS
    )

    state = session.exec(select(SetupState)).first()
    completed_at = state.completed_at if state else None
    started_at = state.started_at if state else None
    never_configured = not farm_name and crates == 0
    steps = {
        # Note `is not None` on the coordinates, not truthiness: latitude 0
        # and longitude 0 are real places. Same trap weather.farm_coords()
        # documents.
        "identity": {"done": bool(farm_name) and bool(own_name) and own_name != PLACEHOLDER_OWN_SUPPLIER,
                      "farm_name": farm_name, "own_supplier_name": own_name},
        "location": {"done": has_location,
                      "gps_lat": settings.gps_lat if settings else None,
                      "gps_lon": settings.gps_lon if settings else None},
        "rate": {"done": rate is not None and rate.default_rate_per_kg > 0,
                  "rate_per_kg": rate.default_rate_per_kg if rate else None},
        "thresholds": {"done": True,  # seeded sane and farm-neutral; never blocks
                        "green_to_yellow_minutes": settings.green_to_yellow_minutes if settings else None,
                        "yellow_to_red_minutes": settings.yellow_to_red_minutes if settings else None},
        "blocks": {"done": blocks > 0, "count": blocks},
        "workers": {"done": workers > 0, "count": workers},
        "devices": {"done": named_devices > 0, "count": named_devices},
    }
    return {
        "required": completed_at is None and (started_at is not None or never_configured),
        "started_at": started_at,
        "completed_at": completed_at,
        "harvest_records": crates,
        "steps": steps,
    }


@router.get("/state")
def setup_state(session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    return build_setup_state(session)


@router.post("/start")
def start_setup(session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Records that the wizard has been opened, so it survives a reload.

    Called by the wizard itself as it paints, not by anything a farm can
    reach by accident: stamping this on a database that was never going to
    be offered the wizard would put an established farm into it.
    Idempotent - the first stamp stands."""
    state = _setup_state_row(session)
    if state.started_at is None:
        state.started_at = datetime.now(timezone.utc)
        session.add(state)
        session.commit()
    return {"ok": True, "started_at": state.started_at}


@router.post("/complete")
def complete_setup(session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Marks the wizard finished. Idempotent, and deliberately says nothing
    about whether every step was actually filled in - skipping the optional
    ones is a legitimate way to finish, and the steps that genuinely cannot
    be guessed are already refused at the point of use (payments.py has no
    rate, weather.farm_coords has no location)."""
    state = _setup_state_row(session)
    state.completed_at = datetime.now(timezone.utc)
    if state.started_at is None:
        state.started_at = state.completed_at  # finished without ever being "started"
    session.add(state)
    session.commit()
    return {"ok": True, "completed_at": state.completed_at}
