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
import re
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

from fastapi import HTTPException, UploadFile  # noqa: E402

import backup  # noqa: E402
import migrate  # noqa: E402
import version  # noqa: E402
from db import DB_PATH, engine, legacy_schema_catch_up  # noqa: E402
from migrate import (BASELINE_REVISION, _baseline_database, _config,  # noqa: E402
                     current_revision, head_revision, run_migrations)
from models import (Block, HarvestRecord, SetupState,  # noqa: E402
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
                    "VALUES ('001', 'Thandi', 'N', 'Thandi N', '', '', '', '8001015009087', '', 1)")
        # The office typed IDs into WhatsApp Number; 3f8a2c6d9e15 moves them
        # out and must leave a real cell number where it is.
        con.execute("INSERT INTO worker (id, first_name, last_name, name, id_number, "
                    "bank, account, whatsapp_number, photo_filename, active) "
                    "VALUES ('002', 'Sipho', 'D', 'Sipho D', '', '', '', '082 123 4567', '', 1)")
        con.commit()
        con.close()

        con = sqlite3.connect(path)
        con.execute("DROP TABLE setupstate")
        # supplier is empty here, so dropping a column off it damages the
        # schema without touching the rows the assertions below count. It
        # used to be adminuser.must_change_password, until the release that
        # removed accounts dropped that table out from under this fixture.
        con.execute("ALTER TABLE supplier DROP COLUMN is_own_farm")
        con.commit()
        con.close()

        assert current_revision(old_engine) is None, "a pre-Alembic database claimed a revision"
        run_migrations(old_engine, snapshot=False)

        assert current_revision(old_engine) == head_revision(), (
            "an existing farm database did not end up at the newest revision")
        insp = inspect(old_engine)
        assert insp.has_table("setupstate"), "a table the farm never received was not restored"
        assert "is_own_farm" in {c["name"] for c in insp.get_columns("supplier")}, \
            "a column the farm never received was not restored"
        assert not _drift(old_engine), (
            "an adopted database does not match models.py once it is up to date")
        with Session(old_engine) as s:
            assert s.exec(select(func.count()).select_from(Block)).one() == 1
            assert s.exec(select(func.count()).select_from(Worker)).one() == 2
            thandi, sipho = s.get(Worker, "001"), s.get(Worker, "002")
            assert (thandi.id_number, thandi.whatsapp_number) == ("8001015009087", ""), \
                "an ID number typed into WhatsApp Number was not moved to id_number"
            assert (sipho.id_number, sipho.whatsapp_number) == ("", "082 123 4567"), \
                "a real WhatsApp number was moved to id_number"
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
                import_blocks(file=headings_only, replace=True, session=s, _admin=None)
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
            import_blocks(file=old_format, replace=False, session=s, _admin=None)
        with Session(temp_engine) as s:
            assert s.get(Block, "15").supplier_id == supplier_id, \
                "a file with no supplier_id column unassigned the block's supplier"

        # ...and a column that IS there, left blank, still clears it.
        with_blank = UploadFile(
            filename="blocks.csv",
            file=io.BytesIO(b"id,name,variety,trees,hectares,supplier_id,active\n15,Blok 15,Mauritius,100,1.5,,true\n"))
        with Session(temp_engine) as s:
            import_blocks(file=with_blank, replace=False, session=s, _admin=None)
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


def test_a_locked_database_refuses_to_migrate():
    """migrate.py, run by hand, must refuse while something else has the
    database open.

    The real defence is stop_server.ps1 checking port 8000 is free, because
    the usual case is a uvicorn process outliving the launcher that started
    it. This is the backstop for what a port check cannot see - a second
    server started by hand - and it matters because the failure it prevents
    is silent: SQLite will let Alembic rewrite tables under a live reader,
    and the pre-migration copy is taken through the backup API, which
    coordinates with a live writer and succeeds. Nothing errors. The farm
    finds out later.
    """
    tmp = tempfile.mkdtemp()
    real_db = migrate.DB_PATH
    holder = None
    try:
        path = os.path.join(tmp, "farm.db")
        temp_engine = create_engine(f"sqlite:///{path}")
        run_migrations(temp_engine, snapshot=False)
        migrate.DB_PATH = path

        # Nothing holding it: the guard must stay out of the way.
        migrate._refuse_if_database_is_in_use()

        holder = sqlite3.connect(path, timeout=1)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            migrate._refuse_if_database_is_in_use()
        except SystemExit as exc:
            assert exc.code == 1, f"refused with exit code {exc.code}, expected 1"
        else:
            raise AssertionError(
                "migrate.py did not refuse to run against a database another "
                "connection had locked")
    finally:
        if holder is not None:
            holder.rollback()
            holder.close()
        migrate.DB_PATH = real_db
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Version
#
# The server had no version at all until recently - the only one anywhere was
# the constant in frontend/shared/api.js, which describes what a browser
# loaded rather than what the server is serving. These checks are about the
# reporting staying true, not about the number itself: a version endpoint
# that quietly reports null is worse than none, because it looks like an
# answer.
# ---------------------------------------------------------------------------

def test_the_version_regex_still_matches_the_constant():
    """backend/version.py reads Boord.VERSION out of api.js with the same
    anchored expression scripts/release.sh greps with. Reformat that line -
    change the indentation, drop the trailing comma - and both stop matching
    at once: the release gate stops checking, and every farm reports a null
    version. Neither failure announces itself."""
    declared = version._declared_version()
    assert declared is not None, (
        f"could not read VERSION out of {version.API_JS} - the line no longer matches "
        f"the expression this and scripts/release.sh both depend on")
    assert declared.strip() == declared and declared, f"VERSION parsed as {declared!r}"


def test_the_declared_version_matches_the_checked_out_tag():
    """On a release checkout, api.js and the tag must agree. release.sh
    refuses to sign a tag where they do not, so a disagreement here means
    somebody moved the checkout by hand."""
    info = version.version_info()
    if info["state"] != "release":
        raise Skip(f"not on a release tag (state: {info['state']}) - nothing to compare")
    assert info["tag"].lstrip("v") == info["frontend_version"], (
        f"tag {info['tag']} but api.js says {info['frontend_version']}")
    assert info["matches"] is True


def test_version_info_survives_without_git():
    """Reported honestly rather than raised. The servers whose version you
    most want to read are the ones where something is already wrong, so this
    endpoint has to answer on a machine with no git, no tags, and a
    repository git refuses to read."""
    real_run, real_static = version._run_git, version._static
    try:
        version._run_git = lambda args: (False, "", "git not found on PATH")
        version._static = None
        info = version.version_info()
    finally:
        version._run_git, version._static = real_run, real_static

    for key in ("version", "tag", "describe", "state", "frontend_version", "matches",
                "git_error", "alembic_head", "alembic_current", "backups", "update"):
        assert key in info, f"{key} missing from version_info() when git is unavailable"
    assert info["state"] in ("reported", "unknown"), info["state"]
    assert info["frontend_version"] is not None, (
        "with no git, api.js is the only source left - it must still be read")


def test_the_reported_alembic_head_is_the_real_head():
    """The heartbeat sends head and current so a farm that never ran its
    migrations is visible from off-site. That only works if head is the
    genuine head rather than a copy that drifted."""
    info = version.version_info()
    assert info["alembic_head"] == head_revision(), (
        f"version_info reports head {info['alembic_head']}, migrate says {head_revision()}")


# ---------------------------------------------------------------------------
# Backups leaving the machine
#
# All in temporary folders, monkey-patching backup.py's module-level paths and
# putting them back in a finally - this suite never writes to the server's own
# data/backups.
#
# The property worth protecting is that the off-site copy can fail in every
# way a removable drive fails, and none of them may cost the farm its local
# backup or its nightly scheduler thread.
# ---------------------------------------------------------------------------

