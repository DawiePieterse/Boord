// First-run setup wizard. Shown instead of the admin tabs while
// GET /api/setup/state reports `required` - see backend/routers/setup.py for
// what makes that true, and why it takes more than a flag to decide it.
//
// Every step calls the ordinary endpoint Settings already uses. Nothing here
// is a special first-run write path, which is what keeps the wizard from
// becoming a second, divergent way to configure a pack house.

const WIZARD_STEPS = [
  { key: "identity", optional: false },
  { key: "location", optional: false },
  { key: "rate", optional: false },
  { key: "thresholds", optional: true },
  { key: "blocks", optional: true },
  { key: "workers", optional: true },
  { key: "devices", optional: true },
  { key: "finish", optional: false },
];

let _wizIndex = 0;
let _wizBound = false;

function wizStep() { return WIZARD_STEPS[_wizIndex]; }

function wizError(message) {
  const el = document.getElementById("wizError");
  if (!message) { el.classList.add("hidden"); return; }
  el.textContent = message;
  el.classList.remove("hidden");
}

// The same reading of a failure the rest of the admin app gives: an
// unreachable server is not a rejected request, and a rejected request has a
// message worth showing rather than a status code.
function wizFailure(e, fallback) {
  return Boord.isNetworkError(e) ? "Cannot reach the server - try again"
                                  : Boord.errorDetail(e, fallback);
}

// Anything that reaches innerHTML from outside this file goes through here.
// The import results quote the farm's own spreadsheet back at them - a
// rejected row reports the cell it could not read, and the server's refusal
// messages quote it too - so cell contents end up rendered as markup unless
// they are escaped. Only an admin can upload, so this is self-inflicted at
// worst, but a stray "<" silently eating the rest of a message is reason
// enough on its own.
function wizEscape(value) {
  const el = document.createElement("div");
  el.textContent = String(value ?? "");
  return el.innerHTML;
}

async function showSetupWizard(state) {
  document.getElementById("loginScreen").classList.add("hidden");
  document.getElementById("pwChangeScreen").classList.add("hidden");
  document.getElementById("app").classList.add("hidden");
  document.getElementById("setupWizardScreen").classList.remove("hidden");

  // Record that the wizard has been opened before anything is saved. Its
  // first step writes the farm name, which is one of the two things that
  // made this install look unconfigured - without this marker, reloading
  // after step 1 drops straight into a half-set-up app with the remaining
  // steps unreachable.
  try {
    await Boord.api("/api/setup/start", { method: "POST", auth: true });
  } catch (e) {
    // Not fatal: the wizard still works for as long as this tab stays open.
  }
  bindSetupWizard();
  await prefillWizard(state);
  // Resume where the farm actually got to rather than always at step 1:
  // closing the tab half way through is normal, and step 1 of 9 for the
  // third time reads as though nothing was saved.
  _wizIndex = firstUnfinishedStep(state);
  renderWizardStep();
}

// The first required step that is not satisfied, or the first unsatisfied
// optional one after those. Never past the last step.
function firstUnfinishedStep(state) {
  const steps = (state && state.steps) || {};
  for (let i = 0; i < WIZARD_STEPS.length - 1; i++) {
    const step = WIZARD_STEPS[i];
    if (!steps[step.key] || !steps[step.key].done) return i;
  }
  return 0;
}

