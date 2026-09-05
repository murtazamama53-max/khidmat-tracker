from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from app.extensions import db
from app.models import Adjustment, AuditLog, FeePeriod, Invoice, Payment, Student
from app.routes.auth import owner_only
from app.services.date_range import app_today
from app.services.tuition_service import (
    FeePeriodRange,
    FeeResolutionError,
    OverlappingFeePeriodError,
    close_previous_open_period,
    invoice_display_status,
    resolve_fee,
    validate_no_overlap,
)

bp = Blueprint("students", __name__)


def _fee_periods_for(student_id: int) -> list[FeePeriodRange]:
    rows = FeePeriod.query.filter_by(student_id=student_id).all()
    return [
        FeePeriodRange(id=r.id, student_id=r.student_id, amount=Decimal(r.amount), effective_from=r.effective_from, effective_to=r.effective_to)
        for r in rows
    ]


def _invoice_summary(invoice: Invoice):
    """Returns (total_paid, remaining, display_status, adj_total, payments, adjustments) for one invoice."""
    payments = Payment.query.filter_by(source_type="invoice", source_id=invoice.id).all()
    total_paid = sum((Decimal(p.amount) for p in payments), Decimal("0"))
    remaining = Decimal(invoice.amount) - total_paid
    adjustments = Adjustment.query.filter_by(invoice_id=invoice.id).all()
    adj_total = sum(
        (Decimal(a.amount) if a.type == "bonus" else -Decimal(a.amount) for a in adjustments), Decimal("0")
    )
    display_status = invoice_display_status(Decimal(invoice.amount), total_paid, invoice.due_date, app_today(current_app.config["TIMEZONE"]))
    return total_paid, remaining, display_status, adj_total, payments, adjustments


@bp.route("/students")
@owner_only
def list_students():
    user_id = session["user_id"]
    students = Student.query.filter_by(user_id=user_id).order_by(Student.name).all()
    return render_template("students.html", students=students)


@bp.route("/students/add", methods=["POST"])
@owner_only
def add_student():
    user_id = session["user_id"]
    name = request.form.get("name", "").strip()
    note = request.form.get("note", "").strip() or None

    if not name:
        flash("Please provide a student name.", "error")
        return redirect(url_for("students.list_students"))

    student = Student(user_id=user_id, name=name, note=note, active=True)
    db.session.add(student)
    db.session.flush()
    db.session.add(AuditLog(user_id=user_id, action="student_added", entity_type="student", entity_id=student.id))
    db.session.commit()
    flash(f"Student '{name}' added. Add a fee period to start billing.", "success")
    return redirect(url_for("students.detail", student_id=student.id))


@bp.route("/students/<int:student_id>")
@owner_only
def detail(student_id):
    user_id = session["user_id"]
    student = Student.query.filter_by(id=student_id, user_id=user_id).first_or_404()
    fee_periods = FeePeriod.query.filter_by(student_id=student.id).order_by(FeePeriod.effective_from.desc()).all()
    invoices = Invoice.query.filter_by(student_id=student.id).order_by(Invoice.period_start.desc()).all()

    invoice_rows = []
    for inv in invoices:
        total_paid, remaining, display_status, adj_total, payments, adjustments = _invoice_summary(inv)
        invoice_rows.append(
            {
                "invoice": inv,
                "total_paid": total_paid,
                "remaining": remaining,
                "display_status": display_status,
                "adj_total": adj_total,
                "payments": payments,
                "adjustments": adjustments,
            }
        )

    return render_template("student_detail.html", student=student, fee_periods=fee_periods, invoice_rows=invoice_rows, today=app_today(current_app.config["TIMEZONE"]))


@bp.route("/students/<int:student_id>/toggle-active", methods=["POST"])
@owner_only
def toggle_active(student_id):
    user_id = session["user_id"]
    student = Student.query.filter_by(id=student_id, user_id=user_id).first_or_404()
    student.active = not student.active
    db.session.add(
        AuditLog(user_id=user_id, action="student_status_changed", entity_type="student", entity_id=student.id)
    )
    db.session.commit()
    flash(f"{student.name} marked {'active' if student.active else 'inactive'}.", "success")
    return redirect(url_for("students.detail", student_id=student.id))