def _offsite_sandbox():
    """(tmp root, restore fn). Patches every path copy_offsite touches."""
    tmp = tempfile.mkdtemp()
    saved = (backup.BACKUPS_DIR, backup.COPY_DEST_FILE, backup.OFFSITE_STATE_PATH)
    backup.BACKUPS_DIR = os.path.join(tmp, "backups")
    backup.COPY_DEST_FILE = os.path.join(tmp, "backup_copy_to.txt")
    backup.OFFSITE_STATE_PATH = os.path.join(backup.BACKUPS_DIR, "last_offsite_copy.json")
    os.makedirs(backup.BACKUPS_DIR)

    def restore():
        backup.BACKUPS_DIR, backup.COPY_DEST_FILE, backup.OFFSITE_STATE_PATH = saved
        shutil.rmtree(tmp, ignore_errors=True)

    return tmp, restore


def _write_dest(path):
    with open(backup.COPY_DEST_FILE, "w", encoding="utf-8") as fh:
        fh.write("# where finished backups are copied\n")
        fh.write(path + "\n")


def _fake_archive(name="backup_20260101_000000.zip"):
    full = os.path.join(backup.BACKUPS_DIR, name)
    with open(full, "wb") as fh:
        fh.write(os.urandom(4096))
    return name, full


def test_no_destination_configured_is_not_a_failure():
    """Most installs will never set this. Not configured has to be silent and
    stateless - not an error, and not a state file that later reads as a
    failed copy."""
    _, restore = _offsite_sandbox()
    try:
        name, _ = _fake_archive()
        result = backup.copy_offsite(name)
        assert result == {"configured": False}, result
        assert not os.path.exists(backup.OFFSITE_STATE_PATH), (
            "an unconfigured install wrote an off-site state file")
        status = backup.offsite_status()
        assert status["configured"] is False and status["last"] is None
    finally:
        restore()


def test_a_missing_destination_never_raises():
    """The USB stick is unplugged. This is the normal case, not the strange
    one, and it must cost nothing: the local backup still happens, the
    scheduler thread survives, and the failure is recorded where somebody can
    see it rather than thrown."""
    tmp, restore = _offsite_sandbox()
    try:
        _write_dest(os.path.join(tmp, "not-plugged-in", "boord"))
        name, _ = _fake_archive()
        result = backup.copy_offsite(name)
        assert result["ok"] is False
        assert result["error"], "a missing destination recorded no reason"
        assert result["consecutive_failures"] == 1

        # And again - the count is what makes "failing every night since
        # April" different from "failed once".
        result = backup.copy_offsite(name)
        assert result["consecutive_failures"] == 2, result

        status = backup.offsite_status()
        assert status["configured"] is True
        assert status["problem"], "offsite_status did not report the missing folder"
    finally:
        restore()


def test_the_offsite_copy_is_byte_identical():
    """A copy that is not byte-for-byte the archive is not a backup. Also
    checks no .part survives: an interrupted copy left under the real name
    would be found by whoever is restoring, at the worst possible moment."""
    tmp, restore = _offsite_sandbox()
    try:
        dest = os.path.join(tmp, "offsite")
        os.makedirs(dest)
        _write_dest(dest)
        name, source = _fake_archive()

        result = backup.copy_offsite(name)
        assert result["ok"] is True, result

        with open(source, "rb") as fh:
            original = fh.read()
        with open(os.path.join(dest, name), "rb") as fh:
            copied = fh.read()
        assert original == copied, "the off-site copy is not byte-identical"
        assert [f for f in os.listdir(dest)] == [name], os.listdir(dest)

        # A destination this code does not own: it may add, never remove.
        theirs = os.path.join(dest, "someone-elses-file.txt")
        with open(theirs, "w", encoding="utf-8") as fh:
            fh.write("not ours")
        backup.copy_offsite(name)
        assert os.path.exists(theirs), "the off-site copy deleted a file it did not put there"
    finally:
        restore()


def test_a_destination_inside_the_repo_is_refused():
    """The two destinations that are certainly wrong, because they are not off
    the machine at all - and would show a green light for a copy that dies
    with the same disk."""
    for path, what in ((backup.BACKUPS_DIR, "the backups folder"),
                        (os.path.join(backup.REPO_ROOT, "data"), "inside the repo"),
                        (backup.REPO_ROOT, "the repo root")):
        assert backup._reject_destination(path), f"{what} was accepted as an off-site destination"

    # And a folder that is genuinely elsewhere is not refused for being one.
    elsewhere = tempfile.mkdtemp()
    try:
        assert backup._reject_destination(elsewhere) is None, (
            f"a folder outside the machine's Boord install was refused: {elsewhere}")
    finally:
        shutil.rmtree(elsewhere, ignore_errors=True)


# ---------------------------------------------------------------------------
# Admin access
#
# Boord has no login. What decides who sees Settings, payments, exports and
# every worker's contact details is the network a request arrives on: only
# Tailscale gets in - not the farm wifi, and as of this release not the
# server's own console either (backend/security.py).
#
# That makes these checks load-bearing in a way the old password never was.
# The Field and Pack House screens are still served to every phone on the farm
# wifi, so a guard silently dropped from one endpoint is not a locked door left
# ajar - it is worker PII on the LAN, with nothing in the UI to show for it.
# The route list below is asserted whole, in both directions, so adding an
# endpoint or dropping a guard fails here rather than passing quietly.
# ---------------------------------------------------------------------------
ADMIN_ONLY_ROUTES = {
    ("DELETE", "/api/blocks/{block_id}"),
    ("DELETE", "/api/devices/{device_id}"),
    ("DELETE", "/api/harvest-records/{record_uuid}"),
    ("DELETE", "/api/reports/market-invoices"),
    ("DELETE", "/api/suppliers/{supplier_id}"),
    ("DELETE", "/api/teams/{team_id}"),
    ("DELETE", "/api/workers/{worker_id}"),
    ("GET", "/api/backups"),
    ("GET", "/api/backups/offsite"),
    ("GET", "/api/backups/{filename}/download"),
    ("GET", "/api/blocks/export"),
    ("GET", "/api/dashboard/summary"),
    ("GET", "/api/devices"),
    ("GET", "/api/harvest-records/counts"),
    ("GET", "/api/lots/{lot_id}"),
    ("GET", "/api/payments"),
    ("GET", "/api/payments/export"),
    ("GET", "/api/reports/block-harvest"),
    ("GET", "/api/reports/daily-harvest"),
    ("GET", "/api/reports/harvest-data"),
    ("GET", "/api/reports/harvesting-list"),
    ("GET", "/api/reports/in-transit-list"),
    ("GET", "/api/reports/delivery-notes"),
    ("GET", "/api/reports/litchi-wages"),
    ("GET", "/api/reports/market-mail"),
    ("GET", "/api/reports/market-notes"),
    ("GET", "/api/reports/market-packs"),
    ("GET", "/api/reports/market-sales"),
    ("GET", "/api/reports/market-summary"),
    ("GET", "/api/reports/pallets-stacked"),
    ("GET", "/api/reports/lot-receiving"),
    ("GET", "/api/reports/picking-notes"),
    ("GET", "/api/reports/received-list"),
    ("GET", "/api/reports/team-picking-list"),
    ("GET", "/api/reports/transport-summary"),
    ("GET", "/api/reports/worker-harvest"),
    ("GET", "/api/setup/state"),
    ("GET", "/api/suppliers/{supplier_id}/billing"),
    ("GET", "/api/workers/export"),
    ("PATCH", "/api/harvest-records/{record_uuid}"),
    ("POST", "/api/backups"),
    ("POST", "/api/blocks"),
    ("POST", "/api/blocks/import"),
    ("POST", "/api/devices"),
    ("POST", "/api/payments/calculate"),
    ("POST", "/api/rate-settings"),
    ("POST", "/api/reports/market-mail/fetch"),
    ("POST", "/api/setup/complete"),
    ("POST", "/api/setup/start"),
    ("POST", "/api/suppliers"),
    ("POST", "/api/teams"),
    ("POST", "/api/workers"),
    ("POST", "/api/workers/import"),
    ("POST", "/api/workers/{worker_id}/photo"),
    ("PUT", "/api/reports/market-notes/{note_uuid}"),
    ("PUT", "/api/reports/market-products"),
    ("PUT", "/api/system-settings"),
}


