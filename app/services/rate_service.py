"""
Rate resolution service.

Resolves the applicable historical rate for a given source and date.
This is what makes the system "date-aware": a January session must always
use the January rate, even after a July rate is added (blueprint section 7).
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional


class RateResolutionError(ValueError):
    pass


class OverlappingRatePeriodError(ValueError):
    pass


@dataclass(frozen=True)
class RatePeriod:
    id: Optional[int]
    source_id: int
    rate: Decimal
    effective_from: date
    effective_to: Optional[date]  # None = still open / current


def resolve_rate(session_date: date, rate_periods: Iterable[RatePeriod]) -> RatePeriod:
    """
    Select the rate period whose effective_from <= session_date AND
    (effective_to is None OR session_date <= effective_to).

    If more than one period matches (shouldn't happen if overlap validation
    is enforced on write), the most recently started period wins.
    If none match, raises RateResolutionError -- callers must not silently
    fall back to "the current rate" for a historical date.
    """
    candidates = [
        p
        for p in rate_periods
        if p.effective_from <= session_date
        and (p.effective_to is None or session_date <= p.effective_to)
    ]
    if not candidates:
        raise RateResolutionError(
            f"No rate is defined for this source covering {session_date.isoformat()}. "
            "Add a rate period that covers this date before saving the session."
        )
    candidates.sort(key=lambda p: p.effective_from, reverse=True)
    return candidates[0]


def validate_no_overlap(
    new_period: RatePeriod, existing_periods: Iterable[RatePeriod]
) -> None:
    """
    Raise OverlappingRatePeriodError if new_period's [effective_from, effective_to]
    range overlaps any existing period for the same source.
    An open-ended period (effective_to=None) is treated as extending to infinity.
    """
    new_start = new_period.effective_from
    new_end = new_period.effective_to

    for existing in existing_periods:
        if existing.id is not None and existing.id == new_period.id:
            continue
        if existing.source_id != new_period.source_id:
            continue

        existing_start = existing.effective_from
        existing_end = existing.effective_to

        # Two ranges overlap unless one ends strictly before the other starts.
        new_ends_before_existing = new_end is not None and new_end < existing_start
        existing_ends_before_new = existing_end is not None and existing_end < new_start

        if not new_ends_before_existing and not existing_ends_before_new:
            raise OverlappingRatePeriodError(
                f"New rate period {new_start}..{new_end or 'open'} overlaps existing "
                f"period {existing_start}..{existing_end or 'open'}."
            )


def close_previous_open_period(
    new_period: RatePeriod, existing_periods: Iterable[RatePeriod]
) -> Optional[RatePeriod]:
    """
    When adding a new rate that starts after an existing open-ended period,
    return an updated copy of that open period with effective_to set to the
    day before the new period starts, so periods stay contiguous and
    non-overlapping. Caller is responsible for persisting the change.
    Returns None if there's no open period to close for this source.
    """
    from datetime import timedelta

    for existing in existing_periods:
        if existing.source_id != new_period.source_id:
            continue
        if existing.effective_to is None and existing.effective_from < new_period.effective_from:
            new_close_date = new_period.effective_from - timedelta(days=1)
            return RatePeriod(
                id=existing.id,
                source_id=existing.source_id,
                rate=existing.rate,
                effective_from=existing.effective_from,
                effective_to=new_close_date,
            )
    return None
