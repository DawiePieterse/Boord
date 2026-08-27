import os
import secrets
from datetime import date

from passlib.context import CryptContext
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, Session, create_engine, select

from models import (AdminUser, Device, DeviceRole, OwnerViewToken, RateSetting, RateType, Supplier,
                     SystemSetting, Team)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "boord.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "ChangeMe123!"  # must be changed on first login


def _column_ddl(column, dialect) -> str:
    """ADD COLUMN clause for a model column missing from a live table."""
    ddl = f'"{column.name}" {column.type.compile(dialect)}'
    if column.nullable:
        return ddl
    default = getattr(column.default, "arg", None) if column.default is not None else None
    if default is None or callable(default):
        # Nothing to backfill existing rows with, and SQLite won't accept a
        # NOT NULL column without a default. Adding it nullable keeps the farm
        # running; a fresh install still gets the strict schema from create_all.
        return ddl
    literal = "'{}'".format(str(default).replace("'", "''")) if isinstance(default, str) else str(default)
    return f"{ddl} NOT NULL DEFAULT {literal}"


def _add_missing_columns() -> None:
    """Bring an existing database up to the current models.

    create_all() only ever creates whole tables, so a server upgraded in place
    would keep its old columns and every query touching a new field would fail
    with "no such column". This adds them. Strictly additive - it never drops
    or alters an existing column, so downgrading is just running the old code.
    """
    inspector = inspect(engine)
    live_tables = set(inspector.get_table_names())
    for table in SQLModel.metadata.sorted_tables:
        if table.name not in live_tables:
            continue  # create_all() just built it, columns and all
        present = {c["name"] for c in inspector.get_columns(table.name)}
        missing = [c for c in table.columns if c.name not in present]
        for column in missing:
            with engine.begin() as conn:
                conn.execute(text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN {_column_ddl(column, engine.dialect)}'))
            print(f"[migration] {table.name}: added column {column.name}")


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def get_session():
    with Session(engine) as session:
        yield session


def get_own_supplier_id(session: Session):
    """The supplier row representing the farm's own fruit - every lot
    dispatched from a field device is auto-tagged with this id."""
    own = session.exec(select(Supplier).where(Supplier.is_own_farm == True)).first()  # noqa: E712
    return own.id if own else None


def seed_defaults() -> None:
    with Session(engine) as session:
        if not session.exec(select(Team)).first():
            session.add(Team(id="A", name="Span A", induna=""))
            session.add(Team(id="B", name="Span B", induna=""))

        # Blocks are deliberately NOT seeded. This used to create 21 real
        # blocks - one farm's actual orchard, with its varieties, tree counts
        # and hectares - in every new database. Nobody else's orchard looks
        # like that, so for any other farm it was both wrong and somebody
        # else's business data. A new install starts with none and imports its
        # own via Admin -> Master Data -> Blocks (templates/blocks.csv has the
        # column headings); routers/master_data.py:import_blocks already
        # handles csv and xlsx.

        if not session.exec(select(Device)).first():
            for i in range(1, 6):
                team_id = "A" if i <= 3 else "B"
                session.add(Device(id=f"device-0{i}", station=f"Field Station {i}", role=DeviceRole.field,
                                    team_id=team_id))
            session.add(Device(id="device-06", station="Packhouse Receiving 1", role=DeviceRole.packhouse))
            session.add(Device(id="device-07", station="Packhouse Receiving 2", role=DeviceRole.packhouse))
            session.add(Device(id="admin-pc", station="Pack house office", role=DeviceRole.admin))

        if not session.exec(select(RateSetting)).first():
            session.add(RateSetting(
                effective_date=date.today(),
                rate_type=RateType.per_kg,
                default_rate_per_kg=3.00,
                tier_rates_json='{"1": 2.5, "1.5": 3.5, "2": 4.5}',
            ))

        if not session.exec(select(SystemSetting)).first():
            session.add(SystemSetting())

        if not session.exec(select(OwnerViewToken)).first():
            session.add(OwnerViewToken(token=secrets.token_urlsafe(24)))

        if not session.exec(select(Supplier).where(Supplier.is_own_farm == True)).first():  # noqa: E712
            session.add(Supplier(name="Own Farm", is_own_farm=True))

        if not session.exec(select(AdminUser)).first():
            session.add(AdminUser(
                username=DEFAULT_ADMIN_USERNAME,
                password_hash=pwd_context.hash(DEFAULT_ADMIN_PASSWORD),
            ))

        session.commit()
