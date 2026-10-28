import json
import os
import sqlite3
from collections import Counter
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

import market_mail
from db import DATA_DIR, get_own_supplier_id, get_session, supplier_map
from excel_io import XLSX_MEDIA, rows_to_xlsx_bytes
from models import Block, Device, HarvestRecord, Lot, MarketInvoice, MarketProduct, MarketStatement, \
    ReceivingRecord, Supplier, SystemSetting, Team, Worker
from routers.dashboard import dashboard_summary
from routers.lots import blocks_by_lot as _blocks_by_lot, list_in_transit, list_pending, list_received
from routers.payments import harvest_records_between
from security import require_admin_client
from timeutil import day_bounds, local_str, to_local

# Every endpoint here is admin-only.
router = APIRouter(prefix="/api/reports", tags=["reports"], dependencies=[Depends(require_admin_client)])

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


def _packhouse_code(session: Session) -> str:
    """The installation's registered Pack House Code (PHC). One install is one
    pack house, so it's the same value on every row - it's carried as a column
    rather than a title because these sheets get filtered, split and pasted
    into a grower's or an auditor's own workbook, where a header row would be
    left behind."""
    settings = session.exec(select(SystemSetting)).first()
    return settings.packhouse_code if settings else ""


def _traceability(supplier: Optional[Supplier]) -> list:
    """A supplier's PUC and GlobalG.A.P. number, for the two traceability
    columns that follow every Supplier column. Blank for an unknown supplier
    or one whose numbers haven't been captured in Master Data - an empty cell
    reads as "not registered with us", which is the truth."""
    return [supplier.puc if supplier else "", supplier.global_gap_number if supplier else ""]


def _mean(values: list, places: int):
    """Blank rather than 0 when nothing was measured - an empty cell reads as
    "not recorded", where 0 would read as a freezing morning."""
    if not values:
        return ""
    return round(sum(values) / len(values), places) if places else round(sum(values) / len(values))


def _lots_between(session: Session, period_start: date, period_end: date, supplier_id: Optional[int]) -> list[Lot]:
    start, end = day_bounds(period_start, period_end)
    query = select(Lot).where(Lot.timestamp >= start, Lot.timestamp <= end)
    if supplier_id is not None:
        query = query.where(Lot.supplier_id == supplier_id)
    return session.exec(query.order_by(Lot.timestamp)).all()


def _receiving_by_lot(session: Session, lot_ids: list) -> dict:
    receiving_by_lot = {}
    for rec in session.exec(select(ReceivingRecord).where(ReceivingRecord.lot_id.in_(lot_ids))).all():
        receiving_by_lot.setdefault(rec.lot_id, rec)
    return receiving_by_lot


@router.get("/daily-harvest")
def daily_harvest_report(day: date = Query(default_factory=date.today), supplier_id: Optional[int] = None,
                          session: Session = Depends(get_session)):
    records = harvest_records_between(session, day, day, supplier_id)
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
                         session: Session = Depends(get_session)):
    """Daaglikse Oesdata / Daily Harvest Data: the block x date pivot behind
    the paper "Daaglikse Oesdata" log - one column per block, one row per
    day, matching how the paper form and the season workbook both lay it
    out."""
    records = harvest_records_between(session, period_start, period_end, supplier_id)
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
def lot_receiving_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                          session: Session = Depends(get_session)):
    lots = _lots_between(session, period_start, period_end, supplier_id)
    receiving_by_lot = _receiving_by_lot(session, [l.id for l in lots])
    suppliers = supplier_map(session)
    phc = _packhouse_code(session)

    headers = ["Pack House Code", "Slip Number", "Supplier", "PUC", "GlobalG.A.P. Number",
               "Dispatched", "Team", "Driver", "Expected Crates", "Kg Sent",
               "Status", "Received At", "Actual Crates", "Discrepancy", "Condition", "Waste Kg",
               "Temp (°C)", "Humidity (%)", "Weather"]
    rows = []
    for lot in lots:
        rec = receiving_by_lot.get(lot.id)
        supplier = suppliers.get(lot.supplier_id)
        rows.append([
            phc, lot.slip_number, supplier.name if supplier else "",
            *_traceability(supplier),
            local_str(lot.timestamp), lot.team_id, lot.driver,
            lot.total_crates, round(lot.total_kg, 1), lot.status.value,
            local_str(lot.received_at),
            rec.actual_crates if rec else "", rec.discrepancy if rec else "",
            rec.condition if rec else "", round(rec.waste_kg, 1) if rec else "",
            lot.weather_temp if lot.weather_temp is not None else "",
            lot.weather_humidity if lot.weather_humidity is not None else "",
            lot.weather_condition or "",
        ])
    return _xlsx_response(headers, rows, "Lot & Receiving", f"Lot_Receiving_{period_start}_{period_end}.xlsx")


