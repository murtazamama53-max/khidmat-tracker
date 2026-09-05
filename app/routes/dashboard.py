import calendar as calendar_module
from datetime import date
from decimal import Decimal

from flask import Blueprint, current_app, render_template, request, session

from app.extensions import db
from app.models import CalendarAccount, CalendarDraft, Goal, IncomeSource, Student
from app.models import Session as SessionModel
from app.models import User
from app.routes.auth import owner_only
from app.services import earnings_query as eq
from app.services import goals_service
from app.services.analytics_service import (
    earnings_by_month,
    hours_by_month,
    paid_vs_pending,
    source_contribution,
)
from app.services.calculation_engine import effective_hourly_rate
from app.services.date_range import InvalidRangeError, app_today, previous_period, resolve_range
from app.services.tuition_service import invoice_display_status

bp = Blueprint("dashboard", __name__)

MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _parse_date_arg(name):
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _period_metrics(user_id: int, start: date, end: date, today: date) -> dict:
    """Real, deterministic totals for one date range -- used for both the
    currently-selected period and (separately) the prior comparable period
    for 'vs last period' trend deltas. Same code path both times, so a
    trend delta can never drift from what the KPI cards themselves show."""
    sessions = eq.get_sessions(user_id, start, end)
    invoices = eq.get_invoices_overlapping(user_id, start, end)
    adjustments = eq.get_adjustments(user_id, start, end)

    khidmat_earnings = sum((Decimal(s.calculated_amount) for s in sessions), Decimal("0"))
    total_minutes = sum(s.duration_minutes for s in sessions)
    total_hours = Decimal(total_minutes) / Decimal(60)
    rate = effective_hourly_rate(khidmat_earnings, total_hours)

    tuition_facts = eq.invoice_payment_facts(invoices)
    tuition_paid, tuition_pending = paid_vs_pending(tuition_facts)
    tuition_expected = tuition_paid + tuition_pending

    adjustments_net = sum(
        (Decimal(a.amount) if a.type == "bonus" else -Decimal(a.amount) for a in adjustments), Decimal("0")
    )

    return {
        "sessions": sessions,
        "invoices": invoices,
        "tuition_facts": tuition_facts,
        "khidmat_earnings": khidmat_earnings,
        "total_hours": total_hours,
        "effective_rate": rate,
        "session_count": len(sessions),
        "tuition_paid": tuition_paid,
        "tuition_pending": tuition_pending,
        "tuition_expected": tuition_expected,
        "adjustments_net": adjustments_net,
        "expected_total": khidmat_earnings + tuition_expected + adjustments_net,
        "paid_total": khidmat_earnings + tuition_paid + adjustments_net,
    }


def _pct_change(current: Decimal, previous: Decimal):
    """None when there's no meaningful baseline to compare against (rather
    than showing a fabricated or infinite percentage)."""
    if previous == 0:
        return None
    return (current - previous) / abs(previous) * Decimal("100")


