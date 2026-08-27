# Boord - User & Admin Manual

Boord is a small, self-hosted app that tracks litchis
from the moment they're picked in the field, through dispatch to the pack
house, to receiving, wages, and reporting. It runs on one local server and
is used from several devices at once - a phone or tablet at each field
station, one or two devices at the pack house gate, and a computer in the
farm office.

This manual is for everyone who touches the app:
- **Field capture staff** - picking teams who log crates as they're picked.
- **Pack house staff** - whoever receives incoming loads at the gate.
- **Farm admin / office staff** - whoever manages workers, wages, reports,
  and the server itself.

## Table of Contents

1. [Overview & Concepts](#1-overview--concepts)
2. [Initial Server Setup](#2-initial-server-setup)
3. [Device Setup](#3-device-setup)
4. [Field App - Capturing the Harvest](#4-field-app---capturing-the-harvest)
5. [Worker ID Badges](#5-worker-id-badges)
6. [Pack House Receiving](#6-pack-house-receiving)
7. [Admin - Dashboard](#7-admin---dashboard)
8. [Owner View - Analysis, Weather & Risk](#8-owner-view---analysis-weather--risk)
9. [Admin - Master Data](#9-admin---master-data)
10. [Admin - Payments](#10-admin---payments)
11. [Admin - Reports](#11-admin---reports)
12. [Admin - Settings](#12-admin---settings)
13. [Troubleshooting / FAQ](#13-troubleshooting--faq)
    - [Annexe A: Data Field Reference](#annexe-a-data-field-reference)

---

## 1. Overview & Concepts

### The three device roles

Every device that opens the app is one of exactly three roles:

| Role | Used by | What it does |
|---|---|---|
| **Field** | Picking teams, one device per field station | Logs crates as they're picked, sends "picking slips" (loads) to the pack house |
| **Pack House** | Gate/receiving staff | Sees incoming loads, checks them in when the truck arrives |
| **Admin** | Farm office | Manages workers/teams/blocks/suppliers, calculates wages, runs reports, configures settings |

A device only ever shows the screen for its own role - a field tablet never
sees the admin screens, and vice versa. This keeps each device simple and
hard to misuse by accident.

### Why capture is offline-first

Field stations are often out of signal range. The field app is built so
that **capturing a crate never depends on having a connection** - every
crate is saved on the device first, and quietly synced to the server in
the background whenever a connection is available. A picking team can keep
working through a signal dropout without losing anything or needing to
notice it happened.

### Why the oldest-first, color-coded ordering matters

Litchis are highly perishable - quality drops fast once picked, and drops
faster again once picked fruit sits waiting in the sun. Two places in the
app use the same "traffic light" idea to make that visible at a glance:

- The **pack house's incoming queue** shows the oldest (longest-waiting)
  load first, colored **green → yellow → red** as it ages past the
  thresholds set in Settings (see [chapter 12](#12-admin---settings)).
- The **admin Dashboard's Harvesting and In Transit lists** use the exact
  same coloring, so the office can see at a glance whether fruit is moving
  through fast enough.

This isn't just a UI nicety - it's the app's main quality-control signal:
red means "this has been waiting too long, offload/process it before
anything green."

---

## 2. Initial Server Setup

The app is a single Python service (FastAPI) that serves its own frontend
and stores everything in a local SQLite database file - there's no
separate database server, cloud account, or build step involved. It runs
on one ordinary computer in the farm office (the "server"), and every
other device (field phones/tablets, pack house devices) just opens a web
address pointing at that computer - nothing needs to be installed on
those other devices.

### Prerequisites

- A Windows, Mac, or Linux computer that can be left switched on and
  connected to the network for the whole harvest season - this is the
  "server" referred to throughout this manual. It does not need to be
  powerful; any normal office PC is enough (see the recommended spec
  below).
- Python 3.9 or newer (3.11 is a safe default if installing fresh).
- Network access from every device that needs to reach the app (the same
  Wi-Fi/LAN the farm already uses, or a Tailscale network if devices need
  to reach it from outside the farm's own network - the app itself doesn't
  care which, it just needs to be reachable).
- The app's project folder, copied onto that computer (via USB drive,
  email/zip, or `git clone` - however it was given to you). This manual
  assumes it ends up at **`C:\Boord`** on Windows, or
  `~/Boord` on Mac/Linux; adjust the paths below if you use a
  different location.

#### Recommended server PC spec

This is a lightweight app (one Python process, a local SQLite database, no
heavy computation) - it does not need server-grade hardware, just an
ordinary PC that stays switched on.

| Spec | Minimum | Recommended |
|---|---|---|
| OS | Windows 10/11 64-bit, macOS, or Linux | Same |
| CPU | Any dual-core from the last ~10 years (Celeron/i3-class) | Any modern i3/Ryzen 3 or better |
| RAM | 4 GB | 8 GB |
| Storage | 60 GB free | 128 GB+ SSD |
| Network | Wi-Fi or Ethernet, same LAN as field/pack house devices | Ethernet, with a static IP or DHCP reservation |

The app itself (Python plus all its dependencies) takes up around
150 MB. The database grows to a few tens of MB over a season - most of
that is the hourly weather history rather than harvest data - and the 14
rolling backups are small by comparison, because they leave that weather
history out (see [Data Backup](#data-backup)) and are only written on
days something actually changed. Storage capacity is not a real
constraint here.

A small **UPS (uninterruptible power supply)** is worth adding even
though it's not strictly required: the one real risk on a farm is a power
cut corrupting the SQLite database mid-write, and a UPS gives the PC
enough time to either ride out a brief outage or shut down cleanly. No
GPU or other special hardware is needed.

If the server is a **laptop** rather than a desktop, its own battery
already covers this - a brief power cut just runs it on battery instead
of risking a sudden shutdown mid-write, so a separate UPS isn't needed.
See the lid-close note under
[Setting up on a Windows PC](#setting-up-on-a-windows-pc-step-by-step)
though - that's the one laptop-specific setting that still needs
changing.

### Getting the code onto the server: GitHub (recommended) or USB/zip

The app's source code is kept in a **private** GitHub repository at
`github.com/DawiePieterse/Boord`. Deploying from
GitHub is the recommended way to get it onto a new server, and also the
way to pull future updates onto a server that's already running - it
avoids re-copying files by hand and keeps a clear record of exactly what
code is running. Copying the folder via USB drive or a zip file (see
[Prerequisites](#prerequisites)) still works fine if preferred; skip to
[Step 2](#setting-up-on-a-windows-pc-step-by-step) below if so.

**Step A - Install Git for Windows.**
Go to `git-scm.com/download/win` and download/run the 64-bit installer.
The default options on every screen are fine - just click "Next" through
to "Install". This gives the PC the `git` command used below (Windows
already includes the underlying `ssh`/`ssh-keygen` tools it needs, via
its built-in OpenSSH client).

**Step B - Generate an SSH key for this server.**
Since the repo is private, this PC needs its own key to prove it's
allowed to read it. Open Command Prompt and run:
```bat
ssh-keygen -t ed25519 -C "farm-server"
```
Press Enter three times to accept the default file location and no
passphrase. Then display the public key so it can be copied:
```bat
type %USERPROFILE%\.ssh\id_ed25519.pub
```

**Step C - Add the key to GitHub as a deploy key.**
1. In a browser, go to the repository on GitHub, then
   **Settings → Deploy keys → Add deploy key**.
2. Give it a title (e.g. "Farm server"), paste in the public key from
   Step B, and **leave "Allow write access" unchecked** - this server only
   needs to *read* the code, never push changes back, so a read-only key
   is the safer choice.
3. Click **Add key**.

**Step D - Confirm the connection, then clone.**
Test the key against the repository specifically (a plain `ssh -T
git@github.com` does **not** reliably confirm a deploy key - test against
the actual repository URL instead):
```bat
git ls-remote git@github.com:DawiePieterse/Boord.git
```
This should print a list of branches/commits with no error. If it
prints `Permission denied (publickey)`, the key from Step B wasn't added
correctly in Step C - double check it and retry. Once it works, clone the
repo to wherever the app should live, e.g.:
```bat
git clone git@github.com:DawiePieterse/Boord.git C:\Boord
```
Continue from [Step 2 (Install Python)](#setting-up-on-a-windows-pc-step-by-step)
below - the clone replaces Step 1 (copying the folder by hand).

A fresh clone lands on the `main` branch, which is not a release. Once the
server is otherwise set up, do the release-key steps below and then run
`update_server.bat` once - that is what moves it onto the newest signed
release and is where it stays from then on. The clone itself is the one
moment you are trusting the network rather than a signature, so do it over
a connection you control, not on someone else's WiFi.

**Cutting a new release (developer).**
Farm servers install signed tags, so pushing to `main` deploys nothing on
its own. Use the helper rather than tagging by hand:
```bash
scripts/release.sh v2.1
```
It refuses to proceed unless the working tree is clean, the tag looks like
`v<number>`, that tag doesn't already exist, a `user.signingkey` is
configured, and `Boord.VERSION` in `frontend/shared/api.js` matches the
tag. That last check matters more than it looks: `Boord.VERSION` is what
every screen prints in its header, and it is how you tell at a glance
whether a device actually picked up an update - a header that disagrees
with the deployed release is worse than no version display at all.

The tag is signed and verified locally, but **not pushed**. Push it
yourself when you're ready for farms to see it:
```bash
git push origin v2.1
```
Never re-point a tag that has already been pushed. Servers compare the tag
name they are on against the newest tag, so a moved tag silently fails to
re-deploy on any server already sitting on it. Cut a new version instead.

**Trusting the release key (one-time, per server).**
A Boord release is not "whatever is on the `main` branch" - it is a
**tag signed with the release key**. `update_server.bat` refuses to
install a tag that isn't signed by that specific key, which means simply
being able to push to GitHub is not enough to ship code to a farm. That
matters because the server runs as SYSTEM: without this, a stolen GitHub
token would mean code execution on every farm running Boord.

For that check to work, the server needs to know two things - the public
key itself, and which fingerprint to insist on.

**Step 0 - Install GnuPG on the server.** Get **Gpg4win** from
`gpg4win.org` and run the installer; the defaults are fine.

> Git for Windows ships a `gpg.exe` at
> `C:\Program Files\Git\usr\bin\gpg.exe`, and it does **not** work for
> this. It is a build that keeps its keys in a `keyboxd` daemon which the
> Git distribution does not include, so importing a key fails with
> `error running '/usr/lib/gnupg/keyboxd': probably not installed` and
> `Total number processed: 0`. Do not point `gpg.program` at it.

Then tell git which gpg to use, or `git verify-tag` will not find one.
Check where the installer put it and set that path:
```bat
where gpg
git config --global gpg.program "C:/Program Files/GnuPG/bin/gpg.exe"
```
Forward slashes, and keep the quotes - the space in "Program Files" breaks
it otherwise. Open a **new** Command Prompt afterwards, since PATH changes
do not reach an already-open window.

**Step 1 - Import the public key.** On the developer's machine, export it:
```bash
gpg --armor --export <KEY-ID> > boord-release-key.asc
```
Copy that file to the server (USB is fine - it is public), then in Command
Prompt on the server:
```bat
gpg --import boord-release-key.asc
```

**Step 2 - Record the fingerprint the server will trust.** Get the
40-character fingerprint:
```bat
gpg --fingerprint <KEY-ID>
```
Write it, with no spaces, into `data\release_key.fpr`:
```bat
echo A1B2C3D4E5F6A7B8C9D0E1F2A3B4C5D6E7F8A9B0> data\release_key.fpr
```

> This file deliberately lives in `data\`, not in the checkout. A
> fingerprint stored inside the repository would be rewritten by the very
> update it is supposed to be vouching for - so anyone who could push
> could also swap in their own key. `data\` is gitignored, so a checkout
> never touches it. Treat this file as the thing that actually protects
> the farm, and only change it when you deliberately rotate the key.

If `data\release_key.fpr` is missing, `update_server.bat` stops and
updates nothing. That is intentional: a server that cannot tell a genuine
release from a tampered one should not be installing either.

**Pulling future updates.**
Once a new signed release is tagged and pushed, update an already-running
server.

The easy way: double-click **`update_server.bat`** at the top of the
project folder. It fetches the release tags, picks the newest `v*` by
version order, **verifies its signature against `data\release_key.fpr`**,
checks it out, installs any new dependencies, refreshes historical weather
and harvest data (see below), and restarts the server (via the Scheduled
Task) all in one step - this is the recommended way to deploy an update,
since it's easy to forget the restart step if done by hand (a checkout
alone does not restart anything, so the running server keeps serving the
old code until it's explicitly restarted).

If the signature check fails, the script stops and changes nothing - the
server keeps running the release it was already on. Do not "fix" this by
checking the tag out by hand; find out why it failed first. The usual
innocent cause is a rotated release key that this server wasn't told
about.

The manual way, if you'd rather do each step yourself: open Command
Prompt in `C:\Boord` and run:
```bat
git fetch --tags --force origin
git tag --list "v*" --sort=-v:refname
git verify-tag <newest-tag>
git checkout --force <newest-tag>
```
Check the `verify-tag` output names your release key **before** the
checkout - that is the whole point of the exercise. The checkout leaves
git in "detached HEAD" state, which is normal and correct here: the server
tracks releases, not a branch.

This only touches the app's code - the database, worker photos, and
backups all live in the gitignored `data/` folder and are never affected
by an update. If `backend/requirements.txt` changed, re-run the installer
(`install.bat`) or `pip install -r requirements.txt` to pick up any new
dependencies. Optionally refresh historical data too (`update_server.bat`
does all three of these automatically - see below for why it matters):
```bat
backend\.venv\Scripts\python.exe scripts\import_historical_weather.py
backend\.venv\Scripts\python.exe scripts\import_historical_weather_archive.py
backend\.venv\Scripts\python.exe scripts\import_historical_annual_yield.py
```
(`import_historical_harvest.py`, the 2020-2025 daily harvest import, is
NOT one of these three - it's a true one-off against a fixed source
workbook, so it isn't part of `update_server.bat`'s automatic refresh; see
[Re-importing historical data on a server](#re-importing-historical-data-on-a-server)
in the Analysis chapter if it's ever needed by hand, e.g. after
regenerating that workbook.) Only 2020-2025 actually drives the Risk
indicator or Harvest Forecast (both fixed to that reference range);
1987-2019 is reference-only, for the Weather tab and the Historical
Harvest Data report's Annual Totals sheet.

Then restart the server (see
[Stopping, starting, and restarting the server](#stopping-starting-and-restarting-the-server-task-scheduler)
below).

**Checking an install is sound.** After a fresh install, a database
restore, or any update that worries you, run the self-test:
```bat
backend\.venv\Scripts\python.exe scripts\selftest.py
```
It checks the parts of the app that do real arithmetic - the Risk
indicator's scoring, the Harvest Forecast's projections, and whether every
sheet of the Historical Harvest Data report reconciles against the
database - and prints a line per check, ending in PASSED or FAILED. It
reads only; it never writes to the database and needs no internet, so it
is safe to run on the live server at any time, including while people are
using the app. If anything reports FAILED, the message names what
disagreed - send that text on rather than acting on the app's figures.

**Why `update_server.bat` refreshes weather on every update.** Open-Meteo's
recent days start out as provisional forecast-model estimates and firm up
into finalized reanalysis figures over the following days/weeks - the
Weather tab's own automatic sync (`weather.sync_recent_weather()`, run as a
side effect of opening that tab) only ever *appends* new hours, so it never
goes back and corrects an earlier provisional value once the real one is
available. `update_server.bat` re-runs the full historical import (a
wholesale replace, same script as the one-off backfill above) between
stopping and starting the server, so the
[Risk tab](#8-owner-view---analysis-weather--risk)'s
score and Harvest Forecast - both sensitive to exactly how accurate recent
weather is, not just whether it's present - are working from the best data
available each time the server restarts. The 1987-2019 weather and harvest
scripts run there too, mostly as a no-op once already populated (that data
is finalized and doesn't change) - they're there so a fresh or rebuilt
server picks them up automatically instead of needing the manual steps
above. If any of these steps fail (no internet, Open-Meteo unreachable),
the update still completes and the server still
restarts - it just keeps whatever weather history it already had until the
next successful refresh.

Either way, remember that each phone/tablet's installed app also needs a
full close-and-reopen afterward to pick up the update - see
[Confirming devices picked up an update](#confirming-devices-picked-up-an-update-version-numbers).

### Stopping, starting, and restarting the server (Task Scheduler)

If the server was set up via `install.bat` (or manual Step 12), it runs as
a Scheduled Task named **"Boord Server"** that starts
automatically at boot, as SYSTEM, with **no visible window** - there's no
Command Prompt to close, so stopping/starting it goes through Task
Scheduler instead.

**Using the Task Scheduler app:**
1. Open **Task Scheduler** (search for it in the Start menu).
2. Find **"Boord Server"** in the Task Scheduler Library.
3. Right-click it → **End** to stop the server, or **Run** to start it.
   To restart (e.g. after an update), do **End** then **Run**.

**Using Command Prompt (must be "Run as administrator"):**
```bat
schtasks /end /tn "Boord Server"
schtasks /run /tn "Boord Server"
```

**Simplest restart:** since the task launches automatically on every
startup anyway, just restarting the PC has the same effect as End + Run.

**Checking it's actually running:** browse to `http://localhost:8000/` on
the server PC itself - if the device setup screen loads, it's up. There's
no window to glance at for this, since the task runs headless.

### Quick setup: the automated installer (recommended)

The project folder includes `install.bat`, which automates everything in
the manual step-by-step section below - installing Python if it's
missing, creating the virtual environment, installing dependencies,
opening the firewall port, and registering the server to auto-start with
Windows (no login or password needed for it to start). It's safe to
double-click again later if something needs redoing - each step checks
what's already in place first.

1. Get the project folder onto the PC, e.g. by
   [cloning it from GitHub](#getting-the-code-onto-the-server-github-recommended-or-usbzip)
   or copying it via USB/zip (see Step 1 below).
2. Double-click **`install.bat`** at the top of that folder.
3. If Windows shows a blue **"Windows protected your PC"** screen, click
   **"More info"**, then **"Run anyway"**. This is normal for any script
   that isn't from a large, registered publisher - it doesn't mean
   anything is wrong with it.
4. If a **User Account Control** prompt appears asking to let the app
   make changes, click **"Yes"** - administrator rights are needed to
   configure the firewall and register the auto-start task.
5. Wait for it to finish. A black window will print its progress (this
   can take a few minutes the first time, mostly spent downloading Python
   and the app's dependencies) and finish with the address to browse to
   from other devices. Press Enter to close the window when it says
   "Setup complete!".
6. **Change the default admin password immediately** - the installer
   does not do this for you. Log in with username `admin` and password
   `ChangeMe123!`, then go to Settings → Change admin password.

If the installer fails partway, or you'd rather understand/do each part
by hand, use the manual steps below instead - they're exactly what the
installer automates.

### Setting up on a Windows PC (step by step)

This is the most common setup, since most farm offices run Windows. Every
step is done once, when first setting up the server. **Most farms can
skip straight to the automated installer above** - use these steps
instead if you prefer to do it by hand, or need to troubleshoot one
specific part.

**Step 1 - Get the app folder onto the PC.**
Either [clone it from GitHub](#getting-the-code-onto-the-server-github-recommended-or-usbzip)
(recommended), or copy the whole project folder over via USB drive or a
zip file. Either way, place it somewhere permanent and easy to find, e.g.
`C:\Boord`. Avoid Desktop or Downloads, since those are more
likely to get tidied up or deleted by accident.

> **Never put the app folder inside a synced cloud drive** - Google Drive,
> OneDrive, Dropbox or iCloud. Two things go wrong. The sync client keeps
> file handles open, which blocks updates and folder deletion (this is a
> real one: it took a reboot to work out why a folder refused to delete).
> More seriously, `data\` holds a live SQLite database plus every worker's
> ID number, bank details and photograph - syncing a database file while
> it is being written is a known way to corrupt it, and uploading worker
> records to a personal cloud account is a data-protection problem you do
> not want. Back up *to* a cloud drive by all means (chapter 14) - a
> backup zip is a finished file, which is exactly what sync is good at.
> Just do not run the app from one.

> **Set the farm's GPS location before importing any weather.** Weather,
> the Risk indicator and the Harvest Forecast are all computed for the
> coordinates in **Admin → Settings**, and until those are filled in the
> app will not fetch weather at all - the header reads "Set farm location
> in Settings" and the import scripts skip with a message. That is
> deliberate. Guessing a location produces confident Risk scores for
> somewhere else, which looks completely normal and is completely wrong.

**Step 2 - Install Python.**

1. Go to `python.org` in a browser and download the latest Python 3
   installer for Windows (64-bit).
2. Run the installer. **On the very first screen, tick the checkbox at
   the bottom that says "Add python.exe to PATH"** before clicking
   "Install Now" - this step is easy to miss and, if skipped, every command
   below will fail with `'python' is not recognized`.
3. Once installation finishes, click "Close".

**Step 3 - Open Command Prompt in the `backend` folder.**

1. Open the `C:\Boord\backend` folder in File Explorer.
2. Click into the empty area of the address bar at the top of the
   window, type `cmd`, and press Enter. A black Command Prompt window
   opens already pointed at that folder (confirm the prompt reads
   `C:\Boord\backend>`).

**Step 4 - Create a virtual environment.**
A virtual environment keeps this app's Python packages separate from
anything else on the PC. In the Command Prompt window, run:
```bat
python -m venv .venv
```
This creates a `.venv` folder inside `backend` and takes a few seconds.

**Step 5 - Activate the virtual environment.**
```bat
.venv\Scripts\activate
```
The prompt changes to start with `(.venv)` once this has worked - check
for that before continuing. You'll need to repeat this activation step
every time you open a new Command Prompt window to work with the app
(but *not* every time the server itself runs day-to-day - see Step 10).

**Step 6 - Install the app's dependencies.**
```bat
pip install -r requirements.txt
```
This downloads everything the app needs (FastAPI, the database library,
etc.) into the virtual environment. It can take a few minutes on the
first run, depending on the internet connection. If it fails partway with
a build/compiler error, the most common cause is a 32-bit Python install -
uninstall it and reinstall the 64-bit version from Step 2.

**Step 7 - Run the server for the first time.**
```bat
uvicorn main:app --host 0.0.0.0 --port 8000
```
- `--host 0.0.0.0` makes the server reachable from other devices on the
  network, not just this PC - this is required for field/pack house
  devices to connect.
- `8000` is just an example port; any free port works, but stick with one
  number once devices are configured against it.
- The window will print a few lines ending in something like
  `Uvicorn running on http://0.0.0.0:8000` and then go quiet - that's
  normal; it means the server is up and waiting. **Leave this Command
  Prompt window open** - closing it stops the server.

**Step 8 - Confirm it's working.**
On the same PC, open a browser and go to `http://localhost:8000/`. You
should see the app's device setup screen (see [chapter 3](#3-device-setup)).
If you see this, the server itself is working correctly.

**Step 9 - Find this PC's network address.**
Other devices don't use `localhost` - they need this PC's actual address
on the network. Open a **second** Command Prompt window (leave the server
running in the first one) and run:
```bat
ipconfig
```
Look for **"IPv4 Address"** under the network adapter that's actually
connected (Wi-Fi or Ethernet) - it looks like `192.168.1.50`. Other
devices will reach the app at `http://192.168.1.50:8000/` (using this
PC's own address and the port from Step 7).

**Step 10 - Allow the app through Windows Firewall.**
Windows will usually pop up a "Windows Defender Firewall has blocked some
features of this app" prompt the first time the server starts - tick both
"Private networks" and "Public networks" (if shown) and click "Allow
access". If that prompt was missed or dismissed, add the rule manually:

1. Open **Windows Security → Firewall & network protection → Advanced
   settings**.
2. Click **Inbound Rules → New Rule…**.
3. Choose **Port** → Next.
4. Choose **TCP**, and under "Specific local ports" enter the port from
   Step 7 (e.g. `8000`) → Next.
5. Choose **Allow the connection** → Next.
6. Leave Domain/Private/Public all ticked → Next.
7. Give it a name, e.g. "Boord Server" → Finish.

**Step 11 - Stop the PC from going to sleep.**
The server only works while the PC is awake. Go to **Settings → System →
Power & battery → Screen and sleep**, and set "When plugged in, put my
device to sleep" to **Never**. (The screen itself can still turn off -
that doesn't affect the server - only "sleep"/"hibernate" does.)

> **If the server is a laptop, not a desktop PC:** the above isn't
> enough by itself - closing the lid puts a laptop to sleep (or shuts it
> down) by default, regardless of the sleep-timeout setting, which would
> take the server offline. Search the Start menu for **"Choose what
> closing the lid does"** and set **"When I close the lid" (on battery
> and plugged in)** to **"Do nothing"**. Combined with Step 11 above, the
> server then keeps running lid-closed and plugged in.

**Step 12 - (Recommended) Make the server start automatically.**
Without this step, someone has to manually repeat Steps 3, 5, and 7 every
time the PC restarts (e.g. after a power cut or Windows update). To avoid
that, first create a new text file at `C:\Boord\start_server.bat`
containing exactly:

```bat
@echo off
cd /d "C:\Boord\backend"
call .venv\Scripts\activate.bat
uvicorn main:app --host 0.0.0.0 --port 8000
```

Then set Windows to run it automatically on startup:

1. First close any server already running: Task Manager (Ctrl+Shift+Esc) →
   End task on every `python.exe`. From here on the scheduled task owns the
   server - starting `start_server.bat` by hand as well is what leaves two
   instances running on different ports (see
   [chapter 13](#13-troubleshooting--faq)).
2. Open **Task Scheduler** (search for it in the Start menu).
3. Click **Create Basic Task…** and name it exactly **Boord Server**, Next.

   > The name is not cosmetic. `update_server.bat` restarts the server by
   > looking this task up by that literal string; under any other name it
   > silently skips the restart and leaves the old backend running while
   > the newly-pulled frontend is served straight from disk - so the
   > version badge updates and the fix appears not to have deployed.

4. Trigger: choose **"When the computer starts"**, Next.
5. Action: choose **"Start a program"**, Next, then browse to and select
   `C:\Boord\start_server.bat`, Next, Finish.
6. Find the new task in the Task Scheduler Library, right-click →
   **Properties**, and on the **Settings** tab tick **"If the task fails,
   restart every:"**, set 1 minute, so a crash recovers itself.
7. Still in Properties, set who it runs as - **and match the trigger to
   that choice**, or the task never fires:

   | Account has | General tab | Triggers tab |
   |---|---|---|
   | A Windows password | **"Run whether user is logged on or not"** (asks for the password on save) | leave as **At startup** |
   | No password | **"Run only when user is logged on"** | change to **At log on** |

   > "At startup" fires before anyone has signed in. Combined with "run
   > only when user is logged on" that condition is never true when it
   > fires, so the task quietly never runs and the server stays down after
   > a reboot - with nothing in the task's History to suggest it was
   > supposed to. A PC with no Windows password auto-signs-in at boot, so
   > "At log on" gets there moments later and is the pairing to use.

   Adding a Windows password purely to enable the first row is usually the
   wrong trade for a server left in an office: it makes the PC stop at a
   lock screen after a power cut, which is worse for unattended restarts
   than what it fixes.

8. Right-click the task → **Run**, and check `http://localhost:8000/admin/`
   loads on the server PC itself.
9. **Then restart the PC and, without touching anything, check the app
   again from another device.** An auto-start that was never tested this
   way is worse than none, because everyone assumes it's covered.

With this in place the server behaves like any other piece of office
equipment - it just needs the PC left on.

### Setting up on Mac or Linux

From the `backend/` folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Then, to run it:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

- `--host 0.0.0.0` makes the server reachable from other devices on the
  network, not just the machine it's running on.
- Find this machine's network address with `ifconfig` (look for `inet`
  under the active Wi-Fi/Ethernet adapter) so other devices can reach
  `http://<that-address>:8000/`.
- To keep the server running after closing the terminal, and to have it
  restart automatically after a reboot, use your platform's standard
  approach for long-running services (e.g. a `launchd` agent on Mac, or a
  `systemd` service on Linux) pointed at the same `uvicorn` command above.

### Connecting external users with Tailscale (only if needed)

Skip this section entirely if every device (field, pack house, admin) stays
on the farm's own Wi-Fi/LAN - it's only needed if someone **outside** that
network has to reach the server, e.g. a remote bookkeeper checking Reports,
or a partner farm's own admin accessing their supplier data from elsewhere.
Tailscale is a free, private network that lets specific outside devices
reach the server securely over the internet, without opening any ports on
the farm's router or exposing the app to the public internet.

> **An off-site owner who only wants to check progress** doesn't need
> Tailscale or a login at all - see
> [Owner View](#owner-view-read-only-dashboard-link) in
> [chapter 12](#12-admin---settings) for a link-only read-only Dashboard.
> Come back to this section if they need the *full* Admin screen remotely
> instead.

**Step 1 - Install Tailscale on the server.**

1. On the same PC the server runs on, go to `tailscale.com/download` in a
   browser and download the installer for its operating system (Windows,
   Mac, or Linux).
2. Run the installer, then sign in when prompted (with a Google, Microsoft,
   or email account) - this creates your farm's own private Tailscale
   network (a "tailnet"), separate from anyone else's.
3. Once signed in, Tailscale assigns this server a private address like
   `100.x.x.x`. Find it by clicking the Tailscale icon in the system tray
   (Windows) or menu bar (Mac) and reading the address shown there, or by
   running `tailscale ip -4` in a Command Prompt/terminal.
4. Make sure Tailscale is set to start automatically: right-click its tray
   icon → **Preferences/Settings** → confirm **"Start on login"** (or
   equivalent) is ticked, so it reconnects on its own after every restart,
   the same way the server itself does ([Step 12](#setting-up-on-a-windows-pc-step-by-step)
   on Windows).

> **This farm's setup:** the server is signed into a specific Tailscale
> account, with its own tailnet address. Account, address, and password
> are kept in a password manager, not this file.

**Step 2 - Share (not invite) access to just this one machine.**

Tailscale's **"Share"** feature grants an outside person access to only
this one server, without adding them to the rest of the farm's tailnet or
letting them see any other device on it - this is the right option for an
external user, as opposed to "Invite" (which adds someone as a full member
of the tailnet).

1. In a browser, go to `login.tailscale.com/admin/machines` and sign in
   with the same account used in Step 1.
2. Find this server in the machine list, click the **"…"** menu next to
   it, and choose **Share…**.
3. Enter the external person's email address and send the invite (or copy
   the generated link and send it yourself, e.g. via WhatsApp or email).
4. The external person accepts the invite, creating their own free
   Tailscale account if they don't already have one, and installs
   Tailscale on their own phone, tablet, or PC (same download step as
   Step 1, but signing in with their own account).

**Step 3 - Connecting.**
Once accepted, the external device can reach this one server at its
Tailscale address from anywhere with an internet connection - e.g.
`http://100.x.x.x:8000/` - exactly the same way a device on the farm's own
Wi-Fi reaches it by LAN address. Everything else (the device setup screen,
roles, admin login) works identically; Tailscale only changes how the
device reaches the server, not what it can do once it's there.

**Step 4 - Removing access later.**
When an external person no longer needs access (e.g. a contractor's
season is over), go back to `login.tailscale.com/admin/machines`, find
their device under the server's sharing settings, and remove it - this
revokes their access immediately without affecting anyone else.

### Enabling the QR camera scanner (HTTPS via Tailscale - required for Field devices)

Android's and iOS's browsers only allow a page to use the camera if it's
loaded over HTTPS or from `localhost` - a plain LAN address like
`http://192.168.1.50:8000/` fails that check, so **Scan Worker QR** in the
Field app (see [chapter 4](#4-field-app---capturing-the-harvest)) will fail
with **"Camera unavailable"** on every device that reaches the server this
way. Since worker identification is QR-only with no manual picker
fallback, this isn't optional - it must be set up before Field devices can
capture harvest data at all.

Field devices also need to work over **mobile data** while out picking
(they're only back on the farm's own Wi-Fi at the end of the day), which
rules out a certificate tied to the server's local network address - that
would only ever work from on-site. Tailscale HTTPS is the one setup that
covers both requirements at once, since it's reachable over the internet
from anywhere the device has a signal, not just the farm's LAN.

Fixing this means putting Tailscale's HTTPS in front of the app, which
requires no changes to the app itself:

1. Install Tailscale on the server first (see
   [Connecting external users with Tailscale](#connecting-external-users-with-tailscale-only-if-needed))
   if not already done.
2. Turn on certificates for the tailnet: go to
   `login.tailscale.com/admin/dns` and enable **"HTTPS Certificates"**
   (requires MagicDNS, which is on by default).
3. On the server, run:
   ```bash
   tailscale serve --bg --https=443 http://localhost:8000
   ```
   (use whichever port the app actually runs on). This runs in the
   background and proxies HTTPS traffic straight through to the existing
   plain-HTTP app - `uvicorn`/`main.py` don't need to change.
4. Find the exact address to use with `tailscale status` - it looks like
   `https://<server-name>.<tailnet-name>.ts.net/`.

Once this is done, every Field/Pack House device needs the Tailscale app
installed and connected, and pointed at the new address - see
[Connecting via Tailscale HTTPS](#connecting-via-tailscale-https) in
[chapter 3](#3-device-setup).

### Remote desktop access via AnyDesk

Separate from Tailscale (which only gets a device to the app itself),
[AnyDesk](https://anydesk.com) is installed on the server PC for full
remote-desktop control - useful for admin tasks that need the actual
Windows desktop (restarting the Scheduled Task, running `update_server.bat`,
Windows updates, etc.) without physically being at the farm office.

> **This farm's server:** has a fixed AnyDesk access code for unattended
> access. Code and password are kept in a password manager, not this
> file.

To connect: install AnyDesk on the device you're connecting from, enter
this server's access code, and provide the password when prompted.

### Uptime alerting (email if the server goes down)

Gets you an email if the server is unreachable for more than an hour.
This can't be done by anything running only on the server PC itself - if
that PC loses power or its internet entirely, nothing on it can send you
anything. Instead it uses a **"dead man's switch"**: the server pings an
outside monitoring service every 10 minutes to say "still alive," and
that outside service is the one that notices when the pings stop and
emails you - it's watching for silence, not waiting to be told about a
problem.

**Step 1 - Create a healthchecks.io check.**
1. Sign up free at [healthchecks.io](https://healthchecks.io).
2. Create a check (e.g. named "Boord Server").
3. Click **Edit** and set **Period** to **10 minutes** and **Grace Time**
   to **1 hour** - this means an isolated missed ping (a brief network
   blip) is tolerated, but if pings stop entirely for over an hour, it
   emails the account's address.
4. Copy the ping URL shown on the check's page (starts with
   `https://hc-ping.com/...`).

**Step 2 - Save the ping URL on the server.**
Create a new text file at `heartbeat_url.txt`, in the same folder as
`heartbeat.ps1` (the top of the project folder), containing just that one
URL and nothing else. This file is deliberately **not** committed to
git - like a password, it's account-specific and shouldn't live in
version control (`.gitignore` already excludes it).

**Step 3 - Register the heartbeat task.**
Double-click **`setup_heartbeat.bat`**. It registers a Scheduled Task
("Boord Heartbeat") that runs `heartbeat.ps1` every 10 minutes,
which only pings healthchecks.io when `http://localhost:8000/` actually
responds - so a crashed/hung server (not just a powered-off PC) also
triggers the alert, since the heartbeat is contingent on the app itself
working, not just the PC being on.

That's it - as long as the check on healthchecks.io keeps receiving
pings, nothing happens. If the server goes down and stays down past the
1-hour grace period, healthchecks.io emails the account used to sign up.

### Keeping the server running

However it's started, leave it running continuously during harvest
season - the app expects to be a long-lived local service, not something
started and stopped around each use. (The automatic nightly backup,
[chapter 12](#12-admin---settings), only fires if the server happens to
be running at 02:00 - see that chapter's note on this limitation.
Separately, a night on which nothing changed is skipped on purpose and
adds no backup - that's normal, not a fault.)

### What happens on first startup

The very first time the server runs, it automatically creates the
database and seeds a clean starting baseline:

- Two teams: **Span A** and **Span B** (indunas left blank - fill in via
  [Master Data](#9-admin---master-data))
- **8 devices**: `device-01` through `device-05` (field), `device-06` and
  `device-07` (pack house), and `admin-pc` (admin) - see
  [chapter 3](#3-device-setup) for how these get assigned to physical
  devices
- One supplier row representing the farm's own fruit ("Own Farm") - rename
  it to your farm under [Master Data](#9-admin---master-data)
- A default admin login: **username `admin`, password `ChangeMe123!`**

**No wage rate.** Nothing is seeded, and **Calculate** under
[Payments](#10-admin---payments) refuses with "No wage rate has been set"
until you enter one under [Settings](#12-admin---settings). A wrong block
list is obvious the moment somebody opens the Field app; a wrong wage rate
produces a perfectly ordinary-looking payslip at the wrong number, and the
person most likely to notice is the one being underpaid. So the app will
not guess, and it will not quietly calculate everyone at R0.00 either.

**No blocks.** A new database has an empty block register, and the Field
app's Block dropdown reads "(no blocks set up yet - add them in Admin)"
until you fill it in. That is deliberate: a block register is the shape of
one particular orchard - its labels, varieties, tree counts and hectares -
and no two farms share one.

Import yours via **Admin → Master Data → Blocks → Import**, using
`templates/blocks.csv` in the project folder for the column headings
(`id`, `name`, `variety`, `trees`, `hectares`, `active`). `templates/README.md`
describes each column. Both `.csv` and `.xlsx` are accepted, and importing
again updates blocks with a matching `id` rather than duplicating them, so
it is safe to correct a mistake and re-import.

> **⚠️ Change the default admin password immediately** after first login,
> via [Settings → Change admin password](#12-admin---settings).

### ⚠️ Do not run `seed_demo.py` on a real farm database

The repo includes `backend/seed_demo.py`, which fills the database with
**fake historical harvest data** for demoing and testing the app. It is
**not** part of normal startup and must never be run against a real farm's
database - doing so will inject invented workers, lots, and payments
alongside real data with no easy way to tell them apart afterward. It's
safe to use only against a throwaway/test copy of the app.

---

## 3. Device Setup

The very first time any device (phone, tablet, or computer) opens the
app's URL, it shows a one-time **Device Setup** screen instead of jumping
straight into a role's screen.

1. Pick this device's **Device ID** from the dropdown. The list comes from
   the server and shows every active device with its station - e.g.
   `device-08 - Field Station 6` - so a device added in
   [Master Data → Devices](#9-admin---master-data) is available here
   straight away, with no change to the app needed.
2. Tap **Continue**.
3. The device looks up that ID's role and **automatically routes itself**
   to the right screen - Field, Pack House, or Admin - based on what's
   configured for that device.
4. The device **remembers its ID** after this (stored in the browser, not
   on the server) - it won't ask again on future visits, and will jump
   straight to its role's screen.

> If the setup screen can't reach the server it can't show the list, and
> falls back to a box for typing the ID by hand. Setup itself still needs
> the server, since the ID has to be checked against Master Data before
> the device can be used.

### Installing as an app icon (optional, recommended)

Each of the three screens - Field, Pack House, Admin - is a small
installable app (a "PWA") with its own name and icon, so a device can show
"Veld", "Pakhuis", or "Admin" on its home screen like any
other app, instead of a browser tab/bookmark. This isn't required (the
browser bookmark/URL works fine on its own), but it makes the right screen
one tap away and avoids anyone confusing a browser address bar with the
actual app.

**Upgrading from an older install.** If the device previously had the app
under its old name, remove that icon from the home screen and clear the
site data for the server address before re-adding it. A leftover icon keeps
pointing at the old cached copy, so the device can look like it did not pick
up the update.

**Important: install from the role's own screen, not the setup screen.**
Let the device auto-route itself first (steps above), then install from
the resulting URL (e.g. `.../field/`, `.../packhouse/`, `.../admin/`) -
installing from the very first device-ID entry screen would just save a
generic icon, not the role-specific one.

- **Android (Chrome)**: tap the three-dot menu (top right) → **"Add to
  Home screen"** (or look for an automatic "Install app" banner/icon in
  the address bar).
- **iPhone/iPad (Safari)**: tap the **Share** icon → **"Add to Home
  Screen"**.
- **Windows/Mac (Chrome or Edge)**: look for an **install icon** (a
  monitor with a down-arrow) at the right end of the address bar, or use
  the three-dot/three-line menu → **"Install [app name]..."**.

Each installs independently with its own icon and name - installing the
Field app on a phone doesn't affect what a Pack House tablet or the
office admin computer shows.

### Connecting via Tailscale HTTPS

Assumes the server-side setup is already done - see
[Enabling the QR camera scanner](#enabling-the-qr-camera-scanner-https-via-tailscale---required-for-field-devices)
in [chapter 2](#2-initial-server-setup). Every Field/Pack House device
needs this, not just ones that leave the farm - they need to keep working
over mobile data while out picking, which only Tailscale (not a
LAN-only setup) can do.

**Step 1 - Install and connect Tailscale on this device**, signed into
the same tailnet as the server (see the next section for keeping it
connected reliably on Android).

**Step 2 - Point the device at the new address.**
Open the `https://...ts.net/` address instead of the old LAN IP. This is
a different origin, so it'll show the Device Setup screen again (above)
- re-enter that device's ID once. Then reinstall the home-screen app icon
from the new HTTPS address (see
[Installing as an app icon](#installing-as-an-app-icon-optional-recommended)
above), and delete the old icon that points at the `http://` address.

### Keeping Tailscale always-on on Android field devices

Since every Field/Pack House device depends on Tailscale to reach the
server (see above), Android's aggressive battery management can silently kill the
Tailscale connection in the background, which then breaks the app until
someone notices and reopens Tailscale manually. To stop that happening:

1. **Turn on Always-on VPN.** Settings → **Network & Internet → VPN** → tap
   the gear icon next to Tailscale → enable **"Always-on VPN"**. This
   makes Android keep it running and reconnect it automatically after a
   restart.
2. **Exempt Tailscale from battery optimization.** Settings → **Apps →
   Tailscale → Battery** → set to **"Unrestricted"** (some phones label
   this "Unmonitored app" or "Don't optimize"). Without this, Android
   periodically freezes the app in the background and the connection drops
   until someone opens it again.
3. **Check for a manufacturer-specific app killer.** Samsung, Xiaomi,
   Huawei, and OnePlus phones ship an extra battery manager on top of
   stock Android that can re-kill apps even after Step 2 - look for a
   separate "Sleeping apps," "Protected apps," or "Autostart manager" list
   under that phone's battery/device-care settings and make sure Tailscale
   is excluded/allowed there. [dontkillmyapp.com](https://dontkillmyapp.com)
   has exact steps per phone model.
4. **Stay signed in.** If Tailscale ever gets signed out, it stops
   connecting entirely until someone signs back in - it won't silently
   reconnect on its own.

### Confirming devices picked up an update (version numbers)

Every screen - Field, Pack House, Admin - shows a small **v{number}** in
its top-right corner (e.g. `v1.1`). This is the one reliable way to confirm
a device is actually running the latest code after an update and
restart, since the Field/Pack House/Admin apps are installable PWAs with
an offline cache (see
[Installing as an app icon](#installing-as-an-app-icon-optional-recommended)
above) - a device can stay on an older cached version even after the
server itself has been updated, until its cache is refreshed.

**After deploying an update:** check the version number on a few devices.
If one is behind, do a normal open-close-reopen of the app icon (see
[chapter 13](#13-troubleshooting--faq) - same fix as a stuck "Camera
unavailable" screen); a full close and reopen is what lets the app notice
and install the new cached version in the background, then show it on the
next open.

The version number only changes when the code that ships it changes - it's
incremented deliberately each time a real update goes out, not tied to the
date or any automatic counter.

### "Unknown device id"

If the ID isn't recognized, the device shows an error and refuses to
continue. This is mainly reachable by typing an ID by hand (the dropdown
only ever offers IDs that exist), or if the device was deleted from Master
Data after being set up. **Devices are never auto-registered** - an admin
must create the device first, in
[Master Data → Devices](#9-admin---master-data), before it can be used. This is intentional: it stops a stray or
mistyped device ID from silently attaching itself to the wrong team or
role.

### Reassigning a device

To point a device at a different role/station (e.g. repurposing a spare
tablet), either:
- Clear the device's browser data/cache so it forgets its saved ID and
  shows the setup screen again, or
- Simply change that device ID's role/station in
  [Master Data → Devices](#9-admin---master-data) - the physical device
  keeps using the same ID, but the server now treats it differently on its
  next check-in.

---

## 4. Field App - Capturing the Harvest

The field app is what a picking team's device shows once set up. Its job
is simple: log every crate as it's weighed, and send a "picking slip" (a
load of crates) to the pack house when a truck is ready to take it.

### Station header

The top of the screen shows which station/team/induna this device is
registered as, and a sync status pill. The pill is **colour-coded** so it
can be read at arm's length without stopping to look properly:

| Pill | Colour | Meaning |
|---|---|---|
| **Online - synced** | Green | Everything captured on this device has reached the server. |
| **Syncing 3...** | Amber | Crates are being uploaded right now. |
| **Offline** | Red | The server can't be reached. Capture carries on regardless. |
| **Offline - 3 pending** | Red | Same, and 3 captured crates are waiting to be sent. |
| **Online - sync failed, retrying** | Amber | The server answered but rejected the last attempt. Nothing is lost; it keeps retrying. |

"Offline" covers both having no signal at all and having signal but no
route to the farm server - from the device's point of view they're the
same thing, and it's the second one that's more common in the orchard.

### The offline banner

Whenever a screen can't reach the server it also shows an amber bar under
the header - on the field app, *"Offline - crates save to this device and
sync when back in range"*. It clears itself the moment the connection
comes back. Every screen has one (see
[chapter 6](#6-pack-house-receiving) and
[chapter 7](#7-admin---dashboard)); the wording differs to suit what that
screen does while offline.

### Pull down to refresh

On any screen, dragging down from the top of the page reloads that
screen's data - the same as the Refresh button where there is one. It's
there because an installed app has no browser reload button. Dragging
inside a dialog or a list that scrolls on its own just scrolls it as
normal.

### Identifying the worker: QR scan only

Tap **Scan Worker QR** and point the device's camera at the worker's
printed ID badge (see [chapter 5](#5-worker-id-badges) for how badges are
made). The app matches the scanned code against known workers and shows
the worker's name once matched.

> **Why QR-only, no typing/dropdown?** Picking teams can move dozens of
> workers through a station in a day - manually finding the right name in
> a long dropdown list, especially with common surnames, risks
> misattributing a crate (and therefore wages) to the wrong person. A QR
> scan is unambiguous.

If the scanned code doesn't match any known worker, the app tells you so
and nothing is selected - print or reprint that worker's badge instead of
guessing.

### Selecting the block

Choose the block being picked from the **Block** dropdown.

### Capturing a crate's weight

Use the on-screen numeric keypad to enter the crate's weight in kg, then
tap **Save Crate**. The crate is saved immediately to the device - see
"Offline-first capture" below - and the running totals update:

- **Crate count** and **total weight** for the current, not-yet-dispatched
  load
- **Elapsed time**, color-coded green/yellow/red the same way described in
  [chapter 1](#1-overview--concepts), so the team can see for themselves
  when a load has been sitting long enough that it should go.

### Offline-first capture

Every crate is written to the device's local storage the instant you tap
Save Crate - **capture never waits on a network connection**. In the
background, the app checks for a connection every few seconds and quietly
syncs anything not yet sent to the server. You do not need to do anything
to make this happen, and a signal dropout mid-picking has no effect on
capture.

### Sending the load: "Send Picking Slip"

When a truck is ready to take the crates picked so far, tap **Send Picking
Slip**. You'll be asked for:

- **Crates going now** - defaults to every crate captured so far, but can
  be reduced if the truck can't take everything (see "Splitting a load"
  below)
- **Driver name** - required

Once sent, the load moves into "in transit" and will appear in the pack
house's queue (see [chapter 6](#6-pack-house-receiving)). The screen shows
a confirmation banner ("Picking Slip Sent - X crates - Y kg dispatched"),
and the load also appears under **Dispatched Lots Today** on the same
screen.

### Splitting a load

Sometimes a truck arrives before a picking round is finished. If you enter
a **Crates going now** value lower than the total captured, the app splits
the load: the crates going now are dispatched immediately under the
current slip number, and the remaining crates are automatically rolled
onto a **new** slip number, ready to combine with whatever gets picked
next.

> Splitting requires an active connection (the server needs to be the one
> deciding which crates go on which slip). If offline, the app shows a
> hint and asks you to either reconnect or just send everything on this
> load instead of splitting.

The pack house will see both resulting loads flagged as a **split load**
with a link back to the other part, so receiving staff know part of this
same picking session may arrive separately (see
[chapter 6](#6-pack-house-receiving)).

---

## 5. Worker ID Badges

Badges are what a field device scans to identify a worker (see
[chapter 4](#4-field-app---capturing-the-harvest)). An admin generates and
prints them from **Master Data → Workers**:

- **Print Badges (filtered)** - only the workers currently matching the
  Farm/Supplier filter
- **Print Badges (selected)** - only the workers with their row checkbox
  ticked
- **Print Badges (all)** - every active worker

Each badge shows the farm name, the worker's photo (if one's been
captured - see [chapter 9](#9-admin---master-data)), their name, their
employee number in large text, and a QR code encoding that employee
number.

Badges print **60 mm wide × 80 mm high**, portrait, to fit the ID card
holders the farm uses - nine to an A4 sheet, with dashed lines to cut
along. Cut on the line and a badge slides straight into a holder; no
trimming, and nothing to laminate unless you want to.

Most of the card is the QR code, at **50 mm square**. That's deliberate:
it's the part the badge gets used for every day, out in the sun at arm's
length, and a bigger code is quicker to catch and can be scanned from
further back. It's printed as line art rather than as a picture, so it
comes out as sharp as the printer can manage, and it carries the highest
error-correction level the format allows - roughly a third of the code
can be scratched, smudged or torn and it will still scan.

> **⚠️ Set the print dialog's scale to 100%.** Browsers default to
> "Fit to page" or "Shrink to fit" in some print dialogs, which quietly
> resizes everything - the badges then come out a few millimetres small
> and won't sit properly in the holder. The print page reminds you of this
> above the Print button. If a batch comes out the wrong size, this is
> almost always why.

---

## 6. Pack House Receiving

This is the screen at the gate where incoming loads are checked in.

### The in-transit queue

Every load currently on its way (dispatched from a field station, or
logged as an external delivery - see below) appears here, **oldest
first**, colored green/yellow/red by how long it's been in transit (the
same thresholds as [chapter 1](#1-overview--concepts), configurable in
[Settings](#12-admin---settings)). Each card shows the slip number, farm/
supplier, team, driver, crate count, and total kg.

If a load is part of a **split** (see [chapter 4](#4-field-app---capturing-the-harvest)),
its card shows a **"Split load - N related slip(s)"** flag. Opening the
load shows the related slip(s) with their own status (still in transit,
already received, or still being picked) - so receiving staff know to
expect (or ask about) the rest of that picking session.

### If the server can't be reached

The queue is kept on the device, so the screen still shows **the last
queue it loaded** rather than going blank - staff can see what was on its
way. The amber offline bar appears, and the timestamp at the top right
reads *"offline - last update 4 min ago"* so it's clear the list is a
snapshot and not live. It refreshes by itself once the connection is
back.

Receiving a load **does** need the connection: confirming a receipt while
offline shows *"Could not confirm - check connection and retry"* and
nothing is recorded. Unlike field capture, receipts are not queued for
later - check the load in again once the connection returns.

### Logging an external delivery

For fruit arriving from another farmer (not via this farm's own field
devices), tap **+ Log External Delivery** and fill in:

- **Supplier** - the external farm this fruit belongs to (must already
  exist in [Master Data → Suppliers](#9-admin---master-data))
- **Crates**
- **Total Kg**
- **Driver**
- **Notes**

This drops the load straight into the in-transit queue like any other
load, ready to be received the same way.

### Receiving a load

Tapping a card opens the **Receive** modal, with fields in this order:

1. **Expected crates** - read-only, what was dispatched
2. **Actual crates received** - enter what actually arrived
3. **Condition** - tick any that apply: **Good**, **Damaged**, **Sunburn**,
   **Wet**, **Other** (more than one can be ticked; all ticked values are
   recorded together)
4. **Notes**
5. **Received by** - required; the app remembers the last name entered
   here and prefills it next time, to save retyping for the same gate
   staff member

Confirming moves the load's status to **received** and records the exact
receiving time - this is what feeds the admin Dashboard/Reports' "Received"
figures.

---

## 7. Admin - Dashboard

The Dashboard is the admin app's landing screen - a farm-wide overview of
what's happening right now, all scoped to a shared filter bar at the top.

### Filter bar

- **Farm / Supplier** - narrow everything below to one farm/supplier, or
  leave on "All farms / suppliers"
- **Period start / Period end**, plus quick-fill buttons **Today**,
  **This Week**, **Season** (season = 1 Jan - 31 Dec of the harvest year
  set in [Settings](#12-admin---settings)) - or set custom dates directly.
  Whichever preset matches the dates currently selected is highlighted, so
  it's clear at a glance whether "Today" or a custom range is showing.
- Changing any filter - a preset button, a typed date, or the Farm/Supplier
  dropdown - refreshes everything below immediately, so there's no separate
  Refresh button to remember to press (dragging down from the top of the
  page still refreshes too, same as everywhere else in the app).

### If the server can't be reached

The amber offline bar appears and whatever was last loaded stays on
screen, rather than the figures resetting to zero and reading as a real
day of no harvest. Numbers refresh by themselves once the connection is
back.

A dropped connection does **not** sign the admin out - only the server
actually rejecting the session does that, in which case the sign-in
screen returns with *"Session expired - sign in again"*.

### KPI cards

Teams Active, Workers Active, Blocks Active (all counted as "had activity
in the selected period", not a static list), Total Kg, Total Crates, Avg
Kg/Lot, Avg Kg/Crate, and a breakdown of Harvesting / In Transit / Received
crates and kg.

### The five collapsible lists

Each starts collapsed, showing its running total in the header; tap to
expand for the detail rows.

1. **Harvesting** - loads still being picked (not yet dispatched),
   oldest-first, color-coded the same as pack house
2. **In Transit** - dispatched, not yet received, same ordering/coloring
3. **Received** - newest-received first; tap any row to open that load's
   crates - see
   [Correcting a captured crate](#correcting-a-captured-crate) below
4. **Workers** - per-worker crates/kg/amount-due/avg-kg-per-crate, sorted
   by kg picked (highest first), showing each worker's **Farm/Supplier**
5. **Blocks** - per-block crates/kg/avg-kg-per-crate/avg-kg-per-tree/
   avg-kg-per-hectare, sorted alphabetically by block name

### Correcting a captured crate

Tapping a row in the **Received** list opens that load's individual
crates - time, block, worker, weight, deduction, and net kg - each with
an **Edit** link. This is the one place to fix a mistake made at capture
time (the wrong worker scanned, a mistyped weight) after the fact; there's
no equivalent for a load still Harvesting or In Transit.

Editing a crate lets you change its **Worker**, **Weight (kg)**, and
**Deduction (kg)** - nothing else about it (block, device, and when it was
picked stay exactly as captured). Saving:

- Recalculates that lot's total crates/kg immediately, so the Received
  list, Dashboard KPIs, exports, and supplier billing all reflect the
  correction right away.
- Shows a warning if **wages were already calculated** for the affected
  worker(s) and period - correcting a crate doesn't retroactively update a
  wage sheet that was already run, so re-run **Calculate Wages** in
  [Payments](#10-admin---payments) for that period afterward. If the crate
  was reassigned to a different worker, both the old and new worker's
  periods are checked, since the correction changes both their totals.

> A field device is never able to undo a correction made this way - it
> doesn't re-send a crate it thinks already synced, and even if it retried
> after a lost connection, the correction always wins over the device's
> original capture.

---

## 8. Owner View - Analysis, Weather & Risk

> **These three tabs live on the Owner View only, not in Admin.** The
> Analysis, Weather and Risk tabs are opened from the Owner View link
> (`.../owner/?key=...`) issued in
> [Settings](#owner-view-read-only-dashboard-link). Admin keeps the
> day-to-day operational tabs - Dashboard, Master Data, Payments, Reports
> and Settings - and no longer carries these three.

The Analysis tab compares the **current season** against **2020-2025
historical records**, so a farmer can see at a glance whether the season is
running ahead or behind, which blocks are over- or under-performing their
own history, and how varieties and season timing are trending over the
years - not just what happened today (that's what the Dashboard is for).

Historical data was imported once from the farm's own pre-app record
spreadsheets, covering the six seasons 2020 through 2025. It's a fixed,
read-only reference - the app never writes to it - while "current season"
always means whatever's actually been captured this year through the Field
app, compared against the harvest year set in
[Settings](#12-admin---settings).

### Re-importing historical data on a server

> **The source workbooks are not shipped with the app.** They hold one
> farm's own harvest records, so they live in `data\imports\` - which is
> gitignored and per-farm - rather than in the repository. A new install
> has no workbooks and the two harvest import scripts simply skip, which is
> correct: a farm should never be importing another farm's history. To load
> your own, put the workbook in `data\imports\` (or pass its path as the
> first argument to the script) and re-run.


The historical import is a one-off script, not something an update
carries over - the source workbook and the script that reads it are both
in the repo, but running the script is a separate manual step against
*that server's own database*. This means a fresh install, or a server
whose database was reset, needs the import run on it directly - a pull
alone leaves the Analysis tab showing only the current season, with
**vs 5-Yr Average Pace** reading "-", until this is done.

From Command Prompt in `C:\Boord`, after confirming the update
(`update_server.bat` - see
[Getting the code onto the server](#getting-the-code-onto-the-server-github-recommended-or-usbzip))
has already brought in the latest code:
```bat
backend\.venv\Scripts\python.exe scripts\import_historical_harvest.py
```
Safe to re-run any time (e.g. after regenerating the source workbook) -
it replaces the whole historical table each time rather than appending.
No server restart needed; the next Analysis tab load picks it up.

There's a second, separate import for the even-older 1987-2019 seasons
(annual totals only, no daily breakdown - these don't feed the Analysis
tab, only the Historical Harvest Data report's Annual Totals sheet).
2012-2019 has a per-block breakdown; 1987-2009 only has a whole-farm
total per year, since those records predate today's block register and
use an incompatible numbering scheme. `update_server.bat` runs this
automatically on every update (see "Pulling future updates" above), so
it's rarely needed by hand - but for a fresh install, or to run it in
isolation:
```bat
backend\.venv\Scripts\python.exe scripts\import_historical_annual_yield.py
```
Safe to re-run any time; replaces its table wholesale.

### A note on the historical numbers

A handful of today's blocks (**8a/8b**, **10a/10b**, **17a/17b**, **19a/19b**)
didn't exist as separate blocks before the app - the original spreadsheets
recorded one combined daily total for each pair. Those combined totals have
been split between the two sub-blocks in proportion to their hectares (e.g.
a day's picking on the old "block 8" is split roughly 58%/42% between 8a and
8b, matching their relative size). This is a reasonable **estimate**, not
what was actually picked from each sub-block on that day - every figure
built from an estimated split carries a small info icon next to its block
name in the Per-Block Yield table so it's never mistaken for an exact
historical record.

### KPI cards

**Season to Date** (cumulative kg picked so far this season), **vs 5-Yr
Average Pace** (how that compares to where the 5-year historical average
stood at the same point in the season - green when ahead, red when behind),
**Current Season** (the harvest year being compared), and **Years of
History** available.

### Downloading a chart as PDF

Every chart card has a small <i class="fa-solid fa-file-pdf"></i> button in
its top-right corner - tap it to download that chart as a one-page PDF,
headed with the farm name and the chart's title, ready to print, email,
or drop into a report. Charts still under-populated (e.g. the current
season before picking starts) download just as they appear on screen.

### Season Pace

A line chart of cumulative kg picked, day by day through the season -
starting from **1 August**, since that's when picking actually begins each
year, not 1 January. Each historical year gets its own color (see the
legend below the chart), plus a dashed black **5-Yr Average** line and the
current season highlighted in navy. The current season always appears in
the legend, even before any picking has happened, so the color/position
doesn't shift once it starts. Shows at a glance whether this season is
tracking ahead of, behind, or in line with history - and where in the
season that gap opened up.

### Per-Block Yield

A bar chart plus table comparing this season's yield per block against that
block's own historical average, normalized by size so blocks of different
hectares/tree counts compare fairly. Switch between **Kg / Hectare** and
**Kg / Tree** with the dropdown - both the chart and the table (including
its column headers) switch to match. A dashed red **Farm Avg** line marks
this season's farm-wide average for the selected metric, so blocks above
or below the whole farm's own average stand out, not just their own
history; the table's **vs Average** column is colored green (above its own
historical average) or red (below) so under-performing blocks stand out
immediately.

### Harvest Volume by Block and Season

A bubble chart - one bubble per block per season, sized by kg harvested,
blocks ordered top-to-bottom by total volume across all seasons - for
seeing at a glance which blocks and which seasons produced the most. A
**Total Harvest** bar alongside each block's row shows its all-seasons
total, in the same ranked order as the bubbles.

### Yield per Tree/Hectare by Block and Season

A heatmap (light = low, dark red = high) of yield for every block against
every season, in block number order - switch between **Kg / Tree** and
**Kg / Hectare** with the dropdown (the title updates to match). Scan a
row to see a block's own history, or a column to see how a whole season
compared across blocks.

### Variety Performance Over Time

A stacked bar chart of **average kg per tree** per variety (Mauritius,
Early Delight, Hung Long, Third Month Red, Mix) - one bar per season, its
varieties stacked within it by color - for spotting which varieties are
trending up or down year over year. Use the dropdown to switch to a single
variety, which redraws the chart as a plain bar-per-season for just that
variety; it starts on **All Varieties**.

### Harvest Season Length (first pick → last pick)

One horizontal bar per season, spanning from that season's first day of
picking to its last, labelled with the span in days and how many of those
days actually had picking (e.g. "118d span, 46 pick days"). The current
season always gets a row - shown as "No picking yet" until the first crate
is captured, rather than the chart only gaining a row once picking starts.
Shows whether the season is starting, ending, or lasting longer than it
used to, and how much of the span was actually worked versus idle gaps.

### Monthly Harvest Volume

A heatmap of total kg picked by month (August through December) for every
season, latest season at the top, each cell also showing that month's
share of the season's total kg - making it easy to see which months carry
the bulk of the harvest and whether that's shifting year to year. A
**Total Kg (100%)** column after December shows each season's grand total,
shaded on its own blue scale (separate from the month cells' scale) so the
biggest season stands out at a glance. The current season always has a
row, shown blank until picking begins.

### Risk tab (Critical Season Risk Indicator)

A separate nav tab next to Analysis and Weather (`/api/risk/summary`,
served by `backend/routers/risk.py`) - a transparent 0-100 score of how
risky a season's weather looks for a poor harvest, built from the four
weather factors that best explained this farm's own harvest variation.
This is a one-off correlation study behind the score, not a generic
agronomy model and not recalculated live - the driver list only changes if
the study is redone.

The study was last re-run once the farm's full 1987-2025 harvest and
weather history was imported, replacing an earlier version fitted on 2020-
2025 alone. Two of those original four factors (winter chill hours, and
rain days during flowering) turned out to have no relationship with this
farm's yields at all once there was enough history to test them properly -
winter chill correlated at r = -0.02 across 37 seasons, essentially zero -
and were replaced. The four below were also checked against each other, so
the score doesn't count one underlying signal four times over.

**The four drivers**, each scored 0-25 and summed to the 0-100 score:
- **Fruit Development Air Dryness** (16 Sep - 31 Oct) - mean dew point.
  The strongest single signal in the whole record: dry air while fruit is
  sizing means the crop loses water faster than it can take it up.
- **Fruit Development Warmth** (16 Sep - 15 Nov) - mean daily maximum
  temperature. Persistently warm afternoons line up with smaller crops -
  a steadier measure than counting rare extreme days.
- **Flowering-Period Sunshine** (1 Aug - 15 Sep) - total hours of sunshine.
  Bright flowering weather produced the bigger crops; dull, overcast spells
  during flowering mean poorer pollination and fruit set.
- **Fruit-Sizing Rainfall** (1 Oct - 30 Nov) - total mm. Rain while the
  fruit fills out fed the bigger harvests; dry Octobers and Novembers line
  up with the smaller ones.

Each driver is scaled between the best and worst value seen across the
reference seasons (2012 onward - when the replanted orchard came into
bearing), so 25/25 on one factor means "as bad as the worst season on
file for that factor," not an absolute agronomic threshold. A calendar
window that hasn't closed yet for the season being viewed is left out of
the sum entirely (never assumed to be zero risk) - which is why the
current season shows a partial **"score so far"**, with a count of how
many of the four factors are known, until its last window closes.

Use the **Season** dropdown to inspect any reference season or the current
one - each shows its own score, band (Low/Moderate/Elevated/High), and the
per-driver breakdown with that season's actual value against the reference
range. Below that, **Risk Score by Season** and **Actual Harvest by
Season** bar charts sit stacked so a higher score can be checked by eye
against that year's actual outcome - both downloadable as PDF the same way
as Analysis tab charts (see [above](#downloading-a-chart-as-pdf)).

#### Harvest Forecast

At the top of the Risk tab, above the season inspector, the **Harvest
Forecast** card (`/api/risk/forecast`) turns the same four drivers into
three current-season kg predictions - **Favorable**, **Expected**, and
**Unfavorable** - rather than one falsely-precise number, since most of a
season's outcome still depends on weather that hasn't happened yet.

For whichever part of a driver's calendar window is still ahead, the
forecast blends a real short-range weather forecast (up to 15 days out,
via Open-Meteo) with a historical-scenario assumption for whatever's
beyond that - each driver's own best/average/worst across the reference
seasons (2012 onward, the same range the score itself is scaled against)
for the Favorable/Expected/Unfavorable scenario respectively. The
**Basis** column in the table under the three scenario cards shows exactly
how many days of each driver came from actual data, a real forecast, or a
historical assumption (e.g. "14d actual + 16d forecast + 16d assumed"). A
driver whose real data has a genuine gap (a down sensor, or the forecast
service itself unreachable) falls back to the historical range alone for
its whole window rather than silently treating missing data as "no risk" -
flagged in that row.

Each scenario's projected score converts to a kg figure via a straight
line fitted through the historical (risk score, harvest total) pairs from
2016 onward - the first season the replanted blocks bore any fruit, since
before that only block 7 was cropping and earlier totals reflect a young
orchard rather than its weather. The line's own strength (r-value) and
sample size are disclosed in the methodology panel below. The Favorable
and Unfavorable ends are held to the best and worst harvests actually on
record rather than running the line out past them, so those two read as
"about as good or bad as it has ever gone" rather than exact figures -
each combines every driver's own best or worst *year*, not one real season
that went that way on everything at once. If Open-Meteo's forecast is
unreachable when the tab loads, the card still renders - with a note that
every driver has fallen back to the historical range alone for its whole
remaining window.

The collapsible **"How this score is calculated"** panel at the bottom
spells out the score's method and, deliberately, its limits: weather
explains roughly a quarter to a half of this farm's swing from season to
season (real signal, but most of what makes a season good or bad is
something the score can't see), and many factor-and-window combinations
were tested to arrive at these four, so they were kept only because they
held up across separate stretches of the farm's history. The panel also
notes something growers often assume but this farm's record does not
support: there is **no alternate-bearing pattern** here - across 35
back-to-back season pairs, last season's crop predicts this one at
r = 0.07, statistically nothing. (An earlier version of this manual and
of the app claimed the opposite; the longer record disproved it.) A second
list in the same panel covers the Harvest Forecast card specifically: the
regression's r-value and sample size, the forecast/historical-range
blending method, the clamping of the two extreme scenarios, and that the
whole card is a description of what the historical pattern implies about
this season's weather, not a guarantee of what will be harvested.

---

## 9. Admin - Master Data

Master Data has five subtabs for the farm's reference data.

### Workers

Employee number, name, ID number, bank/account, WhatsApp number, which
farm/supplier they belong to, a photo (captured via the device's camera
right in the edit form, or uploaded), and active/inactive. Supports
CSV/xlsx **Export** and **Import** for bulk edits, and the
[Print Badges](#5-worker-id-badges) buttons.

### Teams

ID (e.g. "A"), name (e.g. "Span A"), induna, active.

### Blocks

The 21 preseeded blocks (see [chapter 2](#2-initial-server-setup)), each
with name, variety, tree count, hectares, active already filled in.
Supports CSV/xlsx export/import for bulk edits rather than one block at a
time - useful whenever the farm's actual block layout changes (e.g. a
block gets re-divided by variety).

**Import** always adds new block IDs and updates existing ones from the
file, but never removes anything on its own - a block ID that used to
exist but isn't in the file just stays as it was. Tick **"Replace all
(deactivate blocks not in the file)"** before importing when the file is
a complete replacement for the block list (e.g. after re-dividing a block
into smaller ones by variety) - any currently-active block whose ID isn't
in the file gets deactivated, not deleted, so historical harvest records
that reference it are unaffected.

### Devices

ID, role (field/packhouse/admin), station name, team, induna, data
capturer, active. This is where new devices must be added before they can
complete [Device Setup](#3-device-setup).

### Suppliers

Every farm whose fruit passes through this pack house, including the
farm's own row (marked "(Own Farm)"). Contact details and a packing rate
(per kg, or per crate if per-kg is left at 0) used for the **Facility
Billing** panel on this same subtab - pick a supplier and date range,
tap **Calculate**, and see how much facility-use fee that supplier owes
for fruit actually received (not still in transit) in that period.

---

## 10. Admin - Payments

Calculates and exports wages for a filtered period.

- Same filter bar convention as the Dashboard (Farm/Supplier + Today/This
  Week/Season/custom dates)
- **Calculate Wages** builds the table below, **grouped by farm/supplier**
  (own farm first, then alphabetically), each group showing a summary row
  (worker count, total kg, total wages) followed by that group's workers
  (name, kg, rate, amount due)
- **Export Wage Sheet** downloads the same grouped breakdown as an `.xlsx`
  file

> This screen only calculates what's **owed** - marking wages as paid, or
> tracking payment status, is deliberately not handled inside this app;
> that's managed in whatever external system/process the farm already
> uses for actually paying workers.

---

## 11. Admin - Reports

Downloadable `.xlsx` reports, all sharing the same filter bar as Payments/
Dashboard (Farm/Supplier + date range):

| Report | Contents |
|---|---|
| Daily Harvest Summary | Crates/kg by block and team for one day. Always uses just the period start date, even if a wider range is picked - the reports grid flags this one "Daily report only" when that happens. |
| Lot & Receiving Report | Every lot dispatched in the range, with receiving detail once received |
| Plukstrokies / Picking Notes | One row per dispatched lot - slip number, date/time, block(s), crates sent vs. received, driver, supplier, condition, notes, received by, weather - sorted by date then team, matching the paper picking-slip notes an induna keeps |
| Span Pluklys / Team Picking List | One row per team per day, matching the paper "Inligting van die Dag" slip - data capturer, induna, worker count and total deductions, plus repeating columns for every block picked that day (name, kg, deductions) and every lot dispatched that day (crates, time, slip number) |
| Daaglikse Oesdata / Daily Harvest Data | Kg by date (rows) x block (columns) over a range, with per-day totals (kg, workers, avg/worker, crates, avg/crate) and per-block totals (kg, avg/tree, avg/hectare) |
| Harvesting List | Loads still being picked, matching the Dashboard's Harvesting list |
| In Transit List | Dispatched, not-yet-received loads |
| Pakhuis Ontvangstes / Pack House Receivables | Received loads, matching the pack house's paper "Packhouse Receipt Lists" slip - slip number, date/time split out, the receiving block, farm/supplier, team, driver, crates, kg, and rejected (waste) kg |
| Worker Harvest Report | Per-worker crates/kg/amount-due/avg-kg-per-crate |
| Lietsjie Lone / Litchi Wages | One row per worker, with the crates that worker harvested broken out per day - one column per date worked, plus a total - so a whole pay period reads off a single row |
| Block Harvest Report | Per-block crates/kg/avg-kg-per-crate/avg-kg-per-tree |
| Historical Harvest Data | Every harvest figure the farm has on file, 1987 through the current season, in one workbook. **Season Summary** (one row per season: total, year-on-year change, and how that season was recorded), **Block by Year** (every block-level season total in one grid, whichever way the year was recorded), **Annual Totals** (1987-2019 season-only figures - per block from 2012, whole-farm only before that), then a **block x date sheet per season** from 2020 on, where real daily records exist - the farm's own "Daaglikse Oesdata" workbook, kept live. Ignores the date range filter above (there's only ever one of these); download it any time to get everything to date in one file. |

Every generated report is also saved on the server itself, in
`data\reports\` (same filename as the download, e.g.
`Daily_Harvest_2026-08-05.xlsx`) - not just wherever the browser that
downloaded it happened to save it. Regenerating the same report again
(same type and date range) overwrites that file rather than piling up
duplicates. This folder isn't covered by the automatic nightly backup
(see [Data Backup](#data-backup) below) - back it up the same way you'd
back up anything else in `data\`.

---

## 12. Admin - Settings

### Data Backup

- **Backup Now** - immediately zips the farm data and worker photos into a
  downloadable archive, and adds it to the list below (created date, size,
  a download link per entry). This always creates a backup, whether
  anything changed or not
- The same backup also runs **automatically every day at 02:00**, keeping
  only the **14 most recent** backups (older ones are deleted
  automatically)
- **The nightly run only saves a backup if the farm data actually changed
  that day.** On a quiet day - no crates, no receiving, no payments, no
  edits to workers or master data or settings - nothing is written and no
  new row appears in the list. That is deliberate: it used to write a full
  copy every single night, including right through the off-season, which
  is what made the backups take up so much space. If a full **30 days**
  pass with no changes at all, one backup is taken anyway as a safety net.
- Weather history is **not** included in the archive. It is by far the
  biggest thing in the database (bigger than every harvest record put
  together) and it can be downloaded again from the weather service, so
  leaving it out takes a backup from roughly 9 MB down to under 100 KB.
  The trade-off shows up when restoring - see the note in
  [Restoring from a backup](#restoring-from-a-backup) below.

> **A gap in the backup list is not a problem in itself.** If the newest
> backup is a few days or a few weeks old, the ordinary explanation is
> that nothing has changed since then - there is nothing new to save.
> Press **Backup Now** any time you want a fresh copy regardless.

> **⚠️ The automatic backup only runs if the server happens to be running
> at 02:00.** If the machine is switched off overnight, that night's backup
> simply doesn't happen - there's no separate always-on scheduler behind
> it. Keep the server running continuously during harvest season (see
> [chapter 2](#2-initial-server-setup)).

> **Recommended: copy backups off the server regularly.** The 14-backup
> retention only protects against recent mistakes (e.g. accidentally
> deleting a worker) - it does **not** protect against the server's disk
> failing entirely, since all 14 copies live on that same machine. Every
> so often, download the latest backup from this list and copy it
> somewhere off the machine - a cloud drive such as Google Drive, Dropbox,
> or similar is a simple, effective option. That off-machine copy is the
> only real safeguard against losing everything if the hardware fails.

### Restoring from a backup

There's currently **no restore button in the app** - Settings only lets
you create and download backups, not load one back in. Restoring means
manually swapping files on the server, and it's a full replacement, not a
merge: **everything captured after the backup's timestamp is lost**
(crates, payments, worker/master-data edits, anything) once you restore
it - there's no way to selectively bring back just part of it.

**Before restoring, protect today's data in case you need it back:**
Copy the *current* `data\boord.db` and `data\photos\` folder
somewhere safe first (e.g. rename them or copy them out of `data\`). If
the restore turns out to be the wrong call, you'll still have what was
there before you overwrote it.

**Steps:**
1. Get the backup zip you want to restore - either downloaded from
   Settings → Data Backup, or directly from `data\backups\` on the server
   (filenames are timestamped, e.g. `backup_20260804_020000.zip`).
2. Stop the server (see
   [Stopping, starting, and restarting the server](#stopping-starting-and-restarting-the-server-task-scheduler)).
3. Unzip the backup - it contains `boord.db` and a `photos\`
   folder.
4. Copy those into `data\`, replacing the current
   `data\boord.db` and `data\photos\`.
5. Start the server again.
6. **Rebuild the weather history.** Backups don't carry it, so straight
   after a restore the Weather and Risk tabs will be empty, and they will
   *not* fill themselves back in - the app only ever tops up the last few
   hours, it never re-fetches years. Everything else in the app works
   normally in the meantime. To bring it back, run these two scripts once
   each from the project folder (they take a while - they're downloading
   years of hourly readings):

   ```bat
   backend\.venv\Scripts\python.exe scripts\import_historical_weather.py
   backend\.venv\Scripts\python.exe scripts\import_historical_weather_archive.py
   ```

   Running `update_server.bat` does both of these for you as part of its
   normal refresh (see [chapter 2](#2-initial-server-setup)), so if you'd
   rather not type them, run that instead.
   Harvest data, workers, payments and settings are all restored by step 4
   and are not affected by this.

### Farm settings

Farm name, location description, current harvest season (year - drives
what "Season" means throughout the app), the green→yellow and yellow→red
urgency thresholds in minutes (used everywhere the traffic-light coloring
appears - [chapter 1](#1-overview--concepts)), and GPS coordinates
(latitude/longitude, or pick a location on the map) - setting these
enables automatic weather capture on every dispatched load.

### Harvest rate

The per-kg wage rate used by [Payments](#10-admin---payments).

### Change admin password

Change the admin login password - **do this immediately after first setup**
([chapter 2](#2-initial-server-setup)).

### Owner View (read-only dashboard link)

A link-only alternative to full Admin access, for an owner or other
interested party who just wants to check on progress without a
username/password and without being able to change anything.

The link (`.../owner/?key=...`) opens a four-tab, stripped-down version of
the Admin app: a **Dashboard** tab with the same KPI cards, Harvesting/In
Transit/Received lists, a per-worker Workers breakdown, and Blocks
breakdown, filtered the same way - but with **no wage figures** (the
Workers list shows crates/kg/avg-kg-per-crate, same as full Admin, just
without amount due) - plus the **Analysis**, **Weather** and **Risk** tabs
(see [chapter 8](#8-owner-view---analysis-weather--risk)), PDF downloads
included. Those three season-level tabs are here and nowhere else - Admin
does not have them. None of Admin's other tabs (Master Data, Payments,
Reports, Settings) are available.

- **Copy** the link from Settings and send it directly to whoever it's
  for - opening it needs no login at all, just the link itself.
- Anyone with the link can view it for as long as it's valid, so only
  share it with the people it's meant for - don't post it anywhere
  public.
- **Regenerate Link** immediately invalidates the old link and issues a
  new one - use this if a link was shared more widely than intended, or
  someone who had it no longer should.

> **"This link isn't valid" means the link, not the connection.** The
> Owner View only shows that message when the server actively rejects the
> key - i.e. the link really was regenerated, mistyped, or truncated when
> it was shared. If the owner simply can't reach the server (Tailscale
> off, no signal), they get the amber offline bar instead, with the last
> figures still on screen. So if they report the invalid-link message,
> re-send them the current link from Settings; it isn't a connection
> problem.

> **Offline, the page shows its own last-seen figures, not just whatever
> happened to be on screen already.** It keeps a small local cache (a
> handful of recently-viewed date-range/farm combinations) on that
> device, so reopening the page or switching back to a preset like Today
> while offline still shows real numbers, labeled with how old they are
> (e.g. *"Offline - showing figures from 12 min ago"*) instead of going
> blank. A combination it's never loaded before shows *"No saved figures
> for this period"* rather than stale numbers from a different range.

This link only works the same way any other screen does - reachable on
the farm's own Wi-Fi, or over Tailscale if the recipient needs it from
outside the farm (see
[Connecting external users with Tailscale](#connecting-external-users-with-tailscale-only-if-needed)).
For someone who needs the *full* Admin screen remotely (not just the
Dashboard), Tailscale plus the normal admin login is still the way to
go - this link is deliberately just the read-only Dashboard.

> **If the owner will check this from off the farm** (the usual case),
> two things need to be true, not just one:
> 1. **They need Tailscale installed and connected** on whatever device
>    they'll open the link from (see
>    [Connecting external users with Tailscale](#connecting-external-users-with-tailscale-only-if-needed)
>    to give them access) - the token alone only proves they're allowed
>    to view it, it doesn't make the server reachable from outside the
>    farm's own network.
> 2. **The link itself has to use the Tailscale address, not the LAN
>    one.** The Copy button builds the link from whatever address *you*
>    (the admin) happen to be viewing this Settings page from at that
>    moment - if you're on the farm's own Wi-Fi when you copy it, the
>    link will have the LAN address baked in, which an off-site owner
>    can never reach, no matter how correct their token is. Open Admin
>    via the Tailscale address first (`https://<server-name>.<tailnet-name>.ts.net/admin/`),
>    *then* copy the Owner View link from there.

### Header weather

The admin screen's header shows a live current-weather readout (temperature,
condition, humidity) next to the farm name/clock - a quick at-a-glance
check of conditions without leaving the app.

---

## 13. Troubleshooting / FAQ

**"Unknown device id" on a device's first setup**
The device ID hasn't been created yet. An admin must add it in
[Master Data → Devices](#9-admin---master-data) first - see
[chapter 3](#3-device-setup).

**"Camera unavailable" when tapping Scan Worker QR**
The device is reaching the server over plain `http://` (a LAN IP) rather
than HTTPS - Android/iOS block camera access on any page that isn't a
secure origin, regardless of camera permissions. See
[Enabling the QR camera scanner](#enabling-the-qr-camera-scanner-https-via-tailscale---required-for-field-devices)
to set up Tailscale HTTPS, which fixes this for every device at once.

**A device shows an older version number than expected**
Its offline app cache hasn't refreshed yet - see
[Confirming devices picked up an update](#confirming-devices-picked-up-an-update-version-numbers).
Fully close the app (not just background it) and reopen it; if that
doesn't clear it, clear the site's data in the browser and reopen.

> Each screen keeps its **own** copy of the app, so updating the field
> device does nothing for the pack house device and vice versa. After a
> deploy, every device needs one open while it can still reach the
> server. Check the version badge on each rather than assuming one
> device's update covers the rest.

**A device newly added in Master Data doesn't appear on the setup screen**
The setup screen reads the list from the server each time it loads, so a
device added in [Master Data → Devices](#9-admin---master-data) should
appear as soon as the page is reloaded. If it doesn't: check the device
is marked **active** (inactive devices are deliberately not offered), and
that the setup screen can actually reach the server - if it can't, it
falls back to a type-in box and shows no list at all. Typing the ID by
hand always works as long as the ID exists in Master Data.

**"QR code doesn't match a known worker" when scanning a badge**
Either the worker doesn't exist in [Master Data → Workers](#9-admin---master-data)
yet, or their badge is stale/misprinted. Reprint the badge from Workers
after confirming the worker record exists (see
[chapter 5](#5-worker-id-badges)).

**Field app shows "Online - sync failed, retrying"**
The device reached the server but the server rejected the last sync
attempt. Nothing is lost - captured crates stay queued on the device and
the app keeps retrying automatically every few seconds. (If the server
can't be reached at all, the pill reads **Offline** instead, in red.)

**A screen says "Offline" but the device clearly has Wi-Fi**
"Offline" means *this app can't reach the farm server*, which is not the
same as having no signal - the far more common case in the orchard is a
device still associated with an access point that has no route back to
the server PC. Check the server PC is on and awake, and (for anyone
off-site) that **Tailscale** shows Connected. Field capture is unaffected
either way: crates keep saving to the device and go up automatically when
the connection returns.

**Every screen says "Offline", including on the server PC itself**
When even the server PC's own browser shows the amber bar, work down this
list in order - each step rules out a whole layer, and skipping to the
end wastes the most time:

1. **Is the server actually running - exactly once?** Open Task Manager
   (Ctrl+Shift+Esc) → Details and look for `python.exe`. A Windows restart
   (power cut, update) leaves the server down unless the Task Scheduler
   auto-start in [chapter 2](#2-initial-server-setup) Step 12 was set up -
   that step is optional, and without it nothing brings the server back by
   itself. Start it with `start_server.bat`.

   Seeing **more than one** `python.exe` group matters just as much as
   seeing none: a second server started while the first was still running
   binds a different port, so `tailscale serve` may be forwarding to one
   instance while you are reading the other, and the two disagree about
   what has been saved. End every `python.exe` task, confirm none remain,
   then start exactly one.
2. **Try `http://localhost:8000/admin/` on the server PC.** This bypasses
   the network entirely. If this works but the `.ts.net` address doesn't,
   the app and its data are fine and the problem is purely Tailscale -
   continue to step 3. If this *also* fails, go back to step 1.
3. **Check Tailscale.** Its tray icon should read Connected. If the
   browser shows `ERR_NAME_NOT_RESOLVED` on the `.ts.net` address, the
   name isn't resolving at all: those names exist only in Tailscale's own
   MagicDNS, never in ordinary public DNS, which is why an unrelated site
   still loads fine while the farm's own address doesn't. Reconnect
   Tailscale, run `ipconfig /flushdns`, and confirm **MagicDNS** is still
   enabled at `login.tailscale.com/admin/dns`.

> **A working internet connection tells you nothing here.** The farm
> server is reached over Tailscale, not the public internet, so an
> ordinary web page loading normally is not evidence the app should be
> reachable - the two use different DNS and different routes. Test the
> app's own address, not a search engine.

> The Risk and Weather tabs additionally call out to the weather service
> while loading. If those two are offline but Dashboard and Analysis work,
> the server is fine and it's that outbound call failing - both tabs fall
> back to stored data on their own, so this shows up as slowness or stale
> figures rather than an empty screen.

**"Reconnect to send a partial load, or dispatch everything now" when
sending a picking slip**
You tried to split a load (send fewer crates than captured) while offline.
Splitting needs a connection because the server decides the split; either
wait for a connection or send the full load instead.

**Analysis tab only shows the current season, no historical lines**
The server's database hasn't had the historical import run against it yet
- an update brings in the app code and the source workbook, but
loading that data into the database is a separate manual step per server.
See [Re-importing historical data on a server](#re-importing-historical-data-on-a-server)
in chapter 8.

**Forgotten admin password**
There's currently no self-service "forgot password" flow in the app - a
new password can only be set from inside Settings while already logged in.
If the admin password is lost entirely, restoring access requires direct
access to the server's database rather than anything available from the
app's screens.

---

## Annexe A: Data Field Reference

Field-by-field reference for every stored record type, with a realistic
example value and any limitation worth knowing. Types/defaults/foreign
keys below match `backend/models.py` exactly.

### Worker

| Field | Type | Example | Notes / Limitations |
|---|---|---|---|
| `id` | text | `"001"` | Primary key - the employee number. Must be unique and must match the number printed on the worker's badge. |
| `first_name` | text | `"Sipho"` | |
| `last_name` | text | `"Dlamini"` | |
| `id_number` | text | `"8501015800083"` | Free text, not validated. |
| `bank` | text | `"Nedbank"` | |
| `account` | text | `"9963334018"` | |
| `whatsapp_number` | text | `"+27821234567"` | Optional, not currently used to send anything automatically. |
| `supplier_id` | number (optional) | `2` | Which farm this worker belongs to. Left blank for the farm's own workers. |
| `photo_filename` | text | `"001.jpg"` | Set automatically when a photo is captured/uploaded - not hand-edited. |
| `active` | true/false | `true` | Inactive workers are hidden from most pickers/dropdowns but kept for history. |

### Team

| Field | Type | Example | Notes / Limitations |
|---|---|---|---|
| `id` | text | `"A"` | Primary key. Short code, e.g. a single letter. |
| `name` | text | `"Span A"` | |
| `induna` | text | `"Samuel Mthembu"` | |
| `active` | true/false | `true` | |

### Block

| Field | Type | Example | Notes / Limitations |
|---|---|---|---|
| `id` | text | `"8a"` | Primary key - a real farm block label. One of the 21 preseeded labels (see chapter 2); not free-form. |
| `name` | text | `"Block 8a"` | |
| `variety` | text | `"Mauritius"` | |
| `trees` | number | `450` | Whole number of trees on the block. |
| `hectares` | decimal | `2.3` | |
| `active` | true/false | `true` | |

### Device

| Field | Type | Example | Notes / Limitations |
|---|---|---|---|
| `id` | text | `"device-01"` | Primary key - must be entered exactly on the physical device's setup screen (chapter 3). |
| `role` | enum | `"field"` | Must be exactly one of `field`, `packhouse`, `admin`. |
| `station` | text | `"Field Station 1"` | |
| `team_id` | text (optional) | `"A"` | Which team this field device belongs to. Not applicable to pack house/admin devices. |
| `induna` | text | `"Samuel Mthembu"` | |
| `data_capturer` | text | `""` | Free text, optional. |
| `active` | true/false | `true` | |
| `last_seen` | timestamp | (auto) | Updated automatically every time the device checks in - not editable. |

### Supplier

| Field | Type | Example | Notes / Limitations |
|---|---|---|---|
| `id` | number | `1` | Primary key, auto-assigned. |
| `name` | text | `"Jansen Boerdery"` | |
| `contact_name` / `contact_phone` / `contact_email` | text | `"Piet Jansen"` / `"082-555-1234"` / — | All optional. |
| `is_own_farm` | true/false | `false` | Exactly one supplier row in the whole system should ever have this set to `true` - that row represents the farm's own fruit. |
| `packing_rate_per_kg` | decimal | `1.50` | If greater than 0, this rate is used for Facility Billing; otherwise the per-crate rate below is used. |
| `packing_rate_per_crate` | decimal | `25.00` | Only used when the per-kg rate is 0. |
| `active` | true/false | `true` | |

### HarvestRecord (one crate)

| Field | Type | Example | Notes / Limitations |
|---|---|---|---|
| `uuid` | text | (auto-generated) | Primary key, generated on the capturing device - lets a retry after a dropped connection safely resubmit the same crate without duplicating it. |
| `timestamp` | timestamp | `2026-07-08T09:14:00Z` | When the crate was captured. |
| `worker_id` | text (optional) | `"001"` | Set via the QR badge scan (chapter 4) - required in practice even though nullable in the schema. |
| `block_id` | text (optional) | `"8a"` | Required in practice. |
| `weight_kg` | decimal | `12.4` | A single crate's weight - typically in the 8-20kg range for litchi crates. |
| `deduction_kg` | decimal | `0.0` | "Aftrekkings" (waste/reject deduction), subtracted from the crate's weight everywhere a total is calculated. Always `0` at capture time - there's no field-app screen for it - but an admin can set a non-zero value when [correcting a crate](#correcting-a-captured-crate) after it's received. |
| `lot_id` | number (optional) | `21` | Always set at capture time, pointing at a placeholder load that becomes a real dispatch once "Send Picking Slip" is used. |
| `weather_temp` / `weather_humidity` / `weather_condition` | decimal / decimal / text | `24.1` / `55` / `"Clear"` | Conditions at the farm the moment the crate synced to the server, only if GPS coordinates are set in Settings - same source as the per-lot weather on Lot below, but stamped once per crate rather than once per dispatch. |
| `edited_at` / `edited_by` | timestamp / text (both optional) | — / `"admin"` | Set only when an admin [corrects this crate](#correcting-a-captured-crate) after capture - never touched by the field app. Once set, a re-sync of the capturing device's original copy of this crate can no longer overwrite the correction. |

### Lot (a picking slip / load)

| Field | Type | Example | Notes / Limitations |
|---|---|---|---|
| `slip_number` | text | `"device-01-20260708091400"` | Unique. Auto-generated from the device ID and a timestamp; a split creates a second, related slip number. |
| `timestamp` | timestamp | `2026-07-08T09:14:00Z` | Dispatch time - the basis for the urgency color-coding. |
| `supplier_id` | number | `1` | Which farm this load's fruit belongs to. |
| `driver` | text | `"Sello"` | Required when dispatching. |
| `total_crates` / `total_kg` | number / decimal | `18` / `238.5` | |
| `status` | enum | `"in_transit"` | One of `created` (still being picked), `in_transit` (dispatched), `received`, `processing_complete`. |
| `received_at` | timestamp (optional) | — | Set the moment pack house staff confirm receipt. |
| `weather_temp` / `weather_humidity` / `weather_condition` | decimal / decimal / text | `24.1` / `55` / `"Clear"` | Captured automatically at dispatch time (or when an external delivery is logged), only if GPS coordinates are set in Settings. |
| `split_from_slip_number` | text (optional) | — | Only set on the "leftover" slip created by a split (chapter 4) - points back at the original slip it was carved out of. |

### ReceivingRecord

| Field | Type | Example | Notes / Limitations |
|---|---|---|---|
| `lot_id` | number | `21` | Which load this receiving entry belongs to. |
| `expected_crates` / `actual_crates` | number | `18` / `17` | |
| `discrepancy` | number | `-1` | Calculated automatically as `actual - expected` - not entered directly. |
| `condition` | text | `"Good, Sunburn"` | Free text, but in practice a comma-joined list of whichever of Good/Damaged/Sunburn/Wet/Other were ticked. |
| `received_by` | text | `"Elsa"` | Required - the app remembers and prefills the last value entered on this device. |

### Payment

| Field | Type | Example | Notes / Limitations |
|---|---|---|---|
| `worker_id` | text | `"001"` | |
| `period_start` / `period_end` | date | `2026-07-01` / `2026-07-31` | |
| `total_kg` | decimal | `318.6` | Sum of that worker's net crate weights in the period. |
| `rate_applied` | decimal | `3.00` | The per-kg rate in effect when calculated. |
| `amount_due` | decimal | `955.80` | Calculated - not directly editable. There is intentionally no "paid" flag or status field on this record (chapter 10); payment status is tracked outside this app. |

### RateSetting

| Field | Type | Example | Notes / Limitations |
|---|---|---|---|
| `effective_date` | date | `2026-07-01` | |
| `rate_type` | enum | `"per_kg"` | `per_kg` or `per_crate_tier`. |
| `default_rate_per_kg` | decimal | `3.00` | Used when `rate_type` is `per_kg`. |
| `tier_rates_json` | text (JSON) | `{"1": 2.5, "1.5": 3.5, "2": 4.5}` | Only relevant if using `per_crate_tier` - maps a crate-size tier to its own rate. |

### SystemSetting

| Field | Type | Example | Notes / Limitations |
|---|---|---|---|
| `farm_name` | text | `"Boord Boerdery"` | Blank on a fresh install - set it in Settings. |
| `farm_location` | text | `"Bekfontein, Mpumalanga"` (example) | Free text description, not used for weather (see `gps_lat`/`gps_lon`). |
| `current_harvest_year` | number | `2026` | Drives what "Season" means throughout the app (chapters 7, 9, 10). |
| `green_to_yellow_minutes` / `yellow_to_red_minutes` | number | `90` / `150` | The urgency color thresholds referenced throughout chapters 1, 4, 6, 7. |
| `gps_lat` / `gps_lon` | decimal (optional) | `-25.572747` / `31.606722` | Setting both enables automatic weather capture on dispatch. |

### AdminUser

| Field | Type | Example | Notes / Limitations |
|---|---|---|---|
| `username` | text | `"admin"` | |
| `password_hash` | text | (hashed) | Never shown or exported anywhere - only a hash is stored. |
