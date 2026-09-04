// Admin app: master data, payments, reports, settings.

let _systemSettings = null;

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
  const dateStr = now.toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
  const timeStr = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
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
      el.innerHTML = `<i class="fa-solid ${icon}"></i> ${Math.round(w.temp)}°C · ${w.condition}${w.humidity != null ? ` · ${w.humidity}% humidity` : ""}`;
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
    if (Boord.isNetworkError(e)) { Boord.setOffline(true); return null; }
    return null;
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
    });
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
  Boord.toast(_apiErrorDetail(e) || "The server refused this request");
}

// ---------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------
function bindTabs() {
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
    if (Boord.isNetworkError(e)) { Boord.setOffline(true); return; } // keep last data on screen
    if (Boord.isAuthError(e)) { accessRefused(e); return; }
    Boord.toast("Could not load the dashboard");
    return;
  }
  Boord.setOffline(false);

  renderDashboardKpis(harvesting, inTransit, received, summary);
  renderDashboardLists(harvesting, inTransit, received, summary);
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
    ["Total Kg", `${totalKg.toFixed(1)} kg`],
    ["Total Crates", `${totalCrates} crates`],
    ["Avg Kg/Lot", avgKgPerLot.toFixed(1)],
    ["Avg Kg/Crate", avgKgPerCrate.toFixed(1)],
    ["Harvesting", `${h.crates} crates / ${h.kg.toFixed(1)} kg`],
    ["In Transit", `${t.crates} crates / ${t.kg.toFixed(1)} kg`],
    ["Received", `${r.crates} crates / ${r.kg.toFixed(1)} kg`],
  ];
  document.getElementById("dashKpiGrid").innerHTML = cards.map(([label, value]) => `
    <div class="bg-white rounded-xl shadow p-4">
      <div class="text-xs text-slate-500">${label}</div>
      <div class="text-xl font-bold">${value}</div>
    </div>
  `).join("");
}

function renderDashboardLists(harvesting, inTransit, received, summary) {
  const h = _lotTotals(harvesting);
  const t = _lotTotals(inTransit);
  const r = _lotTotals(received);

  document.getElementById("dash-harvesting-title").textContent = `Harvesting - ${h.crates} crates / ${h.kg.toFixed(1)} kg`;
  document.getElementById("dash-harvesting-body").innerHTML = harvesting.map((l) => `
    <div class="p-3 urgency-${l.urgency}">
      <div class="font-semibold text-sm">${l.slip_number} <span class="text-xs font-normal text-slate-500">${l.supplier_name}</span></div>
      <div class="text-sm">${l.total_crates} crates / ${l.total_kg.toFixed(1)} kg - ${l.age_minutes} min ago</div>
    </div>
  `).join("") || `<div class="p-3 text-sm text-slate-400">Nothing currently being harvested</div>`;

  document.getElementById("dash-intransit-title").textContent = `In transit - ${t.crates} crates / ${t.kg.toFixed(1)} kg`;
  document.getElementById("dash-intransit-body").innerHTML = inTransit.map((l) => `
    <div class="p-3 urgency-${l.urgency}">
      <div class="font-semibold text-sm">${l.slip_number} <span class="text-xs font-normal text-slate-500">${l.supplier_name}</span></div>
      <div class="text-sm">${l.total_crates} crates / ${l.total_kg.toFixed(1)} kg - ${l.age_minutes} min ago</div>
    </div>
  `).join("") || `<div class="p-3 text-sm text-slate-400">Nothing currently in transit</div>`;

  document.getElementById("dash-received-title").textContent = `Received - ${r.crates} crates / ${r.kg.toFixed(1)} kg`;
  document.getElementById("dash-received-body").innerHTML = received.map((l) => `
    <button type="button" data-lot-id="${l.id}" class="received-lot-row w-full text-left p-3 hover:bg-slate-50">
      <div class="font-semibold text-sm">${l.slip_number} <span class="text-xs font-normal text-slate-500">${l.supplier_name}</span></div>
      <div class="text-sm text-slate-600">${l.total_crates} crates / ${l.total_kg.toFixed(1)} kg - received ${Boord.fmtDateTime(l.received_at)} <span class="text-blue-700">· view / edit crates</span></div>
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
      <td class="p-2">${w.name}</td>
      <td class="p-2">${w.supplier_name}</td>
      <td class="p-2">${w.crates}</td>
      <td class="p-2">${w.total_kg.toFixed(1)}</td>
      <td class="p-2">R${w.amount_due.toFixed(2)}</td>
      <td class="p-2">${w.avg_kg_crate.toFixed(1)}</td>
    </tr>
  `).join("") || `<tr><td class="p-2 text-slate-400" colspan="6">No harvest activity in this period</td></tr>`;

  document.getElementById("dash-blocks-title").textContent = `Blocks - ${summary.blocks.length} blocks`;
  document.getElementById("dash-blocks-rows").innerHTML = summary.blocks.map((b) => `
    <tr class="border-b">
      <td class="p-2">${b.name}</td>
      <td class="p-2">${b.crates}</td>
      <td class="p-2">${b.total_kg.toFixed(1)}</td>
      <td class="p-2">${b.avg_kg_crate.toFixed(1)}</td>
      <td class="p-2">${b.avg_kg_tree != null ? b.avg_kg_tree.toFixed(1) : "-"}</td>
      <td class="p-2">${b.avg_kg_hectare != null ? b.avg_kg_hectare.toFixed(1) : "-"}</td>
    </tr>
  `).join("") || `<tr><td class="p-2 text-slate-400" colspan="6">No harvest activity in this period</td></tr>`;
}