@router.get("/picking-notes")
def picking_notes_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                          session: Session = Depends(get_session)):
    lots = _lots_between(session, period_start, period_end, supplier_id)
    receiving_by_lot = _receiving_by_lot(session, [l.id for l in lots])
    suppliers = supplier_map(session)
    teams = {t.id: t for t in session.exec(select(Team)).all()}
    blocks_by_lot = _blocks_by_lot(session, [l.id for l in lots])
    phc = _packhouse_code(session)

    headers = ["Pack House Code", "Slip Number", "Date", "Time", "Block", "Team", "Crates Sent",
               "Crates Received", "Total Kg", "Driver", "Supplier", "PUC", "GlobalG.A.P. Number",
               "Condition", "Notes", "Received By",
               "Weather", "Temp (°C)", "Humidity (%)"]
    entries = []
    for lot in lots:
        rec = receiving_by_lot.get(lot.id)
        supplier = suppliers.get(lot.supplier_id)
        team = teams.get(lot.team_id)
        # A lot has no block field of its own - it's stamped per crate, so the
        # slip's block(s) are whatever its HarvestRecords were captured against.
        blocks = sorted(blocks_by_lot.get(lot.id, ()))
        local_ts = to_local(lot.timestamp)
        team_name = team.name if team else lot.team_id or ""
        entries.append((
            local_ts.date() if local_ts else date.min, team_name, local_ts,
            [
                phc, lot.slip_number, local_ts.strftime("%Y-%m-%d") if local_ts else "",
                local_ts.strftime("%H:%M") if local_ts else "", ", ".join(blocks),
                team_name, lot.total_crates,
                rec.actual_crates if rec else "", round(lot.total_kg, 1), lot.driver,
                supplier.name if supplier else "", *_traceability(supplier),
                rec.condition if rec else "", rec.notes if rec else "", rec.received_by if rec else "",
                lot.weather_condition or "",
                lot.weather_temp if lot.weather_temp is not None else "",
                lot.weather_humidity if lot.weather_humidity is not None else "",
            ],
        ))
    entries.sort(key=lambda e: (e[0], e[1], e[2]))
    rows = [e[3] for e in entries]
    return _xlsx_response(headers, rows, "Picking Notes", f"Picking_Notes_{period_start}_{period_end}.xlsx")


@router.get("/team-picking-list")
def team_picking_list_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                              session: Session = Depends(get_session)):
    """Span Pluklys / Team Picking List: one row per team per day, with the
    day's blocks (kg + deductions) and dispatched lots (crates, time, slip
    number) laid out as repeating column groups - matching the fields on the
    paper "Inligting van die Dag" slip an induna's team fills in by hand."""
    records = harvest_records_between(session, period_start, period_end, supplier_id)
    lots = _lots_between(session, period_start, period_end, supplier_id)

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

    return _xlsx_response(headers, rows, "Team Picking List", f"Team_Picking_List_{period_start}_{period_end}.xlsx")


LOT_LIST_HEADERS = ["Pack House Code", "Slip Number", "Supplier", "PUC", "GlobalG.A.P. Number",
                    "Team", "Driver", "Crates", "Kg", "Age (min)"]


def _lot_rows(lots_data: list, suppliers: dict, phc: str) -> list:
    return [[
        phc, l["slip_number"], l["supplier_name"], *_traceability(suppliers.get(l["supplier_id"])),
        l["team_id"] or "", l["driver"], l["total_crates"], l["total_kg"],
        l["age_minutes"],
    ] for l in lots_data]


@router.get("/harvesting-list")
def harvesting_list_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                            session: Session = Depends(get_session)):
    lots_data = list_pending(supplier_id=supplier_id, period_start=period_start, period_end=period_end,
                              session=session)
    suppliers = supplier_map(session)
    rows = _lot_rows(lots_data, suppliers, _packhouse_code(session))
    return _xlsx_response(LOT_LIST_HEADERS, rows, "Harvesting",
                           f"Harvesting_{period_start}_{period_end}.xlsx")


