"""
Calculation engine.

THIS MODULE IS THE ONLY PLACE MONEY MATH HAPPENS.

Rules (see blueprint section 9 "Exact calculation engine"):
  - Minutes are the canonical duration unit.
  - Decimal is used for all monetary arithmetic (never float).
  - No rounding unless explicitly enabled in settings.
  - Zero or negative duration is rejected.
  - Overnight sessions (end < start) must be explicitly confirmed by the
    caller (is_overnight=True) or this module raises.
  - This module never calls an LLM and never guesses. It only takes
    structured input and produces structured, exact output.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


class CalculationError(ValueError):
    """Raised for invalid calculation inputs (zero duration, bad overnight, etc.)."""


class AmbiguousOvernightError(CalculationError):
    """Raised when end time < start time and the caller has not confirmed overnight."""


@dataclass(frozen=True)
class DurationResult:
    duration_minutes: int
    decimal_hours: Decimal
    human_readable: str  # e.g. "1h 40m"


@dataclass(frozen=True)
class EarningResult:
    duration_minutes: int
    decimal_hours: Decimal
    applied_rate: Decimal
    calculated_amount: Decimal


def _to_minutes(hour: int, minute: int) -> int:
    return hour * 60 + minute


def exact_time_duration(
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
    is_overnight: bool = False,
) -> DurationResult:
    """
    Compute duration in minutes between a start and end clock time.

    If end time is earlier than start time, the caller MUST pass
    is_overnight=True to explicitly confirm the session crosses midnight,
    otherwise AmbiguousOvernightError is raised (per blueprint section 9).
    """
    start_total = _to_minutes(start_hour, start_minute)
    end_total = _to_minutes(end_hour, end_minute)

    if end_total <= start_total:
        if not is_overnight:
            raise AmbiguousOvernightError(
                "End time is not after start time. Confirm overnight session "
                "explicitly (is_overnight=True) or correct the times."
            )
        # Crosses midnight: add 24h worth of minutes to the end.
        end_total += 24 * 60

    duration_minutes = end_total - start_total

    if duration_minutes <= 0:
        raise CalculationError("Duration must be greater than zero minutes.")

    return _build_duration_result(duration_minutes)


def fixed_hours_duration(quantity_hours: Decimal) -> DurationResult:
    """Compute duration for a fixed-hour quantity input like Sbhs(7)."""
    if quantity_hours <= 0:
        raise CalculationError("Quantity of hours must be greater than zero.")

    duration_minutes = int((Decimal(quantity_hours) * 60).to_integral_value(rounding=ROUND_HALF_UP))
    return _build_duration_result(duration_minutes)


def _build_duration_result(duration_minutes: int) -> DurationResult:
    decimal_hours = Decimal(duration_minutes) / Decimal(60)
    hours = duration_minutes // 60
    minutes = duration_minutes % 60
    if hours and minutes:
        human = f"{hours}h {minutes:02d}m"
    elif hours:
        human = f"{hours}h"
    else:
        human = f"{minutes}m"
    return DurationResult(
        duration_minutes=duration_minutes,
        decimal_hours=decimal_hours,
        human_readable=human,
    )


def duration_from_minutes(duration_minutes: int) -> DurationResult:
    """
    Builds a DurationResult from an already-known, unambiguous minute
    count. Used by calendar sync, where Google Calendar gives full
    start/end datetimes (not just clock times), so there is no
    same-day-vs-overnight ambiguity left to resolve -- the caller has
    already computed duration_minutes from two real datetimes.
    """
    if duration_minutes <= 0:
        raise CalculationError("Duration must be greater than zero minutes.")
    return _build_duration_result(duration_minutes)


def calculate_earning(
    duration: DurationResult,
    hourly_rate: Decimal,
    rounding_enabled: bool = False,
) -> EarningResult:
    """
    gross_earning = decimal_hours * hourly_rate

    No rounding by default (blueprint section 9: "Rounding: Off by default").
    When rounding_enabled=True, rounds to 2 decimal places (currency minor unit).
    """
    if hourly_rate <= 0:
        raise CalculationError("Hourly rate must be greater than zero.")

    amount = duration.decimal_hours * Decimal(hourly_rate)
    if rounding_enabled:
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return EarningResult(
        duration_minutes=duration.duration_minutes,
        decimal_hours=duration.decimal_hours,
        applied_rate=Decimal(hourly_rate),
        calculated_amount=amount,
    )


def calculate_fixed_fee(amount: Decimal) -> Decimal:
    """Private tuition / other-income fixed amounts pass through unchanged."""
    if amount < 0:
        raise CalculationError("Fixed fee amount cannot be negative.")
    return Decimal(amount)


def sum_amounts(*amounts: Decimal) -> Decimal:
    """Sum an arbitrary number of Decimal amounts safely."""
    total = Decimal("0")
    for a in amounts:
        total += Decimal(a)
    return total


def effective_hourly_rate(total_time_based_earnings: Decimal, total_time_based_hours: Decimal) -> Optional[Decimal]:
    """
    total earnings / total time-based hours.
    Excludes fixed tuition from the denominator per blueprint section 20.
    Returns None when there are no hours to divide by (avoid div-by-zero).
    """
    if total_time_based_hours == 0:
        return None
    return total_time_based_earnings / total_time_based_hours
