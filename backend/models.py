"""SQLModel schema for the Boord harvest + receiving system.

This is the trimmed "Lite" schema: field harvest, pack house receiving,
master data, and wage payments only. No sulfur/acid/grading/pallet/carton/
pre-pack-punnet/order/sales tables - see the full app for those.
"""
from datetime import datetime, date
from enum import Enum
from typing import Optional

from sqlmodel import SQLModel, Field


class DeviceRole(str, Enum):
    field = "field"
    packhouse = "packhouse"
    admin = "admin"


class LotStatus(str, Enum):
    created = "created"
    in_transit = "in_transit"
    received = "received"
    processing_complete = "processing_complete"


class RateType(str, Enum):
    per_kg = "per_kg"
    per_crate_tier = "per_crate_tier"


# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------

class Team(SQLModel, table=True):
    id: str = Field(primary_key=True)  # e.g. "A" (Span A)
    name: str
    induna: str = ""
    active: bool = True


class Block(SQLModel, table=True):
    id: str = Field(primary_key=True)  # real farm block label, e.g. "15", "8a"
    name: str = ""
    variety: str = ""
    trees: int = 0
    hectares: float = 0.0
    active: bool = True


class Worker(SQLModel, table=True):
    id: str = Field(primary_key=True)  # employee number, e.g. "001"
    first_name: str = ""
    last_name: str = ""
    name: str = ""  # display name = first_name + " " + last_name, kept for reports compat
    id_number: str = ""
    bank: str = ""
    account: str = ""
    team_id: Optional[str] = Field(default=None, foreign_key="team.id")  # kept for compat; not used in UI
    whatsapp_number: str = ""
    supplier_id: Optional[int] = Field(default=None, foreign_key="supplier.id")  # which farm/supplier this worker belongs to
    photo_filename: str = ""  # basename under data/photos/, e.g. "001.jpg"; empty = no photo
    active: bool = True


class Device(SQLModel, table=True):
    id: str = Field(primary_key=True)  # e.g. "device-01"
    station: str = ""
    role: DeviceRole
    team_id: Optional[str] = Field(default=None, foreign_key="team.id")
    induna: str = ""
    data_capturer: str = ""
    active: bool = True
    last_seen: Optional[datetime] = None


class Supplier(SQLModel, table=True):
    """A fruit source delivering into the pack house - either the farm's own
    fruit or another farmer's, kept separate everywhere downstream (lots,
    receiving, billing). Exactly one row should have is_own_farm=True,
    seeded once in db.seed_defaults()."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    contact_name: str = ""
    contact_phone: str = ""
    contact_email: str = ""
    is_own_farm: bool = False
    packing_rate_per_kg: float = 0.0  # facility-use fee charged to this supplier
    packing_rate_per_crate: float = 0.0  # used instead of per_kg if per_kg is 0
    active: bool = True


class AdminUser(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True)
    password_hash: str
    # True while the account still holds the password db.seed_defaults
    # generated for this install. Every admin endpoint refuses the token of a
    # user in that state - see security.get_current_admin - so the change is
    # forced rather than merely advised. Existing databases get the column
    # added as False by db._add_missing_columns, which is the right default:
    # those farms were told to replace ChangeMe123! long ago, and locking
    # them out of their own data on an upgrade would be the worse failure.
    must_change_password: bool = False


class SystemSetting(SQLModel, table=True):
    """Single-row table of farm-wide settings. Served to every device
    (including unauthenticated Field/Pack House ones) via a public
    GET /api/system-settings - never put anything secret on this model."""
    id: Optional[int] = Field(default=None, primary_key=True)
    farm_name: str = ""
    farm_location: str = ""
    green_to_yellow_minutes: int = 90
    yellow_to_red_minutes: int = 150
    current_harvest_year: int = datetime.now().year
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None


class OwnerViewToken(SQLModel, table=True):
    """Single-row secret token gating the read-only Owner View dashboard
    (see routers/owner_view.py) - deliberately kept off SystemSetting,
    which is public."""
    id: Optional[int] = Field(default=None, primary_key=True)
    token: str


# ---------------------------------------------------------------------------
# Transactional data: harvest -> transport -> receiving -> pay
# ---------------------------------------------------------------------------

class Lot(SQLModel, table=True):
    """A picking slip / transport lot of crates - the main unit the pack
    house works with, whether dispatched from one of the farm's own field
    devices or logged manually at receiving for another farmer's fruit."""
    id: Optional[int] = Field(default=None, primary_key=True)
    slip_number: str = Field(unique=True)  # e.g. "260701-001"
    timestamp: datetime  # dispatch time - basis for urgency sorting
    device_id: Optional[str] = Field(default=None, foreign_key="device.id")
    team_id: Optional[str] = Field(default=None, foreign_key="team.id")
    supplier_id: Optional[int] = Field(default=None, foreign_key="supplier.id")
    driver: str = ""
    total_crates: int = 0
    total_kg: float = 0.0
    status: LotStatus = LotStatus.created
    notes: str = ""
    received_at: Optional[datetime] = None
    weather_temp: Optional[float] = None
    weather_humidity: Optional[float] = None
    weather_condition: str = ""
    # Set when this lot was carved out of an earlier lot via a split
    # (routers/lots.py split_lot) - the ORIGINAL lot's slip_number, so receiving
    # staff can tell this pickup was part of a multi-load session and the rest
    # may arrive (or has already arrived) separately.
    split_from_slip_number: Optional[str] = None


