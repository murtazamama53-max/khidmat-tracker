"""
Route-level integration tests. Uses an in-memory SQLite DB via TestConfig
so these never touch the real instance/khidmat.db file.
"""
from datetime import date as date_cls
from decimal import Decimal

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db


@pytest.fixture
def app():
    app = create_app(TestConfig)
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def _create_owner(client, email="murtaza@example.com", password="testpass123"):
    return client.post(
        "/setup",
        data={"name": "Murtaza", "email": email, "password": password, "confirm": password},
    )


def test_setup_then_login_flow(client):
    r = _create_owner(client)
    assert r.status_code == 302

    client.get("/logout")
    r = client.post("/login", data={"email": "murtaza@example.com", "password": "testpass123"})
    assert r.status_code == 302
    assert r.headers["Location"] == "/"


def test_second_owner_account_is_blocked(client):
    _create_owner(client)
    client.get("/logout")
    r = client.post(
        "/setup",
        data={"name": "Intruder", "email": "intruder@example.com", "password": "whatever123", "confirm": "whatever123"},
        follow_redirects=True,
    )
    assert b"already exists" in r.data.lower() or b"log in" in r.data.lower()

    from app.models import User

    with client.application.app_context():
        assert User.query.count() == 1


def test_dashboard_requires_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/setup" in r.headers["Location"]


def test_wrong_password_rejected(client):
    _create_owner(client)
    client.get("/logout")
    r = client.post("/login", data={"email": "murtaza@example.com", "password": "wrongpass"})
    assert r.status_code == 200  # re-renders login with flash, no redirect
    assert b"Incorrect" in r.data


def test_full_quick_add_flow_matches_blueprint_example(client):
    """Sbhs(7) & sghs(5-6:20) at Rs.250/h => 1750 + 333.33 = 2083.33 (blueprint section 10)."""
    _create_owner(client)

    client.post("/sources/add", data={"name": "Sbhs", "mode": "FIXED_HOURS", "category": "khidmat"})
    client.post("/sources/add", data={"name": "sghs", "mode": "EXACT_TIME", "category": "khidmat"})

    from app.models import IncomeSource

    with client.application.app_context():
        sbhs_id = IncomeSource.query.filter_by(name="Sbhs").first().id
        sghs_id = IncomeSource.query.filter_by(name="sghs").first().id

    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "250", "effective_from": "2026-01-01"})
    client.post("/rates/add", data={"source_id": sghs_id, "rate": "250", "effective_from": "2026-01-01"})

    r = client.post(
        "/sessions/parse-preview", json={"text": "Sbhs(7) & sghs(5-6:20)", "date": "2026-08-14"}
    )
    assert r.status_code == 200
    preview = r.get_json()
    assert preview["all_confirmed"] is True
    assert preview["total"] == "2083.33"
    assert preview["items"][0]["amount"] == "1750.00"
    assert preview["items"][1]["amount"] == "333.33"

    r2 = client.post(
        "/sessions/confirm",
        json={"date": preview["date"], "raw_text": "Sbhs(7) & sghs(5-6:20)", "items": preview["items"]},
    )
    assert r2.status_code == 200
    assert r2.get_json()["saved"] == 2

    from app.models import Session as SessionModel

    with client.application.app_context():
        rows = SessionModel.query.order_by(SessionModel.source_id).all()
        assert len(rows) == 2
        assert rows[0].duration_minutes == 420
        assert rows[1].duration_minutes == 80


def test_historical_rate_change_does_not_alter_old_session_amount(client):
    _create_owner(client)
    client.post("/sources/add", data={"name": "Sbhs", "mode": "FIXED_HOURS", "category": "khidmat"})

    from app.models import IncomeSource

    with client.application.app_context():
        sbhs_id = IncomeSource.query.filter_by(name="Sbhs").first().id

    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "250", "effective_from": "2026-01-01", "effective_to": "2026-06-30"})
    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "300", "effective_from": "2026-07-01"})

    # January session should use 250, not the new 300 rate.
    r = client.post("/sessions/parse-preview", json={"text": "Sbhs(7)", "date": "2026-01-15"})
    jan = r.get_json()
    assert jan["items"][0]["amount"] == "1750.00"  # 7 * 250

    # August session should use the new 300 rate.
    r2 = client.post("/sessions/parse-preview", json={"text": "Sbhs(7)", "date": "2026-08-01"})
    aug = r2.get_json()
    assert aug["items"][0]["amount"] == "2100.00"  # 7 * 300


def test_guest_cannot_access_owner_pages(client):
    _create_owner(client)
    client.get("/logout")

    r = client.get("/guest")
    assert r.status_code == 200

    for path in ["/", "/sessions", "/rates"]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["Location"] == "/guest"


def test_guest_calculation_never_persists(client):
    _create_owner(client)
    client.get("/logout")
    client.get("/guest")

    r = client.post("/guest/calculate", json={"text": "Sbhs(7)", "rate": "300"})
    assert r.status_code == 200
    assert r.get_json()["total"] == "2100.00"

    from app.models import Session as SessionModel

    with client.application.app_context():
        assert SessionModel.query.count() == 0