@bp.route("/")
@owner_only
def index():
    user = db.session.get(User, session["user_id"])
    today = app_today(current_app.config["TIMEZONE"])

    range_key = request.args.get("range", "this_month")
    if range_key not in ("this_month", "last_month", "this_year", "custom"):
        range_key = "this_month"

    try:
        start, end, range_label = resolve_range(
            range_key, today, custom_start=_parse_date_arg("start"), custom_end=_parse_date_arg("end")
        )
    except InvalidRangeError:
        start, end, range_label = resolve_range("this_month", today)
        range_key = "this_month"

    range_sessions = eq.get_sessions(user.id, start, end)
    metrics = _period_metrics(user.id, start, end, today)
    range_invoices = metrics["invoices"]
    range_adjustments = eq.get_adjustments(user.id, start, end)

    khidmat_earnings = metrics["khidmat_earnings"]
    total_hours = metrics["total_hours"]
    rate = metrics["effective_rate"]
    tuition_paid = metrics["tuition_paid"]
    tuition_pending = metrics["tuition_pending"]
    tuition_expected = metrics["tuition_expected"]
    adjustments_net = metrics["adjustments_net"]
    expected_total = metrics["expected_total"]
    paid_total = metrics["paid_total"]
    pending_total = tuition_pending
    tuition_facts = metrics["tuition_facts"]

    # --- Trend vs. the comparable previous period (e.g. "This Month" vs
    # "Last Month"). Real recomputed totals, not an estimate -- and simply
    # omitted (None) for custom ranges or when there's no baseline to
    # compare against, rather than showing a misleading number.
    prev_range = previous_period(range_key, start, today)
    trends = {
        "total": None, "khidmat": None, "tuition": None, "pending": None,
        "hours": None, "rate": None, "sessions": None, "other": None,
    }
    if prev_range is not None:
        prev_start, prev_end, prev_label = prev_range
        prev_metrics = _period_metrics(user.id, prev_start, prev_end, today)
        trends["total"] = _pct_change(expected_total, prev_metrics["expected_total"])
        trends["khidmat"] = _pct_change(khidmat_earnings, prev_metrics["khidmat_earnings"])
        trends["tuition"] = _pct_change(tuition_expected, prev_metrics["tuition_expected"])
        trends["pending"] = _pct_change(pending_total, prev_metrics["tuition_pending"])
        trends["hours"] = _pct_change(total_hours, prev_metrics["total_hours"])
        trends["sessions"] = _pct_change(Decimal(metrics["session_count"]), Decimal(prev_metrics["session_count"]))
        trends["other"] = _pct_change(adjustments_net, prev_metrics["adjustments_net"])
        if rate is not None and prev_metrics["effective_rate"] is not None:
            trends["rate"] = _pct_change(rate, prev_metrics["effective_rate"])
        trend_label = prev_label
    else:
        trend_label = None

    # Tuition is its own domain (fixed fee / student / billing period / payment
    # status) -- never folded into Khidmat's hours or rate. These counts feed
    # a dedicated Tuition panel, visually distinct from the Khidmat panel.
    tuition_student_count = len({inv.student_id for inv in range_invoices})
    tuition_paid_count = 0
    tuition_pending_count = 0
    for inv, facts in zip(range_invoices, tuition_facts):
        status = invoice_display_status(facts["amount"], facts["paid"], inv.due_date, today)
        if status == "paid":
            tuition_paid_count += 1
        else:
            tuition_pending_count += 1  # covers pending, partial, and overdue alike

    breakdown = [
        {"label": "Khidmat", "amount": khidmat_earnings, "css": "kpi-emerald"},
        {"label": "Tuition", "amount": tuition_expected, "css": "kpi-violet"},
        {"label": "Other / Adjustments", "amount": adjustments_net, "css": "kpi-gold"},
    ]

    # --- Chart data: Khidmat earnings + Khidmat hours trend for the current
    # year, real DB values only. Deliberately Khidmat-only on BOTH lines --
    # blending Tuition/Other into the earnings line while hours stays
    # Khidmat-only would visually imply the combined income came from those
    # hours, which is exactly the kind of misleading pairing to avoid.
    # Total income vs. source split is shown separately by the breakdown
    # donut below, where it belongs.
    year_start = date(today.year, 1, 1)
    year_sessions = eq.get_sessions(user.id, year_start, today)
    khidmat_year_events = eq.to_earning_events(year_sessions, [], [])
    year_session_facts = eq.to_session_facts(year_sessions)
    earnings_month_totals = earnings_by_month(khidmat_year_events, today.year)
    hours_month_totals = hours_by_month(year_session_facts, today.year)

    months_so_far = today.month
    chart_labels = MONTH_LABELS[:months_so_far]
    chart_earnings = [float(earnings_month_totals[m]) for m in range(1, months_so_far + 1)]
    chart_hours = [float(hours_month_totals[m]) for m in range(1, months_so_far + 1)]

    source_totals = source_contribution(
        eq.to_earning_events(range_sessions, range_invoices, range_adjustments)
    )
    source_breakdown = [{"label": k, "amount": float(v)} for k, v in source_totals.items() if v > 0]

    # --- Calendar card: current month, days with sessions, broken down by
    # source (not just a count) so the widget can render a color-coded dot
    # per source -- e.g. SBHS vs SGHS -- rather than one undifferentiated
    # dot. Color assignment mirrors routes/calendar.py's _source_color_index
    # exactly (source_id % 5) so the same source always gets the same
    # color everywhere in the app.
    cal_month_start = today.replace(day=1)
    _, days_in_month = calendar_module.monthrange(today.year, today.month)
    cal_month_end = today.replace(day=days_in_month)
    month_sessions_for_calendar = eq.get_sessions(user.id, cal_month_start, cal_month_end)
    day_sources: dict = {}
    for s in month_sessions_for_calendar:
        bucket = day_sources.setdefault(s.date.day, {})
        entry = bucket.setdefault(s.source_id, {"source_id": s.source_id, "source": s.source.name, "count": 0, "color_index": s.source_id % 5})
        entry["count"] += 1
    day_counts = {day: sum(e["count"] for e in sources.values()) for day, sources in day_sources.items()}
    day_sources_out = {day: list(sources.values()) for day, sources in day_sources.items()}
    cal_sources_legend = list({src["source_id"]: src for sources in day_sources.values() for src in sources.values()}.values())
    cal_sources_legend.sort(key=lambda s: s["source"])
    first_weekday = (cal_month_start.weekday() + 1) % 7  # convert Mon=0 to Sun=0 for a Sun-first grid

    calendar_account = CalendarAccount.query.filter_by(user_id=user.id).first()
    needs_review_count = CalendarDraft.query.filter_by(user_id=user.id, status="pending").count()

    goal = Goal.query.filter_by(user_id=user.id).first()
    month_actuals = goals_service.compute_month_actuals(user.id, today)
    goal_progress = goals_service.compute_progress(goal, month_actuals)

    recent_sessions = (
        SessionModel.query.filter_by(user_id=user.id).order_by(SessionModel.date.desc(), SessionModel.id.desc()).limit(8).all()
    )

    has_sources = IncomeSource.query.filter_by(user_id=user.id).count() > 0
    active_student_count = Student.query.filter_by(user_id=user.id, active=True).count()
    completion_rate = None
    if expected_total > 0:
        completion_rate = (paid_total / expected_total) * Decimal("100")
        completion_rate = max(Decimal("0"), min(Decimal("100"), completion_rate))

    return render_template(
        "dashboard.html",
        user=user,
        today=today,
        range_key=range_key,
        range_label=range_label,
        range_start=start,
        range_end=end,
        total_earnings=expected_total,
        expected_total=expected_total,
        paid_total=paid_total,
        pending_total=pending_total,
        khidmat_earnings=khidmat_earnings,
        tuition_earnings=tuition_expected,
        tuition_paid=tuition_paid,
        tuition_pending=tuition_pending,
        tuition_student_count=tuition_student_count,
        tuition_paid_count=tuition_paid_count,
        tuition_pending_count=tuition_pending_count,
        adjustments_net=adjustments_net,
        breakdown=breakdown,
        total_hours=total_hours,
        effective_rate=rate,
        session_count=len(range_sessions),
        recent_sessions=recent_sessions,
        has_sources=has_sources,
        chart_labels=chart_labels,
        chart_earnings=chart_earnings,
        chart_hours=chart_hours,
        source_breakdown=source_breakdown,
        cal_year=today.year,
        cal_month=today.month,
        cal_month_name=calendar_module.month_name[today.month],
        cal_days_in_month=days_in_month,
        cal_first_weekday=first_weekday,
        cal_day_counts=day_counts,
        cal_day_sources=day_sources_out,
        cal_sources_legend=cal_sources_legend,
        cal_today_day=today.day,
        calendar_account=calendar_account,
        needs_review_count=needs_review_count,
        goal=goal,
        goal_progress=goal_progress,
        trends=trends,
        trend_label=trend_label,
        active_student_count=active_student_count,
        completion_rate=completion_rate,
    )
