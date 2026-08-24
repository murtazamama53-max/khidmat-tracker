from app.extensions import db


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    note = db.Column(db.String(255), nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)

    fee_periods = db.relationship("FeePeriod", backref="student", lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Student {self.name}>"
