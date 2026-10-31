// Admin app: master data, payments, reports, planner, settings.

let _systemSettings = null;

// Every server-supplied string interpolated into innerHTML goes through this
// - see Boord.escapeHtml in shared/api.js.
const esc = Boord.escapeHtml;

function updateBannerPackhouseName() {
  const el = document.getElementById("headerPackhouseName");
  if (!el) return;
  const s = _systemSettings || {};
  const name = s.packhouse_name || "Boord";
  el.textContent = s.packhouse_code ? `${name} · ${s.packhouse_code}` : name;
}

function updateBannerClock() {
  const el = document.getElementById("headerDateTime");
  if (!el) return;
  const now = new Date();
  // en-ZA and no seconds, like every other time on the screens (see the fmt*
  // helpers in shared/api.js). A ticking seconds digit in the header only
  // drew the eye away from the numbers that matter.
  const dateStr = now.toLocaleDateString("en-ZA", { weekday: "long", year: "numeric", month: "long", day: "numeric" });
  const timeStr = Boord.fmtTime(now);
  el.textContent = `${dateStr}  ·  ${timeStr}`;
}

async function updateBannerWeather() {
  const el = document.getElementById("headerWeather");
  if (!el) return;
  try {
    const w = await Boord.api("/api/weather/current");
    if (w && w.no_location) {
      // The farm's GPS isn't set. Say so rather than leaving the strip blank:
      // Weather, Risk and the Harvest Forecast all stay empty until it is,
      // and a silent gap gives no clue why.
      el.innerHTML = `<i class="fa-solid fa-location-dot"></i> Set pack house location in Settings`;
    } else if (w && w.temp !== undefined && w.temp !== null) {
      const icon = Boord.weatherIcon(w.condition);
      el.innerHTML = `<i class="fa-solid ${icon}"></i> ${Math.round(w.temp)}°C · ${esc(w.condition)}${w.humidity != null ? ` · ${w.humidity}% humidity` : ""}`;
    }
  } catch (e) {
    // weather is a nice-to-have - never blocks or errors the rest of the header
  }
}

function initBanner() {
  document.getElementById("appVersion").textContent = `v${Boord.VERSION}`;
  updateBannerPackhouseName();
  updateBannerClock();
  updateBannerWeather();
  setInterval(updateBannerClock, 1000);
}

// Straight into the app. There is no sign-in and nothing to validate first:
// if this page loaded at all, the server has already decided this request may
// see admin data, because it refuses to serve /admin/ to anything but its own
// console and the tailnet (backend/main.py, backend/security.py).
async function init() {
  await showApp();
}


// What the setup wizard needs to know before the app paints, or null when the
// question can't be answered right now - an unreachable server included, which
// is why a failure here paints the app rather than a stop screen.
async function fetchSetupState() {
  try {
    return await Boord.api("/api/setup/state");
  } catch (e) {
    return null; // api() has already flagged an unreachable server as offline
  }
}

// Called once per page load in every path except one: finishing the setup
// wizard calls it a second time. Everything that attaches a listener
// therefore has to run under _appBound, or every button in the app would be
// bound twice and fire twice.
let _appBound = false;

async function showApp() {
  // A database nobody has claimed yet gets the wizard instead of the tabs.
  // Deliberately decided here rather than by the server refusing every
  // endpoint until setup is done: the failure that pattern invites is locking
  // a farm out of its own harvest data over an unset threshold value, and the
  // settings that genuinely cannot be guessed already refuse at the point of
  // use.
  const setup = await fetchSetupState();
  if (setup && setup.blocked) return;
  if (setup && setup.required) { await showSetupWizard(setup); return; }

  document.getElementById("setupWizardScreen").classList.add("hidden");
  document.getElementById("app").classList.remove("hidden");

  if (!_appBound) {
    _appBound = true;
    bindTabs();
    bindCollapsibles();
    bindDashboard();
    bindMasterData();
    bindPayments();
    bindReports();
    bindMarkets();
    bindMarketModal();
    bindPlanner();
    bindSettings();
    bindSuppliers();

    Boord.offlineBanner("Offline - data may be out of date");
    Boord.onOfflineChange = (off) => { if (!off) refreshDashboard(); };
    LWPTR.attach(async () => {
      updateBannerWeather();
      const active = document.querySelector(".tab-btn.active");
      const tab = active ? active.dataset.tab : "dashboard";
      if (tab === "dashboard") await refreshDashboard();
      // Master Data now lives inside the Settings tab.
      else if (tab === "settings") { await loadSettingsForm(); await loadAllMasterData(); }
      else if (tab === "markets") { await loadMarketNotes(); await loadMarketMail(); }
      else if (tab === "planner") await loadPlanner(_plan ? { week_start: _plan.week_start } : {});
    });
    // iOS, in the installed app: after the keyboard (or a date or select
    // picker) closes, the tab bar fixed along the bottom of a phone can stay
    // up where the keyboard's top edge was, halfway up the screen over the
    // cards. Scrolling to where the page already is, once the keyboard is
    // gone, makes iOS place it at the bottom again. Skipped while focus moves
    // to another field, so tabbing through a form doesn't jump. The same fix
    // as Boord Packhouse's bottom menu (its v0.60).
    document.addEventListener("focusout", () => setTimeout(() => {
      if (!document.activeElement || !document.activeElement.matches("input, textarea, select")) {
        window.scrollTo(window.scrollX, window.scrollY);
      }
    }, 400));
  }

  try { await loadSettingsForm(); } catch (e) { /* offline - keep defaults */ }
  initBanner();
  try { await loadAllMasterData(); } catch (e) { /* offline - tables fill on reconnect */ }
  refreshDashboard();
}

// A refusal from an endpoint that normally answers. The middleware blocks the
// Admin page itself, so reaching this means the tab was opened from an address
// the server trusted and is no longer being served from one - Tailscale
// dropping mid-session is the way that actually happens. Show what the server
// said, which names the fix, rather than a generic "could not load".
function accessRefused(e) {
  Boord.toast(Boord.errorDetail(e, "The server refused this request"));
}

// ---------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------
function bindTabs() {
  // Boord Labour's admin pages: the same Tailscale host, Labour's port. It
  // asks for its own PIN - this link grants nothing by itself.
  document.getElementById("labourLink").href = `https://${location.hostname}:8040/admin/`;
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".tab-content").forEach((c) => c.classList.add("hidden"));
      document.getElementById(`tab-${btn.dataset.tab}`).classList.remove("hidden");
    });
  });
  document.querySelectorAll(".subtab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".subtab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".subtab-content").forEach((c) => c.classList.add("hidden"));
      document.getElementById(`subtab-${btn.dataset.subtab}`).classList.remove("hidden");
    });
  });
}

// ---------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------
function bindCollapsibles() {
  document.querySelectorAll(".collapsible-header").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById(btn.dataset.target).classList.toggle("hidden");
      const icon = btn.querySelector(".fa-chevron-down, .fa-chevron-up");
      if (icon) { icon.classList.toggle("fa-chevron-down"); icon.classList.toggle("fa-chevron-up"); }
    });
  });
}

function bindDashboard() {
  const today = Boord.localDateStr();
  document.getElementById("dashStart").value = today;
  document.getElementById("dashEnd").value = today;
  document.querySelector('.tab-btn[data-tab="dashboard"]').addEventListener("click", refreshDashboard);

  // Picking a period or a supplier must redraw the dashboard straight away.
  // Setting the inputs alone leaves the old period's figures on screen under
  // the new dates - which reads as "the season looks exactly like today"
  // rather than needing a separate Refresh press.
  Boord.bindDateRangePresets({
    todayBtn: document.getElementById("dashTodayBtn"),
    weekBtn: document.getElementById("dashWeekBtn"),
    seasonBtn: document.getElementById("dashSeasonBtn"),
    startInput: document.getElementById("dashStart"),
    endInput: document.getElementById("dashEnd"),
    seasonAnchor: () => ({
      month: (_systemSettings && _systemSettings.season_start_month) || 1,
      day: (_systemSettings && _systemSettings.season_start_day) || 1,
    }),
    onChange: refreshDashboard,
  });
  document.getElementById("dashStart").addEventListener("change", refreshDashboard);
  document.getElementById("dashEnd").addEventListener("change", refreshDashboard);
  document.getElementById("dashSupplierFilter").addEventListener("change", refreshDashboard);

  document.getElementById("closeLotCratesBtn").addEventListener("click", closeLotCratesModal);
  document.getElementById("editSlipBtn").addEventListener("click", editSlip);
}

async function refreshDashboard() {
  const start = document.getElementById("dashStart").value;
  const end = document.getElementById("dashEnd").value;
  const supplierId = document.getElementById("dashSupplierFilter").value;
  const supplierParam = supplierId ? `&supplier_id=${supplierId}` : "";
  const qs = `period_start=${start}&period_end=${end}${supplierParam}`;

  let harvesting, inTransit, received, summary;
  try {
    [harvesting, inTransit, received, summary] = await Promise.all([
      Boord.api(`/api/lots/pending?${qs}`),
      Boord.api(`/api/lots/in-transit?${qs}`),
      Boord.api(`/api/lots/received?${qs}`),
      Boord.api(`/api/dashboard/summary?${qs}`),
    ]);
  } catch (e) {
    if (Boord.isNetworkError(e)) return; // keep last data on screen
    if (Boord.isAuthError(e)) { accessRefused(e); return; }
    Boord.toast("Could not load the dashboard");
    return;
  }

  renderDashboardKpis(harvesting, inTransit, received, summary);
  renderDashboardLists(harvesting, inTransit, received, summary);
  renderDashboardEmpty([...harvesting, ...inTransit, ...received], start, end);
}

// A row of zeros reads like a broken dashboard; an empty period says so in
// words instead. Still rendered underneath, so nothing else changes.
function renderDashboardEmpty(lots, start, end) {
  const empty = !lots.some((l) => l.total_crates > 0);
  document.getElementById("dashEmpty").classList.toggle("hidden", !empty);
  document.getElementById("dashData").classList.toggle("hidden", empty);
  if (!empty) return;
  const day = (iso) => Boord.fmtDate(iso);
  const supplierSelect = document.getElementById("dashSupplierFilter");
  const forSupplier = supplierSelect.value ? ` for ${supplierSelect.options[supplierSelect.selectedIndex].text}` : "";
  document.getElementById("dashEmptyDetail").textContent = start === end
    ? `No crates logged on ${day(start)}${forSupplier}.`
    : `No crates logged between ${day(start)} and ${day(end)}${forSupplier}.`;
}

function _lotTotals(lots) {
  return {
    crates: lots.reduce((s, l) => s + l.total_crates, 0),
    kg: lots.reduce((s, l) => s + l.total_kg, 0),
  };
}

function renderDashboardKpis(harvesting, inTransit, received, summary) {
  const h = _lotTotals(harvesting);
  const t = _lotTotals(inTransit);
  const r = _lotTotals(received);
  const allLots = [...harvesting, ...inTransit, ...received];
  const totalCrates = h.crates + t.crates + r.crates;
  const totalKg = h.kg + t.kg + r.kg;
  const avgKgPerLot = allLots.length ? totalKg / allLots.length : 0;
  const avgKgPerCrate = totalCrates ? totalKg / totalCrates : 0;

  const cards = [
    ["Teams Active", `${summary.active_teams} teams`],
    ["Workers Active", `${summary.active_workers} workers`],
    ["Blocks Active", `${summary.active_blocks} blocks`],
    ["Total kg picked", Boord.fmtKg(totalKg)],
    ["Total Crates", `${totalCrates} crates`],
    ["Avg Kg/Slip", Boord.fmtNum(avgKgPerLot)],
    ["Avg Kg/Crate", Boord.fmtNum(avgKgPerCrate)],
    ["Harvesting", `${h.crates} crates / ${Boord.fmtKg(h.kg)}`],
    ["In Transit", `${t.crates} crates / ${Boord.fmtKg(t.kg)}`],
    ["Delivered kg", _deliveredKg(summary.delivered_kg)],
    ["Delivered vs Picked", _deliveredVsPicked(summary.delivered_kg, totalKg)],
  ];
  document.getElementById("dashKpiGrid").innerHTML = cards.map(([label, value]) => `
    <div class="bg-white rounded-xl shadow p-4">
      <div class="text-xs text-slate-500">${label}</div>
      <div class="text-xl font-bold">${value}</div>
    </div>
  `).join("");
}

// Kg on the period's Packhouse delivery notes. Delivery notes can't be split
// by supplier, so with one picked the card says so rather than mislead.
function _deliveredKg(deliveredKg) {
  if (deliveredKg == null) {
    return document.getElementById("dashSupplierFilter").value ? "All suppliers only" : "No Packhouse data";
  }
  return Boord.fmtKg(deliveredKg);
}

// Kg on the period's delivery notes less kg picked, and that as a % of
// picked: negative is fruit picked that never went out (culls, packing
// loss, or still in the cold store).
function _deliveredVsPicked(deliveredKg, pickedKg) {
  if (deliveredKg == null) return _deliveredKg(deliveredKg);
  const diff = deliveredKg - pickedKg;
  const sign = diff > 0 ? "+" : "";
  const pct = pickedKg ? ` (${sign}${Boord.fmtNum(diff / pickedKg * 100)}%)` : "";
  return `${sign}${Boord.fmtKg(diff)}${pct}`;
}

function renderDashboardLists(harvesting, inTransit, received, summary) {
  const h = _lotTotals(harvesting);
  const t = _lotTotals(inTransit);
  const r = _lotTotals(received);

  document.getElementById("dash-harvesting-title").textContent = `Harvesting - ${h.crates} crates / ${Boord.fmtKg(h.kg)}`;
  document.getElementById("dash-harvesting-body").innerHTML = harvesting.map((l) => `
    <div class="p-3 urgency-${esc(l.urgency)}">
      <div class="font-semibold text-sm">${esc(l.slip_number)} <span class="text-xs font-normal text-slate-500">${esc(l.supplier_name)}${_blocksText(l)}</span></div>
      <div class="text-sm">${l.total_crates} crates / ${Boord.fmtKg(l.total_kg)} - ${l.age_minutes} min ago</div>
    </div>
  `).join("") || `<div class="p-3 text-sm text-slate-400">Nothing currently being harvested</div>`;

  document.getElementById("dash-intransit-title").textContent = `In transit - ${t.crates} crates / ${Boord.fmtKg(t.kg)}`;
  document.getElementById("dash-intransit-body").innerHTML = inTransit.map((l) => `
    <div class="p-3 urgency-${esc(l.urgency)}">
      <div class="font-semibold text-sm">${esc(l.slip_number)} <span class="text-xs font-normal text-slate-500">${esc(l.supplier_name)}${_blocksText(l)}</span></div>
      <div class="text-sm">${_crateText(l)} / ${Boord.fmtKg(l.total_kg)} - ${l.age_minutes} min ago</div>
    </div>
  `).join("") || `<div class="p-3 text-sm text-slate-400">Nothing currently in transit</div>`;

  document.getElementById("dash-received-title").textContent = `Received - ${r.crates} crates / ${Boord.fmtKg(r.kg)}`;
  document.getElementById("dash-received-body").innerHTML = received.map((l) => `
    <button type="button" data-lot-id="${esc(l.id)}" class="received-lot-row w-full text-left p-3 hover:bg-slate-50">
      <div class="font-semibold text-sm">${esc(l.slip_number)} <span class="text-xs font-normal text-slate-500">${esc(l.supplier_name)}${_blocksText(l)}</span></div>
      <div class="text-sm text-slate-600">${_crateText(l)} / ${Boord.fmtKg(l.total_kg)} - received ${Boord.fmtDateTime(l.received_at)} <span class="text-blue-700">· view / edit crates</span></div>
    </button>
  `).join("") || `<div class="p-3 text-sm text-slate-400">Nothing received in this period</div>`;
  // Rebuilt from scratch on every refresh, so bind after render - the
  // received/dispatched/harvesting/workers/blocks pattern throughout this
  // file (loadWorkers, loadBlocks, ...) does the same for the same reason.
  document.querySelectorAll("#dash-received-body .received-lot-row").forEach((btn) => {
    btn.addEventListener("click", () => openLotCrates(parseInt(btn.dataset.lotId, 10)));
  });

  document.getElementById("dash-workers-title").textContent = `Workers - ${summary.workers.length} workers`;
  document.getElementById("dash-workers-rows").innerHTML = summary.workers.map((w) => `
    <tr class="border-b">
      <td class="p-2">${esc(w.name)}</td>
      <td class="p-2">${esc(w.supplier_name)}</td>
      <td class="p-2">${w.crates}</td>
      <td class="p-2">${Boord.fmtNum(w.total_kg)}</td>
      <td class="p-2">${Boord.fmtRand(w.amount_due)}</td>
      <td class="p-2">${Boord.fmtNum(w.avg_kg_crate)}</td>
    </tr>
  `).join("") || `<tr><td class="p-2 text-slate-400" colspan="6">No harvest activity in this period</td></tr>`;

  document.getElementById("dash-blocks-title").textContent = `Blocks - ${summary.blocks.length} blocks`;
  document.getElementById("dash-blocks-rows").innerHTML = summary.blocks.map((b) => `
    <tr class="border-b">
      <td class="p-2">${esc(b.name)}</td>
      <td class="p-2">${b.crates}</td>
      <td class="p-2">${Boord.fmtNum(b.total_kg)}</td>
      <td class="p-2">${Boord.fmtNum(b.avg_kg_crate)}</td>
      <td class="p-2">${b.avg_kg_tree != null ? Boord.fmtNum(b.avg_kg_tree) : "-"}</td>
      <td class="p-2">${b.avg_kg_hectare != null ? Boord.fmtNum(b.avg_kg_hectare) : "-"}</td>
    </tr>
  `).join("") || `<tr><td class="p-2 text-slate-400" colspan="6">No harvest activity in this period</td></tr>`;
}

// ---------------------------------------------------------------------
// Lot crates (Received list drill-down + correcting a field-captured crate)
// ---------------------------------------------------------------------
let _lotCratesContext = null; // { lot, crates } for whichever lot is open

function _workerOptionLabel(w) {
  const name = w.name || `${w.first_name || ""} ${w.last_name || ""}`.trim() || w.id;
  return `${name} (${w.id})${w.active ? "" : " - inactive"}`;
}

function _workerName(workerId) {
  const w = (window._workersCache || []).find((w) => w.id === workerId);
  if (w) return w.name || `${w.first_name || ""} ${w.last_name || ""}`.trim() || w.id;
  return workerId || "(no worker recorded)";
}

async function openLotCrates(lotId) {
  let data;
  try {
    data = await Boord.api(`/api/lots/${lotId}`);
  } catch (e) {
    if (Boord.isAuthError(e)) { accessRefused(e); return; }
    Boord.toast("Could not load this lot's crates - check connection");
    return;
  }
  _lotCratesContext = data;
  document.getElementById("lotCratesWagesWarning").classList.add("hidden");
  renderLotCratesModal();
  document.getElementById("lotCratesModal").classList.remove("hidden");
  document.getElementById("lotCratesModal").classList.add("flex");
}

function closeLotCratesModal() {
  document.getElementById("lotCratesModal").classList.add("hidden");
  document.getElementById("lotCratesModal").classList.remove("flex");
  _lotCratesContext = null;
}

// "20 picked -> 8 sent" when crates were combined for transport, else "20 crates".
function _crateText(l) {
  const sent = l.transport_crates;
  return sent != null && sent !== l.total_crates ? `${l.total_crates} picked → ${sent} sent` : `${l.total_crates} crates`;
}

