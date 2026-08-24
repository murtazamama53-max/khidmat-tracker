from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.extensions import db
from app.models import AuditLog, User

bp = Blueprint("auth", __name__)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id") or session.get("is_guest"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def owner_only(view):
    """Explicitly blocks guests, even if a guest session cookie exists."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("is_guest"):
            flash("This area isn't available in Guest Workspace.", "error")
            return redirect(url_for("guest.workspace"))
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if user is None or not user.check_password(password):
            flash("Incorrect email or password.", "error")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user.id
        session["is_guest"] = False
        session.permanent = True

        db.session.add(AuditLog(user_id=user.id, action="login", entity_type="user", entity_id=user.id))
        db.session.commit()

        next_url = request.args.get("next") or url_for("dashboard.index")
        return redirect(next_url)

    return render_template("login.html")


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """
    First-run owner account creation. Only accessible while no owner
    account exists yet -- prevents anyone from creating a second owner
    account later by hitting this URL.
    """
    if User.query.first() is not None:
        flash("An owner account already exists. Please log in.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "Murtaza").strip() or "Murtaza"
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not email or "@" not in email:
            flash("Please enter a valid email.", "error")
            return render_template("setup.html")
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("setup.html")
        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("setup.html")

        user = User(email=email, name=name)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        db.session.add(AuditLog(user_id=user.id, action="owner_account_created", entity_type="user", entity_id=user.id))
        db.session.commit()

        session.clear()
        session["user_id"] = user.id
        session["is_guest"] = False
        session.permanent = True
        flash("Owner account created.", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("setup.html")


@bp.route("/logout")
def logout():
    user_id = session.get("user_id")
    if user_id and not session.get("is_guest"):
        db.session.add(AuditLog(user_id=user_id, action="logout", entity_type="user", entity_id=user_id))
        db.session.commit()
    session.clear()
    return redirect(url_for("auth.login"))
