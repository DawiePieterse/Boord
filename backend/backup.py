"""Farm data backup: zips the SQLite DB + worker photos, retains the most
recent MAX_BACKUPS, and runs itself automatically once a day at 02:00 via a
background daemon thread - no scheduling library needed for a single daily
job. Backup files never include the app's source code (already in git),
only the data that changes at runtime.

The nightly run only writes an archive when the farm data actually changed
since the last one; pressing "Backup Now" is always unconditional.

This file used to carry a good deal more machinery, because the weather
history was 42.0 MB of a 42.4 MB database and every archive had to empty it
out and VACUUM to stay a sensible size. That table left with the Owner app,
and the whole database is now a few hundred KB - so the archive is simply
the database, and the special case is gone.
"""
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Optional

from db import DATA_DIR, DB_PATH, PHOTOS_DIR

BACKUPS_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(BACKUPS_DIR, exist_ok=True)
MAX_BACKUPS = 14
# Safety net: take one backup anyway after this long with no changes, so a
# bug in the change detection can't quietly leave the farm with nothing
# recent to restore from.
MAX_DAYS_WITHOUT_BACKUP = 30
# Lives in BACKUPS_DIR on purpose - _backup_filenames() only matches
# backup_*.zip, so the pruner will never delete it.
STATE_PATH = os.path.join(BACKUPS_DIR, "last_backup.json")


def _backup_filenames() -> list[str]:
    return sorted(
        f for f in os.listdir(BACKUPS_DIR) if f.startswith("backup_") and f.endswith(".zip")
    )


def _prune_old_backups() -> None:
    stale = _backup_filenames()[:-MAX_BACKUPS] if MAX_BACKUPS > 0 else _backup_filenames()
    for name in stale:
        os.remove(os.path.join(BACKUPS_DIR, name))


def _photo_paths() -> list[tuple[str, str]]:
    """(full path, path relative to PHOTOS_DIR) for every worker photo,
    sorted by the relative path so both the archive and the fingerprint
    below see the same files in the same order every run."""
    found = []
    for root, _, files in os.walk(PHOTOS_DIR):
        for f in files:
            full = os.path.join(root, f)
            found.append((full, os.path.relpath(full, PHOTOS_DIR)))
    return sorted(found, key=lambda pair: pair[1])


def _snapshot_db(destination: str) -> None:
    """A consistent copy of the live database, via SQLite's own backup API.

    Plain-copying the .db file (what this used to do) reads whatever bytes
    happen to be on disk. In `delete` journal mode - which this database uses
    - a write in flight has the .db partially updated with the rollback data
    sitting in a separate -journal file the zip never captured, so a copy
    taken mid-transaction is unrecoverable. The nightly 02:00 run is usually
    safe simply because nobody is picking, but Settings has a "Backup Now"
    button that gets pressed during harvest while field devices sync every
    10 seconds. The failure is silent - the zip writes fine and only turns
    out to be corrupt when someone tries to restore it.

    sqlite3's backup() coordinates with any concurrent writer and yields a
    self-consistent snapshot.
    """
    source = sqlite3.connect(DB_PATH)
    try:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _build_snapshot(destination: str) -> None:
    """The database as it goes into the archive: a consistent point-in-time
    snapshot, whole. Only ever touches the throwaway copy, never DB_PATH."""
    _snapshot_db(destination)


