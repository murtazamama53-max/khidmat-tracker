"""
Guest mode.

Guests get a temporary, in-memory calculator workspace. Their data is
stored ONLY in the Flask session cookie (never the database), so it is
mechanically impossible for guest input to leak into owner records, and
it is destroyed automatically when the session ends (blueprint section 17).
"""
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, render_template, request, session

from app.services import calculation_engine as calc
from app.services.parser import parse_input

bp = Blueprint("guest", __name__)

GUEST_SESSION_KEY = "guest_calculations"


@bp.route("/guest")
def workspace():
    session.clear()  # guarantees no leftover owner session data
    session["is_guest"] = True
    session[GUEST_SESSION_KEY] = []
    return render_template("guest.html")


@bp.route("/guest/calculate", methods=["POST"])
def calculate():
    """
    Guest-only calculator endpoint. Accepts shorthand text and an assumed
    hourly rate (guests have no rate history), returns a calculation that
    is NEVER persisted to the database.
    """
    if not session.get("is_guest"):
        return jsonify({"error": "Guest workspace only."}), 403

    data = request.get_json(silent=True) or {}
    raw_text = (data.get("text") or "").strip()
    try:
        rate = Decimal(str(data.get("rate", "0")))
    except InvalidOperation:
        return jsonify({"error": "Invalid rate."}), 400

    if rate <= 0:
        return jsonify({"error": "Enter an hourly rate greater than zero to calculate."}), 400

    parsed = parse_input(raw_text)
    if not parsed.ok:
        return jsonify({"error": "; ".join(parsed.errors) or "Could not parse input."}), 400

    components = []
    total = Decimal("0")
    for c in parsed.components:
        try:
            if c.mode == "FIXED_HOURS":
                duration = calc.fixed_hours_duration(Decimal(str(c.quantity_hours)))
            else:
                duration = calc.exact_time_duration(c.start.hour, c.start.minute, c.end.hour, c.end.minute)
            earning = calc.calculate_earning(duration, rate)
        except calc.CalculationError as e:
            return jsonify({"error": str(e)}), 400

        total += earning.calculated_amount
        components.append(
            {
                "source": c.source_name,
                "mode": c.mode,
                "duration": duration.human_readable,
                "amount": str(earning.calculated_amount.quantize(Decimal("0.01"))),
                "needs_confirmation": c.needs_confirmation,
            }
        )

    return jsonify({"components": components, "total": str(total.quantize(Decimal("0.01")))})
