from datetime import date
from decimal import Decimal

from app.services.analytics_service import (
    EarningEvent,
    SessionFact,
    average_session_duration_minutes,
    earnings_by_month,
    highest_and_lowest_earning_month,
    hours_by_month,
    paid_vs_pending,
    sessions_by_month,
    source_contribution,
)


def test_earnings_by_month_groups_correctly():
    events = [
        EarningEvent(date(2026, 1, 5), Decimal("1000"), "Khidmat"),
        EarningEvent(date(2026, 1, 20), Decimal("500"), "Khidmat"),
        EarningEvent(date(2026, 2, 1), Decimal("2000"), "Tuition"),
        EarningEvent(date(2025, 1, 1), Decimal("9999"), "Khidmat"),  # different year, excluded
    ]
    totals = earnings_by_month(events, 2026)
    assert totals[1] == Decimal("1500")
    assert totals[2] == Decimal("2000")
    assert totals[3] == Decimal("0")
    assert sum(totals.values()) == Decimal("3500")


def test_hours_by_month():
    sessions = [
        SessionFact(date(2026, 3, 1), 420, Decimal("1750"), "Sbhs"),
        SessionFact(date(2026, 3, 15), 80, Decimal("333.33"), "sghs"),
    ]
    totals = hours_by_month(sessions, 2026)
    assert totals[3] == (Decimal(420 + 80) / Decimal(60))


def test_sessions_by_month_counts():
    sessions = [
        SessionFact(date(2026, 5, 1), 60, Decimal("250"), "Sbhs"),
        SessionFact(date(2026, 5, 2), 60, Decimal("250"), "Sbhs"),
        SessionFact(date(2026, 6, 1), 60, Decimal("250"), "Sbhs"),
    ]
    totals = sessions_by_month(sessions, 2026)
    assert totals[5] == 2
    assert totals[6] == 1
    assert totals[7] == 0


def test_source_contribution():
    events = [
        EarningEvent(date(2026, 1, 1), Decimal("1750"), "Khidmat"),
        EarningEvent(date(2026, 1, 1), Decimal("333.33"), "Khidmat"),
        EarningEvent(date(2026, 1, 1), Decimal("10000"), "Tuition"),
    ]
    result = source_contribution(events)
    assert result["Khidmat"] == Decimal("2083.33")
    assert result["Tuition"] == Decimal("10000")


def test_average_session_duration():
    sessions = [
        SessionFact(date(2026, 1, 1), 100, Decimal("0"), "sghs"),
        SessionFact(date(2026, 1, 2), 60, Decimal("0"), "sghs"),
    ]
    avg = average_session_duration_minutes(sessions)
    assert avg == Decimal("80")


def test_average_session_duration_empty():
    assert average_session_duration_minutes([]) is None


def test_paid_vs_pending():
    facts = [
        {"amount": Decimal("10000"), "paid": Decimal("6000")},
        {"amount": Decimal("8000"), "paid": Decimal("8000")},
        {"amount": Decimal("5000"), "paid": Decimal("0")},
    ]
    paid, pending = paid_vs_pending(facts)
    assert paid == Decimal("14000")
    assert pending == Decimal("9000")  # (10000-6000) + (8000-8000) + (5000-0)


def test_paid_vs_pending_overpayment_never_negative_pending():
    facts = [{"amount": Decimal("1000"), "paid": Decimal("1200")}]
    paid, pending = paid_vs_pending(facts)
    assert pending == Decimal("0")


def test_highest_and_lowest_earning_month():
    totals = {m: Decimal("0") for m in range(1, 13)}
    totals[3] = Decimal("5000")
    totals[7] = Decimal("15000")
    totals[11] = Decimal("2000")
    (hi_m, hi_v), (lo_m, lo_v) = highest_and_lowest_earning_month(totals)
    assert hi_m == 7 and hi_v == Decimal("15000")
    assert lo_m == 11 and lo_v == Decimal("2000")


def test_highest_and_lowest_earning_month_no_data():
    totals = {m: Decimal("0") for m in range(1, 13)}
    (hi_m, hi_v), (lo_m, lo_v) = highest_and_lowest_earning_month(totals)
    assert hi_m is None and lo_m is None
