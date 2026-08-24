from datetime import date

import pytest

from app import create_app
from app.config import TestConfig
from app.services.assistant_service import (
    answer_question,
    detect_intent,
    find_periods,
    match_source,
)

# --------------------------------------------------------------------------
# Pure parsing tests -- no DB / no Flask app context needed
# --------------------------------------------------------------------------


def test_find_periods_explicit_month_and_year():
    periods = find_periods("how much did I earn in july 2026?", today=date(2026, 8, 19))
    assert len(periods) == 1
    assert periods[0].start == date(2026, 7, 1)
    assert periods[0].end == date(2026, 7, 31)
    assert periods[0].label == "July 2026"


def test_find_periods_bare_month_future_falls_back_a_year():
    # "December" said in January must mean last December, not one 11 months away.
    periods = find_periods("what did I earn in december", today=date(2026, 1, 5))
    assert periods[0].start == date(2025, 12, 1)
    assert periods[0].end == date(2025, 12, 31)


def test_find_periods_bare_month_current_year_when_already_passed():
    periods = find_periods("earnings in march", today=date(2026, 8, 19))
    assert periods[0].start == date(2026, 3, 1)


def test_find_periods_two_periods_in_reading_order():
    periods = find_periods("why is august lower than july", today=date(2026, 8, 19))
    assert len(periods) == 2
    assert periods[0].label == "August 2026"
    assert periods[1].label == "July 2026"


def test_find_periods_this_month_then_last_month_reading_order():
    periods = find_periods("why is this month lower than last month", today=date(2026, 8, 19))
    assert len(periods) == 2
    assert periods[0].label == "this month"
    assert periods[1].label == "last month"
    assert periods[0].start == date(2026, 8, 1)
    assert periods[1].start == date(2026, 7, 1)
    assert periods[1].end == date(2026, 7, 31)


@pytest.mark.parametrize(
    "keyword,expected_start,expected_end",
    [
        ("today", date(2026, 8, 19), date(2026, 8, 19)),
        ("yesterday", date(2026, 8, 18), date(2026, 8, 18)),
        ("this week", date(2026, 8, 17), date(2026, 8, 19)),  # Monday..today (Wed)
        ("this year", date(2026, 1, 1), date(2026, 8, 19)),
        ("last year", date(2025, 1, 1), date(2025, 12, 31)),
    ],
)
def test_find_periods_keyword_ranges(keyword, expected_start, expected_end):
    periods = find_periods(f"how much did I earn {keyword}?", today=date(2026, 8, 19))
    assert periods[0].start == expected_start
    assert periods[0].end == expected_end


def test_find_periods_no_period_mentioned_returns_empty():
    assert find_periods("how much did I earn?", today=date(2026, 8, 19)) == []


@pytest.mark.parametrize(
    "text,expected_intent",
    [
        ("how much did I earn this month?", "earnings"),
        ("how many hours did I work last month?", "hours"),
        ("how many sessions this month?", "sessions"),
        ("what's my effective rate this year?", "rate"),
        ("how much is pending from tuition?", "pending"),
        ("how much have I been paid this month?", "paid"),
        ("why is this month lower than last month?", "trend"),
        ("compare july to august", "trend"),
    ],
)
def test_detect_intent(text, expected_intent):
    assert detect_intent(text) == expected_intent


def test_match_source_finds_known_income_source_case_insensitively():
    assert match_source("how much did I earn from sghs in july", ["Sbhs", "sghs"]) == "sghs"


def test_match_source_finds_tuition_pseudo_source():
    assert match_source("how much is pending from tuition", ["Sbhs", "sghs"]) == "tuition"


def test_match_source_returns_none_when_unscoped():
    assert match_source("how much did I earn this month", ["Sbhs", "sghs"]) is None


# --------------------------------------------------------------------------
# DB-backed answer_question tests -- reuses the app's real deterministic
# calculation engine via the normal HTTP routes to seed data, then calls
# answer_question directly.
# --------------------------------------------------------------------------


@pytest.fixture
def app():
    return create_app(TestConfig)


@pytest.fixture
def client(app):
    return app.test_client()


