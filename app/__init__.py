import os
import sys

from flask import Flask, redirect, session, url_for
from flask_migrate import Migrate
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config
from app.extensions import db

csrf = CSRFProtect()
migrate = Migrate()


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    if app.config.get("IS_PRODUCTION") and app.config["SECRET_KEY"] == "dev-key-change-me-in-.env":
        raise RuntimeError(
            "Refusing to start in production with the default SECRET_KEY. "
            "Set a real SECRET_KEY environment variable (see .env.example)."
        )

    # Vercel (and most hosts) terminate HTTPS in front of the app and
    # forward plain HTTP internally with X-Forwarded-* headers. Without
    # this, Flask would think every request is insecure HTTP -- breaking
    # SESSION_COOKIE_SECURE and producing http:// OAuth redirect URIs.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    # instance_path (Flask's instance-relative-config folder) is only ever
    # needed for local SQLite dev/tests and local backups -- both already
    # redirect to /tmp when ON_SERVERLESS (see config.py). Vercel's deployed
    # filesystem is read-only outside /tmp, so creating this directory
    # there would crash create_app() on every cold start.
    if not app.config.get("ON_SERVERLESS"):
        os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    from app import models  # noqa: F401  (register models with SQLAlchemy metadata)
    from app.routes import assistant, auth, calendar, dashboard, goals, guest, rates, reports, sessions, settings, students

    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(assistant.bp)
    app.register_blueprint(sessions.bp)
    app.register_blueprint(rates.bp)
    app.register_blueprint(students.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(goals.bp)
    app.register_blueprint(calendar.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(guest.bp)

    @app.context_processor
    def inject_globals():
        from app.models import CalendarAccount, User

        calendar_account = None
        has_pin = False
        if session.get("user_id") and not session.get("is_guest"):
            calendar_account = CalendarAccount.query.filter_by(user_id=session["user_id"]).first()
            current_user = db.session.get(User, session["user_id"])
            has_pin = bool(current_user and current_user.pin_hash)

        return {
            "currency": app.config["CURRENCY"],
            "is_guest": session.get("is_guest", False),
            "sidebar_calendar_account": calendar_account,
            "sidebar_has_pin": has_pin,
        }

    @app.before_request
    def require_setup_first():
        from flask import request

        from app.models import User

        # Allow static assets, the setup/guest flows, and Google's webhook
        # (which has no session and is authenticated by its own channel-token
        # check, not by an owner existing) through untouched.
        if request.endpoint in (None, "static", "auth.setup", "guest.workspace", "guest.calculate", "calendar.webhook"):
            return None
        if User.query.first() is None and request.endpoint != "auth.setup":
            return redirect(url_for("auth.setup"))
        return None

    @app.before_request
    def enforce_pin_lock():
        from datetime import datetime, timezone

        from flask import request

        from app.models import User

        # Only ever applies to a real, logged-in owner session -- never guests,
        # never unauthenticated visitors (they're already blocked elsewhere).
        if not session.get("user_id") or session.get("is_guest"):
            return None
        if request.endpoint in (None, "static", "settings.unlock", "auth.logout"):
            return None

        user = db.session.get(User, session["user_id"])
        if user is None or not user.pin_hash:
            return None  # no PIN configured -- auto-lock is opt-in

        if session.get("locked"):
            return redirect(url_for("settings.unlock"))

        now = datetime.now(timezone.utc)
        last_activity_raw = session.get("last_activity")
        if last_activity_raw:
            last_activity = datetime.fromisoformat(last_activity_raw)
            idle_seconds = (now - last_activity).total_seconds()
            if idle_seconds / 60 > app.config["PIN_AUTO_LOCK_MINUTES"]:
                session["locked"] = True
                return redirect(url_for("settings.unlock"))
            # Only touch (and reissue) the session cookie roughly once a
            # minute, not on every single request. Writing to `session` on
            # every request forces a fresh Set-Cookie on every response --
            # harmless in isolation, but needless churn that's worth
            # avoiding, especially with several requests firing close
            # together (e.g. the dashboard's own AJAX calls).
            if idle_seconds >= 60:
                session["last_activity"] = now.isoformat()
        else:
            session["last_activity"] = now.isoformat()
        return None

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        from flask import flash, jsonify, redirect, request, url_for

        message = "That took a bit too long to submit, so it needed a refresh. Please try again."

        # AJAX endpoints (quick-add, assistant) send/expect JSON and already
        # know how to display a JSON {"error": ...} response -- redirecting
        # them would hand their fetch() call an HTML page instead.
        if request.is_json:
            return jsonify({"error": message}), 400

        # A CSRF mismatch on a regular form is usually just a stale page
        # (session expired, tab left open past the token's time limit, or
        # -- as audited here -- any of a few other reasons a form's
        # embedded token no longer matches the current session) rather
        # than an actual attack. Flask-WTF's default response is a raw 400
        # page with no way forward; redirecting back with a fresh token
        # lets the person simply try again instead of hitting a dead end.
        flash(message, "error")
        if request.referrer and request.referrer.startswith(request.host_url):
            return redirect(request.referrer)
        if session.get("user_id") and not session.get("is_guest"):
            return redirect(url_for("dashboard.index"))
        return redirect(url_for("auth.login"))

    with app.app_context():
        # Zero-config convenience for local dev, tests, and simple SQLite
        # deployments: auto-create any missing tables. Skipped specifically
        # when running `flask db ...` (Flask-Migrate/Alembic CLI commands)
        # so migrations can be generated/applied against a clean schema
        # instead of colliding with tables this would otherwise have
        # already created. Production deployments on PostgreSQL should run
        # `flask db upgrade` explicitly -- see README "Deployment".
        running_migration_cli = len(sys.argv) > 1 and sys.argv[0].endswith("flask") and sys.argv[1] == "db"
        if not running_migration_cli:
            db.create_all()

    return app
