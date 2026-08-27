// Minimal IndexedDB wrapper for the field harvest write-ahead log.
// One object store ("harvest_records") holding every crate captured on this
// device, each with a "synced" flag. This is the source of truth for the
// current load's running totals - the UI never needs the server to be
// reachable to keep working.
//
// Every request settles: an IndexedDB request that fails without an onerror
// handler would leave its promise pending forever, and because the capture
// screen awaits these on startup that would freeze the whole UI rather than
// just losing one read.
const IDB = (() => {
  const DB_NAME = "boord_field_db";
  const STORE = "harvest_records";
  // Synced crates older than this are dropped once they're no longer part of
  // the slip being picked. Nothing used to delete anything, so the store grew
  // for the whole season and every sync tick (10s) and lot re-render (15s)
  // deserialised every crate the device had ever captured. Two weeks is far
  // longer than a crate can plausibly stay relevant on the device - the server
  // has held it since it synced - while still leaving plenty for the recent
  // list and any same-week troubleshooting.
  const PRUNE_AFTER_DAYS = 14;
  let dbPromise = null;

  function open() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 2);
      req.onupgradeneeded = (event) => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const store = db.createObjectStore(STORE, { keyPath: "uuid" });
          store.createIndex("slip_number", "slip_number");
        } else if (event.oldVersion < 2) {
          // v1 created an index on "synced", which never worked: IndexedDB
          // only accepts number/string/Date/binary/array as keys, so records
          // whose "synced" is a boolean are simply absent from it (verified -
          // a store with true/false/0 shows only the 0 through the index).
          // It was never read, so dropping it changes no behaviour; it just
          // stops implying a fast path that does not exist. The records
          // themselves are untouched, so no crate can be lost here.
          const store = req.transaction.objectStore(STORE);
          if (store.indexNames.contains("synced")) store.deleteIndex("synced");
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
      // Another tab holding an old version open would otherwise stall here
      // indefinitely with no error of any kind.
      req.onblocked = () => reject(new Error("IndexedDB upgrade blocked by another tab"));
    });
    // A failed open must not be cached, or every later call inherits it.
    dbPromise.catch(() => { dbPromise = null; });
    return dbPromise;
  }

  async function tx(mode) {
    const db = await open();
    return db.transaction(STORE, mode).objectStore(STORE);
  }

  // Resolves when the whole transaction commits, not merely when the request
  // reports success - a write is only durable once the transaction completes.
  function done(store) {
    return new Promise((resolve, reject) => {
      store.transaction.oncomplete = () => resolve();
      store.transaction.onerror = () => reject(store.transaction.error);
      store.transaction.onabort = () => reject(store.transaction.error);
    });
  }

  function request(req) {
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  return {
    async add(record) {
      const store = await tx("readwrite");
      store.put(record);
      await done(store);
    },
    async getBySlip(slipNumber) {
      const store = await tx("readonly");
      return request(store.index("slip_number").getAll(slipNumber));
    },
    async getUnsynced() {
      const store = await tx("readonly");
      const all = await request(store.getAll());
      return all.filter((r) => !r.synced);
    },
    async markSynced(uuids) {
      await IDB._patch(uuids, (rec) => { rec.synced = true; });
    },
    async reassignSlip(uuids, newSlip) {
      await IDB._patch(uuids, (rec) => { rec.slip_number = newSlip; });
    },
    async _patch(uuids, mutate) {
      const store = await tx("readwrite");
      for (const uuid of uuids) {
        const getReq = store.get(uuid);
        getReq.onsuccess = () => {
          const rec = getReq.result;
          if (rec) { mutate(rec); store.put(rec); }
        };
      }
      await done(store);
    },
    async recent(limit = 5) {
      const store = await tx("readonly");
      const all = await request(store.getAll());
      return all.sort((a, b) => b.timestamp.localeCompare(a.timestamp)).slice(0, limit);
    },

    // Drops crates that are safely on the server and no longer needed on the
    // device, bounding a store that otherwise grew all season. Deliberately
    // conservative - a record is only removed when ALL of these hold:
    //   * it is marked synced (never risk an un-uploaded crate),
    //   * it is not part of the slip currently being picked (getBySlip drives
    //     the live lot totals on screen),
    //   * it is older than PRUNE_AFTER_DAYS.
    // Returns how many were removed.
    async pruneSynced(currentSlip) {
      const cutoff = new Date(Date.now() - PRUNE_AFTER_DAYS * 86400000).toISOString();
      const store = await tx("readwrite");
      const all = await request(store.getAll());
      let removed = 0;
      for (const r of all) {
        if (!r.synced) continue;
        if (currentSlip && r.slip_number === currentSlip) continue;
        if (!r.timestamp || r.timestamp >= cutoff) continue;
        store.delete(r.uuid);
        removed++;
      }
      await done(store);
      return removed;
    },
  };
})();