@router.get("/in-transit-list")
def in_transit_list_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                            session: Session = Depends(get_session)):
    lots_data = list_in_transit(supplier_id=supplier_id, period_start=period_start, period_end=period_end,
                                 session=session)
    suppliers = supplier_map(session)
    rows = _lot_rows(lots_data, suppliers, _packhouse_code(session))
    return _xlsx_response(LOT_LIST_HEADERS, rows, "In Transit",
                           f"In_Transit_{period_start}_{period_end}.xlsx")


@router.get("/received-list")
def received_list_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                          session: Session = Depends(get_session)):
    """Matches the packhouse's paper "Packhouse Receipt Lists" slip: date and
    time split out, plus the receiving block and rejected (waste) amount."""
    lots_data = list_received(period_start=period_start, period_end=period_end, supplier_id=supplier_id,
                               session=session)
    lot_ids = [l["id"] for l in lots_data]
    receiving_by_lot = _receiving_by_lot(session, lot_ids)
    blocks_by_lot = _blocks_by_lot(session, lot_ids)
    suppliers = supplier_map(session)
    phc = _packhouse_code(session)

    headers = ["Pack House Code", "Slip Number", "Date", "Time", "Block", "Supplier", "PUC",
               "GlobalG.A.P. Number", "Team", "Driver", "Crates", "Kg", "Rejected"]
    rows = []
    for l in lots_data:
        rec = receiving_by_lot.get(l["id"])
        local_ts = to_local(l["received_at"])
        rows.append([
            phc, l["slip_number"],
            local_ts.strftime("%Y-%m-%d") if local_ts else "",
            local_ts.strftime("%H:%M") if local_ts else "",
            ", ".join(sorted(blocks_by_lot.get(l["id"], []))),
            l["supplier_name"], *_traceability(suppliers.get(l["supplier_id"])),
            l["team_id"] or "", l["driver"], l["total_crates"], l["total_kg"],
            round(rec.waste_kg, 1) if rec else "",
        ])
    return _xlsx_response(headers, rows, "Received", f"Received_{period_start}_{period_end}.xlsx")


@router.get("/worker-harvest")
def worker_harvest_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                           session: Session = Depends(get_session)):
    summary = dashboard_summary(period_start, period_end, supplier_id, session)
    # dashboard_summary resolves each worker to a supplier NAME only, so the
    # traceability numbers are looked up the same way _supplier_display_name
    # resolves that name.
    workers_by_id = {w.id: w for w in session.exec(select(Worker)).all()}
    suppliers_by_id = supplier_map(session)
    own_supplier = suppliers_by_id.get(get_own_supplier_id(session))

    def _supplier_for(worker_id):
        w = workers_by_id.get(worker_id)
        if not w:
            return own_supplier
        return suppliers_by_id.get(w.supplier_id)

    headers = ["Emp Nr", "Name", "Supplier", "PUC", "GlobalG.A.P. Number", "Crates", "Kg",
               "Amount Due", "Avg Kg/Crate"]
    rows = [[
        w["worker_id"], w["name"], w["supplier_name"], *_traceability(_supplier_for(w["worker_id"])),
        w["crates"], w["total_kg"], w["amount_due"],
        w["avg_kg_crate"],
    ] for w in summary["workers"]]
    return _xlsx_response(headers, rows, "Worker Harvest", f"Worker_Harvest_{period_start}_{period_end}.xlsx")


@router.get("/litchi-wages")
def litchi_wages_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                         session: Session = Depends(get_session)):
    """Lietsjie Lone / Litchi Wages: one row per worker, with the crates that
    worker harvested broken out per day - one column per day worked, so the
    wage clerk can read a whole pay period off a single row."""
    records = harvest_records_between(session, period_start, period_end, supplier_id)

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
    headers = ["Emp Nr", "Name & Surname"]
    headers += [d.isoformat() for d in days]
    headers += ["Total Crates Harvested"]

    rows = []
    for worker_id in sorted(worker_days.keys()):
        w = workers.get(worker_id)
        wd = worker_days[worker_id]
        rows.append([worker_id, w.name if w else "", *(wd.get(d, 0) for d in days), sum(wd.values())])

    return _xlsx_response(headers, rows, "Litchi Wages", f"Litchi_Wages_{period_start}_{period_end}.xlsx")


