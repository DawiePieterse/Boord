import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, SQLModel, select

from db import get_session, supplier_id_for_device
from models import HarvestRecord, Lot, LotStatus, Supplier, SystemSetting
from security import require_admin_client
from timeutil import day_bounds
from weather import fetch_weather_cached

router = APIRouter(prefix="/api/lots", tags=["lots"])


class LotIn(SQLModel):
    """Request body schema for creating/upserting a lot."""
    slip_number: str
    timestamp: datetime
    device_id: Optional[str] = None
    team_id: Optional[str] = None
    supplier_id: Optional[int] = None
    driver: str = ""
    total_crates: int = 0
    total_kg: float = 0.0
    status: LotStatus = LotStatus.created
    notes: str = ""


class SplitLotIn(SQLModel):
    keep_count: int  # how many of this lot's crates to hold back onto a new lot


class ExternalLotIn(SQLModel):
    """Manual lot entry at the pack house for another farmer's fruit -
    there's no field-device dispatch step, so this goes straight into the
    in-transit list ready to be checked in like any other lot."""
    supplier_id: int
    timestamp: Optional[datetime] = None
    driver: str = ""
    total_crates: int
    total_kg: float
    notes: str = ""


def _urgency(age_minutes: float, settings: SystemSetting) -> str:
    if age_minutes >= settings.yellow_to_red_minutes:
        return "red"
    if age_minutes >= settings.green_to_yellow_minutes:
        return "yellow"
    return "green"


def _with_urgency(lot: Lot, settings: SystemSetting, suppliers: dict) -> dict:
    now = datetime.now(timezone.utc)
    ts = lot.timestamp if lot.timestamp.tzinfo else lot.timestamp.replace(tzinfo=timezone.utc)
    age_minutes = (now - ts).total_seconds() / 60
    supplier = suppliers.get(lot.supplier_id)
    return {
        **lot.model_dump(),
        "total_kg": round(lot.total_kg, 1),
        "age_minutes": round(age_minutes),
        "urgency": _urgency(age_minutes, settings),
        "supplier_name": supplier.name if supplier else "",
        "is_own_farm": supplier.is_own_farm if supplier else False,
    }


def _supplier_map(session: Session) -> dict:
    return {s.id: s for s in session.exec(select(Supplier)).all()}


def recompute_lot_totals(session: Session, lot: Lot) -> None:
    """Re-derive a dispatched lot's stored total_crates/total_kg from its
    HarvestRecords, and persist them onto the Lot row.

    Only Lot.status != created carries stored totals in the first place -
    a still-pending lot (list_pending, above) always computes live, so
    there's nothing to recompute there. Every other lot's totals are set
    once at dispatch (LotIn.total_crates/total_kg) and never touched again
    server-side - so after routers/harvest_records.py corrects a crate, this
    is what keeps the Received list, the dashboard KPIs, the receiving/lot
    exports, and supplier billing (routers/suppliers.py) all showing the
    same number the crates actually add up to."""
    if lot.status == LotStatus.created:
        return
    crates = session.exec(select(HarvestRecord).where(HarvestRecord.lot_id == lot.id)).all()
    lot.total_crates = len(crates)
    lot.total_kg = round(sum(c.weight_kg - c.deduction_kg for c in crates), 1)
    session.add(lot)


def _build_split_index(session: Session):
    """One-hop split lineage index: every lot ever carved out of an earlier
    one via split_lot() knows its parent's slip_number - this fetches all
    such children plus their parent rows once per request (not per-lot), so
    attaching related-lot info to a list of lots doesn't trigger N+1 queries."""
    children = session.exec(select(Lot).where(Lot.split_from_slip_number != None)).all()  # noqa: E711
    parent_slips = {c.split_from_slip_number for c in children}
    parents = session.exec(select(Lot).where(Lot.slip_number.in_(parent_slips))).all() if parent_slips else []
    parents_by_slip = {p.slip_number: p for p in parents}
    children_by_parent_slip = defaultdict(list)
    for c in children:
        children_by_parent_slip[c.split_from_slip_number].append(c)
    return parents_by_slip, children_by_parent_slip


