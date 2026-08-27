"""Timezone helpers.

Every timestamp is recorded in UTC and stored naive (SQLite drops the tzinfo),
but the people using the app think entirely in farm time - SAST on the server's
own clock. These helpers are the single place that bridges the two, so day
filters cover the day the farm actually worked and exported reports read in
local time rather than UTC.
"""
from datetime import date, datetime, time, timezone
from typing import Optional


def as_utc(dt: datetime) -> datetime:
    """Attach UTC to a timestamp that came out of the database naive."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_local(dt: Optional[datetime]) -> Optional[datetime]:
    """A stored timestamp expressed in the server's local timezone."""
    return as_utc(dt).astimezone() if dt is not None else None


def local_str(dt: Optional[datetime], fallback: str = "") -> str:
    """Local 'YYYY-MM-DD HH:MM' for reports and exports."""
    local = to_local(dt)
    return local.strftime("%Y-%m-%d %H:%M") if local else fallback


def day_bounds(start_day: date, end_day: Optional[date] = None) -> tuple:
    """The naive-UTC range matching a span of local calendar days.

    Combining a date with midnight directly would cut the day on UTC
    boundaries - 02:00 to 02:00 local in SAST - so anything picked before
    sunrise landed in the previous day's totals.
    """
    end_day = end_day if end_day is not None else start_day
    start = datetime.combine(start_day, time.min).astimezone()
    end = datetime.combine(end_day, time.max).astimezone()
    return (start.astimezone(timezone.utc).replace(tzinfo=None),
            end.astimezone(timezone.utc).replace(tzinfo=None))