# Boord Packhouse owns pallets and delivery notes in its own packhouse.db. The
# hub opens it read-only, the way the sibling apps open boord.db, rather than
# asking Packhouse's API: that only lists the last 90 days, and a season report
# can reach further back. Beside this repo on the Mac and on the farm PC
# (C:\Boord and C:\Boord-packhouse); the env var is for anywhere else.
PACKHOUSE_DB_PATH = os.path.abspath(os.environ.get("BOORD_PACKHOUSE_DB", os.path.join(
    DATA_DIR, "..", "..", "Boord-packhouse", "data", "packhouse.db")))


def _packhouse_rows(sql: str, params: tuple = ()) -> list:
    """Rows from packhouse.db as dicts. A 503 with words, not a 500, when Pack
    house isn't installed here or hasn't made the table yet - the office should
    read "Packhouse isn't set up", not "could not generate report"."""
    if not os.path.exists(PACKHOUSE_DB_PATH):
        raise HTTPException(503, "Boord Packhouse data was not found on this server")
    conn = sqlite3.connect(f"file:{PACKHOUSE_DB_PATH}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError as e:
        raise HTTPException(503, f"Boord Packhouse data could not be read: {e}")
    finally:
        conn.close()


def _pallet_stats(lines: list) -> tuple:
    cartons = sum(ln.get("cartons", 0) for ln in lines)
    return cartons, round(sum(ln.get("cartons", 0) * ln.get("carton_kg", 0) for ln in lines), 1)


def _line_text(ln: dict) -> str:
    block = f", {ln.get('block_name')}" if ln.get("block_id") else ""
    return f"{ln.get('cartons')} x {ln.get('packaging_name')}{block}"


@router.get("/pallets-stacked")
def pallets_stacked_report(period_start: date, period_end: date, supplier_id: Optional[int] = None):
    """One row per pallet stacked (scanned) in the range, from Packhouse's
    Pallet Stack, with the delivery note it went out on, if any."""
    start, end = day_bounds(period_start, period_end)
    sql = "SELECT * FROM pallet WHERE removed = 0 AND scanned_at >= ? AND scanned_at <= ?"
    params = [str(start), str(end)]
    if supplier_id is not None:
        sql += " AND supplier_id = ?"
        params.append(supplier_id)
    pallets = _packhouse_rows(sql + " ORDER BY scanned_at", tuple(params))

    note_of = {}
    for n in _packhouse_rows("SELECT note_no, pallets FROM deliverynote WHERE removed = 0"):
        for p in json.loads(n["pallets"] or "[]"):
            note_of[p.get("uuid")] = n["note_no"]

    headers = ["Date", "Time", "Pallet Ref", "Pallet Year-No", "Barcode", "Date Code", "Supplier", "Market",
               "Cartons", "Kg", "Contents", "Scanned By", "Delivery Note"]
    rows = []
    for p in pallets:
        lines = json.loads(p["lines"] or "[]")
        cartons, kg = _pallet_stats(lines)
        local_ts = to_local(datetime.fromisoformat(p["scanned_at"]))
        rows.append([
            local_ts.strftime("%Y-%m-%d") if local_ts else "", local_ts.strftime("%H:%M") if local_ts else "",
            p["pallet_ref"], f"{p['pallet_year']}-{p['pallet_no']:04d}" if p["pallet_no"] else "",
            p["barcode"], p["date_code"], p["supplier_name"], p["market_name"], cartons, kg,
            "; ".join(_line_text(ln) for ln in lines), p["scanned_by_name"], note_of.get(p["uuid"], ""),
        ])
    return _xlsx_response(headers, rows, "Pallets Stacked", f"Pallets_Stacked_{period_start}_{period_end}.xlsx")


@router.get("/delivery-notes")
def delivery_notes_report(period_start: date, period_end: date, supplier_id: Optional[int] = None):
    """One row per delivery note loaded in the range. The supplier filter is
    accepted but not applied: a note carries pallets of whatever suppliers
    were stacked, and keeps only their names."""
    notes = _packhouse_rows("SELECT * FROM deliverynote WHERE removed = 0 AND loaded_on >= ? AND loaded_on <= ? "
                            "ORDER BY loaded_on, note_no", (period_start.isoformat(), period_end.isoformat()))
    headers = ["Note No", "Loaded On", "Offloaded On", "Receiver", "Transporter", "Seal No", "Cooled To (°C)",
               "Target Receiver", "Pallets", "Cartons", "Kg", "Pallet Refs", "Made By"]
    rows = []
    for n in notes:
        pallets = json.loads(n["pallets"] or "[]")
        stats = [_pallet_stats(p.get("lines", [])) for p in pallets]
        rows.append([
            n["note_no"], n["loaded_on"], n["offloaded_on"], n["receiver"].replace("\n", ", "), n["transporter"],
            n["seal_no"], n["cooled_to"], n["target_receiver"].replace("\n", ", "), len(pallets),
            sum(s[0] for s in stats), round(sum(s[1] for s in stats), 1),
            ", ".join(p.get("pallet_ref", "") for p in pallets), n["made_by_name"],
        ])
    return _xlsx_response(headers, rows, "Delivery Notes", f"Delivery_Notes_{period_start}_{period_end}.xlsx")


@router.get("/block-harvest")
def block_harvest_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                          session: Session = Depends(get_session)):
    summary = dashboard_summary(period_start, period_end, supplier_id, session)
    headers = ["Block", "Crates", "Kg", "Avg Kg/Crate", "Avg Kg/Tree", "Avg Kg/Ha"]
    rows = [[
        b["name"], b["crates"], b["total_kg"], b["avg_kg_crate"],
        b["avg_kg_tree"] if b["avg_kg_tree"] is not None else "",
        b["avg_kg_hectare"] if b["avg_kg_hectare"] is not None else "",
    ] for b in summary["blocks"]]
    return _xlsx_response(headers, rows, "Block Harvest", f"Block_Harvest_{period_start}_{period_end}.xlsx")