def _related_lots(session: Session, lot: Lot, parents_by_slip: dict, children_by_parent_slip: dict) -> list:
    related = []
    if lot.split_from_slip_number and lot.split_from_slip_number in parents_by_slip:
        related.append(parents_by_slip[lot.split_from_slip_number])
    related.extend(children_by_parent_slip.get(lot.slip_number, []))

    result = []
    for r in related:
        if r.status == LotStatus.created:
            # Still-pending lots don't have real totals stored yet (see
            # list_pending) - compute them live so the relative's crate/kg
            # count shown at receiving isn't misleadingly 0.
            crates = session.exec(select(HarvestRecord).where(HarvestRecord.lot_id == r.id)).all()
            total_crates = len(crates)
            total_kg = round(sum(c.weight_kg - c.deduction_kg for c in crates), 1)
        else:
            total_crates = r.total_crates
            total_kg = round(r.total_kg, 1)
        result.append({
            "slip_number": r.slip_number,
            "status": r.status,
            "total_crates": total_crates,
            "total_kg": total_kg,
            "received_at": r.received_at,
        })
    return result


@router.get("/pending")
def list_pending(supplier_id: Optional[int] = None, period_start: Optional[date] = None,
                  period_end: Optional[date] = None, session: Session = Depends(get_session)):
    """Crates already captured in the field whose picking slip hasn't been
    dispatched yet ('Send Picking Slip' not tapped). These lots exist only
    as placeholders (status=created, created on first crate sync) so their
    totals are computed live from HarvestRecord rather than stored on Lot.
    Attributed to the dispatching field device's supplier (its allocation,
    or the pack house's own fruit when it has none).

    period_start/period_end are optional and only applied when both are
    passed (dashboard KPI use) - left unfiltered by default so device screens
    always see every pending lot regardless of when picking started."""
    settings = session.exec(select(SystemSetting)).first() or SystemSetting()
    suppliers = _supplier_map(session)
    query = select(Lot).where(Lot.status == LotStatus.created)
    if supplier_id is not None:
        query = query.where(Lot.supplier_id == supplier_id)
    if period_start is not None and period_end is not None:
        start_dt, end_dt = day_bounds(period_start, period_end)
        query = query.where(Lot.timestamp >= start_dt, Lot.timestamp <= end_dt)
    lots = session.exec(query.order_by(Lot.timestamp.asc())).all()
    result = []
    for l in lots:
        crates = session.exec(select(HarvestRecord).where(HarvestRecord.lot_id == l.id)).all()
        if not crates:
            continue
        total_kg = sum(c.weight_kg - c.deduction_kg for c in crates)
        enriched = _with_urgency(l, settings, suppliers)
        enriched["total_crates"] = len(crates)
        enriched["total_kg"] = round(total_kg, 1)
        result.append(enriched)
    result.sort(key=lambda r: r["age_minutes"], reverse=True)
    return result


@router.get("/in-transit")
def list_in_transit(supplier_id: Optional[int] = None, period_start: Optional[date] = None,
                     period_end: Optional[date] = None, session: Session = Depends(get_session)):
    """Landing view for the pack house app: dispatched lots not yet
    received, oldest (most urgent, red) first - own fruit and other
    suppliers' fruit together, distinguished by supplier_name.

    period_start/period_end are optional and only applied when both are
    passed (dashboard KPI use) - left unfiltered by default so the pack
    house gate always sees every truck currently on its way, regardless of
    when it was dispatched."""
    settings = session.exec(select(SystemSetting)).first() or SystemSetting()
    suppliers = _supplier_map(session)
    query = select(Lot).where(Lot.status == LotStatus.in_transit)
    if supplier_id is not None:
        query = query.where(Lot.supplier_id == supplier_id)
    if period_start is not None and period_end is not None:
        start_dt, end_dt = day_bounds(period_start, period_end)
        query = query.where(Lot.timestamp >= start_dt, Lot.timestamp <= end_dt)
    lots = session.exec(query.order_by(Lot.timestamp.asc())).all()
    parents_by_slip, children_by_parent_slip = _build_split_index(session)
    enriched = []
    for l in lots:
        e = _with_urgency(l, settings, suppliers)
        e["related_lots"] = _related_lots(session, l, parents_by_slip, children_by_parent_slip)
        enriched.append(e)
    enriched.sort(key=lambda r: r["age_minutes"], reverse=True)
    return enriched


@router.get("/received")
def list_received(period_start: Optional[date] = None, period_end: Optional[date] = None,
                   supplier_id: Optional[int] = None, session: Session = Depends(get_session)):
    """Recently received lots for the dashboard, newest-received first.

    period_start/period_end are optional and only applied when both are
    passed - left unfiltered by default (all received lots, any time)."""
    settings = session.exec(select(SystemSetting)).first() or SystemSetting()
    suppliers = _supplier_map(session)
    # received_at is only ever set at gate check-in, so it alone defines
    # "received" - graded lots (status=processing_complete) stay in the list.
    query = select(Lot).where(Lot.received_at != None)  # noqa: E711
    if period_start is not None and period_end is not None:
        start_dt, end_dt = day_bounds(period_start, period_end)
        query = query.where(Lot.received_at >= start_dt, Lot.received_at <= end_dt)
    if supplier_id is not None:
        query = query.where(Lot.supplier_id == supplier_id)
    lots = session.exec(query.order_by(Lot.received_at.desc())).all()
    return [_with_urgency(l, settings, suppliers) for l in lots]


