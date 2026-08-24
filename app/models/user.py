from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False, default="Murtaza")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="owner")  # 'owner' only (guest is sessionless)
    pin_hash = db.Column(db.String(255), nullable=True)  # optional app-lock PIN, separate from the login password
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def set_password(self, raw_password: str) -> None:
        # scrypt (werkzeug default) is a strong, salted KDF -- never store plaintext.
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    def set_pin(self, raw_pin: str) -> None:
        self.pin_hash = generate_password_hash(raw_pin)

    def check_pin(self, raw_pin: str) -> bool:
        if not self.pin_hash:
            return False
        return check_password_hash(self.pin_hash, raw_pin)

    def clear_pin(self) -> None:
        self.pin_hash = None

    def __repr__(self):
        return f"<User {self.email}>"
