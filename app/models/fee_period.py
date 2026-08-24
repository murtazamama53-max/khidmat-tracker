from app.extensions import db


class FeePeriod(db.Model):
    __tablename__ = "fee_periods"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    effective_from = db.Column(db.Date, nullable=False)
    effective_to = db.Column(db.Date, nullable=True)  # NULL = open-ended
    due_day = db.Column(db.Integer, nullable=True)  # day-of-month fee is due

    def __repr__(self):
        return f"<FeePeriod student={self.student_id} amount={self.amount} {self.effective_from}..{self.effective_to or 'open'}>"
