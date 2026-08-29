# Admin — Training Guide

For farm office staff managing workers, wages, reports, and settings.

---

## Signing in

Open the Admin app, enter your username and password, and tap **Sign
in**. On a brand-new setup the username is `admin` and the password is
the random one the installer printed when it set the server up — there
is no shared default. Boord then asks you to choose your own password
before it will open; nothing else works until you have. After that you
can change it whenever you like under **Settings → Change admin
password**.

Once signed in, the top bar shows the farm name, today's date/time, and
current weather. Five tabs run across the top: **Dashboard**,
**Master Data**, **Payments**, **Reports**, **Settings**.

---

## The first time: the setup wizard

On a brand-new server you will not see those tabs straight away. Boord
shows a nine-step wizard instead, because a fresh install genuinely does
not know whose farm it is — it has no name, no location, no blocks and no
wage rate, and it will not invent any of them.

Work through it: farm name and supplier name, GPS (there is a map to click
on), the rate you pay per kilogram, the transit thresholds, then your
blocks, your people, your station names, and any past seasons you have
records for. The first three are required. The rest have a **Skip for now**
button, and the last screen tells you what is still outstanding.

If you have to stop, just close it. Next time you open the Admin app it
picks up at the first step you have not done. When you press **Open
Boord** on the last screen the wizard is finished with — everything on it
lives under **Settings** and **Master Data** from then on.

You will not see this on a farm that is already running Boord.

---

## Dashboard — your daily overview

This is where you check how the harvest is moving.

1. Set the **Farm/Supplier** filter (or leave it on "All") and a
   **Period start/end** date range — or just tap **Today**, **This
   Week**, or **Season** for a quick preset (whichever one matches the
   current dates stays highlighted). Everything below refreshes the
   moment any filter changes — there's no separate Refresh button.
2. The KPI cards along the top summarize the period: teams/workers/
   blocks active, total kg and crates, averages, and a breakdown of
   Harvesting / In Transit / Received.
3. Below that, five expandable lists give the detail behind those
   numbers:
   - **Harvesting** — loads still being picked in the field.
   - **In Transit** — dispatched, not yet received at the pack house.
   - **Received** — already checked in; tap a row to open its crates.
   - **Workers** — per-worker totals for the period.
   - **Blocks** — per-block totals for the period, including yield per
     hectare.

Tap any list's header to expand or collapse it.

**Harvesting and In Transit use the same green/yellow/red color coding
as the Pack House screen** — red means a load has been waiting too long
and needs attention.

### Correcting a captured crate

Tapping a row in the **Received** list opens that load's crates —
time, block, worker, weight, deduction, and net kg — each with an
**Edit** link. Use this to fix a mistake made at capture time (wrong
worker scanned, mistyped weight): you can change the **Worker**,
**Weight (kg)**, and **Deduction (kg)** on that crate, and the lot's
totals recalculate immediately everywhere they're shown.

If wages were already calculated for the affected period, a warning
banner says so — re-run **Calculate Wages** in Payments for that period
afterward, since a correction doesn't rewrite a wage sheet that's
already been run.

---

## Season analysis — in the Owner app, not here

This season against 2020–2025 history, the farm's weather record back to
1987, and the season risk score and harvest forecast are not part of the
Admin app. They live in the separate **Boord Owner** app, which has its
own link — ask whoever set it up.

Admin is for the day-to-day operational work: the Dashboard, Master
Data, Payments, Reports and Settings.

For the record on this server, none of that analysis is lost by not
being here — the figures are all still recorded, and the **Historical
Harvest Data** report under **Reports** gives you every harvest figure
the farm has on file, 1987 to today, in one workbook.

---

## Master Data — the farm's reference information

Five subtabs: **Workers**, **Teams**, **Blocks**, **Devices**,
**Suppliers**.

### Adding a worker