@router.get("")
def list_lots(status: Optional[str] = None, supplier_id: Optional[int] = None,
              session: Session = Depends(get_session)):
    query = select(Lot)
    if status:
        query = query.where(Lot.status == status)
    if supplier_id is not None:
        query = query.where(Lot.supplier_id == supplier_id)
    return session.exec(query.order_by(Lot.timestamp.desc())).all()


@router.get("/{lot_id}")
def get_lot(lot_id: int, session: Session = Depends(get_session), _admin=Depends(require_admin_client)):
    """Admin-only: a lot plus its individual crates, for the harvest-data
    edit screen. Not used by the field or pack house screens - those only
    ever need the aggregate totals from the list endpoints above."""
    lot = session.get(Lot, lot_id)
    if not lot:
        raise HTTPException(404, "Lot not found")
    crates = session.exec(
        select(HarvestRecord).where(HarvestRecord.lot_id == lot_id).order_by(HarvestRecord.timestamp)
    ).all()
    return {"lot": lot, "crates": crates}


@router.post("")
def upsert_lot(lot_in: LotIn, session: Session = Depends(get_session)):
    """Create or update a lot by slip_number (idempotent - safe to retry).
    Field devices never send supplier_id (they don't know about suppliers) -
    it's resolved here from the dispatching device's allocation, falling back
    to the pack house's own fruit."""
    existing = session.exec(select(Lot).where(Lot.slip_number == lot_in.slip_number)).first()
    data = lot_in.model_dump()
    data["total_kg"] = round(data.get("total_kg", 0.0), 1)
    if data.get("supplier_id") is None:
        # Keep the placeholder lot's supplier if sync.py already set one from
        # the device; otherwise resolve it from the device now.
        data["supplier_id"] = (existing.supplier_id if existing and existing.supplier_id is not None
                                else supplier_id_for_device(session, data.get("device_id")))
    lot = Lot(**data, id=existing.id if existing else None)
    if existing:
        # LotIn doesn't carry split_from_slip_number (dispatch never sets it),
        # so without this the merge below would silently wipe it back to None
        # every time an existing lot is re-upserted (e.g. field->in_transit).
        lot.split_from_slip_number = existing.split_from_slip_number

    # Capture conditions at the moment of dispatch. Most lots already exist as
    # a placeholder row by this point (crates synced from the field before
    # "Send Picking Slip" was tapped - see sync.py _resolve_lot_id), so this
    # can't be gated on `not existing`; it has to key off the created->in_transit
    # transition instead. Once a lot is in_transit, leave its weather alone -
    # a retried dispatch shouldn't overwrite the conditions at check-in.
    dispatching = lot_in.status == LotStatus.in_transit and (
        not existing or existing.status != LotStatus.in_transit)
    if dispatching:
        settings = session.exec(select(SystemSetting)).first()
        if settings and settings.gps_lat is not None and settings.gps_lon is not None:
            weather = fetch_weather_cached(settings.gps_lat, settings.gps_lon)
            lot.weather_temp = weather.get("temp")
            lot.weather_humidity = weather.get("humidity")
            lot.weather_condition = weather.get("condition", "")
    elif existing:
        lot.weather_temp = existing.weather_temp
        lot.weather_humidity = existing.weather_humidity
        lot.weather_condition = existing.weather_condition

    saved = session.merge(lot)
    session.commit()
    session.refresh(saved)

    # A field device that queued this dispatch offline (or is retrying a lost
    # response) posts the totals it computed at capture time - if an admin
    # has since corrected one of this lot's crates, that payload would
    # silently undo the correction. Existing-lot only: a brand new slip_number
    # can't have any crates against it yet, edited or otherwise.
    if existing:
        has_edit = session.exec(
            select(HarvestRecord.uuid)
            .where(HarvestRecord.lot_id == saved.id, HarvestRecord.edited_at != None)  # noqa: E711
        ).first()
        if has_edit:
            recompute_lot_totals(session, saved)
            session.commit()
            session.refresh(saved)

    return saved


