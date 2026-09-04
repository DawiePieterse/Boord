import os
from typing import Optional

from sqlalchemy import inspect, text
from sqlmodel import SQLModel, Session, create_engine, select

from models import Device, DeviceRole, Supplier, SystemSetting, Team

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "boord.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def _model_default_literal(table: str, column: str):
    """The default models.py declares for a column, written as a SQL literal,
    or None if it does not declare a usable one.

    The source database cannot supply this. A SQLModel default lives in
    Python - `is_own_farm: bool = False` reaches SQLite as a plain NOT NULL
    column with no DEFAULT clause - so a column copied from there into an
    existing table would have to be added nullable, and the farm would sit
    one nullable column away from models.py with _report_drift() saying so on
    every boot for the rest of its life.

    This decides HOW to add a column, never WHETHER to: it is only ever
    consulted for a column the source database already has.
    """
    model_table = SQLModel.metadata.tables.get(table)
    if model_table is None or column not in model_table.columns:
        return None
    default = model_table.columns[column].default
    arg = getattr(default, "arg", None) if default is not None else None
    if arg is None or callable(arg):
        return None
    return "'{}'".format(str(arg).replace("'", "''")) if isinstance(arg, str) else str(arg)


def _add_column_ddl(table: str, column: dict) -> str:
    """ADD COLUMN clause for one column of a source table's PRAGMA table_info.

    NOT NULL is dropped when there is no default to backfill existing rows
    with - SQLite will not take the column otherwise. A fresh install still
    gets the strict schema, because it is built by the migrations rather
    than by this; that difference is what _report_drift() means by harmless
    old damage.
    """
    ddl = f'"{column["name"]}" {column["type"]}'
    if not column["notnull"]:
        return ddl
    # dflt_value comes out of SQLite already written as a SQL literal.
    default = column["dflt_value"]
    if default is None:
        default = _model_default_literal(table, column["name"])
    return ddl if default is None else f"{ddl} NOT NULL DEFAULT {default}"


def _sqlite_schema(conn) -> tuple:
    """({table: CREATE TABLE sql}, {table: [CREATE INDEX sql]}) for a database.

    Read from sqlite_master rather than reflected into SQLAlchemy types and
    rendered back out. The point of this function's caller is to reproduce
    another database's schema exactly, and a round trip through reflection
    is where "exactly" quietly turns into "near enough" - an Enum comes back
    as a plain VARCHAR, a server default as a text clause, and the result
    differs from what the migrations build in ways nobody notices until the
    drift report starts complaining on one farm and not another.
    """
    tables, indexes = {}, {}
    rows = conn.execute(text(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
    )).all()
    for kind, name, tbl_name, sql in rows:
        if kind == "table" and name != "alembic_version":
            tables[name] = sql
        elif kind == "index":
            indexes.setdefault(tbl_name, []).append(sql)
    return tables, indexes


def legacy_schema_catch_up(target_engine, source_engine) -> None:
    """The pre-Alembic schema maintenance, kept for exactly one purpose.

    This was how every Boord database was built and upgraded before
    migrations existed: whole tables created outright, then a strictly
    additive ADD COLUMN pass for fields added to existing ones. It could not
    rename, retype, drop or backfill anything, which is why it was replaced.

    It survives because it is the only thing that can bring a database from
    *any* pre-Alembic release up to the baseline revision, whichever release
    that farm last ran. migrate._adopt_existing_database() calls it once,
    then stamps the database and never calls it again. Nothing else should
    call it: a schema change made this way is invisible to Alembic, and the
    next migration to touch that table will be reasoning about a schema that
    is not what it thinks.

    `source_engine` is the schema being caught up TO - a throwaway database
    built by the baseline migration itself (migrate._baseline_database()).
    It used to be models.py, and that was fine for exactly as long as the
    baseline and models.py described the same schema. The first migration
    to add a column ended that: the catch-up would build a table at today's
    shape, the database would then be stamped at the baseline, and that
    migration would try to add a column that was already there. The farms
    it would have failed on are precisely the ones this function exists for
    - the ones several releases behind, updating in front of somebody.
    """
    with source_engine.connect() as source, target_engine.begin() as target:
        source_tables, source_indexes = _sqlite_schema(source)
        live = set(inspect(target_engine).get_table_names())

        for name, create_sql in source_tables.items():
            if name in live:
                continue
            target.execute(text(create_sql))
            for index_sql in source_indexes.get(name, []):
                target.execute(text(index_sql))
            print(f"[migration] created table {name}")

        for name in source_tables:
            if name not in live:
                continue  # just built it, columns and indexes and all
            present = {c["name"] for c in inspect(target_engine).get_columns(name)}
            wanted = source.execute(text(f'PRAGMA table_info("{name}")')).mappings().all()
            for column in wanted:
                if column["name"] in present:
                    continue
                target.execute(text(
                    f'ALTER TABLE "{name}" ADD COLUMN {_add_column_ddl(name, column)}'))
                print(f"[migration] {name}: added column {column['name']}")


