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

    # Incremental sync (Google's syncToken mechanism). Once set, the next
    # sync requests only what changed since this token instead of
    # re-fetching the whole date window. Cleared and a full resync
    # triggered automatically if Google reports it expired (HTTP 410).
    sync_token = db.Column(db.Text, nullable=True)

    # Push notifications (Google's watch()/channels API). A channel is
    # registered against a specific calendar and expires (~1 week for
    # Calendar resources) -- watch_expiration lets sync logic renew it
    # proactively rather than silently going stale. watch_channel_token is
    # a locally-generated secret handed to Google at watch-creation time;
    # Google echoes it back on every notification, and its exact presence
    # is how an incoming webhook call is verified as genuinely tied to
    # this account's channel (see routes/calendar.py webhook()).
    watch_channel_id = db.Column(db.String(64), nullable=True, unique=True, index=True)
    watch_resource_id = db.Column(db.String(255), nullable=True)
    watch_channel_token = db.Column(db.String(64), nullable=True)
    watch_expiration = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<CalendarAccount user={self.user_id} email={self.google_email}>"