@router.post("/by-slip/{slip_number}/split")
def split_lot(slip_number: str, body: SplitLotIn, session: Session = Depends(get_session)):
    """A truck arrives before a field station has finished filling out today's
    picking slip - only some of the currently-pending crates go on this load.
    The oldest crates stay on this lot (what's being dispatched now, FIFO -
    whatever's been sitting longest goes out first); the newest `keep_count`
    crates move onto a brand-new placeholder lot so they can combine with
    whatever gets picked next, for a later dispatch."""
    lot = session.exec(select(Lot).where(Lot.slip_number == slip_number)).first()
    if not lot:
        raise HTTPException(404, "Lot not found")
    if lot.status != LotStatus.created:
        raise HTTPException(409, "Lot already dispatched")

    crates = session.exec(select(HarvestRecord).where(HarvestRecord.lot_id == lot.id)).all()
    if body.keep_count <= 0 or body.keep_count >= len(crates):
        raise HTTPException(400, "keep_count must be between 1 and total_crates - 1")

    crates_sorted = sorted(crates, key=lambda c: c.timestamp)
    held_back = crates_sorted[-body.keep_count:]

    new_slip = f"{lot.device_id or 'split'}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    new_lot = Lot(
        slip_number=new_slip,
        timestamp=datetime.now(timezone.utc),
        device_id=lot.device_id,
        team_id=lot.team_id,
        supplier_id=lot.supplier_id,
        status=LotStatus.created,
        split_from_slip_number=lot.slip_number,
    )
    session.add(new_lot)
    session.flush()

    for c in held_back:
        c.lot_id = new_lot.id
        session.add(c)

    session.commit()
    session.refresh(new_lot)

    total_kg = sum(c.weight_kg - c.deduction_kg for c in held_back)
    return {
        "new_lot": {**new_lot.model_dump(), "total_crates": len(held_back), "total_kg": round(total_kg, 1)},
        "moved_uuids": [c.uuid for c in held_back],
    }


def _external_slip_number(session: Session, supplier_id: int, timestamp: datetime) -> str:
    """A slip a human can read back over a two-way radio.

    This used to append %f - six digits of microseconds - which made a
    24-character slip for a number that gets read aloud at a gate, written on
    paper and typed into a query. Seconds match what a field device already
    mints (device-01-20260830132301), and two deliveries logged in the same
    second by someone pressing a button is not a real case. It is still a
    UNIQUE column, though, so it cannot merely be assumed: a taken slip gets a
    short suffix rather than a 500 in front of the receiving clerk.
    """
    base = f"EXT-{supplier_id}-{timestamp.strftime('%Y%m%d%H%M%S')}"
    candidate = base
    for suffix in range(2, 100):
        if not session.exec(select(Lot).where(Lot.slip_number == candidate)).first():
            return candidate
        candidate = f"{base}-{suffix}"
    return f"{base}-{timestamp.strftime('%f')}"  # absurd, but never a collision


@router.post("/external")
def create_external_lot(lot_in: ExternalLotIn, session: Session = Depends(get_session)):
    """Pack house staff logs an incoming delivery from a supplier who doesn't
    use the pack house's field devices. Goes straight into the in-transit
    list, ready to be checked in through the normal receiving flow."""
    supplier = session.get(Supplier, lot_in.supplier_id)
    if not supplier:
        raise HTTPException(404, "Supplier not found")
    timestamp = lot_in.timestamp or datetime.now(timezone.utc)
    slip_number = _external_slip_number(session, lot_in.supplier_id, timestamp)
    lot = Lot(
        slip_number=slip_number,
        timestamp=timestamp,
        supplier_id=lot_in.supplier_id,
        driver=lot_in.driver,
        total_crates=lot_in.total_crates,
        total_kg=round(lot_in.total_kg, 1),
        status=LotStatus.in_transit,
        notes=lot_in.notes,
    )
    # This lot goes straight to in_transit with no separate dispatch step, so
    # it needs its own weather capture - it'd otherwise never get one. Same
    # farm-location conditions as a field dispatch (routers/lots.py upsert_lot):
    # there's no GPS for wherever the other farmer picked, so this is read as
    # "conditions at the pack house when the delivery was logged," not
    # "conditions where it was grown."
    settings = session.exec(select(SystemSetting)).first()
    if settings and settings.gps_lat is not None and settings.gps_lon is not None:
        weather = fetch_weather_cached(settings.gps_lat, settings.gps_lon)
        lot.weather_temp = weather.get("temp")
        lot.weather_humidity = weather.get("humidity")
        lot.weather_condition = weather.get("condition", "")
    session.add(lot)
    session.commit()
    session.refresh(lot)
    return lot
