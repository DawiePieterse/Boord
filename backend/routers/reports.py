import io
import os
from collections import Counter
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlmodel import Session, select

from db import DATA_DIR, get_session
from excel_io import rows_to_xlsx_bytes
from models import Block, Device, HarvestRecord, Lot, \
    ReceivingRecord, Supplier, SystemSetting, Team, Worker
from routers.dashboard import dashboard_summary
from routers.lots import list_in_transit, list_pending, list_received
from routers.payments import _worker_ids_for_supplier
from security import get_current_admin
from timeutil import day_bounds, local_str, to_local

router = APIRouter(prefix="/api/reports", tags=["reports"])
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

REPORTS_DIR = os.path.join(DATA_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)


def _xlsx_response(headers, rows, sheet_title, filename):
    data = rows_to_xlsx_bytes(headers, rows, sheet_title)
    # Every generated report is also kept on disk in data/reports/, so it's
    # swept up by whatever backup routine already covers the data/ folder -
    # not just left in whichever browser downloaded it.
    with open(os.path.join(REPORTS_DIR, filename), "wb") as f:
        f.write(data)
    return Response(content=data, media_type=XLSX_MEDIA,
                     headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _mean(values: list, places: int):
    """Blank rather than 0 when nothing was measured - an empty cell reads as
    "not recorded", where 0 would read as a freezing morning."""
    if not values:
        return ""
    return round(sum(values) / len(values), places) if places else round(sum(values) / len(values))


@router.get("/daily-harvest")
def daily_harvest_report(day: date = Query(default_factory=date.today), supplier_id: Optional[int] = None,
                          session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    start, end = day_bounds(day)
    query = select(HarvestRecord).where(HarvestRecord.timestamp >= start, HarvestRecord.timestamp <= end)
    worker_ids = _worker_ids_for_supplier(session, supplier_id)
    if worker_ids is not None:
        query = query.where(HarvestRecord.worker_id.in_(worker_ids))
    records = session.exec(query).all()
    blocks = {b.id: b for b in session.exec(select(Block)).all()}
    teams = {t.id: t for t in session.exec(select(Team)).all()}

    totals: dict = {}
    for r in records:
        key = (r.block_id, r.team_id)
        entry = totals.setdefault(key, {"crates": 0, "kg": 0.0, "temps": [], "humidity": [],
                                         "conditions": Counter()})
        entry["crates"] += 1
        entry["kg"] += r.weight_kg - r.deduction_kg
        # Weather is stamped per crate at check-in and is absent on crates
        # captured before that existed, or when the lookup failed - so each
        # measure is averaged over whichever crates actually carry it.
        if r.weather_temp is not None:
            entry["temps"].append(r.weather_temp)
        if r.weather_humidity is not None:
            entry["humidity"].append(r.weather_humidity)
        if r.weather_condition:
            entry["conditions"][r.weather_condition] += 1

    headers = ["Date", "Block", "Variety", "Team", "Induna", "Crates", "Kg",
               "Avg Temp (°C)", "Avg Humidity (%)", "Conditions"]
    rows = []
    for (block_id, team_id), data in sorted(totals.items(), key=lambda x: (x[0][0] or "", x[0][1] or "")):
        block = blocks.get(block_id)
        team = teams.get(team_id)
        rows.append([
            day.isoformat(), block_id or "", block.variety if block else "", team.name if team else team_id or "",
            team.induna if team else "", data["crates"], round(data["kg"], 1),
            _mean(data["temps"], 1), _mean(data["humidity"], 0),
            # Whatever it was doing for most of the picking, so a block picked
            # through a passing shower doesn't read as a clear morning.
            data["conditions"].most_common(1)[0][0] if data["conditions"] else "",
        ])
    return _xlsx_response(headers, rows, "Daily Harvest", f"Daily_Harvest_{day}.xlsx")


@router.get("/harvest-data")
def harvest_data_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                         session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Daaglikse Oesdata / Daily Harvest Data: the block x date pivot behind
    the paper "Daaglikse Oesdata" log - one column per block, one row per
    day, matching how the paper form and the season workbook both lay it
    out."""
    start, end = day_bounds(period_start, period_end)
    worker_ids = _worker_ids_for_supplier(session, supplier_id)
    query = select(HarvestRecord).where(HarvestRecord.timestamp >= start, HarvestRecord.timestamp <= end)
    if worker_ids is not None:
        query = query.where(HarvestRecord.worker_id.in_(worker_ids))
    records = session.exec(query).all()
    blocks = {b.id: b for b in session.exec(select(Block)).all()}

    block_days: dict = {}   # block_id -> {day: kg}
    block_totals: dict = {}  # block_id -> kg
    day_totals: dict = {}   # day -> {"kg", "workers", "crates"}
    for r in records:
        local_ts = to_local(r.timestamp)
        if local_ts is None:
            continue
        day = local_ts.date()
        kg = r.weight_kg - r.deduction_kg
        block_days.setdefault(r.block_id, {})
        block_days[r.block_id][day] = block_days[r.block_id].get(day, 0.0) + kg
        block_totals[r.block_id] = block_totals.get(r.block_id, 0.0) + kg
        dt = day_totals.setdefault(day, {"kg": 0.0, "workers": set(), "crates": 0})
        dt["kg"] += kg
        dt["crates"] += 1
        if r.worker_id:
            dt["workers"].add(r.worker_id)

    days = sorted(day_totals.keys())
    block_ids = sorted(block_totals, key=lambda bid: ((blocks.get(bid).name if blocks.get(bid) else "") or bid or ""),
                        reverse=True)
    block_names = [(blocks[bid].name if blocks.get(bid) and blocks[bid].name else (bid or "")) for bid in block_ids]

    headers = ["Date"] + block_names + \
              ["Daily Total Kg", "Number of Workers", "Avg Kg/Worker", "Number of Crates", "Avg Kg/Crate"]
    tail_blank = ["", "", "", "", ""]

    rows = [
        ["Variety"] + [blocks[bid].variety if bid in blocks else "" for bid in block_ids] + tail_blank,
        ["Trees"] + [blocks[bid].trees if bid in blocks else "" for bid in block_ids] + tail_blank,
        ["Hectares"] + [blocks[bid].hectares if bid in blocks else "" for bid in block_ids] + tail_blank,
        [""] * len(headers),
    ]
    for d in days:
        dt = day_totals[d]
        row = [d.isoformat()]
        row += [round(block_days[bid][d], 1) if d in block_days.get(bid, {}) else "" for bid in block_ids]
        row += [
            round(dt["kg"], 1), len(dt["workers"]),
            round(dt["kg"] / len(dt["workers"]), 1) if dt["workers"] else "",
            dt["crates"],
            round(dt["kg"] / dt["crates"], 1) if dt["crates"] else "",
        ]
        rows.append(row)

    grand_total_kg = round(sum(block_totals.values()), 1)
    total_trees = sum((blocks[bid].trees if bid in blocks else 0) for bid in block_ids)
    total_hectares = sum((blocks[bid].hectares if bid in blocks else 0) for bid in block_ids)

    rows.append([""] * len(headers))
    rows.append(["Total Kg"] + [round(block_totals[bid], 1) for bid in block_ids] +
                [grand_total_kg, "", "", "", ""])
    rows.append(["Avg Kg/Tree"] + [
        round(block_totals[bid] / blocks[bid].trees, 2) if bid in blocks and blocks[bid].trees else ""
        for bid in block_ids
    ] + [round(grand_total_kg / total_trees, 2) if total_trees else "", "", "", "", ""])
    rows.append(["Avg Kg/Hectare"] + [
        round(block_totals[bid] / blocks[bid].hectares, 2) if bid in blocks and blocks[bid].hectares else ""
        for bid in block_ids
    ] + [round(grand_total_kg / total_hectares, 2) if total_hectares else "", "", "", "", ""])

    return _xlsx_response(headers, rows, "Daily Harvest Data",
                           f"Daily_Harvest_Data_{period_start}_{period_end}.xlsx")


@router.get("/lot-receiving")
def lot_receiving_report(date_from: date, date_to: date, supplier_id: Optional[int] = None,
                          session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    start, end = day_bounds(date_from, date_to)
    query = select(Lot).where(Lot.timestamp >= start, Lot.timestamp <= end)
    if supplier_id is not None:
        query = query.where(Lot.supplier_id == supplier_id)
    lots = session.exec(query.order_by(Lot.timestamp)).all()
    receiving_by_lot = {}
    for rec in session.exec(select(ReceivingRecord)).all():
        receiving_by_lot.setdefault(rec.lot_id, rec)
    suppliers = {s.id: s for s in session.exec(select(Supplier)).all()}

    headers = ["Slip Number", "Supplier", "Dispatched", "Team", "Driver", "Expected Crates", "Kg Sent",
               "Status", "Received At", "Actual Crates", "Discrepancy", "Condition", "Waste Kg",
               "Temp (°C)", "Humidity (%)", "Weather"]
    rows = []
    for lot in lots:
        rec = receiving_by_lot.get(lot.id)
        supplier = suppliers.get(lot.supplier_id)
        rows.append([
            lot.slip_number, supplier.name if supplier else "",
            local_str(lot.timestamp), lot.team_id, lot.driver,
            lot.total_crates, round(lot.total_kg, 1), lot.status.value,
            local_str(lot.received_at),
            rec.actual_crates if rec else "", rec.discrepancy if rec else "",
            rec.condition if rec else "", round(rec.waste_kg, 1) if rec else "",
            lot.weather_temp if lot.weather_temp is not None else "",
            lot.weather_humidity if lot.weather_humidity is not None else "",
            lot.weather_condition or "",
        ])
    return _xlsx_response(headers, rows, "Lot & Receiving", f"Lot_Receiving_{date_from}_{date_to}.xlsx")


@router.get("/picking-notes")
def picking_notes_report(date_from: date, date_to: date, supplier_id: Optional[int] = None,
                          session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    start, end = day_bounds(date_from, date_to)
    query = select(Lot).where(Lot.timestamp >= start, Lot.timestamp <= end)
    if supplier_id is not None:
        query = query.where(Lot.supplier_id == supplier_id)
    lots = session.exec(query.order_by(Lot.timestamp)).all()
    receiving_by_lot = {}
    for rec in session.exec(select(ReceivingRecord)).all():
        receiving_by_lot.setdefault(rec.lot_id, rec)
    suppliers = {s.id: s for s in session.exec(select(Supplier)).all()}
    teams = {t.id: t for t in session.exec(select(Team)).all()}

    headers = ["Slip Number", "Date", "Time", "Block", "Team", "Crates Sent", "Crates Received",
               "Total Kg", "Driver", "Supplier", "Condition", "Notes", "Received By",
               "Weather", "Temp (°C)", "Humidity (%)"]
    entries = []
    for lot in lots:
        rec = receiving_by_lot.get(lot.id)
        supplier = suppliers.get(lot.supplier_id)
        team = teams.get(lot.team_id)
        # A lot has no block field of its own - it's stamped per crate, so the
        # slip's block(s) are whatever its HarvestRecords were captured against.
        crates = session.exec(select(HarvestRecord).where(HarvestRecord.lot_id == lot.id)).all()
        blocks = sorted({c.block_id for c in crates if c.block_id})
        local_ts = to_local(lot.timestamp)
        team_name = team.name if team else lot.team_id or ""
        entries.append((
            local_ts.date() if local_ts else date.min, team_name, local_ts,
            [
                lot.slip_number, local_ts.strftime("%Y-%m-%d") if local_ts else "",
                local_ts.strftime("%H:%M") if local_ts else "", ", ".join(blocks),
                team_name, lot.total_crates,
                rec.actual_crates if rec else "", round(lot.total_kg, 1), lot.driver,
                supplier.name if supplier else "",
                rec.condition if rec else "", rec.notes if rec else "", rec.received_by if rec else "",
                lot.weather_condition or "",
                lot.weather_temp if lot.weather_temp is not None else "",
                lot.weather_humidity if lot.weather_humidity is not None else "",
            ],
        ))
    entries.sort(key=lambda e: (e[0], e[1], e[2]))
    rows = [e[3] for e in entries]
    return _xlsx_response(headers, rows, "Picking Notes", f"Picking_Notes_{date_from}_{date_to}.xlsx")


@router.get("/team-picking-list")
def team_picking_list_report(date_from: date, date_to: date, supplier_id: Optional[int] = None,
                              session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Span Pluklys / Team Picking List: one row per team per day, with the
    day's blocks (kg + deductions) and dispatched lots (crates, time, slip
    number) laid out as repeating column groups - matching the fields on the
    paper "Inligting van die Dag" slip an induna's team fills in by hand."""
    start, end = day_bounds(date_from, date_to)

    worker_ids = _worker_ids_for_supplier(session, supplier_id)
    hr_query = select(HarvestRecord).where(HarvestRecord.timestamp >= start, HarvestRecord.timestamp <= end)
    if worker_ids is not None:
        hr_query = hr_query.where(HarvestRecord.worker_id.in_(worker_ids))
    records = session.exec(hr_query).all()

    lot_query = select(Lot).where(Lot.timestamp >= start, Lot.timestamp <= end)
    if supplier_id is not None:
        lot_query = lot_query.where(Lot.supplier_id == supplier_id)
    lots = session.exec(lot_query).all()

    teams = {t.id: t for t in session.exec(select(Team)).all()}
    blocks = {b.id: b for b in session.exec(select(Block)).all()}
    devices = {d.id: d for d in session.exec(select(Device)).all()}

    groups: dict = {}

    def _group(team_id, day):
        return groups.setdefault((team_id, day), {
            "devices": Counter(), "workers": set(), "blocks": {}, "lots": [],
        })

    for r in records:
        local_ts = to_local(r.timestamp)
        day = local_ts.date() if local_ts else date.min
        g = _group(r.team_id, day)
        if r.device_id:
            g["devices"][r.device_id] += 1
        if r.worker_id:
            g["workers"].add(r.worker_id)
        entry = g["blocks"].setdefault(r.block_id, {"kg": 0.0, "deductions": 0.0})
        entry["kg"] += r.weight_kg
        entry["deductions"] += r.deduction_kg

    for lot in lots:
        local_ts = to_local(lot.timestamp)
        day = local_ts.date() if local_ts else date.min
        g = _group(lot.team_id, day)
        if not g["devices"] and lot.device_id:
            g["devices"][lot.device_id] += 1
        g["lots"].append((local_ts, lot))

    max_blocks = max((len(g["blocks"]) for g in groups.values()), default=0)
    max_lots = max((len(g["lots"]) for g in groups.values()), default=0)

    headers = ["Data Capturer", "Team", "Induna", "Date", "Workers", "Total Deductions"]
    for i in range(1, max_blocks + 1):
        headers += [f"Block {i} Name", f"Block {i} Total Kg", f"Block {i} Deductions"]
    for i in range(1, max_lots + 1):
        headers += [f"Lot {i} Crates", f"Lot {i} Time", f"Lot {i} Slip Number"]

    rows = []
    for (team_id, day), g in sorted(groups.items(), key=lambda kv: (kv[0][1], teams.get(kv[0][0]).name if teams.get(kv[0][0]) else kv[0][0] or "")):
        team = teams.get(team_id)
        device = devices.get(g["devices"].most_common(1)[0][0]) if g["devices"] else None
        total_deductions = sum(b["deductions"] for b in g["blocks"].values())
        row = [
            device.data_capturer if device else "",
            team.name if team else team_id or "", team.induna if team else "",
            day.isoformat() if day != date.min else "", len(g["workers"]), round(total_deductions, 1),
        ]
        for block_id in sorted(g["blocks"], key=lambda x: x or ""):
            block = blocks.get(block_id)
            b = g["blocks"][block_id]
            row += [block.name or block_id if block else (block_id or ""), round(b["kg"], 1), round(b["deductions"], 1)]
        row += [""] * (3 * (max_blocks - len(g["blocks"])))
        for local_ts, lot in sorted(g["lots"], key=lambda x: x[1].timestamp):
            row += [lot.total_crates, local_ts.strftime("%H:%M") if local_ts else "", lot.slip_number]
        row += [""] * (3 * (max_lots - len(g["lots"])))
        rows.append(row)

    return _xlsx_response(headers, rows, "Team Picking List", f"Team_Picking_List_{date_from}_{date_to}.xlsx")


def _lot_rows(lots_data: list) -> list:
    return [[
        l["slip_number"], l["supplier_name"], l["team_id"] or "", l["driver"], l["total_crates"], l["total_kg"],
        l["age_minutes"],
    ] for l in lots_data]


@router.get("/harvesting-list")
def harvesting_list_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                            session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    lots_data = list_pending(supplier_id=supplier_id, period_start=period_start, period_end=period_end,
                              session=session)
    headers = ["Slip Number", "Supplier", "Team", "Driver", "Crates", "Kg", "Age (min)"]
    return _xlsx_response(headers, _lot_rows(lots_data), "Harvesting",
                           f"Harvesting_{period_start}_{period_end}.xlsx")


@router.get("/in-transit-list")
def in_transit_list_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                            session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    lots_data = list_in_transit(supplier_id=supplier_id, period_start=period_start, period_end=period_end,
                                 session=session)
    headers = ["Slip Number", "Supplier", "Team", "Driver", "Crates", "Kg", "Age (min)"]
    return _xlsx_response(headers, _lot_rows(lots_data), "In Transit",
                           f"In_Transit_{period_start}_{period_end}.xlsx")


@router.get("/received-list")
def received_list_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                          session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Matches the packhouse's paper "Packhouse Receipt Lists" slip: date and
    time split out, plus the receiving block and rejected (waste) amount."""
    lots_data = list_received(period_start=period_start, period_end=period_end, supplier_id=supplier_id,
                               session=session)
    lot_ids = [l["id"] for l in lots_data]
    receiving_by_lot = {}
    for rec in session.exec(select(ReceivingRecord).where(ReceivingRecord.lot_id.in_(lot_ids))).all():
        receiving_by_lot.setdefault(rec.lot_id, rec)
    blocks_by_lot = {}
    for c in session.exec(select(HarvestRecord).where(HarvestRecord.lot_id.in_(lot_ids))).all():
        if c.block_id:
            blocks_by_lot.setdefault(c.lot_id, set()).add(c.block_id)

    headers = ["Slip Number", "Date", "Time", "Block", "Supplier", "Team", "Driver", "Crates", "Kg",
               "Rejected"]
    rows = []
    for l in lots_data:
        rec = receiving_by_lot.get(l["id"])
        local_ts = to_local(l["received_at"])
        rows.append([
            l["slip_number"],
            local_ts.strftime("%Y-%m-%d") if local_ts else "",
            local_ts.strftime("%H:%M") if local_ts else "",
            ", ".join(sorted(blocks_by_lot.get(l["id"], []))),
            l["supplier_name"], l["team_id"] or "", l["driver"], l["total_crates"], l["total_kg"],
            round(rec.waste_kg, 1) if rec else "",
        ])
    return _xlsx_response(headers, rows, "Received", f"Received_{period_start}_{period_end}.xlsx")


@router.get("/worker-harvest")
def worker_harvest_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                           session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    summary = dashboard_summary(period_start, period_end, supplier_id, session, admin)
    headers = ["Emp Nr", "Name", "Supplier", "Crates", "Kg", "Amount Due", "Avg Kg/Crate"]
    rows = [[
        w["worker_id"], w["name"], w["supplier_name"], w["crates"], w["total_kg"], w["amount_due"],
        w["avg_kg_crate"],
    ] for w in summary["workers"]]
    return _xlsx_response(headers, rows, "Worker Harvest", f"Worker_Harvest_{period_start}_{period_end}.xlsx")


@router.get("/litchi-wages")
def litchi_wages_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                         session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Lietsjie Lone / Litchi Wages: one row per worker, with the crates that
    worker harvested broken out per day - one column per day worked, so the
    wage clerk can read a whole pay period off a single row."""
    start, end = day_bounds(period_start, period_end)
    worker_ids = _worker_ids_for_supplier(session, supplier_id)
    query = select(HarvestRecord).where(HarvestRecord.timestamp >= start, HarvestRecord.timestamp <= end)
    if worker_ids is not None:
        query = query.where(HarvestRecord.worker_id.in_(worker_ids))
    records = session.exec(query).all()

    workers = {w.id: w for w in session.exec(select(Worker)).all()}

    worker_days: dict = {}  # worker_id -> day -> crates harvested
    for r in records:
        if not r.worker_id:
            continue
        local_ts = to_local(r.timestamp)
        day = local_ts.date() if local_ts else date.min
        days_for_worker = worker_days.setdefault(r.worker_id, {})
        days_for_worker[day] = days_for_worker.get(day, 0) + 1

    days = sorted({d for wd in worker_days.values() for d in wd})
    headers = ["Emp Nr", "Name & Surname", "ID Number"]
    headers += [d.isoformat() for d in days]
    headers += ["Total Crates Harvested", "Bank", "Account"]

    rows = []
    for worker_id in sorted(worker_days.keys()):
        w = workers.get(worker_id)
        wd = worker_days[worker_id]
        row = [worker_id, w.name if w else "", w.id_number if w else ""]
        tot_harvested = 0
        for d in days:
            harvested = wd.get(d, 0)
            row.append(harvested)
            tot_harvested += harvested
        row += [tot_harvested, w.bank if w else "", w.account if w else ""]
        rows.append(row)

    return _xlsx_response(headers, rows, "Litchi Wages", f"Litchi_Wages_{period_start}_{period_end}.xlsx")


@router.get("/block-harvest")
def block_harvest_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                          session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    summary = dashboard_summary(period_start, period_end, supplier_id, session, admin)
    headers = ["Block", "Crates", "Kg", "Avg Kg/Crate", "Avg Kg/Tree", "Avg Kg/Ha"]
    rows = [[
        b["name"], b["crates"], b["total_kg"], b["avg_kg_crate"],
        b["avg_kg_tree"] if b["avg_kg_tree"] is not None else "",
        b["avg_kg_hectare"] if b["avg_kg_hectare"] is not None else "",
    ] for b in summary["blocks"]]
    return _xlsx_response(headers, rows, "Block Harvest", f"Block_Harvest_{period_start}_{period_end}.xlsx")