// "· Block 7, 8a" from the slip's crates. A delivery logged from an outside
// grower (POST /api/lots/external, slip "EXT-...") has no crates and so no
// block: say what it is instead, so the gap doesn't read as missing data.
function _blocksText(l) {
  if (l.blocks && l.blocks.length) return ` · Block ${esc(l.blocks.join(", "))}`;
  return String(l.slip_number || "").startsWith("EXT-") ? " · External delivery" : "";
}

function blocksLabelFor(blocks) {
  return blocks && blocks.length ? `Block ${blocks.join(", ")} - ` : "";
}

function renderLotCratesModal() {
  if (!_lotCratesContext) return;
  const { lot, crates } = _lotCratesContext;
  document.getElementById("lotCratesTitle").textContent = `Lot ${lot.slip_number}`;
  document.getElementById("lotCratesMeta").textContent =
    `${blocksLabelFor([...new Set(crates.map((c) => c.block_id).filter(Boolean))].sort())}${_crateText(lot)} / ${Boord.fmtKg(lot.total_kg)} - received ${Boord.fmtDateTime(lot.received_at)}`;
  renderLotSlipDetails();

  document.getElementById("lotCratesRows").innerHTML = crates.map((c) => {
    const net = Boord.fmtNum(c.weight_kg - (c.deduction_kg || 0));
    const editedNote = c.edited_at
      ? `<div class="text-[11px] text-amber-700">edited by ${esc(c.edited_by || "admin")}, ${Boord.fmtDateTime(c.edited_at)}</div>`
      : "";
    return `
      <tr class="border-b align-top">
        <td class="p-2 whitespace-nowrap">${Boord.fmtTime(c.timestamp)}</td>
        <td class="p-2">${esc(c.block_id || "")}</td>
        <td class="p-2">${esc(_workerName(c.worker_id))}${editedNote}</td>
        <td class="p-2">${Boord.fmtNum(c.weight_kg)}</td>
        <td class="p-2">${Boord.fmtNum(c.deduction_kg || 0)}</td>
        <td class="p-2 font-semibold">${net}</td>
        <td class="p-2 text-right whitespace-nowrap">
          <button class="text-blue-700 text-xs" data-edit-crate="${esc(c.uuid)}">Edit</button>
          <button class="text-red-700 text-xs ml-2" data-delete-crate="${esc(c.uuid)}">Delete</button>
        </td>
      </tr>`;
  }).join("") || `<tr><td class="p-2 text-slate-400" colspan="7">No crates on this lot</td></tr>`;

  document.querySelectorAll("#lotCratesRows [data-edit-crate]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const crate = _lotCratesContext.crates.find((c) => c.uuid === btn.dataset.editCrate);
      if (crate) editCrate(crate);
    });
  });
  document.querySelectorAll("#lotCratesRows [data-delete-crate]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const crate = _lotCratesContext.crates.find((c) => c.uuid === btn.dataset.deleteCrate);
      if (crate) deleteCrate(crate);
    });
  });
}

// The slip's own fields and its receipt, under the title: what Edit slip
// changes. A field that is blank on the slip is still listed, so the office
// can see it is blank rather than wonder whether the screen dropped it.
function renderLotSlipDetails() {
  const { lot, receiving } = _lotCratesContext;
  const supplier = (window._suppliersCache || []).find((s) => s.id === lot.supplier_id);
  const team = (window._teamsCache || []).find((x) => x.id === lot.team_id);
  const edited = lot.edited_at
    ? `<div class="col-span-full text-[11px] text-amber-700">slip edited by ${esc(lot.edited_by || "admin")}, ${Boord.fmtDateTime(lot.edited_at)}</div>`
    : "";
  const item = (label, value) =>
    `<div><span class="text-slate-400">${label}</span> ${esc(value === null || value === undefined || value === "" ? "-" : String(value))}</div>`;
  const receipt = receiving ? [
    item("Expected", receiving.expected_crates),
    item("Received", `${receiving.actual_crates}${receiving.discrepancy ? ` (${receiving.discrepancy > 0 ? "+" : ""}${receiving.discrepancy})` : ""}`),
    item("Condition", receiving.condition),
    item("Waste", Boord.fmtKg(receiving.waste_kg || 0)),
    item("Received by", receiving.received_by),
    item("Receiving notes", receiving.notes),
  ].join("") : `<div class="col-span-full text-slate-400">No receipt recorded</div>`;
  document.getElementById("lotSlipDetails").innerHTML = [
    item("Supplier", supplier ? supplier.name : lot.supplier_id),
    item("Team", team ? team.name : lot.team_id),
    item("Driver", lot.driver),
    item("Dispatched", Boord.fmtDateTime(lot.timestamp)),
    item("Received", Boord.fmtDateTime(lot.received_at)),
    item("Slip notes", lot.notes),
    receipt,
    edited,
  ].join("");
}

// "YYYY-MM-DDTHH:MM" in the browser's local time, for a datetime-local
// input; the server's naive-UTC stamp comes in through parseServerDate.
function _toLocalInput(value) {
  const d = Boord.parseServerDate(value);
  if (!d) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

// The input's local time back to an ISO instant for the server; null when
// the field was cleared, so a patch leaves the stored time alone.
function _fromLocalInput(s) {
  const d = s ? new Date(s) : null;
  return d && !isNaN(d.getTime()) ? d.toISOString() : null;
}

// Edit the slip itself - the fields a paper picking note and the gate's
// receipt carry - including its block, which is really every crate's block
// (PATCH /api/lots/{id}; see LotEdit in routers/lots.py). Crates, weights
// and workers stay per crate, in editCrate.
function editSlip() {
  if (!_lotCratesContext) return;
  const { lot, crates, receiving } = _lotCratesContext;
  // From the crates as they are now, not the `blocks` the open fetched: a
  // crate edited since then (editCrate) has moved in the context's crates only.
  const blocks = [...new Set(crates.map((c) => c.block_id).filter(Boolean))].sort();
  const hasCrates = crates.length > 0;
  const fields = [];
  const initial = {
    ...lot,
    timestamp: _toLocalInput(lot.timestamp),
    received_at: _toLocalInput(lot.received_at),
    transport_crates: lot.transport_crates ?? "",
    team_id: lot.team_id || "",
    expected_crates: receiving ? receiving.expected_crates : "",
    actual_crates: receiving ? receiving.actual_crates : "",
    condition: receiving ? receiving.condition : "",
    waste_kg: receiving ? receiving.waste_kg : "",
    received_by: receiving ? receiving.received_by : "",
    receiving_notes: receiving ? receiving.notes : "",
  };

  if (hasCrates) {
    // The slip's block is whichever block its crates were captured against.
    // Picking one here moves every crate; a slip across two blocks starts on
    // "(as captured)" so that Save with nothing chosen changes no crate.
    const current = blocks.length === 1 ? blocks[0] : "";
    const blockOptions = (window._blocksCache || [])
      .filter((b) => b.active || blocks.includes(b.id))
      .map((b) => ({ value: b.id, label: `${b.name || b.id}${b.active ? "" : " - inactive"}` }));
    if (!current || !blockOptions.some((o) => o.value === current)) {
      blockOptions.unshift({ value: current, label: blocks.length
        ? `${blocks.join(", ")} (as captured${blocks.length > 1 ? ", mixed" : ""})` : "(no block recorded)" });
    }
    initial.block_id = current;
    fields.push({ key: "block_id", label: "Block (every crate on this slip)", type: "select", options: blockOptions });
  }

  const supplierOptions = (window._suppliersCache || [])
    .filter((s) => s.active || s.id === lot.supplier_id)
    .map((s) => ({ value: s.id, label: `${s.name}${s.active ? "" : " - inactive"}` }));
  if (!supplierOptions.some((o) => o.value === lot.supplier_id)) {
    supplierOptions.unshift({ value: lot.supplier_id ?? "", label: lot.supplier_id ? String(lot.supplier_id) : "(no supplier)" });
  }
  const teamOptions = [{ value: "", label: "(no team)" }].concat((window._teamsCache || [])
    .filter((x) => x.active || x.id === lot.team_id)
    .map((x) => ({ value: x.id, label: `${x.name || x.id}${x.active ? "" : " - inactive"}` })));
  if (lot.team_id && !teamOptions.some((o) => o.value === lot.team_id)) {
    teamOptions.push({ value: lot.team_id, label: lot.team_id });
  }

  fields.push(
    { key: "supplier_id", label: "Supplier", type: "select", options: supplierOptions },
    { key: "team_id", label: "Team", type: "select", options: teamOptions },
    { key: "driver", label: "Driver", type: "text" },
    { key: "timestamp", label: "Dispatched", type: "datetime-local" },
    { key: "received_at", label: "Received", type: "datetime-local" },
    // A slip with crates adds them up; only an external delivery has typed
    // totals. The server refuses typed totals on a slip with crates, so the
    // inputs are shown but locked rather than hidden.
    { key: "total_crates", label: hasCrates ? "Crates picked (added up from the crates below)" : "Crates", type: "number", disabled: hasCrates },
    { key: "transport_crates", label: "Crates sent (blank = same as picked)", type: "number" },
    { key: "total_kg", label: hasCrates ? "Kg (added up from the crates below)" : "Kg", type: "number", disabled: hasCrates },
    { key: "notes", label: "Slip notes", type: "text" },
  );
  if (receiving) {
    fields.push(
      { key: "expected_crates", label: "Receiving: crates expected", type: "number" },
      { key: "actual_crates", label: "Receiving: crates received", type: "number" },
      { key: "condition", label: "Receiving: condition", type: "text" },
      { key: "waste_kg", label: "Receiving: waste (kg)", type: "number" },
      { key: "received_by", label: "Received by", type: "text" },
      { key: "receiving_notes", label: "Receiving notes", type: "text" },
    );
  }

  openEditModal(`Edit Slip ${lot.slip_number}`, fields, initial, async (values) => {
    const num = (v, parse = parseFloat) => (v === "" || v === null || v === undefined ? null : parse(v));
    const body = {
      supplier_id: num(values.supplier_id, (v) => parseInt(v, 10)),
      team_id: values.team_id ?? "",
      driver: values.driver,
      timestamp: _fromLocalInput(values.timestamp),
      received_at: _fromLocalInput(values.received_at),
      // Blank means "same as picked"; the server reads 0 as clearing it.
      transport_crates: values.transport_crates === "" ? 0 : parseInt(values.transport_crates, 10),
      notes: values.notes,
    };
    if (hasCrates) {
      if (values.block_id) body.block_id = values.block_id; // "" = as captured, no move
    } else {
      body.total_crates = num(values.total_crates, (v) => parseInt(v, 10));
      body.total_kg = num(values.total_kg);
    }
    if (receiving) {
      Object.assign(body, {
        expected_crates: num(values.expected_crates, (v) => parseInt(v, 10)),
        actual_crates: num(values.actual_crates, (v) => parseInt(v, 10)),
        condition: values.condition,
        waste_kg: num(values.waste_kg),
        received_by: values.received_by,
        receiving_notes: values.receiving_notes,
      });
    }
    // A null is a field left blank: patch semantics, the server keeps what
    // it has. (team_id "" and transport_crates 0 are deliberate clears.)
    Object.keys(body).forEach((k) => { if (body[k] === null || Number.isNaN(body[k])) delete body[k]; });

    let result;
    try {
      result = await Boord.api(`/api/lots/${lot.id}`, { method: "PATCH", body });
    } catch (e) {
      if (Boord.isAuthError(e)) { accessRefused(e); return; }
      Boord.toast("Could not save: " + Boord.errorDetail(e, "Unknown error"));
      throw e; // keeps the modal open, as editCrate does
    }
    Boord.toast("Slip updated");
    if (_lotCratesContext && _lotCratesContext.lot.id === lot.id) {
      _lotCratesContext = result;
      renderLotCratesModal();
    }
    await refreshDashboard(); // Received row, KPIs, Blocks all move together
  });
}

// Lot totals in the open modal follow whatever the server re-derived.
function _applyLotTotals(lotTotals) {
  if (lotTotals && _lotCratesContext && _lotCratesContext.lot.id === lotTotals.lot_id) {
    _lotCratesContext.lot.total_crates = lotTotals.total_crates;
    _lotCratesContext.lot.total_kg = lotTotals.total_kg;
  }
}

async function deleteCrate(crate) {
  const net = Boord.fmtKg(crate.weight_kg - (crate.deduction_kg || 0));
  if (!confirm(`Delete this crate?\n\n${_workerName(crate.worker_id)} - block ${crate.block_id || "?"} - ${net}\n\nThis can't be undone.`)) return;
  let result;
  try {
    result = await Boord.api(`/api/harvest-records/${encodeURIComponent(crate.uuid)}`, { method: "DELETE" });
  } catch (e) {
    if (Boord.isAuthError(e)) { accessRefused(e); return; }
    Boord.toast("Could not delete: " + _apiErrorDetail(e));
    return;
  }
  const slipGone = !!(result.lot && result.lot.deleted);
  Boord.toast(slipGone ? "Crate deleted - the slip had no crates left and was removed" : "Crate deleted");
  if (_lotCratesContext) {
    _lotCratesContext.crates = _lotCratesContext.crates.filter((c) => c.uuid !== crate.uuid);
    _applyLotTotals(result.lot);
    renderLotCratesModal();
    // Left open rather than closed, so a wages warning below still shows.
    if (slipGone) document.getElementById("lotCratesMeta").textContent = "Slip removed - it had no crates left";
  }
  renderWagesWarning(result.wages_affected);
  await refreshDashboard();
}

function renderWagesWarning(wagesAffected) {
  // Left alone (never cleared) when a save comes back with nothing affected,
  // so correcting a second crate in the same session doesn't silently drop
  // the warning from the first one.
  if (!wagesAffected || !wagesAffected.length) return;
  const el = document.getElementById("lotCratesWagesWarning");
  const lines = wagesAffected.map((w) =>
    `<strong>${esc(w.worker_name)}</strong>: wages for ${esc(w.period_start)} to ${esc(w.period_end)} were already calculated and do not reflect this change.`);
  el.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> ${lines.join("<br>")}<br>` +
    `Re-run <strong>Calculate Wages</strong> for the affected period(s) in Payments to update the wage sheet.`;
  el.classList.remove("hidden");
}

function editCrate(crate) {
  // The worker who picked this crate must stay selectable even if they've
  // since been deactivated - the select is populated from initial[key]
  // (see openEditModal), and if that id isn't among the options the browser
  // silently falls back to whatever option is first, which would submit a
  // worker the admin never actually chose.
  const cache = window._workersCache || [];
  const options = cache
    .filter((w) => w.active || w.id === crate.worker_id)
    .map((w) => ({ value: w.id, label: _workerOptionLabel(w) }));
  if (!crate.worker_id) options.unshift({ value: "", label: "(no worker recorded)" });
  // Same rule for the block: a since-retired block must stay selectable.
  const blockOptions = (window._blocksCache || [])
    .filter((b) => b.active || b.id === crate.block_id)
    .map((b) => ({ value: b.id, label: `${b.name || b.id}${b.active ? "" : " - inactive"}` }));
  if (!blockOptions.some((o) => o.value === crate.block_id)) {
    blockOptions.unshift({ value: crate.block_id || "", label: crate.block_id || "(no block recorded)" });
  }

  openEditModal("Edit Crate", [
    { key: "worker_id", label: "Worker", type: "select", options },
    { key: "block_id", label: "Block", type: "select", options: blockOptions },
    { key: "weight_kg", label: "Weight (kg)", type: "number" },
    { key: "deduction_kg", label: "Deduction (kg)", type: "number" },
  ], crate, async (values) => {
    const body = {
      worker_id: values.worker_id,
      block_id: values.block_id || null,
      weight_kg: parseFloat(values.weight_kg),
      deduction_kg: parseFloat(values.deduction_kg) || 0,
    };
    let result;
    try {
      result = await Boord.api(`/api/harvest-records/${encodeURIComponent(crate.uuid)}`, {
        method: "PATCH", body,
      });
    } catch (e) {
      if (Boord.isAuthError(e)) { accessRefused(e); return; }
      Boord.toast("Could not save: " + Boord.errorDetail(e, "Unknown error"));
      throw e; // the bindMasterData save handler only closes the modal on
      // success - rethrowing keeps it open so a bad number can be fixed
      // without re-entering everything.
    }
    Boord.toast("Crate updated");

    if (_lotCratesContext) {
      const idx = _lotCratesContext.crates.findIndex((c) => c.uuid === crate.uuid);
      if (idx >= 0) _lotCratesContext.crates[idx] = result.record;
      _applyLotTotals(result.lot);
      renderLotCratesModal();
    }
    renderWagesWarning(result.wages_affected);
    await refreshDashboard(); // Received row, KPIs, Workers/Blocks all move together
  });
}

// ---------------------------------------------------------------------
// Master data (generic table + modal editor)
// ---------------------------------------------------------------------
let editContext = null;

function openEditModal(title, fields, initial, onSave) {
  document.getElementById("editModalTitle").textContent = title;
  const container = document.getElementById("editModalFields");
  container.innerHTML = fields.map((f) => `
    <div>
      <label class="text-xs text-slate-500 block">${f.label}</label>
      ${f.type === "select"
        ? `<select data-key="${f.key}" class="w-full border border-slate-300 rounded-lg p-2">${f.options.map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("")}</select>`
        : f.type === "checkbox"
        ? `<label class="flex items-center gap-2 mt-1"><input data-key="${f.key}" type="checkbox" class="w-4 h-4"> <span class="text-sm">${f.label}</span></label>`
        : f.type === "file"
        ? `<div class="flex gap-2">
             <input data-key="${f.key}" type="file" accept="image/*" class="flex-1 min-w-0 border border-slate-300 rounded-lg p-2">
             <button type="button" data-camera-for="${f.key}" title="Take photo with camera" class="px-3 border border-slate-300 rounded-lg bg-slate-50"><i class="fa-solid fa-camera"></i></button>
           </div>`
        : `<input data-key="${f.key}" type="${f.type || "text"}" ${f.disabled ? "disabled" : ""} class="w-full border border-slate-300 rounded-lg p-2">`}
    </div>
  `).join("");
  fields.forEach((f) => {
    if (f.type === "file") {
      const input = container.querySelector(`[data-key="${f.key}"]`);
      const camBtn = container.querySelector(`[data-camera-for="${f.key}"]`);
      if (camBtn && input) camBtn.addEventListener("click", () => openCameraCapture(input));
      return; // file inputs can't have their value set programmatically
    }
    const el = container.querySelector(`[data-key="${f.key}"]`);
    const value = initial ? initial[f.key] : "";
    if (f.type === "checkbox") el.checked = !!value;
    else el.value = value ?? "";
  });
  editContext = { fields, onSave };
  document.getElementById("editModal").classList.remove("hidden");
  document.getElementById("editModal").classList.add("flex");
}

function closeEditModal() {
  document.getElementById("editModal").classList.add("hidden");
  document.getElementById("editModal").classList.remove("flex");
  editContext = null;
  stopCameraStream();
}

// ---------------------------------------------------------------------
// Camera capture (for the Photo field in Add/Edit Worker)
// ---------------------------------------------------------------------
let _cameraStream = null;
let _cameraTargetInput = null;

function stopCameraStream() {
  if (_cameraStream) {
    _cameraStream.getTracks().forEach((t) => t.stop());
    _cameraStream = null;
  }
}

async function openCameraCapture(inputEl) {
  _cameraTargetInput = inputEl;
  const modal = document.getElementById("cameraModal");
  const video = document.getElementById("cameraPreview");
  const errEl = document.getElementById("cameraError");
  errEl.classList.add("hidden");
  modal.classList.remove("hidden");
  modal.classList.add("flex");
  try {
    _cameraStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    video.srcObject = _cameraStream;
  } catch (e) {
    errEl.textContent = "Could not access camera: " + (e.message || e);
    errEl.classList.remove("hidden");
  }
}

function closeCameraModal() {
  document.getElementById("cameraModal").classList.add("hidden");
  document.getElementById("cameraModal").classList.remove("flex");
  stopCameraStream();
  _cameraTargetInput = null;
}

function captureCameraPhoto() {
  const video = document.getElementById("cameraPreview");
  if (!video.videoWidth) return; // stream not ready yet
  const canvas = document.getElementById("cameraCanvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  canvas.toBlob((blob) => {
    if (!blob || !_cameraTargetInput) return;
    const file = new File([blob], `photo-${Date.now()}.jpg`, { type: "image/jpeg" });
    const dt = new DataTransfer();
    dt.items.add(file);
    _cameraTargetInput.files = dt.files;
    _cameraTargetInput.dispatchEvent(new Event("change", { bubbles: true }));
    closeCameraModal();
  }, "image/jpeg", 0.9);
}

function bindMasterData() {
  document.getElementById("editModalCancel").addEventListener("click", closeEditModal);
  document.getElementById("editModalSave").addEventListener("click", async () => {
    if (!editContext) return;
    const values = {};
    editContext.fields.forEach((f) => {
      if (f.type === "file") return; // handled separately by the caller's onSave, not part of the JSON body
      const el = document.getElementById("editModalFields").querySelector(`[data-key="${f.key}"]`);
      values[f.key] = f.type === "checkbox" ? el.checked : el.value;
    });
    // A save the server refused (a 403 once Tailscale dropped, a team id
    // that doesn't exist) used to reject into nowhere: the modal stayed
    // open, no toast, and Save looked dead. Say what happened, and keep the
    // modal - and everything typed into it - open to fix and save again.
    try {
      await editContext.onSave(values);
    } catch (e) {
      Boord.toast(Boord.isNetworkError(e) ? "No connection - not saved, try again"
                                          : Boord.errorDetail(e, "Could not save"));
      return;
    }
    closeEditModal();
  });

  document.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => handleAction(btn.dataset.action));
  });
  document.getElementById("importWorkers").addEventListener("change", (e) => importFile(e, "/api/workers/import", loadWorkers));
  document.getElementById("importBlocks").addEventListener("change", (e) => {
    const replace = document.getElementById("importBlocksReplace").checked;
    importFile(e, `/api/blocks/import?replace=${replace}`, loadBlocks);
  });
  document.getElementById("workerSupplierFilter").addEventListener("change", renderWorkersTable);
  document.getElementById("cameraCancelBtn").addEventListener("click", closeCameraModal);
  document.getElementById("cameraCaptureBtn").addEventListener("click", captureCameraPhoto);
}

async function importFile(event, url, reload) {
  const file = event.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    const result = await Boord.api(url, { method: "POST", body: form, isForm: true, timeoutMs: Boord.UPLOAD_TIMEOUT_MS });
    const extra = result.deactivated ? `, deactivated ${result.deactivated}` : "";
    Boord.toast(`Imported ${result.imported} rows${extra}`);
    await reload();
  } catch (e) {
    Boord.toast("Import failed - check the file format");
  }
  event.target.value = "";
}

async function handleAction(action) {
  if (action === "export-workers-xlsx") return exportFile("/api/workers/export?fmt=xlsx", "Workers.xlsx");
  if (action === "export-blocks-xlsx") return exportFile("/api/blocks/export?fmt=xlsx", "Blocks.xlsx");
  if (action === "new-worker") return editWorker();
  if (action === "print-selected") return printSelectedWorkers();
  if (action === "print-all") return printAllWorkers();
  if (action === "print-filtered") return printFilteredWorkers();
  if (action === "new-team") return editTeam();
  if (action === "new-block") return editBlock();
  if (action === "new-device") return editDevice();
  if (action === "new-supplier") return editSupplier();
}

// Every download on the admin screen. A failed one - offline, or a 403 once
// Tailscale has dropped - rejected silently before, so the button looked
// like it had done nothing.
async function exportFile(path, filename) {
  let blob;
  try {
    blob = await Boord.api(path, { timeoutMs: Boord.UPLOAD_TIMEOUT_MS });
  } catch (e) {
    Boord.toast(Boord.isNetworkError(e) ? `No connection - could not download ${filename}`
                                        : Boord.errorDetail(e, `Could not download ${filename}`));
    return;
  }
  Boord.downloadBlob(blob, filename);
}

// Suppliers and teams first, then everything that renders their names.
// Workers, blocks and devices all resolve a supplier_id (and devices a
// team_id) against the caches those two fill, so loading the whole lot in
// one Promise.all is a race: whichever table wins draws blank names and
// only looks right after the next refresh.
async function loadAllMasterData() {
  await Promise.all([loadSuppliers(), loadTeams()]);
  await Promise.all([loadWorkers(), loadBlocks(), loadDevices()]);
}

// Workers
async function loadWorkers() {
  // Full records, because the Edit modal needs id_number and whatsapp_number.
  // /api/workers hands that back only to the admin - decided from the
  // address this request arrives on, so there is nothing to ask for here.
  // A Field tablet calling the same endpoint gets a reduced projection.
  const workers = await Boord.api("/api/workers");
  window._workersCache = workers;
  renderWorkersTable();
}

function renderWorkersTable() {
  const workers = window._workersCache || [];
  const suppliers = new Map((window._suppliersCache || []).map((s) => [s.id, s.name]));
  const filterVal = document.getElementById("workerSupplierFilter")?.value || "";
  const filtered = filterVal ? workers.filter((w) => String(w.supplier_id ?? "") === filterVal) : workers;
  window._workersFiltered = filtered;

  document.getElementById("workersTable").innerHTML = filtered.map((w) => `
    <tr class="border-b ${w.active ? "" : "opacity-50"}">
      <td class="p-2"><input type="checkbox" class="worker-select-checkbox w-4 h-4" data-select="${esc(w.id)}"></td>
      <td class="p-2">${w.photo_filename
        ? `<img src="/photos/${esc(encodeURIComponent(w.photo_filename))}" class="w-8 h-8 rounded-full object-cover">`
        : '<span class="w-8 h-8 rounded-full bg-slate-200 inline-flex items-center justify-center text-slate-400 text-xs">?</span>'}</td>
      <td class="p-2 font-mono">${esc(w.id)}</td>
      <td class="p-2">${esc(w.first_name || "")}</td>
      <td class="p-2">${esc(w.last_name || w.name || "")}</td>
      <td class="p-2 text-xs">${esc(suppliers.get(w.supplier_id) || "")}</td>
      <td class="p-2">${w.active ? '<span class="text-green-600 text-xs">Active</span>' : '<span class="text-slate-400 text-xs">Inactive</span>'}</td>
      <td class="p-2 text-right space-x-2">
        <button class="text-blue-700 text-xs" data-edit="${esc(w.id)}">Edit</button>
        <button class="text-slate-500 text-xs" data-qr="${esc(w.id)}">QR</button>
      </td>
    </tr>
  `).join("");
  document.querySelectorAll("#workersTable [data-edit]").forEach((btn) => {
    btn.addEventListener("click", () => editWorker(workers.find((w) => w.id === btn.dataset.edit)));
  });
  document.querySelectorAll("#workersTable [data-qr]").forEach((btn) => {
    btn.addEventListener("click", () => {
      window.open(`print-badge.html?ids=${encodeURIComponent(btn.dataset.qr)}`, "_blank");
    });
  });
  const selectAll = document.getElementById("selectAllWorkers");
  if (selectAll) {
    selectAll.checked = false;
    selectAll.onchange = (e) => {
      document.querySelectorAll(".worker-select-checkbox").forEach((cb) => { cb.checked = e.target.checked; });
    };
  }
}

function populateSupplierFilterSelect(elementId, suppliers) {
  const select = document.getElementById(elementId);
  if (!select) return;
  const current = select.value;
  const active = suppliers.filter((s) => s.active);
  select.innerHTML = `<option value="">All suppliers</option>` +
    active.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}${s.is_own_farm ? " (Own fruit)" : ""}</option>`).join("");
  if (current) select.value = current;
}