// ---------------------------------------------------------------------
// Lot crates (Received list drill-down + correcting a field-captured crate)
// ---------------------------------------------------------------------
let _lotCratesContext = null; // { lot, crates } for whichever lot is open

function _apiErrorDetail(e) {
  // Boord.api() throws `new Error("${status} ${bodyText}")` - FastAPI's body is
  // usually {"detail": "..."}, so pull that out rather than toasting raw JSON.
  const bodyText = String((e && e.message) || e || "").replace(/^\d+\s*/, "");
  try {
    const parsed = JSON.parse(bodyText);
    if (parsed && typeof parsed.detail === "string") return parsed.detail;
  } catch (err) { /* not JSON - the raw text is all there is */ }
  return bodyText || "Unknown error";
}

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

function renderLotCratesModal() {
  if (!_lotCratesContext) return;
  const { lot, crates } = _lotCratesContext;
  document.getElementById("lotCratesTitle").textContent = `Lot ${lot.slip_number}`;
  document.getElementById("lotCratesMeta").textContent =
    `${lot.total_crates} crates / ${lot.total_kg.toFixed(1)} kg - received ${Boord.fmtDateTime(lot.received_at)}`;

  document.getElementById("lotCratesRows").innerHTML = crates.map((c) => {
    const net = (c.weight_kg - (c.deduction_kg || 0)).toFixed(1);
    const editedNote = c.edited_at
      ? `<div class="text-[11px] text-amber-700">edited by ${c.edited_by || "admin"}, ${Boord.fmtDateTime(c.edited_at)}</div>`
      : "";
    return `
      <tr class="border-b align-top">
        <td class="p-2 whitespace-nowrap">${Boord.fmtTime(c.timestamp)}</td>
        <td class="p-2">${c.block_id || ""}</td>
        <td class="p-2">${_workerName(c.worker_id)}${editedNote}</td>
        <td class="p-2">${c.weight_kg.toFixed(1)}</td>
        <td class="p-2">${(c.deduction_kg || 0).toFixed(1)}</td>
        <td class="p-2 font-semibold">${net}</td>
        <td class="p-2 text-right"><button class="text-blue-700 text-xs" data-edit-crate="${c.uuid}">Edit</button></td>
      </tr>`;
  }).join("") || `<tr><td class="p-2 text-slate-400" colspan="7">No crates on this lot</td></tr>`;

  document.querySelectorAll("#lotCratesRows [data-edit-crate]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const crate = _lotCratesContext.crates.find((c) => c.uuid === btn.dataset.editCrate);
      if (crate) editCrate(crate);
    });
  });
}

