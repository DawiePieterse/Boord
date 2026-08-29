// First-run setup wizard. Shown instead of the admin tabs while
// GET /api/setup/state reports `required` - see backend/routers/setup.py for
// what makes that true, and why it takes more than a flag to decide it.
//
// Every step calls the ordinary endpoint Settings and Master Data already
// use. Nothing here is a special first-run write path, which is what keeps
// the wizard from becoming a second, divergent way to configure a farm.

// Order is load-bearing. Blocks before historical harvest, because those rows
// reference block ids. Location before the weather backfill, because fetching
// weather for an unset location is exactly the ordering bug this replaces -
// today the only thing preventing it is that the importer refuses to guess.
const WIZARD_STEPS = [
  { key: "identity", optional: false },
  { key: "location", optional: false },
  { key: "rate", optional: false },
  { key: "thresholds", optional: true },
  { key: "blocks", optional: true },
  { key: "workers", optional: true },
  { key: "devices", optional: true },
  { key: "history", optional: true },
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
  document.getElementById("wizHistDailyFile").addEventListener("change",
    (e) => wizImport(e, "/api/historical-harvest/import", "wizHistDailyResult", "daily harvest rows"));
  document.getElementById("wizHistAnnualFile").addEventListener("change",
    (e) => wizImport(e, "/api/historical-annual-yield/import", "wizHistAnnualResult", "season totals"));
  document.getElementById("wizWeatherBackfillBtn").addEventListener("click", wizFetchWeatherHistory);
  document.getElementById("wizCopyOwnerLinkBtn").addEventListener("click",
    () => copyOwnerViewLink("wizOwnerLink"));
}

async function prefillWizard(state) {
  const steps = state.steps || {};
  const set = (id, value) => { document.getElementById(id).value = value ?? ""; };

  set("wizFarmName", steps.identity.farm_name);
  set("wizOwnSupplier", steps.identity.own_supplier_name);
  set("wizGpsLat", steps.location.gps_lat);
  set("wizGpsLon", steps.location.gps_lon);
  set("wizRatePerKg", steps.rate.rate_per_kg);
  set("wizGreenYellow", steps.thresholds.green_to_yellow_minutes);
  set("wizYellowRed", steps.thresholds.yellow_to_red_minutes);

  const settings = await Boord.api("/api/system-settings");
  set("wizFarmLocation", settings && settings.farm_location);
  set("wizHarvestYear", (settings && settings.current_harvest_year) || new Date().getFullYear());
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
  if (step.key === "history") renderWeatherYearChoices();
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
    const farmName = value("wizFarmName");
    const ownName = value("wizOwnSupplier");
    if (!farmName) throw "Enter the farm's name";
    if (!ownName) throw "Enter a name for your own fruit as a supplier";
    await patchSystemSettings({
      farm_name: farmName,
      farm_location: value("wizFarmLocation"),
      current_harvest_year: parseInt(value("wizHarvestYear"), 10) || new Date().getFullYear(),
    });
    const suppliers = await Boord.api("/api/suppliers");
    const own = (suppliers || []).find((s) => s.is_own_farm);
    if (own && own.name !== ownName) {
      await Boord.api("/api/suppliers", { method: "POST", auth: true, body: { ...own, name: ownName } });
    }
    return;
  }

  if (key === "location") {
    // parseFloat, then an explicit isNaN check - `|| null` would turn a
    // latitude of 0 into "unset", and the equator is a real place. Same trap
    // weather.farm_coords() documents on the server side.
    const lat = parseFloat(value("wizGpsLat"));
    const lon = parseFloat(value("wizGpsLon"));
    if (isNaN(lat) || isNaN(lon)) throw "Enter both coordinates, or pick the farm on the map";
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
    // The weather depth to suggest is derived from the seasons on file, and
    // a history import is what puts them there - so the suggestion made when
    // this step painted is now out of date, on the same screen.
    if (result.seasons) renderWeatherYearChoices();
  } catch (e) {
    resultEl.innerHTML = `<span class="text-red-600">${wizEscape(wizFailure(e, "Import failed - check the columns against the template"))}</span>`;
  }
  event.target.value = "";
}

// How many years of weather to offer. The floor is 1987 - the earliest
// season any farm has harvest figures for, and where the server clamps
// anyway (weather.ARCHIVE_START_DATE), so the last option is always
// "everything there is" however long this app stays in service.
const WEATHER_YEAR_CHOICES = [5, 10, 20];
const WEATHER_ARCHIVE_START_YEAR = 1987;
// What nobody can guess for the farm: how long this will take on ITS
// internet. Measured at ~2.2 MB and under 3 seconds for five years on a
// fast link, and writing the rows costs more than fetching them on a farm
// laptop - so these are deliberately vague and deliberately pessimistic,
// because the honest thing to convey is the shape of the trade, not a
// number that will be wrong.
function weatherYearsEstimate(years) {
  if (years <= 5) return "under a minute";
  if (years <= 10) return "a minute or two";
  if (years <= 20) return "a few minutes";
  return "five minutes or more";
}

// The download has to outlast itself: Boord.UPLOAD_TIMEOUT_MS is two
// minutes, which is right for a spreadsheet and not for forty years of
// hourly weather. Scaled by what was actually asked for, and capped, so a
// stalled request still eventually gives up rather than hanging the step.
function weatherYearsTimeout(years) {
  return Math.min(15 * 60 * 1000, Math.max(Boord.UPLOAD_TIMEOUT_MS, years * 25000));
}

