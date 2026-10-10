from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import Session, SQLModel, select

from db import get_session
from models import Block, DeletedHarvestRecord, HarvestRecord, Lot, Payment, Worker
from routers.lots import delete_lot_if_empty, recompute_lot_totals
from security import require_admin_client
from timeutil import to_local

# Every endpoint here is admin-only.
router = APIRouter(prefix="/api/harvest-records", tags=["harvest-records"], dependencies=[Depends(require_admin_client)])

# Every crate seed_demo.py invents is given a uuid starting with this. Real
# crates carry a uuid minted by the field device (Boord.uuid()), so the
# prefix is what tells demo data apart from a farm's own.
DEMO_UUID_PREFIX = "demo-"


@router.get("/counts")
def harvest_record_counts(session: Session = Depends(get_session)):
    """How many crates this database holds, and how many of those the demo
    seeder wrote.

    seed_demo.py calls this before it writes anything: a database holding
    crates it did not create belongs to a real farm, and seeding it would
    overwrite the farm's GPS and blocks and file fake workers alongside real
    people. The split is what lets the seeder stay safe to re-run against its
    own demo database."""
    total = session.exec(select(func.count()).select_from(HarvestRecord)).one()
    demo = session.exec(
        select(func.count()).select_from(HarvestRecord)
        .where(HarvestRecord.uuid.startswith(DEMO_UUID_PREFIX))
    ).one()
    return {"total": total, "demo": demo}


class HarvestRecordEdit(SQLModel):
    """PATCH body - patch semantics, only the fields actually being changed
    need to be sent. Worker, block, weight and deduction are the only things
    an admin can correct here; everything else about a crate (which device,
    when it was picked) stays exactly as captured. See the comment on
    HarvestRecord.edited_at in models.py for how this interacts with sync."""
    worker_id: Optional[str] = None
    block_id: Optional[str] = None
    weight_kg: Optional[float] = None
    deduction_kg: Optional[float] = None


def _wages_affected(session: Session, record: HarvestRecord, worker_ids: set) -> list[dict]:
    """Payment rows that already cover this crate's date, for every worker
    whose total this correction touches - the crate's own worker, and, if the
    worker was reassigned, the one it moved away from too (their total is now
    also wrong, in the opposite direction).

    Calculating wages snapshots numbers into the Payment table (routers/
    payments.py calculate_payments); this edit does not touch that table, so
    a period that was already run stays showing the old figure until someone
    explicitly re-runs Calculate Wages. Returning the affected periods here
    lets the admin screen say so instead of leaving it to be found by
    accident."""
    if not worker_ids:
        return []
    crate_date = to_local(record.timestamp).date()
    payments = session.exec(select(Payment).where(Payment.worker_id.in_(worker_ids))).all()
    workers = {w.id: w for w in session.exec(select(Worker).where(Worker.id.in_(worker_ids))).all()}
    affected = []
    for p in payments:
        if p.period_start <= crate_date <= p.period_end:
            w = workers.get(p.worker_id)
            affected.append({
                "worker_id": p.worker_id,
                "worker_name": (w.name if w else "") or p.worker_id,
                "period_start": p.period_start,
                "period_end": p.period_end,
            })
    return affected