function bindSetupWizard() {
  if (_wizBound) return;  // showSetupWizard can run more than once per page
  _wizBound = true;

  document.getElementById("wizNextBtn").addEventListener("click", () => advanceWizard(false));
  document.getElementById("wizSkipBtn").addEventListener("click", () => advanceWizard(true));
  document.getElementById("wizBackBtn").addEventListener("click", () => {
    if (_wizIndex === 0) return;
    _wizIndex -= 1;
    renderWizardStep();
  });

  // The Settings map picker, pointed at this screen's own inputs. bindMapModal
  // as well, because the modal's Cancel/Confirm buttons are otherwise only
  // wired up by bindSettings(), which does not run while the wizard is showing.
  document.getElementById("wizPickMapBtn").addEventListener("click",
    () => openMapModal("wizGpsLat", "wizGpsLon"));
  bindMapModal();

  document.getElementById("wizBlocksFile").addEventListener("change",
    (e) => wizImport(e, "/api/blocks/import?replace=false", "wizBlocksResult", "blocks"));
  document.getElementById("wizWorkersFile").addEventListener("change",
    (e) => wizImport(e, "/api/workers/import", "wizWorkersResult", "workers"));
}

async function prefillWizard(state) {
  const steps = state.steps || {};
  const set = (id, value) => { document.getElementById(id).value = value ?? ""; };

  set("wizPackhouseName", steps.identity.packhouse_name);
  set("wizOwnSupplier", steps.identity.own_supplier_name);
  set("wizGpsLat", steps.location.gps_lat);
  set("wizGpsLon", steps.location.gps_lon);
  set("wizRatePerKg", steps.rate.rate_per_kg);
  set("wizGreenYellow", steps.thresholds.green_to_yellow_minutes);
  set("wizYellowRed", steps.thresholds.yellow_to_red_minutes);

  const settings = await Boord.api("/api/system-settings");
  set("wizPackhouseLocation", settings && settings.packhouse_location);
  set("wizPackhouseCode", settings && settings.packhouse_code);
  set("wizSeasonMonth", (settings && settings.season_start_month) || 1);
  set("wizSeasonDay", (settings && settings.season_start_day) || 1);

  const own = (await Boord.api("/api/suppliers") || []).find((s) => s.is_own_farm);
  set("wizOwnPuc", own && own.puc);
  set("wizOwnGlobalGap", own && own.global_gap_number);
}

function renderWizardStep() {
  wizError("");
  const step = wizStep();
  document.querySelectorAll(".wiz-step").forEach((el) => {
    el.classList.toggle("hidden", el.dataset.step !== step.key);
  });

  const number = _wizIndex + 1;
  document.getElementById("wizStepCount").textContent = `Step ${number} of ${WIZARD_STEPS.length}`;
  document.getElementById("wizProgress").style.width = `${Math.round((number / WIZARD_STEPS.length) * 100)}%`;
  document.getElementById("wizStepOptional").classList.toggle("hidden", !step.optional);
  document.getElementById("wizSkipBtn").classList.toggle("hidden", !step.optional);
  document.getElementById("wizBackBtn").disabled = _wizIndex === 0;
  document.getElementById("wizBackBtn").classList.toggle("opacity-40", _wizIndex === 0);
  document.getElementById("wizNextBtn").textContent =
    step.key === "finish" ? "Open Boord" : "Continue";

  if (step.key === "devices") renderWizardDevices();
  if (step.key === "finish") renderWizardFinish();
  window.scrollTo(0, 0);
}

// Save the current step, then move on. `skipping` bypasses the save entirely -
// an optional step left untouched must not write blanks over anything.
async function advanceWizard(skipping) {
  const step = wizStep();
  const nextBtn = document.getElementById("wizNextBtn");
  wizError("");

  if (!skipping) {
    nextBtn.disabled = true;
    try {
      await saveWizardStep(step.key);
    } catch (e) {
      wizError(typeof e === "string" ? e : wizFailure(e, "Could not save that step"));
      return;
    } finally {
      nextBtn.disabled = false;
    }
  }

  if (step.key === "finish") {
    await Boord.api("/api/setup/complete", { method: "POST", auth: true });
    document.getElementById("setupWizardScreen").classList.add("hidden");
    await showApp();
    return;
  }
  _wizIndex += 1;
  renderWizardStep();
}

