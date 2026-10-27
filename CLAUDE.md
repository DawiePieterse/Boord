# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Boord is the farm's harvest app. Field tablets log litchi crates per picker, the pack house checks loads in, and the admin office pays pickers per kg and runs reports. It runs on the farm PC on port **8000**, published by `tailscale serve --https=443`. `MANUAL.md` is the user and admin manual; read it for behaviour, setup and anything user-facing. `TRAINING_*.md` are the training guides, one per role, each with its PDF.

Boord is the hub the sibling apps lean on:
- **Boord Owner** (8010), **Boord Labour** (8040), **Boord Monitor** (8050) and **Boord Packhouse** (8060) open `data/boord.db` read-only.
- **Boord Labour** vendors `frontend/shared/`, and its badge QR code holds exactly `worker.id`.

So a schema change here can break a sibling. Labour, Owner and Packhouse refuse to boot if a column they read disappears.

## The Boord suite

This app is one of seven that run together on the farm PC (`bekfontein-server`), each its own repo, process, port and database. They are reached only over Tailscale, and each binds `127.0.0.1` and is published with `tailscale serve`. The same section is in every app's `CLAUDE.md`. When a fact here changes, change it in all seven.

| App | Repo (`DawiePieterse/…`) | What it does | Port | Tailnet address | Mac folder | Farm PC folder |
|---|---|---|---|---|---|---|
| **Boord** (this app) | `Boord` | Harvest capture and picker pay per kg: field tablets, pack-house receiving, admin office. The hub. | 8000 | `https://bekfontein-server.taile32cb0.ts.net/` | `~/Documents/Projects/Boord` | `C:\Boord` |
| Boord Owner | `boordowner` | The owner's view: season vs history, weather, risk, crop estimate, optional AI. | 8010 | `https://bekfontein-server.taile32cb0.ts.net:8443/` | `~/Documents/Projects/BoordOwner` | `C:\BoordOwner` |
| Boord Notes | `boord-notes` | Private farm notebook for Andre and his son, offline-first, optional AI. | 8020 | `https://bekfontein-server.taile32cb0.ts.net:9443/` | `~/Documents/Projects/BoordNotes` | `C:\bekfontein-farm-notebook` |
| Kudde | `Kudde` | Cattle records: animals and their events. | 8030 | `https://bekfontein-server.taile32cb0.ts.net:8030/` | `~/Documents/Projects/Kudde` | `C:\Kudde` |
| Boord Labour | `Boord-labour` | Packhouse time clock: badge scans at the door, hourly pay with overtime. | 8040 | `https://bekfontein-server.taile32cb0.ts.net:8040/` | `~/Documents/Projects/Boord-labour` | `C:\Boord-labour` |
| Boord Monitor | `Boord-monitor` | Watches the PC and the other apps: status page, SLOs, incidents, Web Push alerts, a Restart button; runs the watchdog and heartbeat. | 8050 | `https://bekfontein-server.taile32cb0.ts.net:8050/` | `~/Documents/Projects/Boord-monitor` | `C:\boord-monitor` |
| Boord Packhouse | `boord-packhouse` | After receiving to the truck: sulfur, acid, lines, pre-pack, cold store and PPECB, dispatch. | 8060 | `https://bekfontein-server.taile32cb0.ts.net:8060/` | `~/Documents/Projects/Boord-packhouse` | `C:\Boord-packhouse` |

