"""Schema migrations for the farm database.

Boord's schema used to be maintained by db.legacy_schema_catch_up(), which
could do exactly one thing: ADD COLUMN. No renames, no type changes, no
constraints, no backfill - so any structural change to the schema was simply
unshippable, and the workaround was always to add another nullable column
beside the wrong one. This module replaces it with Alembic.

Three things happen here that a plain `alembic upgrade head` does not do:

1. **A pre-Alembic database is caught up and stamped, not replayed.** Farms
   have been running since before any of this existed and their databases
   have no alembic_version table. Running the baseline migration on one
   would fail on the first CREATE TABLE. Instead the old additive path runs
   one final time - which is what brings a farm on any older version up to
   the baseline shape - and the database is then stamped at the baseline.

2. **The database is copied before anything alters it.** A full copy, kept
   beside the nightly backups. If that copy cannot be written, the migration
   does not run. See _snapshot_or_refuse().

3. **Drift is reported.** After migrating, the models are compared against
   the live schema. A mismatch means somebody changed models.py without
   writing a migration, and on a fresh install that shows up as a missing
   column at runtime rather than at deploy time. It is printed, never
   raised: a farm server has to boot.

Run by main.py at startup, and by update_server.bat in the foreground before
the server is restarted - the second one so that on the one occasion a
migration goes wrong, it goes wrong in front of the person who chose to
update, instead of inside a Scheduled Task nobody is watching.
"""
import os
import sys

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlmodel import SQLModel

import models  # noqa: F401 - importing it registers every table on SQLModel.metadata
from db import engine as default_engine, legacy_schema_catch_up

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ALEMBIC_INI = os.path.join(BACKEND_DIR, "alembic.ini")
MIGRATIONS_DIR = os.path.join(BACKEND_DIR, "migrations")

# The revision that describes the schema as it was when Alembic was adopted.
# Any database that predates alembic_version is stamped here - see
# _adopt_existing_database(). Never change this value: it is not "the first
# migration", it is "what every farm already had", and repointing it at a
# later revision would stamp an old farm as though changes it never received
# had already been applied.
BASELINE_REVISION = "8bc3a7dc4b1e"


def _config(target_engine) -> Config:
    cfg = Config(ALEMBIC_INI)
    # Absolute, because a Scheduled Task starts the server from whatever
    # directory Windows picks and the relative path in alembic.ini only
    # works for someone standing in backend/.
    cfg.set_main_option("script_location", MIGRATIONS_DIR)
    cfg.attributes["connectable"] = target_engine
    # env.py reads this. Inside the server process, alembic.ini's logging
    # config would replace uvicorn's and the app would go quiet.
    cfg.attributes["configure_logger"] = False
    return cfg


def _current_revision(target_engine):
    with target_engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _head_revision(cfg: Config):
    return ScriptDirectory.from_config(cfg).get_current_head()


def head_revision() -> str:
    """The newest revision the checked-out code carries."""
    return _head_revision(_config(default_engine))


def current_revision(target_engine=None):
    """The revision a database is stamped at, or None if it predates Alembic."""
    return _current_revision(target_engine or default_engine)


def _has_app_tables(target_engine) -> bool:
    """True if this database holds anything of the farm's.

    alembic_version does not count - a database that holds only that is one
    somebody stamped and never populated, and it should still be built from
    the migrations.
    """
    tables = set(inspect(target_engine).get_table_names())
    return bool(tables - {"alembic_version"})


def _snapshot_or_refuse(label: str) -> None:
    """Copy the database before a migration touches it, or stop.

    Refusing is the deliberate choice. The alternative is altering a farm's
    only copy of its harvest records with no way back, and "the server did
    not start" is recoverable in a way "the migration half-finished" is not
    - nothing has changed yet at this point, so checking the previous
    release back out is a complete fix.
    """
    from backup import snapshot_before_migration  # here, to keep db <- backup <- migrate acyclic

    try:
        path = snapshot_before_migration(label)
    except Exception as e:
        rule = "=" * 60
        print(f"\n{rule}\n"
              f" MIGRATION STOPPED - could not back up the database first.\n"
              f"\n"
              f"     {e!r}\n"
              f"\n"
              f" Nothing has been changed. The database is exactly as it was,\n"
              f" so the previous release still runs against it.\n"
              f"\n"
              f" Usually this is a full disk. Free some space in data\\backups\\\n"
              f" and run update_server.bat again.\n"
              f"{rule}\n", flush=True)
        raise
    print(f"[migration] database copied to {os.path.basename(path)} before migrating", flush=True)