def _guarded_routes():
    """(method, path) for every route that depends on require_admin_client."""
    import main  # noqa: E402 - imported here so a broken app fails this check
    from security import require_admin_client  # noqa: E402

    found = set()
    for route in main.app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        if any(d.call is require_admin_client for d in dependant.dependencies):
            for method in set(route.methods) - {"HEAD", "OPTIONS"}:
                found.add((method, route.path))
    return found


def _request_from(host, http_host=None):
    """The smallest thing security.py will read from. It only touches
    request.client and the Host headers, so there is no need to stand up a
    server to ask it what it makes of an address."""
    from fastapi import Request  # noqa: E402

    headers = []
    if http_host is not None:
        headers.append((b"host", http_host.encode()))
    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers}
    if host is not None:
        scope["client"] = (host, 54321)
    return Request(scope)


def _without_dev_loopback():
    """security.py reads DEV_LOOPBACK_ENV at call time, and run_preview.py sets
    it - so a developer running this suite after the preview server, in the same
    shell, would otherwise have every loopback assertion below silently invert."""
    import os  # noqa: E402
    from security import DEV_LOOPBACK_ENV  # noqa: E402

    return _EnvUnset(os, DEV_LOOPBACK_ENV)


class _EnvUnset:
    def __init__(self, os_module, name):
        self.os, self.name = os_module, name

    def __enter__(self):
        self.old = self.os.environ.pop(self.name, None)

    def __exit__(self, *exc):
        if self.old is not None:
            self.os.environ[self.name] = self.old


def test_every_admin_only_endpoint_is_still_guarded():
    missing = ADMIN_ONLY_ROUTES - _guarded_routes()
    assert not missing, (
        "these endpoints no longer require the admin network - anything on the "
        f"farm wifi can call them: {sorted(missing)}")


def test_nothing_new_slipped_behind_the_admin_guard():
    """The other direction, and not pedantry: the Field and Pack House screens
    call this same API from tablets that are NOT on the admin network, so an
    endpoint that quietly grows the guard breaks in the orchard rather than
    anywhere a developer is looking."""
    extra = _guarded_routes() - ADMIN_ONLY_ROUTES
    assert not extra, (
        "these endpoints became admin-only without this list being updated - "
        f"check that no Field or Pack House screen calls them: {sorted(extra)}")


def test_the_farm_wifi_cannot_reach_the_admin_app():
    from security import is_admin_client  # noqa: E402

    with _without_dev_loopback():
        for host in ("192.168.68.114", "192.168.68.1", "10.0.0.5", "172.16.4.9",
                     "41.13.7.22", "2c0f:f8f0::1"):
            assert not is_admin_client(_request_from(host)), \
                f"{host} was treated as the admin, and that is the farm wifi"


def test_a_forged_host_header_is_not_enough():
    """The .ts.net check is only ever a second signal on top of a loopback
    peer. A Host header costs nothing to fake, so on its own it must buy
    nothing - otherwise one curl from a picker's phone reads the payroll."""
    from security import is_admin_client  # noqa: E402

    with _without_dev_loopback():
        forged = _request_from("192.168.68.50",
                               http_host="bekfontein-server.taile32cb0.ts.net")
        assert not is_admin_client(forged), \
            "a Host header alone let the farm wifi into the Admin app"


def test_the_tailnet_can_reach_it():
    """Signal 1: uvicorn rewrote request.client from X-Forwarded-For, which it
    trusts only from loopback, so this is `tailscale serve` or a direct hit on
    the tailnet IP."""
    from security import is_admin_client  # noqa: E402

    with _without_dev_loopback():
        for host in ("100.64.0.1", "100.101.102.103", "fd7a:115c:a1e0::1234"):
            assert is_admin_client(_request_from(host)), \
                f"{host} was locked out of the Admin app"


def test_loopback_alone_is_not_the_admin_any_more():
    """The server's own console is refused like anywhere else. A loopback
    exemption is one that anybody with a remote desktop session to that laptop
    inherits, and AnyDesk is how this server is normally reached."""
    from security import is_admin_client  # noqa: E402

    with _without_dev_loopback():
        for host in ("127.0.0.1", "::1"):
            for http_host in (None, "localhost:8000", "127.0.0.1:8000"):
                assert not is_admin_client(_request_from(host, http_host)), \
                    f"the console got in as {host} with Host {http_host!r}"


def test_loopback_carrying_a_tailnet_host_still_gets_in():
    """Signal 2, and the reason this release cannot lock the farm out: if
    Tailscale ever stops sending X-Forwarded-For, its traffic arrives as plain
    loopback and signal 1 stops matching. The Host it asks for is what still
    tells it apart from somebody sitting at the console."""
    from security import is_admin_client  # noqa: E402

    with _without_dev_loopback():
        for http_host in ("bekfontein-server.taile32cb0.ts.net",
                          "bekfontein-server.taile32cb0.ts.net:443"):
            assert is_admin_client(_request_from("127.0.0.1", http_host)), \
                f"tailscale serve would have been refused with Host {http_host!r}"


def test_the_dev_escape_hatch_is_opt_in_and_off_by_default():
    """run_preview.py sets this so a developer's Mac, which has no Tailscale in
    front of it, can still open Admin. start_server.bat never does. If it ever
    started defaulting to on, the farm's console would silently be let back
    in."""
    import os  # noqa: E402
    from security import DEV_LOOPBACK_ENV, is_admin_client  # noqa: E402

    with _without_dev_loopback():
        assert not is_admin_client(_request_from("127.0.0.1", "localhost:8000"))
        os.environ[DEV_LOOPBACK_ENV] = "1"
        try:
            assert is_admin_client(_request_from("127.0.0.1", "localhost:8000")), \
                "run_preview.py could not open Admin - local development is broken"
        finally:
            os.environ.pop(DEV_LOOPBACK_ENV, None)


def test_a_request_with_no_peer_is_not_the_admin():
    """ASGI transports may omit the client entirely. Reading that as trusted
    would open the Admin app to everyone the moment anything at all sat in
    front of uvicorn."""
    from security import is_admin_client  # noqa: E402

    with _without_dev_loopback():
        assert not is_admin_client(_request_from(None))
        assert not is_admin_client(_request_from("not-an-ip-address"))



# ---------------------------------------------------------------------------
# Frontend app shell
#
# Each screen's service worker caches its shell - HTML, JS, CSS - and as of
# v3.4 refreshes the whole set together or not at all. Writing each file back
# on its own could leave a device holding one release's index.html beside
# another's app.js, and the failure that produces is not a degraded screen but
# a blank one: the older JavaScript reaches for an element the newer HTML does
# not have and throws before anything renders. That happened on a farm server
# on 2026-09-04 and read, from the outside, as the server being down.
#
# Refreshing atomically buys that safety at the cost of a new failure mode,
# which is what the first check below exists for: if any single SHELL entry
# cannot be fetched, refreshShell() commits nothing, and the screen is pinned
# to its installed shell forever. One typo'd path would freeze updates for
# every device, silently.
# ---------------------------------------------------------------------------
SERVICE_WORKERS = ("frontend/admin/service-worker.js",
                   "frontend/field/service-worker.js",
                   "frontend/packhouse/service-worker.js",
                   "frontend/service-worker.js")

SCREEN_ENTRY_POINTS = ("frontend/admin/app.js",
                       "frontend/field/app.js",
                       "frontend/packhouse/receiving.js")