@bp.route("/students/<int:student_id>/fee-periods/add", methods=["POST"])
@owner_only
def add_fee_period(student_id):
    user_id = session["user_id"]
    student = Student.query.filter_by(id=student_id, user_id=user_id).first_or_404()

    amount_str = request.form.get("amount", "")
    from_str = request.form.get("effective_from", "")
    to_str = request.form.get("effective_to", "").strip()
    due_day = request.form.get("due_day", type=int)

    try:
        amount = Decimal(amount_str)
        if amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        flash("Fee amount must be a positive number.", "error")
        return redirect(url_for("students.detail", student_id=student_id))

    try:
        effective_from = datetime.strptime(from_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid effective-from date.", "error")
        return redirect(url_for("students.detail", student_id=student_id))

    effective_to = None
    if to_str:
        try:
            effective_to = datetime.strptime(to_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid effective-to date.", "error")
            return redirect(url_for("students.detail", student_id=student_id))
        if effective_to < effective_from:
            flash("Effective-to date cannot be before effective-from date.", "error")
            return redirect(url_for("students.detail", student_id=student_id))

    existing_periods = _fee_periods_for(student.id)
    new_period = FeePeriodRange(id=None, student_id=student.id, amount=amount, effective_from=effective_from, effective_to=effective_to)

    closure = close_previous_open_period(new_period, existing_periods)
    if closure is not None:
        row = db.session.get(FeePeriod, closure.id)
        row.effective_to = closure.effective_to
        existing_periods = [p if p.id != closure.id else closure for p in existing_periods]

    try:
        validate_no_overlap(new_period, existing_periods)
    except OverlappingFeePeriodError as e:
        flash(str(e), "error")
        return redirect(url_for("students.detail", student_id=student_id))

    row = FeePeriod(student_id=student.id, amount=amount, effective_from=effective_from, effective_to=effective_to, due_day=due_day)
    db.session.add(row)
    db.session.flush()
    db.session.add(AuditLog(user_id=user_id, action="fee_period_added", entity_type="fee_period", entity_id=row.id))
    db.session.commit()
    flash(f"Fee of Rs. {amount} set for {student.name} starting {effective_from.strftime('%d %b %Y')}.", "success")
    return redirect(url_for("students.detail", student_id=student_id))


@bp.route("/students/<int:student_id>/invoices/add", methods=["POST"])
@owner_only
def add_invoice(student_id):
    """
    Generates a billing period. The fee is resolved from the student's
    fee-period history for the invoice's period_start date and snapshotted
    onto the invoice -- editing the fee schedule later never changes an
    already-generated invoice (same principle as session.applied_rate).
    """
    user_id = session["user_id"]
    student = Student.query.filter_by(id=student_id, user_id=user_id).first_or_404()

    start_str = request.form.get("period_start", "")
    end_str = request.form.get("period_end", "")
    due_str = request.form.get("due_date", "").strip()

    try:
        period_start = datetime.strptime(start_str, "%Y-%m-%d").date()
        period_end = datetime.strptime(end_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid period dates.", "error")
        return redirect(url_for("students.detail", student_id=student_id))

    if period_end < period_start:
        flash("Period end cannot be before period start.", "error")
        return redirect(url_for("students.detail", student_id=student_id))

    due_date = None
    if due_str:
        try:
            due_date = datetime.strptime(due_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid due date.", "error")
            return redirect(url_for("students.detail", student_id=student_id))

    try:
        fee_period = resolve_fee(period_start, _fee_periods_for(student.id))
    except FeeResolutionError as e:
        flash(str(e), "error")
        return redirect(url_for("students.detail", student_id=student_id))

    invoice = Invoice(
        user_id=user_id,
        student_id=student.id,
        period_start=period_start,
        period_end=period_end,
        amount=fee_period.amount,
        due_date=due_date,
        status="pending",
    )
    db.session.add(invoice)
    db.session.flush()
    db.session.add(AuditLog(user_id=user_id, action="invoice_generated", entity_type="invoice", entity_id=invoice.id))
    db.session.commit()
    flash(f"Invoice generated for {student.name}: Rs. {fee_period.amount}.", "success")
    return redirect(url_for("students.detail", student_id=student_id))


@bp.route("/invoices/<int:invoice_id>/payments/add", methods=["POST"])
@owner_only
def add_payment(invoice_id):
    user_id = session["user_id"]
    invoice = Invoice.query.filter_by(id=invoice_id, user_id=user_id).first_or_404()

    amount_str = request.form.get("amount", "")
    method = request.form.get("method", "").strip() or None
    paid_str = request.form.get("paid_at", "").strip()

    try:
        amount = Decimal(amount_str)
        if amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        flash("Payment amount must be a positive number.", "error")
        return redirect(url_for("students.detail", student_id=invoice.student_id))

    paid_at = datetime.now(timezone.utc)
    if paid_str:
        try:
            paid_at = datetime.strptime(paid_str, "%Y-%m-%d")
        except ValueError:
            flash("Invalid payment date.", "error")
            return redirect(url_for("students.detail", student_id=invoice.student_id))

    total_paid, remaining, _, _, _, _ = _invoice_summary(invoice)
    if amount > remaining:
        flash(
            f"Payment of Rs. {amount} exceeds the remaining balance of Rs. {remaining}. "
            "Record the correct remaining amount, or adjust the invoice.",
            "error",
        )
        return redirect(url_for("students.detail", student_id=invoice.student_id))

    payment = Payment(source_type="invoice", source_id=invoice.id, amount=amount, status="paid", method=method, paid_at=paid_at)
    db.session.add(payment)
    db.session.flush()

    # Recompute and persist the invoice's stored status from actual payments received.
    new_total_paid = total_paid + amount
    invoice.status = invoice_display_status(Decimal(invoice.amount), new_total_paid, invoice.due_date, app_today(current_app.config["TIMEZONE"]))

    db.session.add(AuditLog(user_id=user_id, action="payment_added", entity_type="payment", entity_id=payment.id))
    db.session.commit()
    flash(f"Payment of Rs. {amount} recorded.", "success")
    return redirect(url_for("students.detail", student_id=invoice.student_id))


@bp.route("/adjustments/add", methods=["POST"])
@owner_only
def add_adjustment():
    """
    A bonus/deduction/manual adjustment against a session, an invoice, or
    neither (a standalone "other income" entry). Always requires a reason
    and never mutates the record it may relate to (blueprint section 15).
    """
    from app.models import Session as SessionModel

    user_id = session["user_id"]
    adj_type = request.form.get("type", "").strip()
    amount_str = request.form.get("amount", "")
    reason = request.form.get("reason", "").strip()
    date_str = request.form.get("date", "")
    session_id = request.form.get("session_id", type=int)
    invoice_id = request.form.get("invoice_id", type=int)
    redirect_to = request.form.get("redirect_to") or url_for("dashboard.index")

    if adj_type not in ("bonus", "deduction"):
        flash("Adjustment type must be bonus or deduction.", "error")
        return redirect(redirect_to)
    if not reason:
        flash("An adjustment requires a reason.", "error")
        return redirect(redirect_to)

    try:
        amount = Decimal(amount_str)
        if amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        flash("Adjustment amount must be a positive number.", "error")
        return redirect(redirect_to)

    try:
        adj_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else app_today(current_app.config["TIMEZONE"])
    except ValueError:
        flash("Invalid date.", "error")
        return redirect(redirect_to)

    if session_id is not None:
        owned = SessionModel.query.filter_by(id=session_id, user_id=user_id).first()
        if owned is None:
            flash("Invalid session.", "error")
            return redirect(redirect_to)
    if invoice_id is not None:
        owned = Invoice.query.filter_by(id=invoice_id, user_id=user_id).first()
        if owned is None:
            flash("Invalid invoice.", "error")
            return redirect(redirect_to)

    adjustment = Adjustment(
        user_id=user_id,
        session_id=session_id,
        invoice_id=invoice_id,
        type=adj_type,
        amount=amount,
        reason=reason,
        date=adj_date,
    )
    db.session.add(adjustment)
    db.session.flush()
    db.session.add(AuditLog(user_id=user_id, action="adjustment_added", entity_type="adjustment", entity_id=adjustment.id))
    db.session.commit()
    flash(f"{adj_type.title()} of Rs. {amount} recorded.", "success")
    return redirect(redirect_to)
