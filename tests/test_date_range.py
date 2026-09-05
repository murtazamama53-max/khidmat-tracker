from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from app.services.date_range import InvalidRangeError, app_today, resolve_range


def test_app_today_uses_configured_timezone_not_server_utc():
    """
    Direct regression for a real production bug: on Vercel the server clock
    is UTC. Pakistan is UTC+5, so for the first ~5 hours of every Pakistan
    calendar day, a naive date.today() on the server still reports
    "yesterday". app_today() must use the app's configured timezone
    instead of the server's local time.
    """
    # 8pm UTC on Aug 25 is already 1am on Aug 26 in Karachi (UTC+5).
    fake_utc_now = datetime(2026, 8, 25, 20, 0, 0, tzinfo=timezone.utc)

    with patch("app.services.date_range.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz: fake_utc_now.astimezone(tz)
        result = app_today("Asia/Karachi")

    assert result == date(2026, 8, 26)
    assert result != fake_utc_now.date()  # must NOT be the stale server-UTC date


def test_app_today_utc_passthrough():
    fake_utc_now = datetime(2026, 8, 25, 20, 0, 0, tzinfo=timezone.utc)
    with patch("app.services.date_range.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz: fake_utc_now.astimezone(tz)
        result = app_today("UTC")
    assert result == date(2026, 8, 25)


def test_this_month():
    start, end, label = resolve_range("this_month", date(2026, 8, 14))
    assert start == date(2026, 8, 1)
    assert end == date(2026, 8, 14)


def test_last_month_regular():
    start, end, label = resolve_range("last_month", date(2026, 8, 14))
    assert start == date(2026, 7, 1)
    assert end == date(2026, 7, 31)


def test_last_month_crosses_year_boundary():
    start, end, label = resolve_range("last_month", date(2026, 1, 14))
    assert start == date(2025, 12, 1)
    assert end == date(2025, 12, 31)


def test_this_year():
    start, end, label = resolve_range("this_year", date(2026, 8, 14))
    assert start == date(2026, 1, 1)
    assert end == date(2026, 8, 14)


def test_custom_range():
    start, end, label = resolve_range("custom", date(2026, 8, 14), custom_start=date(2026, 3, 1), custom_end=date(2026, 5, 31))
    assert start == date(2026, 3, 1)
    assert end == date(2026, 5, 31)


def test_custom_range_missing_dates_raises():
    with pytest.raises(InvalidRangeError):
        resolve_range("custom", date(2026, 8, 14))


def test_custom_range_end_before_start_raises():
    with pytest.raises(InvalidRangeError):
        resolve_range("custom", date(2026, 8, 14), custom_start=date(2026, 5, 1), custom_end=date(2026, 3, 1))


def test_unknown_key_raises():
    with pytest.raises(InvalidRangeError):
        resolve_range("bogus", date(2026, 8, 14))