# ---------------------------------------------------------------------------
# Deleted crates
#
# A crate deleted by the admin, or undone in the field, is a wage that must
# stay gone - and the field device is built to resend whatever it thinks never
# landed. Runs in a throwaway database, like the migration checks.
# ---------------------------------------------------------------------------
def test_a_deleted_crate_stays_deleted():
    from datetime import timezone  # noqa: E402
    from models import DeletedHarvestRecord, Lot, ReceivingRecord  # noqa: E402
    from routers.harvest_records import (HarvestRecordEdit, delete_harvest_record,  # noqa: E402
                                         edit_harvest_record)
    from routers.lots import LotIn, upsert_lot  # noqa: E402
    from routers.sync import FieldUndoBatch, HarvestSyncBatch, sync_harvest, undo_harvest  # noqa: E402

    tmp = tempfile.mkdtemp()
    try:
        eng = create_engine(f"sqlite:///{os.path.join(tmp, 't.db')}")
        run_migrations(eng, snapshot=False)
        now = datetime.now(timezone.utc)

        def crate(uuid, slip="S1"):
            return {"uuid": uuid, "timestamp": now, "worker_id": "001", "block_id": "1",
                    "weight_kg": 20.0, "device_id": "dev1", "slip_number": slip}

        def sync(*uuids, slip="S1"):
            sync_harvest(HarvestSyncBatch(records=[crate(u, slip) for u in uuids]), s)

        def lot(slip):
            return s.exec(select(Lot).where(Lot.slip_number == slip)).first()

        def dispatch():
            return upsert_lot(LotIn(slip_number="S1", timestamp=now, device_id="dev1",
                                    total_crates=2, total_kg=40, status="in_transit"), s)

        with Session(eng) as s:
            s.add_all([Worker(id="001"), Block(id="1"), Block(id="2"), Block(id="3", active=False)])
            s.commit()
            sync("a", "b", "c")

            # Field undo while picking - including a crate the undo beat to the server.
            assert undo_harvest(FieldUndoBatch(device_id="dev1", uuids=["c", "x"]), s) == {"refused": []}
            sync("c", "x")
            assert s.get(HarvestRecord, "c") is None and s.get(HarvestRecord, "x") is None, (
                "a replayed sync brought an undone crate back")
            assert s.get(DeletedHarvestRecord, "x"), "an undo that beat its crate left no tombstone"
            assert undo_harvest(FieldUndoBatch(device_id="dev2", uuids=["b"]), s)["refused"] == ["b"], (
                "a device undid another device's crate")

            dispatch()
            assert undo_harvest(FieldUndoBatch(device_id="dev1", uuids=["b"]), s)["refused"] == ["b"], (
                "the field undid a crate already on a truck")

            # Admin: block edit is validated and survives a replay.
            edit_harvest_record("a", HarvestRecordEdit(block_id="2"), s)
            try:
                edit_harvest_record("a", HarvestRecordEdit(block_id="3"), s)
                raise AssertionError("a crate was moved onto an inactive block")
            except HTTPException as exc:
                assert exc.status_code == 400
            sync("a")
            assert s.get(HarvestRecord, "a").block_id == "2", "a replay undid the admin's block edit"

            # Admin delete re-derives the lot, and neither a replay nor a stale
            # dispatch retry puts the crate or its kg back.
            assert delete_harvest_record("b", s)["lot"]["total_crates"] == 1
            sync("b")
            assert s.get(HarvestRecord, "b") is None, "a replayed sync brought a deleted crate back"
            assert dispatch().total_crates == 1, "a stale dispatch restored a deleted crate's totals"

            # The admin deleting a slip's last crate deletes the slip, and a
            # stale dispatch retry can't bring it back.
            s.add(ReceivingRecord(lot_id=lot("S1").id, timestamp=now))
            s.commit()
            assert delete_harvest_record("a", s)["lot"].get("deleted"), "emptied slip not reported deleted"
            assert lot("S1") is None, "a slip with no crates left was not deleted"
            assert not s.exec(select(ReceivingRecord)).all(), "the deleted slip's receiving row was left behind"
            dispatch()
            assert lot("S1") is None, "a stale dispatch re-created a deleted slip"

            # A field undo emptying the slip still being picked removes its
            # placeholder - and picking on into the same slip brings it back.
            sync("d", slip="S2")
            undo_harvest(FieldUndoBatch(device_id="dev1", uuids=["d"]), s)
            assert lot("S2") is None, "undoing the only crate left an empty placeholder slip"
            sync("e", slip="S2")
            assert lot("S2") is not None, "a crate on an undone-to-empty slip found no slip"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_empty_slip_cleanup_only_takes_emptied_slips():
    """Migration 9d4f1a6b2e70 deletes slips v3.13 left at 0 crates. It must
    take only slips that lost every crate to a delete - never a lot logged by
    hand at receiving, which never had crates in the first place."""
    tmp = tempfile.mkdtemp()
    try:
        eng = create_engine(f"sqlite:///{os.path.join(tmp, 't.db')}")
        cfg = _config(eng)
        command.upgrade(cfg, "5b2e9d71c3a4")
        with eng.begin() as conn:
            for lot_id, slip in ((1, "emptied"), (2, "manual"), (3, "partly")):
                conn.exec_driver_sql(
                    "INSERT INTO lot (id, slip_number, timestamp, driver, total_crates, total_kg, status,"
                    " notes, weather_condition) VALUES (?, ?, '2026-10-07', '', 0, 0, 'received', '', '')",
                    (lot_id, slip))
            conn.exec_driver_sql(
                "INSERT INTO receivingrecord (lot_id, timestamp, expected_crates, actual_crates, discrepancy,"
                " condition, waste_kg, notes, received_by) VALUES (1, '2026-10-07', 0, 0, 0, '', 0, '', ''),"
                " (2, '2026-10-07', 0, 5, 0, '', 0, '', '')")
            conn.exec_driver_sql(
                "INSERT INTO harvestrecord (uuid, timestamp, weight_kg, deduction_kg, lot_id, notes,"
                " weather_condition) VALUES ('kept', '2026-10-07', 20, 0, 3, '', '')")
            conn.exec_driver_sql(
                "INSERT INTO deletedharvestrecord (uuid, lot_id, deleted_at, deleted_by) VALUES"
                " ('gone1', 1, '2026-10-07', 'admin'), ('gone3', 3, '2026-10-07', 'admin')")
        command.upgrade(cfg, "head")
        with eng.connect() as conn:
            slips = {r[0] for r in conn.exec_driver_sql("SELECT slip_number FROM lot")}
            assert slips == {"manual", "partly"}, f"wrong slips survived the cleanup: {slips}"
            assert [r[0] for r in conn.exec_driver_sql("SELECT lot_id FROM receivingrecord")] == [2]
            tombs = dict(conn.exec_driver_sql("SELECT uuid, slip_number FROM deletedharvestrecord").all())
            assert tombs == {"gone1": "emptied", "gone3": "partly"}, f"tombstones lost their slip: {tombs}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_slip_sends_fewer_crates_than_were_picked_and_shows_its_blocks():
    from datetime import timezone  # noqa: E402
    from fastapi import HTTPException  # noqa: E402
    from routers.lots import LotIn, list_in_transit, upsert_lot  # noqa: E402
    from routers.sync import HarvestSyncBatch, sync_harvest  # noqa: E402

    tmp = tempfile.mkdtemp()
    try:
        eng = create_engine(f"sqlite:///{os.path.join(tmp, 't.db')}")
        run_migrations(eng, snapshot=False)
        now = datetime.now(timezone.utc)
        with Session(eng) as s:
            s.add_all([Worker(id="001"), Block(id="15"), Block(id="8a")])
            s.commit()
            sync_harvest(HarvestSyncBatch(records=[
                {"uuid": f"c{i}", "timestamp": now, "worker_id": "001", "block_id": b,
                 "weight_kg": 10.0, "device_id": "dev1", "slip_number": "S1"}
                for i, b in enumerate(("15", "15", "8a", "8a"))]), s)

            def send(n):
                return upsert_lot(LotIn(slip_number="S1", timestamp=now, device_id="dev1", total_crates=4,
                                        total_kg=40, transport_crates=n, status="in_transit"), s)

            for bad in (0, 5):
                try:
                    send(bad)
                    raise AssertionError(f"transport_crates={bad} was accepted")
                except HTTPException as e:
                    assert e.status_code == 400
            send(2)
            row = list_in_transit(session=s)[0]
            assert (row["total_crates"], row["transport_crates"]) == (4, 2), "picking count was overwritten"
            assert row["blocks"] == ["15", "8a"], f"slip lost its blocks: {row['blocks']}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Receiving and wages
