import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from db import get_own_supplier_id, get_session, latest_rate_setting, supplier_map
from excel_io import tabular_response
from models import HarvestRecord, Payment, RateType, Worker
from security import require_admin_client
from timeutil import day_bounds

# Every endpoint here is admin-only.
router = APIRouter(prefix="/api/payments", tags=["payments"], dependencies=[Depends(require_admin_client)])


def _worker_ids_for_supplier(session: Session, supplier_id: Optional[int]) -> Optional[set]:
    """Resolve which workers belong to a supplier filter, or None for no filter.
    Own-fruit workers carry the own-fruit supplier's id like any other
    (migration 5e0b7d3c21aa), so this is a plain equality match."""
    if supplier_id is None:
        return None
    return set(session.exec(select(Worker.id).where(Worker.supplier_id == supplier_id)).all())


def harvest_records_between(session: Session, start_day: date, end_day: Optional[date] = None,
                            supplier_id: Optional[int] = None) -> list[HarvestRecord]:
    """Crates picked over a span of local days, optionally for one supplier's workers."""
    start_dt, end_dt = day_bounds(start_day, end_day)
    query = select(HarvestRecord).where(HarvestRecord.timestamp >= start_dt, HarvestRecord.timestamp <= end_dt)
    worker_ids = _worker_ids_for_supplier(session, supplier_id)
    if worker_ids is not None:
        query = query.where(HarvestRecord.worker_id.in_(worker_ids))
    return session.exec(query).all()


def suppliers_with_own(session: Session) -> tuple[dict, str]:
    """(suppliers_by_id, own-fruit display name)."""
    suppliers_by_id = supplier_map(session)
    own_supplier = suppliers_by_id.get(get_own_supplier_id(session))
    return suppliers_by_id, own_supplier.name if own_supplier else "Own fruit"


def _supplier_display_name(worker: Optional[Worker], suppliers_by_id: dict, own_name: str) -> str:
    """The supplier name to show for a worker, for grouping the wage sheet. A
    payment whose worker has since been removed files under own fruit."""
    if not worker:
        return own_name
    supplier = suppliers_by_id.get(worker.supplier_id)
    return supplier.name if supplier else "Unknown"


def _tier_rate_for_weight(weight_kg: float, tiers: dict[str, float]) -> float:
    """Classify a crate into the nearest configured size tier (largest tier
    key <= the crate's weight, else the smallest tier) and return its rate.
    Tiers mirror the farm's real per-crate-size wage categories (e.g. 1 /
    1.5 / 2 kg classes) rather than a flat rate per kg."""
    if not tiers:
        return 0.0
    rates = {float(k): v for k, v in tiers.items()}
    return rates[max((k for k in rates if weight_kg >= k), default=min(rates))]


def _worker_totals(session: Session, records: list[HarvestRecord]):
    # Deliberately the newest RateSetting regardless of the period being
    # calculated, not the rate in effect during it - confirmed farm policy:
    # wages are always recalculated using the latest rate.
    setting = latest_rate_setting(session)
    tiers = json.loads(setting.tier_rates_json) if setting else {}

    totals: dict[str, dict] = {}
    for r in records:
        if not r.worker_id:
            continue
        net_kg = r.weight_kg - r.deduction_kg
        entry = totals.setdefault(r.worker_id, {"total_kg": 0.0, "amount": 0.0})
        entry["total_kg"] += net_kg
        if setting and setting.rate_type == RateType.per_crate_tier:
            entry["amount"] += _tier_rate_for_weight(net_kg, tiers)
        else:
            entry["amount"] += net_kg * (setting.default_rate_per_kg if setting else 0.0)
    return totals, setting


@router.post("/calculate")
def calculate_payments(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                        session: Session = Depends(get_session)):
    totals, setting = _worker_totals(
        session, harvest_records_between(session, period_start, period_end, supplier_id))
    if setting is None:
        # No wage rate has ever been set on this install. Refuse rather than
        # fall through to _worker_totals' 0.0 default and write a full set of
        # Payment rows at R0.00 - those are stored records that look like a
        # completed wage run, and "everyone earned nothing" is a far more
        # expensive thing to discover late than an error here. A seeded
        # default rate would be worse still: it would produce a plausible
        # payslip at another farm's number.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No wage rate has been set. Add one under Settings before calculating wages.",
        )
    rate_applied = setting.default_rate_per_kg
    existing_by_worker = {p.worker_id: p for p in session.exec(
        select(Payment).where(Payment.period_start == period_start, Payment.period_end == period_end)
    ).all()}
    results = []
    for worker_id, data in totals.items():
        payment = existing_by_worker.get(worker_id) or Payment(worker_id=worker_id, period_start=period_start, period_end=period_end)
        payment.total_kg = round(data["total_kg"], 1)
        payment.rate_applied = rate_applied
        payment.amount_due = round(data["amount"], 2)
        session.add(payment)
        results.append(payment)
    session.commit()
    for p in results:
        session.refresh(p)
    return results


@router.get("")
def list_payments(period_start: Optional[date] = None, period_end: Optional[date] = None,
                   session: Session = Depends(get_session)):
    """Admin-only, like every other endpoint in this router. It was the one
    that lacked the dependency, so anyone who could reach the server could
    read every worker's amount_due - the farm's whole payroll, to anyone who
    found the port. That port is still open to the farm Wi-Fi for the Field
    and Pack House screens, so the dependency is what stands between the two.
    No frontend code calls this (the admin screen uses /calculate and
    /export), so guarding it changes nothing for the app."""
    query = select(Payment)
    if period_start:
        query = query.where(Payment.period_start == period_start)
    if period_end:
        query = query.where(Payment.period_end == period_end)
    return session.exec(query).all()


@router.get("/export")
def export_payments(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                     fmt: str = Query("xlsx", pattern="^(csv|xlsx)$"),
                     session: Session = Depends(get_session)):
    payments = session.exec(
        select(Payment).where(Payment.period_start == period_start, Payment.period_end == period_end)
    ).all()
    worker_ids = _worker_ids_for_supplier(session, supplier_id)
    if worker_ids is not None:
        payments = [p for p in payments if p.worker_id in worker_ids]
    workers = {w.id: w for w in session.exec(select(Worker)).all()}
    suppliers_by_id, own_name = suppliers_with_own(session)

    groups: dict[str, list[Payment]] = {}
    for p in payments:
        name = _supplier_display_name(workers.get(p.worker_id), suppliers_by_id, own_name)
        groups.setdefault(name, []).append(p)
    group_names = sorted(groups.keys(), key=lambda n: (n != own_name, n))

    headers = ["Supplier", "Emp Nr", "Naam & Van", "Total Kg", "Rate", "Amount Due"]
    rows = []
    for name in group_names:
        group_payments = groups[name]
        worker_count = len(group_payments)
        total_kg = round(sum(p.total_kg for p in group_payments), 1)
        total_wages = round(sum(p.amount_due for p in group_payments), 2)
        summary = (f"{name} - {worker_count} worker{'s' if worker_count != 1 else ''} - "
                   f"{total_kg} kg - R{total_wages:.2f} total wages")
        rows.append([summary, "", "", "", "", ""])
        for p in group_payments:
            w = workers.get(p.worker_id)
            rows.append([
                name, p.worker_id, w.name if w else "", p.total_kg, p.rate_applied, p.amount_due,
            ])

    return tabular_response(headers, rows, fmt, f"Wages_{period_start}_{period_end}", "Payments")
