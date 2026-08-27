"""Populate a running Boord server with demo data for local
testing only. Never imported by main.py/db.py and never run automatically -
run by hand against an empty dev database when you want something to click
through (e.g. python3 seed_demo.py).

Usage:
    python3 seed_demo.py [base_url]

Defaults to http://localhost:8811. Posts through the normal API (not the DB
directly), so it works against any running instance: workers/suppliers via
the admin endpoints, crates via /api/sync/harvest, dispatches via /api/lots,
external deliveries via /api/lots/external, check-ins via /api/receiving,
and pre-pack pulls via /api/processing/prepack.

Safe to re-run: workers/suppliers upsert by id/name, crates upsert by uuid.
"""
import json
import random
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8811"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "ChangeMe123!"

random.seed(42)  # deterministic demo data on re-runs


def api(path, body=None, method=None, token=None, form=False):
    url = f"{BASE}{path}"
    headers = {}
    data = None
    if body is not None:
        if form:
            data = body.encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers,
                                  method=method or ("POST" if body is not None else "GET"))
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read() or "null")


def login():
    result = api("/api/auth/login", f"username={ADMIN_USER}&password={ADMIN_PASSWORD}", form=True)
    return result["access_token"]


WORKERS = [
    ("001", "Jan", "Botha"), ("002", "Sipho", "Dlamini"), ("003", "Maria", "van Wyk"),
    ("004", "Thabo", "Nkosi"), ("005", "Anna", "Pretorius"), ("006", "Lindiwe", "Mahlangu"),
    ("007", "Pieter", "Steyn"), ("008", "Nomsa", "Zulu"),
]

BANKS = ["FNB", "Capitec", "Standard Bank", "ABSA", "Nedbank"]

BLOCK_DETAILS = {  # block id -> (variety, trees, hectares)
    "7": ("Mauritius", 420, 3.5), "8a": ("Mauritius", 380, 3.1), "8b": ("McLean's Red", 350, 2.9),
    "9": ("Mauritius", 460, 3.8), "10": ("McLean's Red", 300, 2.5),
}

FIELD_DEVICES = {  # device -> team
    "device-01": "A", "device-02": "A", "device-03": "A",
    "device-04": "B", "device-05": "B",
}

DRIVERS = ["Johan", "Themba", "Frikkie", "Sello"]
INDUNAS = {"A": "Samuel Mthembu", "B": "Petrus Mokoena"}