function printBadges(ids) {
  if (!ids.length) { Boord.toast("No workers selected"); return; }
  window.open(`print-badge.html?ids=${encodeURIComponent(ids.join(","))}`, "_blank");
}

function printSelectedWorkers() {
  const ids = Array.from(document.querySelectorAll(".worker-select-checkbox:checked")).map((cb) => cb.dataset.select);
  printBadges(ids);
}

function printAllWorkers() {
  printBadges((window._workersCache || []).filter((w) => w.active).map((w) => w.id));
}

function printFilteredWorkers() {
  printBadges((window._workersFiltered || []).filter((w) => w.active).map((w) => w.id));
}

function editWorker(worker) {
  const suppliers = (window._suppliersCache || []).filter((s) => s.active);
  openEditModal(worker ? "Edit Worker" : "Add Worker", [
    { key: "id", label: "Employee Number (e.g. 001)", disabled: !!worker },
    { key: "first_name", label: "First Name" },
    { key: "last_name", label: "Last Name" },
    { key: "id_number", label: "ID Number (SA ID or passport)" },
    { key: "whatsapp_number", label: "WhatsApp Number" },
    { key: "supplier_id", label: "Supplier", type: "select",
      options: [{ value: "", label: "(none)" }, ...suppliers.map((s) => ({ value: s.id, label: s.name }))] },
    { key: "photo", label: "Photo (camera or file)", type: "file" },
    { key: "active", label: "Active", type: "checkbox" },
  ], worker || { active: true }, async (values) => {
    const { photo, ...workerValues } = values;
    await Boord.api("/api/workers", {
      method: "POST",
      body: { ...workerValues, supplier_id: workerValues.supplier_id || null, active: !!workerValues.active },
    });
    const fileInput = document.getElementById("editModalFields").querySelector('[data-key="photo"]');
    const file = fileInput && fileInput.files[0];
    if (file) {
      const workerId = worker ? worker.id : workerValues.id;
      const form = new FormData();
      form.append("file", file);
      try {
        await Boord.api(`/api/workers/${encodeURIComponent(workerId)}/photo`, { method: "POST", body: form, isForm: true });
      } catch (e) {
        Boord.toast("Worker saved, but photo upload failed - try again");
        await loadWorkers();
        return;
      }
    }
    Boord.toast("Worker saved");
    await loadWorkers();
  });
}

// Teams
async function loadTeams() {
  const teams = await Boord.api("/api/teams");
  window._teamsCache = teams;
  document.getElementById("teamsTable").innerHTML = teams.map((t) => `
    <tr class="border-b ${t.active ? "" : "opacity-50"}">
      <td class="p-2">${esc(t.id)}</td><td class="p-2">${esc(t.name)}</td><td class="p-2">${esc(t.induna)}</td>
      <td class="p-2">${t.active ? '<span class="text-green-600 text-xs">Active</span>' : '<span class="text-slate-400 text-xs">Inactive</span>'}</td>
      <td class="p-2 text-right"><button class="text-blue-700 text-xs" data-edit="${esc(t.id)}">Edit</button></td>
    </tr>
  `).join("");
  document.querySelectorAll("#teamsTable [data-edit]").forEach((btn) => {
    btn.addEventListener("click", () => editTeam(teams.find((t) => t.id === btn.dataset.edit)));
  });
}

function editTeam(team) {
  openEditModal(team ? "Edit Team" : "Add Team", [
    { key: "id", label: "Id (e.g. A)", disabled: !!team },
    { key: "name", label: "Name" },
    { key: "induna", label: "Induna" },
    { key: "active", label: "Active", type: "checkbox" },
  ], { active: true, ...team }, async (values) => {
    await Boord.api("/api/teams", { method: "POST", body: { ...values, active: !!values.active } });
    Boord.toast("Team saved");
    await loadTeams();
  });
}

// The supplier name for an id, from the cached list; blank for none/unknown.
function supplierName(id) {
  if (id == null || id === "") return "";
  const s = (window._suppliersCache || []).find((x) => x.id == id);
  return s ? s.name : "";
}

// Blocks
async function loadBlocks() {
  const blocks = await Boord.api("/api/blocks");
  // The crate and slip editors' Block pickers read this. It was never
  // filled, so Edit Crate offered only the crate's own block.
  window._blocksCache = blocks;
  document.getElementById("blocksTable").innerHTML = blocks.map((b) => `
    <tr class="border-b ${b.active ? "" : "opacity-50"}">
      <td class="p-2">${esc(b.id)}</td><td class="p-2">${esc(b.variety)}</td><td class="p-2">${esc(b.trees)}</td>
      <td class="p-2">${esc(b.hectares)}</td>
      <td class="p-2 text-xs">${esc(supplierName(b.supplier_id))}</td>
      <td class="p-2">${b.active ? '<span class="text-green-600 text-xs">Active</span>' : '<span class="text-slate-400 text-xs">Inactive</span>'}</td>
      <td class="p-2 text-right"><button class="text-blue-700 text-xs" data-edit="${esc(b.id)}">Edit</button></td>
    </tr>
  `).join("");
  document.querySelectorAll("#blocksTable [data-edit]").forEach((btn) => {
    btn.addEventListener("click", () => editBlock(blocks.find((b) => b.id === btn.dataset.edit)));
  });
}

function editBlock(block) {
  const suppliers = (window._suppliersCache || []).filter((s) => s.active);
  openEditModal(block ? "Edit Block" : "Add Block", [
    { key: "id", label: "Block Id", disabled: !!block },
    { key: "name", label: "Name" },
    { key: "variety", label: "Variety" },
    { key: "trees", label: "Trees", type: "number" },
    { key: "hectares", label: "Hectares", type: "number" },
    { key: "supplier_id", label: "Supplier", type: "select",
      options: [{ value: "", label: "(none)" }, ...suppliers.map((s) => ({ value: s.id, label: s.name }))] },
    { key: "active", label: "Active", type: "checkbox" },
  ], { active: true, ...block }, async (values) => {
    await Boord.api("/api/blocks", {
      method: "POST",
      body: {
        ...values,
        trees: parseInt(values.trees) || 0,
        hectares: parseFloat(values.hectares) || 0,
        supplier_id: values.supplier_id ? parseInt(values.supplier_id) : null,
        active: !!values.active,
      },
    });
    Boord.toast("Block saved");
    await loadBlocks();
  });
}

// Devices
async function loadDevices() {
  const devices = await Boord.api("/api/devices");
  document.getElementById("devicesTable").innerHTML = devices.map((d) => `
    <tr class="border-b">
      <td class="p-2">${esc(d.id)}</td><td class="p-2">${esc(d.role)}</td><td class="p-2">${esc(d.station)}</td><td class="p-2">${esc(d.team_id || "")}</td>
      <td class="p-2 text-xs">${d.role === "field" ? esc(supplierName(d.supplier_id) || "Own fruit") : ""}</td>
      <td class="p-2">${Boord.fmtDateTime(d.last_seen, "never")}</td>
      <td class="p-2 text-right"><button class="text-blue-700 text-xs" data-edit="${esc(d.id)}">Edit</button></td>
    </tr>
  `).join("");
  document.querySelectorAll("#devicesTable [data-edit]").forEach((btn) => {
    btn.addEventListener("click", () => editDevice(devices.find((d) => d.id === btn.dataset.edit)));
  });
}

function editDevice(device) {
  const teams = window._teamsCache || [];
  const suppliers = (window._suppliersCache || []).filter((s) => s.active);
  openEditModal(device ? "Edit Device" : "Add Device", [
    { key: "id", label: "Device Id (e.g. device-01)", disabled: !!device },
    { key: "role", label: "Role", type: "select", options: [
      { value: "field", label: "Field" },
      { value: "packhouse", label: "Receiving" },
      { value: "admin", label: "Admin" },
    ] },
    { key: "station", label: "Station name" },
    { key: "team_id", label: "Team", type: "select", options: [{ value: "", label: "(none)" }, ...teams.filter((t) => t.active).map((t) => ({ value: t.id, label: t.name }))] },
    { key: "supplier_id", label: "Supplier (field devices - whose fruit this device picks)", type: "select",
      options: [{ value: "", label: "(own fruit)" }, ...suppliers.map((s) => ({ value: s.id, label: s.name }))] },
    { key: "induna", label: "Induna" },
    { key: "data_capturer", label: "Data Capturer" },
  ], device || { role: "field" }, async (values) => {
    await Boord.api("/api/devices", {
      method: "POST",
      body: { ...values, supplier_id: values.supplier_id ? parseInt(values.supplier_id) : null, active: true },
    });
    Boord.toast("Device saved");
    await loadDevices();
  });
}

// Suppliers
async function loadSuppliers() {
  const suppliers = await Boord.api("/api/suppliers");
  window._suppliersCache = suppliers;
  document.getElementById("suppliersTable").innerHTML = suppliers.map((s) => `
    <tr class="border-b ${s.active ? "" : "opacity-50"}">
      <td class="p-2">${esc(s.name)}${s.is_own_farm ? ' <span class="text-xs text-blue-700 font-semibold">(Own fruit)</span>' : ""}</td>
      <td class="p-2 text-xs">${esc(s.contact_name || "")}${s.contact_phone ? ` - ${esc(s.contact_phone)}` : ""}</td>
      <td class="p-2 text-xs">${s.packing_rate_per_kg > 0 ? `${Boord.fmtRand(s.packing_rate_per_kg)}/kg` : s.packing_rate_per_crate > 0 ? `${Boord.fmtRand(s.packing_rate_per_crate)}/crate` : "-"}</td>
      <td class="p-2">${s.active ? '<span class="text-green-600 text-xs">Active</span>' : '<span class="text-slate-400 text-xs">Inactive</span>'}</td>
      <td class="p-2 text-right"><button class="text-blue-700 text-xs" data-edit="${esc(s.id)}">Edit</button></td>
    </tr>
  `).join("");
  document.querySelectorAll("#suppliersTable [data-edit]").forEach((btn) => {
    btn.addEventListener("click", () => editSupplier(suppliers.find((s) => s.id == btn.dataset.edit)));
  });
  populateBillingSupplierSelect(suppliers);
  populateSupplierFilterSelect("workerSupplierFilter", suppliers);
  populateSupplierFilterSelect("paySupplierFilter", suppliers);
  populateSupplierFilterSelect("dashSupplierFilter", suppliers);
  populateSupplierFilterSelect("reportsSupplierFilter", suppliers);
  if (window._workersCache) renderWorkersTable(); // resolve supplier names if workers loaded first
}

function editSupplier(supplier) {
  openEditModal(supplier ? "Edit Supplier" : "Add Supplier", [
    { key: "name", label: "Name" },
    { key: "contact_name", label: "Contact Name" },
    { key: "contact_phone", label: "Contact Phone" },
    { key: "contact_email", label: "Contact Email" },
    { key: "puc", label: "PUC (Product Unit Code)" },
    { key: "global_gap_number", label: "GlobalG.A.P. Number (GGN)" },
    { key: "packing_rate_per_kg", label: "Packing Rate (R/kg)", type: "number" },
    { key: "packing_rate_per_crate", label: "Packing Rate (R/crate, used if R/kg is 0)", type: "number" },
    { key: "active", label: "Active", type: "checkbox" },
  ], supplier || { active: true }, async (values) => {
    await Boord.api("/api/suppliers", {
      method: "POST",
      body: {
        ...values,
        id: supplier ? supplier.id : undefined,
        is_own_farm: supplier ? supplier.is_own_farm : false,
        packing_rate_per_kg: parseFloat(values.packing_rate_per_kg) || 0,
        packing_rate_per_crate: parseFloat(values.packing_rate_per_crate) || 0,
        active: !!values.active,
      },
    });
    Boord.toast("Supplier saved");
    await loadSuppliers();
  });
}

