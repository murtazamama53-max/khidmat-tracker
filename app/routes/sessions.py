import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from app.extensions import db
from app.models import AuditLog, IncomeSource, RateHistory
from app.models import Session as SessionModel
from app.routes.auth import owner_only
from app.services import calculation_engine as calc
from app.services.parser import parse_input
from app.services.rate_service import RatePeriod, RateResolutionError, resolve_rate

bp = Blueprint("sessions", __name__)


def _source_modes_for_user(user_id: int) -> dict:
    sources = IncomeSource.query.filter_by(user_id=user_id, active=True).all()
    return {s.name.lower(): s.mode for s in sources}


def _rate_periods_for_source(source_id: int) -> list[RatePeriod]:
    rows = RateHistory.query.filter_by(source_id=source_id).all()
    return [
        RatePeriod(id=r.id, source_id=r.source_id, rate=Decimal(r.rate), effective_from=r.effective_from, effective_to=r.effective_to)
        for r in rows
    ]


@bp.route("/sessions")
@owner_only
def list_sessions():
    user_id = session["user_id"]
    query = SessionModel.query.filter_by(user_id=user_id)

    date_filter = request.args.get("date")
    filtered_date = None
    if date_filter:
        try:
            filtered_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
            query = query.filter(SessionModel.date == filtered_date)
        except ValueError:
            filtered_date = None

    all_sessions = query.order_by(SessionModel.date.desc(), SessionModel.id.desc()).all()
    sources = IncomeSource.query.filter_by(user_id=user_id).all()
    return render_template("sessions.html", sessions=all_sessions, sources=sources, today=date.today(), filtered_date=filtered_date)


@bp.route("/sessions/parse-preview", methods=["POST"])
@owner_only
def parse_preview():
    """
    AJAX endpoint powering the quick-add box. Parses the shorthand text,
    resolves the historical rate for the given date, and returns an
    itemized, fully-calculated preview -- but saves NOTHING yet. Nothing
    is persisted until /sessions/confirm is called (blueprint section 6/23).
    """
    user_id = session["user_id"]
    data = request.get_json(silent=True) or {}
    raw_text = (data.get("text") or "").strip()
    date_str = data.get("date") or date.today().isoformat()

    try:
        session_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date."}), 400

    known_modes = _source_modes_for_user(user_id)
    parsed = parse_input(raw_text, known_sources=known_modes)
    if not parsed.ok:
        return jsonify({"error": "; ".join(parsed.errors) or "Could not parse input."}), 400

    items = []
    total = Decimal("0")
    all_confirmed = True

    for c in parsed.components:
        item = {
            "raw_text": c.raw_text,
            "source_name": c.source_name,
            "mode": c.mode,
            "needs_confirmation": c.needs_confirmation,
            "confirmation_reason": c.confirmation_reason,
        }
        if c.needs_confirmation:
            all_confirmed = False
            items.append(item)
            continue

        source = IncomeSource.query.filter(
            IncomeSource.user_id == user_id, IncomeSource.name.ilike(c.source_name)
        ).first()
        if source is None:
            item["needs_confirmation"] = True
            item["confirmation_reason"] = f"'{c.source_name}' has no matching income source yet."
            all_confirmed = False
            items.append(item)
            continue

        try:
            if c.mode == "FIXED_HOURS":
                duration = calc.fixed_hours_duration(Decimal(str(c.quantity_hours)))
            else:
                duration = calc.exact_time_duration(c.start.hour, c.start.minute, c.end.hour, c.end.minute)
        except calc.CalculationError as e:
            item["needs_confirmation"] = True
            item["confirmation_reason"] = str(e)
            all_confirmed = False
            items.append(item)
            continue

        try:
            rate_period = resolve_rate(session_date, _rate_periods_for_source(source.id))
        except RateResolutionError as e:
            item["needs_confirmation"] = True
            item["confirmation_reason"] = str(e)
            all_confirmed = False
            items.append(item)
            continue

        earning = calc.calculate_earning(duration, rate_period.rate)
        total += earning.calculated_amount

        item.update(
            {
                "source_id": source.id,
                "duration_minutes": duration.duration_minutes,
                "duration_human": duration.human_readable,
                "applied_rate": str(rate_period.rate),
                "amount": str(earning.calculated_amount.quantize(Decimal("0.01"))),
                "start": str(c.start) if c.start else None,
                "end": str(c.end) if c.end else None,
                "quantity_hours": c.quantity_hours,
            }
        )
        items.append(item)

    return jsonify(
        {
            "items": items,
            "total": str(total.quantize(Decimal("0.01"))),
            "all_confirmed": all_confirmed,
            "date": session_date.isoformat(),
        }
    )


