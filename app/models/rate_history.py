from app.extensions import db


class RateHistory(db.Model):
    __tablename__ = "rate_history"

    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(db.Integer, db.ForeignKey("income_sources.id"), nullable=False, index=True)
    rate = db.Column(db.Numeric(12, 4), nullable=False)
    effective_from = db.Column(db.Date, nullable=False)
    effective_to = db.Column(db.Date, nullable=True)  # NULL = open-ended / current
    notes = db.Column(db.String(255), nullable=True)

    def __repr__(self):
        return f"<RateHistory source={self.source_id} rate={self.rate} {self.effective_from}..{self.effective_to or 'open'}>"
