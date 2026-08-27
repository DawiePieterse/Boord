let deviceConfig = null;
let selectedLot = null;
let _lastRefreshed = null;
let _suppliersCache = [];

// Built from local data first, then enriched from the server - a device that
// cannot reach the server gets no error from fetch(), only a long wait, so
// nothing the receiver needs may sit behind a request. See the same note in
// field/app.js.
async function init() {
  document.getElementById("appVersion").textContent = `v${Boord.VERSION}`;
  const deviceId = Boord.getDeviceId();
  if (!deviceId) { location.href = "../"; return; }

  const cachedConfig = Boord.getCachedDeviceConfig(deviceId);
  if (cachedConfig) {
    deviceConfig = cachedConfig;
    document.getElementById("stationLabel").textContent = deviceConfig.station;
  }
  _suppliersCache = Boord.getCachedJSON("boord_cached_suppliers") || [];

  document.getElementById("refreshBtn").addEventListener("click", refresh);
  document.getElementById("cancelReceiveBtn").addEventListener("click", closeModal);
  document.getElementById("confirmReceiveBtn").addEventListener("click", confirmReceipt);
  document.getElementById("externalLotBtn").addEventListener("click", openExternalLotModal);
  document.getElementById("cancelExternalLotBtn").addEventListener("click", closeExternalLotModal);
  document.getElementById("confirmExternalLotBtn").addEventListener("click", confirmExternalLot);

  Boord.offlineBanner("Offline - showing the last loaded queue; it will refresh when back in range");
  Boord.onOfflineChange = (off) => {
    updateLastUpdatedLabel();
    if (!off) refresh(); // reconnected - get the real queue right away
  };
  LWPTR.attach(async () => {
    await loadSuppliers();
    await refresh();
  });

  // Show the queue this device already has before touching the network.
  renderCachedQueue();
  setInterval(refresh, 30000);
  setInterval(updateLastUpdatedLabel, 10000);

  // Background from here; the screen is already usable.
  const config = await resolveDeviceConfig(deviceId, cachedConfig);
  if (!config) return;
  await loadSuppliers();
  await refresh();
}

async function resolveDeviceConfig(deviceId, cachedConfig) {
  try {
    deviceConfig = await Boord.fetchDeviceConfig(deviceId);
    document.getElementById("stationLabel").textContent = deviceConfig.station;
    return deviceConfig;
  } catch (e) {
    if (Boord.isNetworkError(e)) {
      Boord.setOffline(true);
      if (cachedConfig) return cachedConfig;
      Boord.toast("No connection - cannot set up this device yet");
      return null;
    }
    if (cachedConfig) return cachedConfig;
    location.href = "../";
    return null;
  }
}

async function loadSuppliers() {
  try {
    _suppliersCache = await Boord.api("/api/suppliers");
    localStorage.setItem("boord_cached_suppliers", JSON.stringify(_suppliersCache));
  } catch (e) { /* keep last known list if offline */ }
}

const QUEUE_CACHE_KEY = "boord_cached_intransit";

async function refresh() {
  let lots = [];
  try {
    lots = await Boord.api("/api/lots/in-transit");
    localStorage.setItem(QUEUE_CACHE_KEY, JSON.stringify({ at: Date.now(), lots }));
    Boord.setOffline(false);
  } catch (e) {
    if (!Boord.isNetworkError(e)) { Boord.toast("Could not reach server"); return; }
    Boord.setOffline(true); // server unreachable
    // Fall back to the last queue we saw, so the receiver still knows which
    // loads are on their way in. Only useful on a cold load - if lots are
    // already on screen, leave them alone.
    if (!_lastRefreshed) renderCachedQueue();
    return;
  }
  renderQueue(lots);
  _lastRefreshed = Date.now();
  updateLastUpdatedLabel();
}

// Draws the last in-transit queue this device saw. Used at startup and
// whenever a cold refresh can't reach the server.
function renderCachedQueue() {
  const cached = Boord.getCachedJSON(QUEUE_CACHE_KEY);
  if (!cached || !cached.lots) {
    document.getElementById("emptyState").classList.add("hidden");
    document.getElementById("lotsList").innerHTML =
      `<div class="text-center text-slate-400 py-10">No queue saved on this device yet - reconnect to load it.</div>`;
    updateLastUpdatedLabel();
    return;
  }
  _lastRefreshed = cached.at;
  renderQueue(cached.lots);
}

function renderQueue(lots) {
  const list = document.getElementById("lotsList");
  const empty = document.getElementById("emptyState");
  empty.classList.toggle("hidden", lots.length > 0);

  list.innerHTML = lots.map((lot) => `
    <button class="w-full text-left rounded-xl p-4 shadow urgency-${lot.urgency} lot-item" data-id="${lot.id}">
      <div class="flex justify-between items-center">
        <div>
          <div class="font-bold">${lot.slip_number}</div>
          <div class="text-sm text-slate-600">
            ${lot.supplier_name ? `<span class="font-semibold">${lot.supplier_name}</span> - ` : ""}
            Team ${lot.team_id || "?"} - Driver ${lot.driver || "?"}
          </div>
        </div>
        <div class="text-right">
          <div class="font-semibold">${lot.total_crates} crates / ${lot.total_kg.toFixed(1)} kg</div>
          <div class="text-xs text-slate-500">${lot.age_minutes} min ago</div>
        </div>
      </div>
      ${lot.related_lots && lot.related_lots.length ? `
        <div class="text-xs text-amber-700 font-semibold mt-1">
          <i class="fa-solid fa-triangle-exclamation"></i> Split load - ${lot.related_lots.length} related slip(s)
        </div>` : ""}
    </button>
  `).join("");

  list.querySelectorAll(".lot-item").forEach((el) => {
    el.addEventListener("click", () => openReceiveModal(lots.find((l) => l.id == el.dataset.id)));
  });
  updateLastUpdatedLabel();
}

