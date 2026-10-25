"""Naive UTC helpers. SQLite returns naive datetimes, so the app stores UTC without tzinfo."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def business_tz():
    from app.config import get_settings

    try:
        return ZoneInfo(get_settings().business_timezone)
    except Exception:
        return timezone.utc


def business_day_bounds(moment: datetime | None = None) -> tuple[datetime, datetime]:
    """Return [start, end) of the business-local calendar day, expressed as naive UTC."""
    moment = moment or utcnow()
    tz = business_tz()
    local = moment.replace(tzinfo=timezone.utc).astimezone(tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def within_business_hours(moment: datetime | None = None, start_hour: int = 9, end_hour: int = 18, days: str = "mon,tue,wed,thu,fri") -> bool:
    moment = moment or utcnow()
    tz = business_tz()
    local = moment.replace(tzinfo=timezone.utc).astimezone(tz)
    allowed = {part.strip().lower()[:3] for part in days.split(",") if part.strip()}
    weekday = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][local.weekday()]
    if weekday not in allowed:
        return False
    return start_hour <= local.hour < end_hour