// ---------------------------------------------------------------------
// Facility Billing
// ---------------------------------------------------------------------
function populateBillingSupplierSelect(suppliers) {
  const select = document.getElementById("billingSupplierSelect");
  const current = select.value;
  const external = suppliers.filter((s) => s.active && !s.is_own_farm);
  select.innerHTML = external.length
    ? external.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")
    : `<option value="">(no external suppliers yet)</option>`;
  if (current) select.value = current;
}

function bindSuppliers() {
  const today = Boord.localDateStr();
  document.getElementById("billingStart").value = today;
  document.getElementById("billingEnd").value = today;
  document.getElementById("calcBillingBtn").addEventListener("click", calculateBilling);
}

async function calculateBilling() {
  const supplierId = document.getElementById("billingSupplierSelect").value;
  const start = document.getElementById("billingStart").value;
  const end = document.getElementById("billingEnd").value;
  if (!supplierId) { Boord.toast("Add an external supplier first"); return; }
  try {
    const data = await Boord.api(`/api/suppliers/${supplierId}/billing?period_start=${start}&period_end=${end}`);
    const summaryEl = document.getElementById("billingSummary");
    summaryEl.textContent = `${data.lots.length} lot${data.lots.length === 1 ? "" : "s"} - ${data.total_crates} crates - ${Boord.fmtKg(data.total_kg)} - Rate: ${Boord.fmtRand(data.rate)} ${data.rate_type === "per_kg" ? "/kg" : "/crate"} - Amount Due: ${Boord.fmtRand(data.amount_due)}`;
    summaryEl.classList.remove("hidden");
    document.getElementById("billingTable").innerHTML = data.lots.map((l) => `
      <tr class="border-b">
        <td class="p-2 font-mono">${esc(l.slip_number)}</td>
        <td class="p-2">${Boord.fmtDateTime(l.received_at)}</td>
        <td class="p-2">${l.crates}</td>
        <td class="p-2">${Boord.fmtNum(l.kg)}</td>
      </tr>
    `).join("") || `<tr><td class="p-2 text-slate-400" colspan="4">No received lots in this period</td></tr>`;
  } catch (e) {
    Boord.toast("Could not calculate billing");
  }
}

// ---------------------------------------------------------------------
// Payments
// ---------------------------------------------------------------------
function bindPayments() {
  const today = Boord.localDateStr();
  document.getElementById("payStart").value = today;
  document.getElementById("payEnd").value = today;
  document.getElementById("calcPayBtn").addEventListener("click", calculatePayments);
  document.getElementById("exportPayBtn").addEventListener("click", exportPayments);

  Boord.bindDateRangePresets({
    todayBtn: document.getElementById("payTodayBtn"),
    weekBtn: document.getElementById("payWeekBtn"),
    seasonBtn: document.getElementById("paySeasonBtn"),
    startInput: document.getElementById("payStart"),
    endInput: document.getElementById("payEnd"),
    seasonAnchor: () => ({
      month: (_systemSettings && _systemSettings.season_start_month) || 1,
      day: (_systemSettings && _systemSettings.season_start_day) || 1,
    }),
  });
}

async function calculatePayments() {
  const start = document.getElementById("payStart").value;
  const end = document.getElementById("payEnd").value;
  const supplierId = document.getElementById("paySupplierFilter").value;
  const supplierParam = supplierId ? `&supplier_id=${supplierId}` : "";
  try {
    const payments = await Boord.api(`/api/payments/calculate?period_start=${start}&period_end=${end}${supplierParam}`, { method: "POST" });
    renderPayments(payments);
  } catch (e) {
    // A new install has no wage rate and the server refuses to calculate
    // until one is set. Without this the rejection went nowhere and the
    // button simply appeared to do nothing.
    Boord.toast(Boord.isNetworkError(e)
      ? "No connection - could not calculate wages"
      : Boord.errorDetail(e, "Could not calculate wages"));
  }
}

function supplierNameForWorker(worker, suppliers) {
  const own = suppliers.find((s) => s.is_own_farm);
  if (!worker || worker.supplier_id == null || (own && worker.supplier_id === own.id)) {
    return own ? own.name : "Own fruit";
  }
  const supplier = suppliers.find((s) => s.id === worker.supplier_id);
  return supplier ? supplier.name : "Unknown";
}

function renderPayments(payments) {
  const workers = new Map((window._workersCache || []).map((w) => [w.id, w]));
  const suppliers = window._suppliersCache || [];
  const ownName = (suppliers.find((s) => s.is_own_farm) || {}).name;

  const groups = new Map();
  for (const p of payments) {
    const name = supplierNameForWorker(workers.get(p.worker_id), suppliers);
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(p);
  }
  const groupNames = Array.from(groups.keys()).sort((a, b) => {
    if (a === ownName) return -1;
    if (b === ownName) return 1;
    return a.localeCompare(b);
  });

  document.getElementById("paymentsTable").innerHTML = groupNames.map((name) => {
    const groupPayments = groups.get(name);
    const totalKg = groupPayments.reduce((sum, p) => sum + p.total_kg, 0);
    const totalWages = groupPayments.reduce((sum, p) => sum + p.amount_due, 0);
    const summaryRow = `
      <tr class="bg-slate-100 font-semibold">
        <td class="p-2" colspan="5">${esc(name)} - ${groupPayments.length} worker${groupPayments.length === 1 ? "" : "s"} - ${Boord.fmtKg(totalKg)} - ${Boord.fmtRand(totalWages)} total wages</td>
      </tr>
    `;
    const rows = groupPayments.map((p) => {
      const w = workers.get(p.worker_id);
      const displayName = w ? (w.name || `${w.first_name} ${w.last_name}`.trim() || w.id) : p.worker_id;
      return `
        <tr class="border-b">
          <td class="p-2 text-xs text-slate-500">${esc(name)}</td>
          <td class="p-2">${esc(displayName)}</td>
          <td class="p-2">${Boord.fmtNum(p.total_kg)}</td>
          <td class="p-2">${Boord.fmtRand(p.rate_applied)}/kg</td>
          <td class="p-2">${Boord.fmtRand(p.amount_due)}</td>
        </tr>
      `;
    }).join("");
    return summaryRow + rows;
  }).join("");
}

async function exportPayments() {
  const start = document.getElementById("payStart").value;
  const end = document.getElementById("payEnd").value;
  const supplierId = document.getElementById("paySupplierFilter").value;
  const supplierParam = supplierId ? `&supplier_id=${supplierId}` : "";
  await exportFile(`/api/payments/export?period_start=${start}&period_end=${end}${supplierParam}&fmt=xlsx`,
                   `Wages_${start}_${end}.xlsx`);
}

// ---------------------------------------------------------------------
// Reports
// ---------------------------------------------------------------------
// Grouped by where the work happens - field, pack house, market - each group
// a grid of cards under its own heading on the Reports tab, in this order.
// `short` is the line on the card; the full `desc` is its tooltip, since a
// paragraph on every card made the grid a wall of text.
const REPORTS = [
  { key: "daily-harvest", label: "Daily Harvest Summary", icon: "fa-sun", color: "text-amber-500", group: "harvest",
    short: "Crates and kg by block and team, for one day",
    desc: "Crates and kg by block and team for one day. Uses the period start date even if a wider range is picked.",
    single_day: true },
  { key: "harvesting-list", label: "Harvesting List", icon: "fa-seedling", color: "text-green-600", group: "harvest",
    short: "Loads still being picked",
    desc: "Loads still being picked, matching the Dashboard's Harvesting list." },
  { key: "picking-notes", label: "Plukstrokies / Picking Notes", icon: "fa-clipboard-list", color: "text-sky-600", group: "harvest",
    short: "The paper picking slip, one row per lot",
    desc: "One row per dispatched lot - slip, block(s), crates sent vs received, driver, supplier, condition, weather - matching the paper picking slip." },
  { key: "team-picking-list", label: "Span Pluklys / Team Picking List", icon: "fa-people-group", color: "text-indigo-500", group: "harvest",
    short: "Each team's day: workers, blocks and lots",
    desc: "One row per team per day - data capturer, induna, worker count, deductions, plus each block picked and each lot dispatched that day." },
  { key: "harvest-data", label: "Daaglikse Oesdata / Daily Harvest Data", icon: "fa-table-cells", color: "text-teal-600", group: "harvest",
    short: "Kg per day against each block",
    desc: "Kg by date (rows) against block (columns) over the range, with per-day and per-block totals including avg per tree and per hectare." },
  { key: "block-harvest", label: "Block Harvest Report", icon: "fa-tree", color: "text-emerald-700", group: "harvest",
    short: "Crates, kg and kg per tree per block",
    desc: "Per-block crates, kg, average kg per crate and average kg per tree over the range." },
  { key: "worker-harvest", label: "Worker Harvest Report", icon: "fa-users", color: "text-violet-500", group: "harvest",
    short: "Crates, kg and pay per worker",
    desc: "Per-worker crates, kg, amount due and average kg per crate over the range." },
  { key: "litchi-wages", label: "Lietsjie Lone / Litchi Wages", icon: "fa-hand-holding-dollar", color: "text-lime-600", group: "harvest",
    short: "A pay period per worker, crates per day",
    desc: "One row per worker with crates broken out per day worked, plus a total - a whole pay period on a single line." },
  { key: "in-transit-list", label: "In Transit List", icon: "fa-truck-fast", color: "text-orange-500", group: "packhouse",
    short: "Loads on the way to the pack house",
    desc: "Loads dispatched from the field but not yet received at the pack house." },
  { key: "lot-receiving", label: "Lot & Receiving Report", icon: "fa-truck", color: "text-sky-600", group: "packhouse",
    short: "Lots dispatched, with their receiving detail",
    desc: "Every lot dispatched in the range, with its receiving detail once the truck has been checked in." },
  { key: "received-list", label: "Pakhuis Ontvangstes / Pack House Receivables", icon: "fa-warehouse", color: "text-red-700", group: "packhouse",
    short: "Loads received, as on the paper list",
    desc: "Received loads - slip, date/time, receiving block, supplier, team, driver, crates, kg and rejected waste kg - matching the paper receipt list." },
  { key: "pallets-stacked", label: "Pallets Stacked", icon: "fa-pallet", color: "text-yellow-700", group: "packhouse",
    short: "Every pallet stacked in Packhouse",
    desc: "Every pallet stacked in Boord Packhouse's Pallet Stack in the range - number, barcode, supplier, market, cartons, kg, contents and the delivery note it went out on." },
  { key: "delivery-notes", label: "Afleweringsnotas / Delivery Notes", icon: "fa-file-lines", color: "text-slate-500", group: "packhouse",
    short: "Notes loaded: pallets, cartons and kg",
    desc: "Delivery notes loaded in the range - receiver, transporter, seal, cooled-to, pallets, cartons and kg. Not filtered by supplier." },
  // Markets & Transport: built from the delivery notes on the Markets tab.
  // By the date each note was loaded; never by supplier.
  { key: "market-summary", label: "Markte Opsomming / Market Summary", icon: "fa-store", color: "text-pink-600", group: "markets",
    short: "Sent, sold and paid per receiver and market",
    desc: "One row per receiver and market - notes, pallets, cartons sent and sold, kg, gross, commission, deductions, net paid, R/carton, R/kg and transport, with a total." },
  { key: "market-packs", label: "Market Sales by Package Type", icon: "fa-boxes-stacked", color: "text-amber-600", group: "markets",
    short: "Cartons and price per package type",
    desc: "Per receiver, market and pack - cartons sent, sold and outstanding, average price per carton and gross R/kg." },
  { key: "market-sales", label: "Consignment Sales", icon: "fa-file-invoice-dollar", color: "text-green-600", group: "markets",
    short: "What went, sold and was paid, per note",
    desc: "One row per delivery note - what went, what sold at what price, and what the market paid after costs." },
  { key: "transport-summary", label: "Vervoer Opsomming / Transport Summary", icon: "fa-truck-moving", color: "text-blue-600", group: "markets",
    short: "Transport cost per delivery note",
    desc: "One row per delivery note - transporter, waybill, invoice, pallets, R/pallet and cost excluding and including VAT, with a total." },
  // The Markets tab's quick print reports, for the dates here: `print` names
  // the printMarketReport kind, and they open the print dialog, not a .xlsx.
  { key: "market-by-receiver", label: "Markets by Receiver", icon: "fa-user-tie", color: "text-purple-600", group: "markets", print: "receiver",
    short: "Notes grouped per receiver",
    desc: "The delivery notes grouped by receiver - pallets, sent, sold, unsold, gross, net paid and transport - with subtotals and a total. Prints landscape; Save as PDF in the print dialog." },
  { key: "market-by-market", label: "Markets by Market", icon: "fa-map-location-dot", color: "text-rose-600", group: "markets", print: "market",
    short: "Notes grouped per market",
    desc: "The delivery notes grouped by market, with subtotals and a total. Prints landscape; Save as PDF in the print dialog." },
  { key: "market-by-transporter", label: "Markets by Transporter", icon: "fa-truck-ramp-box", color: "text-cyan-600", group: "markets", print: "transporter",
    short: "Notes grouped per transporter",
    desc: "The delivery notes grouped by transporter, with subtotals and a total. Prints landscape; Save as PDF in the print dialog." },
  { key: "market-sold", label: "Markets Sold", icon: "fa-handshake", color: "text-emerald-600", group: "markets", print: "sold",
    short: "What sold, per note and pack",
    desc: "Every pack that sold, per delivery note - cartons, bins or crates sold, price and gross, with a total. Prints landscape; Save as PDF in the print dialog." },
  { key: "market-unsold", label: "Markets Unsold", icon: "fa-box-open", color: "text-orange-600", group: "markets", print: "unsold",
    short: "What is still outstanding",
    desc: "Every pack still outstanding, per delivery note; a note with no statement in counts as all unsold. Prints landscape; Save as PDF in the print dialog." },
];

// The query string for one report. Every report takes the same
// period_start/period_end pair (and the optional supplier filter) except the
// `single_day` ones, whose export ignores the period end and only ever covers
// the start date as `day`, no matter how wide a range is picked above.
function reportParams(report, d1, d2, supplierId) {
  const qs = new URLSearchParams(report.single_day
    ? { day: d1 }
    : { period_start: d1, period_end: d2 });
  if (supplierId) qs.set("supplier_id", supplierId);
  return qs.toString();
}

function renderReportsGrid() {
  const d1 = document.getElementById("reportDate1").value;
  const d2 = document.getElementById("reportDate2").value;
  const isRange = d1 && d2 && d1 !== d2;

  const card = (r) => {
    const flagDailyOnly = isRange && r.single_day;
    const note = flagDailyOnly
      ? `<div class="text-xs text-amber-600 font-medium mt-1"><i class="fa-solid fa-triangle-exclamation mr-1"></i>Daily report only - uses ${d1} (period start)</div>`
      : `<div class="text-xs text-slate-400">${r.print ? "Print / PDF" : "Download .xlsx"}</div>`;
    return `
    <button class="bg-white rounded-xl shadow p-4 hover:bg-slate-50 flex flex-col items-center gap-2 text-center" data-report="${r.key}" title="${esc(r.desc)}">
      <i class="fa-solid ${r.icon} text-3xl ${r.color}"></i>
      <div class="font-semibold text-sm">${r.label}</div>
      <div class="text-xs text-slate-500">${r.short}</div>
      ${note}
    </button>
  `;
  };
  document.querySelectorAll("[data-report-group]").forEach((grid) => {
    grid.innerHTML = REPORTS.filter((r) => r.group === grid.dataset.reportGroup).map(card).join("");
  });
  document.querySelectorAll("[data-report]").forEach((btn) => {
    btn.addEventListener("click", () => downloadReport(btn.dataset.report));
  });
}

function bindReports() {
  const today = Boord.localDateStr();
  document.getElementById("reportDate1").value = today;
  document.getElementById("reportDate2").value = today;

  Boord.bindDateRangePresets({
    todayBtn: document.getElementById("reportsTodayBtn"),
    weekBtn: document.getElementById("reportsWeekBtn"),
    seasonBtn: document.getElementById("setSeasonDatesBtn"),
    startInput: document.getElementById("reportDate1"),
    endInput: document.getElementById("reportDate2"),
    seasonAnchor: () => ({
      month: (_systemSettings && _systemSettings.season_start_month) || 1,
      day: (_systemSettings && _systemSettings.season_start_day) || 1,
    }),
    onChange: renderReportsGrid,
  });
  document.getElementById("reportDate1").addEventListener("change", renderReportsGrid);
  document.getElementById("reportDate2").addEventListener("change", renderReportsGrid);

  renderReportsGrid();
}

async function downloadReport(key) {
  const report = REPORTS.find((r) => r.key === key);
  const d1 = document.getElementById("reportDate1").value;
  const d2 = document.getElementById("reportDate2").value;
  const supplierId = document.getElementById("reportsSupplierFilter").value;
  if (!d1 || !d2) { Boord.toast("Pick both dates first"); return; }
  if (report.print) { printMarketReportFor(report.print, d1, d2); return; }
  try {
    const blob = await Boord.api(`/api/reports/${key}?${reportParams(report, d1, d2, supplierId)}`, { timeoutMs: Boord.UPLOAD_TIMEOUT_MS });
    Boord.downloadBlob(blob, `${report.label.replace(/[^a-zA-Z0-9]+/g, "_")}.xlsx`);
  } catch (e) {
    console.error("Report generation failed:", e);
    if (Boord.isAuthError(e)) {
      accessRefused(e);
    } else {
      Boord.toast("Could not generate report - see browser console for details");
    }
  }
}

// ---------------------------------------------------------------------
// Planner: the week's harvest plan. One screen, three sources - the owner's
// estimate (Boord Owner), what has been picked (this app) and what the
// receivers have had and are waiting for (Boord Packhouse) - and one record
// of our own, the plan, which saves itself as the office types. The server
// (backend/routers/planner.py) fetches and derives everything; the copy of
// the arithmetic below (plannerDerive, a port of backend/planner.py's
// derive) only keeps the figures moving while a number is being typed, so
// the screen never waits for a round trip to show a total. The saved answer
// is always the server's.
// ---------------------------------------------------------------------
let _plan = null;          // GET /api/planner/week, the whole view
let _planSaveTimer = null;
let _planPacksTimer = null;
let _planPending = false;  // an edit not yet sent

const PLAN_GROUP_LABEL = { usa: "USA", eu: "EU", local: "Local" };
const PLAN_GROUP_CLASS = { usa: "bg-blue-50 text-blue-800", eu: "bg-violet-50 text-violet-800", local: "bg-green-50 text-green-800" };

function plannerPct(part, whole) {
  if (whole == null || !whole) return null;
  return Math.round(100 * (part || 0) / whole);
}

function plannerSpread(available, days, fixed) {
  const ticked = [...new Set(days.filter((d) => d >= 0 && d <= 6))].sort();
  const out = {};
  let fixedTotal = 0;
  const free = [];
  for (const d of ticked) {
    if (fixed[d] != null) { out[d] = Math.round(Number(fixed[d])); fixedTotal += out[d]; }
    else free.push(d);
  }
  if (free.length) {
    const share = Math.max(0, (Number(available) || 0) - fixedTotal) / free.length;
    for (const d of free) out[d] = Math.round(share);
  }
  return out;
}

