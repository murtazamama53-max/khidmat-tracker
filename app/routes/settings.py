from datetime import datetime, timezone

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, session, url_for

from app.extensions import db
from app.models import AuditLog, User
from app.routes.auth import owner_only
from app.services import backup_service
from app.services.backup_service import BackupError, InvalidBackupFilenameError

bp = Blueprint("settings", __name__)

_NON_SQLITE_BACKUP_MESSAGE = (
    "Backups only work against the local SQLite database. This deployment is using an external "
    "database (PostgreSQL), so back it up using your hosting provider's own backup/export tools instead."
)


def _sqlite_db_path():
    """
    Returns the local file path for the configured database, or None if
    it isn't SQLite. Deliberately never returns the raw
    SQLALCHEMY_DATABASE_URI for a non-SQLite database -- that string can
    contain a username/password (e.g. postgresql://user:pass@host/db) and
    must never be passed to backup_service, logged, or shown to the user.
    """
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if not uri.startswith("sqlite:///"):
        return None
    return uri.replace("sqlite:///", "")


@bp.route("/settings")
@owner_only
def index():
    user = db.session.get(User, session["user_id"])
    using_sqlite = _sqlite_db_path() is not None
    backups = backup_service.list_backups(current_app.config["BACKUP_DIR"]) if using_sqlite else []
    return render_template("settings.html", user=user, backups=backups, using_sqlite=using_sqlite)


@bp.route("/settings/pin/set", methods=["POST"])
@owner_only
def set_pin():
    user = db.session.get(User, session["user_id"])
    pin = request.form.get("pin", "").strip()
    confirm = request.form.get("confirm_pin", "").strip()

    if not pin.isdigit() or not (4 <= len(pin) <= 8):
        flash("PIN must be 4-8 digits.", "error")
        return redirect(url_for("settings.index"))
    if pin != confirm:
        flash("PINs do not match.", "error")
        return redirect(url_for("settings.index"))

    user.set_pin(pin)
    db.session.add(AuditLog(user_id=user.id, action="pin_set", entity_type="user", entity_id=user.id))
    db.session.commit()
    flash("App-lock PIN set.", "success")
    return redirect(url_for("settings.index"))


@bp.route("/settings/pin/remove", methods=["POST"])
@owner_only
def remove_pin():
    user = db.session.get(User, session["user_id"])
    user.clear_pin()
    db.session.add(AuditLog(user_id=user.id, action="pin_removed", entity_type="user", entity_id=user.id))
    db.session.commit()
    flash("App-lock PIN removed.", "success")
    return redirect(url_for("settings.index"))


@bp.route("/lock", methods=["POST"])
@owner_only
def lock():
    user = db.session.get(User, session["user_id"])
    if not user.pin_hash:
        flash("Set an app-lock PIN first.", "error")
        return redirect(url_for("settings.index"))
    session["locked"] = True
    return redirect(url_for("settings.unlock"))


@bp.route("/unlock", methods=["GET", "POST"])
def unlock():
    # Deliberately NOT @owner_only: a locked session must still be able to
    # reach this one page to unlock itself. It manually re-checks that a
    # real owner session exists below, so it can't be used to bypass login.
    if not session.get("user_id") or session.get("is_guest"):
        return redirect(url_for("auth.login"))

    user = db.session.get(User, session["user_id"])
    if not session.get("locked"):
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        pin = request.form.get("pin", "").strip()
        if user.check_pin(pin):
            session["locked"] = False
            session["last_activity"] = datetime.now(timezone.utc).isoformat()
            return redirect(url_for("dashboard.index"))
        flash("Incorrect PIN.", "error")

    return render_template("unlock.html")


@bp.route("/settings/backups/create", methods=["POST"])
@owner_only
def create_backup():
    user_id = session["user_id"]
    db_path = _sqlite_db_path()
    if db_path is None:
        flash(_NON_SQLITE_BACKUP_MESSAGE, "error")
        return redirect(url_for("settings.index"))
    try:
        info = backup_service.create_backup(db_path, current_app.config["BACKUP_DIR"], current_app.config["SECRET_KEY"])
    except BackupError as e:
        flash(str(e), "error")
        return redirect(url_for("settings.index"))

    db.session.add(AuditLog(user_id=user_id, action="backup_created", entity_type="backup", entity_id=None))
    db.session.commit()
    flash(f"Backup created: {info.filename}", "success")
    return redirect(url_for("settings.index"))


@bp.route("/settings/backups/<path:filename>/download")
@owner_only
def download_backup(filename):
    import io

    try:
        decrypted = backup_service.decrypt_backup_for_download(filename, current_app.config["BACKUP_DIR"], current_app.config["SECRET_KEY"])
    except (InvalidBackupFilenameError, BackupError) as e:
        flash(str(e), "error")
        return redirect(url_for("settings.index"))

    db.session.add(AuditLog(user_id=session["user_id"], action="backup_downloaded", entity_type="backup", entity_id=None))
    db.session.commit()

    return send_file(
        io.BytesIO(decrypted),
        as_attachment=True,
        download_name=filename.replace(".db.enc", ".db"),
        mimetype="application/x-sqlite3",
    )


@bp.route("/settings/backups/<path:filename>/restore", methods=["POST"])
@owner_only
def restore_backup_route(filename):
    user_id = session["user_id"]
    db_path = _sqlite_db_path()
    if db_path is None:
        flash(_NON_SQLITE_BACKUP_MESSAGE, "error")
        return redirect(url_for("settings.index"))

    db.session.remove()
    db.engine.dispose()  # close pooled connections before swapping the file out from under them

    try:
        backup_service.restore_backup(filename, current_app.config["BACKUP_DIR"], db_path, current_app.config["SECRET_KEY"])
    except (InvalidBackupFilenameError, BackupError) as e:
        flash(str(e), "error")
        return redirect(url_for("settings.index"))
    finally:
        db.engine.dispose()  # force fresh connections against the restored file

    try:
        db.session.add(AuditLog(user_id=user_id, action="backup_restored", entity_type="backup", entity_id=None))
        db.session.commit()
    except Exception:
        # A very old backup might predate a newer column/table; don't let
        # logging this event crash the restore itself, which already succeeded.
        db.session.rollback()

    flash(f"Database restored from {filename}. A safety backup of the prior state was taken automatically.", "success")
    return redirect(url_for("settings.index"))