# ---------------------------------------------------------------------------
# Markets & Transport: what each delivery note sent (Packhouse's), what the
# market paid for it and what carrying it cost (the office's MarketStatement).
# These replace the season "Markte" workbook the office kept by hand.
# ---------------------------------------------------------------------------

# South African VAT. Statements and transport costs are typed excluding it,
# the way the transport invoices list them; the Transport Summary adds it back.
VAT_RATE = 0.15


class MarketLineIn(BaseModel):
    pack: str
    sold: Optional[int] = Field(default=None, ge=0)
    price: Optional[float] = Field(default=None, ge=0)


class MarketStatementIn(BaseModel):
    lines: List[MarketLineIn] = []
    commission: float = Field(default=0, ge=0)
    deductions: float = Field(default=0, ge=0)
    bank_cost: float = Field(default=0, ge=0)
    waybill_no: str = ""
    invoice_no: str = ""
    transport_cost: float = Field(default=0, ge=0)
    notes: str = ""


def _ratio(num: float, den: float):
    """Blank rather than a division error when nothing was sold or carried."""
    return round(num / den, 2) if den else ""


def _market_notes(period_start: date, period_end: date, session: Session) -> list:
    """Every live delivery note loaded in the range, with what went out on it
    per pack and what the market paid. Packs come from the note's own copy of
    its pallets, so a pack retired in Packhouse since still shows. Not
    filtered by supplier, like the Delivery Notes report: a note carries
    whatever suppliers were stacked.

    The money comes from the market's emailed invoices (market_mail.py) once
    any have arrived for the note, all its part-payments added up, and
    otherwise from the statement the office typed. Transport is always typed."""
    notes = _packhouse_rows("SELECT * FROM deliverynote WHERE removed = 0 AND loaded_on >= ? AND loaded_on <= ? "
                            "ORDER BY loaded_on, note_no", (period_start.isoformat(), period_end.isoformat()))
    uuids = [n["uuid"] for n in notes]
    statements = {s.note_uuid: s for s in session.exec(
        select(MarketStatement).where(MarketStatement.note_uuid.in_(uuids))).all()} if uuids else {}
    invoices = {}
    note_nos = [str(n["note_no"]) for n in notes]
    for inv in session.exec(select(MarketInvoice).where(MarketInvoice.note_no.in_(note_nos))
                            .order_by(MarketInvoice.invoice_date, MarketInvoice.invoice_no)).all() if notes else []:
        invoices.setdefault(inv.note_no, []).append(inv)
    pack_of = {m.product: m.pack for m in session.exec(select(MarketProduct)).all()}
    out = []
    for n in notes:
        pallets = json.loads(n["pallets"] or "[]")
        st = statements.get(n["uuid"])
        invs = invoices.get(str(n["note_no"]), [])
        packs = {}
        for p in pallets:
            for ln in p.get("lines", []):
                name = ln.get("packaging_name", "")
                pk = packs.setdefault(name, {"pack": name, "carton_kg": ln.get("carton_kg", 0), "sent": 0})
                pk["sent"] += ln.get("cartons", 0)
        if invs:
            for pk in packs.values():
                pk["sold"], pk["gross"] = 0, 0
            for inv in invs:
                for ln in json.loads(inv.lines):
                    # A product not yet matched to a pack still counts, as a
                    # line of its own with nothing sent and no kg.
                    name = pack_of.get(ln["product"], ln["product"])
                    pk = packs.setdefault(name, {"pack": name, "carton_kg": 0, "sent": 0, "sold": 0, "gross": 0,
                                                 "unmapped": ln["product"] not in pack_of})
                    pk["sold"] += ln["sold"]
                    pk["gross"] += ln["income"]
            for pk in packs.values():
                pk["gross"] = round(pk["gross"], 2)
                pk["price"] = round(pk["gross"] / pk["sold"], 2) if pk["sold"] else None
            money = {k: round(sum(getattr(inv, k) for inv in invs), 2)
                     for k in ("commission", "deductions", "bank_cost")}
        else:
            typed = {ln["pack"]: ln for ln in json.loads(st.lines)} if st else {}
            for pk in packs.values():
                pk["sold"] = typed.get(pk["pack"], {}).get("sold")
                pk["price"] = typed.get(pk["pack"], {}).get("price")
                pk["gross"] = round((pk["sold"] or 0) * (pk["price"] or 0), 2)
            money = {k: getattr(st, k) if st else 0 for k in ("commission", "deductions", "bank_cost")}
        packs = sorted(packs.values(), key=lambda pk: pk["pack"])
        gross = round(sum(pk["gross"] for pk in packs), 2)
        out.append(dict({
            "uuid": n["uuid"], "note_no": n["note_no"], "loaded_on": n["loaded_on"],
            "receiver": (n["receiver"] or "").split("\n")[0],
            "market": ", ".join(sorted({p.get("market_name", "") for p in pallets} - {""})),
            "transporter": n["transporter"], "pallets": len(pallets), "packs": packs,
            "sent": sum(pk["sent"] for pk in packs), "sold": sum(pk["sold"] or 0 for pk in packs),
            "kg_sent": round(sum(pk["sent"] * pk["carton_kg"] for pk in packs), 1),
            "kg_sold": round(sum((pk["sold"] or 0) * pk["carton_kg"] for pk in packs), 1),
            "entered": st is not None or bool(invs), "gross": gross,
            "net": round(gross - sum(money.values()), 2),
            "invoices": [{"invoice_no": i.invoice_no, "invoice_date": str(i.invoice_date), "payment": i.payment,
                          "agent": i.agent, "net": i.net} for i in invs],
            "waybill_no": st.waybill_no if st else "", "invoice_no": st.invoice_no if st else "",
            "transport_cost": st.transport_cost if st else 0, "notes": st.notes if st else "",
        }, **money))
    return out