// backend/planner.py derive(), line for line. Change the two together.
function plannerDerive(view) {
  const r1 = (x) => Math.round(x);   // whole numbers, as backend/planner.py
  const blocks = [];
  let harvest = 0;
  const dayTotals = [0, 0, 0, 0, 0, 0, 0];
  for (const b of view.blocks) {
    const est = b.estimate_kg;
    const ytd = b.ytd_kg || 0;
    const remaining = est != null ? r1(est - ytd) : null;
    let available = b.available_kg;
    if (available == null) available = remaining != null ? Math.max(0, remaining) : 0;
    const fixed = {};
    for (const [k, v] of Object.entries(b.fixed || {})) fixed[parseInt(k, 10)] = v;
    const perDay = b.on ? plannerSpread(available, b.days || [], fixed) : {};
    const total = r1(Object.values(perDay).reduce((s, x) => s + x, 0));
    if (b.on) { harvest += total; for (const [d, kg] of Object.entries(perDay)) dayTotals[d] += kg; }
    blocks.push({
      block_id: b.block_id, remaining_kg: remaining, remaining_pct: plannerPct(remaining, est),
      ytd_pct: plannerPct(ytd, est), last_week_pct: plannerPct(b.last_week_kg, est),
      available_kg: r1(available), available_pct: remaining ? plannerPct(available, remaining) : null,
      per_day: perDay, total_kg: total,
    });
  }
  const packs = {};
  for (const p of view.packs || []) packs[p.name] = p;
  const deliveries = view.plan_deliveries || {};
  const receivers = [];
  const packTotals = {};
  for (const name of Object.keys(packs)) packTotals[name] = { cartons: 0, kg: 0, pallets: 0 };
  let dKg = 0, dCartons = 0, dPallets = 0;
  for (const r of view.receivers || []) {
    const rid = String(r.id);
    const cells = {};
    let rc = 0, rk = 0, rp = 0;
    for (const [name, p] of Object.entries(packs)) {
      const cartons = parseInt((deliveries[rid] || {})[name] || 0, 10) || 0;
      const units = cartons * (p.units_per_carton || 1);   // Packhouse's unit: a Pre-pac punnet
      const kg = r1(units * (p.carton_kg || 0));
      const perPallet = p.cartons_per_pallet || 0;
      // Whole pallets, rounded up: a part-filled pallet still travels.
      const pallets = perPallet ? (cartons ? Math.ceil(cartons / perPallet) : 0) : null;
      cells[name] = { cartons, units, kg, pallets };
      rc += cartons; rk += kg; rp += pallets || 0;
      packTotals[name].cartons += cartons; packTotals[name].kg += kg; packTotals[name].pallets += pallets || 0;
    }
    receivers.push({ id: r.id, cells, cartons: rc, kg: r1(rk), pallets: rp });
    dCartons += rc; dKg += rk; dPallets += rp;
  }
  for (const t of Object.values(packTotals)) { t.kg = r1(t.kg); }
  const loss = view.loss || {};
  let effLoss = loss.override_pct;
  if (effLoss == null) effLoss = loss.season_pct;
  if (effLoss == null) effLoss = 0;
  harvest = r1(harvest);
  const deliverable = r1(harvest * (1 - effLoss / 100));
  dKg = r1(dKg);
  return {
    blocks, day_totals: dayTotals.map(r1), harvest_kg: harvest, receivers, pack_totals: packTotals,
    delivery_cartons: dCartons, delivery_kg: dKg, delivery_pallets: dPallets,
    loss_pct: r1(effLoss), deliverable_kg: deliverable, balance_kg: r1(deliverable - dKg),
  };
}

function bindPlanner() {
  document.getElementById("plannerSeason").addEventListener("change", (e) => {
    flushPlannerSave();
    loadPlanner({ season: e.target.value });
  });
  document.getElementById("plannerWeek").addEventListener("change", (e) => {
    flushPlannerSave();
    loadPlanner({ week_start: e.target.value });
  });
  const step = (n) => {
    if (!_plan) return;
    const idx = _plan.weeks.findIndex((w) => w.week_start === _plan.week_start);
    const next = _plan.weeks[idx + n];
    flushPlannerSave();
    if (next) loadPlanner({ week_start: next.week_start });
    else loadPlanner({ week_start: Boord.localDateStr(plannerAddDays(new Date(_plan.week_start), 7 * n)) });
  };
  document.getElementById("plannerPrevWeek").addEventListener("click", () => step(-1));
  document.getElementById("plannerNextWeek").addEventListener("click", () => step(1));
  document.getElementById("plannerThisWeek").addEventListener("click", () => { flushPlannerSave(); loadPlanner({}); });
  document.getElementById("plannerExportXlsx").addEventListener("click", () => exportPlanner("xlsx"));
  document.getElementById("plannerExportPdf").addEventListener("click", () => exportPlanner("pdf"));
  // The figures (crates picked, notes loaded, a new estimate) change from
  // outside, so the tab fetches again each time it opens, keeping the week.
  document.querySelector('.tab-btn[data-tab="planner"]').addEventListener("click", () => {
    loadPlanner(_plan ? { week_start: _plan.week_start } : {});
  });
  // An edit still waiting its 800 ms when the page closes goes out now.
  window.addEventListener("pagehide", flushPlannerSave);
}

function plannerAddDays(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; }

function plannerDateLabel(iso, withYear = false) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-ZA", withYear ? { day: "numeric", month: "short", year: "numeric" } : { day: "numeric", month: "short" });
}

async function loadPlanner({ week_start, season } = {}) {
  const err = document.getElementById("plannerError");
  const params = week_start ? `?week_start=${week_start}` : season ? `?season=${season}` : "";
  try {
    _plan = await Boord.api(`/api/planner/week${params}`);
  } catch (e) {
    err.textContent = Boord.errorDetail(e, "The planner could not be loaded");
    err.classList.remove("hidden");
    return;
  }
  err.classList.add("hidden");
  _planPending = false;
  renderPlanner();
}

function renderPlanner() {
  const v = _plan;
  // Season and week pickers.
  const seasonSel = document.getElementById("plannerSeason");
  seasonSel.innerHTML = v.seasons.map((y) => `<option value="${y}" ${y === v.season_year ? "selected" : ""}>${y} season</option>`).join("");
  const weekSel = document.getElementById("plannerWeek");
  // ISO weeks, Monday to Sunday, each option with its dates; a dot marks a
  // week with a plan saved.
  weekSel.innerHTML = v.weeks.map((w) => {
    const end = Boord.localDateStr(plannerAddDays(new Date(w.week_start + "T00:00:00"), 6));
    const marks = `${w.has_plan ? "● " : ""}${w.current ? "(this week) " : ""}`;
    return `<option value="${w.week_start}" ${w.week_start === v.week_start ? "selected" : ""}>${marks}Week ${w.iso_week} · ${plannerDateLabel(w.week_start)} – ${plannerDateLabel(end)}</option>`;
  }).join("");
  // Where the figures come from, and what is missing.
  const src = [];
  if (v.estimate.available) src.push(`Estimate: ${esc(v.estimate.name || "latest")} (Boord Owner, ${Boord.fmtNum(v.estimate.total_kg, 0)} kg)`);
  else src.push(`<span class="text-amber-700"><i class="fa-solid fa-triangle-exclamation"></i> ${esc(v.estimate.reason)}</span>`);
  if (!v.packhouse.available) src.push(`<span class="text-amber-700"><i class="fa-solid fa-triangle-exclamation"></i> ${esc(v.packhouse.reason)}</span>`);
  document.getElementById("plannerSources").innerHTML = src.join("<br>");
  renderPlannerSaveState();
  renderPlannerHarvest();
  renderPlannerDelivery();
  refreshPlannerDerived();
}

function renderPlannerSaveState() {
  const el = document.getElementById("plannerSaveState");
  if (_planPending) { el.textContent = "Saving…"; return; }
  el.textContent = _plan && _plan.updated_at ? `Saved ${Boord.fmtDateTime(_plan.updated_at)}` : "Not saved yet";
}

// ----- Harvest grid -----

function renderPlannerHarvest() {
  const v = _plan;
  const today = v.today;
  // Day headings carry their date; today's column is marked.
  document.querySelectorAll("#plannerHarvest thead th[data-day]").forEach((th) => {
    const d = parseInt(th.dataset.day, 10);
    const iso = Boord.localDateStr(plannerAddDays(new Date(v.week_start + "T00:00:00"), d));
    const isToday = iso === today;
    th.innerHTML = `<div class="text-base">${"MTWTFSS"[d]}</div><div class="text-[10px] font-normal ${isToday ? "text-blue-700 font-semibold" : "text-slate-400"}">${plannerDateLabel(iso)}</div>`;
    th.classList.toggle("bg-blue-50", isToday);
  });
  const rows = document.getElementById("plannerHarvestRows");
  const kg = (x) => (x == null ? "–" : Boord.fmtNum(x, 0));
  // Every block of the farm's own is listed; the checkbox in front of its
  // name says whether it is picked this week. An unticked block keeps its
  // figures on screen (the office can see what remains there) but is greyed,
  // its days are hidden and nothing of it reaches the totals.
  rows.innerHTML = v.blocks.length ? v.blocks.map((b) => {
    const id = esc(b.block_id);
    const dis = b.on ? "" : "disabled";
    const days = [0, 1, 2, 3, 4, 5, 6].map((d) => {
      const ticked = b.on && (b.days || []).includes(d);
      const fixed = b.fixed && b.fixed[String(d)] != null;
      const actual = (b.actual || [])[d] || 0;
      return `<td class="p-1 text-center align-top ${ticked ? "" : "planner-day-off"}">
        <input type="checkbox" data-plan-day="${id}:${d}" ${ticked ? "checked" : ""} ${dis} title="Pick this block on this day">
        <div class="${ticked ? "" : "hidden"}"><input type="number" min="0" step="1" inputmode="numeric" class="${fixed ? "planner-fixed" : ""}" data-plan-daykg="${id}:${d}" title="kg to pick. Type a figure to hold it; clear it to share the rest again"></div>
        <div class="planner-actual ${actual && !ticked ? "planner-offplan" : ""}" data-plan-actual="${id}:${d}" title="${actual && !ticked ? "Picked on a day this block was not planned for" : ""}">${actual ? `picked ${Boord.fmtNum(actual, 0)}` : "&nbsp;"}</div>
      </td>`;
    }).join("");
    return `<tr class="border-b ${b.on ? "" : "planner-block-off"}" data-plan-row="${id}">
      <td class="p-2 font-medium whitespace-nowrap">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" class="w-5 h-5" data-plan-on="${id}" ${b.on ? "checked" : ""} title="Harvest this block this week">
          <span>${esc(b.name)}${b.variety ? `<div class="text-xs text-slate-400 font-normal">${esc(b.variety)}</div>` : ""}</span>
        </label>
      </td>
      <td class="p-2 text-right">${kg(b.estimate_kg)}</td>
      <td class="p-2 text-right">${kg(b.ytd_kg)}<div class="text-xs text-slate-400" data-plan-cell="ytd_pct:${id}"></div></td>
      <td class="p-2 text-right">${kg(b.last_week_kg)}<div class="text-xs text-slate-400" data-plan-cell="last_week_pct:${id}"></div></td>
      <td class="p-2 text-right"><span data-plan-cell="remaining_kg:${id}"></span><div class="text-xs text-slate-400" data-plan-cell="remaining_pct:${id}"></div></td>
      <td class="p-2 text-right whitespace-nowrap">
        <input type="number" min="0" step="1" inputmode="numeric" data-plan-avail="${id}" ${dis} title="kg available to pick this week. Empty = all that remains"> kg
        <input type="number" min="0" max="100" step="1" inputmode="numeric" class="planner-pct" data-plan-availpct="${id}" ${dis} title="as a % of what remains">%
      </td>
      ${days}
      <td class="p-2 text-right font-semibold" data-plan-cell="total_kg:${id}"></td>
    </tr>`;
  }).join("") : `<tr><td colspan="14" class="p-3 text-sm text-slate-500">No active blocks of the farm's own in Master Data.</td></tr>`;

  // Listeners. `input` keeps the derived cells moving; `change` saves.
  rows.querySelectorAll("[data-plan-day]").forEach((cb) => cb.addEventListener("change", () => {
    const [bid, d] = cb.dataset.planDay.split(":");
    const b = plannerBlock(bid);
    const day = parseInt(d, 10);
    b.days = (b.days || []).filter((x) => x !== day);
    if (cb.checked) b.days.push(day);
    delete b.fixed[String(day)];
    const cell = cb.closest("td");
    cell.classList.toggle("planner-day-off", !cb.checked);
    cell.querySelector("div").classList.toggle("hidden", !cb.checked);
    cell.querySelector("[data-plan-daykg]").classList.remove("planner-fixed");
    const picked = (b.actual || [])[day] || 0;
    const actual = cell.querySelector("[data-plan-actual]");
    actual.classList.toggle("planner-offplan", !!picked && !cb.checked);
    actual.title = picked && !cb.checked ? "Picked on a day this block was not planned for" : "";
    refreshPlannerDerived();
    schedulePlannerSave();
  }));
  rows.querySelectorAll("[data-plan-daykg]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const [bid, d] = inp.dataset.planDaykg.split(":");
      const b = plannerBlock(bid);
      if (inp.value === "") delete b.fixed[d];
      else b.fixed[d] = Math.round(Math.max(0, parseFloat(inp.value) || 0));
      inp.classList.toggle("planner-fixed", inp.value !== "");
      refreshPlannerDerived();
    });
    inp.addEventListener("change", schedulePlannerSave);
  });
  rows.querySelectorAll("[data-plan-avail]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const b = plannerBlock(inp.dataset.planAvail);
      b.available_kg = inp.value === "" ? null : Math.round(Math.max(0, parseFloat(inp.value) || 0));
      refreshPlannerDerived();
    });
    inp.addEventListener("change", schedulePlannerSave);
  });
  rows.querySelectorAll("[data-plan-availpct]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const b = plannerBlock(inp.dataset.planAvailpct);
      const d = plannerDerive(_plan).blocks.find((x) => x.block_id === b.block_id);
      if (inp.value === "" || d.remaining_kg == null) b.available_kg = null;
      else b.available_kg = Math.round(Math.max(0, d.remaining_kg) * (parseFloat(inp.value) || 0) / 100);
      refreshPlannerDerived();
    });
    inp.addEventListener("change", schedulePlannerSave);
  });
  rows.querySelectorAll("[data-plan-on]").forEach((cb) => cb.addEventListener("change", () => {
    plannerBlock(cb.dataset.planOn).on = cb.checked;
    renderPlannerHarvest(); refreshPlannerDerived(); schedulePlannerSave();
  }));
}

function plannerBlock(id) {
  const b = _plan.blocks.find((x) => x.block_id === id);
  if (!b.fixed) b.fixed = {};
  if (!b.days) b.days = [];
  return b;
}

// ----- Delivery grid -----

function renderPlannerDelivery() {
  const v = _plan;
  const head = document.getElementById("plannerDeliveryHead");
  const rows = document.getElementById("plannerDeliveryRows");
  if (!v.packhouse.available) {
    head.innerHTML = "";
    rows.innerHTML = `<tr><td class="p-3 text-sm text-slate-500">${esc(v.packhouse.reason)}</td></tr>`;
    document.getElementById("plannerDeliveryFoot").innerHTML = "";
    return;
  }
  // Two header rows: the pack (with the weight of the unit Packhouse counts
  // it in), then the two figures Packhouse doesn't keep, typed here, one
  // under the other so a pack column stays narrow: how many of its units
  // make a carton (4 punnets for Pre-pac) and how many cartons make a
  // pallet. The receiver's totals sit beside its name, before the packs,
  // so they stay in view when the pack columns run off a small screen.
  // The packs come from the server already in order: Pre-pac, then by
  // carton size (planner.pack_sort_key), the same order as the exports.
  const unitKg = (p) => `${Boord.fmtNum(p.carton_kg, 2)} kg${(p.units_per_carton || 1) > 1 ? ` × ${p.units_per_carton}` : ""}/carton`;
  head.innerHTML = `<tr class="text-left border-b bg-slate-50">
      <th class="p-2" rowspan="2">Receiver</th>
      <th class="p-2 text-right" rowspan="2">Cartons</th><th class="p-2 text-right" rowspan="2">Kg</th><th class="p-2 text-right" rowspan="2">Pallets</th>
      ${v.packs.map((p) => `<th class="p-2 text-center">${esc(p.name)}<div class="text-[10px] font-normal text-slate-400" data-plan-unitkg="${esc(p.name)}">${unitKg(p)}${p.active ? "" : " · retired"}</div></th>`).join("")}
    </tr>
    <tr class="text-left border-b bg-slate-50 text-[11px] font-normal text-slate-500">
      ${v.packs.map((p) => `<th class="p-1 text-center font-normal whitespace-nowrap">
        <div><input type="number" min="1" step="1" class="planner-cartons planner-pct" data-plan-percarton="${esc(p.name)}" value="${p.units_per_carton || 1}" title="Packhouse units (punnets) per carton"> /carton</div>
        <div class="mt-1"><input type="number" min="0" step="1" class="planner-cartons planner-pct" data-plan-perpallet="${esc(p.name)}" value="${p.cartons_per_pallet || ""}" placeholder="?" title="Cartons per pallet"> /pallet</div>
      </th>`).join("")}
    </tr>`;
  let lastGroup = null;
  const body = [];
  for (const r of v.receivers) {
    if (r.group !== lastGroup) {
      lastGroup = r.group;
      body.push(`<tr><td colspan="${v.packs.length + 4}" class="px-2 py-1 text-xs font-semibold ${PLAN_GROUP_CLASS[r.group]}">${PLAN_GROUP_LABEL[r.group]}</td></tr>`);
    }
    const rid = esc(String(r.id));
    const cells = v.packs.map((p) => {
      const done = (v.delivered[String(r.id)] || {})[p.name] || {};
      const planned = (v.plan_deliveries[String(r.id)] || {})[p.name] || "";
      return `<td class="p-1 text-center align-top">
        <input type="number" min="0" step="1" inputmode="numeric" class="planner-cartons" data-plan-cartons="${rid}:${esc(p.name)}" value="${planned}" placeholder="cartons" title="Cartons this week">
        <div class="planner-actual" data-plan-done="${rid}:${esc(p.name)}" data-ytd="${done.ytd || 0}" data-lastweek="${done.last_week || 0}"></div>
      </td>`;
    }).join("");
    body.push(`<tr class="border-b">
      <td class="p-2 font-medium">${esc(r.name)}${r.market ? `<div class="text-xs text-slate-400 font-normal">${esc(r.market)}</div>` : ""}</td>
      <td class="p-2 text-right" data-plan-cell="rcartons:${rid}"></td>
      <td class="p-2 text-right" data-plan-cell="rkg:${rid}"></td>
      <td class="p-2 text-right" data-plan-cell="rpallets:${rid}"></td>
      ${cells}
    </tr>`);
  }
  rows.innerHTML = body.length ? body.join("") : `<tr><td colspan="${v.packs.length + 4}" class="p-3 text-sm text-slate-500">No receivers in Boord Packhouse's settings yet.</td></tr>`;
  document.getElementById("plannerDeliveryFoot").innerHTML = `<tr class="font-semibold bg-slate-50">
      <td class="p-2">Total</td>
      <td class="p-2 text-right" data-plan-cell="delivery_cartons"></td>
      <td class="p-2 text-right" data-plan-cell="delivery_kg"></td>
      <td class="p-2 text-right" data-plan-cell="delivery_pallets"></td>
      ${v.packs.map((p) => `<td class="p-2 text-center" data-plan-cell="pack:${esc(p.name)}"></td>`).join("")}
    </tr>`;

  rows.querySelectorAll("[data-plan-cartons]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const [rid, pack] = inp.dataset.planCartons.split(/:(.*)/s);
      const d = _plan.plan_deliveries[rid] || (_plan.plan_deliveries[rid] = {});
      const n = parseInt(inp.value, 10);
      if (inp.value === "" || !n) delete d[pack]; else d[pack] = Math.max(0, n);
      refreshPlannerDerived();
    });
    inp.addEventListener("change", schedulePlannerSave);
  });
  const savePacks = () => {
    clearTimeout(_planPacksTimer);
    _planPacksTimer = setTimeout(async () => {
      try {
        await Boord.api("/api/planner/packs", { method: "PUT",
          body: _plan.packs.map((p) => ({ pack: p.name, cartons_per_pallet: p.cartons_per_pallet || 0,
                                          units_per_carton: p.units_per_carton || 1 })) });
      } catch (e) { Boord.toast(Boord.errorDetail(e, "Pack settings not saved")); }
    }, 500);
  };
  head.querySelectorAll("[data-plan-perpallet]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const p = _plan.packs.find((x) => x.name === inp.dataset.planPerpallet);
      p.cartons_per_pallet = Math.max(0, parseInt(inp.value, 10) || 0);
      refreshPlannerDerived();
    });
    inp.addEventListener("change", savePacks);
  });
  head.querySelectorAll("[data-plan-percarton]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const p = _plan.packs.find((x) => x.name === inp.dataset.planPercarton);
      p.units_per_carton = Math.max(1, parseInt(inp.value, 10) || 1);
      head.querySelector(`[data-plan-unitkg="${CSS.escape(p.name)}"]`).textContent = unitKg(p) + (p.active ? "" : " · retired");
      refreshPlannerDerived();
    });
    inp.addEventListener("change", savePacks);
  });
}

