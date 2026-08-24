"""
Tuition service.

Private tuition is fixed-fee, not hourly (blueprint section 6/14):
"Private tuition should not be forced into the hourly engine." This
module mirrors rate_service.py's date-aware resolution pattern, but for
student fee periods instead of source hourly rates, and adds invoice
status derivation from payments received.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

STATUS_PENDING = "pending"
STATUS_PARTIAL = "partial"
STATUS_PAID = "paid"


class FeeResolutionError(ValueError):
    pass


class OverlappingFeePeriodError(ValueError):
    pass


@dataclass(frozen=True)
class FeePeriodRange:
    id: Optional[int]
    student_id: int
    amount: Decimal
    effective_from: date
    effective_to: Optional[date]  # None = open-ended / current


def resolve_fee(billing_date: date, fee_periods: Iterable[FeePeriodRange]) -> FeePeriodRange:
    """
    Select the fee period whose effective_from <= billing_date AND
    (effective_to is None OR billing_date <= effective_to). Mirrors
    rate_service.resolve_rate exactly, so August always uses the fee
    that was in force in August, never today's fee (blueprint section 6:
    "Ahmed: Jan-Jun Rs 8,000/month; Jul-Sep Rs 10,000/month. When August
    is viewed, the app uses Rs 10,000 without changing January-June history.")
    """
    candidates = [
        p
        for p in fee_periods
        if p.effective_from <= billing_date and (p.effective_to is None or billing_date <= p.effective_to)
    ]
    if not candidates:
        raise FeeResolutionError(
            f"No tuition fee is defined covering {billing_date.isoformat()}. "
            "Add a fee period that covers this date before generating an invoice."
        )
    candidates.sort(key=lambda p: p.effective_from, reverse=True)
    return candidates[0]


def validate_no_overlap(new_period: FeePeriodRange, existing_periods: Iterable[FeePeriodRange]) -> None:
    new_start = new_period.effective_from
    new_end = new_period.effective_to

    for existing in existing_periods:
        if existing.id is not None and existing.id == new_period.id:
            continue
        if existing.student_id != new_period.student_id:
            continue

        existing_start = existing.effective_from
        existing_end = existing.effective_to

        new_ends_before_existing = new_end is not None and new_end < existing_start
        existing_ends_before_new = existing_end is not None and existing_end < new_start

        if not new_ends_before_existing and not existing_ends_before_new:
            raise OverlappingFeePeriodError(
                f"New fee period {new_start}..{new_end or 'open'} overlaps existing "
                f"period {existing_start}..{existing_end or 'open'}."
            )


def close_previous_open_period(
    new_period: FeePeriodRange, existing_periods: Iterable[FeePeriodRange]
) -> Optional[FeePeriodRange]:
    from datetime import timedelta

    for existing in existing_periods:
        if existing.student_id != new_period.student_id:
            continue
        if existing.effective_to is None and existing.effective_from < new_period.effective_from:
            new_close_date = new_period.effective_from - timedelta(days=1)
            return FeePeriodRange(
                id=existing.id,
                student_id=existing.student_id,
                amount=existing.amount,
                effective_from=existing.effective_from,
                effective_to=new_close_date,
            )
    return None


def invoice_display_status(
    invoice_amount: Decimal,
    total_paid: Decimal,
    due_date: Optional[date],
    today: date,
) -> str:
    """
    Derives the display status for an invoice from what's actually been
    paid, rather than a manually-maintained flag that could go stale.
    'overdue' is a display-only refinement of 'pending'/'partial' when
    the due date has passed with money still owed -- it is never stored
    as a separate terminal state so it can't get stuck.
    """
    if total_paid >= invoice_amount:
        return STATUS_PAID
    if total_paid > 0:
        base = STATUS_PARTIAL
    else:
        base = STATUS_PENDING
    if due_date is not None and today > due_date:
        return "overdue"
    return base
