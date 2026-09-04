from datetime import date

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from db import get_session
from models import Lot, Supplier
from security import require_admin_client
from timeutil import day_bounds

router = APIRouter(prefix="/api/suppliers", tags=["suppliers"])


@router.get("")
def list_suppliers(session: Session = Depends(get_session)):
    """No auth required - field/pack house devices need this list too
    (e.g. to populate the 'log external delivery' supplier dropdown)."""
    return session.exec(select(Supplier)).all()


@router.post("")
def upsert_supplier(supplier: Supplier, session: Session = Depends(get_session), _admin=Depends(require_admin_client)):
    session.merge(supplier)
    session.commit()
    return {"ok": True}


@router.delete("/{supplier_id}")
def deactivate_supplier(supplier_id: int, session: Session = Depends(get_session),
                         _admin=Depends(require_admin_client)):
    obj = session.get(Supplier, supplier_id)
    if obj:
        obj.active = False
        session.add(obj)
        session.commit()
    return {"ok": True}


def compute_supplier_billing(session: Session, supplier_id: int, period_start: date, period_end: date) -> dict:
    """Facility-use fee for a supplier's fruit received in the period -
    per-kg rate if set, else per-crate. Only received lots count (fruit
    still in transit hasn't used the pack house yet)."""
    supplier = session.get(Supplier, supplier_id)
    start_dt, end_dt = day_bounds(period_start, period_end)
    # The received_at range alone defines "received" (NULL never matches a
    # range); filtering on status == received would silently drop lots whose
    # status advanced to processing_complete after grading - under-billing.
    lots = session.exec(
        select(Lot)
        .where(Lot.supplier_id == supplier_id,
               Lot.received_at >= start_dt, Lot.received_at <= end_dt)
        .order_by(Lot.received_at)
    ).all()
    total_crates = sum(l.total_crates for l in lots)
    total_kg = round(sum(l.total_kg for l in lots), 1)

    rate_type = "per_kg" if supplier and supplier.packing_rate_per_kg > 0 else "per_crate"
    rate = 0.0
    if supplier:
        rate = supplier.packing_rate_per_kg if rate_type == "per_kg" else supplier.packing_rate_per_crate
    amount_due = round((total_kg if rate_type == "per_kg" else total_crates) * rate, 2)

    return {
        "supplier": supplier,
        "period_start": period_start,
        "period_end": period_end,
        "lots": lots,
        "total_crates": total_crates,
        "total_kg": total_kg,
        "rate_type": rate_type,
        "rate": rate,
        "amount_due": amount_due,
    }


@router.get("/{supplier_id}/billing")
def supplier_billing(supplier_id: int, period_start: date, period_end: date,
                      session: Session = Depends(get_session), _admin=Depends(require_admin_client)):
    data = compute_supplier_billing(session, supplier_id, period_start, period_end)
    return {
        "supplier": data["supplier"],
        "period_start": data["period_start"],
        "period_end": data["period_end"],
        "total_crates": data["total_crates"],
        "total_kg": data["total_kg"],
        "rate_type": data["rate_type"],
        "rate": data["rate"],
        "amount_due": data["amount_due"],
        "lots": [
            {"slip_number": l.slip_number, "received_at": l.received_at,
             "crates": l.total_crates, "kg": round(l.total_kg, 1)}
            for l in data["lots"]
        ],
    }