def _fingerprint(snapshot_path: Optional[str]) -> str:
    """A hash of everything worth backing up, used to decide whether the
    nightly run has anything new to save.

    Taken from the snapshot rather than the live database: it's already a
    consistent point-in-time copy. Photos go in too - a new worker photo
    never touches the .db, so an mtime or DB-only check would miss it
    entirely.
    """
    digest = hashlib.sha256()
    con = sqlite3.connect(snapshot_path) if snapshot_path else None
    try:
        tables = [] if con is None else [row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            digest.update(f"\n#{table}\n".encode())
            try:
                rows = con.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            except sqlite3.OperationalError:
                # WITHOUT ROWID table - no rowid to order by. Unordered is
                # still stable in practice, and a wrong hash here only ever
                # costs an extra backup, never a missed one.
                rows = con.execute(f'SELECT * FROM "{table}"')
            for row in rows:
                digest.update(repr(row).encode())
    finally:
        if con is not None:
            con.close()

    for full, relative in _photo_paths():
        digest.update(f"\n@{relative}\n".encode())
        with open(full, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _read_state() -> Optional[dict]:
    """What the last archive contained. Anything unreadable reads as "no
    previous backup" - erring towards taking one, never towards skipping."""
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def _write_state(fingerprint: str, filename: str) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump({
            "fingerprint": fingerprint,
            "filename": filename,
            "created_at": datetime.now().isoformat(),
        }, fh)


def _backup_due(fingerprint: str) -> bool:
    state = _read_state()
    if state is None or state.get("fingerprint") != fingerprint:
        return True
    if state.get("filename") not in _backup_filenames():
        # The archive this fingerprint describes is gone - the folder was
        # cleared out, or someone tidied that one zip out of data\backups\
        # by hand. Either way there is no longer a copy of this data, so the
        # state file must not be what stops us taking one.
        return True
    try:
        last = datetime.fromisoformat(state["created_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return datetime.now() - last >= timedelta(days=MAX_DAYS_WITHOUT_BACKUP)


def create_backup(skip_if_unchanged: bool = False) -> Optional[str]:
    """Write a new archive and return its filename.

    With skip_if_unchanged (the nightly run) nothing is written and None is
    returned when the farm data is byte-for-byte what the last archive held.
    "Backup Now" leaves it off and always gets an archive.
    """
    fd, snapshot = tempfile.mkstemp(suffix=".db", dir=BACKUPS_DIR)
    os.close(fd)
    os.remove(snapshot)  # sqlite3 wants to create the file itself
    try:
        has_db = os.path.exists(DB_PATH)
        if has_db:
            _build_snapshot(snapshot)
            fingerprint = _fingerprint(snapshot)
        else:
            fingerprint = _fingerprint(None)  # photos only

        if skip_if_unchanged and not _backup_due(fingerprint):
            state = _read_state() or {}
            print(f"[backup] farm data unchanged since {state.get('filename', 'the last backup')}"
                  " - nightly backup skipped", flush=True)
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"backup_{timestamp}.zip"
        # Only now, past the skip decision - creating the ZipFile earlier
        # would leave an empty archive behind on a skipped night.
        with zipfile.ZipFile(os.path.join(BACKUPS_DIR, filename), "w", zipfile.ZIP_DEFLATED) as zf:
            if has_db:
                zf.write(snapshot, arcname="boord.db")
            for full, relative in _photo_paths():
                zf.write(full, arcname=os.path.join("photos", relative))
    finally:
        # Never leave a stray .db beside the archives - _backup_filenames only
        # matches backup_*.zip, so a leftover would sit there unnoticed.
        if os.path.exists(snapshot):
            os.remove(snapshot)
    _write_state(fingerprint, filename)
    _prune_old_backups()
    return filename


# Copies taken immediately before a schema migration, by migrate.py. Kept
# separate from the nightly archives in every way that matters:
#
# - Uncompressed .db, not a zip. A rollback wants a file you can copy back
#   over boord.db with the server stopped, at the moment something has gone
#   wrong and nobody is in the mood to work out an archive layout.
# - Its own retention. _backup_filenames() only ever matches backup_*.zip,
#   so the nightly pruner cannot reach these, and they cannot crowd the
#   fourteen rolling backups out either.
PRE_MIGRATION_PREFIX = "pre_migration_"
PRE_MIGRATION_KEEP = 3


def _pre_migration_filenames() -> list[str]:
    return sorted(
        f for f in os.listdir(BACKUPS_DIR)
        if f.startswith(PRE_MIGRATION_PREFIX) and f.endswith(".db")
    )


def snapshot_before_migration(label: str) -> str:
    """Full consistent copy of the database, returned as its path.

    Raises rather than returning None on any failure - a caller that cannot
    get this copy must not migrate, so a quiet failure here would defeat the
    entire point of taking it.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BACKUPS_DIR, f"{PRE_MIGRATION_PREFIX}{timestamp}_{safe}.db")

    _snapshot_db(path)

    # Only prune once the new copy is safely on disk. Pruning first would, on
    # a full disk, delete the oldest rollback point and then fail to write
    # the new one - leaving the farm with fewer copies than it started with
    # at the exact moment it was about to need one.
    for stale in _pre_migration_filenames()[:-PRE_MIGRATION_KEEP]:
        os.remove(os.path.join(BACKUPS_DIR, stale))
    return path


def list_backups() -> list[dict]:
    result = []
    for name in reversed(_backup_filenames()):
        full = os.path.join(BACKUPS_DIR, name)
        stat = os.stat(full)
        result.append({
            "filename": name,
            "size_bytes": stat.st_size,
            # Tagged UTC so the browser can convert it to farm time; a naive
            # string would be read as local by JS and silently shifted.
            "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })
    return result


def _seconds_until_next_2am() -> float:
    now = datetime.now()
    target = now.replace(hour=2, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _scheduler_loop() -> None:
    while True:
        time.sleep(_seconds_until_next_2am())
        try:
            create_backup(skip_if_unchanged=True)
        except Exception as e:
            # Don't let one bad night kill the thread - try again tomorrow.
            # But say so: swallowing this silently meant a backup that failed
            # every night left no trace anywhere, and the one place it would
            # be noticed is the day someone needs to restore.
            print(f"[backup] nightly backup FAILED: {e!r}", flush=True)


def start_backup_scheduler() -> None:
    threading.Thread(target=_scheduler_loop, daemon=True).start()
