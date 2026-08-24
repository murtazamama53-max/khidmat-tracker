from datetime import datetime, timezone

from app.extensions import db

STATUS_COMPLETED = "completed"
STATUS_DRAFT = "draft"
STATUS_SOURCE_DELETED = "source_deleted"


class Session(db.Model):
    """
    A single time-based earning record (SBHS/SGHS style).
    Two components from one quick-add capture (e.g. "Sbhs(7) & sghs(5-6:20)")
    share the same capture_event_id but remain independent rows so
    source-specific history is never flattened (blueprint section 10).
    """

    __tablename__ = "sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    source_id = db.Column(db.Integer, db.ForeignKey("income_sources.id"), nullable=False)
    capture_event_id = db.Column(db.String(36), nullable=True, index=True)

    date = db.Column(db.Date, nullable=False, index=True)
    mode = db.Column(db.String(20), nullable=False)  # FIXED_HOURS | EXACT_TIME
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    quantity_hours = db.Column(db.Numeric(8, 4), nullable=True)  # set for FIXED_HOURS mode, e.g. Sbhs(7) -> 7.0000

    duration_minutes = db.Column(db.Integer, nullable=False)
    decimal_hours = db.Column(db.Numeric(10, 6), nullable=False)

    applied_rate = db.Column(db.Numeric(12, 4), nullable=False)
    calculated_amount = db.Column(db.Numeric(12, 2), nullable=False)

    status = db.Column(db.String(20), nullable=False, default=STATUS_COMPLETED)
    notes = db.Column(db.Text, nullable=True)
    raw_input = db.Column(db.String(255), nullable=True)  # what the user actually typed

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    calendar_link = db.relationship("CalendarLink", backref="session", uselist=False, cascade="all, delete-orphan")
    adjustments = db.relationship("Adjustment", backref="session", lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Session {self.date} source={self.source_id} amount={self.calculated_amount}>"