function updateLastUpdatedLabel() {
  const btn = document.getElementById("refreshBtn");
  const el = document.getElementById("lastUpdatedLabel");
  if (!btn || !el) return;
  if (!_lastRefreshed) {
    el.textContent = Boord.isOffline() ? "offline" : "updating...";
  } else {
    const secs = Math.round((Date.now() - _lastRefreshed) / 1000);
    const age = secs < 60 ? (secs < 5 ? "just now" : `${secs}s ago`) : `${Math.round(secs / 60)} min ago`;
    el.textContent = Boord.isOffline() ? `offline - last update ${age}` : age;
  }
  btn.classList.toggle("bg-red-600", Boord.isOffline());
  btn.classList.toggle("bg-white/20", !Boord.isOffline());
}

function renderRelatedLots(lot) {
  const box = document.getElementById("relatedLotsBox");
  const related = lot.related_lots || [];
  if (!related.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  const statusText = (r) => {
    if (r.status === "received") return `received ${Boord.fmtTime(r.received_at)}`;
    if (r.status === "in_transit") return "still in transit";
    return "still being picked in the field";
  };
  box.classList.remove("hidden");
  box.innerHTML = `
    <div class="text-xs font-semibold text-amber-700 mb-1">
      <i class="fa-solid fa-triangle-exclamation"></i> Split load - part of this pickup was also sent separately:
    </div>
    ${related.map((r) => `
      <div class="text-xs text-amber-700">Slip ${r.slip_number} - ${r.total_crates} crates / ${r.total_kg.toFixed(1)} kg - ${statusText(r)}</div>
    `).join("")}
  `;
}

function openReceiveModal(lot) {
  selectedLot = lot;
  document.getElementById("modalSlip").textContent = lot.slip_number;
  document.getElementById("modalMeta").textContent =
    `${lot.supplier_name ? lot.supplier_name + " - " : ""}Team ${lot.team_id || "?"} - Driver ${lot.driver || "?"} - dispatched ${lot.age_minutes} min ago`;
  document.getElementById("expectedCrates").textContent = lot.total_crates;
  document.getElementById("actualCrates").value = lot.total_crates;
  document.getElementById("notes").value = "";
  document.getElementById("receivedBy").value = Boord.getLastReceivedBy();
  document.querySelectorAll("#conditionOptions input").forEach((cb) => { cb.checked = cb.value === "Good"; });
  renderRelatedLots(lot);

  document.getElementById("receiveModal").classList.remove("hidden");
  document.getElementById("receiveModal").classList.add("flex");
}

function closeModal() {
  document.getElementById("receiveModal").classList.add("hidden");
  document.getElementById("receiveModal").classList.remove("flex");
  selectedLot = null;
}

async function confirmReceipt() {
  if (!selectedLot) return;
  const actualCrates = parseInt(document.getElementById("actualCrates").value, 10) || 0;
  const condition = Array.from(document.querySelectorAll("#conditionOptions input:checked")).map((cb) => cb.value).join(", ") || "Good";
  const notes = document.getElementById("notes").value;
  const receivedBy = document.getElementById("receivedBy").value.trim();
  if (!receivedBy) { Boord.toast("Enter who received this lot"); return; }
  Boord.setLastReceivedBy(receivedBy);

  try {
    await Boord.api("/api/receiving", {
      method: "POST",
      body: {
        lot_id: selectedLot.id,
        timestamp: new Date().toISOString(),
        expected_crates: selectedLot.total_crates,
        actual_crates: actualCrates,
        condition,
        notes,
        received_by: receivedBy,
      },
    });
  } catch (e) {
    Boord.toast("Could not confirm - check connection and retry");
    return;
  }

  Boord.beepSaved();
  Boord.toast("Receipt confirmed");
  closeModal();
  await refresh();
}

function openExternalLotModal() {
  const select = document.getElementById("extSupplierSelect");
  const external = _suppliersCache.filter((s) => s.active && !s.is_own_farm);
  select.innerHTML = external.length
    ? external.map((s) => `<option value="${s.id}">${s.name}</option>`).join("")
    : `<option value="">(no external suppliers set up yet)</option>`;
  document.getElementById("extCrates").value = "";
  document.getElementById("extKg").value = "";
  document.getElementById("extDriver").value = "";
  document.getElementById("extNotes").value = "";

  document.getElementById("externalLotModal").classList.remove("hidden");
  document.getElementById("externalLotModal").classList.add("flex");
}

function closeExternalLotModal() {
  document.getElementById("externalLotModal").classList.add("hidden");
  document.getElementById("externalLotModal").classList.remove("flex");
}

async function confirmExternalLot() {
  const supplierId = parseInt(document.getElementById("extSupplierSelect").value, 10);
  const crates = parseInt(document.getElementById("extCrates").value, 10) || 0;
  const kg = parseFloat(document.getElementById("extKg").value) || 0;
  const driver = document.getElementById("extDriver").value.trim();
  const notes = document.getElementById("extNotes").value;
  if (!supplierId) { Boord.toast("Select a supplier"); return; }
  if (!crates || !kg) { Boord.toast("Enter crates and kg"); return; }

  try {
    await Boord.api("/api/lots/external", {
      method: "POST",
      body: { supplier_id: supplierId, driver, total_crates: crates, total_kg: kg, notes },
    });
    Boord.beepSaved();
    Boord.toast("Delivery logged");
    closeExternalLotModal();
    await refresh();
  } catch (e) {
    Boord.toast("Could not log delivery - check connection and retry");
  }
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(() => {});
}

init();
