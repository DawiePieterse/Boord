"""The Admin Planner's arithmetic, kept apart from the database and the
sibling apps' files so scripts/selftest.py can check it with plain numbers.

The planner answers one question a week: of what the owner expected each
block to give this season, how much is left, how much of that do we pick
this week, on which days, and will it fill the cartons the receivers are
waiting for. Everything here is the arithmetic behind that; routers/planner.py
fetches the figures (Boord's crates, Owner's estimate, Packhouse's delivery
notes) and hands them in.

Every figure comes out a whole number (kg, cartons, pallets, %): the office
plans in whole kg and a decimal on a planning sheet only looks like
precision. Weeks are ISO weeks, Monday to Sunday, named by the Monday. A season is
Boord's: it starts on the anchor day in Settings (season_start_month/day) and
is labelled by the year it starts in, so the weeks after New Year still
belong to the 2026 season. "YTD" is everything picked from the season's start
up to the Sunday before the chosen week: when the week being planned is the
current one, counting its own Monday and Tuesday in YTD would shrink
"remaining" and make the default for "available this week" double-count.
"""
from datetime import date, timedelta
from typing import Dict, List, Optional

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def season_year_for(d: date, month: int, day: int) -> int:
    """The season a day falls in, labelled by the year it started: the same
    rule as Boord.seasonYearFor in frontend/shared/api.js."""
    anchor = _anchor(d.year, month, day)
    return d.year if d >= anchor else d.year - 1


def _anchor(year: int, month: int, day: int) -> date:
    # 29 February in a non-leap year: the anchor slides to 1 March rather
    # than raising, so a farm that picked that date is never locked out.
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, 3, 1)


def season_bounds(season_year: int, month: int, day: int) -> tuple:
    """(first day, last day) of a season, inclusive."""
    start = _anchor(season_year, month, day)
    end = _anchor(season_year + 1, month, day) - timedelta(days=1)
    return start, end


def season_weeks(season_year: int, month: int, day: int) -> List[date]:
    """Every week (its Monday) that touches the season, first to last."""
    start, end = season_bounds(season_year, month, day)
    weeks, monday = [], monday_of(start)
    while monday <= end:
        weeks.append(monday)
        monday += timedelta(days=7)
    return weeks


def block_sort_key(block_id: str) -> tuple:
    """Natural block order (7, 8a, 8b, 9, 10a ...) rather than alphabetical,
    where "10a" would come before "7". Boord Owner sorts its estimate the
    same way, so the two screens list the blocks in one order."""
    block_id = block_id or ""
    num = "".join(ch for ch in block_id if ch.isdigit())
    suffix = block_id[len(num):] if num else block_id
    return (int(num) if num else 0, suffix.lower())


def spread(available_kg: float, days: List[int], fixed: Dict[int, float]) -> Dict[int, float]:
    """Kg to pick on each ticked weekday (0 = Monday).

    A day the office typed a figure for (`fixed`) keeps it; the rest share
    what is left of `available_kg` equally, so editing one day rebalances
    the untouched ones and the block's total stays at what is available.
    When every ticked day is fixed the total is simply their sum, and the
    screen shows it against the available figure. Nothing goes negative: if
    the fixed days already exceed what is available, the others get 0.
    """
    ticked = sorted(set(d for d in days if 0 <= d <= 6))
    out: Dict[int, float] = {}
    fixed_total = 0.0
    free = []
    for d in ticked:
        if d in fixed and fixed[d] is not None:
            out[d] = round(float(fixed[d]))
            fixed_total += out[d]
        else:
            free.append(d)
    if free:
        share = max(0.0, (float(available_kg or 0) - fixed_total)) / len(free)
        for d in free:
            out[d] = round(share)
    return out


def pct(part: Optional[float], whole: Optional[float]) -> Optional[float]:
    """part as a percentage of whole, a whole number, or None when there is no
    whole to be a percentage of (no estimate yet) - never a division error
    and never a misleading 0."""
    if whole is None or not whole:
        return None
    return round(100.0 * (part or 0) / whole)


def loss_pct(harvested_kg: float, delivered_kg: float) -> Optional[float]:
    """The share of picked kg that did not go out as fruit on a delivery
    note: juice, rejects, give-away, shrink. None until something has been
    picked. Can come out negative early in a season (a note loaded for kg
    picked the week before), which the screen shows rather than hides."""
    if not harvested_kg:
        return None
    return round(100.0 * (harvested_kg - delivered_kg) / harvested_kg)