#
# The two places a retry or a re-run is routine and used to change the
# answer: the gate confirming a receipt again after the Wi-Fi ate the first
# reply, and the office re-running Calculate Wages after correcting a crate.
# Throwaway databases, like the checks above.
# ---------------------------------------------------------------------------
def _farm(tmp):
    """A migrated throwaway database with one worker, one block and the
    pack house's own supplier. Returns (engine, own supplier id)."""
    eng = create_engine(f"sqlite:///{os.path.join(tmp, 't.db')}")
    run_migrations(eng, snapshot=False)
    with Session(eng) as s:
        own = Supplier(name="Own fruit", is_own_farm=True)
        s.add_all([Worker(id="001"), Block(id="1"), own])
        s.commit()
        s.refresh(own)
        return eng, own.id


def test_a_replayed_dispatch_cannot_undo_receiving():
    from datetime import timedelta, timezone  # noqa: E402
    from models import Lot, LotStatus, ReceivingRecord  # noqa: E402
    from routers.lots import ExternalLotIn, LotIn, create_external_lot, upsert_lot  # noqa: E402
    from routers.receiving import ReceivingRecordIn, confirm_receipt  # noqa: E402
    from routers.sync import HarvestSyncBatch, sync_harvest  # noqa: E402

    tmp = tempfile.mkdtemp()
    try:
        eng, own_id = _farm(tmp)
        now = datetime.now(timezone.utc)
        with Session(eng) as s:
            sync_harvest(HarvestSyncBatch(records=[
                {"uuid": f"c{i}", "timestamp": now, "worker_id": "001", "block_id": "1",
                 "weight_kg": 20.0, "device_id": "dev1", "slip_number": "S1"} for i in (1, 2)]), s)

            def dispatch():
                return upsert_lot(LotIn(slip_number="S1", timestamp=now, device_id="dev1", total_crates=2,
                                        total_kg=40, status="in_transit"), s)

            lot = dispatch()
            assert lot.status == LotStatus.in_transit

            # The gate confirms, the reply is lost, the clerk confirms again
            # with different numbers: the first receipt is the one that stands.
            first = confirm_receipt(ReceivingRecordIn(lot_id=lot.id, timestamp=now, expected_crates=2,
                                                      actual_crates=2, received_by="Sipho"), s)
            again = confirm_receipt(ReceivingRecordIn(lot_id=lot.id, timestamp=now + timedelta(minutes=5),
                                                      expected_crates=2, actual_crates=1, received_by="Sipho"), s)
            assert (again.id, again.actual_crates) == (first.id, 2), "a second confirmation was recorded"
            assert len(s.exec(select(ReceivingRecord)).all()) == 1, "a lot was received twice"
            received_at = s.get(Lot, lot.id).received_at
            assert received_at is not None

            # The tablet replays its queued dispatch after the truck was
            # checked in. Answered, but the lot stays received.
            replayed = dispatch()
            assert replayed.status == LotStatus.received, "a replayed dispatch put a received lot back in transit"
            assert s.get(Lot, lot.id).received_at == received_at, "a replayed dispatch moved received_at"

            # An external delivery needs real numbers, not a minus sign.
            for bad in ({"total_crates": 0, "total_kg": 10.0}, {"total_crates": 5, "total_kg": -1.0}):
                try:
                    create_external_lot(ExternalLotIn(supplier_id=own_id, **bad), s)
                    raise AssertionError(f"an external delivery of {bad} was accepted")
                except HTTPException as e:
                    assert e.status_code == 400
            assert create_external_lot(ExternalLotIn(supplier_id=own_id, total_crates=5, total_kg=100.0), s).id
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rerunning_wages_takes_back_what_a_correction_took_away():
    from datetime import timezone  # noqa: E402
    from models import Payment, RateSetting  # noqa: E402
    from routers.harvest_records import HarvestRecordEdit, edit_harvest_record  # noqa: E402
    from routers.payments import calculate_payments  # noqa: E402
    from routers.sync import HarvestSyncBatch, sync_harvest  # noqa: E402
    from timeutil import to_local  # noqa: E402

    tmp = tempfile.mkdtemp()
    try:
        eng, own_id = _farm(tmp)
        now = datetime.now(timezone.utc)
        day = to_local(now).date()
        with Session(eng) as s:
            other = Supplier(name="Neighbour")
            s.add(other)
            s.commit()
            s.refresh(other)
            s.add_all([Worker(id="002", supplier_id=own_id), Worker(id="003", supplier_id=other.id),
                       RateSetting(effective_date=day, default_rate_per_kg=3.0)])
            s.get(Worker, "001").supplier_id = own_id
            s.commit()
            sync_harvest(HarvestSyncBatch(records=[
                {"uuid": "a", "timestamp": now, "worker_id": "001", "block_id": "1", "weight_kg": 20.0,
                 "device_id": "dev1", "slip_number": "S1"},
                {"uuid": "c", "timestamp": now, "worker_id": "003", "block_id": "1", "weight_kg": 10.0,
                 "device_id": "dev2", "slip_number": "S2"}]), s)

            def sheet(supplier_id=None):
                return {p.worker_id: p.amount_due for p in calculate_payments(day, day, supplier_id, s)}

            def stored():
                return {p.worker_id: p.amount_due for p in s.exec(select(Payment)).all()}

            assert sheet() == {"001": 60.0, "003": 30.0}
            # The office moves 001's only crate onto 002 and, as the Dashboard
            # tells it to, re-runs Calculate Wages - for its own farm only.
            edit_harvest_record("a", HarvestRecordEdit(worker_id="002"), s)
            assert sheet(own_id) == {"002": 60.0}
            assert stored() == {"002": 60.0, "003": 30.0}, (
                "the old worker's row stayed on the wage sheet, or the other supplier's was taken off")
            assert sheet() == {"002": 60.0, "003": 30.0}
            assert stored() == {"002": 60.0, "003": 30.0}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_zero_wage_rate_is_refused():
    from models import RateSetting  # noqa: E402
    from routers.master_data import RateSettingIn, create_rate_setting  # noqa: E402

    tmp = tempfile.mkdtemp()
    try:
        eng, _ = _farm(tmp)
        today = datetime.now().date()
        with Session(eng) as s:
            for bad in (dict(default_rate_per_kg=0), dict(default_rate_per_kg=-2.5),
                        dict(rate_type="per_crate_tier", tier_rates_json="{}"),
                        dict(rate_type="per_crate_tier", tier_rates_json="not json"),
                        dict(rate_type="per_crate_tier", tier_rates_json='{"1": 0, "2": 3.5}')):
                try:
                    create_rate_setting(RateSettingIn(effective_date=today, **bad), s)
                    raise AssertionError(f"a wage rate of {bad} was saved")
                except HTTPException as e:
                    assert e.status_code == 400
            create_rate_setting(RateSettingIn(effective_date=today, default_rate_per_kg=3.5), s)
            create_rate_setting(RateSettingIn(effective_date=today, rate_type="per_crate_tier",
                                              tier_rates_json='{"1": 2.5, "1.5": 3.5}'), s)
            assert len(s.exec(select(RateSetting)).all()) == 2, "a good rate was refused"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Markets & Transport