// Packhouse's count of a pack (punnets, for Pre-pac) shown as cartons.
function plannerUnitsToCartons(units, perCarton) {
  const per = perCarton || 1;
  return per === 1 ? (units || 0) : Math.round((units || 0) / per);
}

// ----- Derived figures, written into the cells in place -----

function refreshPlannerDerived() {
  const v = _plan;
  const d = plannerDerive(v);
  v.derived = d;
  const set = (key, text) => document.querySelectorAll(`[data-plan-cell="${CSS.escape(key)}"]`).forEach((el) => { el.textContent = text; });
  const kg = (x) => (x == null ? "–" : Boord.fmtNum(x, 0));
  const pc = (x) => (x == null ? "" : `${Boord.fmtNum(x, 0)}%`);
  const focused = document.activeElement;
  for (const b of d.blocks) {
    const src = v.blocks.find((x) => x.block_id === b.block_id);
    set(`ytd_pct:${b.block_id}`, pc(b.ytd_pct));
    set(`last_week_pct:${b.block_id}`, pc(b.last_week_pct));
    set(`remaining_kg:${b.block_id}`, kg(b.remaining_kg));
    set(`remaining_pct:${b.block_id}`, b.remaining_pct == null ? "" : `${pc(b.remaining_pct)} left`);
    set(`total_kg:${b.block_id}`, src.on ? kg(b.total_kg) : "");
    const row = document.querySelector(`[data-plan-row="${CSS.escape(b.block_id)}"]`);
    if (!row) continue;
    if (!src.on) {
      row.querySelector("[data-plan-avail]").value = "";
      row.querySelector("[data-plan-availpct]").value = "";
      continue;
    }
    // A derived figure fills an input only while the office isn't typing in
    // it; a typed day figure (fixed) is left exactly as typed.
    const avail = row.querySelector("[data-plan-avail]");
    if (avail !== focused) avail.value = b.available_kg;
    const availPct = row.querySelector("[data-plan-availpct]");
    if (availPct !== focused) availPct.value = b.available_pct == null ? "" : Boord.fmtNum(b.available_pct, 0).replace(/\s/g, "");
    row.querySelectorAll("[data-plan-daykg]").forEach((inp) => {
      const day = inp.dataset.planDaykg.split(":")[1];
      const fixed = src.fixed && src.fixed[day] != null;
      if (inp === focused) return;
      inp.value = fixed ? src.fixed[day] : (b.per_day[day] != null ? b.per_day[day] : "");
    });
    const total = row.querySelector(`[data-plan-cell="total_kg:${CSS.escape(b.block_id)}"]`);
    // Every day typed by hand can leave the total away from what is
    // available: say so in amber rather than silently.
    const drift = b.total_kg !== b.available_kg && Object.keys(src.fixed || {}).length && (src.days || []).every((x) => src.fixed[String(x)] != null);
    total.classList.toggle("text-amber-700", !!drift);
    total.title = drift ? `Typed days add up to ${kg(b.total_kg)} against ${kg(b.available_kg)} available` : "";
  }
  const on = v.blocks.filter((b) => b.on);
  const sum = (f) => on.reduce((s, b) => s + (f(b) || 0), 0);
  const byId = Object.fromEntries(d.blocks.map((b) => [b.block_id, b]));
  const anyEst = on.some((b) => b.estimate_kg != null);
  document.getElementById("plannerHarvestFoot").innerHTML = on.length ? `<tr class="font-semibold bg-slate-50">
      <td class="p-2">Total (${on.length} blocks)</td>
      <td class="p-2 text-right">${anyEst ? kg(sum((b) => b.estimate_kg)) : "–"}</td>
      <td class="p-2 text-right">${kg(sum((b) => b.ytd_kg))}</td>
      <td class="p-2 text-right">${kg(sum((b) => b.last_week_kg))}</td>
      <td class="p-2 text-right">${anyEst ? kg(sum((b) => byId[b.block_id].remaining_kg)) : "–"}</td>
      <td class="p-2 text-right">${kg(sum((b) => byId[b.block_id].available_kg))}</td>
      ${d.day_totals.map((t) => `<td class="p-2 text-center">${t ? kg(t) : ""}</td>`).join("")}
      <td class="p-2 text-right">${kg(d.harvest_kg)}</td>
    </tr>` : `<tr class="bg-slate-50"><td class="p-2 text-sm text-slate-500" colspan="14">No block ticked for this week yet.</td></tr>`;

  // Delivery. What Packhouse has loaded, in cartons at the current units per carton.
  const perCarton = Object.fromEntries((v.packs || []).map((p) => [p.name, p.units_per_carton || 1]));
  document.querySelectorAll("[data-plan-done]").forEach((el) => {
    const pack = el.dataset.planDone.split(/:(.*)/s)[1];
    el.textContent = `YTD ${Boord.fmtNum(plannerUnitsToCartons(+el.dataset.ytd, perCarton[pack]), 0)} · last wk ${Boord.fmtNum(plannerUnitsToCartons(+el.dataset.lastweek, perCarton[pack]), 0)}`;
  });
  for (const r of d.receivers) {
    set(`rcartons:${r.id}`, Boord.fmtNum(r.cartons, 0));
    set(`rkg:${r.id}`, kg(r.kg));
    set(`rpallets:${r.id}`, r.pallets ? Boord.fmtNum(r.pallets, 0) : "–");
  }
  for (const [name, t] of Object.entries(d.pack_totals)) {
    set(`pack:${name}`, `${Boord.fmtNum(t.cartons, 0)} cartons${t.pallets ? ` · ${Boord.fmtNum(t.pallets, 0)} pal` : ""}`);
  }
  set("delivery_cartons", Boord.fmtNum(d.delivery_cartons, 0));
  set("delivery_kg", kg(d.delivery_kg));
  set("delivery_pallets", d.delivery_pallets ? Boord.fmtNum(d.delivery_pallets, 0) : "–");

  // Grand totals.
  const loss = v.loss || {};
  const short = d.balance_kg < 0;
  const lossInput = document.getElementById("plannerLossInput");
  const lossValue = lossInput && lossInput === focused ? lossInput.value : (loss.override_pct == null ? "" : loss.override_pct);
  const tile = (label, body, cls = "") => `<div class="rounded-xl p-3 ${cls || "bg-slate-50"}"><div class="text-xs text-slate-500">${label}</div>${body}</div>`;
  document.getElementById("plannerGrand").innerHTML = [
    tile("Planned harvest", `<div class="text-xl font-bold">${Boord.fmtNum(d.harvest_kg, 0) + " kg"}</div><div class="text-xs text-slate-400">${on.length} blocks on</div>`),
    tile("Juice and loss", `<div class="flex items-center gap-1"><input type="number" id="plannerLossInput" min="-100" max="100" step="1" inputmode="numeric" class="border border-slate-300 rounded-lg p-1 w-20 text-right text-lg font-bold" placeholder="${loss.season_pct == null ? "–" : Boord.fmtNum(loss.season_pct, 0)}" value="${lossValue}" title="Type to override the season's share; clear to use it again"> <span class="text-lg font-bold">%</span></div>
      <div class="text-xs text-slate-400">${loss.override_pct != null ? `typed · season ${loss.season_pct == null ? "–" : pc(loss.season_pct)}` : loss.season_pct != null ? `season so far: ${Boord.fmtNum(loss.harvested_kg, 0)} kg picked, ${Boord.fmtNum(loss.delivered_kg, 0)} kg delivered` : "no season figure yet"}</div>`),
    tile("Deliverable", `<div class="text-xl font-bold">${Boord.fmtNum(d.deliverable_kg, 0) + " kg"}</div><div class="text-xs text-slate-400">after ${pc(d.loss_pct)} loss</div>`),
    tile("Planned delivery", `<div class="text-xl font-bold">${Boord.fmtNum(d.delivery_kg, 0) + " kg"}</div><div class="text-xs text-slate-400">${Boord.fmtNum(d.delivery_cartons, 0)} cartons${d.delivery_pallets ? ` · ${Boord.fmtNum(d.delivery_pallets, 0)} pallets` : ""}</div>`),
    tile(short ? "Short" : "Surplus", `<div class="text-xl font-bold ${short ? "text-red-700" : "text-green-700"}"><i class="fa-solid ${short ? "fa-arrow-down" : "fa-arrow-up"}"></i> ${Boord.fmtNum(Math.abs(d.balance_kg), 0) + " kg"}</div><div class="text-xs ${short ? "text-red-600" : "text-green-600"}">${short ? "picking planned is short of the cartons" : "picking planned covers the cartons"}</div>`,
      short ? "bg-red-50" : "bg-green-50"),
  ].join("");
  const newLoss = document.getElementById("plannerLossInput");
  newLoss.addEventListener("input", () => {
    _plan.loss.override_pct = newLoss.value === "" ? null : Math.round(parseFloat(newLoss.value));
    refreshPlannerDerived();
  });
  newLoss.addEventListener("change", schedulePlannerSave);
  if (lossInput === focused) { newLoss.focus(); }
}

// ----- Saving -----

function plannerBody() {
  const v = _plan;
  return {
    loss_pct: v.loss.override_pct == null || Number.isNaN(v.loss.override_pct) ? null : v.loss.override_pct,
    blocks: v.blocks.map((b) => ({ block_id: b.block_id, on: !!b.on, available_kg: b.available_kg,
                                   days: b.days || [], fixed: b.fixed || {} })),
    deliveries: v.plan_deliveries,
  };
}

function schedulePlannerSave() {
  _planPending = true;
  renderPlannerSaveState();
  clearTimeout(_planSaveTimer);
  _planSaveTimer = setTimeout(savePlanner, 800);
}

async function savePlanner() {
  clearTimeout(_planSaveTimer);
  if (!_plan || !_planPending) return;
  const weekStart = _plan.week_start;
  try {
    const fresh = await Boord.api(`/api/planner/week/${weekStart}`, { method: "PUT", body: plannerBody() });
    // The office may have moved on to another week while this was in flight.
    if (_plan && _plan.week_start === weekStart) { _plan.updated_at = fresh.updated_at; _planPending = false; }
  } catch (e) {
    document.getElementById("plannerSaveState").textContent = `Not saved: ${Boord.errorDetail(e, "server error")}`;
    return;
  }
  renderPlannerSaveState();
}

// Leaving the page (or the week) before the save timer fires: send it now.
// keepalive lets the request outlive the page.
function flushPlannerSave() {
  if (!_plan || !_planPending) return;
  clearTimeout(_planSaveTimer);
  _planPending = false;
  fetch(`${API_BASE}/api/planner/week/${_plan.week_start}`, {
    method: "PUT", keepalive: true, headers: { "Content-Type": "application/json" },
    body: JSON.stringify(plannerBody()),
  }).catch(() => {});
}

async function exportPlanner(kind) {
  if (!_plan) return;
  await savePlanner();
  try {
    const blob = await Boord.api(`/api/planner/week/${_plan.week_start}/export.${kind}`, { timeoutMs: Boord.UPLOAD_TIMEOUT_MS });
    Boord.downloadBlob(blob, `Harvest_Plan_${_plan.season_year}_week_${String(_plan.iso_week).padStart(2, "0")}.${kind}`);
  } catch (e) {
    Boord.toast(Boord.errorDetail(e, "Could not make the export"));
  }
}

// ---------------------------------------------------------------------
// Markets: the delivery notes Boord Packhouse loaded in the
// chosen period, each with the market statement and transport invoice the
// office types against it (what the season "Markte" workbook held).
// ---------------------------------------------------------------------
let _marketNotes = [];
let _marketNote = null;
// The heading the office last clicked. It holds across period changes, so
// sorting by market and then widening to the season keeps the market order.
let _marketSort = { key: "loaded_on", dir: 1 };

// A note with nothing typed in yet has no sold, unsold or net: it sorts
// below every entered one, whichever way round, rather than passing for zero.
function marketSortValue(n, key) {
  if ((key === "sold" || key === "unsold" || key === "net") && !n.entered) return null;
  return key === "unsold" ? n.sent - n.sold : n[key];
}

function sortMarketNotes() {
  const { key, dir } = _marketSort;
  _marketNotes.sort((a, b) => {
    const x = marketSortValue(a, key), y = marketSortValue(b, key);
    if (x == null || x === "") return (y == null || y === "") ? 0 : 1;
    if (y == null || y === "") return -1;
    // numeric: true so note 1341 comes before 13410, and names ignore case.
    return dir * (typeof x === "number" && typeof y === "number"
      ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: "base" }));
  });
}

// The Markets tab opens on the last week. Packhouse and the market's email
// change the list from outside, so it is fetched again each time the tab opens.
function bindMarkets() {
  Boord.bindDateRangePresets({
    todayBtn: document.getElementById("marketsTodayBtn"),
    weekBtn: document.getElementById("marketsWeekBtn"),
    seasonBtn: document.getElementById("marketsSeasonBtn"),
    startInput: document.getElementById("marketDate1"),
    endInput: document.getElementById("marketDate2"),
    seasonAnchor: () => ({
      month: (_systemSettings && _systemSettings.season_start_month) || 1,
      day: (_systemSettings && _systemSettings.season_start_day) || 1,
    }),
    onChange: loadMarketNotes,
  });
  document.getElementById("marketDate1").addEventListener("change", loadMarketNotes);
  document.getElementById("marketDate2").addEventListener("change", loadMarketNotes);
  document.querySelectorAll("#marketNotesHead [data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      _marketSort = { key, dir: _marketSort.key === key ? -_marketSort.dir : 1 };
      renderMarketNotes();
    });
  });
  document.querySelectorAll("[data-market-report]").forEach((btn) => {
    btn.addEventListener("click", () => printMarketReport(btn.dataset.marketReport));
  });
  document.querySelector('.tab-btn[data-tab="markets"]').addEventListener("click", () => {
    loadMarketNotes();
    loadMarketMail();
  });
  document.getElementById("marketsWeekBtn").click();
}

async function loadMarketNotes() {
  const d1 = document.getElementById("marketDate1").value;
  const d2 = document.getElementById("marketDate2").value;
  const tbody = document.getElementById("marketNotesRows");
  if (!d1 || !d2) return;
  try {
    _marketNotes = await Boord.api(`/api/reports/market-notes?period_start=${d1}&period_end=${d2}`);
  } catch (e) {
    // Most often Packhouse isn't on this server; the 503 says so in words.
    tbody.innerHTML = `<tr><td colspan="12" class="p-3 text-sm text-slate-500">${esc(e.detail || e.message)}</td></tr>`;
    document.getElementById("marketNotesFoot").innerHTML = "";
    return;
  }
  renderMarketNotes();
}

function renderMarketNotes() {
  const tbody = document.getElementById("marketNotesRows");
  sortMarketNotes();
  document.querySelectorAll("#marketNotesHead [data-sort]").forEach((th) => {
    const on = th.dataset.sort === _marketSort.key;
    th.querySelector("i")?.remove();
    th.setAttribute("aria-sort", on ? (_marketSort.dir > 0 ? "ascending" : "descending") : "none");
    th.insertAdjacentHTML("beforeend", ` <i class="fa-solid ${on ? (_marketSort.dir > 0 ? "fa-sort-up" : "fa-sort-down") : "fa-sort text-slate-300"} text-xs"></i>`);
  });
  tbody.innerHTML = _marketNotes.length ? _marketNotes.map((n) => `
    <tr class="border-b">
      <td class="p-2 whitespace-nowrap">${esc(n.loaded_on)}</td>
      <td class="p-2">${esc(n.note_no)}</td>
      <td class="p-2">${esc(n.receiver)}</td>
      <td class="p-2">${esc(n.market)}</td>
      <td class="p-2">${esc(n.transporter)}</td>
      <td class="p-2">${n.pallets}</td>
      <td class="p-2">${n.sent}</td>
      <td class="p-2">${n.entered ? n.sold : "-"}</td>
      <td class="p-2">${n.entered ? n.sent - n.sold : "-"}</td>
      <td class="p-2">${n.entered ? Boord.fmtRand(n.net) : '<span class="text-amber-600">Not entered</span>'}${n.invoices.length ? ` <i class="fa-solid fa-envelope text-slate-400" title="From ${n.invoices.length} emailed invoice(s)"></i>` : ""}</td>
      <td class="p-2">${n.transport_cost ? Boord.fmtRand(n.transport_cost) : "-"}</td>
      <td class="p-2 whitespace-nowrap"><button class="px-3 py-1 bg-slate-200 rounded-lg text-xs" data-market-note="${esc(n.uuid)}">${n.entered ? "Edit" : "Enter"}</button>
        <button class="px-3 py-1 bg-slate-200 rounded-lg text-xs" data-market-print="${esc(n.uuid)}" title="Print or save as PDF"><i class="fa-solid fa-print"></i> Print</button></td>
    </tr>
  `).join("") : `<tr><td colspan="12" class="p-3 text-sm text-slate-500">No delivery notes loaded in this period.</td></tr>`;
  // Sold, Unsold and Net Paid add up only the notes with a statement in, the
  // same notes their columns show figures for; the rest are still "-".
  const sum = (f, only) => _marketNotes.reduce((s, n) => s + (!only || n.entered ? f(n) : 0), 0);
  document.getElementById("marketNotesFoot").innerHTML = _marketNotes.length ? `
    <tr class="font-semibold bg-slate-50">
      <td class="p-2" colspan="5">Total (${_marketNotes.length} notes)</td>
      <td class="p-2">${sum((n) => n.pallets)}</td>
      <td class="p-2">${sum((n) => n.sent)}</td>
      <td class="p-2">${sum((n) => n.sold, true)}</td>
      <td class="p-2">${sum((n) => n.sent - n.sold, true)}</td>
      <td class="p-2 whitespace-nowrap">${Boord.fmtRand(sum((n) => n.net, true))}</td>
      <td class="p-2 whitespace-nowrap">${Boord.fmtRand(sum((n) => n.transport_cost || 0))}</td>
      <td></td>
    </tr>` : "";
  tbody.querySelectorAll("[data-market-note]").forEach((btn) => {
    btn.addEventListener("click", () => openMarketModal(_marketNotes.find((n) => n.uuid === btn.dataset.marketNote)));
  });
  tbody.querySelectorAll("[data-market-print]").forEach((btn) => {
    btn.addEventListener("click", () => printMarketNote(_marketNotes.find((n) => n.uuid === btn.dataset.marketPrint)));
  });
}