// Suggest a depth from the farm's own history rather than a fixed default:
// the Risk indicator needs weather for every season it has yield figures
// for, so "as far back as your oldest season" is the only answer that is
// about this farm. With no history imported yet there is nothing to go on,
// and the middle option is a reasonable place to start.
async function renderWeatherYearChoices() {
  const select = document.getElementById("wizWeatherYears");
  const hint = document.getElementById("wizWeatherYearsHint");
  const thisYear = new Date().getFullYear();
  const maxYears = thisYear - WEATHER_ARCHIVE_START_YEAR + 1;

  let earliestSeason = null;
  try {
    const state = await Boord.api("/api/setup/state", { auth: true });
    earliestSeason = state.steps.history.earliest_season;
  } catch (e) {
    // Only the suggestion is lost - the choice below still works.
  }

  const options = WEATHER_YEAR_CHOICES.filter((y) => y < maxYears).concat([maxYears]);
  const needed = earliestSeason ? thisYear - earliestSeason + 1 : null;
  const suggested = needed
    ? options.find((y) => y >= needed) || maxYears
    : options[1] || options[0];

  select.innerHTML = options.map((years) => {
    const from = years >= maxYears ? WEATHER_ARCHIVE_START_YEAR : thisYear - years + 1;
    const label = years >= maxYears
      ? `Everything since ${WEATHER_ARCHIVE_START_YEAR} - ${weatherYearsEstimate(years)}`
      : `Back to ${from} - ${years} years, ${weatherYearsEstimate(years)}`;
    return `<option value="${years}"${years === suggested ? " selected" : ""}>${label}</option>`;
  }).join("");

  hint.textContent = earliestSeason
    ? `Your harvest history starts in ${earliestSeason}, so anything less than ${needed} years `
      + `leaves the Risk indicator with seasons it cannot score.`
    : "No harvest history loaded yet. Load it above first and this will suggest a depth to match it.";
}

async function wizFetchWeatherHistory() {
  const btn = document.getElementById("wizWeatherBackfillBtn");
  const resultEl = document.getElementById("wizWeatherResult");
  const years = parseInt(document.getElementById("wizWeatherYears").value, 10) || 10;
  btn.disabled = true;
  resultEl.innerHTML = `<span class="text-slate-500">Downloading ${years} years of weather - `
    + `${weatherYearsEstimate(years)}. Leave this page open.</span>`;
  try {
    const result = await Boord.api(`/api/weather/history/backfill?years=${years}`, {
      method: "POST", auth: true, timeoutMs: weatherYearsTimeout(years),
    });
    if (result.no_location) {
      resultEl.innerHTML = `<span class="text-amber-700">Go back to the location step first - Boord will not fetch weather for a place it has not been told about.</span>`;
    } else {
      // A farm whose harvest history reaches back further than the weather
      // it just fetched has a Risk indicator that refuses to score those
      // seasons. Say so here rather than let it be found later on a Risk tab
      // that simply does not work - and say what to do about it, which is
      // now simply to ask for more years.
      const gap = result.uncovered_season
        ? `<div class="text-amber-700 text-xs mt-1">Your harvest history starts in ${result.uncovered_season}, but this only reaches back to ${result.start_date.slice(0, 4)}. Choose more years above and fetch again - until then the Risk indicator has no weather for those seasons.</div>`
        : "";
      // Only ever non-zero when the farm's GPS was corrected after weather
      // had already been downloaded. Worth saying plainly: those hours were
      // somewhere else's, and they are gone now.
      const replaced = result.removed_elsewhere
        ? `<div class="text-slate-500 text-xs mt-1">${result.removed_elsewhere.toLocaleString()} older hours downloaded for a previous location were removed.</div>`
        : "";
      resultEl.innerHTML =
        `<div class="text-green-700"><i class="fa-solid fa-check"></i> ${result.imported.toLocaleString()} hours of weather, ${result.start_date} to ${result.end_date}</div>${gap}${replaced}`;
    }
  } catch (e) {
    resultEl.innerHTML = `<span class="text-red-600">${wizEscape(wizFailure(e, "Could not fetch the weather history"))}</span>`;
  } finally {
    btn.disabled = false;
  }
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

  try {
    await loadOwnerViewLink();
    document.getElementById("wizOwnerLink").value = document.getElementById("ownerViewLink").value;
  } catch (e) {
    document.getElementById("wizOwnerLink").value = "Could not load - find it under Settings";
  }

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
  if (!s.blocks.done) outstanding.push("No blocks yet - the Field app has nothing to pick from until you import them under Master Data.");
  if (!s.rate.done) outstanding.push("No wage rate - Payments will refuse to calculate.");
  if (!s.location.done) outstanding.push("No farm location - no weather, no Risk indicator, no Harvest Forecast.");
  if (!s.workers.done) outstanding.push("No workers yet - add them under Master Data.");

  const el = document.getElementById("wizOutstanding");
  if (!outstanding.length) { el.classList.add("hidden"); return; }
  el.innerHTML = `<div class="font-semibold mb-1">Still to do</div><ul class="list-disc ml-5 space-y-1">`
    + outstanding.map((t) => `<li>${t}</li>`).join("") + `</ul>`;
  el.classList.remove("hidden");
}
