from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, SQLModel, select

from db import get_own_supplier_id, get_session
from models import HarvestRecord, Lot, LotStatus, SystemSetting
from weather import fetch_weather_cached

router = APIRouter(prefix="/api/sync", tags=["sync"])


class HarvestRecordIn(SQLModel):
    uuid: str
    timestamp: datetime
    worker_id: Optional[str] = None
    block_id: Optional[str] = None
    weight_kg: float
    deduction_kg: float = 0.0
    device_id: Optional[str] = None
    team_id: Optional[str] = None
    notes: str = ""
    slip_number: Optional[str] = None  # which lot (picking slip) this crate belongs to, if any


class HarvestSyncBatch(SQLModel):
    records: list[HarvestRecordIn]


def _resolve_lot_id(session: Session, slip_number: Optional[str], device_id, team_id, timestamp) -> Optional[int]:
    if not slip_number:
        return None
    lot = session.exec(select(Lot).where(Lot.slip_number == slip_number)).first()
    if lot:
        return lot.id
    # Field device hasn't dispatched the slip yet (crate saved before "Send
    # Picking Slip" was tapped) - create a placeholder lot so the crate has
    # somewhere to attach; totals get filled in when the slip is dispatched.
    # Field devices only ever capture the farm's own fruit.
    lot = Lot(slip_number=slip_number, timestamp=timestamp, device_id=device_id,
              team_id=team_id, status=LotStatus.created, supplier_id=get_own_supplier_id(session))
    session.add(lot)
    session.commit()
    session.refresh(lot)
    return lot.id


def _farm_weather(session: Session) -> dict:
    """Conditions at the farm right now, or {} if it can't be established.
    Same source and same silent-failure rule as the per-dispatch capture in
    routers/lots.py - weather is a nice-to-have and must never fail a sync."""
    settings = session.exec(select(SystemSetting)).first()
    if not settings or settings.gps_lat is None or settings.gps_lon is None:
        return {}
    return fetch_weather_cached(settings.gps_lat, settings.gps_lon)


@router.post("/harvest")
def sync_harvest(batch: HarvestSyncBatch, session: Session = Depends(get_session)):
    """Idempotent upsert by client-generated uuid - safe for a field device
    to retry the same batch after regaining mobile signal."""
    accepted = 0
    weather = None  # looked up lazily - a batch of pure retries needs no call
    for r in batch.records:
        existing = session.get(HarvestRecord, r.uuid)
        lot_id = _resolve_lot_id(session, r.slip_number, r.device_id, r.team_id, r.timestamp)
        worker_id, weight_kg, deduction_kg = r.worker_id, r.weight_kg, r.deduction_kg
        if existing:
            # A retry of an already-stored crate keeps its original stamps -
            # re-reading the weather now would overwrite the conditions at
            # check-in with whatever it happens to be doing on the retry.
            synced_at = existing.synced_at
            temp, humidity, condition = (
                existing.weather_temp, existing.weather_humidity, existing.weather_condition)
            if existing.edited_at is not None:
                # An admin has corrected this crate (routers/harvest_records.py).
                # The device is still replaying its OLD payload - a lost/timed-out
                # response, a retry loop, or a restored IndexedDB can all resend a
                # batch the device thinks never landed. Without this, that resend
                # would silently overwrite the correction with the original
                # mis-capture on every sync tick. The correction wins; only the
                # server-owned edited_at/edited_by pair records that it happened.
                worker_id, weight_kg, deduction_kg = (
                    existing.worker_id, existing.weight_kg, existing.deduction_kg)
        else:
            if weather is None:
                weather = _farm_weather(session)
            synced_at = datetime.now(timezone.utc)
            temp = weather.get("temp")
            humidity = weather.get("humidity")
            condition = weather.get("condition", "")
        record = HarvestRecord(
            uuid=r.uuid, timestamp=r.timestamp, worker_id=worker_id, block_id=r.block_id,
            weight_kg=weight_kg, deduction_kg=deduction_kg, device_id=r.device_id,
            team_id=r.team_id, lot_id=lot_id, notes=r.notes, synced_at=synced_at,
            weather_temp=temp, weather_humidity=humidity, weather_condition=condition,
            edited_at=existing.edited_at if existing else None,
            edited_by=existing.edited_by if existing else None,
        )
        session.merge(record)
        accepted += 1
    session.commit()
    return {"accepted": accepted}
