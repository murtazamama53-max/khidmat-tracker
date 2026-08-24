from datetime import datetime, timezone

from app.extensions import db

TYPE_BONUS = "bonus"
TYPE_DEDUCTION = "deduction"


class Adjustment(db.Model):
    """
    A bonus, deduction, or manual adjustment. Attaches to a session OR an
    invoice OR neither (a standalone "other income" / manual entry).
    Never mutates the original calculated_amount it may relate to
    (blueprint section 15: "Never silently alter the original calculation").
    """

    __tablename__ = "adjustments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    session_id = db.Column(db.Integer, db.ForeignKey("sessions.id"), nullable=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=True)
    type = db.Column(db.String(20), nullable=False)  # bonus | deduction
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Adjustment {self.type} {self.amount} session={self.session_id} invoice={self.invoice_id}>"
