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
    supplier_id: Optional[int] = Field(default=None, foreign_key="supplier.id")  # which supplier's orchard this block belongs to
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
    # Which supplier a field device is picking for. Lots dispatched from this
    # device are attributed to this supplier; None falls back to the own-fruit
    # supplier (see db.supplier_id_for_device). Only meaningful for role=field.
    supplier_id: Optional[int] = Field(default=None, foreign_key="supplier.id")
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
    puc: str = ""  # Product Unit Code (traceability - the grower's registered production unit)
    global_gap_number: str = ""  # GlobalG.A.P. Number (GGN) for this supplier
    packing_rate_per_kg: float = 0.0  # facility-use fee charged to this supplier
    packing_rate_per_crate: float = 0.0  # used instead of per_kg if per_kg is 0
    active: bool = True


class SystemSetting(SQLModel, table=True):
    """Single-row table of pack-house-wide settings. Served to every device
    on the farm Wi-Fi (Field and Receiving tablets included) via a public
    GET /api/system-settings - never put anything secret on this model."""
    id: Optional[int] = Field(default=None, primary_key=True)
    packhouse_name: str = ""
    packhouse_location: str = ""
    packhouse_code: str = ""  # PHC - the pack house's registered code
    green_to_yellow_minutes: int = 90
    yellow_to_red_minutes: int = 150
    # The season is a recurring anchor (month + day). The app derives which
    # season is current and labels it by the year it starts in;
    # current_harvest_year is kept as that derived label for report headers
    # and older readers. Default 1 January reproduces the old calendar-year
    # behaviour.
    season_start_month: int = 1
    season_start_day: int = 1
    current_harvest_year: int = datetime.now().year
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None


class SetupState(SQLModel, table=True):
    """Single-row record of how far the first-run wizard got - see
    routers/setup.py.

    Deliberately its own table rather than two more columns on SystemSetting:
    SystemSetting is served publicly to every device on the farm Wi-Fi. It
    also gets round-tripped whole by
    PUT /api/system-settings, which builds a fresh row from the request body
    and merges it - so a field the Settings form does not know about is
    silently blanked every time somebody presses Save. A completion marker
    that erases itself on the farm's next settings change would put an
    established farm back at step 1.

    started_at is stamped the first time the wizard is shown, and it is what
    makes the wizard resumable. Without it the wizard was offered on the
    strength of a blank packhouse_name - which its own first step then fills in,
    so closing the tab after step 1 dropped the admin into a half-configured
    app with the remaining steps unreachable.

    A database that predates this table has no row at all, which must never
    on its own mean "not set up yet": see build_setup_state() for the two
    further signs of an already-working farm that have to agree before the
    wizard is offered.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


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
    edited_by: Optional[str] = None  # always "admin" - see routers/harvest_records


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