// One note's statement on its own page, from the figures the list already
// has, so the office can print it for the file or "Save as PDF" in the print
// dialog to email it. A plain page with its own styles: Tailwind isn't loaded
// in the new window, and nothing here is fetched again.
function printMarketNote(n) {
  const r = (v) => Boord.fmtRand(v || 0);
  const dash = (v) => (n.entered && v != null ? v : "-");
  const rows = n.packs.map((pk) => `<tr><td>${esc(pk.pack)}</td><td>${pk.sent}</td><td>${dash(pk.sold)}</td>
    <td>${n.entered && pk.sold != null ? pk.sent - pk.sold : "-"}</td><td>${pk.price != null ? r(pk.price) : "-"}</td>
    <td>${n.entered ? r(pk.gross) : "-"}</td></tr>`).join("");
  const line = (label, v) => `<tr><td>${label}</td><td>${v}</td></tr>`;
  printPage(`Delivery note ${n.note_no}`, `
    <h1>Bekfontein Boerdery - Delivery note ${esc(n.note_no)}</h1>
    <div class="meta">${[n.loaded_on, n.receiver, n.market, n.transporter, `${n.pallets} pallets`].filter(Boolean).map(esc).join(" · ")}</div>
    <table><thead><tr><th>Pack</th><th>Sent</th><th>Sold</th><th>Unsold</th><th>Price</th><th>Gross</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="6">No packs on this note.</td></tr>'}</tbody>
      <tfoot><tr><th>Total</th><th>${n.sent}</th><th>${n.entered ? n.sold : "-"}</th><th>${n.entered ? n.sent - n.sold : "-"}</th><th></th><th>${n.entered ? r(n.gross) : "-"}</th></tr></tfoot></table>
    ${n.entered ? `<table class="sum">
      ${line("Gross sales", r(n.gross))}${line("Commission", r(n.commission))}${line("Deductions", r(n.deductions))}${line("Bank charges", r(n.bank_cost))}
      <tr class="net"><td>Net paid</td><td>${r(n.net)}</td></tr></table>` : "<p><b>Market statement not entered yet.</b></p>"}
    <table class="sum">
      ${line("Transport", n.transport_cost ? r(n.transport_cost) : "-")}${line("Waybill", esc(n.waybill_no || "-"))}${line("Transport invoice", esc(n.invoice_no || "-"))}</table>
    ${n.invoices.length ? `<p class="small">From emailed invoices: ${n.invoices.map((i) => `${esc(i.invoice_no)} (${esc(i.payment)}, ${esc(i.invoice_date)}, net ${r(i.net)})`).join("; ")}</p>` : ""}
    ${n.notes ? `<p>${esc(n.notes)}</p>` : ""}
`);
}

// A plain page in its own window with its own styles: Tailwind isn't loaded
// there. On a phone that window has no address bar or back button (the Admin
// app runs installed, full screen), so the page carries its own Back to Boord,
// Share / PDF (shared/report-pdf.js: the phone's own share sheet with the
// report as a PDF) and Print; none of them is printed. Back closes the
// window; where the phone won't let a page close itself it goes to the Admin
// app instead. Only a desktop opens the print dialog by itself: on a phone
// it would stand in front of the share button. The print dialog's "Save as PDF" is how the office makes a PDF to
// email, so no PDF library has to ride along to the farm PC.
// `w` is a window already opened, by a caller that has to fetch first: a
// window opened after an await is no longer the click's, and gets blocked.
function printPage(title, body, w) {
  w = w || window.open("", "_blank");
  if (!w) { Boord.toast("The browser blocked the print window - allow pop-ups for Boord"); return; }
  w.document.write(`<!doctype html><html><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>${esc(title)}</title>
    <style>
      body { font-family: system-ui, sans-serif; margin: 24px; color: #0f172a; }
      h1 { font-size: 20px; margin: 0 0 4px; } .meta { color: #475569; margin-bottom: 16px; }
      table { border-collapse: collapse; width: 100%; margin-bottom: 16px; }
      th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid #cbd5e1; }
      th { background: #f1f5f9; } .sum { width: auto; min-width: 320px; } .sum td:last-child { text-align: right; }
      .net td { font-weight: bold; } .small { font-size: 12px; color: #64748b; }
      .grp { font-weight: bold; font-size: 15px; padding-top: 14px; border-bottom: 2px solid #0f172a; }
      td:first-child { white-space: nowrap; } .sub td { font-weight: bold; } @media print { tr { break-inside: avoid; } .grp { break-after: avoid; } }
      .bar { position: sticky; top: 0; display: flex; gap: 8px; padding: 8px 0 12px; background: #fff; }
      .bar button { font: inherit; font-size: 16px; padding: 10px 16px; border: 0; border-radius: 8px; background: #e2e8f0; color: #0f172a; }
      .bar .back { background: #1e3a8a; color: #fff; } @media print { .bar { display: none; } }
    </style></head><body>
    <div class="bar">
      <button class="back" onclick="window.close(); setTimeout(function () { location.href = '/admin/'; }, 300)">&larr; Back to Boord</button>
      <button id="sharePdf" disabled>Making PDF...</button>
      <button onclick="window.print()">Print</button>
    </div>
    <div id="report">${body}
    <p class="small">Printed ${esc(new Date().toLocaleString())} from Boord ${esc(Boord.VERSION || "")}</p></div>
    <script src="../shared/vendor/html2canvas/html2canvas.min.js"></script>
    <script src="../shared/vendor/jspdf/jspdf.umd.min.js"></script>
    <script src="../shared/report-pdf.js"></script>
    </body></html>`);
  w.document.close();
  w.focus();
  if (!window.matchMedia("(pointer: coarse)").matches) w.print();
}

// The quick reports under the notes list: the notes already loaded for the
// dates at the top, in the list's own order. A note with no statement in
// yet has no sold figure; it shows "-" and counts as all unsold on Unsold,
// because nothing on it has been paid for.
// The same reports from the Reports tab, for its dates: the notes are
// fetched for them, so the Markets tab's list is left as it is.
async function printMarketReportFor(kind, d1, d2) {
  const w = window.open("", "_blank");
  if (!w) { Boord.toast("The browser blocked the print window - allow pop-ups for Boord"); return; }
  try {
    const notes = await Boord.api(`/api/reports/market-notes?period_start=${d1}&period_end=${d2}`);
    printMarketReport(kind, notes, d1, d2, w);
  } catch (e) {
    w.close();
    Boord.toast(e.detail || "Could not load the delivery notes - check connection");
  }
}

function printMarketReport(kind, notes, d1, d2, w) {
  notes = notes || _marketNotes;
  d1 = d1 || document.getElementById("marketDate1").value;
  d2 = d2 || document.getElementById("marketDate2").value;
  const period = d1 === d2 ? d1 : `${d1} to ${d2}`;
  const r = (v) => Boord.fmtRand(v || 0);
  const head = (cols) => `<thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead>`;
  let title, body;
  if (kind === "receiver" || kind === "market" || kind === "transporter") {
    title = { receiver: "Receiver", market: "Market", transporter: "Transporter" }[kind];
    const others = ["receiver", "market", "transporter"].filter((k) => k !== kind);
    const cols = ["Loaded", "Note", ...others.map((k) => k[0].toUpperCase() + k.slice(1)),
      "Pallets", "Sent", "Sold", "Unsold", "Gross", "Net paid", "Transport"];
    const totals = (ns, label, tag) => `<tr class="sub"><${tag} colspan="4">${label}</${tag}>
      <${tag}>${ns.reduce((s, n) => s + n.pallets, 0)}</${tag}><${tag}>${ns.reduce((s, n) => s + n.sent, 0)}</${tag}>
      <${tag}>${ns.reduce((s, n) => s + (n.entered ? n.sold : 0), 0)}</${tag}>
      <${tag}>${ns.reduce((s, n) => s + (n.entered ? n.sent - n.sold : 0), 0)}</${tag}>
      <${tag}>${r(ns.reduce((s, n) => s + (n.entered ? n.gross : 0), 0))}</${tag}>
      <${tag}>${r(ns.reduce((s, n) => s + (n.entered ? n.net : 0), 0))}</${tag}>
      <${tag}>${r(ns.reduce((s, n) => s + (n.transport_cost || 0), 0))}</${tag}></tr>`;
    const groups = {};
    notes.forEach((n) => (groups[n[kind] || "(none)"] = groups[n[kind] || "(none)"] || []).push(n));
    // One table, a heading row per group, so the columns line up down the
    // page and the grand total sits under them.
    body = `<table>${head(cols)}<tbody>${Object.keys(groups).sort((a, b) => a.localeCompare(b)).map((g) => `
      <tr><td colspan="${cols.length}" class="grp">${esc(g)}</td></tr>
      ${groups[g].map((n) => `<tr><td>${esc(n.loaded_on)}</td><td>${esc(n.note_no)}</td>
        ${others.map((k) => `<td>${esc(n[k] || "")}</td>`).join("")}<td>${n.pallets}</td><td>${n.sent}</td>
        <td>${n.entered ? n.sold : "-"}</td><td>${n.entered ? n.sent - n.sold : "-"}</td>
        <td>${n.entered ? r(n.gross) : "-"}</td><td>${n.entered ? r(n.net) : "Not entered"}</td>
        <td>${n.transport_cost ? r(n.transport_cost) : "-"}</td></tr>`).join("")}
        ${totals(groups[g], `${esc(g)}: ${groups[g].length} note${groups[g].length === 1 ? "" : "s"}`, "td")}`).join("")}</tbody>
      <tfoot>${totals(notes, `Total, ${notes.length} notes`, "th")}</tfoot></table>`;
  } else {
    // Sold and Unsold are per pack: what sold at what price, and what is
    // still outstanding on each note.
    const sold = kind === "sold";
    title = sold ? "Sold" : "Unsold";
    const lines = [];
    notes.forEach((n) => n.packs.forEach((pk) => {
      const out = pk.sent - (n.entered ? pk.sold || 0 : 0);
      if (sold ? n.entered && pk.sold > 0 : out > 0) lines.push({ n, pk, out });
    }));
    const cols = ["Loaded", "Note", "Receiver", "Market", "Pack",
      ...(sold ? ["Sold", "Price", "Gross"] : ["Sent", "Sold", "Unsold"])];
    const sum = (f) => lines.reduce((s, l) => s + f(l), 0);
    body = `<table>${head(cols)}<tbody>${lines.map(({ n, pk, out }) => `<tr><td>${esc(n.loaded_on)}</td>
      <td>${esc(n.note_no)}</td><td>${esc(n.receiver)}</td><td>${esc(n.market)}</td><td>${esc(pk.pack)}</td>
      ${sold ? `<td>${pk.sold}</td><td>${pk.price != null ? r(pk.price) : "-"}</td><td>${r(pk.gross)}</td>`
        : `<td>${pk.sent}</td><td>${n.entered ? pk.sold || 0 : "Not entered"}</td><td>${out}</td>`}</tr>`).join("")
      || `<tr><td colspan="8">Nothing ${sold ? "sold" : "unsold"} in this period.</td></tr>`}</tbody>
      ${lines.length ? `<tfoot><tr><th colspan="5">Total, ${lines.length} line${lines.length === 1 ? "" : "s"}</th>
        ${sold ? `<th>${sum((l) => l.pk.sold)}</th><th></th><th>${r(sum((l) => l.pk.gross))}</th>`
          : `<th>${sum((l) => l.pk.sent)}</th><th>${sum((l) => l.pk.sent - l.out)}</th><th>${sum((l) => l.out)}</th>`}</tr></tfoot>` : ""}</table>`;
  }
  printPage(`Markets - ${title} ${period}`, `
    <style>@page { size: landscape; }</style>
    <h1>Bekfontein Boerdery - Markets: ${esc(title)}</h1>
    <div class="meta">Delivery notes loaded ${esc(period)} · ${notes.length} note${notes.length === 1 ? "" : "s"}</div>
    ${notes.length ? body : "<p>No delivery notes loaded in this period.</p>"}`, w);
}

// A blank box is "not on the statement yet", not zero.
function numOrNull(el) {
  return el.value.trim() === "" ? null : Number(el.value);
}

function updateMarketNet() {
  // Unsold follows the Sold box as it is typed; blank until a figure is in.
  document.querySelectorAll("#marketPackRows tr").forEach((tr) => {
    const sold = numOrNull(tr.querySelector(".mk-sold"));
    tr.querySelector(".mk-unsold").textContent = sold == null ? "-" : Number(tr.dataset.sent) - sold;
  });
  // An invoiced note's figures are the invoices' own: multiplying the
  // averaged price back out would be a few rand off what was paid.
  if (_marketNote && _marketNote.invoices.length) {
    const n = _marketNote;
    document.getElementById("marketNetLine").textContent =
      `Gross ${Boord.fmtRand(n.gross)} - costs ${Boord.fmtRand(n.commission + n.deductions + n.bank_cost)} = net ${Boord.fmtRand(n.net)}`;
    return;
  }
  let gross = 0;
  document.querySelectorAll("#marketPackRows tr").forEach((tr) => {
    gross += (numOrNull(tr.querySelector(".mk-sold")) || 0) * (numOrNull(tr.querySelector(".mk-price")) || 0);
  });
  const costs = ["mkCommission", "mkDeductions", "mkBankCost"]
    .reduce((sum, id) => sum + (numOrNull(document.getElementById(id)) || 0), 0);
  document.getElementById("marketNetLine").textContent =
    `Gross ${Boord.fmtRand(gross)} - costs ${Boord.fmtRand(costs)} = net ${Boord.fmtRand(gross - costs)}`;
}

function openMarketModal(note) {
  _marketNote = note;
  document.getElementById("marketModalTitle").textContent = `Delivery note ${note.note_no}`;
  document.getElementById("marketModalMeta").textContent =
    [note.loaded_on, note.receiver, note.market, note.transporter, `${note.pallets} pallets`].filter(Boolean).join(" · ");
  document.getElementById("marketPackRows").innerHTML = note.packs.map((pk) => `
    <tr class="border-b" data-pack="${esc(pk.pack)}" data-sent="${pk.sent}">
      <td class="p-1">${esc(pk.pack)}</td>
      <td class="p-1">${pk.sent}</td>
      <td class="p-1"><input type="number" min="0" step="1" class="mk-sold w-24 border border-slate-300 rounded-lg p-1" value="${pk.sold ?? ""}"></td>
      <td class="p-1 mk-unsold"></td>
      <td class="p-1"><input type="number" min="0" step="0.01" class="mk-price w-28 border border-slate-300 rounded-lg p-1" value="${pk.price ?? ""}"></td>
    </tr>
  `).join("");
  // Once the market's invoice has come in by email its figures are the
  // statement, so the sales boxes only show them; transport stays typed.
  const imported = note.invoices.length > 0;
  const box = document.getElementById("marketInvoices");
  box.classList.toggle("hidden", !imported);
  box.innerHTML = imported ? `
    <div class="font-semibold mb-1"><i class="fa-solid fa-envelope mr-1 text-slate-400"></i>From the market's emailed invoices</div>
    ${note.invoices.map((i) => `
      <div class="flex items-center justify-between gap-2 py-1">
        <span>${esc(i.invoice_no)} · ${esc(i.payment)} · ${esc(i.invoice_date)} · net ${Boord.fmtRand(i.net)}</span>
        <button class="text-xs text-red-600" data-remove-invoice="${esc(i.invoice_no)}">Remove</button>
      </div>`).join("")}
    ${note.packs.some((pk) => pk.unmapped) ? '<div class="text-xs text-amber-700 mt-1">Some market products are not matched to a pack yet - see Market invoices by email below the list.</div>' : ""}
  ` : "";
  box.querySelectorAll("[data-remove-invoice]").forEach((btn) => {
    btn.addEventListener("click", () => removeMarketInvoice(btn.dataset.removeInvoice));
  });
  document.querySelectorAll("#marketPackRows input, #mkCommission, #mkDeductions, #mkBankCost")
    .forEach((el) => { el.disabled = imported; el.classList.toggle("bg-slate-100", imported); });
  const set = (id, v) => { document.getElementById(id).value = v ?? ""; };
  // A note with no statement yet opens blank rather than with zeros.
  set("mkCommission", note.entered ? note.commission : "");
  set("mkDeductions", note.entered ? note.deductions : "");
  set("mkBankCost", note.entered ? note.bank_cost : "");
  set("mkTransport", note.transport_cost || "");
  set("mkWaybill", note.waybill_no);
  set("mkInvoice", note.invoice_no);
  set("mkNotes", note.notes);
  updateMarketNet();
  document.getElementById("marketModal").classList.remove("hidden");
  document.getElementById("marketModal").classList.add("flex");
}

function closeMarketModal() {
  document.getElementById("marketModal").classList.add("hidden");
  document.getElementById("marketModal").classList.remove("flex");
  _marketNote = null;
}

async function saveMarketStatement() {
  if (!_marketNote) return;
  const num = (id) => numOrNull(document.getElementById(id)) || 0;
  const body = {
    lines: [...document.querySelectorAll("#marketPackRows tr")].map((tr) => ({
      pack: tr.dataset.pack,
      sold: numOrNull(tr.querySelector(".mk-sold")),
      price: numOrNull(tr.querySelector(".mk-price")),
    })),
    commission: num("mkCommission"),
    deductions: num("mkDeductions"),
    bank_cost: num("mkBankCost"),
    transport_cost: num("mkTransport"),
    waybill_no: document.getElementById("mkWaybill").value,
    invoice_no: document.getElementById("mkInvoice").value,
    notes: document.getElementById("mkNotes").value,
  };
  try {
    await Boord.api(`/api/reports/market-notes/${encodeURIComponent(_marketNote.uuid)}`, { method: "PUT", body });
  } catch (e) {
    if (Boord.isAuthError(e)) { accessRefused(e); return; }
    Boord.toast(`Not saved: ${e.detail || e.message}`);
    return;
  }
  Boord.toast(`Note ${_marketNote.note_no} saved`);
  closeMarketModal();
  loadMarketNotes();
}

async function removeMarketInvoice(invoiceNo) {
  if (!confirm(`Take invoice ${invoiceNo} out of the reports? Its email stays labelled Boord-Done in Gmail, so it won't come back by itself.`)) return;
  try {
    await Boord.api(`/api/reports/market-invoices?invoice_no=${encodeURIComponent(invoiceNo)}`, { method: "DELETE" });
  } catch (e) {
    Boord.toast(`Not removed: ${e.detail || e.message}`);
    return;
  }
  Boord.toast(`Invoice ${invoiceNo} removed`);
  closeMarketModal();
  loadMarketNotes();
  loadMarketMail();
}

