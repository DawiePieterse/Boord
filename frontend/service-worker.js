// App-shell cache for the device-setup page at the site root.
//
// The four screens each cache their own shell, but the root page had no
// service worker at all, so opening the farm address with no signal gave a
// browser error page - even on a device that was set up weeks ago. Anyone who
// bookmarks or types the server address rather than launching the installed
// PWA hit that dead end. This caches the root shell so the page always loads;
// index.html then routes an already-configured device from its cached config.
const CACHE_PREFIX = "boord-root-";
const CACHE = "boord-root-v2";
const REVALIDATE_TIMEOUT_MS = 10000;
const SHELL = [
  "./",
  "./index.html",
  "./shared/styles.css",
  "./shared/api.js",
  "./shared/tailwind.js",
];

// This worker's scope is "/", which covers the whole origin - but the four
// screens have their own, narrower registrations and must keep serving
// themselves. So this one only ever answers for the root document and the
// handful of files that page needs; everything else falls through untouched.
const SHELL_PATHS = new Set(["/", "/index.html", "/shared/styles.css",
                             "/shared/api.js", "/shared/tailwind.js"]);

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

// Drop only THIS page's older caches - CacheStorage is shared per-origin and
// wiping every non-matching key would destroy the screens' offline shells.
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k.startsWith(CACHE_PREFIX) && k !== CACHE).map((k) => caches.delete(k))
    ))
  );
  self.clients.claim();
});

// Stale-while-revalidate, same strategy as the screens: answer instantly from
// cache so a device with no signal still gets the page, and refresh the copy
// in the background whenever the server is actually reachable.
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) return; // never cache API calls
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (!SHELL_PATHS.has(url.pathname)) return; // belongs to a screen, not to us

  // Cache page loads by path only, so a query string can't produce a miss.
  const cacheKey = event.request.mode === "navigate" ? url.origin + url.pathname : event.request;

  // waitUntil keeps this worker alive until the refreshed copy is actually
  // written, and the deadline stops background fetches piling up on an
  // unreachable network and starving the page of connections.
  const revalidateAbort = new AbortController();
  const revalidateTimer = setTimeout(() => revalidateAbort.abort(), REVALIDATE_TIMEOUT_MS);
  const update = fetch(event.request, { signal: revalidateAbort.signal })
    .then(async (res) => {
      if (res.ok) {
        const cache = await caches.open(CACHE);
        await cache.put(cacheKey, res.clone());
      }
      return res;
    })
    .catch(() => null) // offline: the cached copy below is the answer
    .finally(() => clearTimeout(revalidateTimer));
  event.waitUntil(update);

  event.respondWith(
    caches.open(CACHE)
      .then((cache) => cache.match(cacheKey))
      .then((cached) => cached || update.then((res) => res || Response.error()))
  );
});
