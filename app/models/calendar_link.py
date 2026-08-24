from datetime import datetime, timezone

from app.extensions import db


class CalendarLink(db.Model):
    __tablename__ = "calendar_links"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("sessions.id"), nullable=False)
    provider = db.Column(db.String(30), nullable=False, default="google")
    calendar_id = db.Column(db.String(255), nullable=False)
    event_id = db.Column(db.String(255), nullable=False, index=True)
    occurrence_id = db.Column(db.String(255), nullable=True)
    title = db.Column(db.String(255), nullable=True)  # event summary, for review/traceability display
    source_deleted = db.Column(db.Boolean, nullable=False, default=False)
    synced_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("provider", "calendar_id", "event_id", "occurrence_id", name="uq_calendar_event"),
    )
