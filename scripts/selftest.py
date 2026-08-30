#!/usr/bin/env python3
"""Self-test for the parts of this app where a wrong answer looks just as
plausible as a right one: the schema migration paths, the pre-migration
rollback copy, the bulk-import guards, and the first-run wizard's view of
how far a farm has got.

Runs against the server's OWN database, read-only: it never writes, so it
is safe to run on the live farm server to confirm an install is sound.
Anything that depends on the farm's specific numbers is asserted as an
invariant rather than a hardcoded figure, so this keeps working as seasons
are added.

This file used to be about three times this length. Most of it tested the
Risk indicator's arithmetic, the Harvest Forecast's projections, the stored
weather history and the Historical Harvest Data report - all of which left
for the Boord Owner app, now its own project outside this repository. Those
tests were deleted rather than moved, and are worth recovering from this
repo's history rather than rewriting.

Plain asserts, no pytest - this project deliberately carries no test
dependency.

Usage:
    backend/.venv/bin/python3 scripts/selftest.py
Exits non-zero if anything fails.
"""
import io
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from alembic import command  # noqa: E402
from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.migration import MigrationContext  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import create_engine, inspect  # noqa: E402
from sqlmodel import Session, SQLModel, func, select  # noqa: E402

import asyncio  # noqa: E402
from fastapi import HTTPException, UploadFile  # noqa: E402

import backup  # noqa: E402
from db import DB_PATH, engine, legacy_schema_catch_up  # noqa: E402
from migrate import (BASELINE_REVISION, _baseline_database, _config,  # noqa: E402
                     current_revision, head_revision, run_migrations)
from models import (AdminUser, Block, HarvestRecord, SetupState,  # noqa: E402
                    Supplier, SystemSetting, Worker)
from routers.master_data import import_blocks  # noqa: E402
from routers.setup import build_setup_state  # noqa: E402

_passed = 0
_failed = []
_skipped = []


class Skip(Exception):
    """Raised by a check that cannot run against this particular database.

    Only for a genuinely absent precondition - a table the server has not
    created yet, say - never for an assertion that is inconvenient. Skips
    are counted and listed separately so they can't be mistaken for passes.
    """


