from app.extensions import db

# Valid modes for a time-based income source
MODE_FIXED_HOURS = "FIXED_HOURS"
MODE_EXACT_TIME = "EXACT_TIME"


class IncomeSource(db.Model):
    __tablename__ = "income_sources"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)  # e.g. SBHS, SGHS
    category = db.Column(db.String(40), nullable=False, default="khidmat")
    mode = db.Column(db.String(20), nullable=False)  # FIXED_HOURS | EXACT_TIME
    active = db.Column(db.Boolean, nullable=False, default=True)

    rate_history = db.relationship("RateHistory", backref="source", lazy=True, cascade="all, delete-orphan")
    sessions = db.relationship("Session", backref="source", lazy=True)

    __table_args__ = (db.UniqueConstraint("user_id", "name", name="uq_user_source_name"),)

    def __repr__(self):
        return f"<IncomeSource {self.name} ({self.mode})>"
