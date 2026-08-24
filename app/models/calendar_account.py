from datetime import datetime, timezone

from app.extensions import db


class CalendarAccount(db.Model):
    """
    One connected Google account per owner. The refresh token is the only
    long-lived secret we store, and it is kept encrypted at rest via
    services/token_crypto.py -- never stored or displayed in plaintext,
    and never sent to the frontend (blueprint sections 5/10/12: "Never
    store the Google password", "tokens encrypted at rest", "never
    expose access tokens to the frontend").
    """

    __tablename__ = "calendar_accounts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)

    google_email = db.Column(db.String(255), nullable=True)
    calendar_id = db.Column(db.String(255), nullable=False, default="primary")
    encrypted_refresh_token = db.Column(db.Text, nullable=False)

    connected_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_sync_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<CalendarAccount user={self.user_id} email={self.google_email}>"
