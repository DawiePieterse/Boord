from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, SQLModel, select

from db import get_session, supplier_id_for_device, upsert
from models import DeletedHarvestRecord, HarvestRecord, Lot, LotStatus
from routers.harvest_records import delete_crate
from weather import current_farm_weather

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
    # The lot is attributed to whichever supplier this field device is
    # allocated to, falling back to the pack house's own fruit.
    lot = Lot(slip_number=slip_number, timestamp=timestamp, device_id=device_id,
              team_id=team_id, status=LotStatus.created,
              supplier_id=supplier_id_for_device(session, device_id))
    session.add(lot)
    session.commit()
    session.refresh(lot)
    return lot.id


@router.post("/harvest")
def sync_harvest(batch: HarvestSyncBatch, session: Session = Depends(get_session)):
    """Idempotent upsert by client-generated uuid - safe for a field device
    to retry the same batch after regaining mobile signal."""
    accepted = 0
    weather = None  # looked up lazily - a batch of pure retries needs no call
    uuids = [r.uuid for r in batch.records]
    existing_by_uuid = {h.uuid: h for h in session.exec(
        select(HarvestRecord).where(HarvestRecord.uuid.in_(uuids))).all()} if uuids else {}
    deleted = set(session.exec(select(DeletedHarvestRecord.uuid)
                               .where(DeletedHarvestRecord.uuid.in_(uuids))).all()) if uuids else set()
    lot_ids = {}  # slip_number -> lot id; a batch almost always shares one slip
    for r in batch.records:
        # Deleted (admin, or the field's own undo) - a replay must not bring it
        # back. Still counted as accepted: the device only needs to stop sending.
        if r.uuid in deleted:
            accepted += 1
            continue
        existing = existing_by_uuid.get(r.uuid)
        if r.slip_number not in lot_ids:
            lot_ids[r.slip_number] = _resolve_lot_id(
                session, r.slip_number, r.device_id, r.team_id, r.timestamp)
        lot_id = lot_ids[r.slip_number]
        fields = dict(uuid=r.uuid, timestamp=r.timestamp, device_id=r.device_id,
                      team_id=r.team_id, lot_id=lot_id, notes=r.notes)
        # An admin-corrected crate (routers/harvest_records.py, edited_at set)
        # keeps the correction. The device may still be replaying its OLD
        # payload - a lost/timed-out response, a retry loop, or a restored
        # IndexedDB can all resend a batch the device thinks never landed -
        # and that resend must not overwrite the correction on every sync tick.
        if existing is None or existing.edited_at is None:
            fields.update(worker_id=r.worker_id, block_id=r.block_id,
                          weight_kg=r.weight_kg, deduction_kg=r.deduction_kg)
        if existing is None:
            # Stamped once, on first arrival. A retry of an already-stored
            # crate keeps its original stamps - re-reading the weather now
            # would overwrite the conditions at check-in with whatever it
            # happens to be doing on the retry.
            if weather is None:
                # {} when unavailable - weather must never fail a sync.
                weather = current_farm_weather(session)
            fields.update(synced_at=datetime.now(timezone.utc), weather_temp=weather.get("temp"),
                          weather_humidity=weather.get("humidity"),
                          weather_condition=weather.get("condition", ""))
        existing_by_uuid[r.uuid] = upsert(session, existing, HarvestRecord, fields)
        accepted += 1
    session.commit()
    return {"accepted": accepted}


class FieldUndoBatch(SQLModel):
    device_id: str
    uuids: list[str]


@router.post("/harvest/undo")
def undo_harvest(batch: FieldUndoBatch, session: Session = Depends(get_session)):
    """The field device's "Undo last crate", flushed from its queue. Only
    crates this device captured, and only while their slip is still being
    picked - once a slip has left on a truck, a crate is the office's to fix
    (DELETE /api/harvest-records/{uuid}), not the orchard's. A uuid the server
    never saw is still tombstoned: the undo may have beaten the crate's own
    upload. Refused uuids are returned so the device can say so."""
    refused = []
    for uuid in batch.uuids:
        record = session.get(HarvestRecord, uuid)
        if record:
            lot = session.get(Lot, record.lot_id) if record.lot_id else None
            if record.device_id != batch.device_id or (lot and lot.status != LotStatus.created):
                refused.append(uuid)
                continue
        delete_crate(session, uuid, "field")
    session.commit()
    return {"refused": refused}
