// Tombstone. This used to be the Owner View's app-shell worker, and that is
// exactly the problem it now exists to solve.
//
// The old worker precached the whole Owner View under "boord-owner-v18" and
// answered navigations from that cache. Deleting the screen does not reach a
// phone that already has it: the registration survives, the cached shell
// keeps rendering, and it renders against /api/owner-view endpoints that no
// longer exist - so the owner sees the app they know, filled with errors,
// with nothing to explain it.
//
// A worker is only replaced by another worker at the same scope. So this one
// takes over, throws away every cache the old one made, unregisters itself,
// and reloads whatever pages it had claimed - which lands them on the plain
// index.html beside this file.
//
// Delete this file (and index.html) once the new Owner app has shipped and
// the owner's devices have been through here. It costs nothing to leave in
// the meantime: it caches nothing and intercepts nothing.
const CACHE_PREFIX = "boord-owner-";

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k.startsWith(CACHE_PREFIX)).map((k) => caches.delete(k)));

    // Unregister before reloading, not after: a client that reloads while
    // this worker is still registered would just start it up again.
    await self.registration.unregister();

    const clients = await self.clients.matchAll({ type: "window" });
    for (const client of clients) client.navigate(client.url);
  })());
});

// No fetch handler at all. Every request goes straight to the network, which
// is what a screen that is only a static page needs.