// Reads the current SystemSetting and PUTs it back with only this step's
// fields changed. The endpoint takes a whole row, so sending a form's worth of
// fields on their own would blank everything the wizard is not showing yet.
async function patchSystemSettings(changes) {
  const current = (await Boord.api("/api/system-settings")) || {};
  const merged = { ...current, ...changes };
  await Boord.api("/api/system-settings", { method: "PUT", auth: true, body: merged });
  return merged;
}

async function saveWizardStep(key) {
  const value = (id) => document.getElementById(id).value.trim();

  if (key === "identity") {
    const packhouseName = value("wizPackhouseName");
    const ownName = value("wizOwnSupplier");
    if (!packhouseName) throw "Enter the pack house name";
    if (!ownName) throw "Enter a name for your own fruit as a supplier";
    const month = parseInt(value("wizSeasonMonth"), 10) || 1;
    const day = parseInt(value("wizSeasonDay"), 10) || 1;
    await patchSystemSettings({
      packhouse_name: packhouseName,
      packhouse_location: value("wizPackhouseLocation"),
      packhouse_code: value("wizPackhouseCode"),
      season_start_month: month,
      season_start_day: day,
      current_harvest_year: Boord.seasonYearFor(month, day),
    });
    const suppliers = await Boord.api("/api/suppliers");
    const own = (suppliers || []).find((s) => s.is_own_farm);
    const puc = value("wizOwnPuc");
    const ggn = value("wizOwnGlobalGap");
    if (own && (own.name !== ownName || own.puc !== puc || own.global_gap_number !== ggn)) {
      await Boord.api("/api/suppliers", {
        method: "POST", auth: true,
        body: { ...own, name: ownName, puc, global_gap_number: ggn },
      });
    }
    return;
  }

  if (key === "location") {
    // parseFloat, then an explicit isNaN check - `|| null` would turn a
    // latitude of 0 into "unset", and the equator is a real place. Same trap
    // weather.farm_coords() documents on the server side.
    const lat = parseFloat(value("wizGpsLat"));
    const lon = parseFloat(value("wizGpsLon"));
    if (isNaN(lat) || isNaN(lon)) throw "Enter both coordinates, or pick the pack house on the map";
    if (lat < -90 || lat > 90) throw "Latitude must be between -90 and 90";
    if (lon < -180 || lon > 180) throw "Longitude must be between -180 and 180";
    await patchSystemSettings({ gps_lat: lat, gps_lon: lon });
    return;
  }

  if (key === "rate") {
    const rate = parseFloat(value("wizRatePerKg"));
    if (isNaN(rate) || rate <= 0) throw "Enter the rate you pay per kilogram";
    await Boord.api("/api/rate-settings", {
      method: "POST", auth: true,
      body: {
        effective_date: Boord.localDateStr(),
        rate_type: "per_kg",
        default_rate_per_kg: rate,
        tier_rates_json: "{}",
      },
    });
    return;
  }

  if (key === "thresholds") {
    const green = parseInt(value("wizGreenYellow"), 10);
    const red = parseInt(value("wizYellowRed"), 10);
    if (isNaN(green) || isNaN(red) || green <= 0 || red <= 0) throw "Both thresholds must be a number of minutes";
    if (red <= green) throw "Yellow → Red must be longer than Green → Yellow";
    await patchSystemSettings({ green_to_yellow_minutes: green, yellow_to_red_minutes: red });
    return;
  }

  if (key === "devices") {
    const rows = document.querySelectorAll("#wizDevicesList [data-device-id]");
    for (const row of rows) {
      const station = row.querySelector("input").value.trim();
      const device = JSON.parse(row.dataset.device);
      if (station === device.station) continue;  // untouched - no pointless write
      await Boord.api("/api/devices", { method: "POST", auth: true, body: { ...device, station } });
    }
    return;
  }

  // blocks / workers / history / finish save as their uploads happen, so
  // Continue has nothing left to do.
}

