from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, SQLModel, select

from db import get_session
from models import Lot, LotStatus, ReceivingRecord

router = APIRouter(prefix="/api/receiving", tags=["receiving"])


class ReceivingRecordIn(SQLModel):
    lot_id: int
    timestamp: datetime
    expected_crates: int = 0
    actual_crates: int = 0
    condition: str = "Good"
    waste_kg: float = 0.0
    notes: str = ""
    received_by: str = ""


@router.post("")
def confirm_receipt(record_in: ReceivingRecordIn, session: Session = Depends(get_session)):
    lot = session.get(Lot, record_in.lot_id)
    if not lot:
        raise HTTPException(404, "Lot not found")

    data = record_in.model_dump()
    data["waste_kg"] = round(data.get("waste_kg", 0.0), 1)
    record = ReceivingRecord(
        **data,
        discrepancy=record_in.actual_crates - record_in.expected_crates,
    )
    session.add(record)

    lot.status = LotStatus.received
    # Honor the device-reported check-in time (normalized to UTC) so backlog
    # check-ins land on their real day instead of piling into "today".
    received_at = record_in.timestamp or datetime.now(timezone.utc)
    if received_at.tzinfo is not None:
        received_at = received_at.astimezone(timezone.utc)
    lot.received_at = received_at
    session.add(lot)

    session.commit()
    session.refresh(record)
    return record


@router.get("/{lot_id}")
def get_receiving_for_lot(lot_id: int, session: Session = Depends(get_session)):
    return session.exec(select(ReceivingRecord).where(ReceivingRecord.lot_id == lot_id)).all()
