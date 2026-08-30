// Field harvest capture: offline-first. Every crate is written to IndexedDB
// first, then a background loop pushes it to the server whenever a network
// request actually succeeds - the UI never blocks on connectivity.

let deviceConfig = null;
let systemSettings = { green_to_yellow_minutes: 90, yellow_to_red_minutes: 150 };
let weightBuffer = "";

const PENDING_LOTS_KEY = "boord_pending_lots";
const CURRENT_SLIP_KEY = "boord_current_slip";

function getCurrentSlip() {
  let slip = localStorage.getItem(CURRENT_SLIP_KEY);
  if (!slip) {
    slip = newSlipNumber();
    localStorage.setItem(CURRENT_SLIP_KEY, slip);
  }
  return slip;
}

function newSlipNumber() {
  const now = new Date();
  const stamp = now.toISOString().replace(/[-:T.]/g, "").slice(0, 14);
  return `${deviceConfig.id}-${stamp}`;
}

function getPendingLots() {
  return JSON.parse(localStorage.getItem(PENDING_LOTS_KEY) || "[]");
}
function setPendingLots(lots) {
  localStorage.setItem(PENDING_LOTS_KEY, JSON.stringify(lots));
}

// Everything this screen needs to work is already on the device (IndexedDB
// crates, cached worker/block lists, cached device config), so the whole UI is
// built and painted from local data BEFORE any request is made. The network is
// contacted afterwards purely to enrich and sync. Nothing above the first
// await may depend on the server: a device in a dead spot gets no error from
// fetch(), just a long wait, and a worker must never be left staring at a
// half-drawn screen holding a full crate.
async function init() {
  document.getElementById("appVersion").textContent = `v${Boord.VERSION}`;
  const deviceId = Boord.getDeviceId();
  if (!deviceId) { location.href = "../"; return; }

  const cachedConfig = Boord.getCachedDeviceConfig(deviceId);
  if (cachedConfig) {
    deviceConfig = cachedConfig;
    renderStationLabel();
  }

  // Offline banner + instant pill flip on connectivity changes. Set up before
  // anything touches the network so failures have somewhere to show.
  Boord.offlineBanner("Offline - crates save to this device and sync when back in range");
  Boord.onOfflineChange = (off) => { if (off) showOfflineStatus(); else syncLoop(); };

  systemSettings = Boord.getCachedJSON("boord_cached_settings") || systemSettings;
  renderCachedWorkers();
  renderCachedBlocks();

  bindKeypad();
  updateWorkerDisplay();
  document.getElementById("saveCrateBtn").addEventListener("click", saveCrate);
  document.getElementById("sendSlipBtn").addEventListener("click", openDriverModal);
  document.getElementById("cancelSlipBtn").addEventListener("click", closeDriverModal);
  document.getElementById("confirmSlipBtn").addEventListener("click", sendPickingSlip);
  document.getElementById("scanBtn").addEventListener("click", openScanner);
  document.getElementById("closeScanBtn").addEventListener("click", closeScanner);
  LWPTR.attach(refreshFromServer);

  renderDispatchedLots();
  if (deviceConfig) await renderLot();
  // Screen is now live - the worker can capture crates from this point on.

  setInterval(renderLot, 15000);
  setInterval(syncLoop, 10000);
  setInterval(refreshLists, 30000);

  // Background from here; none of the above waited on it.
  const hadConfig = !!deviceConfig;
  const config = await resolveDeviceConfig(deviceId, cachedConfig);
  if (!config) return; // brand new device, server says unknown - redirecting
  if (!hadConfig) await renderLot();

  refreshFromServer();
}

// A device this browser has already set up keeps working from its cached
// config forever. Only a never-seen device needs the server, and only that
// case may bounce the user to setup - an unreachable server must never be
// mistaken for an unknown device.
async function resolveDeviceConfig(deviceId, cachedConfig) {
  try {
    deviceConfig = await Boord.fetchDeviceConfig(deviceId);
    renderStationLabel();
    // A re-allocated device gets its block list corrected on this load
    // rather than at the next 30s refresh - the crates in between would
    // otherwise be captured against the previous supplier's orchard.
    renderCachedBlocks();
    return deviceConfig;
  } catch (e) {
    if (Boord.isNetworkError(e)) {
      Boord.setOffline(true);
      if (cachedConfig) return cachedConfig;
      Boord.toast("No connection - cannot set up this device yet");
      return null;
    }
    if (cachedConfig) return cachedConfig; // server says unknown, but we've run before
    location.href = "../";
    return null;
  }
}

