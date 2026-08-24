from datetime import datetime, timezone

from app.extensions import db


class Goal(db.Model):
    """
    One row per user: the standing monthly targets shown as progress bars
    on the dashboard (blueprint section 27). Simple by design -- editing
    the target changes it going forward, it doesn't rewrite history.
    """

    __tablename__ = "goals"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)

    monthly_income_target = db.Column(db.Numeric(12, 2), nullable=True)
    monthly_hours_target = db.Column(db.Numeric(8, 2), nullable=True)
    monthly_sessions_target = db.Column(db.Integer, nullable=True)

    updated_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self):
        return f"<Goal user={self.user_id} income={self.monthly_income_target} hours={self.monthly_hours_target}>"
