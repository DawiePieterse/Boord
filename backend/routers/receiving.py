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

    # Idempotent, like the field's sync. The receiving tablet's request can
    # land while its response is lost to the pack house Wi-Fi, and the clerk
    # is then told "check connection and retry" and taps Confirm again. That
    # used to write a second receiving row for a receipt that happened once
    # and move received_at (the Received list's and billing's date) to the
    # retry. A received lot cannot be un-received, so a second confirmation
    # can only ever be a retry: it gets the receipt that is already there.
    existing = session.exec(select(ReceivingRecord).where(ReceivingRecord.lot_id == lot.id)
                            .order_by(ReceivingRecord.id)).first()
    if existing is not None:
        return existing

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
    received_at = record_in.timestamp
    if received_at.tzinfo is not None:
        received_at = received_at.astimezone(timezone.utc)
    lot.received_at = received_at
    session.add(lot)

    session.commit()
    session.refresh(record)
    return record