@router.patch("/{record_uuid}")
def edit_harvest_record(record_uuid: str, body: HarvestRecordEdit,
                         session: Session = Depends(get_session)):
    """Admin correction of a field-captured crate - a wrong worker picked on
    the keypad or a fat-fingered weight is otherwise a wrong wage with no
    remedy short of editing the database by hand.

    Deliberately narrow: only worker/block/weight/deduction, and only through
    this endpoint. A field device re-syncing the same crate no longer overwrites
    an edit - see the edited_at check in routers/sync.py's upsert branch."""
    record = session.get(HarvestRecord, record_uuid)
    if not record:
        raise HTTPException(404, "Harvest record not found")

    old_worker_id = record.worker_id
    new_worker_id = body.worker_id if body.worker_id is not None else record.worker_id
    new_weight_kg = body.weight_kg if body.weight_kg is not None else record.weight_kg
    new_deduction_kg = body.deduction_kg if body.deduction_kg is not None else record.deduction_kg
    new_block_id = body.block_id if body.block_id is not None else record.block_id

    if new_worker_id != old_worker_id:
        worker = session.get(Worker, new_worker_id)
        if not worker:
            raise HTTPException(400, "Unknown worker")
        if not worker.active:
            raise HTTPException(400, f"{worker.name or worker.id} is not an active worker")

    if new_block_id != record.block_id:
        block = session.get(Block, new_block_id)
        if not block:
            raise HTTPException(400, "Unknown block")
        if not block.active:
            raise HTTPException(400, f"Block {block.name or block.id} is not active")

    if new_weight_kg <= 0:
        raise HTTPException(400, "Weight must be greater than zero")
    # Net kg (weight - deduction) is what every total in the app is built
    # from - letting it go negative would quietly corrupt wages and reports
    # rather than raise anywhere obvious.
    if new_deduction_kg < 0 or new_deduction_kg > new_weight_kg:
        raise HTTPException(400, "Deduction must be between 0 and the weight")

    record.worker_id = new_worker_id
    record.weight_kg = new_weight_kg
    record.deduction_kg = new_deduction_kg
    record.block_id = new_block_id
    record.edited_at = datetime.now(timezone.utc)
    # No accounts any more, so there is no username to record - and with one
    # farm admin there never was more than one possible answer. "admin" is
    # what the Admin app already falls back to for rows that predate this
    # field, so the two read identically in the crate list.
    record.edited_by = "admin"
    session.add(record)
    session.commit()
    session.refresh(record)

    lot_totals = None
    if record.lot_id:
        lot = session.get(Lot, record.lot_id)
        if lot:
            recompute_lot_totals(session, lot)
            session.commit()
            session.refresh(lot)
            lot_totals = {"lot_id": lot.id, "total_crates": lot.total_crates, "total_kg": lot.total_kg}

    affected_worker_ids = {old_worker_id, new_worker_id} - {None}
    wages_affected = _wages_affected(session, record, affected_worker_ids)

    return {"record": record, "lot": lot_totals, "wages_affected": wages_affected}


def delete_crate(session: Session, record_uuid: str, deleted_by: str) -> Optional[HarvestRecord]:
    """Remove a crate and leave its tombstone, so a field device replaying it
    later is ignored by routers/sync.py rather than bringing it back. The
    tombstone is written even when the crate never reached the server - a
    field undo can race the crate's own upload. If it was the slip's last
    crate, the slip goes too. Returns the removed record (None if it wasn't
    here). Does not commit."""
    record = session.get(HarvestRecord, record_uuid)
    lot = session.get(Lot, record.lot_id) if record and record.lot_id else None
    if record:
        session.delete(record)
    if not session.get(DeletedHarvestRecord, record_uuid):
        session.add(DeletedHarvestRecord(
            uuid=record_uuid, lot_id=lot.id if lot else None,
            slip_number=lot.slip_number if lot else None,
            deleted_at=datetime.now(timezone.utc), deleted_by=deleted_by))
    if lot:
        delete_lot_if_empty(session, lot.id)
    return record


@router.delete("/{record_uuid}")
def delete_harvest_record(record_uuid: str, session: Session = Depends(get_session)):
    """Admin removal of a crate that should never have been logged - a
    double-tap on Save Crate, or a test crate. Same follow-through as an edit:
    the lot's totals are re-derived and any wage period already calculated
    for that worker is reported back."""
    record = session.get(HarvestRecord, record_uuid)
    if not record:
        raise HTTPException(404, "Harvest record not found")
    # Read before the delete - the row is gone afterwards.
    wages_affected = _wages_affected(session, record, {record.worker_id} - {None})
    lot_id = record.lot_id
    delete_crate(session, record_uuid, "admin")
    session.commit()

    lot_totals = None
    if lot_id:
        lot = session.get(Lot, lot_id)
        if lot:
            recompute_lot_totals(session, lot)
            session.commit()
            session.refresh(lot)
            lot_totals = {"lot_id": lot.id, "total_crates": lot.total_crates, "total_kg": lot.total_kg}
        else:
            # That was its last crate - delete_crate removed the slip too.
            lot_totals = {"lot_id": lot_id, "deleted": True}

    return {"lot": lot_totals, "wages_affected": wages_affected}
