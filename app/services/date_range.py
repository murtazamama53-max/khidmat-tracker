"""
Resolves the "This month / Last month / This year / Custom range" selector
(blueprint section 21) into a concrete (start_date, end_date) pair.
"""
from datetime import date, datetime
from typing import Optional, Tuple
from zoneinfo import ZoneInfo


class InvalidRangeError(ValueError):
    pass


VALID_KEYS = ("this_month", "last_month", "this_year", "custom")


def app_today(tz_name: str) -> date:
    """
    "Today", in the app's configured operating timezone (config.TIMEZONE,
    "Asia/Karachi" by default) -- never the bare server-local date.

    date.today() uses the server's own local timezone, which is UTC on
    Vercel regardless of where the owner actually is. Pakistan is UTC+5,
    so any naive date.today() call is wrong for roughly the first 5 hours
    of every Pakistan day (it still reports "yesterday"), which is exactly
    what showed up as a stale date on the dashboard. Every route that
    needs "today" must go through this function instead of calling
    date.today() directly.
    """
    return datetime.now(ZoneInfo(tz_name)).date()


def resolve_range(
    key: str, today: date, custom_start: Optional[date] = None, custom_end: Optional[date] = None
) -> Tuple[date, date, str]:
    """Returns (start_date, end_date, label) for the given range key."""
    if key == "this_month":
        start = today.replace(day=1)
        return start, today, "This Month"

    if key == "last_month":
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month.fromordinal(first_of_this_month.toordinal() - 1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start, last_month_end, "Last Month"

    if key == "this_year":
        return date(today.year, 1, 1), today, "This Year"

    if key == "custom":
        if custom_start is None or custom_end is None:
            raise InvalidRangeError("Custom range requires both a start and end date.")
        if custom_end < custom_start:
            raise InvalidRangeError("End date cannot be before start date.")
        return custom_start, custom_end, "Custom Range"

    raise InvalidRangeError(f"Unknown range key: {key}")


def previous_period(key: str, start: date, today: date) -> Optional[Tuple[date, date, str]]:
    """
    The comparable prior period for a given range key, used only to compute
    'vs last period' trend deltas on the dashboard -- never used for any
    financial total itself. Returns None for custom ranges, where there is
    no unambiguous prior period to compare against (trend indicators are
    simply hidden in that case rather than guessing).
    """
    if key == "this_month":
        last_month_end = start.fromordinal(start.toordinal() - 1)
        return last_month_end.replace(day=1), last_month_end, "last month"
    if key == "last_month":
        prev_end = start.fromordinal(start.toordinal() - 1)
        return prev_end.replace(day=1), prev_end, "the month before"
    if key == "this_year":
        return date(start.year - 1, 1, 1), date(start.year - 1, 12, 31), str(start.year - 1)
    return None