function renderStationLabel() {
  document.getElementById("stationLabel").textContent =
    `${deviceConfig.station} - Team ${deviceConfig.team_id || "?"} - Induna ${deviceConfig.induna || "?"}`;
  renderSupplierLabel();
}

// Who this device is picking for, stated on the screen rather than left to
// the admin's memory. The allocation decides which supplier every crate from
// this device is filed against, and a wrong one is invisible from here
// otherwise - the fruit simply arrives at the pack house under another
// grower's name, all season.
function renderSupplierLabel() {
  const el = document.getElementById("supplierLabel");
  if (!el) return;
  const supplierId = deviceConfig && deviceConfig.supplier_id;
  if (!supplierId) {
    el.textContent = "Picking own fruit";
    return;
  }
  const supplier = (Boord.getCachedJSON("boord_cached_suppliers") || [])
    .find((s) => s.id === supplierId);
  el.textContent = supplier ? `Picking for ${supplier.name}` : "Picking for another supplier";
}

async function loadSuppliers() {
  try {
    const suppliers = await Boord.api("/api/suppliers");
    localStorage.setItem("boord_cached_suppliers", JSON.stringify(suppliers));
    renderSupplierLabel();
    // The block list is filtered by this device's supplier, so a supplier
    // list that lands after the blocks did has to redraw them - these load
    // concurrently, and whichever order they finish in must give the same
    // dropdown.
    renderCachedBlocks();
  } catch (e) {
    if (Boord.isNetworkError(e)) Boord.setOffline(true);
  }
}

async function refreshLists() {
  await Promise.allSettled([loadWorkers(), loadBlocks(), loadSuppliers()]);
}

async function loadSettings() {
  try {
    systemSettings = await Boord.api("/api/system-settings");
    localStorage.setItem("boord_cached_settings", JSON.stringify(systemSettings));
  } catch (e) {
    if (Boord.isNetworkError(e)) Boord.setOffline(true);
  }
}

// The one place that goes to the server for fresh data: the periodic refresh,
// pull-to-refresh, and the tail of init all funnel through here. The requests
// are independent and run concurrently - in a dead spot each one costs a full
// timeout, and in series that would leave the refresh spinner up for the sum
// of them rather than the longest.
async function refreshFromServer() {
  await Promise.allSettled([loadSettings(), loadWorkers(), loadBlocks(), loadSuppliers(), syncLoop()]);
  await renderLot();
  renderDispatchedLots();
}

// Paints the worker list saved on this device. Called during startup so the
// dropdown is populated before - and regardless of - any network activity.
function renderCachedWorkers() {
  const cached = Boord.getCachedJSON("boord_cached_workers");
  if (cached) renderWorkerOptions(cached);
  else document.getElementById("workerSelect").innerHTML =
    `<option value="">(no worker list on this device yet)</option>`;
}

async function loadWorkers() {
  try {
    const workers = await Boord.api("/api/workers");
    localStorage.setItem("boord_cached_workers", JSON.stringify(workers));
    renderWorkerOptions(workers);
  } catch (e) {
    if (Boord.isNetworkError(e)) Boord.setOffline(true);
    renderCachedWorkers();
  }
}

function renderWorkerOptions(workers) {
  const select = document.getElementById("workerSelect");
  const current = select.value;
  const active = workers.filter((w) => w.active);
  select.innerHTML = `<option value="">— select —</option>` + active.map((w) => {
    const display = w.name || `${w.first_name || ""} ${w.last_name || ""}`.trim() || w.id;
    return `<option value="${w.id}">${display} (${w.id})</option>`;
  }).join("");
  if (current && Array.from(select.options).some((o) => o.value === current)) {
    select.value = current;
  }
  updateWorkerDisplay();
}