#
# What a market paid is money the farm reports on: the sums from a typed-in
# statement must come out the same as the paper ones. A throwaway boord.db
# and a throwaway packhouse.db with one delivery note.
# ---------------------------------------------------------------------------
def test_a_market_statement_sums_to_what_the_market_paid():
    import json  # noqa: E402
    from routers import reports  # noqa: E402

    tmp = tempfile.mkdtemp()
    saved_path = reports.PACKHOUSE_DB_PATH
    try:
        ph = sqlite3.connect(os.path.join(tmp, "packhouse.db"))
        ph.execute("CREATE TABLE deliverynote (uuid TEXT, note_no INTEGER, receiver TEXT, transporter TEXT, "
                   "loaded_on TEXT, pallets TEXT, removed INTEGER)")
        pallet = {"market_name": "Local", "lines": [{"packaging_name": "Pre-pac", "carton_kg": 2.0, "cartons": 10},
                                                    {"packaging_name": "4.5kg XL", "carton_kg": 4.5, "cartons": 4}]}
        ph.execute("INSERT INTO deliverynote VALUES ('n1', 7, 'Dapper\nJoburg', 'HFR', '2026-10-20', ?, 0)",
                   (json.dumps([pallet, pallet]),))
        ph.commit()
        ph.close()
        reports.PACKHOUSE_DB_PATH = os.path.join(tmp, "packhouse.db")

        eng = create_engine(f"sqlite:///{os.path.join(tmp, 't.db')}")
        run_migrations(eng, snapshot=False)
        with Session(eng) as s:
            day = datetime(2026, 10, 20).date()
            note = reports._market_notes(day, day, s)[0]
            assert (note["receiver"], note["market"], note["pallets"], note["sent"]) == ("Dapper", "Local", 2, 28)
            assert not note["entered"] and note["net"] == 0

            reports.save_market_statement("n1", reports.MarketStatementIn(
                lines=[{"pack": "Pre-pac", "sold": 18, "price": 100}, {"pack": "4.5kg XL", "sold": None, "price": 300}],
                commission=150, deductions=20, bank_cost=30, transport_cost=1000), s)
            note = reports._market_notes(day, day, s)[0]
            # 18 x R100 sold; the 4.5kg line has a price but no sales yet.
            assert note["gross"] == 1800 and note["net"] == 1600 and note["sold"] == 18, note
            assert note["kg_sold"] == 36 and note["kg_sent"] == 76, note
            assert reports._money(note)[5:] == [88.89, 44.44], "net R/carton and R/kg"
    finally:
        reports.PACKHOUSE_DB_PATH = saved_path
        shutil.rmtree(tmp, ignore_errors=True)


# An invoice as pypdf's layout mode reads a Johannesburg market pps_invoice:
# the same labels and spacing, made-up figures. {paid} and {nett} let a test
# make a part-payment or break the arithmetic.
_INVOICE = """   DAPPER AGENCIES (PTY) LTD
BEKFONTEIN BOERDERY (EDMS) BPK (25142)
   TAX INVOICE
   {invoice}
DATE: 21-OCT-26   PRODUCER D/NOTE: 7
{payment}   ARRIVE DATE: 20/OCT/2026 01:33
GRN NO: 15708492   SALESPERSON: SOMEONE (433)
PRODUCT: LICH, BX020, THREE MONTH RED, CL 1, XL, *, *   AGENT COMMISSION: 7.5%
QTY DESTR:   0   QTY D/GRADED:   0   QTY PAID/SOLD:   -{paid}   QTY C/F:   0
MKT COMM EXCL:   50.00   AGT COMM EXCL:   75.00   COMM VAT:   18.75   GRN INCOME:   1,000.00
GRN NO: 15708500   SALESPERSON: SOMEONE (433)
PRODUCT: LICH, PTB005, THREE MONTH RED, CL 1, *, 4, *   AGENT COMMISSION: 7.5%
QTY DESTR:   0   QTY D/GRADED:   0   QTY PAID/SOLD:   -2   QTY C/F:   0
MKT COMM EXCL:   55.00   AGT COMM EXCL:   82.50   COMM VAT:   20.63   GRN INCOME:   1,100.00
DEDUCTION DETAILS   AMOUNT   VAT   TOTAL
Market Commission   -105.00   -15.75   -120.75
Agent Commission   -157.50   -23.63   -181.13
Bank Charges   -22.80   -3.42   -26.22
DEDUCTION TOTAL:   -285.30   -42.80   -328.10
   SUM TOTAL SALES:   2,100.00
   SUM TOTAL DEDUCTIONS:   -328.10
   NETT INCOME:   {nett}
 Printed On 23/10/2026 09:31   Batch Page pps_invoice | Version 1.31 of 2
"""


# Onderberg's juice payment advice as pypdf gives it, shaped on a real one:
# two loads, R4/kg paid twice, a 2% levy and another 0.3%.
_ONDERBERG = """Onderberg Verwerkingskooperasie Beperk
HEREWITH THE PAYMENT ADVICE FOR FRUIT DELIVERED:
NO   DATE   DELIVERED KG   LITCHI   TOTAL KG   ROTTEN   DELIVERY NOTE   R 4,000.00   R 4,000.00   TOTAL RAND
170677   03-Dec-25   2310   2310   2,310   0%   J-0008   R 9,240.00   R 9,240.00   R 18,480.00
170990   10-Dec-25   3816   3339   3,339   13%   1306   R 13,356.00   R 13,356.00   R {row2}
   5,649   5,649   R 22,596.00   R 22,596.00   R 45,192.00
DEDUCTIONS   QTY   RATE
SALGA Member Levy   45,192   (2% of fruit cost)   R 903.84
   R 135.58
VAT   R -
   R 1,039.42
PAYMENT 30 DAYS AFTER DELIVERY - 31 JANUARY 2026   R 21,556.58
"""


# Boere Trust's account sale as pypdf's layout mode reads it, shaped on BT405246:
# two GRNs, part payment, bank charges under the costs. {nett} breaks the sums.
_ACCOUNT_SALE = """AGENT VAT REGISTRATION NUMBER: 4250103472
                               TAX INVOICE
                                  BT405246       ACCOUNT SALES NO :     405246
PRODUCER : 097120 BEKFONTEIN BDY (LAUGHING WATERS) DATE : 21/10/2026  Page:  1
           MALALANE                              DELIVERY NOTE NO :     205350
           1320                                             REFNO :       1324
Vat Reg No : 4020133783                             DATE RECEIVED : 16/10/2026
MARKET GRN : 20535001   QUANTITY RECEIVED :   33    QUANTITY B/F :     33
PRODUCT : LITM    NJ10     T2 LITCHI THIRD MONTH RED     SMAN : FRANCO / MARK
DATE        PRICES           AVER.PRICE  MARKET AVG      QUANTITY        VALUE
----          ------         ----------  ----------      --------     --------
19/10/2026 550.00 - 550.00       550.00      550.00              33     18150.00
                             ----------                  --------     --------
                                 550.00                        33     18150.00
QUANTITY OUTSTANDING :    0          AGENT COMM % : 7.50
MARKET GRN : 20535002   QUANTITY RECEIVED :   120    QUANTITY B/F :     93
PRODUCT : LITM    B220     T2 LITCHI THIRD MONTH RED     SMAN : FRANCO / MARK
----          ------         ----------  ----------      --------     --------
19/10/2026 180.00 - 180.00       180.00      180.00               2       360.00
20/10/2026 180.00 - 180.00       180.00      180.00               4       720.00
                             ----------                  --------     --------
                                 180.00                         6      1080.00
QUANTITY OUTSTANDING :   87          AGENT COMM % : 7.50
               **  PART PAYMENT  **        ** TOTAL SOLD **    39
DEDUCTION              AMOUNT  VAT (INPUT)    TOTAL
MARKET FEES            961.50      144.23    1105.73  GROSS AMOUNT   19230.00
SMS DEDUCTION FE        00.39       00.06      00.45  LESS COST       2764.78
AGENT COMMISSION      1442.25      216.35    1658.60
                   ----------  ----------   --------  SUB TOTAL      16465.22
                      2404.14      360.64    2764.78
BANK CHARGES            11.57        1.74      13.31  BANK CHARGES      13.31
                      2415.71      362.38    2778.09              -----------
                   ----------  ----------   --------  NETT AMOUNT    {nett}
TOTAL SOLD      :    66
"""