def check(name, fn):
    global _passed
    try:
        fn()
    except Skip as exc:
        _skipped.append((name, str(exc)))
        print(f"  skip  {name}\n          {exc}")
    except Exception as exc:  # noqa: BLE001 - a failing check must not stop the run
        _failed.append((name, exc))
        print(f"  FAIL  {name}\n          {type(exc).__name__}: {exc}")
    else:
        _passed += 1
        print(f"  ok    {name}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# ---------------------------------------------------------------------------
# Schema migrations
#
# Two kinds of check here. The ones that build a database do it in a
# temporary directory and throw it away - this suite is read-only against
# the server's own database and stays that way. The last two look at the
# live database, and only read.
#
# Worth having because the migration path is the one piece of this app that
# runs against a farm's only copy of its data, at the exact moment a release
# changes what that data looks like, on a machine nobody is watching.
# ---------------------------------------------------------------------------
def _table_shape(path):
    """The structure of every table in a SQLite file.

    Compared as structure rather than as CREATE TABLE text on purpose:
    Alembic emits foreign keys alphabetically and SQLModel.create_all emits
    them in the order the model declares them, so two identical schemas
    have different SQL. Column order is left out for the same reason.
    """
    insp = inspect(create_engine(f"sqlite:///{path}"))
    shape = {}
    for table in sorted(insp.get_table_names()):
        if table == "alembic_version":
            continue
        shape[table] = {
            "columns": {c["name"]: (str(c["type"]), c["nullable"]) for c in insp.get_columns(table)},
            "pk": tuple(sorted(insp.get_pk_constraint(table)["constrained_columns"])),
            "fks": sorted((tuple(f["constrained_columns"]), f["referred_table"],
                           tuple(f["referred_columns"])) for f in insp.get_foreign_keys(table)),
            "unique": sorted((u["name"], tuple(u["column_names"]))
                             for u in insp.get_unique_constraints(table)),
            "indexes": sorted((i["name"], tuple(i["column_names"]), bool(i["unique"]))
                              for i in insp.get_indexes(table)),
        }
    return shape


def _drift(target_engine):
    with target_engine.connect() as conn:
        return compare_metadata(MigrationContext.configure(conn), SQLModel.metadata)


def test_migrations_build_what_the_models_describe():
    """A new install gets its schema from the migrations, not from
    create_all - so the migrations have to produce exactly what create_all
    would have. If they ever stop agreeing, a fresh farm quietly gets a
    different database from an upgraded one, and the difference shows up as
    a "no such column" months later on whichever of the two nobody tested."""
    tmp = tempfile.mkdtemp()
    try:
        by_migration = os.path.join(tmp, "migrated.db")
        by_models = os.path.join(tmp, "created.db")
        run_migrations(create_engine(f"sqlite:///{by_migration}"), snapshot=False)
        SQLModel.metadata.create_all(create_engine(f"sqlite:///{by_models}"))

        migrated, created = _table_shape(by_migration), _table_shape(by_models)
        assert set(migrated) == set(created), (
            f"tables only one way builds: {set(migrated) ^ set(created)}")
        for table in migrated:
            assert migrated[table] == created[table], (
                f"{table} differs\n  migrations: {migrated[table]}\n"
                f"  models:     {created[table]}")
        assert not _drift(create_engine(f"sqlite:///{by_migration}")), (
            "a database built from the migrations does not match models.py - "
            "a migration is missing")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_database_from_before_migrations_is_caught_up_and_stamped():
    """The upgrade path every existing farm takes exactly once.

    Built here the way those databases were built - at the BASELINE shape,
    which is what a database that predates Alembic actually has, not
    today's - then damaged the way a farm several releases behind is
    damaged, a table and a column it never received. It has to come out at
    the newest revision, with the post-baseline migrations actually applied
    and its rows untouched. A farm that skipped four releases is the case
    this has to survive, not a farm that updates weekly.
    """
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "old.db")
        old_engine = create_engine(f"sqlite:///{path}")
        legacy_schema_catch_up(old_engine, _baseline_database())
        # Raw INSERTs, baseline columns only: the catch-up brings the database
        # to the BASELINE shape, which predates block.supplier_id and any
        # other column later migrations add - the ORM models describe today's
        # schema and would name columns this database does not have yet.
        con = sqlite3.connect(path)
        con.execute("INSERT INTO block (id, name, variety, trees, hectares, active) "
                    "VALUES ('15', 'Blok 15', 'Mauritius', 100, 1.5, 1)")
        con.execute("INSERT INTO worker (id, first_name, last_name, name, id_number, "
                    "bank, account, whatsapp_number, photo_filename, active) "
                    "VALUES ('001', 'Thandi', 'N', 'Thandi N', '', '', '', '', '', 1)")
        con.commit()
        con.close()

        con = sqlite3.connect(path)
        con.execute("DROP TABLE setupstate")
        con.execute("ALTER TABLE adminuser DROP COLUMN must_change_password")
        con.commit()
        con.close()

        assert current_revision(old_engine) is None, "a pre-Alembic database claimed a revision"
        run_migrations(old_engine, snapshot=False)

        assert current_revision(old_engine) == head_revision(), (
            "an existing farm database did not end up at the newest revision")
        insp = inspect(old_engine)
        assert insp.has_table("setupstate"), "a table the farm never received was not restored"
        assert "must_change_password" in {c["name"] for c in insp.get_columns("adminuser")}, \
            "a column the farm never received was not restored"
        assert not _drift(old_engine), (
            "an adopted database does not match models.py once it is up to date")
        with Session(old_engine) as s:
            assert s.exec(select(func.count()).select_from(Block)).one() == 1
            assert s.exec(select(func.count()).select_from(Worker)).one() == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_catch_up_lands_exactly_on_the_baseline():
    """The catch-up is what a pre-Alembic farm gets instead of the baseline
    migration, and the database is stamped at the baseline immediately
    afterwards - so it has to arrive at the same schema the baseline
    migration would have built. Not a subset, not a superset.

    A superset is the failure that actually happened: while the catch-up
    worked off models.py it built TODAY's schema, and the first migration
    after the baseline then tried to add a column that was already there.
    That kills the update on exactly the farms furthest behind, and it
    could not happen until a migration finally added a column - which is to
    say, it would have been found on a farm rather than here.
    """
    tmp = tempfile.mkdtemp()
    try:
        baseline_path = os.path.join(tmp, "baseline.db")
        command.upgrade(_config(create_engine(f"sqlite:///{baseline_path}")), BASELINE_REVISION)

        caught_up_path = os.path.join(tmp, "caught_up.db")
        legacy_schema_catch_up(create_engine(f"sqlite:///{caught_up_path}"), _baseline_database())

        caught_up, baseline = _table_shape(caught_up_path), _table_shape(baseline_path)
        assert set(caught_up) == set(baseline), (
            f"tables only one of the two builds: {set(caught_up) ^ set(baseline)}")
        for table in baseline:
            assert caught_up[table] == baseline[table], (
                f"{table} differs\n  catch-up: {caught_up[table]}\n"
                f"  baseline: {baseline[table]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_migrating_an_up_to_date_database_changes_nothing():
    """Startup runs migrations every time. All but the first must be a no-op."""
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "current.db")
        run_migrations(create_engine(f"sqlite:///{path}"), snapshot=False)
        before = _table_shape(path)
        run_migrations(create_engine(f"sqlite:///{path}"), snapshot=False)
        run_migrations(create_engine(f"sqlite:///{path}"), snapshot=False)
        assert _table_shape(path) == before, "re-running migrations altered the schema"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_baseline_is_still_the_root_revision():
    """BASELINE_REVISION is what pre-Alembic farms get stamped at. Pointed at
    anything other than the root, it would stamp an old farm as though
    migrations it never received had already run - and those migrations
    would then never run, silently."""
    script = ScriptDirectory.from_config(_config(engine))
    baseline = script.get_revision(BASELINE_REVISION)
    assert baseline is not None, f"{BASELINE_REVISION} is not in migrations/versions"
    assert baseline.down_revision is None, (
        f"the baseline {BASELINE_REVISION} is no longer the root revision "
        f"(it now follows {baseline.down_revision})")
    assert len(script.get_heads()) == 1, (
        f"the migration history has branched: {script.get_heads()} - farms would apply "
        f"whichever head Alembic happened to pick")