function updateWorkerDisplay() {
  const select = document.getElementById("workerSelect");
  const nameEl = document.getElementById("workerName");
  const icon = document.getElementById("workerCheckIcon");
  const id = select.value;
  if (id) {
    const opt = select.options[select.selectedIndex];
    nameEl.textContent = opt ? opt.text : id;
    nameEl.classList.replace("text-slate-400", "text-slate-800");
    icon.classList.replace("text-slate-200", "text-green-400");
  } else {
    nameEl.textContent = "—";
    nameEl.classList.replace("text-slate-800", "text-slate-400");
    icon.classList.replace("text-green-400", "text-slate-200");
  }
}

function renderCachedBlocks() {
  const cached = Boord.getCachedJSON("boord_cached_blocks");
  if (cached) renderBlockOptions(cached);
  else document.getElementById("blockSelect").innerHTML =
    `<option value="">(no block list on this device yet)</option>`;
}

async function loadBlocks() {
  try {
    const blocks = await Boord.api("/api/blocks");
    localStorage.setItem("boord_cached_blocks", JSON.stringify(blocks));
    renderBlockOptions(blocks);
  } catch (e) {
    if (Boord.isNetworkError(e)) Boord.setOffline(true);
    renderCachedBlocks();
  }
}

// The supplier every crate from this device is filed against: the device's
// own allocation, or the pack house's own fruit when it has none. Null when
// the supplier list has not reached this device yet.
function effectiveSupplierId() {
  if (deviceConfig && deviceConfig.supplier_id) return deviceConfig.supplier_id;
  const own = (Boord.getCachedJSON("boord_cached_suppliers") || []).find((s) => s.is_own_farm);
  return own ? own.id : null;
}

// Which blocks this device may pick from.
//
// Inactive blocks are dropped: deactivating a block is the documented way to
// retire it from capture without deleting the history that references it, and
// this list is the only place that promise is kept.
//
// The rest follows from the crate's own attribution. Every crate captured
// here is filed against this device's supplier, so offering a block that
// belongs to a different grower can only produce fruit recorded against the
// wrong one - and that reads as perfectly ordinary data afterwards. Blocks
// with no supplier are offered to everyone: unassigned means not yet
// allocated, not "somebody else's", and that is the state every install
// upgrading into this field starts in.
//
// The fallback matters as much as the rule: a pack house that allocates a
// device before assigning its blocks would otherwise be left with an empty
// dropdown and no way to pick at all, so an empty match falls back to the
// whole list rather than stranding the picker mid-orchard.
function blocksForThisDevice(blocks) {
  const active = (blocks || []).filter((b) => b.active);
  const supplierId = effectiveSupplierId();
  if (!supplierId) return active;
  const mine = active.filter((b) => !b.supplier_id || b.supplier_id === supplierId);
  return mine.length ? mine : active;
}

function renderBlockOptions(blocks) {
  const select = document.getElementById("blockSelect");
  const current = select.value;
  const usable = blocksForThisDevice(blocks);
  if (!usable.length) {
    // A new install has no blocks until someone imports them (Admin ->
    // Settings -> Master Data). Without this the dropdown just renders
    // empty, which is indistinguishable from the list having failed to load
    // - and saveCrate would then only say "Select a worker and block" with
    // nothing to pick.
    select.innerHTML = `<option value="">(no blocks set up yet - add them in Admin)</option>`;
    return;
  }
  select.innerHTML = usable.map((b) => `<option value="${b.id}">${b.name || b.id}</option>`).join("");
  if (current && Array.from(select.options).some((o) => o.value === current)) {
    select.value = current;
  }
}

function bindKeypad() {
  document.querySelectorAll(".keypad-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const k = btn.dataset.k;
      if (k === "back") {
        weightBuffer = weightBuffer.slice(0, -1);
      } else if (k === "." && weightBuffer.includes(".")) {
        // ignore extra decimal point
      } else {
        weightBuffer += k;
      }
      document.getElementById("weightDisplay").textContent = weightBuffer || "0";
    });
  });
}