@router.get("/market-notes")
def list_market_notes(period_start: date, period_end: date, session: Session = Depends(get_session)):
    """The Markets & Transport list on the Reports tab."""
    return _market_notes(period_start, period_end, session)


@router.put("/market-notes/{note_uuid}")
def save_market_statement(note_uuid: str, body: MarketStatementIn, session: Session = Depends(get_session)):
    if not _packhouse_rows("SELECT uuid FROM deliverynote WHERE uuid = ? AND removed = 0", (note_uuid,)):
        raise HTTPException(404, "That delivery note is not in Boord Packhouse any more")
    session.merge(MarketStatement(
        note_uuid=note_uuid, lines=json.dumps([ln.model_dump() for ln in body.lines]),
        commission=body.commission, deductions=body.deductions, bank_cost=body.bank_cost,
        waybill_no=body.waybill_no.strip(), invoice_no=body.invoice_no.strip(),
        transport_cost=body.transport_cost, notes=body.notes.strip(), updated_at=datetime.utcnow(),
    ))
    session.commit()
    return {"ok": True}


class MarketProductIn(BaseModel):
    product: str
    pack: str = ""


@router.get("/market-mail")
def market_mail_status(session: Session = Depends(get_session)):
    """The market email box on the Reports tab: whether it is set up (the
    address only - the app password never leaves the PC), the last pass and
    its log, and every market product description with the pack it is
    matched to, plus Packhouse's packs to choose from."""
    cfg = market_mail.read_config()
    status = market_mail.read_status()
    products = sorted({ln["product"] for inv in session.exec(select(MarketInvoice)).all()
                       for ln in json.loads(inv.lines)})
    pack_of = {m.product: m.pack for m in session.exec(select(MarketProduct)).all()}
    try:
        packs = [r["name"] for r in _packhouse_rows("SELECT name FROM packaging WHERE active = 1 ORDER BY sort, name")]
    except HTTPException:
        packs = []
    return {
        "address": cfg["address"] if cfg else "", "senders": cfg["senders"] if cfg else [],
        "every_minutes": market_mail.FETCH_MINUTES, "last_run": status.get("last_run"),
        "last_error": status.get("last_error", "") if cfg else "", "log": status.get("log", [])[-15:][::-1],
        "products": [{"product": p, "pack": pack_of.get(p, "")} for p in products], "packs": packs,
    }


