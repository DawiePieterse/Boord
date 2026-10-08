// Shared API helpers used by the field, packhouse and admin views.
// Everything is served from the same origin as the backend, so API_BASE is relative.
const API_BASE = "";

const Boord = {
  // Bump on every deploy that touches frontend code. Shown in each screen's
  // header so it's obvious at a glance whether a device's cached copy is
  // actually up to date - especially useful given the service workers'
  // cache-first strategy (see field/packhouse/admin service-worker.js).
  // Reset to 2.0 on 2026-08-26 for the Boord rename and clean reinstall.
  VERSION: "3.13",

  getDeviceId() { return localStorage.getItem("boord_device_id"); },
  // Re-pointing a tablet at a different device slot has to drop the picking
  // slip in progress. A slip is minted from the device id
  // (device-01-20260830132301), so one left over from the previous identity
  // makes every crate captured next carry a slip that names a station this
  // tablet is no longer standing at - and the lot then arrives at the pack
  // house filed under the wrong one. Crates already saved keep their own
  // slip and still sync; only the next one starts fresh.
  setDeviceId(id) {
    if (localStorage.getItem("boord_device_id") !== id) {
      localStorage.removeItem("boord_current_slip");
    }
    localStorage.setItem("boord_device_id", id);
  },
  clearDeviceId() { localStorage.removeItem("boord_device_id"); },

  getLastReceivedBy() { return localStorage.getItem("boord_last_received_by") || ""; },
  setLastReceivedBy(name) { localStorage.setItem("boord_last_received_by", name); },

  // A device whose WiFi is up but that cannot actually reach the farm server
  // gets no error from fetch() - the request just hangs until the OS gives up,
  // which can be minutes. Every request is therefore given a deadline, and a
  // blown deadline is reported as a normal network failure so callers fall
  // back to cached data instead of waiting.
  NETWORK_TIMEOUT_MS: 8000,
  // File transfers are legitimately slow; they opt into a longer deadline.
  UPLOAD_TIMEOUT_MS: 120000,

  async _fetchWithTimeout(url, options = {}, timeoutMs) {
    const limit = timeoutMs || Boord.NETWORK_TIMEOUT_MS;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), limit);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  },

  // True when a request failed because the server could not be reached
  // (offline, unreachable, or timed out) rather than because it answered
  // with an error. Screens use this to tell "no connection" apart from
  // "rejected" - e.g. the device setup screen must not wipe a saved device
  // id just because the server is unreachable.
  isNetworkError(e) {
    return e instanceof TypeError || (!!e && (e.name === "AbortError" || e.name === "TimeoutError"));
  },

  // Anything from the server that is interpolated into innerHTML goes through
  // here - names, slip numbers, notes, error text. They are typed by people
  // (or quoted back from an imported spreadsheet), and a stray "<" or quote
  // otherwise becomes markup: at best it silently eats the rest of the line,
  // at worst it runs. Safe inside attribute values as well as text.
  escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  },

  // The human-readable half of a server rejection. api() attaches FastAPI's
  // JSON "detail" (or the raw body) as e.detail, so showing e.message raw
  // would hand the user a status code and a lump of JSON. Errors that did
  // not come from api() are still parsed out of `${status} ${body}` if they
  // are shaped that way, and otherwise shown whole.
  errorDetail(e, fallback = "Something went wrong") {
    if (e && typeof e.detail === "string") return e.detail.trim() || fallback;
    // Read .message directly rather than `e.message || e`: an Error with an
    // empty message would otherwise stringify to the bare word "Error" and
    // that would win over the caller's fallback.
    const raw = e && typeof e.message === "string" ? e.message : String(e || "");
    const body = raw.replace(/^\d{3}\s*/, "");
    try {
      const parsed = JSON.parse(body);
      if (parsed && typeof parsed.detail === "string") return parsed.detail;
    } catch (_) { /* not JSON - fall through to the raw text */ }
    return body.trim() || fallback;
  },

  // True when the server actively refused this request rather than failing to
  // answer it. There are no credentials to refuse any more - a 403 from the
  // Admin app means it was reached from an address that is not the server
  // console or the tailnet (backend/security.py).
  isAuthError(e) {
    const status = e && e.status;
    return status === 401 || status === 403;
  },

  // Goes through api() like every other request, so it gets the same
  // deadline and offline-banner bookkeeping. Any rejection - a 404 for an
  // id the server has never heard of included - reads as "Unknown device id".
  async fetchDeviceConfig(deviceId) {
    let config;
    try {
      config = await Boord.api(`/api/devices/${encodeURIComponent(deviceId)}`);
    } catch (e) {
      if (Boord.isNetworkError(e)) throw e;
      const err = new Error("Unknown device id");
      err.status = e.status;
      throw err;
    }
    localStorage.setItem("boord_device_config", JSON.stringify(config));
    return config;
  },

  // The device config a Field or Receiving screen runs under. A device this
  // browser has already set up keeps working from its cached config forever;
  // only a never-seen device needs the server, and only that case may bounce
  // the user to setup - an unreachable server must never be mistaken for an
  // unknown device. `onFresh(config)` runs only when the server answered, so
  // the screen can redraw whatever depends on it. Resolves to null when there
  // is no config to run under (redirecting, or offline with nothing cached).
  async resolveDeviceConfig(deviceId, cachedConfig, onFresh) {
    try {
      const config = await Boord.fetchDeviceConfig(deviceId);
      if (onFresh) onFresh(config);
      return config;
    } catch (e) {
      if (Boord.isNetworkError(e)) {
        if (cachedConfig) return cachedConfig;
        Boord.toast("No connection - cannot set up this device yet");
        return null;
      }
      if (cachedConfig) return cachedConfig; // server says unknown, but we've run before
      location.href = "../";
      return null;
    }
  },

  // Reads a cached JSON blob, tolerating a missing or corrupted entry.
  getCachedJSON(key) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      localStorage.removeItem(key);
      return null;
    }
  },

  // Fetch a list or setting and keep a copy on the device, or - when that
  // fails for any reason - hand back the copy kept last time. Resolves to
  // {data, cached}: `cached` true means `data` is the saved copy (null if
  // nothing was ever saved) and the fetch did not succeed. Never rejects, so
  // a screen in a dead spot carries on with what it already has.
  async cachedLoad(key, fetchFn) {
    try {
      const data = await fetchFn();
      if (data != null) {
        try {
          localStorage.setItem(key, JSON.stringify(data));
        } catch (e) { /* out of quota - the live data still renders */ }
      }
      return { data, cached: false };
    } catch (e) {
      return { data: Boord.getCachedJSON(key), cached: true };
    }
  },

  // The config saved the last time this device successfully reached the
  // server. Screens paint from this immediately so a device that has been set
  // up before never has to wait on the network to become usable.
  getCachedDeviceConfig(deviceId) {
    const config = Boord.getCachedJSON("boord_device_config");
    return config && config.id === deviceId ? config : null;
  },

  // No auth option: Boord has no accounts. Whether a caller may see admin
  // data is decided by the network its request arrives on, server-side, and
  // there is nothing for the browser to attach.
  async api(path, { method = "GET", body, isForm = false, timeoutMs } = {}) {
    const headers = {};
    let payload = body;
    if (body && !isForm) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    // The offline banner is kept here rather than at every call site: a
    // request that never got an answer means the server is unreachable, and
    // any answer at all - an error status included - means it is not.
    let res;
    try {
      res = await Boord._fetchWithTimeout(
        `${API_BASE}${path}`, { method, headers, body: payload }, timeoutMs);
    } catch (e) {
      if (Boord.isNetworkError(e)) Boord.setOffline(true);
      throw e;
    }
    Boord.setOffline(false);
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      // e.status and e.detail carry the rejection in usable form; the
      // message keeps the `${status} ${body}` shape for logs and old callers.
      const err = new Error(`${res.status} ${text}`);
      err.status = res.status;
      err.detail = text;
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed.detail === "string") err.detail = parsed.detail;
      } catch (_) { /* not JSON - the body text is the detail */ }
      throw err;
    }
    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("application/json")) return res.json();
    return res.blob();
  },

  // The server records every timestamp in UTC, but SQLite hands them back
  // without a timezone marker, so they reach the browser looking like
  // "2026-08-08T13:46:21". JavaScript reads a bare date-time string as LOCAL
  // time, which meant every screen printed UTC digits as if they were farm
  // time (two hours slow in SAST). parseServerDate pins a naive string to UTC
  // first; the fmt* helpers then render it in the device's own timezone.
  // Always format server timestamps through these - never new Date(x) directly.
  parseServerDate(value) {
    if (value === null || value === undefined || value === "") return null;
    if (value instanceof Date) return isNaN(value.getTime()) ? null : value;
    let s = String(value).trim();
    // A bare "YYYY-MM-DD" is a calendar date, not an instant, so it is left
    // as-is; only strings carrying a time-of-day need the UTC marker.
    if (/\d{1,2}:\d{2}/.test(s)) {
      s = s.replace(" ", "T");
      if (!/(Z|[+-]\d{2}:?\d{2})$/.test(s)) s += "Z";
    }
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  },

  fmtDateTime(value, fallback = "") {
    const d = Boord.parseServerDate(value);
    return d ? d.toLocaleString() : fallback;
  },

  fmtTime(value, fallback = "") {
    const d = Boord.parseServerDate(value);
    return d ? d.toLocaleTimeString() : fallback;
  },

  // "Today" as the farm sees it, formatted for a date input. toISOString()
  // would give the UTC date, which is still yesterday between midnight and
  // 02:00 local - early enough to matter once picking starts before dawn.
  localDateStr(d = new Date()) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  },

  // Maps backend/weather.py's fixed condition strings to a Font Awesome
  // icon class - update both places together if a new condition is added.
  weatherIcon(condition) {
    const icons = {
      "Clear": "fa-sun",
      "Partly Cloudy": "fa-cloud-sun",
      "Overcast": "fa-cloud",
      "Cloudy": "fa-cloud",
      "Foggy": "fa-smog",
      "Drizzle": "fa-cloud-rain",
      "Rain": "fa-cloud-rain",
      "Heavy Rain": "fa-cloud-showers-heavy",
      "Showers": "fa-cloud-rain",
      "Heavy Showers": "fa-cloud-showers-heavy",
      "Snow": "fa-snowflake",
      "Heavy Snow": "fa-snowflake",
      "Storm": "fa-bolt",
    };
    return icons[condition] || "fa-cloud";
  },

  // Slim amber banner pinned under the header telling the user the screen is
  // offline. Wired to the browser's online/offline events, and api() also
  // flips it from every request's result: navigator.onLine only reflects the
  // radio, not whether the farm server is actually reachable (WiFi up +
  // server unreachable is the common case).
  offlineBanner(message) {
    let el = document.getElementById("boord-offline-banner");
    if (!el) {
      el = document.createElement("div");
      el.id = "boord-offline-banner";
      el.className = "offline-banner hidden";
      const header = document.querySelector(".boord-header");
      if (header && header.parentNode) header.parentNode.insertBefore(el, header.nextSibling);
      else document.body.prepend(el);
    }
    el.innerHTML = `<i class="fa-solid fa-wifi"></i> ${message}`;
    window.addEventListener("offline", () => Boord.setOffline(true));
    window.addEventListener("online", () => Boord.setOffline(false));
    if (!navigator.onLine) Boord._offline = true;
    // Reflect state already set by requests that ran before this call.
    el.classList.toggle("hidden", !Boord._offline);
  },

  setOffline(isOffline) {
    const val = !!isOffline;
    if (Boord._offline === val) return; // only react to actual flips
    Boord._offline = val;
    const el = document.getElementById("boord-offline-banner");
    if (el) el.classList.toggle("hidden", !val);
    if (typeof Boord.onOfflineChange === "function") Boord.onOfflineChange(val);
  },

  isOffline() { return !!Boord._offline; },

  // Screens can set this to react to offline flips (e.g. recolor a status pill).
  onOfflineChange: null,

  // Start a screen, and make sure a failure in its first moments is visible
  // rather than a white page.
  //
  // Every screen's init() opens by reaching for elements out of its own
  // index.html. If a device ends up with one release's HTML and another's
  // JavaScript - which a service worker CAN do, by revalidating the two files
  // at different moments - that lookup returns null, init() throws, and the
  // screen paints nothing at all. That happened on 2026-09-04: the Admin app
  // went blank on a farm server, and from the outside it looked exactly like
  // the server was down.
  //
  // A blank screen is the worst possible presentation of this, because it
  // gives whoever is standing there nothing to act on. So: catch it, say what
  // it is in words a picker or an admin can use, and offer the one button
  // that actually fixes it.
  boot(init) {
    try {
      Promise.resolve(init()).catch(Boord._bootFailed);
    } catch (e) {
      Boord._bootFailed(e);
    }
  },

  _bootFailed(error) {
    console.error("Boord: this screen failed to start", error);
    // Built with inline styles and no classes on purpose. The stylesheet is
    // one of the files that may be stale or missing, so this has to look
    // right without it.
    const panel = document.createElement("div");
    panel.setAttribute("style", [
      "position:fixed", "inset:0", "z-index:99999",
      "background:#f1f5f9", "color:#0f172a",
      "font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif",
      "display:flex", "align-items:center", "justify-content:center",
      "padding:24px", "text-align:center",
    ].join(";"));
    panel.innerHTML = `
      <div style="max-width:26rem">
        <div style="font-size:20px;font-weight:700;margin-bottom:12px">
          This device is running an old copy
        </div>
        <div style="margin-bottom:20px">
          The app on this phone or PC does not match the farm server, so this
          screen could not start. Nothing has been lost, and nothing is wrong
          with the server - this device just needs to fetch the new version.
        </div>
        <button id="boord-boot-reload" style="
          background:#0A2F6B;color:#fff;border:0;border-radius:10px;
          padding:14px 22px;font-size:16px;font-weight:600;cursor:pointer">
          Update this device
        </button>
        <div style="margin-top:16px;font-size:13px;color:#475569">
          If this keeps happening, close the app completely and open it again.
        </div>
      </div>`;
    document.body.appendChild(panel);
    panel.querySelector("#boord-boot-reload")
      .addEventListener("click", () => Boord._reinstall());
  },

  // Throw away everything this device has cached for the app and load it
  // fresh. Deliberately more than location.reload(): a reload is answered by
  // the very service worker whose cache is the problem, which is why "just
  // refresh the page" does not clear this and people end up clearing site
  // data by hand.
  async _reinstall() {
    try {
      if ("serviceWorker" in navigator) {
        const registrations = await navigator.serviceWorker.getRegistrations();
        await Promise.all(registrations.map((r) => r.unregister()));
      }
      if (window.caches) {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
      }
    } catch (e) {
      // Nothing useful to do - reload anyway, it may be enough on its own.
      console.error("Boord: could not clear the cache", e);
    }
    location.reload();
  },

  toast(message) {
    let el = document.getElementById("boord-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "boord-toast";
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove("show"), 2200);
  },

  // Short synthesized tones (no audio files needed, works fully offline).
  // Two distinct patterns so a worker can tell them apart by ear:
  // a single beep for a QR match, a two-note rising chime for a saved crate.
  _tone(frequency, duration, delay = 0) {
    try {
      const ctx = Boord._audioCtx || (Boord._audioCtx = new (window.AudioContext || window.webkitAudioContext)());
      if (ctx.state === "suspended") ctx.resume();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = frequency;
      const startAt = ctx.currentTime + delay;
      gain.gain.setValueAtTime(0.2, startAt);
      gain.gain.exponentialRampToValueAtTime(0.001, startAt + duration);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(startAt);
      osc.stop(startAt + duration);
    } catch (e) { /* audio isn't critical - never block the capture flow on it */ }
  },
  beepScanned() { Boord._tone(880, 0.12); },
  beepSaved() { Boord._tone(660, 0.09); Boord._tone(988, 0.14, 0.1); },

  // The year a season starting on month/day (1-based month) is in on date `d`:
  // if `d` is on or after this year's anchor it is this year's season,
  // otherwise last year's. A season is labelled by the year it starts in.
  seasonYearFor(month, day, d = new Date()) {
    const m = parseInt(month, 10) || 1;
    const dy = parseInt(day, 10) || 1;
    const anchorThisYear = new Date(d.getFullYear(), m - 1, dy);
    return d >= anchorThisYear ? d.getFullYear() : d.getFullYear() - 1;
  },

  // Wires a Today/Week/Season button group to a pair of date inputs: clicking
  // a button sets the inputs and highlights that button; editing a date input
  // directly clears the highlight since the selection no longer matches a preset.
  // `seasonAnchor()` returns {month, day} (1-based month) for the season start;
  // the Season preset then spans [anchor(year), anchor(year+1) - 1 day].
  bindDateRangePresets({ todayBtn, weekBtn, seasonBtn, startInput, endInput, seasonAnchor, onChange }) {
    const buttons = [todayBtn, weekBtn, seasonBtn];
    const setActive = (btn) => buttons.forEach((b) => b.classList.toggle("active", b === btn));
    const clearActive = () => buttons.forEach((b) => b.classList.remove("active"));

    todayBtn.addEventListener("click", () => {
      const t = Boord.localDateStr();
      startInput.value = t; endInput.value = t;
      setActive(todayBtn); if (onChange) onChange();
    });
    weekBtn.addEventListener("click", () => {
      const end = new Date(); const start = new Date();
      start.setDate(end.getDate() - 6);
      startInput.value = Boord.localDateStr(start); endInput.value = Boord.localDateStr(end);
      setActive(weekBtn); if (onChange) onChange();
    });
    seasonBtn.addEventListener("click", () => {
      const anchor = seasonAnchor ? seasonAnchor() : null;
      const month = anchor && anchor.month ? parseInt(anchor.month, 10) : 1;
      const day = anchor && anchor.day ? parseInt(anchor.day, 10) : 1;
      const year = Boord.seasonYearFor(month, day);
      const start = new Date(year, month - 1, day);
      const end = new Date(year + 1, month - 1, day);
      end.setDate(end.getDate() - 1);
      startInput.value = Boord.localDateStr(start);
      endInput.value = Boord.localDateStr(end);
      setActive(seasonBtn); if (onChange) onChange();
    });
    startInput.addEventListener("change", clearActive);
    endInput.addEventListener("change", clearActive);
    setActive(todayBtn); // screens all initialize inputs to "today"
  },

  downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },

  uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  },
};