def _seed(client):
    client.post(
        "/setup",
        data={"name": "Murtaza", "email": "murtaza@example.com", "password": "testpass123", "confirm": "testpass123"},
    )
    client.post("/sources/add", data={"name": "Sbhs", "mode": "FIXED_HOURS"})
    from app.models import IncomeSource, User

    with client.application.app_context():
        sbhs_id = IncomeSource.query.filter_by(name="Sbhs").first().id
        user_id = User.query.first().id
    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "200", "effective_from": "2026-01-01"})

    # July: 4 hours -> Rs. 800
    r = client.post("/sessions/parse-preview", json={"text": "Sbhs(4)", "date": "2026-07-10"})
    preview = r.get_json()
    client.post("/sessions/confirm", json={"date": preview["date"], "raw_text": "Sbhs(4)", "items": preview["items"]})

    # August: 10 hours -> Rs. 2000
    r = client.post("/sessions/parse-preview", json={"text": "Sbhs(10)", "date": "2026-08-05"})
    preview = r.get_json()
    client.post("/sessions/confirm", json={"date": preview["date"], "raw_text": "Sbhs(10)", "items": preview["items"]})

    # Tuition: Ahmed, Rs. 5000, invoiced for August, unpaid (pending)
    client.post("/students/add", data={"name": "Ahmed"})
    from app.models import Student

    with client.application.app_context():
        ahmed_id = Student.query.filter_by(name="Ahmed").first().id
    client.post(f"/students/{ahmed_id}/fee-periods/add", data={"amount": "5000", "effective_from": "2026-01-01"})
    client.post(
        f"/students/{ahmed_id}/invoices/add", data={"period_start": "2026-08-01", "period_end": "2026-08-31"}
    )

    return user_id


TODAY = date(2026, 8, 19)


def test_earnings_by_source_and_month(client, app):
    user_id = _seed(client)
    with app.app_context():
        ans = answer_question(user_id, "how much did I earn from Sbhs in July 2026?", TODAY)
        assert "PKR 800.00" in ans.text
        assert "July 2026" in ans.text

        ans = answer_question(user_id, "how much did I earn from Sbhs in August 2026?", TODAY)
        assert "PKR 2,000.00" in ans.text


def test_pending_from_tuition(client, app):
    user_id = _seed(client)
    with app.app_context():
        ans = answer_question(user_id, "how much is pending from tuition in August 2026?", TODAY)
        assert "PKR 5,000.00" in ans.text


def test_pending_guard_for_non_tuition_source(client, app):
    user_id = _seed(client)
    with app.app_context():
        ans = answer_question(user_id, "how much is pending from Sbhs?", TODAY)
        assert "Only tuition tracks" in ans.text


def test_hours_and_sessions_and_rate(client, app):
    user_id = _seed(client)
    with app.app_context():
        ans = answer_question(user_id, "how many hours did I work in August 2026?", TODAY)
        assert "10.00 hours" in ans.text

        ans = answer_question(user_id, "how many sessions in August 2026?", TODAY)
        assert "1 session(s)" in ans.text

        ans = answer_question(user_id, "what's my effective rate in August 2026?", TODAY)
        assert "PKR 200.00/hr" in ans.text


def test_trend_explanation_grounded_in_real_deltas(client, app):
    user_id = _seed(client)
    with app.app_context():
        ans = answer_question(user_id, "why is august 2026 higher than july 2026?", TODAY)
        # August total = 2000 (Sbhs) + 5000 (tuition, pending) = 7000. July total = 800.
        assert "PKR 6,200.00 higher than" in ans.text
        assert "August 2026: PKR 7,000.00" in ans.text
        assert "July 2026: PKR 800.00" in ans.text
        assert "Tuition" in ans.text  # tuition (5000) moved more than Khidmat (1200)


def test_empty_question_gives_examples():
    from app import create_app as _create_app

    a = _create_app(TestConfig)
    with a.app_context():
        ans = answer_question(1, "", TODAY)
        assert "didn't catch" in ans.text
        assert "How much did I earn this month?" in ans.text