@router.post("/market-mail/fetch")
def market_mail_fetch_now():
    """"Fetch now": one pass straight away instead of waiting for the next."""
    if not market_mail.read_config():
        raise HTTPException(400, "Market email isn't set up on this server: see the manual, data\\market_mail.txt")
    market_mail.fetch_once()
    return {"ok": True}


@router.put("/market-products")
def save_market_product(body: MarketProductIn, session: Session = Depends(get_session)):
    """Match a market's product description to a Packhouse pack; a blank pack
    unmatches it."""
    existing = session.get(MarketProduct, body.product)
    if body.pack.strip():
        session.merge(MarketProduct(product=body.product, pack=body.pack.strip()))
    elif existing:
        session.delete(existing)
    session.commit()
    return {"ok": True}


@router.delete("/market-invoices")
def delete_market_invoice(invoice_no: str, session: Session = Depends(get_session)):
    """Takes an imported invoice back out (a wrong or duplicate one). Its email
    stays labelled in Gmail, so the next pass won't bring it back; remove the
    label there to have it read again. In the query string, not the path:
    invoice numbers have a slash in them."""
    inv = session.get(MarketInvoice, invoice_no)
    if not inv:
        raise HTTPException(404, "No such invoice")
    session.delete(inv)
    session.commit()
    return {"ok": True}


_MONEY_HEADERS = ["Gross", "Commission", "Deductions", "Bank Cost", "Net Paid", "Net R/Carton", "Net R/Kg"]


def _money(r: dict) -> list:
    return [round(r["gross"], 2), round(r["commission"], 2), round(r["deductions"], 2), round(r["bank_cost"], 2),
            round(r["net"], 2), _ratio(r["net"], r["sold"]), _ratio(r["net"], r["kg_sold"])]


@router.get("/market-summary")
def market_summary_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                          session: Session = Depends(get_session)):
    """One row per receiver and market (Boeretrust / Local, AgriLink / USA Sea
    Freight...), the "2025 Totaal" sheet's totals, with a Total row."""
    keys = ["pallets", "sent", "sold", "kg_sent", "kg_sold", "gross", "commission", "deductions", "bank_cost",
            "net", "transport_cost"]
    groups = {}
    for n in _market_notes(period_start, period_end, session):
        g = groups.setdefault((n["receiver"], n["market"]), dict({k: 0 for k in keys}, notes=0, entered=0))
        g["notes"] += 1
        g["entered"] += n["entered"]
        for k in keys:
            g[k] += n[k]
    total = dict({k: sum(g[k] for g in groups.values()) for k in keys},
                 notes=sum(g["notes"] for g in groups.values()), entered=sum(g["entered"] for g in groups.values()))

    def row(receiver, market, g):
        return [receiver, market, g["notes"], f"{g['entered']} of {g['notes']}", g["pallets"], g["sent"], g["sold"],
                g["sent"] - g["sold"], round(g["kg_sent"], 1), round(g["kg_sold"], 1)] + _money(g) + [
                round(g["transport_cost"], 2), round(g["net"] - g["transport_cost"], 2)]

    headers = ["Receiver", "Market", "Notes", "Statements Entered", "Pallets", "Cartons Sent", "Cartons Sold",
               "Outstanding", "Kg Sent", "Kg Sold"] + _MONEY_HEADERS + ["Transport (excl VAT)", "Net After Transport"]
    rows = [row(rc, mk, g) for (rc, mk), g in sorted(groups.items())]
    if rows:
        rows.append(row("Total", "", total))
    return _xlsx_response(headers, rows, "Market Summary", f"Market_Summary_{period_start}_{period_end}.xlsx")