async function saveCrate() {
  const weight = parseFloat(weightBuffer);
  if (!weight || weight <= 0) { Boord.toast("Enter a weight first"); return; }
  const workerId = document.getElementById("workerSelect").value;
  const blockId = document.getElementById("blockSelect").value;
  if (!workerId || !blockId) { Boord.toast("Select a worker and block"); return; }

  const record = {
    uuid: Boord.uuid(),
    timestamp: new Date().toISOString(),
    worker_id: workerId,
    block_id: blockId,
    weight_kg: weight,
    deduction_kg: 0,
    device_id: deviceConfig.id,
    team_id: deviceConfig.team_id,
    slip_number: getCurrentSlip(),
    notes: "",
    synced: false,
  };
  try {
    await IDB.add(record);
  } catch (e) {
    // Never clear the keypad on a failed save - the weight stays on screen so
    // the crate can be re-entered rather than silently lost.
    Boord.toast("Could not save the crate on this device - try again");
    return;
  }
  weightBuffer = "";
  document.getElementById("weightDisplay").textContent = "0";
  document.getElementById("workerSelect").value = "";
  updateWorkerDisplay();
  Boord.beepSaved();
  Boord.toast("Crate saved");
  await renderLot();
  syncLoop();
}

async function renderLot() {
  const slip = getCurrentSlip();
  let crates;
  try {
    crates = await IDB.getBySlip(slip);
  } catch (e) {
    Boord.toast("Could not read saved crates on this device");
    return;
  }
  const crateCount = crates.length;
  const totalKg = crates.reduce((s, c) => s + c.weight_kg - (c.deduction_kg || 0), 0);
  document.getElementById("crateCount").textContent =
    `${crateCount} ${crateCount === 1 ? "crate" : "crates"}`;
  document.getElementById("totalKg").textContent = `${totalKg.toFixed(1)} kg`;

  const card = document.getElementById("lotCard");
  card.classList.remove("urgency-green", "urgency-yellow", "urgency-red");
  if (crateCount === 0) {
    document.getElementById("elapsed").textContent = "--:--";
    card.classList.add("urgency-green");
  } else {
    const first = crates.reduce((min, c) => (c.timestamp < min ? c.timestamp : min), crates[0].timestamp);
    const minutes = (Date.now() - new Date(first).getTime()) / 60000;
    const h = Math.floor(minutes / 60), m = Math.floor(minutes % 60);
    document.getElementById("elapsed").textContent = `${h}:${String(m).padStart(2, "0")}`;
    if (minutes >= systemSettings.yellow_to_red_minutes) card.classList.add("urgency-red");
    else if (minutes >= systemSettings.green_to_yellow_minutes) card.classList.add("urgency-yellow");
    else card.classList.add("urgency-green");
  }

  const recentCrates = [...crates].sort((a, b) => b.timestamp.localeCompare(a.timestamp)).slice(0, 5);
  document.getElementById("recentList").innerHTML = recentCrates
    .map((r) => `<div class="flex justify-between border-b py-1"><span>${r.block_id}</span><span>${Number(r.weight_kg).toFixed(1)} kg</span><span>${r.synced ? "✓ synced" : "⏳ pending"}</span></div>`)
    .join("") || `<div class="text-slate-400">No crates yet</div>`;
}

function updateOfflineSplitHint() {
  const online = navigator.onLine && !Boord.isOffline();
  document.getElementById("offlineSplitHint").classList.toggle("hidden", online);
}

async function openDriverModal() {
  const slip = getCurrentSlip();
  const crates = await IDB.getBySlip(slip);
  const input = document.getElementById("cratesGoingInput");
  input.value = crates.length;
  input.max = crates.length;
  updateOfflineSplitHint();
  document.getElementById("driverModal").classList.remove("hidden");
  document.getElementById("driverModal").classList.add("flex");
}
function closeDriverModal() {
  document.getElementById("driverModal").classList.add("hidden");
  document.getElementById("driverModal").classList.remove("flex");
}

