// App-shell cache so the pack house PWA installs cleanly and its own UI
// still loads if the connection briefly drops. Data (queue, receiving)
// always goes over the network when available.
const CACHE_PREFIX = "boord-packhouse-";
const CACHE = "boord-packhouse-v18";
const SHELL = [
  "./",
  "./index.html",
  "./receiving.js",
  "./manifest.json",
  "../shared/styles.css",
  "../shared/api.js",
  "../shared/sw-core.js",
  "../shared/ptr.js",
  "../shared/tailwind.js",
  "../shared/vendor/fontawesome/css/all.min.css",
  "../shared/vendor/fontawesome/webfonts/fa-solid-900.woff2",
];

// Install/activate/fetch handling is the same for every screen - see
// shared/sw-core.js. Resolved against this file, so it stays at /shared/.
importScripts("../shared/sw-core.js");
