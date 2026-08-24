"""
Fetches real session/invoice/adjustment rows for a user + date range and
shapes them into the plain data structures analytics_service expects.
Kept separate from analytics_service so that module can stay pure/DB-free
and independently testable.
"""
from datetime import date
from decimal import Decimal
from typing import List

from app.models import Adjustment, Invoice, Payment, Student
from app.models import Session as SessionModel
from app.services.analytics_service import EarningEvent, SessionFact


def get_sessions(user_id: int, start: date, end: date) -> List[SessionModel]:
    return (
        SessionModel.query.filter(
            SessionModel.user_id == user_id,
            SessionModel.date >= start,
            SessionModel.date <= end,
        )
        .order_by(SessionModel.date.desc())
        .all()
    )


def get_invoices_overlapping(user_id: int, start: date, end: date) -> List[Invoice]:
    student_ids = [s.id for s in Student.query.filter_by(user_id=user_id).all()]
    if not student_ids:
        return []
    return (
        Invoice.query.filter(
            Invoice.student_id.in_(student_ids),
            Invoice.period_start <= end,
            Invoice.period_end >= start,
        )
        .order_by(Invoice.period_start.desc())
        .all()
    )


def get_adjustments(user_id: int, start: date, end: date) -> List[Adjustment]:
    return Adjustment.query.filter(
        Adjustment.user_id == user_id,
        Adjustment.date >= start,
        Adjustment.date <= end,
    ).all()


def invoice_total_paid(invoice_id: int) -> Decimal:
    payments = Payment.query.filter_by(source_type="invoice", source_id=invoice_id).all()
    return sum((Decimal(p.amount) for p in payments), Decimal("0"))


def to_session_facts(sessions: List[SessionModel]) -> List[SessionFact]:
    return [
        SessionFact(date_=s.date, duration_minutes=s.duration_minutes, amount=Decimal(s.calculated_amount), source_name=s.source.name)
        for s in sessions
    ]


def to_earning_events(
    sessions: List[SessionModel], invoices: List[Invoice], adjustments: List[Adjustment]
) -> List[EarningEvent]:
    events = [EarningEvent(date_=s.date, amount=Decimal(s.calculated_amount), category="Khidmat") for s in sessions]
    events += [EarningEvent(date_=inv.period_start, amount=Decimal(inv.amount), category="Tuition") for inv in invoices]
    for a in adjustments:
        amount = Decimal(a.amount) if a.type == "bonus" else -Decimal(a.amount)
        category = "Other" if (a.session_id is None and a.invoice_id is None) else ("Khidmat" if a.session_id else "Tuition")
        events.append(EarningEvent(date_=a.date, amount=amount, category=category))
    return events


def invoice_payment_facts(invoices: List[Invoice]) -> List[dict]:
    return [{"amount": Decimal(inv.amount), "paid": invoice_total_paid(inv.id)} for inv in invoices]
