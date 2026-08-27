"""Populate a running Boord server with demo data for local
testing only. Never imported by main.py/db.py and never run automatically -
run by hand against an empty dev database when you want something to click
through.

Usage:
    ALLOW_DEMO_SEED=1 python3 seed_demo.py [base_url]

Defaults to http://localhost:8811. Posts through the normal API (not the DB
directly), so it works against any running instance: workers/suppliers via
the admin endpoints, crates via /api/sync/harvest, dispatches via /api/lots,
external deliveries via /api/lots/external, check-ins via /api/receiving,
and pre-pack pulls via /api/processing/prepack.

Safe to re-run: workers/suppliers upsert by id/name, crates upsert by uuid.

DANGEROUS against a real farm, which is why it now asks twice. Nothing used
to stop this being pointed at a live database, where it overwrites the farm's
GPS location, its two teams and five of its blocks, and files eight invented
people - complete with fabricated SA ID numbers and bank account numbers -
into the same worker list that the payroll run reads. Hence:

  * ALLOW_DEMO_SEED=1 must be set, so it can never be an accidental
    up-arrow-and-enter against the wrong window; and
  * refuse_unless_safe() checks, before writing a single row, that everything
    this script would overwrite is either absent or something it put there
    itself.

The admin password comes from BOORD_ADMIN_PASSWORD, or - when seeding a
fresh install on this machine - from data/initial_admin_password.txt.
"""
import json
import os
import random
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8811"
ADMIN_USER = os.environ.get("BOORD_ADMIN_USER", "admin")

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


def admin_password():
    """BOORD_ADMIN_PASSWORD, or the one the install generated for itself.

    There is no shared default password any more - db.seed_defaults() makes a
    random one per install and leaves it in data/initial_admin_password.txt
    until it is replaced, so a freshly created local database needs nothing
    passed in."""
    from_env = os.environ.get("BOORD_ADMIN_PASSWORD")
    if from_env:
        return from_env
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data",
                         "initial_admin_password.txt")
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        sys.exit("No admin password to sign in with. Either run this on the same machine\n"
                 "as a fresh install (where data/initial_admin_password.txt still holds the\n"
                 "generated one), or set BOORD_ADMIN_PASSWORD=<password>.")


def login():
    # urlencode, not an f-string: a generated password is not guaranteed to
    # survive being pasted into a form body unescaped.
    body = urllib.parse.urlencode({"username": ADMIN_USER, "password": admin_password()})
    result = api("/api/auth/login", body, form=True)
    if result.get("must_change_password"):
        sys.exit("This server is still on the admin password it generated at install, and\n"
                 "refuses every other endpoint until that is replaced. Open the Admin app,\n"
                 "set a password, then re-run with BOORD_ADMIN_PASSWORD=<that password>.")
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


DEMO_WORKER_IDS = {emp for emp, _first, _last in WORKERS}
DEMO_BLOCK_IDS = set(BLOCK_DETAILS)
DEMO_GPS = (-25.45, 30.95)  # White River - what this script sets below


def refuse_unless_safe(token):
    """Stop before writing anything if this database holds a real farm's data.

    Each check is scoped to what this script would actually overwrite, and
    ignores what it put there itself, so re-running against a demo database
    stays fine. It is deliberately more suspicious than "are there crates?":
    a farm that has imported its workers and blocks but not yet picked
    anything is the case where fabricated ID numbers would land quietly in
    among real ones."""
    reasons = []

    counts = api("/api/harvest-records/counts", token=token)
    foreign_crates = counts["total"] - counts["demo"]
    if foreign_crates:
        reasons.append(f"{foreign_crates} harvest record(s) this script did not create")

    other_workers = sorted({w["id"] for w in api("/api/workers", token=token)} - DEMO_WORKER_IDS)
    if other_workers:
        reasons.append(f"{len(other_workers)} worker(s) that are not the demo eight "
                       f"({', '.join(other_workers[:3])}...)")

    other_blocks = sorted({b["id"] for b in api("/api/blocks")} - DEMO_BLOCK_IDS)
    if other_blocks:
        reasons.append(f"{len(other_blocks)} block(s) that are not the demo five "
                       f"({', '.join(other_blocks[:3])}...)")

    settings = api("/api/system-settings") or {}
    if settings.get("farm_name"):
        reasons.append(f"a farm name: {settings['farm_name']!r}")
    gps = (settings.get("gps_lat"), settings.get("gps_lon"))
    if gps != (None, None) and gps != DEMO_GPS:
        reasons.append(f"a GPS location this script did not set: {gps[0]}, {gps[1]}")

    if not reasons:
        return
    print(f"Refusing to seed {BASE} - this looks like a real farm's database.", file=sys.stderr)
    for reason in reasons:
        print(f"  - it holds {reason}", file=sys.stderr)
    print("\nTo get a demo database instead: stop the server, move data/boord.db aside,\n"
          "start it again (a new one is created empty), and re-run this script.",
          file=sys.stderr)
    sys.exit(1)


def main():
    if os.environ.get("ALLOW_DEMO_SEED") != "1":
        sys.exit("Refusing to run without ALLOW_DEMO_SEED=1.\n\n"
                 "This writes invented people, ID numbers and bank details into the target\n"
                 "database and overwrites its farm location, teams and blocks. Confirm the\n"
                 f"target is a throwaway database ({BASE}) with:\n\n"
                 "    ALLOW_DEMO_SEED=1 python3 seed_demo.py [base_url]")

    token = login()
    refuse_unless_safe(token)
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