def get_session():
    with Session(engine) as session:
        yield session


def get_own_supplier_id(session: Session):
    """The supplier row representing the pack house's own fruit - the
    fallback for a field lot whose device is not allocated to a supplier."""
    own = session.exec(select(Supplier).where(Supplier.is_own_farm == True)).first()  # noqa: E712
    return own.id if own else None


def supplier_id_for_device(session: Session, device_id) -> Optional[int]:
    """Which supplier a field lot from this device belongs to: the device's
    own allocation if it has one, otherwise the pack house's own fruit. A
    missing or unknown device_id also falls back to own fruit."""
    device = session.get(Device, device_id) if device_id else None
    if device is not None and device.supplier_id is not None:
        return device.supplier_id
    return get_own_supplier_id(session)


def seed_defaults() -> None:
    with Session(engine) as session:
        if not session.exec(select(Team)).first():
            session.add(Team(id="A", name="Span A", induna=""))
            session.add(Team(id="B", name="Span B", induna=""))

        # Blocks are deliberately NOT seeded. This used to create 21 real
        # blocks - one farm's actual orchard, with its varieties, tree counts
        # and hectares - in every new database. Nobody else's orchard looks
        # like that, so for any other pack house it was both wrong and
        # somebody else's business data. A new install starts with none and
        # imports its own via Admin -> Settings -> Master Data -> Blocks
        # (templates/blocks.csv has the column headings);
        # routers/master_data.py:import_blocks already handles csv and xlsx.

        if not session.exec(select(Device)).first():
            for i in range(1, 6):
                team_id = "A" if i <= 3 else "B"
                session.add(Device(id=f"device-0{i}", station=f"Field Station {i}", role=DeviceRole.field,
                                    team_id=team_id))
            session.add(Device(id="device-06", station="Packhouse Receiving 1", role=DeviceRole.packhouse))
            session.add(Device(id="device-07", station="Packhouse Receiving 2", role=DeviceRole.packhouse))
            session.add(Device(id="admin-pc", station="Pack house office", role=DeviceRole.admin))

        # No wage rate is seeded either. This used to default to R3.00/kg -
        # one farm's actual rate. A wrong block list is obvious the moment
        # somebody opens the Field app; a wrong wage rate produces a
        # perfectly plausible payslip at the wrong number, which is the kind
        # of mistake that is only found by the person being underpaid. With
        # no rate row, routers/payments.py refuses to calculate wages at all
        # until one is entered under Admin -> Settings.

        if not session.exec(select(SystemSetting)).first():
            session.add(SystemSetting())

        if not session.exec(select(Supplier).where(Supplier.is_own_farm == True)).first():  # noqa: E712
            session.add(Supplier(name="Own fruit", is_own_farm=True))

        # No admin account is seeded, because there is no longer one to seed.
        # A fresh install used to generate a password, print it, and leave a
        # copy in data/ for install.ps1 to read back; the Admin app is now
        # gated on the network a request arrives from instead - see
        # backend/security.py. Farms upgrading past this point get the
        # adminuser table dropped by migration 4c81f2a90d17.

        session.commit()