function renderWagesWarning(wagesAffected) {
  // Left alone (never cleared) when a save comes back with nothing affected,
  // so correcting a second crate in the same session doesn't silently drop
  // the warning from the first one.
  if (!wagesAffected || !wagesAffected.length) return;
  const el = document.getElementById("lotCratesWagesWarning");
  const lines = wagesAffected.map((w) =>
    `<strong>${w.worker_name}</strong>: wages for ${w.period_start} to ${w.period_end} were already calculated and do not reflect this change.`);
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

  openEditModal("Edit Crate", [
    { key: "worker_id", label: "Worker", type: "select", options },
    { key: "weight_kg", label: "Weight (kg)", type: "number" },
    { key: "deduction_kg", label: "Deduction (kg)", type: "number" },
  ], crate, async (values) => {
    const body = {
      worker_id: values.worker_id,
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
      Boord.toast("Could not save: " + _apiErrorDetail(e));
      throw e; // the bindMasterData save handler only closes the modal on
      // success - rethrowing keeps it open so a bad number can be fixed
      // without re-entering everything.
    }
    Boord.toast("Crate updated");

    if (_lotCratesContext) {
      const idx = _lotCratesContext.crates.findIndex((c) => c.uuid === crate.uuid);
      if (idx >= 0) _lotCratesContext.crates[idx] = result.record;
      if (result.lot && _lotCratesContext.lot.id === result.lot.lot_id) {
        _lotCratesContext.lot.total_crates = result.lot.total_crates;
        _lotCratesContext.lot.total_kg = result.lot.total_kg;
      }
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
        ? `<select data-key="${f.key}" class="w-full border border-slate-300 rounded-lg p-2">${f.options.map((o) => `<option value="${o.value}">${o.label}</option>`).join("")}</select>`
        : f.type === "checkbox"
        ? `<label class="flex items-center gap-2 mt-1"><input data-key="${f.key}" type="checkbox" class="w-4 h-4"> <span class="text-sm">${f.label}</span></label>`
        : f.type === "file"
        ? `<div class="flex gap-2">
             <input data-key="${f.key}" type="file" accept="image/*" capture="environment" class="flex-1 min-w-0 border border-slate-300 rounded-lg p-2">
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
    await editContext.onSave(values);
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

async function exportFile(path, filename) {
  const blob = await Boord.api(path, { timeoutMs: Boord.UPLOAD_TIMEOUT_MS });
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
  // Full records, because the Edit modal needs id_number/bank/account.
  // /api/workers hands those back only to the admin - decided from the
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
      <td class="p-2"><input type="checkbox" class="worker-select-checkbox w-4 h-4" data-select="${w.id}"></td>
      <td class="p-2">${w.photo_filename
        ? `<img src="/photos/${w.photo_filename}" class="w-8 h-8 rounded-full object-cover">`
        : '<span class="w-8 h-8 rounded-full bg-slate-200 inline-flex items-center justify-center text-slate-400 text-xs">?</span>'}</td>
      <td class="p-2 font-mono">${w.id}</td>
      <td class="p-2">${w.first_name || ""}</td>
      <td class="p-2">${w.last_name || w.name || ""}</td>
      <td class="p-2 text-xs">${suppliers.get(w.supplier_id) || ""}</td>
      <td class="p-2">${w.active ? '<span class="text-green-600 text-xs">Active</span>' : '<span class="text-slate-400 text-xs">Inactive</span>'}</td>
      <td class="p-2 text-right space-x-2">
        <button class="text-blue-700 text-xs" data-edit="${w.id}">Edit</button>
        <button class="text-slate-500 text-xs" data-qr="${w.id}">QR</button>
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
    active.map((s) => `<option value="${s.id}">${s.name}${s.is_own_farm ? " (Own fruit)" : ""}</option>`).join("");
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
    { key: "id_number", label: "SA ID Number" },
    { key: "bank", label: "Bank" },
    { key: "account", label: "Account Number" },
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
      <td class="p-2">${t.id}</td><td class="p-2">${t.name}</td><td class="p-2">${t.induna}</td>
      <td class="p-2">${t.active ? '<span class="text-green-600 text-xs">Active</span>' : '<span class="text-slate-400 text-xs">Inactive</span>'}</td>
      <td class="p-2 text-right"><button class="text-blue-700 text-xs" data-edit="${t.id}">Edit</button></td>
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
  window._blocksCache = blocks;
  document.getElementById("blocksTable").innerHTML = blocks.map((b) => `
    <tr class="border-b ${b.active ? "" : "opacity-50"}">
      <td class="p-2">${b.id}</td><td class="p-2">${b.variety}</td><td class="p-2">${b.trees}</td>
      <td class="p-2">${b.hectares}</td>
      <td class="p-2 text-xs">${supplierName(b.supplier_id)}</td>
      <td class="p-2">${b.active ? '<span class="text-green-600 text-xs">Active</span>' : '<span class="text-slate-400 text-xs">Inactive</span>'}</td>
      <td class="p-2 text-right"><button class="text-blue-700 text-xs" data-edit="${b.id}">Edit</button></td>
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
      <td class="p-2">${d.id}</td><td class="p-2">${d.role}</td><td class="p-2">${d.station}</td><td class="p-2">${d.team_id || ""}</td>
      <td class="p-2 text-xs">${d.role === "field" ? (supplierName(d.supplier_id) || "Own fruit") : ""}</td>
      <td class="p-2">${Boord.fmtDateTime(d.last_seen, "never")}</td>
      <td class="p-2 text-right"><button class="text-blue-700 text-xs" data-edit="${d.id}">Edit</button></td>
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
      <td class="p-2">${s.name}${s.is_own_farm ? ' <span class="text-xs text-blue-700 font-semibold">(Own fruit)</span>' : ""}</td>
      <td class="p-2 text-xs">${s.contact_name || ""}${s.contact_phone ? ` - ${s.contact_phone}` : ""}</td>
      <td class="p-2 text-xs">${s.packing_rate_per_kg > 0 ? `R${s.packing_rate_per_kg.toFixed(2)}/kg` : s.packing_rate_per_crate > 0 ? `R${s.packing_rate_per_crate.toFixed(2)}/crate` : "-"}</td>
      <td class="p-2">${s.active ? '<span class="text-green-600 text-xs">Active</span>' : '<span class="text-slate-400 text-xs">Inactive</span>'}</td>
      <td class="p-2 text-right"><button class="text-blue-700 text-xs" data-edit="${s.id}">Edit</button></td>
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
    ? external.map((s) => `<option value="${s.id}">${s.name}</option>`).join("")
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
    summaryEl.textContent = `${data.lots.length} lot${data.lots.length === 1 ? "" : "s"} - ${data.total_crates} crates - ${data.total_kg.toFixed(1)} kg - Rate: R${data.rate.toFixed(2)} ${data.rate_type === "per_kg" ? "/kg" : "/crate"} - Amount Due: R${data.amount_due.toFixed(2)}`;
    summaryEl.classList.remove("hidden");
    document.getElementById("billingTable").innerHTML = data.lots.map((l) => `
      <tr class="border-b">
        <td class="p-2 font-mono">${l.slip_number}</td>
        <td class="p-2">${Boord.fmtDateTime(l.received_at)}</td>
        <td class="p-2">${l.crates}</td>
        <td class="p-2">${l.kg.toFixed(1)}</td>
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
        <td class="p-2" colspan="5">${name} - ${groupPayments.length} worker${groupPayments.length === 1 ? "" : "s"} - ${totalKg.toFixed(1)} kg - R${totalWages.toFixed(2)} total wages</td>
      </tr>
    `;
    const rows = groupPayments.map((p) => {
      const w = workers.get(p.worker_id);
      const displayName = w ? (w.name || `${w.first_name} ${w.last_name}`.trim() || w.id) : p.worker_id;
      return `
        <tr class="border-b">
          <td class="p-2 text-xs text-slate-500">${name}</td>
          <td class="p-2">${displayName}</td>
          <td class="p-2">${p.total_kg.toFixed(1)}</td>
          <td class="p-2">R${p.rate_applied.toFixed(2)}/kg</td>
          <td class="p-2">R${p.amount_due.toFixed(2)}</td>
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
  const blob = await Boord.api(`/api/payments/export?period_start=${start}&period_end=${end}${supplierParam}&fmt=xlsx`);
  Boord.downloadBlob(blob, `Wages_${start}_${end}.xlsx`);
}

// ---------------------------------------------------------------------
// Reports
// ---------------------------------------------------------------------
const REPORTS = [
  { key: "daily-harvest", label: "Daily Harvest Summary", icon: "fa-sun",
    desc: "Crates and kg by block and team for one day. Uses the period start date even if a wider range is picked.",
    params: (d1, d2, s) => `day=${d1}${s ? `&supplier_id=${s}` : ""}` },
  { key: "lot-receiving", label: "Lot & Receiving Report", icon: "fa-truck",
    desc: "Every lot dispatched in the range, with its receiving detail once the truck has been checked in.",
    params: (d1, d2, s) => `date_from=${d1}&date_to=${d2}${s ? `&supplier_id=${s}` : ""}` },
  { key: "picking-notes", label: "Plukstrokies / Picking Notes", icon: "fa-clipboard-list",
    desc: "One row per dispatched lot - slip, block(s), crates sent vs received, driver, supplier, condition, weather - matching the paper picking slip.",
    params: (d1, d2, s) => `date_from=${d1}&date_to=${d2}${s ? `&supplier_id=${s}` : ""}` },
  { key: "team-picking-list", label: "Span Pluklys / Team Picking List", icon: "fa-people-group",
    desc: "One row per team per day - data capturer, induna, worker count, deductions, plus each block picked and each lot dispatched that day.",
    params: (d1, d2, s) => `date_from=${d1}&date_to=${d2}${s ? `&supplier_id=${s}` : ""}` },
  { key: "harvest-data", label: "Daaglikse Oesdata / Daily Harvest Data", icon: "fa-table-cells",
    desc: "Kg by date (rows) against block (columns) over the range, with per-day and per-block totals including avg per tree and per hectare.",
    params: (d1, d2, s) => `period_start=${d1}&period_end=${d2}${s ? `&supplier_id=${s}` : ""}` },
  { key: "harvesting-list", label: "Harvesting List", icon: "fa-seedling",
    desc: "Loads still being picked, matching the Dashboard's Harvesting list.",
    params: (d1, d2, s) => `period_start=${d1}&period_end=${d2}${s ? `&supplier_id=${s}` : ""}` },
  { key: "in-transit-list", label: "In Transit List", icon: "fa-truck-fast",
    desc: "Loads dispatched from the field but not yet received at the pack house.",
    params: (d1, d2, s) => `period_start=${d1}&period_end=${d2}${s ? `&supplier_id=${s}` : ""}` },
  { key: "received-list", label: "Pakhuis Ontvangstes / Pack House Receivables", icon: "fa-warehouse",
    desc: "Received loads - slip, date/time, receiving block, supplier, team, driver, crates, kg and rejected waste kg - matching the paper receipt list.",
    params: (d1, d2, s) => `period_start=${d1}&period_end=${d2}${s ? `&supplier_id=${s}` : ""}` },
  { key: "worker-harvest", label: "Worker Harvest Report", icon: "fa-users",
    desc: "Per-worker crates, kg, amount due and average kg per crate over the range.",
    params: (d1, d2, s) => `period_start=${d1}&period_end=${d2}${s ? `&supplier_id=${s}` : ""}` },
  { key: "litchi-wages", label: "Lietsjie Lone / Litchi Wages", icon: "fa-hand-holding-dollar",
    desc: "One row per worker with crates broken out per day worked, plus a total - a whole pay period on a single line.",
    params: (d1, d2, s) => `period_start=${d1}&period_end=${d2}${s ? `&supplier_id=${s}` : ""}` },
  { key: "block-harvest", label: "Block Harvest Report", icon: "fa-tree",
    desc: "Per-block crates, kg, average kg per crate and average kg per tree over the range.",
    params: (d1, d2, s) => `period_start=${d1}&period_end=${d2}${s ? `&supplier_id=${s}` : ""}` },
];

// Reports whose export ignores the period-end date and only ever covers a
// single day, no matter how wide a range is picked above.
const DAILY_ONLY_REPORTS = new Set(["daily-harvest"]);

function renderReportsGrid() {
  const d1 = document.getElementById("reportDate1").value;
  const d2 = document.getElementById("reportDate2").value;
  const isRange = d1 && d2 && d1 !== d2;

  document.getElementById("reportsGrid").innerHTML = REPORTS.map((r) => {
    const flagDailyOnly = isRange && DAILY_ONLY_REPORTS.has(r.key);
    const note = flagDailyOnly
      ? `<div class="text-xs text-amber-600 font-medium mt-1"><i class="fa-solid fa-triangle-exclamation mr-1"></i>Daily report only - uses ${d1} (period start)</div>`
      : `<div class="text-xs text-slate-400 mt-1">Download .xlsx</div>`;
    return `
    <button class="bg-white rounded-xl shadow p-4 text-left hover:bg-slate-50 flex items-start gap-3" data-report="${r.key}">
      <i class="fa-solid ${r.icon} text-slate-400 mt-0.5"></i>
      <div>
        <div class="font-semibold text-sm">${r.label}</div>
        <div class="text-xs text-slate-500">${r.desc || ""}</div>
        ${note}
      </div>
    </button>
  `;
  }).join("");
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
  try {
    const blob = await Boord.api(`/api/reports/${key}?${report.params(d1, d2, supplierId)}`, { timeoutMs: Boord.UPLOAD_TIMEOUT_MS });
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
      upd.innerHTML = `<b>${u.latest} is available.</b> This server is on ${u.current}. To install it, run <b>update_server.bat</b> on the server PC - it is not installed automatically. Checked ${Boord.fmtDateTime(u.checked_at)}.`;
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
      <td class="p-2 text-right"><a href="#" class="text-blue-700 text-xs" data-download="${b.filename}">Download</a></td>
    </tr>
  `).join("") || `<tr><td class="p-2 text-slate-400" colspan="3">No backups yet</td></tr>`;
  document.querySelectorAll("#backupsTable [data-download]").forEach((a) => {
    a.addEventListener("click", async (e) => {
      e.preventDefault();
      const blob = await Boord.api(`/api/backups/${a.dataset.download}/download`, { timeoutMs: Boord.UPLOAD_TIMEOUT_MS });
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
    line.innerHTML = `<b>Off-site copy is not working:</b> ${s.destination} - ${s.problem}. ${folder}.`;
  } else if (last.ok) {
    line.className = "text-sm rounded-lg p-3 bg-green-50 text-green-800 border border-green-200";
    line.innerHTML = `<b>Last copied ${Boord.fmtDateTime(last.at)}</b> to ${s.destination}. ${folder}.`;
  } else if (last.error) {
    line.className = "text-sm rounded-lg p-3 bg-red-50 text-red-800 border border-red-200";
    const runs = last.consecutive_failures > 1 ? `${last.consecutive_failures} times running` : "last night";
    line.innerHTML = `<b>Off-site copy FAILED ${runs}:</b> ${last.error} - to ${s.destination}. ${folder}.`;
  } else {
    line.className = "text-sm rounded-lg p-3 bg-slate-50 text-slate-600";
    line.innerHTML = `Copying to ${s.destination}. Nothing copied yet - the next backup will be the first. ${folder}.`;
  }

  // Warned about, not blocked. A synced folder is a decision for whoever
  // runs the pack house to make knowingly, and the name of a folder is not
  // proof either way - but nobody should discover afterwards what was in it.
  if (s.looks_like_cloud && warn) {
    warn.classList.remove("hidden");
    warn.className = "text-sm rounded-lg p-3 bg-amber-50 text-amber-800 border border-amber-200";
    warn.innerHTML = "<b>This destination looks like a synced cloud folder.</b> Backups are not encrypted and contain every worker's ID number, banking details and photograph. Until backup encryption is added, use a drive that stays on the farm - a plugged-in USB or external disk, a second disk in this PC, or another machine on the farm's own network.";
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
  const settings = await Boord.api("/api/system-settings");
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
    updateSeasonYearLabel();
  }
  const rate = await Boord.api("/api/rate-settings/current");
  if (rate) {
    document.getElementById("setRatePerKg").value = rate.default_rate_per_kg;
  }
  await loadServerCard();
  await loadBackupsList();
  await loadOffsiteStatus();
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
  };
  await Boord.api("/api/system-settings", { method: "PUT", body: newSettings });
  _systemSettings = { ..._systemSettings, ...newSettings };
  updateBannerPackhouseName();
  updateSeasonYearLabel();
  Boord.toast("Settings saved");
}

async function saveRateSettings() {
  await Boord.api("/api/rate-settings", {
    method: "POST",
    body: {
      effective_date: Boord.localDateStr(),
      rate_type: "per_kg",
      default_rate_per_kg: parseFloat(document.getElementById("setRatePerKg").value) || 0,
      tier_rates_json: "{}",
    },
  });
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

init();