class HarvestRecord(SQLModel, table=True):
    """One crate, captured in the field. Primary key is client-generated so
    repeated sync POSTs from an offline device are safe to retry (idempotent
    upsert by uuid)."""
    uuid: str = Field(primary_key=True)
    timestamp: datetime
    worker_id: Optional[str] = Field(default=None, foreign_key="worker.id")
    block_id: Optional[str] = Field(default=None, foreign_key="block.id")
    weight_kg: float
    deduction_kg: float = 0.0  # aftrekkings - waste/rejects deducted at capture
    device_id: Optional[str] = Field(default=None, foreign_key="device.id")
    team_id: Optional[str] = Field(default=None, foreign_key="team.id")
    lot_id: Optional[int] = Field(default=None, foreign_key="lot.id")
    notes: str = ""
    synced_at: Optional[datetime] = None  # set by server on first insert
    # Conditions at the farm when the crate checked in, stamped once on first
    # insert alongside synced_at (see routers/sync.py). Null when the farm has
    # no GPS set in Settings, or when the weather service couldn't be reached.
    weather_temp: Optional[float] = None
    weather_humidity: Optional[float] = None
    weather_condition: str = ""
    # Set only by an admin correction (routers/harvest_records.py), never by
    # the field app. Lets a re-synced record know an admin's numbers outrank
    # whatever the device still has queued - see the preservation logic in
    # routers/sync.py's upsert branch.
    edited_at: Optional[datetime] = None
    edited_by: Optional[str] = None  # AdminUser.username


class ReceivingRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    lot_id: int = Field(foreign_key="lot.id")
    timestamp: datetime
    expected_crates: int = 0
    actual_crates: int = 0
    discrepancy: int = 0
    condition: str = "Good"  # Good / Damaged / Sunburn / Wet / Other (free text, comma-joined if multiple)
    waste_kg: float = 0.0
    notes: str = ""
    received_by: str = ""


class RateSetting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    effective_date: date
    rate_type: RateType = RateType.per_kg
    default_rate_per_kg: float = 0.0
    tier_rates_json: str = "{}"  # e.g. {"1": 2.5, "1.5": 3.5, "2": 4.5}


class Payment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    worker_id: str = Field(foreign_key="worker.id")
    period_start: date
    period_end: date
    total_kg: float = 0.0
    rate_applied: float = 0.0
    amount_due: float = 0.0


# ---------------------------------------------------------------------------
# Pre-pack pull at receiving (candidate XXL/XL crates set aside for a
# separate pre-pack line, tracked here for audit purposes only - there's no
# grading station or punnet packing in this app, so it's just a record of
# what was pulled aside and by whom).
# ---------------------------------------------------------------------------

class PrePackRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    lot_id: int = Field(foreign_key="lot.id")
    timestamp: datetime
    crates: int = 0
    dominant_block_id: Optional[str] = Field(default=None, foreign_key="block.id")
    operator: str = ""
    notes: str = ""


# ---------------------------------------------------------------------------
# Historical (pre-app) harvest data, for the admin Analysis tab
# ---------------------------------------------------------------------------

class HistoricalHarvest(SQLModel, table=True):
    """Daily per-block kg from seasons before this app existed (2020-2025),
    imported once from the farm's own record spreadsheet - see
    scripts/import_historical_harvest.py for provenance, and that script's
    source workbook's own Notes sheet for the block-split-by-hectare-ratio
    and column-typo caveats behind a few of these rows. Never written to by
    the app itself; re-running the import script replaces the table wholesale."""
    id: Optional[int] = Field(default=None, primary_key=True)
    block_id: Optional[str] = Field(default=None, foreign_key="block.id")
    harvest_date: date
    season_year: int
    kg: float
    estimated: bool = False  # true where a combined historical block column was split by hectare ratio


class HistoricalAnnualYield(SQLModel, table=True):
    """ANNUAL kg totals for seasons even further back (1987-2019) than
    HistoricalHarvest's daily records - the farm's older bookkeeping only
    tracked totals per season, not per day, that far back. See
    scripts/import_historical_annual_yield.py for provenance. Two different
    grains, both from the same source workbook: 2012-2019 is PER-BLOCK
    (block_id set, same block-split-by-hectare-ratio caveat as
    HistoricalHarvest); 1987-2009 is a single WHOLE-FARM row per year
    (block_id NULL) - those years' own block numbering predates today's
    block register with no reliable mapping, so only the farm-wide total
    is kept. Reference-only: with no daily breakdown to align by
    season-day, and no weather data before 2020 to drive it, this doesn't
    feed the Analysis tab or Risk indicator - it only appears as an extra
    sheet on the Historical Harvest Data export. Never written to by the
    app itself; re-running the import script replaces the table wholesale."""
    id: Optional[int] = Field(default=None, primary_key=True)
    block_id: Optional[str] = Field(default=None, foreign_key="block.id")  # NULL = whole-farm total, no block breakdown
    season_year: int
    kg: float
    estimated: bool = False  # true where a combined historical block column was split by hectare ratio


# ---------------------------------------------------------------------------
# Historical weather backfill, for correlating conditions with harvest data
# ---------------------------------------------------------------------------

class WeatherHistory(SQLModel, table=True):
    """Hourly weather for the farm's location, back to 1987, pulled from
    two different Open-Meteo APIs depending on era - see backend/weather.py's
    fetch_historical_hourly() (2020 onward) / fetch_archive_hourly()
    (1987-2019 - soil_temp_6cm_c and uv_index are always NULL that far
    back, that API never carries them) and shared parse_hourly_rows().
    Filled by scripts/import_historical_weather.py (2020 onward) and
    scripts/import_historical_weather_archive.py (1987-2019) - each only
    replaces its own date range, so they compose safely in either order -
    and kept current day-to-day by weather.sync_recent_weather()
    (append-only, run as a side effect of opening the admin/owner Weather
    tab). Only 2020-2025 actually drives anything (the Risk indicator and
    Harvest Forecast are fixed to that reference range - see
    routers/risk.py); 1987-2019 is reference-only, for the Weather tab's
    own chart."""
    id: Optional[int] = Field(default=None, primary_key=True)
    # Unlike every other datetime column in this app (naive UTC - see
    # timeutil.py), this is naive LOCAL farm time: Open-Meteo was queried
    # with timezone=auto, so its "time" strings are already in the farm's
    # own timezone. Never pass this through timeutil.to_local() - use
    # timestamp.date() directly, or it'll be shifted a second time.
    timestamp: datetime = Field(index=True, unique=True)
    temp_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    dew_point_c: Optional[float] = None
    precipitation_mm: Optional[float] = None
    weather_code: Optional[int] = None
    condition: str = ""
    wind_speed_kmh: Optional[float] = None
    soil_temp_6cm_c: Optional[float] = None
    uv_index: Optional[float] = None
    sunshine_duration_s: Optional[float] = None