def test_pre_migration_snapshot_is_a_faithful_full_copy():
    """The copy taken before a migration is a rollback point: everything the
    live database had, at that moment, in one file somebody can put back.
    Rows, not just schema - a migration that goes wrong takes the data with
    it, and this copy is the only thing standing between that and a farm."""
    tmp = tempfile.mkdtemp()
    real_db, real_backups = backup.DB_PATH, backup.BACKUPS_DIR
    try:
        path = os.path.join(tmp, "farm.db")
        backup.DB_PATH = path
        backup.BACKUPS_DIR = os.path.join(tmp, "backups")
        os.makedirs(backup.BACKUPS_DIR)

        temp_engine = create_engine(f"sqlite:///{path}")
        run_migrations(temp_engine, snapshot=False)  # any populated database will do here
        with Session(temp_engine) as s:
            s.add(Block(id="15", name="Blok 15", variety="Mauritius", trees=100, hectares=1.5))
            for crate in range(48):
                s.add(HarvestRecord(uuid=f"selftest-{crate:02d}",
                                     timestamp=datetime(2025, 1, 1 + crate // 24, crate % 24),
                                     block_id="15", weight_kg=18.0, deduction_kg=0.0))
            s.commit()

        copy = backup.snapshot_before_migration("selftest")
        con = sqlite3.connect(copy)
        try:
            assert con.execute("SELECT count(*) FROM harvestrecord").fetchone()[0] == 48, \
                "the pre-migration copy dropped the harvest records"
            assert con.execute("SELECT count(*) FROM block").fetchone()[0] == 1
        finally:
            con.close()

        for i in range(5):
            backup.snapshot_before_migration(f"selftest{i}")
        kept = backup._pre_migration_filenames()
        assert len(kept) == backup.PRE_MIGRATION_KEEP, f"retention kept {len(kept)}: {kept}"
        assert backup._backup_filenames() == [], (
            "the nightly pruner can see the pre-migration copies - it would delete "
            "rollback points to make room for backups")
    finally:
        backup.DB_PATH, backup.BACKUPS_DIR = real_db, real_backups
        shutil.rmtree(tmp, ignore_errors=True)


def test_this_server_is_at_the_newest_revision():
    if not os.path.exists(DB_PATH):
        raise Skip("no database yet - start the server once")
    if not inspect(engine).has_table("alembic_version"):
        raise Skip("this database has not been migrated yet - start the server against "
                    "it once, which stamps it at the baseline, then re-run")
    current, head = current_revision(), head_revision()
    assert current == head, (
        f"this database is at {current} but the code here is at {head} - the server "
        f"has not been restarted since the update, so it is running new code against "
        f"an old schema")


def test_this_server_matches_the_models():
    """Read-only drift check on the live database. The same comparison
    migrate.py prints at startup, asserted here so it cannot scroll past
    unnoticed in a Scheduled Task's console."""
    if not inspect(engine).has_table("alembic_version"):
        raise Skip("this database has not been migrated yet")
    diff = _drift(engine)
    assert not diff, "the live schema does not match models.py: " + "; ".join(
        str(entry) for entry in diff)


# ---------------------------------------------------------------------------
# Master data imports
# ---------------------------------------------------------------------------
def test_replacing_all_blocks_with_an_empty_file_is_refused():
    """"Replace all" reads the uploaded file as the farm's new complete block
    list, so an empty one used to mean "the new list is empty" and retired
    every block the farm had - reporting {"imported": 0, "deactivated": 21},
    which reads as success. The template the wizard hands out is itself a
    headings-only file, so the mistake is one download and one upload away."""
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "blocks.db")
        temp_engine = create_engine(f"sqlite:///{path}")
        run_migrations(temp_engine, snapshot=False)
        with Session(temp_engine) as s:
            s.add(Block(id="15", name="Blok 15", variety="Mauritius", trees=100, hectares=1.5))
            s.add(Block(id="16", name="Blok 16", variety="Mauritius", trees=80, hectares=1.2))
            s.commit()

        headings_only = UploadFile(filename="blocks.csv",
                                    file=io.BytesIO(b"id,name,variety,trees,hectares,active\n"))
        with Session(temp_engine) as s:
            try:
                asyncio.run(import_blocks(file=headings_only, replace=True, session=s, admin=None))
            except HTTPException as e:
                assert e.status_code == 400, e.status_code
                assert "no data rows" in e.detail
            else:
                raise AssertionError("an empty file was accepted as the farm's new block list")

        with Session(temp_engine) as s:
            active = s.exec(select(func.count()).select_from(Block).where(Block.active == True)).one()  # noqa: E712
        assert active == 2, f"{2 - active} block(s) were retired by a file with nothing in it"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_importing_blocks_without_a_supplier_column_keeps_the_supplier():
    """A file with no supplier_id COLUMN must leave each block's supplier
    alone; only a column that is present and blank clears it.

    import_blocks rebuilds each row with session.merge, so reading an absent
    column as None silently unassigns every block the moment somebody
    re-imports the spreadsheet they exported before the field existed - which
    is every spreadsheet an established pack house already has. Nothing about
    the result looks wrong: the response still says imported, and the blocks
    are all still there.
    """
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "blocks.db")
        temp_engine = create_engine(f"sqlite:///{path}")
        run_migrations(temp_engine, snapshot=False)
        with Session(temp_engine) as s:
            s.add(Supplier(name="Mkhize Farms"))
            s.commit()
            supplier_id = s.exec(select(Supplier.id)).one()
            s.add(Block(id="15", name="Blok 15", supplier_id=supplier_id))
            s.commit()

        old_format = UploadFile(
            filename="blocks.csv",
            file=io.BytesIO(b"id,name,variety,trees,hectares,active\n15,Blok 15,Mauritius,100,1.5,true\n"))
        with Session(temp_engine) as s:
            asyncio.run(import_blocks(file=old_format, replace=False, session=s, admin=None))
        with Session(temp_engine) as s:
            assert s.get(Block, "15").supplier_id == supplier_id, \
                "a file with no supplier_id column unassigned the block's supplier"

        # ...and a column that IS there, left blank, still clears it.
        with_blank = UploadFile(
            filename="blocks.csv",
            file=io.BytesIO(b"id,name,variety,trees,hectares,supplier_id,active\n15,Blok 15,Mauritius,100,1.5,,true\n"))
        with Session(temp_engine) as s:
            asyncio.run(import_blocks(file=with_blank, replace=False, session=s, admin=None))
        with Session(temp_engine) as s:
            assert s.get(Block, "15").supplier_id is None, \
                "an explicitly blank supplier_id did not clear the block's supplier"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# First-run setup wizard state
