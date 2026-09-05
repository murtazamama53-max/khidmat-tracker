from decimal import Decimal, InvalidOperation

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from app.extensions import db
from app.models import AuditLog, Goal
from app.routes.auth import owner_only
from app.services import goals_service
from app.services.date_range import app_today

bp = Blueprint("goals", __name__)


@bp.route("/goals")
@owner_only
def index():
    user_id = session["user_id"]
    goal = Goal.query.filter_by(user_id=user_id).first()
    actuals = goals_service.compute_month_actuals(user_id, app_today(current_app.config["TIMEZONE"]))
    progress = goals_service.compute_progress(goal, actuals)
    return render_template("goals.html", goal=goal, progress=progress)


@bp.route("/goals/save", methods=["POST"])
@owner_only
def save():
    user_id = session["user_id"]

    def parse_optional_decimal(name):
        raw = request.form.get(name, "").strip()
        if not raw:
            return None
        try:
            value = Decimal(raw)
        except InvalidOperation:
            return "invalid"
        return value if value > 0 else "invalid"

    income = parse_optional_decimal("monthly_income_target")
    hours = parse_optional_decimal("monthly_hours_target")
    sessions_raw = request.form.get("monthly_sessions_target", "").strip()
    sessions_target = None
    if sessions_raw:
        try:
            sessions_target = int(sessions_raw)
            if sessions_target <= 0:
                sessions_target = "invalid"
        except ValueError:
            sessions_target = "invalid"

    if income == "invalid" or hours == "invalid" or sessions_target == "invalid":
        flash("Targets must be positive numbers, or left blank to clear them.", "error")
        return redirect(url_for("goals.index"))

    goal = Goal.query.filter_by(user_id=user_id).first()
    if goal is None:
        goal = Goal(user_id=user_id)
        db.session.add(goal)

    goal.monthly_income_target = income
    goal.monthly_hours_target = hours
    goal.monthly_sessions_target = sessions_target
    db.session.flush()

    db.session.add(AuditLog(user_id=user_id, action="goals_updated", entity_type="goal", entity_id=goal.id))
    db.session.commit()
    flash("Goals updated.", "success")
    return redirect(url_for("goals.index"))
