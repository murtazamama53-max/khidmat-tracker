from datetime import datetime, timezone

from app.extensions import db


class CalendarMapping(db.Model):
    """
    A rule for auto-mapping calendar events to an income source, e.g.
    "any event whose title contains 'SGHS' -> the SGHS source". Events
    that don't match any active rule become CalendarDraft rows instead
    of being guessed (blueprint section 5: "Do not guess unknown sources").
    """

    __tablename__ = "calendar_mappings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    calendar_id = db.Column(db.String(255), nullable=True)  # NULL = matches any connected calendar
    title_pattern = db.Column(db.String(255), nullable=False)  # case-insensitive substring match
    source_id = db.Column(db.Integer, db.ForeignKey("income_sources.id"), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    source = db.relationship("IncomeSource")

    def __repr__(self):
        return f"<CalendarMapping '{self.title_pattern}' -> source={self.source_id}>"