// Splits off a lot's most-recently-captured crates onto a fresh placeholder
// lot (server picks FIFO - oldest stay on the departing lot) so a truck can
// take only part of what's been picked, leaving the rest to combine with the
// next batch of picking into a later dispatch. Requires connectivity: the
// server is the only source of truth for exactly which crates currently
// share this lot, so unlike a normal dispatch this can't be queued offline.
async function splitAndAdopt(slip, keepCount) {
  const result = await Boord.api(`/api/lots/by-slip/${encodeURIComponent(slip)}/split`, {
    method: "POST",
    body: { keep_count: keepCount },
  });
  await IDB.reassignSlip(result.moved_uuids, result.new_lot.slip_number);
  localStorage.setItem(CURRENT_SLIP_KEY, result.new_lot.slip_number);
  return result;
}

async function sendPickingSlip() {
  const driver = document.getElementById("driverInput").value.trim();
  if (!driver) { Boord.toast("Enter the driver's name"); return; }

  const slip = getCurrentSlip();
  let crates = await IDB.getBySlip(slip);
  if (crates.length === 0) { Boord.toast("No crates logged yet"); return; }

  const totalPending = crates.length;
  const cratesGoing = parseInt(document.getElementById("cratesGoingInput").value, 10) || totalPending;
  let didSplit = false;

  if (cratesGoing < totalPending) {
    if (!navigator.onLine || Boord.isOffline()) {
      updateOfflineSplitHint();
      Boord.toast("Reconnect to send a partial load, or dispatch everything now.");
      return;
    }
    try {
      const splitResult = await splitAndAdopt(slip, totalPending - cratesGoing);
      const movedUuids = new Set(splitResult.moved_uuids);
      crates = crates.filter((c) => !movedUuids.has(c.uuid));
      didSplit = true;
    } catch (e) {
      Boord.toast("Could not split the load - try again: " + (e.message || e));
      return;
    }
  }

  const first = crates.reduce((min, c) => (c.timestamp < min ? c.timestamp : min), crates[0].timestamp);
  const totalKg = crates.reduce((s, c) => s + c.weight_kg - (c.deduction_kg || 0), 0);
  const byBlock = {};
  crates.forEach((c) => { byBlock[c.block_id] = (byBlock[c.block_id] || 0) + c.weight_kg; });

  const lotPayload = {
    slip_number: slip,
    timestamp: first,
    device_id: deviceConfig.id,
    team_id: deviceConfig.team_id,
    driver,
    total_crates: crates.length,
    total_kg: totalKg,
    status: "in_transit",
  };

  try {
    await Boord.api("/api/lots", { method: "POST", body: lotPayload });
  } catch (e) {
    const pending = getPendingLots();
    pending.push(lotPayload);
    setPendingLots(pending);
  }

  addDispatchedLot(slip, crates.length, totalKg);
  renderDispatchedLots();
  if (!didSplit) localStorage.removeItem(CURRENT_SLIP_KEY);
  closeDriverModal();
  document.getElementById("driverInput").value = "";
  showSlipSuccess(slip, crates.length, totalKg);
  await renderLot();
  syncLoop();
}

// Housekeeping after a successful upload: drop long-since-synced crates so
// the local store stays small. Throttled to once a day because it walks the
// store, and it must never take the sync loop down with it - a device that
// can't prune is merely slower, whereas a thrown error here would surface as
// "sync failed" on a sync that actually worked.
const PRUNE_INTERVAL_MS = 24 * 60 * 60 * 1000;
let _lastPrune = 0;

async function pruneOldCrates() {
  if (Date.now() - _lastPrune < PRUNE_INTERVAL_MS) return;
  _lastPrune = Date.now();
  try {
    // Read the key directly rather than getCurrentSlip(), which mints a new
    // slip as a side effect - housekeeping must not start a picking slip.
    await IDB.pruneSynced(localStorage.getItem(CURRENT_SLIP_KEY));
  } catch (e) {
    console.warn("Could not prune old crates:", e);
  }
}

// Header pill: text + color so the state is readable at arm's length in the
// orchard. green = all synced, amber = pushing/pending, red = offline.
function setSyncStatus(state, text) {
  const el = document.getElementById("syncStatus");
  el.textContent = text;
  el.classList.remove("bg-white/20", "bg-green-600", "bg-amber-400", "text-slate-900", "bg-red-600");
  if (state === "synced") el.classList.add("bg-green-600");
  else if (state === "pending") el.classList.add("bg-amber-400", "text-slate-900");
  else if (state === "offline") el.classList.add("bg-red-600");
  else el.classList.add("bg-white/20");
}