async function loadMarketMail() {
  let m;
  try {
    m = await Boord.api("/api/reports/market-mail");
  } catch (e) {
    document.getElementById("marketMailState").textContent = e.detail || e.message;
    return;
  }
  document.getElementById("marketMailState").textContent = m.address
    ? `${m.address} · checked every ${m.every_minutes} min · last ${m.last_run ? m.last_run.replace("T", " ") : "not yet"} · from ${m.senders.join(", ")}`
    : "Not set up on this server yet - see the manual (data\\market_mail.txt).";
  document.getElementById("marketMailFetchBtn").classList.toggle("hidden", !m.address);
  const err = document.getElementById("marketMailError");
  err.textContent = m.last_error ? `Last check failed: ${m.last_error}` : "";
  err.classList.toggle("hidden", !m.last_error);
  document.getElementById("marketMailLog").innerHTML = m.log.length
    ? m.log.map((line) => `<li>${esc(line)}</li>`).join("")
    : "<li>Nothing yet.</li>";

  // Each product description a market has invoiced, matched once to the
  // Packhouse pack it is, so sold lines up with sent.
  const options = (current) => [`<option value="">- not matched -</option>`]
    .concat(m.packs.concat(current && !m.packs.includes(current) ? [current] : [])
      .map((p) => `<option value="${esc(p)}" ${p === current ? "selected" : ""}>${esc(p)}</option>`)).join("");
  document.getElementById("marketProducts").innerHTML = m.products.length ? `
    <div class="text-xs text-slate-500 mb-1">Market products and the Packhouse pack each one is:</div>
    <table class="w-full text-sm"><tbody>
      ${m.products.map((p) => `
        <tr class="border-b">
          <td class="p-1 text-xs">${esc(p.product)}</td>
          <td class="p-1"><select data-market-product="${esc(p.product)}" class="border border-slate-300 rounded-lg p-1 text-sm ${p.pack ? "" : "border-amber-400"}">${options(p.pack)}</select></td>
        </tr>`).join("")}
    </tbody></table>` : "";
  document.querySelectorAll("[data-market-product]").forEach((sel) => {
    sel.addEventListener("change", async () => {
      try {
        await Boord.api("/api/reports/market-products", { method: "PUT", body: { product: sel.dataset.marketProduct, pack: sel.value } });
      } catch (e) {
        Boord.toast(`Not saved: ${e.detail || e.message}`);
        return;
      }
      sel.classList.toggle("border-amber-400", !sel.value);
      loadMarketNotes();
    });
  });
}

async function fetchMarketMailNow() {
  const btn = document.getElementById("marketMailFetchBtn");
  btn.disabled = true;
  btn.textContent = "Checking...";
  try {
    await Boord.api("/api/reports/market-mail/fetch", { method: "POST", timeoutMs: Boord.UPLOAD_TIMEOUT_MS });
  } catch (e) {
    Boord.toast(`Could not check the email: ${e.detail || e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Fetch now";
  }
  loadMarketMail();
  loadMarketNotes();
}

function bindMarketModal() {
  document.getElementById("marketMailFetchBtn").addEventListener("click", fetchMarketMailNow);

  document.getElementById("marketModalClose").addEventListener("click", closeMarketModal);
  document.getElementById("marketModalCancel").addEventListener("click", closeMarketModal);
  document.getElementById("marketModalSave").addEventListener("click", saveMarketStatement);
  document.getElementById("marketModal").addEventListener("input", updateMarketNet);
}

// ---------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------
let _mapInstance = null;
let _pickedLatLng = null;
// Which pair of inputs the picker is currently editing. The map itself is
// built once and reused, so the target has to be state rather than a closure
// captured when it was created - Settings and the setup wizard both open it.
let _mapTarget = { lat: "setGpsLat", lon: "setGpsLon" };
let _mapModalBound = false;

// The modal's own two buttons, bound separately from the Settings tab that
// used to own them. The setup wizard opens the same modal without ever
// running bindSettings(), which left Cancel and "Use this location" dead on
// that screen - the pin could be dropped and then not applied.
function bindMapModal() {
  if (_mapModalBound) return;
  _mapModalBound = true;
  document.getElementById("closeMapBtn").addEventListener("click", closeMapModal);
  document.getElementById("confirmMapBtn").addEventListener("click", confirmMapLocation);
}

function bindSettings() {
  document.getElementById("saveSystemSettingsBtn").addEventListener("click", saveSystemSettings);
  document.getElementById("saveRateSettingsBtn").addEventListener("click", saveRateSettings);
  document.getElementById("pickMapBtn").addEventListener("click", () => openMapModal("setGpsLat", "setGpsLon"));
  bindMapModal();
  document.getElementById("runBackupBtn").addEventListener("click", runBackupNow);
  document.getElementById("setSeasonMonth").addEventListener("change", updateSeasonYearLabel);
  document.getElementById("setSeasonDay").addEventListener("input", updateSeasonYearLabel);
}

// The Server card. Answers the question the app could never answer before:
// which release is this server actually on, and is this screen showing it?
// Boord.VERSION in the header says what THIS browser loaded, which is a
// different question the moment a device is serving a cached copy.
async function loadServerCard() {
  const set = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  const warn = document.getElementById("srvWarning");
  const showWarning = (text, tone) => {
    if (!warn) return;
    warn.textContent = text;
    warn.className = tone === "red"
      ? "text-sm rounded-lg p-3 bg-red-50 text-red-800 border border-red-200"
      : "text-sm rounded-lg p-3 bg-amber-50 text-amber-800 border border-amber-200";
  };

  let info;
  try {
    info = await Boord.api("/api/version");
  } catch (e) {
    set("srvRelease", "could not be read");
    return;
  }

  const releaseText = {
    release: info.tag,
    ahead: `${info.describe} (between releases)`,
    reported: `${info.tag} (reported by the last update)`,
    unknown: "unknown",
  }[info.state] || "unknown";
  set("srvRelease", releaseText);

  set("srvSchema", info.migrations_applied === false
    ? `BEHIND - at ${info.alembic_current || "nothing"}, expected ${info.alembic_head}`
    : (info.alembic_current || "unknown"));

  const b = info.backups || {};
  set("srvBackup", b.last ? `${b.last} (${b.count} kept)` : "none yet");
  set("srvDevice", `v${Boord.VERSION}`);

  // What the daily check last found, if setup_update_check.bat was ever run.
  // Nothing installs itself - this is the whole of the "an update is out"
  // mechanism, and applying it stays a person double-clicking
  // update_server.bat when it suits the pack house.
  const upd = document.getElementById("srvUpdate");
  if (upd) {
    upd.classList.add("hidden");
    const u = info.update;
    if (u && u.available) {
      upd.classList.remove("hidden");
      upd.className = "text-sm rounded-lg p-3 bg-blue-50 text-blue-800 border border-blue-200";
      upd.innerHTML = `<b>${esc(u.latest)} is available.</b> This server is on ${esc(u.current)}. To install it, run <b>update_server.bat</b> on the server PC - it is not installed automatically. Checked ${Boord.fmtDateTime(u.checked_at)}.`;
    } else if (u && u.signature && u.signature !== "ok") {
      // A check that keeps failing looks exactly like "no updates" unless
      // it says so.
      upd.classList.remove("hidden");
      upd.className = "text-sm rounded-lg p-3 bg-amber-50 text-amber-800 border border-amber-200";
      const why = u.signature === "no-pubkey"
        ? "the release key is not in this server's keyring"
        : "the signature on the newest release did not verify";
      upd.innerHTML = `<b>The update check is not working:</b> ${why}. Last tried ${Boord.fmtDateTime(u.checked_at)}. See the manual, chapter 2.`;
    }
  }

  if (warn) warn.classList.add("hidden");
  if (info.migrations_applied === false) {
    warn.classList.remove("hidden");
    showWarning("The database schema is older than this release expects. The migrations did not run - restart the server, and if that does not fix it, do not carry on using the app.", "red");
  } else if (info.version && info.version !== Boord.VERSION) {
    warn.classList.remove("hidden");
    showWarning(`This device is showing cached code from v${Boord.VERSION}, but the server is running ${releaseText}. Close the app completely and reopen it to pick up the new version.`, "amber");
  } else if (info.state === "unknown" && info.git_error) {
    warn.classList.remove("hidden");
    showWarning(`The server cannot read its own version from git: ${info.git_error}`, "amber");
  }
}

async function loadBackupsList() {
  const backups = await Boord.api("/api/backups");
  document.getElementById("backupsTable").innerHTML = backups.map((b) => `
    <tr class="border-b">
      <td class="p-2">${Boord.fmtDateTime(b.created_at)}</td>
      <td class="p-2">${(b.size_bytes / 1024 / 1024).toFixed(2)} MB</td>
      <td class="p-2 text-right"><a href="#" class="text-blue-700 text-xs" data-download="${esc(b.filename)}">Download</a></td>
    </tr>
  `).join("") || `<tr><td class="p-2 text-slate-400" colspan="3">No backups yet</td></tr>`;
  document.querySelectorAll("#backupsTable [data-download]").forEach((a) => {
    a.addEventListener("click", async (e) => {
      e.preventDefault();
      const blob = await Boord.api(`/api/backups/${encodeURIComponent(a.dataset.download)}/download`, { timeoutMs: Boord.UPLOAD_TIMEOUT_MS });
      Boord.downloadBlob(blob, a.dataset.download);
    });
  });
}

// Whether backups are actually leaving this machine. Fourteen copies on the
// server's own disk are no protection at all against that disk failing, so
// "not configured" is a real answer worth showing rather than a blank.
async function loadOffsiteStatus() {
  const line = document.getElementById("offsiteStatus");
  const warn = document.getElementById("offsiteWarning");
  if (!line) return;
  if (warn) warn.classList.add("hidden");

  let s;
  try {
    s = await Boord.api("/api/backups/offsite");
  } catch (e) {
    line.textContent = "Could not read the off-site copy status.";
    return;
  }

  const folder = `${(s.folder_bytes / 1024 / 1024).toFixed(1)} MB in ${s.folder_files} files on this server`;

  if (!s.configured) {
    line.className = "text-sm rounded-lg p-3 bg-slate-50 text-slate-600";
    line.innerHTML = `<b>No off-site copy is set up.</b> Every backup is on this machine only - a failed disk takes all of them. ${folder}. See the manual, chapter 11, to set a destination.`;
    return;
  }

  const last = s.last || {};
  if (s.problem) {
    line.className = "text-sm rounded-lg p-3 bg-red-50 text-red-800 border border-red-200";
    line.innerHTML = `<b>Off-site copy is not working:</b> ${esc(s.destination)} - ${esc(s.problem)}. ${folder}.`;
  } else if (last.ok) {
    line.className = "text-sm rounded-lg p-3 bg-green-50 text-green-800 border border-green-200";
    line.innerHTML = `<b>Last copied ${Boord.fmtDateTime(last.at)}</b> to ${esc(s.destination)}. ${folder}.`;
  } else if (last.error) {
    line.className = "text-sm rounded-lg p-3 bg-red-50 text-red-800 border border-red-200";
    const runs = last.consecutive_failures > 1 ? `${last.consecutive_failures} times running` : "last night";
    line.innerHTML = `<b>Off-site copy FAILED ${runs}:</b> ${esc(last.error)} - to ${esc(s.destination)}. ${folder}.`;
  } else {
    line.className = "text-sm rounded-lg p-3 bg-slate-50 text-slate-600";
    line.innerHTML = `Copying to ${esc(s.destination)}. Nothing copied yet - the next backup will be the first. ${folder}.`;
  }

  // Warned about, not blocked. A synced folder is a decision for whoever
  // runs the pack house to make knowingly, and the name of a folder is not
  // proof either way - but nobody should discover afterwards what was in it.
  if (s.looks_like_cloud && warn) {
    warn.classList.remove("hidden");
    warn.className = "text-sm rounded-lg p-3 bg-amber-50 text-amber-800 border border-amber-200";
    warn.innerHTML = "<b>This destination looks like a synced cloud folder.</b> Backups are not encrypted and contain every worker's name, ID number, WhatsApp number and photograph. Until backup encryption is added, use a drive that stays on the farm - a plugged-in USB or external disk, a second disk in this PC, or another machine on the farm's own network.";
  }
}

async function runBackupNow() {
  try {
    await Boord.api("/api/backups", { method: "POST", timeoutMs: Boord.UPLOAD_TIMEOUT_MS });
    Boord.toast("Backup created");
    await loadBackupsList();
    await loadOffsiteStatus();
  } catch (e) {
    Boord.toast("Backup failed");
  }
}

async function loadSettingsForm() {
  // Independent reads - fetched together rather than one round trip at a time.
  const [settings, rate] = await Promise.all([
    Boord.api("/api/system-settings"), Boord.api("/api/rate-settings/current"),
    loadServerCard(), loadBackupsList(), loadOffsiteStatus(),
  ]);
  _systemSettings = settings;
  if (settings) {
    document.getElementById("setPackhouseName").value = settings.packhouse_name || "";
    document.getElementById("setPackhouseLocation").value = settings.packhouse_location || "";
    document.getElementById("setPackhouseCode").value = settings.packhouse_code || "";
    document.getElementById("setSeasonMonth").value = settings.season_start_month || 1;
    document.getElementById("setSeasonDay").value = settings.season_start_day || 1;
    document.getElementById("setGreenYellow").value = settings.green_to_yellow_minutes;
    document.getElementById("setYellowRed").value = settings.yellow_to_red_minutes;
    document.getElementById("setGpsLat").value = settings.gps_lat ?? "";
    document.getElementById("setGpsLon").value = settings.gps_lon ?? "";
    document.getElementById("setWeatherStationId").value = settings.weather_station_id || "";
    updateSeasonYearLabel();
  }
  if (rate) {
    document.getElementById("setRatePerKg").value = rate.default_rate_per_kg;
  }
}

// The season year shown beside the anchor: derived, never typed, so it can
// never drift from the start date the pack house actually picked.
function updateSeasonYearLabel() {
  const el = document.getElementById("setSeasonYearLabel");
  if (!el) return;
  const month = parseInt(document.getElementById("setSeasonMonth").value, 10) || 1;
  const day = parseInt(document.getElementById("setSeasonDay").value, 10) || 1;
  el.textContent = `Current season: ${Boord.seasonYearFor(month, day)}`;
}

async function saveSystemSettings() {
  const lat = parseFloat(document.getElementById("setGpsLat").value) || null;
  const lon = parseFloat(document.getElementById("setGpsLon").value) || null;
  const month = parseInt(document.getElementById("setSeasonMonth").value, 10) || 1;
  const day = parseInt(document.getElementById("setSeasonDay").value, 10) || 1;
  const newSettings = {
    packhouse_name: document.getElementById("setPackhouseName").value,
    packhouse_location: document.getElementById("setPackhouseLocation").value,
    packhouse_code: document.getElementById("setPackhouseCode").value,
    season_start_month: month,
    season_start_day: day,
    current_harvest_year: Boord.seasonYearFor(month, day),
    green_to_yellow_minutes: parseInt(document.getElementById("setGreenYellow").value) || 90,
    yellow_to_red_minutes: parseInt(document.getElementById("setYellowRed").value) || 150,
    gps_lat: lat,
    gps_lon: lon,
    weather_station_id: document.getElementById("setWeatherStationId").value.trim() || null,
  };
  // A rejected save has to say so. Without the catch, Save on a phone whose
  // Tailscale had dropped did nothing visible at all, and the admin walked
  // away believing the thresholds or the GPS were saved.
  try {
    await Boord.api("/api/system-settings", { method: "PUT", body: newSettings });
  } catch (e) {
    Boord.toast(Boord.isNetworkError(e) ? "No connection - settings not saved"
                                        : Boord.errorDetail(e, "Settings not saved"));
    return;
  }
  _systemSettings = { ..._systemSettings, ...newSettings };
  updateBannerPackhouseName();
  updateSeasonYearLabel();
  Boord.toast("Settings saved");
}

async function saveRateSettings() {
  const rate = parseFloat(document.getElementById("setRatePerKg").value);
  // A blank or zero field used to be saved as a rate of R0.00/kg, which
  // Calculate Wages then applied without complaint: a whole wage sheet at
  // R0.00 that looked like a finished run. The server refuses it too now.
  if (!(rate > 0)) { Boord.toast("Enter a rate per kg above zero"); return; }
  try {
    await Boord.api("/api/rate-settings", {
      method: "POST",
      body: {
        effective_date: Boord.localDateStr(),
        rate_type: "per_kg",
        default_rate_per_kg: rate,
        tier_rates_json: "{}",
      },
    });
  } catch (e) {
    Boord.toast(Boord.isNetworkError(e) ? "No connection - rate not saved"
                                        : Boord.errorDetail(e, "Rate not saved"));
    return;
  }
  Boord.toast("Rate saved");
}

// GPS map modal. Takes the ids of the two inputs to write into so the setup
// wizard can reuse it rather than growing a second copy of a map picker.
function openMapModal(latInputId = "setGpsLat", lonInputId = "setGpsLon") {
  _mapTarget = { lat: latInputId, lon: lonInputId };
  document.getElementById("mapModal").classList.remove("hidden");
  document.getElementById("mapModal").classList.add("flex");

  // isNaN rather than `|| default`: latitude 0 and longitude 0 are real
  // places, and a farm on either would be silently recentred elsewhere.
  const setLat = parseFloat(document.getElementById(latInputId).value);
  const setLon = parseFloat(document.getElementById(lonInputId).value);
  const lat = isNaN(setLat) ? -29.0 : setLat;
  const lon = isNaN(setLon) ? 30.0 : setLon;

  if (!_mapInstance) {
    _mapInstance = L.map("mapContainer").setView([lat, lon], 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap contributors",
    }).addTo(_mapInstance);

    _mapInstance.on("click", (e) => {
      _pickedLatLng = e.latlng;
      document.getElementById("mapCoordDisplay").textContent =
        `Lat: ${e.latlng.lat.toFixed(6)}, Lon: ${e.latlng.lng.toFixed(6)} — click Confirm to use this`;
      _mapInstance.eachLayer((layer) => { if (layer instanceof L.Marker) _mapInstance.removeLayer(layer); });
      L.marker(e.latlng).addTo(_mapInstance);
    });
  } else {
    // Reopened, possibly against the other screen's inputs - recentre on
    // whatever those hold rather than on the last screen's pin.
    _mapInstance.setView([lat, lon], 13);
    _mapInstance.eachLayer((layer) => { if (layer instanceof L.Marker) _mapInstance.removeLayer(layer); });
  }

  if (!isNaN(setLat) && !isNaN(setLon)) L.marker([lat, lon]).addTo(_mapInstance);
  setTimeout(() => _mapInstance.invalidateSize(), 200);
}

function closeMapModal() {
  document.getElementById("mapModal").classList.add("hidden");
  document.getElementById("mapModal").classList.remove("flex");
}

function confirmMapLocation() {
  if (_pickedLatLng) {
    document.getElementById(_mapTarget.lat).value = _pickedLatLng.lat.toFixed(6);
    document.getElementById(_mapTarget.lon).value = _pickedLatLng.lng.toFixed(6);
    _pickedLatLng = null;
  }
  closeMapModal();
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(() => {});
}

// Through Boord.boot so a failure in here shows a message and an Update
// button rather than a blank screen - see shared/api.js.
Boord.boot(init);
