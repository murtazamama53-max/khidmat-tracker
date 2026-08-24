from datetime import date
from decimal import Decimal

import pytest

from app.services.rate_service import (
    OverlappingRatePeriodError,
    RatePeriod,
    RateResolutionError,
    close_previous_open_period,
    resolve_rate,
    validate_no_overlap,
)


def _periods():
    return [
        RatePeriod(id=1, source_id=1, rate=Decimal("250"), effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30)),
        RatePeriod(id=2, source_id=1, rate=Decimal("300"), effective_from=date(2026, 7, 1), effective_to=None),
    ]


def test_historical_rate_change_does_not_affect_old_sessions():
    periods = _periods()
    jan_rate = resolve_rate(date(2026, 1, 15), periods)
    assert jan_rate.rate == Decimal("250")

    # Even though a July rate exists "now", a January date must still resolve to Jan rate.
    aug_rate = resolve_rate(date(2026, 8, 1), periods)
    assert aug_rate.rate == Decimal("300")

    jan_rate_again = resolve_rate(date(2026, 1, 15), periods)
    assert jan_rate_again.rate == Decimal("250")


def test_open_ended_period_covers_future_dates():
    periods = _periods()
    far_future = resolve_rate(date(2030, 1, 1), periods)
    assert far_future.rate == Decimal("300")


def test_no_matching_rate_raises():
    periods = _periods()
    with pytest.raises(RateResolutionError):
        resolve_rate(date(2025, 12, 31), periods)


def test_overlap_detection_rejects_overlapping_period():
    existing = _periods()
    overlapping = RatePeriod(
        id=None, source_id=1, rate=Decimal("275"),
        effective_from=date(2026, 5, 1), effective_to=date(2026, 8, 1),
    )
    with pytest.raises(OverlappingRatePeriodError):
        validate_no_overlap(overlapping, existing)


def test_overlap_detection_allows_adjacent_period():
    existing = [
        RatePeriod(id=1, source_id=1, rate=Decimal("250"), effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30)),
    ]
    adjacent = RatePeriod(id=None, source_id=1, rate=Decimal("300"), effective_from=date(2026, 7, 1), effective_to=None)
    # Should not raise
    validate_no_overlap(adjacent, existing)


def test_close_previous_open_period():
    existing = [
        RatePeriod(id=1, source_id=1, rate=Decimal("250"), effective_from=date(2026, 1, 1), effective_to=None),
    ]
    new_period = RatePeriod(id=None, source_id=1, rate=Decimal("300"), effective_from=date(2026, 7, 1), effective_to=None)
    closed = close_previous_open_period(new_period, existing)
    assert closed is not None
    assert closed.effective_to == date(2026, 6, 30)
