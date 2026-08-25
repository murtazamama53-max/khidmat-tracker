"""
Application configuration.

All secrets are loaded from environment variables. Never hard-code
secrets here. See .env.example for the variables this app expects.
"""
import os
from datetime import timedelta


def _normalize_database_url(url: str) -> str:
    # Some managed Postgres providers still hand out "postgres://", which
    # SQLAlchemy 1.4+ / psycopg2 reject outright -- normalize to the scheme
    # they actually require.
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


# Vercel (and most serverless hosts) ship a read-only deployment bundle;
# only /tmp is writable, and it is not guaranteed to persist between
# invocations. SQLite is fine for local development but must not be relied
# on in that kind of production environment -- set DATABASE_URL to a
# managed PostgreSQL instance instead (see README "Deployment").
_ON_SERVERLESS = bool(os.environ.get("VERCEL"))


class Config:
    ENV = os.environ.get("FLASK_ENV", "development")
    IS_PRODUCTION = ENV == "production"
    # Exposed on the config object (not just the module-private flag above)
    # so app/__init__.py can check app.config["ON_SERVERLESS"] without
    # importing a private name across modules.
    ON_SERVERLESS = _ON_SERVERLESS

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-key-change-me-in-.env")

    basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    _default_sqlite_dir = "/tmp" if _ON_SERVERLESS else os.path.join(basedir, "instance")
    _default_database_url = f"sqlite:///{os.path.join(_default_sqlite_dir, 'khidmat.db')}"
    # `or` (not a two-arg .get()) so DATABASE_URL="" -- present but blank,
    # exactly what .env.example documents for "use local SQLite" -- falls
    # back to the default instead of SQLAlchemy failing to parse "".
    SQLALCHEMY_DATABASE_URI = _normalize_database_url(os.environ.get("DATABASE_URL") or _default_database_url)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # pool_pre_ping avoids "server closed the connection unexpectedly"
    # errors against managed Postgres instances that idle-close connections
    # between infrequent serverless invocations.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Google Calendar OAuth (Phase 4)
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "")

    # Regional / display defaults (section 40 of the blueprint)
    CURRENCY = "PKR"
    TIMEZONE = "Asia/Karachi"
    DATE_FORMAT = "%d %b %Y"
    ROUNDING_ENABLED = False
    MINIMUM_BILLABLE_MINUTES = 0  # 0 = off

    # Session / security
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=45)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Flip on when serving over HTTPS in production
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"

    # If the owner has set a PIN, auto-lock (require PIN re-entry, without
    # a full logout) after this many minutes of inactivity.
    PIN_AUTO_LOCK_MINUTES = int(os.environ.get("PIN_AUTO_LOCK_MINUTES", "10"))

    # Where encrypted backup files are written (never inside app/static/).
    # On serverless this is necessarily ephemeral -- see README for why
    # backups should be downloaded immediately rather than relied on as
    # durable storage when deployed that way.
    BACKUP_DIR = os.environ.get("BACKUP_DIR") or (
        os.path.join("/tmp", "backups") if _ON_SERVERLESS else os.path.join(basedir, "instance", "backups")
    )


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
