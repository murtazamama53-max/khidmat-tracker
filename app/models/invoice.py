from datetime import datetime, timezone

from app.extensions import db

STATUS_PENDING = "pending"
STATUS_PARTIAL = "partial"
STATUS_PAID = "paid"


class Invoice(db.Model):
    """
    A concrete billing period for a student, e.g. "Ahmed, August 2026".
    The fee `amount` is snapshotted from the FeePeriod that was applicable
    when the invoice was generated -- it never changes retroactively if
    the student's fee schedule is edited later (same snapshot principle
    as Session.applied_rate). Status is derived from payments received
    against it, plus whether its due date has passed (see
    services/tuition_service.py:invoice_display_status).
    """

    __tablename__ = "invoices"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False, index=True)

    period_start = db.Column(db.Date, nullable=False, index=True)
    period_end = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    due_date = db.Column(db.Date, nullable=True)

    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    notes = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    adjustments = db.relationship("Adjustment", backref="invoice", lazy=True, cascade="all, delete-orphan")
    student = db.relationship("Student", backref="invoices")

    def __repr__(self):
        return f"<Invoice student={self.student_id} {self.period_start}..{self.period_end} amount={self.amount}>"
