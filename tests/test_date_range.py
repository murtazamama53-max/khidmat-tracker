from datetime import date

import pytest

from app.services.date_range import InvalidRangeError, resolve_range


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