def test_a_boere_trust_account_sale_is_read_and_added_up():
    import market_mail  # noqa: E402
    inv = market_mail.parse_account_sale(_ACCOUNT_SALE.format(nett="16451.91"))
    assert (inv["invoice_no"], inv["note_no"], inv["payment"]) == ("BT405246", "1324", "Part Payment"), inv
    assert inv["agent"] == "Boere Trust" and str(inv["invoice_date"]) == "2026-10-21", inv
    assert (inv["commission"], inv["deductions"], inv["bank_cost"]) == (1658.6, 1106.18, 13.31), inv
    assert [(ln["product"], ln["sold"]) for ln in inv["lines"]] == [
        ("LITM NJ10 T2 LITCHI THIRD MONTH RED", 33), ("LITM B220 T2 LITCHI THIRD MONTH RED", 6)], inv["lines"]
    try:
        market_mail.parse_account_sale(_ACCOUNT_SALE.format(nett="16541.91"))
        raise AssertionError("an account sale that doesn't add up was read")
    except ValueError:
        pass


def test_one_bad_email_does_not_stop_the_inbox():
    """One email the handler cannot cope with (a zip Python can't open, a
    database error) must not end the pass: everything behind it in the inbox
    still gets read and labelled, the bad one is left unlabelled to be tried
    again, and the Reports tab is told which message it was. A fake Gmail
    with two messages; nothing here touches the network or the live
    database."""
    import market_mail  # noqa: E402
    from email.message import EmailMessage  # noqa: E402

    def raw(subject):
        m = EmailMessage()
        m["From"] = "sales@dapper.co.za"
        m["Subject"] = subject
        m.set_content("invoice attached")
        return m.as_bytes()

    class FakeImap:
        labelled = []

        def __init__(self, *args, **kwargs):
            pass

        def login(self, *args):
            pass

        def select(self, *args):
            pass

        def logout(self):
            pass

        def uid(self, cmd, *args):
            if cmd == "SEARCH":
                return "OK", [b"1 2"]
            if cmd == "FETCH":
                return "OK", [(b"", raw("boom" if args[0] == b"1" else "fine"))]
            FakeImap.labelled.append(args[0])  # STORE
            return "OK", []

    def handle(msg, cfg, session):
        if msg["Subject"] == "boom":
            raise RuntimeError("That compression method is not supported")
        return ["Skipped \"fine\" from sales@dapper.co.za: no PDF attached"]

    tmp = tempfile.mkdtemp()
    saved = (market_mail.imaplib.IMAP4_SSL, market_mail._handle_message, market_mail.read_config,
             market_mail.MAIL_DIR, market_mail.STATUS_PATH)
    try:
        market_mail.imaplib.IMAP4_SSL = FakeImap
        market_mail._handle_message = handle
        market_mail.read_config = lambda: {"address": "farm@example.com", "app_password": "x",
                                           "senders": ["dapper.co.za"]}
        market_mail.MAIL_DIR = tmp
        market_mail.STATUS_PATH = os.path.join(tmp, "status.json")
        status = market_mail.fetch_once()
        assert FakeImap.labelled == [b"2"], f"wrong messages labelled done: {FakeImap.labelled}"
        assert status["last_error"] == "", f"one bad email failed the whole pass: {status['last_error']}"
        assert any('Failed "boom"' in line and "RuntimeError" in line for line in status["log"]), status["log"]
        assert any('Skipped "fine"' in line for line in status["log"]), "the message behind the bad one was not read"
        assert market_mail.read_status()["log"] == status["log"], "the status file was not written"
    finally:
        (market_mail.imaplib.IMAP4_SSL, market_mail._handle_message, market_mail.read_config,
         market_mail.MAIL_DIR, market_mail.STATUS_PATH) = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_onderbergs_juice_advice_is_one_invoice_per_load():
    import market_mail  # noqa: E402
    loads = market_mail.parse_onderberg(_ONDERBERG.format(row2="26,712.00"))
    assert [(i["invoice_no"], i["note_no"], i["payment"]) for i in loads] == [
        ("OB-170677", "J-0008", "Juice"), ("OB-170990", "J-1306", "Juice, 13% rotten")], loads
    assert [i["lines"][0]["sold"] for i in loads] == [2310, 3339], loads
    assert str(loads[1]["invoice_date"]) == "2025-12-10", loads
    # The levies shared by rand, every cent of them on some load.
    assert round(sum(i["deductions"] for i in loads), 2) == 1039.42, loads
    assert round(sum(i["net"] for i in loads), 2) == 44152.58, loads
    try:
        market_mail.parse_onderberg(_ONDERBERG.format(row2="26,713.00"))
        raise AssertionError("an advice that doesn't add up was read")
    except ValueError:
        pass