def test_guest_calculate_blocked_without_guest_session(client):
    # No /guest visit first -> not flagged as guest -> endpoint should refuse.
    r = client.post("/guest/calculate", json={"text": "Sbhs(7)", "rate": "300"})
    assert r.status_code == 403


def _setup_owner_source_and_rate(client, mode="FIXED_HOURS", name="Sbhs", rate="250", frm="2026-01-01"):
    _create_owner(client)
    client.post("/sources/add", data={"name": name, "mode": mode, "category": "khidmat"})
    from app.models import IncomeSource

    with client.application.app_context():
        source_id = IncomeSource.query.filter_by(name=name).first().id
    client.post("/rates/add", data={"source_id": source_id, "rate": rate, "effective_from": frm})
    return source_id


def test_quantity_hours_persisted_on_fixed_hours_session(client):
    _setup_owner_source_and_rate(client)
    r = client.post("/sessions/parse-preview", json={"text": "Sbhs(7)", "date": "2026-08-14"})
    preview = r.get_json()
    client.post(
        "/sessions/confirm",
        json={"date": preview["date"], "raw_text": "Sbhs(7)", "items": preview["items"]},
    )

    from app.models import Session as SessionModel

    with client.application.app_context():
        row = SessionModel.query.first()
        assert row.quantity_hours == pytest.approx(7.0)
        assert row.duration_minutes == 420


def test_session_view_and_edit_recalculates_amount(client):
    _setup_owner_source_and_rate(client)
    r = client.post("/sessions/parse-preview", json={"text": "Sbhs(7)", "date": "2026-08-14"})
    preview = r.get_json()
    client.post("/sessions/confirm", json={"date": preview["date"], "raw_text": "Sbhs(7)", "items": preview["items"]})

    from app.models import Session as SessionModel

    with client.application.app_context():
        session_id = SessionModel.query.first().id

    r = client.get(f"/sessions/{session_id}")
    assert r.status_code == 200
    assert b"7" in r.data

    # Edit quantity from 7h to 5h -- amount must be recalculated server-side, not trusted from a form field.
    r2 = client.post(f"/sessions/{session_id}/edit", data={"date": "2026-08-14", "quantity_hours": "5"})
    assert r2.status_code == 302

    with client.application.app_context():
        row = SessionModel.query.get(session_id)
        assert row.duration_minutes == 300  # 5 hours
        assert row.calculated_amount == Decimal("1250.00")  # 5 * 250


def test_session_edit_uses_new_dates_historical_rate(client):
    source_id = _setup_owner_source_and_rate(client)
    with client.application.app_context():
        from app.models import RateHistory

        RateHistory.query.filter_by(source_id=source_id).update({"effective_to": date_cls(2026, 6, 30)})
        db.session.commit()
        db.session.add(
            RateHistory(source_id=source_id, rate=Decimal("300"), effective_from=date_cls(2026, 7, 1))
        )
        db.session.commit()

    r = client.post("/sessions/parse-preview", json={"text": "Sbhs(7)", "date": "2026-01-15"})
    preview = r.get_json()
    client.post("/sessions/confirm", json={"date": preview["date"], "raw_text": "Sbhs(7)", "items": preview["items"]})

    from app.models import Session as SessionModel

    with client.application.app_context():
        session_id = SessionModel.query.first().id
        assert SessionModel.query.get(session_id).calculated_amount == Decimal("1750.00")  # Jan @ 250

    # Move the same session's date into the July window -- should now use 300.
    r2 = client.post(f"/sessions/{session_id}/edit", data={"date": "2026-08-01", "quantity_hours": "7"})
    assert r2.status_code == 302

    with client.application.app_context():
        row = SessionModel.query.get(session_id)
        assert row.calculated_amount == Decimal("2100.00")  # Aug @ 300


def test_session_edit_rejects_when_no_rate_covers_new_date(client):
    _setup_owner_source_and_rate(client, frm="2026-01-01")
    r = client.post("/sessions/parse-preview", json={"text": "Sbhs(7)", "date": "2026-08-14"})
    preview = r.get_json()
    client.post("/sessions/confirm", json={"date": preview["date"], "raw_text": "Sbhs(7)", "items": preview["items"]})

    from app.models import Session as SessionModel

    with client.application.app_context():
        session_id = SessionModel.query.first().id
        original_amount = SessionModel.query.get(session_id).calculated_amount

    # 2025 has no rate at all defined -- edit must be rejected, not silently fall back.
    r2 = client.post(f"/sessions/{session_id}/edit", data={"date": "2025-01-01", "quantity_hours": "7"}, follow_redirects=True)
    assert b"No rate is defined" in r2.data

    with client.application.app_context():
        row = SessionModel.query.get(session_id)
        assert row.calculated_amount == original_amount  # untouched
