from flask import Blueprint, current_app, jsonify, request, session

from app.routes.auth import owner_only
from app.services.assistant_service import EXAMPLE_QUESTIONS, answer_question
from app.services.date_range import app_today

bp = Blueprint("assistant", __name__)


@bp.route("/assistant/ask", methods=["POST"])
@owner_only
def ask():
    """
    AI assistant layer (blueprint section 12 / Phase 6). Answers are
    produced entirely by app.services.assistant_service, which only does
    local pattern matching and reuses the same deterministic query/analytics
    functions as the dashboard and reports -- no external API call is made
    and no money math happens here or in the browser.
    """
    user_id = session["user_id"]
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()

    if not question:
        return jsonify({"error": "Ask me something about your earnings."}), 400
    if len(question) > 300:
        return jsonify({"error": "That question is too long."}), 400

    today = app_today(current_app.config["TIMEZONE"])
    answer = answer_question(user_id, question, today, currency=current_app.config["CURRENCY"])
    return jsonify({"answer": answer.text})


@bp.route("/assistant/examples")
@owner_only
def examples():
    return jsonify({"examples": EXAMPLE_QUESTIONS})
