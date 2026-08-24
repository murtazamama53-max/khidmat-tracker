from datetime import datetime, timezone

from app.extensions import db

STATUS_PENDING = "pending"
STATUS_RESOLVED = "resolved"
STATUS_IGNORED = "ignored"


class CalendarDraft(db.Model):
    """
    A calendar event that couldn't be auto-mapped to a known income
    source. Sits here for the owner to review and either resolve (map to
    a source, creating the session) or ignore -- never silently guessed
    (blueprint section 5) and never silently dropped (section 10:
    "Never fail silently").
    """

    __tablename__ = "calendar_drafts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    calendar_id = db.Column(db.String(255), nullable=False)
    event_id = db.Column(db.String(255), nullable=False)
    occurrence_id = db.Column(db.String(255), nullable=True)
    title = db.Column(db.String(255), nullable=True)

    event_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)

    reason = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("user_id", "calendar_id", "event_id", "occurrence_id", name="uq_calendar_draft_event"),
    )

    def __repr__(self):
        return f"<CalendarDraft '{self.title}' {self.event_date} status={self.status}>"
