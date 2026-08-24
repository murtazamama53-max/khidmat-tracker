from datetime import datetime, timezone

from app.extensions import db

STATUS_PAID = "paid"
STATUS_PENDING = "pending"
STATUS_PARTIAL = "partial"
STATUS_OVERDUE = "overdue"


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    # source_type: 'invoice' | 'session' | 'other'
    source_type = db.Column(db.String(30), nullable=False)
    source_id = db.Column(db.Integer, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    method = db.Column(db.String(40), nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Payment {self.source_type}:{self.source_id} {self.amount} {self.status}>"
