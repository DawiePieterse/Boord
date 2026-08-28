import os
import secrets

from passlib.context import CryptContext
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, Session, create_engine, select

from models import AdminUser, Device, DeviceRole, OwnerViewToken, Supplier, SystemSetting, Team

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "boord.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DEFAULT_ADMIN_USERNAME = "admin"

# Where the password generated for this install is left for whoever is
# standing at the machine. install.ps1 reads it back and prints it; it is
# deleted the moment the password is changed (routers/auth.change_password).
# data/ is gitignored, and backup.py only ever archives boord.db and photos/,
# so this file never leaves the server.
INITIAL_PASSWORD_FILE = os.path.join(DATA_DIR, "initial_admin_password.txt")

# No I, O, 0 or 1: this password gets read off one screen and typed into
# another, often a tablet, by someone who did not choose it.
_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_initial_password() -> str:
    """A random password for this install - three groups of four, ~60 bits.

    There used to be one shared password, `ChangeMe123!`, seeded into every
    database and printed in both the manual and the installer's output. On a
    single farm that is a note to self. Across twenty installs reachable over
    Tailscale it is a published password on every one of them, and it made
    the front door by far the softest part of a system whose update path is
    GPG-signed and fingerprint-pinned.
    """
    groups = ("".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(4)) for _ in range(3))
    return "-".join(groups)


def _write_initial_password(password: str) -> None:
    """Announce the generated password on the console and leave a copy on
    disk. Both, because the console here is usually a Scheduled Task's, which
    nobody ever sees, and because the installer needs somewhere to read it
    from after the server has started."""
    rule = "=" * 60
    print(f"\n{rule}\n"
          f" Boord created an admin account for this install:\n"
          f"     username: {DEFAULT_ADMIN_USERNAME}\n"
          f"     password: {password}\n"
          f" You will be asked to replace this password at first sign-in.\n"
          f"{rule}\n", flush=True)
    try:
        fd = os.open(INITIAL_PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(password + "\n")
    except OSError as e:
        print(f"[boord] could not write {INITIAL_PASSWORD_FILE} ({e!r}) - the password "
              f"printed above is now the only copy of it", flush=True)


def clear_initial_password_file() -> None:
    """Called once the admin has set their own password. Keeping the file
    after that point gains nothing and costs a plaintext password on disk."""
    try:
        os.remove(INITIAL_PASSWORD_FILE)
    except OSError:
        pass  # already gone, or never written - either way there is nothing to do


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


def legacy_schema_catch_up(target_engine=None) -> None:
    """The pre-Alembic schema maintenance, kept for exactly one purpose.

    This was how every Boord database was built and upgraded before
    migrations existed: create_all() for whole tables, then a strictly
    additive ADD COLUMN pass for fields added to existing ones. It could not
    rename, retype, drop or backfill anything, which is why it was replaced.

    It survives because it is the only thing that can bring a database from
    *any* pre-Alembic release up to the baseline revision, whichever release
    that farm last ran. migrate._adopt_existing_database() calls it once,
    then stamps the database and never calls it again. Nothing else should
    call it: a schema change made this way is invisible to Alembic, and the
    next migration to touch that table will be reasoning about a schema that
    is not what it thinks.
    """
    target_engine = target_engine or engine
    SQLModel.metadata.create_all(target_engine)

    inspector = inspect(target_engine)
    live_tables = set(inspector.get_table_names())
    for table in SQLModel.metadata.sorted_tables:
        if table.name not in live_tables:
            continue  # create_all() just built it, columns and all
        present = {c["name"] for c in inspector.get_columns(table.name)}
        missing = [c for c in table.columns if c.name not in present]
        for column in missing:
            with target_engine.begin() as conn:
                conn.execute(text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN '
                    f'{_column_ddl(column, target_engine.dialect)}'))
            print(f"[migration] {table.name}: added column {column.name}")


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

        # No wage rate is seeded either. This used to default to R3.00/kg -
        # one farm's actual rate. A wrong block list is obvious the moment
        # somebody opens the Field app; a wrong wage rate produces a
        # perfectly plausible payslip at the wrong number, which is the kind
        # of mistake that is only found by the person being underpaid. With
        # no rate row, routers/payments.py refuses to calculate wages at all
        # until one is entered under Admin -> Settings.

        if not session.exec(select(SystemSetting)).first():
            session.add(SystemSetting())

        if not session.exec(select(OwnerViewToken)).first():
            session.add(OwnerViewToken(token=secrets.token_urlsafe(24)))

        if not session.exec(select(Supplier).where(Supplier.is_own_farm == True)).first():  # noqa: E712
            session.add(Supplier(name="Own Farm", is_own_farm=True))

        if not session.exec(select(AdminUser)).first():
            initial_password = generate_initial_password()
            session.add(AdminUser(
                username=DEFAULT_ADMIN_USERNAME,
                password_hash=pwd_context.hash(initial_password),
                must_change_password=True,
            ))
            _write_initial_password(initial_password)

        session.commit()
