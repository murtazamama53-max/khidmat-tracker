"""
Deterministic natural-language Q&A over the owner's own recorded data
(blueprint section 12, "V3 AI"). This is a local pattern-matcher, not an
external AI model: it only ever decides *which metric, which date range,
which source* the person is asking about. Every number in every answer
comes straight from earnings_query.py + analytics_service.py + the
calculation engine -- the exact same deterministic code paths the rest of
the app already uses for the dashboard and reports. No arithmetic is ever
invented here, and nothing is sent anywhere; it's all in-process string
matching against known income sources and calendar vocabulary.
"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional, Tuple

from app.models import IncomeSource
from app.services import earnings_query as eq
from app.services.analytics_service import paid_vs_pending
from app.services.calculation_engine import effective_hourly_rate

Period = Tuple[date, date, str]  # (start, end, label)

EXAMPLE_QUESTIONS = [
    "How much did I earn this month?",
    "How many hours did I work last month?",
    "How much is pending from tuition?",
    "Why is this month lower than last month?",
]


@dataclass(frozen=True)
class AssistantAnswer:
    text: str
    breakdown: List[dict] = field(default_factory=list)


# --------------------------------------------------------------------------
# Period parsing (pure, no DB access -- independently unit-testable)
# --------------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_MONTH_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS.keys(), key=len, reverse=True)) + r")\b(?:\s+(\d{4}))?",
    re.IGNORECASE,
)

_KEYWORD_PATTERNS = [
    ("today", re.compile(r"\btoday\b", re.IGNORECASE)),
    ("yesterday", re.compile(r"\byesterday\b", re.IGNORECASE)),
    ("this_week", re.compile(r"\bthis week\b", re.IGNORECASE)),
    ("last_week", re.compile(r"\blast week\b", re.IGNORECASE)),
    ("this_month", re.compile(r"\bthis month\b", re.IGNORECASE)),
    ("last_month", re.compile(r"\blast month\b", re.IGNORECASE)),
    ("this_year", re.compile(r"\bthis year\b", re.IGNORECASE)),
    ("last_year", re.compile(r"\blast year\b", re.IGNORECASE)),
    ("all_time", re.compile(r"\ball time\b|\boverall\b|\baltogether\b", re.IGNORECASE)),
]


def _month_period(month_num: int, year: int) -> Period:
    start = date(year, month_num, 1)
    if month_num == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month_num + 1, 1) - timedelta(days=1)
    return start, end, f"{start.strftime('%B')} {year}"


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


def _resolve_keyword(key: str, today: date) -> Period:
    if key == "today":
        return today, today, "today"
    if key == "yesterday":
        y = today - timedelta(days=1)
        return y, y, "yesterday"
    if key == "this_week":
        return _week_start(today), today, "this week"
    if key == "last_week":
        last_end = _week_start(today) - timedelta(days=1)
        return _week_start(last_end), last_end, "last week"
    if key == "this_month":
        return today.replace(day=1), today, "this month"
    if key == "last_month":
        last_month_end = today.replace(day=1) - timedelta(days=1)
        return last_month_end.replace(day=1), last_month_end, "last month"
    if key == "this_year":
        return date(today.year, 1, 1), today, "this year"
    if key == "last_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), str(today.year - 1)
    if key == "all_time":
        return date(2000, 1, 1), today, "all time"
    raise ValueError(f"Unknown period keyword: {key}")  # pragma: no cover


@dataclass(frozen=True)
class PeriodMention:
    start: date
    end: date
    label: str
    position: int  # character offset where the phrase was found, for reading order


def find_periods(text: str, today: date) -> List[PeriodMention]:
    """Every recognizable date-range phrase in `text`, in reading order."""
    mentions: List[PeriodMention] = []

    for m in _MONTH_PATTERN.finditer(text):
        month_num = _MONTHS[m.group(1).lower()]
        if m.group(2):
            year = int(m.group(2))
        else:
            year = today.year
            candidate_start, _, _ = _month_period(month_num, year)
            if candidate_start > today:
                year -= 1  # "August" mentioned in March means last August, not a future one
        start, end, label = _month_period(month_num, year)
        mentions.append(PeriodMention(start, end, label, m.start()))

    for key, pattern in _KEYWORD_PATTERNS:
        m = pattern.search(text)
        if m:
            start, end, label = _resolve_keyword(key, today)
            mentions.append(PeriodMention(start, end, label, m.start()))

    mentions.sort(key=lambda pm: pm.position)
    return mentions


def _preceding_period(period: PeriodMention, today: date) -> Period:
    """Best-effort 'the period before that one', scaled to how big the
    named period is -- used only when a trend question names just one
    period (e.g. "why is this month lower") and needs something to compare
    it against.
    """
    span_days = (period.end - period.start).days
    if span_days <= 1:
        prev_day = period.start - timedelta(days=1)
        return prev_day, prev_day, prev_day.strftime("%d %b %Y")
    if span_days <= 7:
        prev_end = period.start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span_days)
        return prev_start, prev_end, "the previous week"
    if span_days <= 31:
        prev_month_end = period.start - timedelta(days=1)
        return prev_month_end.replace(day=1), prev_month_end, prev_month_end.strftime("%B %Y")
    return date(period.start.year - 1, 1, 1), date(period.start.year - 1, 12, 31), str(period.start.year - 1)


# --------------------------------------------------------------------------
# Intent + source detection (pure, no DB access)
# --------------------------------------------------------------------------

_TREND_PATTERN = re.compile(
    r"\bwhy\b|\bcompar(e|ison)\b|\bvs\.?\b|\bversus\b|\bthan\b|\bdifference\b|"
    r"\bhigher\b|\blower\b|\bchanged?\b|\btrend\b",
    re.IGNORECASE,
)

_METRIC_PATTERNS = [
    ("hours", re.compile(r"\bhours?\b|\bworked\b", re.IGNORECASE)),
    ("sessions", re.compile(r"\bsessions?\b", re.IGNORECASE)),
    ("rate", re.compile(r"\brate\b", re.IGNORECASE)),
    ("pending", re.compile(r"\bpending\b|\bunpaid\b|\bowe[ds]?\b|\bowing\b|\boutstanding\b", re.IGNORECASE)),
    ("paid", re.compile(r"\bpaid\b|\breceived\b|\bcollected\b", re.IGNORECASE)),
]


def detect_intent(text: str) -> str:
    if _TREND_PATTERN.search(text):
        return "trend"
    for name, pattern in _METRIC_PATTERNS:
        if pattern.search(text):
            return name
    return "earnings"  # sensible default -- most questions are implicitly about money


def match_source(text: str, source_names: List[str]) -> Optional[str]:
    """A known income source name mentioned in the text, or the
    pseudo-sources 'tuition' / 'other'. None means "all sources combined".
    """
    lowered = text.lower()
    for name in sorted(source_names, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name.lower()) + r"\b", lowered):
            return name
    if re.search(r"\btuition\b", lowered):
        return "tuition"
    if re.search(r"\bother\b", lowered):
        return "other"
    return None


# --------------------------------------------------------------------------
# DB-backed computation -- reuses earnings_query.py / analytics_service.py,
# the same functions the dashboard and reports already rely on.
# --------------------------------------------------------------------------

def _fmt(amount: Decimal, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


def _source_label(source: Optional[str]) -> str:
    if source is None:
        return "across all sources"
    if source == "tuition":
        return "from tuition"
    if source == "other":
        return "from other income/adjustments"
    return f"from {source}"


def _period_totals(user_id: int, start: date, end: date, source: Optional[str]) -> dict:
    sessions = eq.get_sessions(user_id, start, end)
    if source and source not in ("tuition", "other"):
        sessions = [s for s in sessions if s.source.name == source]
    khidmat_earnings = sum((Decimal(s.calculated_amount) for s in sessions), Decimal("0"))
    khidmat_minutes = sum(s.duration_minutes for s in sessions)
    khidmat_hours = Decimal(khidmat_minutes) / Decimal(60)

    invoices = eq.get_invoices_overlapping(user_id, start, end)
    tuition_facts = eq.invoice_payment_facts(invoices)
    tuition_paid, tuition_pending = paid_vs_pending(tuition_facts)
    tuition_expected = tuition_paid + tuition_pending

    adjustments = eq.get_adjustments(user_id, start, end)
    standalone = [a for a in adjustments if a.session_id is None and a.invoice_id is None]
    other_net = sum(
        (Decimal(a.amount) if a.type == "bonus" else -Decimal(a.amount) for a in standalone), Decimal("0")
    )

    return {
        "khidmat_sessions": sessions,
        "khidmat_earnings": khidmat_earnings,
        "khidmat_hours": khidmat_hours,
        "tuition_expected": tuition_expected,
        "tuition_paid": tuition_paid,
        "tuition_pending": tuition_pending,
        "other_net": other_net,
    }


def _single_source_total(totals: dict, source: str) -> Decimal:
    if source == "tuition":
        return totals["tuition_expected"]
    if source == "other":
        return totals["other_net"]
    return totals["khidmat_earnings"]


def _total_earned(totals: dict, source: Optional[str]) -> Decimal:
    if source is None:
        return totals["khidmat_earnings"] + totals["tuition_expected"] + totals["other_net"]
    return _single_source_total(totals, source)


def _fallback_answer() -> AssistantAnswer:
    examples = "\n".join(f"\u2022 {e}" for e in EXAMPLE_QUESTIONS)
    return AssistantAnswer("I didn't catch a specific question there. Try something like:\n" + examples)


def _answer_metric(user_id: int, intent: str, source: Optional[str], period: Period, currency: str) -> AssistantAnswer:
    start, end, label = period
    totals = _period_totals(user_id, start, end, source)

    if intent == "hours":
        if source in ("tuition", "other"):
            return AssistantAnswer(f"Hours are only tracked for Khidmat sessions, not {source}.")
        return AssistantAnswer(f"You worked {totals['khidmat_hours']:.2f} hours {_source_label(source)} in {label}.")

    if intent == "sessions":
        if source in ("tuition", "other"):
            return AssistantAnswer(f"Session counts only apply to Khidmat sessions, not {source}.")
        return AssistantAnswer(
            f"You logged {len(totals['khidmat_sessions'])} session(s) {_source_label(source)} in {label}."
        )

    if intent == "rate":
        if source in ("tuition", "other"):
            return AssistantAnswer("Effective hourly rate only applies to Khidmat sessions, not tuition or other income.")
        rate = effective_hourly_rate(totals["khidmat_earnings"], totals["khidmat_hours"])
        if rate is None:
            return AssistantAnswer(f"No hours logged {_source_label(source)} in {label} yet, so there's no effective rate to show.")
        return AssistantAnswer(f"Your effective hourly rate {_source_label(source)} in {label} was {_fmt(rate, currency)}/hr.")

    if intent == "pending":
        if source and source != "tuition":
            return AssistantAnswer(
                "Only tuition tracks a paid/pending status right now -- Khidmat sessions and other income "
                "don't have a separate pending state."
            )
        return AssistantAnswer(f"You have {_fmt(totals['tuition_pending'], currency)} pending from tuition in {label}.")

    if intent == "paid":
        if source == "tuition":
            return AssistantAnswer(f"You've received {_fmt(totals['tuition_paid'], currency)} from tuition in {label}.")
        if source and source != "tuition":
            return AssistantAnswer(
                f"{_source_label(source).capitalize()}, {_fmt(_single_source_total(totals, source), currency)} "
                f"was logged in {label} (Khidmat/other income aren't tracked as paid vs. pending -- only tuition is)."
            )
        paid_total = totals["khidmat_earnings"] + totals["tuition_paid"] + totals["other_net"]
        return AssistantAnswer(f"You've received {_fmt(paid_total, currency)} {_source_label(source)} in {label}.")

    # default: earnings
    total = _total_earned(totals, source)
    if source is None and total != 0:
        parts = []
        if totals["khidmat_earnings"] != 0:
            parts.append(f"{_fmt(totals['khidmat_earnings'], currency)} from Khidmat")
        if totals["tuition_expected"] != 0:
            parts.append(f"{_fmt(totals['tuition_expected'], currency)} from tuition")
        if totals["other_net"] != 0:
            parts.append(f"{_fmt(totals['other_net'], currency)} from other income")
        detail = f" ({', '.join(parts)}.)" if len(parts) > 1 else ""
        return AssistantAnswer(f"You earned {_fmt(total, currency)} in {label}.{detail}")
    return AssistantAnswer(f"You earned {_fmt(total, currency)} {_source_label(source)} in {label}.")


def _answer_trend(user_id: int, periods: List[PeriodMention], today: date, source: Optional[str], currency: str) -> AssistantAnswer:
    if len(periods) >= 2:
        subj_period: Period = (periods[0].start, periods[0].end, periods[0].label)
        base_period: Period = (periods[1].start, periods[1].end, periods[1].label)
    elif len(periods) == 1:
        subj_period = (periods[0].start, periods[0].end, periods[0].label)
        base_period = _preceding_period(periods[0], today)
    else:
        subj_period = _resolve_keyword("this_month", today)
        base_period = _resolve_keyword("last_month", today)

    subj_start, subj_end, subj_label = subj_period
    base_start, base_end, base_label = base_period

    subj_totals = _period_totals(user_id, subj_start, subj_end, source)
    base_totals = _period_totals(user_id, base_start, base_end, source)

    subj_total = _total_earned(subj_totals, source)
    base_total = _total_earned(base_totals, source)

    if subj_total == 0 and base_total == 0:
        return AssistantAnswer(f"There's no recorded income in either {subj_label} or {base_label} yet, so there's nothing to compare.")

    delta = subj_total - base_total
    if delta > 0:
        direction = "higher than"
    elif delta < 0:
        direction = "lower than"
    else:
        direction = "the same as"

    pct_note = ""
    if base_total != 0:
        pct = (delta / base_total) * Decimal("100")
        pct_note = f" ({pct:+.1f}%)"

    lines = [f"{subj_label} was {_fmt(abs(delta), currency)} {direction} {base_label}{pct_note}."]
    lines.append(f"{subj_label}: {_fmt(subj_total, currency)}. {base_label}: {_fmt(base_total, currency)}.")

    if source is None:
        movers = [
            ("Khidmat", subj_totals["khidmat_earnings"] - base_totals["khidmat_earnings"]),
            ("Tuition", subj_totals["tuition_expected"] - base_totals["tuition_expected"]),
            ("Other income", subj_totals["other_net"] - base_totals["other_net"]),
        ]
        movers = [mv for mv in movers if mv[1] != 0]
        if movers:
            biggest = max(movers, key=lambda mv: abs(mv[1]))
            move_dir = "up" if biggest[1] > 0 else "down"
            lines.append(f"The biggest mover was {biggest[0]}, {move_dir} {_fmt(abs(biggest[1]), currency)}.")

    return AssistantAnswer(" ".join(lines))


def answer_question(user_id: int, question: str, today: date, currency: str = "PKR") -> AssistantAnswer:
    """
    Main entry point. Always grounded in real DB numbers computed by the
    existing deterministic services -- this function only decides which
    question is being asked, never invents or estimates a figure.
    """
    text = (question or "").strip()
    if not text:
        return _fallback_answer()

    source_names = [s.name for s in IncomeSource.query.filter_by(user_id=user_id).all()]
    source = match_source(text, source_names)
    intent = detect_intent(text)
    periods = find_periods(text, today)

    if intent == "trend":
        return _answer_trend(user_id, periods, today, source, currency)

    period = (periods[0].start, periods[0].end, periods[0].label) if periods else _resolve_keyword("this_month", today)
    return _answer_metric(user_id, intent, source, period, currency)
