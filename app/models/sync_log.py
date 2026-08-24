from datetime import datetime, timezone

from app.extensions import db

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


class SyncLog(db.Model):
    """
    One row per sync run (blueprint section 30/9): what happened, so
    syncing never fails silently and is always auditable.
    """

    __tablename__ = "sync_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    started_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    ended_at = db.Column(db.DateTime, nullable=True)

    events_found = db.Column(db.Integer, nullable=False, default=0)
    events_imported = db.Column(db.Integer, nullable=False, default=0)
    events_updated = db.Column(db.Integer, nullable=False, default=0)
    events_skipped = db.Column(db.Integer, nullable=False, default=0)
    events_needing_review = db.Column(db.Integer, nullable=False, default=0)
    events_deleted_upstream = db.Column(db.Integer, nullable=False, default=0)

    status = db.Column(db.String(20), nullable=False, default=STATUS_SUCCESS)
    error_text = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<SyncLog user={self.user_id} {self.started_at} status={self.status}>"