def derive(view: dict) -> dict:
    """Every figure the screen, the spreadsheet and the PDF agree on, from
    the week's view (routers/planner.py build_week). Returned alongside it
    so the three never do their own arithmetic differently:

    - per block: remaining, available (the saved figure or remaining), kg
      per ticked day, the block's planned total;
    - per receiver and pack: planned cartons, Packhouse units (punnets for
      Pre-pac), kg and whole pallets (rounded up: a part-filled pallet still
      goes on the truck);
    - the week: planned harvest kg, the loss % in force, deliverable kg
      after it, planned delivery kg, and short (negative) or surplus.
    """
    blocks_out = []
    harvest_total = 0.0
    day_totals = [0.0] * 7
    for b in view["blocks"]:
        est = b.get("estimate_kg")
        ytd = b.get("ytd_kg") or 0.0
        remaining = round(est - ytd) if est is not None else None
        available = b.get("available_kg")
        if available is None:
            available = max(0.0, remaining) if remaining is not None else 0.0
        fixed = {int(k): v for k, v in (b.get("fixed") or {}).items()}
        per_day = spread(available, b.get("days") or [], fixed) if b.get("on") else {}
        total = round(sum(per_day.values()))
        if b.get("on"):
            harvest_total += total
            for d, kg in per_day.items():
                day_totals[d] += kg
        blocks_out.append({
            "block_id": b["block_id"],
            "remaining_kg": remaining,
            "remaining_pct": pct(remaining, est),
            "ytd_pct": pct(ytd, est),
            "last_week_pct": pct(b.get("last_week_kg"), est),
            "available_kg": round(available),
            "available_pct": pct(available, remaining) if remaining else None,
            "per_day": {str(d): kg for d, kg in per_day.items()},
            "total_kg": total,
        })

    packs = {p["name"]: p for p in view.get("packs", [])}
    deliveries = view.get("plan_deliveries") or {}
    receivers_out = []
    pack_totals: Dict[str, dict] = {name: {"cartons": 0, "kg": 0.0, "pallets": 0} for name in packs}
    delivery_kg = 0.0
    delivery_cartons = 0
    delivery_pallets = 0
    for r in view.get("receivers", []):
        rid = str(r["id"])
        cells = {}
        r_cartons, r_kg, r_pallets = 0, 0.0, 0
        for name, p in packs.items():
            cartons = int((deliveries.get(rid) or {}).get(name) or 0)
            # Packhouse's carton_kg is the weight of the unit its pallet lines
            # count (a Pre-pac punnet, 0.48 kg); a carton holds units_per_carton
            # of them (4), so the kg is units x unit weight.
            units = cartons * (p.get("units_per_carton") or 1)
            kg = round(units * (p.get("carton_kg") or 0))
            per_pallet = p.get("cartons_per_pallet") or 0
            # Whole pallets, rounded up: a receiver's cartons go on their own
            # pallets to their own destination, so 20 cartons of a 1500-carton
            # pack is still one pallet on the truck, not 0.01 of one.
            pallets = -(-cartons // per_pallet) if per_pallet and cartons else (0 if per_pallet else None)
            cells[name] = {"cartons": cartons, "units": units, "kg": kg, "pallets": pallets}
            r_cartons += cartons
            r_kg += kg
            r_pallets += pallets or 0
            pack_totals[name]["cartons"] += cartons
            pack_totals[name]["kg"] += kg
            pack_totals[name]["pallets"] += pallets or 0
        receivers_out.append({"id": r["id"], "cells": cells, "cartons": r_cartons,
                              "kg": round(r_kg), "pallets": r_pallets})
        delivery_cartons += r_cartons
        delivery_kg += r_kg
        delivery_pallets += r_pallets
    for t in pack_totals.values():
        t["kg"] = round(t["kg"])

    loss = view.get("loss") or {}
    effective_loss = loss.get("override_pct")
    if effective_loss is None:
        effective_loss = loss.get("season_pct")
    if effective_loss is None:
        effective_loss = 0.0
    harvest_total = round(harvest_total)
    deliverable = round(harvest_total * (1 - effective_loss / 100.0))
    delivery_kg = round(delivery_kg)
    return {
        "blocks": blocks_out,
        "day_totals": [round(x) for x in day_totals],
        "harvest_kg": harvest_total,
        "receivers": receivers_out,
        "pack_totals": pack_totals,
        "delivery_cartons": delivery_cartons,
        "delivery_kg": delivery_kg,
        "delivery_pallets": delivery_pallets,
        "loss_pct": round(effective_loss),
        "deliverable_kg": deliverable,
        # Positive: more deliverable fruit than the receivers are down for.
        # Negative: the plan is short of fruit.
        "balance_kg": round(deliverable - delivery_kg),
    }


def units_to_cartons(units: float, units_per_carton: int) -> float:
    """Packhouse's count of a pack (punnets, for Pre-pac) as cartons, for
    showing what has been delivered beside what is planned. Whole cartons,
    like every figure on the screen."""
    per = units_per_carton or 1
    return round((units or 0) / per) if per != 1 else (units or 0)
