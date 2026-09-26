// App-shell cache so the field PWA still loads with zero signal.
// Data (worker/block lists, sync) always goes over the network when
// available - this only guarantees the UI itself is installable/offline.
const CACHE_PREFIX = "boord-field-";
const CACHE = "boord-field-v23";
const SHELL = [
  "./",
  "./index.html",
  "./app.js",
  "./idb.js",
  "./manifest.json",
  "../shared/styles.css",
  "../shared/api.js",
  "../shared/sw-core.js",
  "../shared/ptr.js",
  "../shared/tailwind.js",
  "../shared/vendor/fontawesome/css/all.min.css",
  "../shared/vendor/fontawesome/webfonts/fa-solid-900.woff2",
  "../shared/vendor/html5-qrcode.min.js",
];

// Install/activate/fetch handling is the same for every screen - see
// shared/sw-core.js. Resolved against this file, so it stays at /shared/.
importScripts("../shared/sw-core.js");