async function showOfflineStatus() {
  let text = "Offline";
  try {
    const unsynced = await IDB.getUnsynced();
    const pendingLots = getPendingLots();
    const count = unsynced.length + pendingLots.length;
    if (count > 0) text = `Offline - ${count} pending`;
  } catch (e) { /* count is nice-to-have */ }
  setSyncStatus("offline", text);
  Boord.setOffline(true);
}

let _syncBusy = false;
let _lastProbe = 0;
// How stale a reachability check may be before an idle device asks again.
// Bounds how long the pill can keep claiming "Online" after the signal drops
// with nothing queued to send; any capture tests the connection right away.
const PROBE_INTERVAL_MS = 15000;
async function syncLoop() {
  if (_syncBusy) return; // interval + online event + PTR can overlap; never double-post
  _syncBusy = true;
  try {
    if (!navigator.onLine) {
      await showOfflineStatus();
      return;
    }
    // Flush pending lot dispatches first (order matters less since the
    // server resolves lots lazily by slip_number either way).
    const pendingLots = getPendingLots();
    const stillPending = [];
    let networkFailure = false;
    for (const lot of pendingLots) {
      try {
        await Boord.api("/api/lots", { method: "POST", body: lot });
      } catch (e) {
        if (Boord.isNetworkError(e)) networkFailure = true;
        stillPending.push(lot);
      }
    }
    setPendingLots(stillPending);
    if (networkFailure) {
      // WiFi says online but the farm server is unreachable - that IS offline
      // as far as the worker cares.
      await showOfflineStatus();
      return;
    }

    // Only a request that actually came back proves the server is reachable.
    // A pass that sends nothing proves nothing, and must leave the status
    // alone rather than announce a connection it never tested.
    let reachedServer = false;

    const unsynced = await IDB.getUnsynced();
    if (unsynced.length > 0) {
      // "Syncing" is shown while an upload is genuinely in flight - not after
      // it lands, and never on a device already known to be offline, where
      // every attempt is doomed and would just flash a misleading label for
      // the length of the timeout. There, refresh the pending count instead.
      if (Boord.isOffline()) await showOfflineStatus();
      else setSyncStatus("pending", `Syncing ${unsynced.length}...`);
      const records = unsynced.map(({ synced, ...rest }) => rest);
      await Boord.api("/api/sync/harvest", { method: "POST", body: { records } });
      await IDB.markSynced(unsynced.map((r) => r.uuid));
      reachedServer = true;
      await pruneOldCrates();
    } else if (stillPending.length === 0 && Date.now() - _lastProbe >= PROBE_INTERVAL_MS) {
      // Nothing to push - but "nothing to push" must not be mistaken for
      // "server reachable". Probe cheaply so an idle device still shows the
      // truth (and keeps its urgency thresholds current). Throttled because
      // the loop ticks every 10s and an idle device needn't ask that often -
      // saving or dispatching a crate tests the connection immediately anyway.
      systemSettings = await Boord.api("/api/system-settings");
      _lastProbe = Date.now();
      reachedServer = true;
    }

    if (!reachedServer) return; // nothing verified this pass - leave the pill as it was

    Boord.setOffline(false);
    // Everything just uploaded is now synced; only lots the server itself
    // refused are still queued.
    if (stillPending.length > 0) setSyncStatus("pending", "Syncing...");
    else setSyncStatus("synced", "Online - synced");
  } catch (e) {
    if (Boord.isNetworkError(e)) {
      await showOfflineStatus();
    } else {
      setSyncStatus("pending", "Online - sync failed, retrying");
    }
  } finally {
    _syncBusy = false;
  }
}