// Imports share the shape of app.js's importFile(), but report into the step's
// own result line rather than a toast that has vanished by the time the user
// looks up from the file picker.
async function wizImport(event, url, resultId, noun) {
  const file = event.target.files[0];
  if (!file) return;
  const resultEl = document.getElementById(resultId);
  resultEl.innerHTML = `<span class="text-slate-500">Importing ${file.name}...</span>`;
  const form = new FormData();
  form.append("file", file);
  try {
    const result = await Boord.api(url, {
      method: "POST", body: form, auth: true, isForm: true, timeoutMs: Boord.UPLOAD_TIMEOUT_MS,
    });
    const seasons = result.seasons && result.seasons.length
      ? ` &middot; seasons ${result.seasons[0]}-${result.seasons[result.seasons.length - 1]}` : "";
    // Rows the server could not read are named, not swallowed. A partly
    // imported file that reports only its successes is how a farm ends up
    // missing a season without knowing it.
    const rejected = result.rejected
      ? `<div class="text-amber-700 text-xs mt-1">${result.rejected} row(s) skipped: ${wizEscape((result.rejected_detail || []).join("; "))}</div>`
      : "";
    resultEl.innerHTML =
      `<div class="text-green-700"><i class="fa-solid fa-check"></i> Imported ${result.imported} ${noun}${seasons}</div>${rejected}`;
  } catch (e) {
    resultEl.innerHTML = `<span class="text-red-600">${wizEscape(wizFailure(e, "Import failed - check the columns against the template"))}</span>`;
  }
  event.target.value = "";
}

const _DEVICE_ROLE_LABELS = { field: "Field", packhouse: "Pack house", admin: "Admin" };

async function renderWizardDevices() {
  const list = document.getElementById("wizDevicesList");
  let devices;
  try {
    devices = await Boord.api("/api/devices", { auth: true });
  } catch (e) {
    list.innerHTML = `<div class="text-red-600">${wizEscape(wizFailure(e, "Could not load the device list"))}</div>`;
    return;
  }
  list.innerHTML = devices
    .slice()
    .sort((a, b) => a.id.localeCompare(b.id))
    .map((d) => `
      <div class="flex items-center gap-2" data-device-id="${d.id}" data-device='${JSON.stringify(d).replace(/'/g, "&apos;")}'>
        <div class="w-28 shrink-0 text-xs text-slate-500">
          <div class="font-mono">${wizEscape(d.id)}</div>
          <div>${wizEscape(_DEVICE_ROLE_LABELS[d.role] || d.role)}</div>
        </div>
        <input value="${wizEscape(d.station || "")}" class="flex-1 border border-slate-300 rounded-lg p-2">
      </div>
    `).join("");
}

async function renderWizardFinish() {
  document.getElementById("wizServerUrl").value = location.origin + "/";

  // Say plainly what was skipped. The whole point of the wizard is that
  // nothing tells you which of five unrelated things you missed; finishing it
  // with a silent gap would reproduce that exactly.
  let state;
  try {
    state = await Boord.api("/api/setup/state", { auth: true });
  } catch (e) {
    return;
  }
  const outstanding = [];
  const s = state.steps;
  if (!s.blocks.done) outstanding.push("No blocks yet - the Field app has nothing to pick from until you import them under Settings -> Master Data.");
  if (!s.rate.done) outstanding.push("No wage rate - Payments will refuse to calculate.");
  if (!s.location.done) outstanding.push("No pack house location - no weather in the header, and none stamped onto crates or picking notes.");
  if (!s.workers.done) outstanding.push("No workers yet - add them under Settings -> Master Data.");

  const el = document.getElementById("wizOutstanding");
  if (!outstanding.length) { el.classList.add("hidden"); return; }
  el.innerHTML = `<div class="font-semibold mb-1">Still to do</div><ul class="list-disc ml-5 space-y-1">`
    + outstanding.map((t) => `<li>${t}</li>`).join("") + `</ul>`;
  el.classList.remove("hidden");
}