#
# The one thing worth asserting against a live database: that this farm is
# NOT offered the wizard. Getting `required` wrong in that direction sits an
# established farm in front of an empty setup form instead of its own
# dashboard, and the check that prevents it deliberately does not trust a
# single flag - so it is worth testing on a real database rather than only
# on a contrived one.
# ---------------------------------------------------------------------------
def _require_setup_table():
    """SetupState is created at server startup (main.on_startup ->
    run_migrations). A database no server has opened since this
    release simply does not have it yet - that is not a failure, and on a
    live farm server, which by definition has started, it never happens."""
    if not inspect(engine).has_table("setupstate"):
        raise Skip("no setupstate table yet - start the server against this "
                    "database once, then re-run")


def test_setup_not_required_on_a_configured_farm():
    _require_setup_table()
    with Session(engine) as s:
        state = build_setup_state(s)
        settings = s.exec(select(SystemSetting)).first()

    if state["started_at"] and not state["completed_at"]:
        # The wizard is open and unfinished - on this farm, right now,
        # somebody is part way through it. Still being offered is correct
        # here, and is the whole reason started_at exists: step 1 writes the
        # farm name, so without it the remaining steps become unreachable on
        # the next page load. Assert that rather than reading a half-done
        # setup as a failure.
        assert state["required"] is True, "an unfinished setup wizard stopped being offered"
        return

    named = bool(settings and (settings.packhouse_name or "").strip())
    picked = state["harvest_records"] > 0
    if not (named or picked or state["completed_at"]):
        return  # a genuinely blank database - the wizard SHOULD be offered
    assert state["required"] is False, (
        f"this farm would be sent through the setup wizard "
        f"(named={named}, crates={state['harvest_records']}, "
        f"started={state['started_at']}, completed={state['completed_at']})")