let qrScanner = null;
function openScanner() {
  // A browser only hands out the camera on a secure origin - https, or
  // localhost. A field tablet reaches this server as plain http://<ip>/ over
  // the pack house LAN, which is neither, so getUserMedia is refused before
  // any permission prompt appears. Saying so here is the difference between
  // "this tablet's camera is broken" and "scanning needs https - pick the
  // worker from the list instead", and the worker is standing in an orchard
  // holding a full crate while they work it out.
  if (!window.isSecureContext) {
    Boord.toast("Scanning needs a secure (https) connection - pick the worker from the list below");
    return;
  }
  document.getElementById("scanModal").classList.remove("hidden");
  document.getElementById("scanModal").classList.add("flex");
  qrScanner = new Html5Qrcode("qrReader");
  qrScanner.start(
    { facingMode: "environment" },
    { fps: 10, qrbox: 200 },
    (decodedText) => {
      const select = document.getElementById("workerSelect");
      const match = Array.from(select.options).find((o) => o.value === decodedText.trim());
      if (match) {
        select.value = decodedText.trim();
        updateWorkerDisplay();
        Boord.beepScanned();
        Boord.toast("Worker selected");
      } else {
        Boord.toast("QR code doesn't match a known worker");
      }
      closeScanner();
    },
    () => {}
  ).catch(() => {
    // Close it rather than leaving an empty black box on screen with a
    // toast that has already faded - a modal that cannot scan is only in
    // the way of the worker dropdown that still works.
    closeScanner();
    Boord.toast("Camera unavailable - pick the worker from the list below");
  });
}
function closeScanner() {
  document.getElementById("scanModal").classList.add("hidden");
  document.getElementById("scanModal").classList.remove("flex");
  // stop() throws - synchronously, so a .catch() alone does not hold it -
  // when the scanner never started, which is exactly the case this is
  // called in when the camera was refused. Closing the modal must not
  // depend on the camera having worked.
  if (qrScanner) {
    try { Promise.resolve(qrScanner.stop()).catch(() => {}); } catch (e) { /* never started */ }
    qrScanner = null;
  }
}

const DISPATCHED_KEY = "boord_dispatched_lots";

function addDispatchedLot(slip, crates, kg) {
  const all = JSON.parse(localStorage.getItem(DISPATCHED_KEY) || "[]");
  const today = Boord.localDateStr();
  const cutoff = Boord.localDateStr(new Date(Date.now() - 30 * 86400000));
  const recent = all.filter((d) => d.date >= cutoff);
  recent.push({ date: today, slip, crates, kg, time: new Date().toLocaleTimeString() });
  localStorage.setItem(DISPATCHED_KEY, JSON.stringify(recent));
}

function renderDispatchedLots() {
  const all = JSON.parse(localStorage.getItem(DISPATCHED_KEY) || "[]");
  const today = Boord.localDateStr();
  const dispatched = all.filter((d) => d.date === today);
  const listEl = document.getElementById("dispatchedList");
  const summaryEl = document.getElementById("dispatchedSummary");
  if (!dispatched.length) {
    listEl.innerHTML = `<div class="text-slate-400 py-1">No lots dispatched yet today</div>`;
    summaryEl.classList.add("hidden");
    return;
  }
  listEl.innerHTML = dispatched.map((d) => `
    <div class="flex justify-between border-b py-1">
      <span class="font-mono text-slate-600">${d.slip.split("-").slice(-1)[0]}</span>
      <span>${d.crates} crates / ${Number(d.kg).toFixed(1)} kg</span>
      <span class="text-slate-400">${d.time}</span>
    </div>`).join("");
  const totalCrates = dispatched.reduce((s, d) => s + d.crates, 0);
  const totalKg = dispatched.reduce((s, d) => s + d.kg, 0);
  summaryEl.textContent = `${dispatched.length} lot${dispatched.length > 1 ? "s" : ""} — ${totalCrates} crates — ${totalKg.toFixed(1)} kg`;
  summaryEl.classList.remove("hidden");
}

function showSlipSuccess(slip, crates, kg) {
  const banner = document.getElementById("slipSuccessBanner");
  document.getElementById("slipSuccessDetail").textContent =
    `${crates} crates — ${kg.toFixed(1)} kg dispatched`;
  banner.classList.remove("hidden");
  setTimeout(() => banner.classList.add("hidden"), 5000);
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(() => {});
}

init();