def _report_drift(target_engine) -> None:
    """Say so if models.py and the live schema have parted company.

    Only reachable when somebody edits a model and does not write the
    migration for it. On the dev machine that is a line in the console
    before the release is cut; on a farm it is the explanation for a "no
    such column" error that would otherwise take an afternoon.
    """
    with target_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), SQLModel.metadata)
    if not diff:
        return
    print("[migration] WARNING: the database does not match models.py:", flush=True)
    for entry in diff:
        print(f"[migration]   {entry}", flush=True)
    print("[migration] Usually this means models.py was changed without a migration to "
          "go with it. Write one with:", flush=True)
    print("[migration]   cd backend && python -m alembic revision --autogenerate "
          "-m \"what changed\"", flush=True)
    print("[migration] It can also be old damage: legacy_schema_catch_up had to add some "
          "columns nullable that the model declares NOT NULL, because SQLite will not "
          "take a NOT NULL column with nothing to backfill it. That kind of difference "
          "is harmless and predates migrations - it is worth tidying up in a migration, "
          "not worth worrying about.", flush=True)


def _adopt_existing_database(cfg: Config, target_engine) -> None:
    """Bring a database that predates Alembic up to the baseline, and stamp it.

    legacy_schema_catch_up() is the pre-Alembic schema maintenance, run one
    last time. It is what makes this work for a farm that skipped several
    releases: whichever version its database was built by, create_all adds
    the tables it never got and the additive pass adds the columns, which
    together is precisely the baseline revision's schema.
    """
    print("[migration] this database predates Alembic - catching it up to the baseline", flush=True)
    legacy_schema_catch_up(target_engine)
    command.stamp(cfg, BASELINE_REVISION)
    print(f"[migration] stamped at baseline {BASELINE_REVISION}", flush=True)


def run_migrations(target_engine=None, snapshot: bool = True) -> None:
    """Bring the database to the newest revision. Safe to call on every start.

    target_engine/snapshot exist for scripts/selftest.py, which runs the
    whole thing against throwaway copies. Everything on a farm uses the
    defaults.
    """
    target_engine = target_engine or default_engine
    cfg = _config(target_engine)
    head = _head_revision(cfg)

    if not _has_app_tables(target_engine):
        # A new install. Nothing to lose and nothing to catch up: build the
        # schema from the migrations themselves, so a fresh farm exercises
        # the same code path an upgrade does rather than a create_all that
        # would hide a broken migration until the first customer upgrade.
        print("[migration] new database - building the schema from migrations", flush=True)
        command.upgrade(cfg, "head")
        print(f"[migration] at {head}", flush=True)
        _report_drift(target_engine)
        return

    current = _current_revision(target_engine)

    if current is None:
        if snapshot:
            _snapshot_or_refuse("pre_alembic")
        _adopt_existing_database(cfg, target_engine)
        current = BASELINE_REVISION

    if current == head:
        _report_drift(target_engine)
        return

    print(f"[migration] {current} -> {head}", flush=True)
    if snapshot:
        _snapshot_or_refuse(f"{current}_to_{head}")
    command.upgrade(cfg, "head")
    print(f"[migration] at {head}", flush=True)
    _report_drift(target_engine)


if __name__ == "__main__":
    # update_server.bat runs this between stopping and starting the server,
    # so the operator sees the migration happen and a failure stops the
    # update instead of being discovered later as a server that won't boot.
    try:
        run_migrations()
    except Exception as e:
        print(f"\n[migration] FAILED: {type(e).__name__}: {e}", flush=True)
        sys.exit(1)
    print("[migration] done", flush=True)