def test_setup_state_reports_every_step():
    """Every step the wizard knows about has to come back with a verdict -
    a missing key reads as "not done" in the browser and silently reopens
    a step the farm has already finished."""
    _require_setup_table()
    with Session(engine) as s:
        state = build_setup_state(s)
    expected = {"identity", "location", "rate", "thresholds", "blocks",
                "workers", "devices"}
    assert set(state["steps"]) == expected, f"steps drifted: {set(state['steps'])}"
    for key, step in state["steps"].items():
        assert isinstance(step.get("done"), bool), f"{key} has no boolean 'done'"


def test_setup_state_writes_nothing():
    """build_setup_state only reads. It runs on every admin page load, and
    it must not be what creates the SetupState row - the absence of that row
    is exactly how a database that predates the wizard is recognised."""
    _require_setup_table()
    with Session(engine) as s:
        before = s.exec(select(func.count()).select_from(SetupState)).one()
    with Session(engine) as s:
        build_setup_state(s)
    with Session(engine) as s:
        after = s.exec(select(func.count()).select_from(SetupState)).one()
    assert before == after, f"SetupState rows changed: {before} -> {after}"


def main():
    print("Boord self-test")
    print("=" * 60)

    section("Schema migrations")
    for fn in (test_migrations_build_what_the_models_describe,
               test_a_database_from_before_migrations_is_caught_up_and_stamped,
               test_the_catch_up_lands_exactly_on_the_baseline,
               test_migrating_an_up_to_date_database_changes_nothing,
               test_baseline_is_still_the_root_revision,
               test_pre_migration_snapshot_is_a_faithful_full_copy,
               test_this_server_is_at_the_newest_revision,
               test_this_server_matches_the_models):
        check(fn.__name__, fn)

    section("Master data imports")
    for fn in (test_replacing_all_blocks_with_an_empty_file_is_refused,
               test_importing_blocks_without_a_supplier_column_keeps_the_supplier):
        check(fn.__name__, fn)

    section("Setup state")
    for fn in (test_setup_not_required_on_a_configured_farm,
               test_setup_state_reports_every_step,
               test_setup_state_writes_nothing):
        check(fn.__name__, fn)

    print("\n" + "=" * 60)
    if _skipped:
        print(f"SKIPPED: {len(_skipped)}")
        for name, why in _skipped:
            print(f"  - {name}: {why}")
    if _failed:
        print(f"FAILED: {len(_failed)} of {_passed + len(_failed)} checks")
        for name, exc in _failed:
            print(f"  - {name}: {type(exc).__name__}: {exc}")
        return 1
    print(f"PASSED: all {_passed} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