1. Master Data → Workers → **+ Add Worker**.
2. Fill in Employee Number, First Name, Last Name, SA ID Number, Bank,
   Account Number, WhatsApp Number, and which Farm/Supplier they belong
   to (leave as "(none)" for your own farm's workers).
3. Optionally take or upload a **Photo** right there.
4. Make sure **Active** is ticked, then **Save**.

### Printing worker badges

Workers scan a QR badge to identify themselves in the Field app — there's
no other way to select a worker, so every active picker needs one.

1. Master Data → Workers.
2. Use **Print Badges (filtered)** to print everyone currently shown in
   the table, **Print Badges (selected)** for just the ones you've
   ticked, or **Print Badges (all)** for the whole list.

Badges come out 60 × 80 mm, nine to a page — cut along the dashed lines
and each one fits an ID card holder as-is. **Check the print dialog's
scale is 100%, not "Fit to page"**, or they'll print slightly too small
to fit.

Reprint a badge any time a worker's is lost, damaged, or a new worker
joins.

### Teams, Blocks, Devices, Suppliers

- **Teams** — ID (e.g. "A"), name, induna. Add/edit with the same
  pattern as Workers.
- **Blocks** — each block's variety, tree count, hectares, and
  active/inactive. Use **Export .xlsx** / **Import** to bulk-edit many
  blocks at once from a spreadsheet rather than one at a time. Tick
  **"Replace all"** before importing only when the file is meant to
  completely replace the block list (e.g. after re-dividing a block) —
  it retires any block not in the file instead of leaving it behind.
- **Devices** — every phone/tablet/PC that can access the app must be
  added here first, with its role (Field, Pack House, or Admin), station
  name, and team. A device shows "Unknown device id" until you've added
  it here.
- **Suppliers** — other farms that use your pack house, with their
  contact details and packing rate. Their loads stay separate from your
  own harvest everywhere in the Dashboard and Reports.

---

## Payments — calculating wages

1. Payments tab.
2. Set the Farm/Supplier filter and date range (or Today/This Week/
   Season).
3. Tap **Calculate Wages** to see each worker's kg and amount due for
   that period, based on the current rate.
4. Tap **Export Wage Sheet** to download it for payroll.

---

## Reports — downloadable spreadsheets

1. Reports tab.
2. Set the date range and, if needed, a Farm/Supplier filter — **both
   dates must be filled in** or the report won't generate.
3. Tap the report you want. Each one downloads as an `.xlsx` file:

| Report | What it contains |
|---|---|
| Daily Harvest Summary | Crates/kg by block and team for one day (always just the start date — a wider range gets flagged "Daily report only") |
| Lot & Receiving Report | Every load dispatched in the range, with receiving detail |
| Plukstrokies / Picking Notes | One row per dispatched load — block(s), crates sent vs. received, driver, condition, notes, weather |
| Span Pluklys / Team Picking List | One row per team per day, matching the paper picking slip — blocks picked and loads dispatched that day |
| Daaglikse Oesdata / Daily Harvest Data | Kg by block vs. date over a range, with block and day totals |
| Harvesting List | Loads still being picked |
| In Transit List | Dispatched but not yet received |
| Pakhuis Ontvangstes / Pack House Receivables | Already received loads, matching the paper receiving slip — date/time, block, and rejected kg included |
| Worker Harvest Report | Per-worker crates/kg/amount due |
| Lietsjie Lone / Litchi Wages | Per-worker crates harvested, one column per date worked, plus a total — a whole pay period on one row |
| Block Harvest Report | Per-block crates/kg/averages |
| Historical Harvest Data | The full multi-year block x date pivot, 2020 through the current season, in one file. Ignores the date range above — there's only ever one. |

---

## Settings

- **Data Backup** — **Backup Now** creates an immediate downloadable
  backup, always. One also happens automatically every night at 02:00,
  but only if the server is running at the time *and* something actually
  changed that day — on a quiet day nothing is written and no new row
  appears, which is normal. (After 30 days with no changes one is taken
  anyway.) Download backups regularly and keep a copy somewhere off the
  server — the last 14 are kept automatically, which protects against
  mistakes but not a hardware failure.
- **Farm settings** — farm name, location, current harvest season year,
  the green→yellow and yellow→red timing thresholds used for the
  color-coded queues, and GPS coordinates (enables the weather widget).
- **Harvest rate** — the current rate per kg used for wage calculations.
- **Change admin password** — do this immediately after first login if
  you haven't already.

---

## If something looks wrong

- **"Could not generate report" / a toast about picking dates first** —
  make sure both the start and end date are filled in before tapping a
  report.
- **A device shows "Unknown device id"** — it hasn't been added in
  Master Data → Devices yet; add it there first. Once added (and marked
  active) it appears in the dropdown on that device's setup screen as
  soon as the page is reloaded.
- **A screen shows an older version number than you expect** (top
  corner, e.g. "v1.1") — fully close and reopen the app (not just switch
  away from it) to pick up the latest update. Each screen updates
  separately, so do this on every device, not just one.
- **An amber "Offline" bar appears** — the screen can't reach the server.
  Whatever is on screen stays put rather than blanking out, and refreshes
  itself once the connection is back. A brief drop does not sign you out;
  only a genuinely expired session does, and that says so.
- **Anything else unexpected** — a full page refresh often clears minor
  glitches; if it persists, note exactly what you were doing and check
  with whoever manages the server.
