from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from db import get_session
from models import Lot, PrePackRecord

router = APIRouter(prefix="/api/processing", tags=["processing"])


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------

class PrePackRecordCreate(BaseModel):
    lot_id: int
    crates: int = 0
    dominant_block_id: Optional[str] = None
    operator: str = ""
    notes: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.utcnow()


def _prepack_crates_for_lot(session: Session, lot_id: int) -> int:
    records = session.exec(select(PrePackRecord).where(PrePackRecord.lot_id == lot_id)).all()
    return sum(r.crates for r in records)


# ---------------------------------------------------------------------------
# Pre-pack pull (candidate XXL/XL crates set aside at receiving)
# ---------------------------------------------------------------------------

@router.post("/prepack")
def create_prepack_record(body: PrePackRecordCreate, session: Session = Depends(get_session)):
    """Pull candidate crates aside at receiving for a separate pre-pack line.
    Recorded here purely for audit/tracking - there's no grading station or
    punnet packing in this app, so the pull is never "confirmed" further."""
    lot = session.get(Lot, body.lot_id)
    if not lot:
        raise HTTPException(404, "Lot not found")
    if body.crates <= 0:
        raise HTTPException(400, "Crates must be greater than zero")
    already_pulled = _prepack_crates_for_lot(session, body.lot_id)
    available = lot.total_crates - already_pulled
    if body.crates > available:
        raise HTTPException(
            400, f"Only {available} crates available to pull ({already_pulled} already pre-packed)"
        )
    record = PrePackRecord(
        lot_id=body.lot_id,
        timestamp=_now(),
        crates=body.crates,
        dominant_block_id=body.dominant_block_id,
        operator=body.operator,
        notes=body.notes,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record
