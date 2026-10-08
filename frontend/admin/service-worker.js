// App-shell cache so the admin PWA installs cleanly and its own UI still
// loads if the connection briefly drops. Data (dashboard, reports, etc.)
// always goes over the network when available.
const CACHE_PREFIX = "boord-admin-";
const CACHE = "boord-admin-v53";
const SHELL = [
  "./",
  "./index.html",
  "./app.js",
  "./setup.js",
  "./manifest.json",
  "../shared/styles.css",
  "../shared/api.js",
  "../shared/sw-core.js",
  "../shared/ptr.js",
  "../shared/tailwind.js",
  "../shared/qrcode.min.js",
  "../shared/vendor/fontawesome/css/all.min.css",
  "../shared/vendor/fontawesome/webfonts/fa-solid-900.woff2",
  "../shared/vendor/leaflet/leaflet.css",
  "../shared/vendor/leaflet/leaflet.js",
];

// Install/activate/fetch handling is the same for every screen - see
// shared/sw-core.js. Resolved against this file, so it stays at /shared/.
importScripts("../shared/sw-core.js");
