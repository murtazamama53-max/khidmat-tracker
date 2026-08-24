from datetime import date
from decimal import Decimal

import pytest

from app.services.tuition_service import (
    FeePeriodRange,
    FeeResolutionError,
    OverlappingFeePeriodError,
    close_previous_open_period,
    invoice_display_status,
    resolve_fee,
    validate_no_overlap,
)


def _ahmed_periods():
    # Exact blueprint example: Jan-Jun Rs.8,000, Jul-Sep Rs.10,000
    return [
        FeePeriodRange(id=1, student_id=1, amount=Decimal("8000"), effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30)),
        FeePeriodRange(id=2, student_id=1, amount=Decimal("10000"), effective_from=date(2026, 7, 1), effective_to=date(2026, 9, 30)),
    ]


def test_august_resolves_to_10000_not_8000():
    periods = _ahmed_periods()
    fee = resolve_fee(date(2026, 8, 1), periods)
    assert fee.amount == Decimal("10000")


def test_january_resolves_to_8000():
    periods = _ahmed_periods()
    fee = resolve_fee(date(2026, 1, 15), periods)
    assert fee.amount == Decimal("8000")


def test_changing_fee_schedule_does_not_alter_resolution_for_earlier_dates():
    periods = _ahmed_periods()
    resolve_fee(date(2026, 8, 1), periods)  # "view August" first
    # January must still resolve the same regardless of order/other periods present
    fee = resolve_fee(date(2026, 1, 1), periods)
    assert fee.amount == Decimal("8000")


def test_no_fee_defined_raises():
    periods = _ahmed_periods()
    with pytest.raises(FeeResolutionError):
        resolve_fee(date(2025, 12, 31), periods)


def test_overlap_detection():
    existing = _ahmed_periods()
    overlapping = FeePeriodRange(id=None, student_id=1, amount=Decimal("9000"), effective_from=date(2026, 6, 1), effective_to=date(2026, 7, 15))
    with pytest.raises(OverlappingFeePeriodError):
        validate_no_overlap(overlapping, existing)


def test_close_previous_open_period():
    existing = [FeePeriodRange(id=1, student_id=1, amount=Decimal("8000"), effective_from=date(2026, 1, 1), effective_to=None)]
    new_period = FeePeriodRange(id=None, student_id=1, amount=Decimal("10000"), effective_from=date(2026, 7, 1), effective_to=None)
    closed = close_previous_open_period(new_period, existing)
    assert closed.effective_to == date(2026, 6, 30)


def test_invoice_status_fully_paid():
    status = invoice_display_status(Decimal("10000"), Decimal("10000"), date(2026, 8, 5), date(2026, 8, 14))
    assert status == "paid"


def test_invoice_status_partial_before_due_date():
    status = invoice_display_status(Decimal("10000"), Decimal("6000"), date(2026, 9, 1), date(2026, 8, 14))
    assert status == "partial"


def test_invoice_status_pending_before_due_date():
    status = invoice_display_status(Decimal("10000"), Decimal("0"), date(2026, 9, 1), date(2026, 8, 14))
    assert status == "pending"


def test_invoice_status_overdue_when_due_date_passed_with_balance():
    status = invoice_display_status(Decimal("10000"), Decimal("6000"), date(2026, 8, 1), date(2026, 8, 14))
    assert status == "overdue"


def test_invoice_status_no_due_date_never_overdue():
    status = invoice_display_status(Decimal("10000"), Decimal("0"), None, date(2026, 12, 31))
    assert status == "pending"


def test_invoice_status_paid_even_past_due_date():
    status = invoice_display_status(Decimal("10000"), Decimal("10000"), date(2026, 1, 1), date(2026, 8, 14))
    assert status == "paid"