def main():
    token = login()
    print(f"Seeding demo data into {BASE}")

    # --- Farm GPS location (White River, Mpumalanga - litchi country) ---
    settings = api("/api/system-settings")
    settings["gps_lat"] = -25.45
    settings["gps_lon"] = 30.95
    api("/api/system-settings", settings, method="PUT", token=token)
    print("  farm location: GPS set")

    # --- Teams (induna names) ------------------------------------------
    for team_id, induna in INDUNAS.items():
        api("/api/teams", {"id": team_id, "name": f"Span {team_id}", "induna": induna, "active": True},
            token=token)
    print(f"  teams: {len(INDUNAS)} updated with indunas")

    # --- External suppliers (seeded before Workers so their ids are ------
    # --- available to assign as a worker's farm/supplier below) ----------
    suppliers = api("/api/suppliers")
    existing_names = {s["name"] for s in suppliers}
    for name, contact, phone, per_kg, per_crate in [
        ("Jansen Boerdery", "Piet Jansen", "082-555-1234", 1.50, 0),
        ("Mkhize Farms", "Bongani Mkhize", "083-555-9876", 0, 25.0),
    ]:
        if name not in existing_names:
            api("/api/suppliers", {
                "name": name, "contact_name": contact, "contact_phone": phone,
                "contact_email": "", "is_own_farm": False,
                "packing_rate_per_kg": per_kg, "packing_rate_per_crate": per_crate,
                "active": True,
            }, token=token)
    suppliers = api("/api/suppliers")
    jansen = next(s for s in suppliers if s["name"] == "Jansen Boerdery")

    # --- Workers ---------------------------------------------------------
    # emp 007/008 belong to an external supplier's crew; the rest are the
    # farm's own workers (supplier_id left unset).
    supplier_by_emp = {"007": jansen["id"], "008": jansen["id"]}
    for emp, first, last in WORKERS:
        api("/api/workers", {
            "id": emp, "first_name": first, "last_name": last,
            "id_number": f"850{random.randint(100, 999)}{random.randint(1000000, 9999999)}",
            "bank": random.choice(BANKS),
            "account": str(random.randint(10**9, 10**10 - 1)),
            "whatsapp_number": f"08{random.randint(2, 4)}{random.randint(1000000, 9999999)}",
            "supplier_id": supplier_by_emp.get(emp),
            "active": True,
        }, token=token)
    print(f"  workers: {len(WORKERS)} ({len(supplier_by_emp)} tagged to Jansen Boerdery)")

    # --- Block details ----------------------------------------------------
    for block_id, (variety, trees, hectares) in BLOCK_DETAILS.items():
        api("/api/blocks", {"id": block_id, "name": f"Block {block_id}", "variety": variety,
                             "trees": trees, "hectares": hectares, "active": True}, token=token)
    print(f"  blocks: {len(BLOCK_DETAILS)} updated with variety/trees/hectares")
    suppliers = api("/api/suppliers")
    external = [s for s in suppliers if not s["is_own_farm"]]
    print(f"  suppliers: {len(external)} external ({', '.join(s['name'] for s in external)})")

    # --- Own-farm harvest history: last 3 days -----------------------------
    now = datetime.now(timezone.utc)
    worker_ids = [w[0] for w in WORKERS]
    block_ids = list(BLOCK_DETAILS)
    total_crates = 0
    lots_dispatched = 0
    lots_received = 0
    received_lots = []

    for days_ago in range(3, 0, -1):
        day_start = (now - timedelta(days=days_ago)).replace(hour=6, minute=30, second=0, microsecond=0)
        for device_id, team_id in FIELD_DEVICES.items():
            slip = f"{device_id}-{day_start.strftime('%Y%m%d')}0000"
            crew = random.sample(worker_ids, random.randint(4, 6))
            block = random.choice(block_ids)
            records = []
            t = day_start + timedelta(minutes=random.randint(0, 40))
            for _ in range(random.randint(15, 25)):
                t += timedelta(minutes=random.randint(2, 9))
                records.append({
                    "uuid": f"demo-{slip}-{len(records)}",
                    "timestamp": t.isoformat(),
                    "worker_id": random.choice(crew),
                    "block_id": block,
                    "weight_kg": round(random.uniform(8.0, 16.5), 1),
                    "deduction_kg": round(random.choice([0, 0, 0, 0.3, 0.5]), 1),
                    "device_id": device_id,
                    "team_id": team_id,
                    "slip_number": slip,
                })
            api("/api/sync/harvest", {"records": records})
            total_crates += len(records)

            total_kg = round(sum(r["weight_kg"] - r["deduction_kg"] for r in records), 1)
            dispatch_time = t + timedelta(minutes=random.randint(5, 20))
            api("/api/lots", {
                "slip_number": slip, "timestamp": dispatch_time.isoformat(),
                "device_id": device_id, "team_id": team_id,
                "driver": random.choice(DRIVERS),
                "total_crates": len(records), "total_kg": total_kg,
                "status": "in_transit",
            })
            lots_dispatched += 1

            lot = next(l for l in api("/api/lots?status=in_transit") if l["slip_number"] == slip)
            api("/api/receiving", {
                "lot_id": lot["id"],
                "timestamp": (dispatch_time + timedelta(minutes=random.randint(40, 130))).isoformat(),
                "expected_crates": len(records), "actual_crates": len(records),
                "condition": "Good", "waste_kg": 0, "notes": "",
                "received_by": random.choice(["Elsa", "Johannes"]),
            })
            lots_received += 1
            received_lots.append(lot["id"])

    print(f"  own harvest history: {total_crates} crates, {lots_dispatched} lots dispatched, {lots_received} received")

    # --- Today: activity in every dashboard state -----------------------
    today_records = []
    picking_start = now - timedelta(hours=2)
    for device_id, mode in [("device-01", "received"), ("device-02", "in_transit"), ("device-03", "pending")]:
        team_id = FIELD_DEVICES[device_id]
        slip = f"{device_id}-{now.strftime('%Y%m%d')}TODAY"
        crew = random.sample(worker_ids, 4)
        block = random.choice(block_ids)
        records = []
        t = picking_start
        for _ in range(random.randint(10, 18)):
            t += timedelta(minutes=random.randint(2, 8))
            records.append({
                "uuid": f"demo-{slip}-{len(records)}",
                "timestamp": t.isoformat(),
                "worker_id": random.choice(crew),
                "block_id": block,
                "weight_kg": round(random.uniform(8.0, 16.5), 1),
                "deduction_kg": 0,
                "device_id": device_id,
                "team_id": team_id,
                "slip_number": slip,
            })
        api("/api/sync/harvest", {"records": records})
        today_records.extend(records)

        if mode in ("in_transit", "received"):
            total_kg = round(sum(r["weight_kg"] for r in records), 1)
            api("/api/lots", {
                "slip_number": slip, "timestamp": t.isoformat(),
                "device_id": device_id, "team_id": team_id,
                "driver": random.choice(DRIVERS),
                "total_crates": len(records), "total_kg": total_kg,
                "status": "in_transit",
            })
        if mode == "received":
            lot = next(l for l in api("/api/lots?status=in_transit") if l["slip_number"] == slip)
            api("/api/receiving", {
                "lot_id": lot["id"], "timestamp": now.isoformat(),
                "expected_crates": len(records), "actual_crates": len(records),
                "condition": "Good", "waste_kg": 0, "notes": "",
                "received_by": "Elsa",
            })
            received_lots.append(lot["id"])
    print(f"  today: {len(today_records)} crates across received/in-transit/pending lots")

    # --- External supplier delivery, received ------------------------------
    jansen = next(s for s in external if s["name"] == "Jansen Boerdery")
    lot = api("/api/lots/external", {
        "supplier_id": jansen["id"], "driver": "Piet",
        "total_crates": 24, "total_kg": 288.5,
        "notes": "Demo delivery",
    })
    api("/api/receiving", {
        "lot_id": lot["id"], "timestamp": now.isoformat(),
        "expected_crates": 24, "actual_crates": 24,
        "condition": "Good", "waste_kg": 0, "notes": "",
        "received_by": "Johannes",
    })
    received_lots.append(lot["id"])
    # one still waiting to be checked in
    api("/api/lots/external", {
        "supplier_id": jansen["id"], "driver": "Piet",
        "total_crates": 15, "total_kg": 176.0, "notes": "Demo delivery - awaiting check-in",
    })
    print("  external lots: 1 received + 1 in transit")

    # --- A couple of pre-pack pulls (XXL/XL crates set aside at receiving) --
    for lot_id, crates in [(received_lots[0], 4), (received_lots[1], 3)]:
        api("/api/processing/prepack", {
            "lot_id": lot_id, "crates": crates,
            "dominant_block_id": random.choice(block_ids),
            "operator": "Elsa",
            "notes": "XXL/XL candidate selection for local pre-pack order",
        })
    print("  pre-pack: 2 pulls recorded")

    print("Done.")


if __name__ == "__main__":
    main()