@bp.route("/sessions/confirm", methods=["POST"])
@owner_only
def confirm():
    """
    Persists a previously-previewed, fully-resolved set of components.
    Re-runs the deterministic calculation server-side rather than trusting
    client-supplied amounts (blueprint section 32: never trust client
    values for financial calculations).
    """
    user_id = session["user_id"]
    data = request.get_json(silent=True) or {}
    date_str = data.get("date")
    raw_text = data.get("raw_text", "")
    items = data.get("items", [])

    if not items:
        return jsonify({"error": "Nothing to save."}), 400

    try:
        session_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid date."}), 400

    capture_event_id = str(uuid.uuid4())
    saved = []

    for item in items:
        source_id = item.get("source_id")
        source = IncomeSource.query.filter_by(id=source_id, user_id=user_id).first()
        if source is None:
            return jsonify({"error": "One of the sources is invalid. Refresh and try again."}), 400

        mode = item.get("mode")
        quantity_hours_value = None
        try:
            if mode == "FIXED_HOURS":
                quantity_hours_value = Decimal(str(item.get("quantity_hours")))
                duration = calc.fixed_hours_duration(quantity_hours_value)
                start_t, end_t = None, None
            else:
                start_str, end_str = item.get("start"), item.get("end")
                sh, sm = [int(x) for x in start_str.split(":")]
                eh, em = [int(x) for x in end_str.split(":")]
                duration = calc.exact_time_duration(sh, sm, eh, em)
                start_t, end_t = datetime.strptime(start_str, "%H:%M").time(), datetime.strptime(end_str, "%H:%M").time()

            rate_period = resolve_rate(session_date, _rate_periods_for_source(source.id))
            earning = calc.calculate_earning(duration, rate_period.rate)
        except (calc.CalculationError, RateResolutionError) as e:
            return jsonify({"error": str(e)}), 400

        record = SessionModel(
            user_id=user_id,
            source_id=source.id,
            capture_event_id=capture_event_id,
            date=session_date,
            mode=mode,
            start_time=start_t,
            end_time=end_t,
            quantity_hours=quantity_hours_value,
            duration_minutes=duration.duration_minutes,
            decimal_hours=duration.decimal_hours,
            applied_rate=rate_period.rate,
            calculated_amount=earning.calculated_amount,
            status="completed",
            raw_input=raw_text,
        )
        db.session.add(record)
        saved.append(record)

    db.session.flush()
    for record in saved:
        db.session.add(
            AuditLog(user_id=user_id, action="session_created", entity_type="session", entity_id=record.id)
        )
    db.session.commit()

    return jsonify({"saved": len(saved), "capture_event_id": capture_event_id})


@bp.route("/sessions/<int:session_id>")
@owner_only
def view(session_id):
    user_id = session["user_id"]
    record = SessionModel.query.filter_by(id=session_id, user_id=user_id).first_or_404()
    sources = IncomeSource.query.filter_by(user_id=user_id, active=True).all()
    return render_template("session_detail.html", s=record, sources=sources)


@bp.route("/sessions/<int:session_id>/edit", methods=["POST"])
@owner_only
def edit(session_id):
    """
    Edits an existing session. The amount is never taken from the form --
    it is always recomputed server-side from the (possibly new) date,
    mode-specific inputs, and the rate history for that source/date,
    exactly like a fresh save (blueprint section 32).
    """
    user_id = session["user_id"]
    record = SessionModel.query.filter_by(id=session_id, user_id=user_id).first_or_404()

    date_str = request.form.get("date", "")
    try:
        new_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date.", "error")
        return redirect(url_for("sessions.view", session_id=session_id))

    try:
        if record.mode == "FIXED_HOURS":
            quantity = Decimal(request.form.get("quantity_hours", "0"))
            duration = calc.fixed_hours_duration(quantity)
            start_t, end_t = None, None
            quantity_hours_value = quantity
        else:
            start_str = request.form.get("start_time", "")
            end_str = request.form.get("end_time", "")
            sh, sm = [int(x) for x in start_str.split(":")]
            eh, em = [int(x) for x in end_str.split(":")]
            is_overnight = request.form.get("is_overnight") == "on"
            duration = calc.exact_time_duration(sh, sm, eh, em, is_overnight=is_overnight)
            start_t = datetime.strptime(start_str, "%H:%M").time()
            end_t = datetime.strptime(end_str, "%H:%M").time()
            quantity_hours_value = None

        rate_period = resolve_rate(new_date, _rate_periods_for_source(record.source_id))
        earning = calc.calculate_earning(duration, rate_period.rate)
    except (calc.CalculationError, RateResolutionError, ValueError, InvalidOperation) as e:
        flash(str(e), "error")
        return redirect(url_for("sessions.view", session_id=session_id))

    record.date = new_date
    record.start_time = start_t
    record.end_time = end_t
    record.quantity_hours = quantity_hours_value
    record.duration_minutes = duration.duration_minutes
    record.decimal_hours = duration.decimal_hours
    record.applied_rate = rate_period.rate
    record.calculated_amount = earning.calculated_amount
    record.notes = request.form.get("notes", "").strip() or None

    db.session.add(AuditLog(user_id=user_id, action="session_edited", entity_type="session", entity_id=record.id))
    db.session.commit()

    flash("Session updated.", "success")
    return redirect(url_for("sessions.view", session_id=session_id))


@bp.route("/sessions/<int:session_id>/delete", methods=["POST"])
@owner_only
def delete(session_id):
    user_id = session["user_id"]
    record = SessionModel.query.filter_by(id=session_id, user_id=user_id).first_or_404()
    db.session.add(AuditLog(user_id=user_id, action="session_deleted", entity_type="session", entity_id=record.id))
    db.session.delete(record)
    db.session.commit()
    flash("Session deleted.", "success")
    return redirect(url_for("sessions.list_sessions"))
