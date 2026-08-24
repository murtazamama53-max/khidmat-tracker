"""
Computes monthly goal progress from real session/invoice/adjustment data.
Shared by the dashboard's compact panel and the dedicated Goals page so
the numbers are always consistent between the two.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from app.services import earnings_query as eq


@dataclass(frozen=True)
class MonthActuals:
    income: Decimal
    hours: Decimal
    session_count: int


def compute_month_actuals(user_id: int, today: date) -> MonthActuals:
    month_start = today.replace(day=1)
    sessions = eq.get_sessions(user_id, month_start, today)
    invoices = eq.get_invoices_overlapping(user_id, month_start, today)
    adjustments = eq.get_adjustments(user_id, month_start, today)

    income = sum((Decimal(s.calculated_amount) for s in sessions), Decimal("0"))
    income += sum((Decimal(i.amount) for i in invoices), Decimal("0"))
    income += sum((Decimal(a.amount) if a.type == "bonus" else -Decimal(a.amount) for a in adjustments), Decimal("0"))

    hours = Decimal(sum(s.duration_minutes for s in sessions)) / Decimal(60)

    return MonthActuals(income=income, hours=hours, session_count=len(sessions))


def _pct(current, target) -> Optional[float]:
    if target is None or Decimal(target) <= 0:
        return None
    return min(float(Decimal(current) / Decimal(target) * 100), 999.0)


def compute_progress(goal, actuals: MonthActuals) -> dict:
    """goal may be None (no targets set yet)."""
    if goal is None:
        return {
            "income_target": None, "income_current": actuals.income, "income_pct": None,
            "hours_target": None, "hours_current": actuals.hours, "hours_pct": None,
            "sessions_target": None, "sessions_current": actuals.session_count, "sessions_pct": None,
        }
    return {
        "income_target": goal.monthly_income_target,
        "income_current": actuals.income,
        "income_pct": _pct(actuals.income, goal.monthly_income_target),
        "hours_target": goal.monthly_hours_target,
        "hours_current": actuals.hours,
        "hours_pct": _pct(actuals.hours, goal.monthly_hours_target),
        "sessions_target": goal.monthly_sessions_target,
        "sessions_current": actuals.session_count,
        "sessions_pct": _pct(actuals.session_count, goal.monthly_sessions_target),
    }
