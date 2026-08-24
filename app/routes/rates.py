from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.extensions import db
from app.models import AuditLog, IncomeSource, RateHistory
from app.routes.auth import owner_only
from app.services.rate_service import (
    OverlappingRatePeriodError,
    RatePeriod,
    close_previous_open_period,
    validate_no_overlap,
)

bp = Blueprint("rates", __name__)


@bp.route("/rates")
@owner_only
def index():
    user_id = session["user_id"]
    sources = IncomeSource.query.filter_by(user_id=user_id).all()
    rates_by_source = {
        s.id: RateHistory.query.filter_by(source_id=s.id).order_by(RateHistory.effective_from.desc()).all()
        for s in sources
    }
    return render_template("rates.html", sources=sources, rates_by_source=rates_by_source)


@bp.route("/sources/add", methods=["POST"])
@owner_only
def add_source():
    user_id = session["user_id"]
    name = request.form.get("name", "").strip()
    mode = request.form.get("mode", "").strip()
    category = request.form.get("category", "khidmat").strip() or "khidmat"

    if not name or mode not in ("FIXED_HOURS", "EXACT_TIME"):
        flash("Please provide a source name and a valid mode.", "error")
        return redirect(url_for("rates.index"))

    existing = IncomeSource.query.filter(IncomeSource.user_id == user_id, IncomeSource.name.ilike(name)).first()
    if existing:
        flash(f"A source named '{name}' already exists.", "error")
        return redirect(url_for("rates.index"))

    source = IncomeSource(user_id=user_id, name=name, mode=mode, category=category)
    db.session.add(source)
    db.session.flush()
    db.session.add(AuditLog(user_id=user_id, action="source_added", entity_type="income_source", entity_id=source.id))
    db.session.commit()
    flash(f"Source '{name}' created. Add a rate to start recording sessions for it.", "success")
    return redirect(url_for("rates.index"))


@bp.route("/rates/add", methods=["POST"])
@owner_only
def add_rate():
    user_id = session["user_id"]
    source_id = request.form.get("source_id", type=int)
    rate_str = request.form.get("rate", "")
    from_str = request.form.get("effective_from", "")
    to_str = request.form.get("effective_to", "").strip()
    notes = request.form.get("notes", "").strip() or None

    source = IncomeSource.query.filter_by(id=source_id, user_id=user_id).first()
    if source is None:
        flash("Invalid source.", "error")
        return redirect(url_for("rates.index"))

    try:
        rate = Decimal(rate_str)
        if rate <= 0:
            raise InvalidOperation
    except InvalidOperation:
        flash("Rate must be a positive number.", "error")
        return redirect(url_for("rates.index"))

    try:
        effective_from = datetime.strptime(from_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid effective-from date.", "error")
        return redirect(url_for("rates.index"))

    effective_to = None
    if to_str:
        try:
            effective_to = datetime.strptime(to_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid effective-to date.", "error")
            return redirect(url_for("rates.index"))
        if effective_to < effective_from:
            flash("Effective-to date cannot be before effective-from date.", "error")
            return redirect(url_for("rates.index"))

    existing_rows = RateHistory.query.filter_by(source_id=source.id).all()
    existing_periods = [
        RatePeriod(id=r.id, source_id=r.source_id, rate=Decimal(r.rate), effective_from=r.effective_from, effective_to=r.effective_to)
        for r in existing_rows
    ]
    new_period = RatePeriod(id=None, source_id=source.id, rate=rate, effective_from=effective_from, effective_to=effective_to)

    # Auto-close a previous open-ended period if this new one starts after it,
    # so periods stay contiguous and non-overlapping (blueprint section 8).
    closure = close_previous_open_period(new_period, existing_periods)
    if closure is not None:
        row = db.session.get(RateHistory, closure.id)
        row.effective_to = closure.effective_to
        existing_periods = [p if p.id != closure.id else closure for p in existing_periods]

    try:
        validate_no_overlap(new_period, existing_periods)
    except OverlappingRatePeriodError as e:
        flash(str(e), "error")
        return redirect(url_for("rates.index"))

    row = RateHistory(source_id=source.id, rate=rate, effective_from=effective_from, effective_to=effective_to, notes=notes)
    db.session.add(row)
    db.session.flush()
    db.session.add(AuditLog(user_id=user_id, action="rate_added", entity_type="rate_history", entity_id=row.id))
    db.session.commit()
    flash(f"Rate of Rs. {rate} added for {source.name} starting {effective_from.strftime('%d %b %Y')}.", "success")
    return redirect(url_for("rates.index"))


def _parse_rate_form(form):
    """Shared parsing/validation for both add_rate and edit_rate. Returns (rate, from, to, notes) or raises ValueError with a user-facing message."""
    rate_str = form.get("rate", "")
    from_str = form.get("effective_from", "")
    to_str = form.get("effective_to", "").strip()
    notes = form.get("notes", "").strip() or None

    try:
        rate = Decimal(rate_str)
        if rate <= 0:
            raise InvalidOperation
    except InvalidOperation:
        raise ValueError("Rate must be a positive number.")

    try:
        effective_from = datetime.strptime(from_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("Invalid effective-from date.")

    effective_to = None
    if to_str:
        try:
            effective_to = datetime.strptime(to_str, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Invalid effective-to date.")
        if effective_to < effective_from:
            raise ValueError("Effective-to date cannot be before effective-from date.")

    return rate, effective_from, effective_to, notes


@bp.route("/rates/<int:rate_id>/edit", methods=["GET", "POST"])
@owner_only
def edit_rate(rate_id):
    """
    Edits an existing rate period. This corrects the rate timeline itself
    (e.g. fixing a typo) -- it deliberately does NOT touch any session
    that already snapshotted a rate under the old value, since those
    sessions store their own applied_rate/calculated_amount independently
    (blueprint: "Historical sessions must retain their original applied
    rate and calculated amount").
    """
    user_id = session["user_id"]
    row = RateHistory.query.join(IncomeSource).filter(
        RateHistory.id == rate_id, IncomeSource.user_id == user_id
    ).first_or_404()

    if request.method == "GET":
        return render_template("rate_edit.html", rate=row, source=row.source)

    try:
        rate, effective_from, effective_to, notes = _parse_rate_form(request.form)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("rates.edit_rate", rate_id=rate_id))

    existing_rows = RateHistory.query.filter_by(source_id=row.source_id).all()
    existing_periods = [
        RatePeriod(id=r.id, source_id=r.source_id, rate=Decimal(r.rate), effective_from=r.effective_from, effective_to=r.effective_to)
        for r in existing_rows
    ]
    candidate = RatePeriod(id=row.id, source_id=row.source_id, rate=rate, effective_from=effective_from, effective_to=effective_to)

    try:
        validate_no_overlap(candidate, existing_periods)
    except OverlappingRatePeriodError as e:
        flash(str(e), "error")
        return redirect(url_for("rates.edit_rate", rate_id=rate_id))

    row.rate = rate
    row.effective_from = effective_from
    row.effective_to = effective_to
    row.notes = notes
    db.session.add(AuditLog(user_id=user_id, action="rate_edited", entity_type="rate_history", entity_id=row.id))
    db.session.commit()
    flash("Rate period updated. Existing sessions in this range keep their originally-applied amounts.", "success")
    return redirect(url_for("rates.index"))