@router.get("/market-packs")
def market_packs_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                        session: Session = Depends(get_session)):
    """One row per receiver, market and pack: sent, sold, outstanding and the
    average price, the per-pack columns of the old workbook turned on their side."""
    groups = {}
    for n in _market_notes(period_start, period_end, session):
        for pk in n["packs"]:
            g = groups.setdefault((n["receiver"], n["market"], pk["pack"]), {"sent": 0, "sold": 0, "gross": 0, "kg": 0})
            g["sent"] += pk["sent"]
            g["sold"] += pk["sold"] or 0
            g["gross"] += pk["gross"]
            g["kg"] += (pk["sold"] or 0) * pk["carton_kg"]
    headers = ["Receiver", "Market", "Pack", "Cartons Sent", "Cartons Sold", "Outstanding", "Avg Price/Carton",
               "Gross", "Gross R/Kg"]
    rows = [[rc, mk, pack, g["sent"], g["sold"], g["sent"] - g["sold"], _ratio(g["gross"], g["sold"]),
             round(g["gross"], 2), _ratio(g["gross"], g["kg"])] for (rc, mk, pack), g in sorted(groups.items())]
    return _xlsx_response(headers, rows, "Market Sales by Package Type", f"Market_Sales_by_Package_Type_{period_start}_{period_end}.xlsx")


@router.get("/market-sales")
def market_sales_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                        session: Session = Depends(get_session)):
    """One row per delivery note: what went, what sold, what the market paid."""
    headers = ["Loaded On", "Note No", "Receiver", "Market", "Transporter", "Pallets", "Cartons Sent",
               "Cartons Sold", "Outstanding", "Kg Sold"] + _MONEY_HEADERS + ["Packs", "Statement", "Notes"]
    rows = [[
        n["loaded_on"], n["note_no"], n["receiver"], n["market"], n["transporter"], n["pallets"], n["sent"],
        n["sold"], n["sent"] - n["sold"], n["kg_sold"],
    ] + _money(n) + [
        "; ".join(f"{pk['pack']}: {pk['sent']} sent, {pk['sold'] if pk['sold'] is not None else '-'} sold"
                  + (f" @ R{pk['price']:.2f}" if pk["price"] is not None else "") for pk in n["packs"]),
        "Entered" if n["entered"] else "Not entered", n["notes"],
    ] for n in _market_notes(period_start, period_end, session)]
    return _xlsx_response(headers, rows, "Consignment Sales", f"Consignment_Sales_{period_start}_{period_end}.xlsx")


@router.get("/transport-summary")
def transport_summary_report(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                             session: Session = Depends(get_session)):
    """The "Vervoer opsomming" sheet: one row per delivery note with its
    transporter's waybill, invoice and cost, and a Total row."""
    headers = ["Loaded On", "Note No", "Receiver", "Market", "Transporter", "Waybill No", "Invoice No", "Pallets",
               "R/Pallet (excl VAT)", "Total (excl VAT)", "VAT", "Total (incl VAT)"]
    notes = _market_notes(period_start, period_end, session)
    rows = [[
        n["loaded_on"], n["note_no"], n["receiver"], n["market"], n["transporter"], n["waybill_no"], n["invoice_no"],
        n["pallets"], _ratio(n["transport_cost"], n["pallets"]), round(n["transport_cost"], 2),
        round(n["transport_cost"] * VAT_RATE, 2), round(n["transport_cost"] * (1 + VAT_RATE), 2),
    ] for n in notes]
    if rows:
        # The average per pallet is over the notes whose invoice is in, so one
        # not typed yet doesn't drag it down.
        pallets, cost = sum(n["pallets"] for n in notes), sum(n["transport_cost"] for n in notes)
        costed = sum(n["pallets"] for n in notes if n["transport_cost"])
        rows.append(["Total", "", "", "", "", "", "", pallets, _ratio(cost, costed), round(cost, 2),
                     round(cost * VAT_RATE, 2), round(cost * (1 + VAT_RATE), 2)])
    return _xlsx_response(headers, rows, "Transport Summary", f"Transport_Summary_{period_start}_{period_end}.xlsx")