def test_an_emailed_market_invoice_is_read_checked_and_added_up():
    import json  # noqa: E402
    import market_mail  # noqa: E402
    from routers import reports  # noqa: E402

    def invoice(no, payment="Final Payment", paid=10, nett="1,771.90"):
        return _INVOICE.format(invoice=no, payment=payment, paid=paid, nett=nett)

    inv = market_mail.parse_invoice(invoice("1/1", "Partial Payment #1"))
    assert (inv["invoice_no"], inv["note_no"], inv["payment"]) == ("1/1", "7", "Partial Payment #1"), inv
    assert inv["agent"] == "DAPPER AGENCIES (PTY) LTD" and str(inv["invoice_date"]) == "2026-10-21", inv
    assert (inv["commission"], inv["bank_cost"], inv["gross"], inv["net"]) == (301.88, 26.22, 2100.0, 1771.9), inv
    assert [ln["sold"] for ln in inv["lines"]] == [10, 2], inv["lines"]
    try:
        market_mail.parse_invoice(invoice("1/2", nett="1,871.90"))
        raise AssertionError("an invoice that doesn't add up was read")
    except ValueError:
        pass

    tmp = tempfile.mkdtemp()
    saved_path = reports.PACKHOUSE_DB_PATH
    try:
        ph = sqlite3.connect(os.path.join(tmp, "packhouse.db"))
        ph.execute("CREATE TABLE deliverynote (uuid TEXT, note_no INTEGER, receiver TEXT, transporter TEXT, "
                   "loaded_on TEXT, pallets TEXT, removed INTEGER, bins TEXT DEFAULT '[]')")
        pallet = {"market_name": "Local", "lines": [{"packaging_name": "2kg XL", "carton_kg": 2.0, "cartons": 30}]}
        ph.execute("INSERT INTO deliverynote VALUES ('n7', 7, 'Dapper', 'HFR', '2026-10-20', ?, 0, '[]')",
                   (json.dumps([pallet]),))
        # A juice note: bins and crates, no pallets.
        ph.execute("INSERT INTO deliverynote VALUES ('j8', 8, 'Onderberg', 'Self', '2026-10-20', '[]', 0, ?)",
                   (json.dumps([{"container": "bin", "bin_no": 88, "empty_kg": 37, "full_kg": 503},
                                {"bin_no": 89, "empty_kg": 33, "full_kg": 529},
                                {"container": "crate", "bin_no": 1, "empty_kg": 2, "full_kg": 20}]),))
        ph.commit()
        ph.close()
        reports.PACKHOUSE_DB_PATH = os.path.join(tmp, "packhouse.db")

        eng = create_engine(f"sqlite:///{os.path.join(tmp, 't.db')}")
        run_migrations(eng, snapshot=False)
        with Session(eng) as s:
            assert market_mail.save_invoice(inv, s)
            assert not market_mail.save_invoice(inv, s), "the same invoice was filed twice"
            market_mail.save_invoice(market_mail.parse_invoice(invoice("1/2")), s)
            for load in market_mail.parse_onderberg(_ONDERBERG.format(row2="26,712.00")):
                market_mail.save_invoice(load, s)
            reports.save_market_product(reports.MarketProductIn(
                product="LICH, BX020, THREE MONTH RED, CL 1, XL, *, *", pack="2kg XL"), s)
            day = datetime(2026, 10, 20).date()
            note, juice = reports._market_notes(day, day, s)
            assert (note["market"], juice["market"]) == ("Local", "Juice"), (note, juice)
            # Two part-payments, one product matched to the pack on the
            # pallet and one not: it still counts, on a line of its own.
            assert (note["gross"], note["net"], note["commission"]) == (4200.0, 3543.8, 603.76), note
            xl = [pk for pk in note["packs"] if pk["pack"] == "2kg XL"][0]
            assert (xl["sent"], xl["sold"], xl["price"]) == (30, 20, 100.0), xl
            assert any(pk.get("unmapped") and pk["sold"] == 4 for pk in note["packs"]), note["packs"]
            assert len(note["invoices"]) == 2
            # Juice note 8 takes Onderberg's J-0008 load, not fruit note 8's.
            assert ([i["invoice_no"] for i in juice["invoices"]], juice["gross"]) == (["OB-170677"], 18480.0), juice
            # Its bins and crates are its lines, as cartons are a fruit note's.
            sent = {pk["pack"]: (pk["sent"], pk["sent"] * pk["carton_kg"]) for pk in juice["packs"]}
            assert (sent["Bins"], sent["Crates"]) == ((2, 962), (1, 18)), sent
    finally:
        reports.PACKHOUSE_DB_PATH = saved_path
        shutil.rmtree(tmp, ignore_errors=True)


def _repo_file(relative):
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), relative)


def _shell_entries(worker_path):
    """The SHELL array's paths, read out of the worker's source."""
    source = open(_repo_file(worker_path)).read()
    start = source.index("const SHELL = [")
    body = source[start:source.index("];", start)]
    return re.findall(r'"([^"]+)"', body)


def test_every_shell_file_a_worker_lists_actually_exists():
    for worker in SERVICE_WORKERS:
        base = os.path.dirname(_repo_file(worker))
        for entry in _shell_entries(worker):
            resolved = os.path.normpath(os.path.join(base, entry))
            if entry.endswith("/"):
                resolved = os.path.join(resolved, "index.html")
            assert os.path.exists(resolved), (
                f"{worker} caches {entry!r}, which is not on disk. Since v3.4 the "
                f"shell refreshes all-or-nothing, so one unfetchable entry pins "
                f"every device to its installed copy and no update ever lands.")


def test_the_shell_refreshes_all_or_nothing():
    """The property itself, asserted against the source: a shell file is never
    written back on its own. Restoring the per-file `cache.put` would bring
    back the blank screen without failing anything else. The caching logic
    lives once in shared/sw-core.js; each screen's worker only declares its
    cache name and shell list, then loads the core."""
    core = "frontend/shared/sw-core.js"
    source = open(_repo_file(core)).read()
    assert "refreshShell" in source, f"{core} lost its atomic shell refresh"
    assert "shellFileChanged" in source, f"{core} no longer checks whether the shell moved"
    for worker in SERVICE_WORKERS[:3]:
        source = open(_repo_file(worker)).read()
        assert 'importScripts("../shared/sw-core.js")' in source, f"{worker} does not load sw-core.js"
        assert "../shared/sw-core.js" in _shell_entries(worker), (
            f"{worker} does not cache sw-core.js, so it cannot start offline")


def test_every_screen_starts_through_the_boot_guard():
    """Boord.boot turns a failure during startup into a message and an Update
    button. Called directly, init() throwing paints nothing at all - which is
    the worst way to tell a picker in an orchard that anything is wrong."""
    for screen in SCREEN_ENTRY_POINTS:
        source = open(_repo_file(screen)).read()
        assert "Boord.boot(init)" in source, f"{screen} does not start through Boord.boot"
        assert not re.search(r"^init\(\);", source, re.M), (
            f"{screen} still calls init() directly somewhere - a throw there is a "
            f"blank screen")


def test_the_boot_guard_is_actually_in_the_shared_library():
    source = open(_repo_file("frontend/shared/api.js")).read()
    for name in ("boot(init)", "_bootFailed", "_reinstall"):
        assert name in source, f"shared/api.js is missing {name}"



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
               test_this_server_matches_the_models,
               test_a_locked_database_refuses_to_migrate):
        check(fn.__name__, fn)

    section("Version")
    for fn in (test_the_version_regex_still_matches_the_constant,
               test_the_declared_version_matches_the_checked_out_tag,
               test_version_info_survives_without_git,
               test_the_reported_alembic_head_is_the_real_head):
        check(fn.__name__, fn)

    section("Master data imports")
    for fn in (test_replacing_all_blocks_with_an_empty_file_is_refused,
               test_importing_blocks_without_a_supplier_column_keeps_the_supplier):
        check(fn.__name__, fn)

    section("Deleted crates")
    for fn in (test_a_deleted_crate_stays_deleted,
               test_the_empty_slip_cleanup_only_takes_emptied_slips,
               test_a_slip_sends_fewer_crates_than_were_picked_and_shows_its_blocks):
        check(fn.__name__, fn)

    section("Receiving and wages")
    for fn in (test_a_replayed_dispatch_cannot_undo_receiving,
               test_rerunning_wages_takes_back_what_a_correction_took_away,
               test_a_zero_wage_rate_is_refused):
        check(fn.__name__, fn)

    section("Markets & Transport")
    for fn in (test_a_market_statement_sums_to_what_the_market_paid,
               test_an_emailed_market_invoice_is_read_checked_and_added_up,
               test_onderbergs_juice_advice_is_one_invoice_per_load,
               test_a_boere_trust_account_sale_is_read_and_added_up,
               test_one_bad_email_does_not_stop_the_inbox):
        check(fn.__name__, fn)

    section("Backups")
    for fn in (test_no_destination_configured_is_not_a_failure,
               test_a_missing_destination_never_raises,
               test_the_offsite_copy_is_byte_identical,
               test_a_destination_inside_the_repo_is_refused):
        check(fn.__name__, fn)

    section("Frontend app shell")
    for fn in (test_every_shell_file_a_worker_lists_actually_exists,
               test_the_shell_refreshes_all_or_nothing,
               test_every_screen_starts_through_the_boot_guard,
               test_the_boot_guard_is_actually_in_the_shared_library):
        check(fn.__name__, fn)

    section("Admin access")
    for fn in (test_every_admin_only_endpoint_is_still_guarded,
               test_nothing_new_slipped_behind_the_admin_guard,
               test_the_farm_wifi_cannot_reach_the_admin_app,
               test_a_forged_host_header_is_not_enough,
               test_the_tailnet_can_reach_it,
               test_loopback_alone_is_not_the_admin_any_more,
               test_loopback_carrying_a_tailnet_host_still_gets_in,
               test_the_dev_escape_hatch_is_opt_in_and_off_by_default,
               test_a_request_with_no_peer_is_not_the_admin):
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
