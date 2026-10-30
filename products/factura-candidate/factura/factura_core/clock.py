"""UTC storage, explicit merchant calendar (RD by default)."""
from datetime import date, datetime, time, timedelta, timezone
import os
from zoneinfo import ZoneInfo


def business_zone():
    return ZoneInfo(os.environ.get("FACTURA_TIMEZONE", "America/Santo_Domingo"))


def today_business():
    return datetime.now(business_zone()).date()


def local_datetime(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(business_zone())


def utc_start(day):
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return datetime.combine(day, time.min, business_zone()).astimezone(timezone.utc).isoformat()


def utc_after(day):
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return utc_start(day + timedelta(days=1))