**How they depend on each other:**
- **Boord is the hub.** Its `data/boord.db` is opened read-only by Owner (harvest, lots, blocks, suppliers), Labour (the worker register, and Boord's `data/photos` for faces), Monitor (tablet station, last sync and outbox) and Packhouse (received lots, blocks, suppliers, workers).
  - Boord is that database's only writer and migrates it at startup.
  - Owner, Labour and Packhouse refuse to boot if a column they read disappears. A Boord migration that drops or renames a column needs those apps updated in step.
- **Labour's badges are Boord's.** The badge QR holds exactly `worker.id` (`Boord/frontend/admin/print-badge.html`), and Labour and Packhouse vendor Boord's `frontend/shared/`.
- **Owner asks Notes.** The "Ask the farm notes" bridge calls Notes' `/api/ai/ask` at `OWNER_NOTES_URL` (`http://127.0.0.1:8020`) and can share Notes' `data/anthropic_key.txt`.
- **Monitor keeps the others running.** Since Monitor 2.0 its `watchdog.ps1` (restarts an app that stops answering) and `heartbeat.ps1` (pings healthchecks.io) cover all seven, as the scheduled tasks "Boord Watchdog" and "Boord Heartbeat". Until then they lived in Boord's repo; Boord's copies are now forwarders to Monitor's.
  - **The app table is Monitor's `apps.json`, the one copy.** Monitor's `backend/apps.py`, `watchdog.ps1` and `heartbeat.ps1` all read it. Adding or moving an app is one line there plus the port table above; the `tailscale serve` ports are also in Boord's MANUAL.md.
  - The watchdog stands down while any app's updater (its `updater` in `apps.json`) or an installer runs, and its `-Restart "<Name>"` is what Monitor's Restart button runs. It exits 2 when it stands down.
- **Monitor watches everything.** It shows each app's up/down state and `/api/version`, Tailscale and the PC, keeps uptime history, SLOs and incidents, and pushes an alert after three failed checks in a row.
  - Every app's `/api/version` keeps the same field names (`tag`, `state`, `frontend_version`), because Monitor and the heartbeat read them all.
  - An off-site copy of the monitor on the owner's Mac alerts when the whole farm PC is unreachable.
- **Kudde and Notes stand alone.** No other app reads their data. They are only watched and restarted.

**What they share by convention, not code:**
- One release key: fingerprint `67C64CFDD584DD140E58AF6E329C9B9DD0562A9D` in each app's `data\release_key.fpr`.
- Releases are signed tags, made from an up-to-date `main` on the Mac and installed by each app's updater on the farm PC.
- `stop_server.ps1` stops each server, never a bare `schtasks /end`. Labour's copy is the reference.
- Each installer adds a Git `safe.directory` exception so SYSTEM's Git can read the repo folder.
- Python 3.9-compatible code, plain JS with no CDNs (the phones are tailnet-only), and `VERSION` plus the service-worker `CACHE` bumped with every frontend change.
- Fixes to the copied deployment files (`install.ps1`, the updaters, `stop_server.ps1`, `release.sh`) flow between the repos.

## Commands

```sh
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
backend/.venv/bin/python3 backend/run_preview.py        # dev server, http://localhost:8811/
backend/.venv/bin/python3 scripts/selftest.py           # the tests: plain asserts, read-only against data/boord.db
cd backend && .venv/bin/python -m alembic revision --autogenerate -m "what changed"   # new migration
scripts/ship.sh v3.22      # push main, then sign and push the tag (wraps release.sh)
scripts/release.sh v3.22   # sign a tag only; VERSION in frontend/shared/api.js must match
```

Keep code Python 3.9-compatible, the farm's minimum: no `X | None`, no `match`.

There is no pytest and no CI. `scripts/selftest.py` is the test suite on purpose: the project carries no test dependency. It never writes, so it is safe to run on the live farm server.

## Architecture

- **Three device roles** (`models.DeviceRole`): field, pack house and admin. A device only ever shows its own role's screens.
  - `frontend/field/`: crate capture.
  - `frontend/packhouse/`: receiving loads.
  - `frontend/admin/`: master data, payments, reports, settings.
  - `frontend/owner/` is a tombstone page whose service worker cleans up phones that cached the old Owner view. That view is now the Boord Owner app.
- **Capture is offline-first.**
  - `frontend/field/idb.js` is the write-ahead log. Every crate is saved on the tablet first.
  - `POST /api/sync` upserts by the crate's phone-made `uuid`, so a re-send is harmless.
  - Synced crates are pruned from the tablet after 14 days.
  - Tablets also post their unsent count (`POST /api/devices/{id}/queue`), which Boord Monitor reads from `boord.db`.
- **No accounts.** `security.py` admits the Admin app only on a request that arrived over Tailscale. That includes the server's own console, because AnyDesk is how the PC is usually reached.
  - It trusts `X-Forwarded-For` only from loopback (`tailscale serve`), with a `.ts.net` Host as the fallback.
  - `run_preview.py` sets `BOORD_ALLOW_LOOPBACK_ADMIN=1` for local development. The farm's launcher never does.
  - The server binds 127.0.0.1. Never bind 0.0.0.0.
- **Schema changes are Alembic migrations** (`backend/migrations/versions/`, run by `migrate.py` at startup and by the updater).
  - A pre-Alembic database is caught up and stamped at the baseline, not replayed.
  - The database is copied before anything alters it. If the copy can't be written, nothing migrates.
  - Drift between `models.py` and the live schema is printed at startup. A drift line means a model changed without a migration.
- **Backups** (`backup.py`): a nightly 02:00 zip of the database and worker photos, written only when the data changed. "Backup Now" in Settings is always unconditional.
- **Market invoices** (`market_mail.py`): every 15 minutes a thread reads the farm Gmail over IMAP (an app password in `data/market_mail.txt`, never in Git), parses the Johannesburg market agents' PDF invoices with `pypdf`, and files them as `MarketInvoice` rows against Packhouse's delivery note numbers. Handled mail is labelled `Boord-Done` in Gmail. An invoice whose figures don't add up is refused, never half-imported.
- **`/api/version`** (`version.py`) reports the release tag, the frontend `VERSION`, the migration head versus current revision, backups and the update check.
  - Its states are `release`, `ahead`, `reported` and `unknown`.
  - Boord Monitor, its `heartbeat.ps1` and anyone at a tablet read it, so keep the field names stable.

## The farm PC

- **Releases are signed tags; merging to `main` deploys nothing.**
  - `update_server.bat` picks the newest `v*` tag and verifies it against `data\release_key.fpr`.
  - It stops the server with `stop_server.ps1`, migrates, and restarts.
  - `setup_update_check.bat` registers a daily `--check` that only reports.
- **Tag only from an up-to-date `main`, and never move a released tag.** `release.sh` refuses a tag whose `VERSION` disagrees, but it can't know your clone is stale: Boord Notes' `v2.16` was signed in a stale clone and points at a months-old commit. Run `git pull --ff-only origin main` first. A wrong tag is fixed with a new release, never by re-pointing it.
- **`stop_server.ps1`, never a bare `schtasks /end`.**
  - Ending the task ends only the launcher. The uvicorn it started keeps the port, so the new release can't bind it and the old one keeps serving.
  - `stop_server.ps1` waits for the port to come free and stops a leftover only if it runs from this repo's `.venv`.
  - It counts only loopback and all-interface listeners, because `tailscale serve --https=<port>` makes tailscaled listen on the app's own port. That is how Kudde, Labour and Monitor are published.
- **The watchdog and heartbeat moved to Boord Monitor** (its 2.0), with the one app table (`apps.json`) they read.
  - `watchdog.ps1` and `heartbeat.ps1` here are only forwarders: a "Boord Watchdog" or "Boord Heartbeat" task registered before the move still runs them, and they hand every argument to Monitor's copies (found from the "Boord Monitor Server" task), passing the exit code back. The heartbeat forwarder also copies `heartbeat_url.txt` to Monitor's `data\`. Monitor's `install.bat` re-points both tasks, after which nothing runs them. Delete them once every farm has done that.
  - So Monitor 2.0 must be on the PC before a Boord release with the forwarders is installed. Without it the watchdog forwarder writes a FAILED line to `data\watchdog.log` and does nothing.
  - `uninstall.ps1` no longer removes the heartbeat task: it covers every app, not just Boord.
  - The `tailscale serve` port table in MANUAL.md must still match Monitor's `apps.json`.

## Frontend

- **Plain JS, no build step, no CDNs.** The tablets and phones are tailnet-only. `shared/tailwind.js` and `shared/vendor/` are served locally.
- **`shared/sw-core.js` is the one service worker.** Each screen's `service-worker.js` declares only `CACHE_PREFIX`, `CACHE` and `SHELL`, then imports it.
- **Any frontend change needs two bumps:**
  - `VERSION` in `frontend/shared/api.js`, which is shown in every header and checked by `release.sh`;
  - `CACHE` in the `service-worker.js` of each screen whose shell changed.
- **Never change the IndexedDB name or bump its version carelessly** (`idb.js`). Unsynced crates live there. An upgrade must keep the existing store.

## Installing and deploying

### Where it lives

| | |
|---|---|
| Your Mac (the only clone to release from) | `~/Documents/Projects/Boord` |
| Farm PC folder | `C:\Boord` |
| Farm PC scheduled task | "Boord Server", runs as SYSTEM at boot |
| Port on the farm PC | `127.0.0.1:8000` |
| Address on the tailnet | `https://bekfontein-server.taile32cb0.ts.net/` |

### First install on the farm PC (once)

Only for a new PC or a reinstall. An app that is already running is updated, not reinstalled.

1. Open a Command Prompt and clone the repo into the folder above:
   ```bat
   git clone https://github.com/DawiePieterse/Boord.git C:\Boord
   ```
2. In `C:\Boord`, double-click **`install.bat`** and accept the administrator prompt.
   It installs Python if needed, builds the app's environment, writes the launcher, registers the scheduled task and starts the server. Re-running it is safe and keeps the data.
3. Tell the updater which release key to trust. In the same folder, type it exactly like this, with the redirect first:
   ```bat
   >data\release_key.fpr echo 67C64CFDD584DD140E58AF6E329C9B9DD0562A9D
   ```
4. Then double-click `setup_update_check.bat` once (a daily check that only reports new releases). The watchdog and the heartbeat are set up by Boord Monitor's `install.bat`.
5. Publish it on the tailnet, in a Command Prompt opened as administrator:
   ```bat
   tailscale serve --bg --https=443 http://localhost:8000
   ```
6. If the installer did not already add it, let SYSTEM's Git read the folder, as administrator (forward slashes):
   ```bat
   git config --system --add safe.directory C:/Boord
   ```
7. Check it from your Mac: `curl -s https://bekfontein-server.taile32cb0.ts.net/api/version` should show the release tag.

### Deploying a change (every release)

Merging to `main` changes nothing on the farm. A release is a signed tag, and the farm PC installs it when its updater runs.

1. **Merge the PR on GitHub.** A frontend change must already bump `VERSION` in `frontend/shared/api.js` and the service worker's `CACHE`.
2. **On your Mac, bring the clone up to date.** This step is what stops a tag landing on an old commit:
   ```sh
   cd ~/Documents/Projects/Boord && git checkout main && git pull --ff-only origin main
   git log -1 --format='%h %s'                  # the merge you expect
   grep -m1 'VERSION:' frontend/shared/api.js   # the version you are releasing
   ```
3. **Sign and push the tag** (`vX.Y` must equal `VERSION`):
   ```sh
   scripts/ship.sh vX.Y        # signs, verifies and pushes the tag
   ```
   Never move or reuse a released tag. Fix a wrong one with the next version.
4. **On the farm PC**, in `C:\Boord`, double-click **`update_server.bat`**. It verifies the signature, stops the server with `stop_server.ps1`, installs dependencies, copies and migrates the database, and starts it again. If it stops with an error, nothing has changed. Read the message; never check the tag out by hand.
5. **If the release changed `install.ps1`** (its notes say "run install.bat once more"), double-click `install.bat` as well. The updater does not rewrite the launcher or the task.
6. **Check from your Mac:** `curl -s https://bekfontein-server.taile32cb0.ts.net/api/version` should show `"tag":"vX.Y","state":"release"`.
7. **Close and reopen the Field, Pack House and Admin apps on every tablet and phone (fully, not just backgrounded) so they load the new version. The version shows in each screen's header.**

## Conventions

Comments are long on purpose. They explain *why*, usually a farm constraint: one admin, tablets out of signal in an orchard, a PC reached over AnyDesk that nobody administers. Keep that style.
