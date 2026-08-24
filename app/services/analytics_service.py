"""
Analytics service.

Pure functions that take already-fetched data (never touches the DB or
Flask directly) and compute the metrics listed in blueprint section 26.
Keeping this DB-agnostic makes it independently testable and keeps the
route layer responsible for scoping queries to the right owner/date range.
"""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional


@dataclass(frozen=True)
class EarningEvent:
    """A single dated amount of income, tagged with its source category."""

    date_: object  # datetime.date
    amount: Decimal
    category: str  # e.g. 'Khidmat', 'Tuition', 'Other'


@dataclass(frozen=True)
class SessionFact:
    date_: object
    duration_minutes: int
    amount: Decimal
    source_name: str


def earnings_by_month(events: Iterable[EarningEvent], year: int) -> dict:
    """{1: Decimal, ..., 12: Decimal} for the given calendar year."""
    totals = {m: Decimal("0") for m in range(1, 13)}
    for e in events:
        if e.date_.year == year:
            totals[e.date_.month] += Decimal(e.amount)
    return totals


def hours_by_month(sessions: Iterable[SessionFact], year: int) -> dict:
    totals = {m: Decimal("0") for m in range(1, 13)}
    for s in sessions:
        if s.date_.year == year:
            totals[s.date_.month] += Decimal(s.duration_minutes) / Decimal(60)
    return totals


def sessions_by_month(sessions: Iterable[SessionFact], year: int) -> dict:
    totals = {m: 0 for m in range(1, 13)}
    for s in sessions:
        if s.date_.year == year:
            totals[s.date_.month] += 1
    return totals


def source_contribution(events: Iterable[EarningEvent]) -> dict:
    """{category: total_amount} across whatever events are passed in."""
    totals: dict = defaultdict(lambda: Decimal("0"))
    for e in events:
        totals[e.category] += Decimal(e.amount)
    return dict(totals)


def average_session_duration_minutes(sessions: Iterable[SessionFact]) -> Optional[Decimal]:
    sessions = list(sessions)
    if not sessions:
        return None
    total = sum((Decimal(s.duration_minutes) for s in sessions), Decimal("0"))
    return total / Decimal(len(sessions))


def paid_vs_pending(invoice_facts: Iterable[dict]) -> tuple:
    """
    invoice_facts: iterable of {'amount': Decimal, 'paid': Decimal}.
    Returns (total_paid, total_pending) as Decimals.
    """
    total_paid = Decimal("0")
    total_pending = Decimal("0")
    for f in invoice_facts:
        amount = Decimal(f["amount"])
        paid = Decimal(f["paid"])
        total_paid += paid
        total_pending += max(amount - paid, Decimal("0"))
    return total_paid, total_pending


def highest_and_lowest_earning_month(month_totals: dict) -> tuple:
    """
    month_totals: {1: Decimal, ..., 12: Decimal}.
    Returns ((highest_month, highest_amount), (lowest_month, lowest_amount))
    considering only months with nonzero earnings; (None, None) for either
    side if there's no data at all.
    """
    nonzero = {m: v for m, v in month_totals.items() if v > 0}
    if not nonzero:
        return (None, None), (None, None)
    highest_month = max(nonzero, key=lambda m: nonzero[m])
    lowest_month = min(nonzero, key=lambda m: nonzero[m])
    return (highest_month, nonzero[highest_month]), (lowest_month, nonzero[lowest_month])
