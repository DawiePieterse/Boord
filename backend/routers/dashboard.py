from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from db import get_own_supplier_id, get_session
from models import Block, HarvestRecord, Supplier, Worker
from routers.payments import _supplier_display_name, _worker_ids_for_supplier, _worker_totals
from security import get_current_admin
from timeutil import day_bounds

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def dashboard_summary(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                       session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Active-entity counts + per-worker and per-block breakdowns for the
    admin Dashboard tab. "Active" means had harvest activity within the
    filtered period/supplier, not a static master-data active flag - so
    these numbers move with the filters like everything else on the screen."""
    start_dt, end_dt = day_bounds(period_start, period_end)
    worker_ids = _worker_ids_for_supplier(session, supplier_id)
    query = select(HarvestRecord).where(HarvestRecord.timestamp >= start_dt, HarvestRecord.timestamp <= end_dt)
    if worker_ids is not None:
        query = query.where(HarvestRecord.worker_id.in_(worker_ids))
    records = session.exec(query).all()

    active_teams = {r.team_id for r in records if r.team_id}
    active_workers = {r.worker_id for r in records if r.worker_id}
    active_blocks = {r.block_id for r in records if r.block_id}

    crate_counts: dict[str, int] = {}
    for r in records:
        if r.worker_id:
            crate_counts[r.worker_id] = crate_counts.get(r.worker_id, 0) + 1

    # Keep the rate row: with none configured _worker_totals values every
    # worker at 0.00, and a dashboard showing R0.00 as though it were a real
    # figure is worse than one that says no rate is set.
    totals, rate_setting = _worker_totals(session, period_start, period_end, supplier_id)
    workers_by_id = {w.id: w for w in session.exec(select(Worker)).all()}
    suppliers_by_id = {s.id: s for s in session.exec(select(Supplier)).all()}
    own_id = get_own_supplier_id(session)
    own_supplier = suppliers_by_id.get(own_id)
    own_name = own_supplier.name if own_supplier else "Own fruit"
    workers = []
    for worker_id, data in totals.items():
        w = workers_by_id.get(worker_id)
        crates = crate_counts.get(worker_id, 0)
        workers.append({
            "worker_id": worker_id,
            "name": w.name if w else worker_id,
            "supplier_name": _supplier_display_name(w, suppliers_by_id, own_id, own_name),
            "crates": crates,
            "total_kg": round(data["total_kg"], 1),
            "amount_due": round(data["amount"], 2),
            "avg_kg_crate": round(data["total_kg"] / crates, 1) if crates else 0,
        })
    workers.sort(key=lambda w: w["total_kg"], reverse=True)

    block_totals: dict[str, dict] = {}
    for r in records:
        if not r.block_id:
            continue
        entry = block_totals.setdefault(r.block_id, {"crates": 0, "total_kg": 0.0})
        entry["crates"] += 1
        entry["total_kg"] += r.weight_kg - r.deduction_kg
    blocks_by_id = {b.id: b for b in session.exec(select(Block)).all()}
    blocks = []
    for block_id, data in block_totals.items():
        b = blocks_by_id.get(block_id)
        total_kg = round(data["total_kg"], 1)
        blocks.append({
            "block_id": block_id,
            "name": b.name if b else block_id,
            "crates": data["crates"],
            "total_kg": total_kg,
            "avg_kg_crate": round(total_kg / data["crates"], 1) if data["crates"] else 0,
            "avg_kg_tree": round(total_kg / b.trees, 1) if b and b.trees else None,
            "avg_kg_hectare": round(total_kg / b.hectares, 1) if b and b.hectares else None,
        })
    blocks.sort(key=lambda b: (b["name"] or "").lower())

    return {
        "active_teams": len(active_teams),
        "active_workers": len(active_workers),
        "active_blocks": len(active_blocks),
        # False on a new install until a wage rate is entered. Every
        # amount_due above is 0.00 in that state, and the UI needs to be able
        # to say so rather than presenting zeroes as real earnings.
        "rate_configured": rate_setting is not None,
        "workers": workers,
        "blocks": blocks,
    }
