"""Timezone utilities for SportsReminder.

DST-aware offset calculations for Israel and Berlin,
plus helper functions for display dates and Israel local time.
"""

import datetime
import calendar as _calendar

# DST-aware timezone support (zoneinfo is stdlib since Python 3.9)
try:
    from zoneinfo import ZoneInfo
    _ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
    _BERLIN_TZ  = ZoneInfo("Europe/Berlin")   # EuroLeague uses CET/CEST
    _HAS_ZONEINFO = True
except Exception:
    _HAS_ZONEINFO = False

__all__ = [
    "_last_weekday",
    "_israel_utc_offset_h",
    "_berlin_utc_offset_h",
    "_compute_display_date",
    "_HAS_ZONEINFO",
    "_ISRAEL_TZ",
    "_BERLIN_TZ",
    "today_israel",
    "now_israel_time",
]

def _last_weekday(year: int, month: int, weekday: int) -> int:
    """Return the day-of-month of the last occurrence of weekday (0=Mon..6=Sun) in month."""
    last = _calendar.monthrange(year, month)[1]
    return max(d for d in range(last, last - 7, -1)
               if datetime.date(year, month, d).weekday() == weekday)

def _israel_utc_offset_h(at_utc: datetime.datetime) -> int:
    """Israel's UTC offset at a given UTC moment: +3 (IDT, summer) or +2 (IST, winter).
    DST rule: starts last Friday of March 02:00 IL (= 00:00 UTC), ends last Sunday of Oct 01:00 UTC."""
    if _HAS_ZONEINFO:
        aware = at_utc.replace(tzinfo=datetime.timezone.utc).astimezone(_ISRAEL_TZ)
        return int(aware.utcoffset().total_seconds() // 3600)
    y = at_utc.year
    dst_start = datetime.datetime(y, 3, _last_weekday(y, 3, 4), 0, 0)   # Fri→00:00 UTC
    dst_end   = datetime.datetime(y, 10, _last_weekday(y, 10, 6), 1, 0)  # Sun→01:00 UTC
    return 3 if dst_start <= at_utc < dst_end else 2

def _berlin_utc_offset_h(at_utc: datetime.datetime) -> int:
    """Europe/Berlin UTC offset at a given UTC moment: +2 (CEST, summer) or +1 (CET, winter).
    CEST starts last Sunday of March 01:00 UTC, ends last Sunday of Oct 01:00 UTC."""
    if _HAS_ZONEINFO:
        aware = at_utc.replace(tzinfo=datetime.timezone.utc).astimezone(_BERLIN_TZ)
        return int(aware.utcoffset().total_seconds() // 3600)
    y = at_utc.year
    cest_start = datetime.datetime(y, 3, _last_weekday(y, 3, 6), 1, 0)   # Sun→01:00 UTC
    cest_end   = datetime.datetime(y, 10, _last_weekday(y, 10, 6), 1, 0)  # Sun→01:00 UTC
    return 2 if cest_start <= at_utc < cest_end else 1


def today_israel() -> str:
    """Return today's date in Israel as YYYY-MM-DD."""
    utc_now = datetime.datetime.utcnow()
    israel_now = utc_now + datetime.timedelta(hours=_israel_utc_offset_h(utc_now))
    return israel_now.strftime("%Y-%m-%d")

def now_israel_time() -> str:
    """Return current time in Israel as HH:MM."""
    utc_now = datetime.datetime.utcnow()
    israel_now = utc_now + datetime.timedelta(hours=_israel_utc_offset_h(utc_now))
    return israel_now.strftime("%H:%M")


def _compute_display_date(il_date: str, time_str: str) -> str:
    """Games between 00:00-04:59 Israel time belong to the previous evening.
    Returns il_date - 1 day for those games, otherwise il_date unchanged.
    This ensures e.g. a 00:00 IL game on June 23 displays under June 22."""
    if time_str == "TBD":
        return il_date
    try:
        h = int(time_str.split(":")[0])
        if 0 <= h < 5:
            dt = datetime.datetime.strptime(il_date, "%Y-%m-%d")
            return (dt - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        pass
    return il_date

