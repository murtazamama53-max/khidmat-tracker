from datetime import date
from decimal import Decimal

import pytest

from app import create_app
from app.config import TestConfig


class CalendarTestConfig(TestConfig):
    GOOGLE_CLIENT_ID = "fake-client-id.apps.googleusercontent.com"
    GOOGLE_CLIENT_SECRET = "fake-client-secret"
    GOOGLE_REDIRECT_URI = "http://localhost:5000/calendar/oauth/callback"


@pytest.fixture
def app():
    return create_app(CalendarTestConfig)


@pytest.fixture
def client(app):
    return app.test_client()


def _create_owner(client):
    return client.post(
        "/setup",
        data={"name": "Murtaza", "email": "murtaza@example.com", "password": "testpass123", "confirm": "testpass123"},
    )


def _setup_sources_and_rates(client, rate="250", frm="2026-01-01", to=None):
    client.post("/sources/add", data={"name": "SBHS", "mode": "FIXED_HOURS"})
    client.post("/sources/add", data={"name": "SGHS", "mode": "EXACT_TIME"})
    from app.models import IncomeSource

    with client.application.app_context():
        sbhs_id = IncomeSource.query.filter_by(name="SBHS").first().id
        sghs_id = IncomeSource.query.filter_by(name="SGHS").first().id
    data = {"source_id": sbhs_id, "rate": rate, "effective_from": frm}
    if to:
        data["effective_to"] = to
    client.post("/rates/add", data=data)
    data2 = dict(data)
    data2["source_id"] = sghs_id
    client.post("/rates/add", data=data2)
    return sbhs_id, sghs_id


def _connect_fake_account(client):
    """Bypasses live OAuth: directly inserts a connected CalendarAccount, as the callback route would after a successful token exchange."""
    from app.extensions import db
    from app.models import CalendarAccount
    from app.services import token_crypto

    with client.application.app_context():
        user_id = 1
        encrypted = token_crypto.encrypt_token("fake-refresh-token", client.application.config["SECRET_KEY"])
        account = CalendarAccount(user_id=user_id, google_email="murtaza@gmail.com", calendar_id="primary", encrypted_refresh_token=encrypted)
        db.session.add(account)
        db.session.commit()


def _fake_event(event_id, title, start_iso, end_iso, status="confirmed", recurring_event_id=None):
    return {
        "id": event_id,
        "summary": title,
        "status": status,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
        "recurringEventId": recurring_event_id,
    }


# ---------------- OAuth flow configuration (no live network) ----------------


def test_connect_redirects_to_google_when_configured(client):
    _create_owner(client)
    r = client.get("/calendar/connect", follow_redirects=False)
    assert r.status_code == 302
    assert "accounts.google.com" in r.headers["Location"]
    assert "fake-client-id.apps.googleusercontent.com" in r.headers["Location"]


def test_connect_shows_helpful_error_when_not_configured(client):
    app2 = create_app(TestConfig)  # TestConfig has no Google credentials set
    c2 = app2.test_client()
    c2.post("/setup", data={"name": "M", "email": "m@example.com", "password": "testpass123", "confirm": "testpass123"})
    r = c2.get("/calendar/connect", follow_redirects=True)
    assert b"isn&#39;t configured" in r.data or b"is not configured" in r.data or b"isn't configured" in r.data


def test_oauth_callback_stores_encrypted_refresh_token(client, monkeypatch):
    _create_owner(client)

    from app.routes import calendar as calendar_routes

    monkeypatch.setattr(
        calendar_routes.google_calendar_client,
        "exchange_code_for_refresh_token",
        lambda flow, url: ("real-looking-refresh-token", "murtaza@gmail.com"),
    )

    r = client.get("/calendar/oauth/callback?code=fakecode&state=fakestate", follow_redirects=True)
    assert b"connected" in r.data.lower()

    from app.models import CalendarAccount

    with client.application.app_context():
        account = CalendarAccount.query.first()
        assert account is not None
        assert account.google_email == "murtaza@gmail.com"
        # The plaintext token must never be stored directly.
        assert "real-looking-refresh-token" not in account.encrypted_refresh_token


def test_oauth_callback_handles_google_error_param(client):
    _create_owner(client)
    r = client.get("/calendar/oauth/callback?error=access_denied", follow_redirects=True)
    assert b"cancelled" in r.data.lower() or b"failed" in r.data.lower()

    from app.models import CalendarAccount

    with client.application.app_context():
        assert CalendarAccount.query.count() == 0


def test_disconnect_removes_account_but_keeps_sessions(client, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)

    from app.routes import calendar as calendar_routes

    monkeypatch.setattr(
        calendar_routes.google_calendar_client,
        "fetch_events",
        lambda *a, **k: [_fake_event("e1", "SBHS", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00")],
    )
    client.post("/calendar/mappings/add", data={"title_pattern": "SBHS", "source_id": "1"})
    client.post("/calendar/sync")

    from app.models import CalendarAccount
    from app.models import Session as SessionModel

    with client.application.app_context():
        assert SessionModel.query.count() == 1

    client.post("/calendar/disconnect")
    with client.application.app_context():
        assert CalendarAccount.query.count() == 0
        assert SessionModel.query.count() == 1  # session preserved


# ---------------- Full sync, mocked at the network boundary ----------------


def _mock_fetch(monkeypatch, events):
    from app.routes import calendar as calendar_routes

    monkeypatch.setattr(calendar_routes.google_calendar_client, "fetch_events", lambda *a, **k: list(events))


def test_first_sync_imports_and_calculates_correctly(client, monkeypatch):
    """The exact blueprint example: 4:50 PM-6:30 PM -> 100 minutes, calculated by the deterministic engine."""
    _create_owner(client)
    _setup_sources_and_rates(client, rate="250")
    _connect_fake_account(client)
    client.post("/calendar/mappings/add", data={"title_pattern": "SGHS", "source_id": "2"})

    _mock_fetch(monkeypatch, [_fake_event("e1", "SGHS", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00")])
    r = client.post("/calendar/sync", follow_redirects=True)
    assert b"1 imported" in r.data

    from app.models import Session as SessionModel

    with client.application.app_context():
        s = SessionModel.query.first()
        assert s.duration_minutes == 100
        assert s.calculated_amount == Decimal("416.67")  # (100/60)*250, no rounding then displayed
        assert s.mode == "EXACT_TIME"


def test_second_sync_does_not_duplicate(client, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)
    client.post("/calendar/mappings/add", data={"title_pattern": "SBHS", "source_id": "1"})

    events = [_fake_event("e1", "SBHS", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00")]
    _mock_fetch(monkeypatch, events)
    client.post("/calendar/sync")
    client.post("/calendar/sync")  # sync again, same events

    from app.models import Session as SessionModel

    with client.application.app_context():
        assert SessionModel.query.count() == 1  # not 2


def test_event_edit_updates_linked_session(client, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)
    client.post("/calendar/mappings/add", data={"title_pattern": "SBHS", "source_id": "1"})

    _mock_fetch(monkeypatch, [_fake_event("e1", "SBHS", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00")])
    client.post("/calendar/sync")

    from app.models import Session as SessionModel

    with client.application.app_context():
        original_id = SessionModel.query.first().id
        assert SessionModel.query.first().duration_minutes == 100

    # Event edited: 4:50-6:30 -> 4:50-6:15
    _mock_fetch(monkeypatch, [_fake_event("e1", "SBHS", "2026-08-14T16:50:00+00:00", "2026-08-14T18:15:00+00:00")])
    r = client.post("/calendar/sync", follow_redirects=True)
    assert b"1 updated" in r.data

    with client.application.app_context():
        assert SessionModel.query.count() == 1  # same session, not a new one
        s = SessionModel.query.get(original_id)
        assert s.duration_minutes == 85


def test_event_deletion_preserves_session_and_flags_it(client, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)
    client.post("/calendar/mappings/add", data={"title_pattern": "SBHS", "source_id": "1"})

    _mock_fetch(monkeypatch, [_fake_event("e1", "SBHS", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00")])
    client.post("/calendar/sync")

    from app.models import CalendarLink
    from app.models import Session as SessionModel

    with client.application.app_context():
        assert SessionModel.query.count() == 1
        original_amount = SessionModel.query.first().calculated_amount

    # Event deleted upstream: Google now returns it with status=cancelled.
    _mock_fetch(monkeypatch, [_fake_event("e1", "SBHS", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00", status="cancelled")])
    r = client.post("/calendar/sync", follow_redirects=True)
    assert b"1 deleted upstream" in r.data

    with client.application.app_context():
        assert SessionModel.query.count() == 1  # session NOT deleted
        assert SessionModel.query.first().calculated_amount == original_amount  # amount preserved
        link = CalendarLink.query.first()
        assert link.source_deleted is True


def test_unknown_event_becomes_draft_requiring_review(client, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)
    # No mapping rules configured at all.

    _mock_fetch(monkeypatch, [_fake_event("e1", "Random unrelated meeting", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00")])
    r = client.post("/calendar/sync", follow_redirects=True)
    assert b"need review" in r.data

    from app.models import CalendarDraft
    from app.models import Session as SessionModel

    with client.application.app_context():
        assert SessionModel.query.count() == 0  # nothing guessed
        draft = CalendarDraft.query.first()
        assert draft is not None
        assert draft.status == "pending"


def test_resolving_a_draft_creates_the_session(client, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)

    _mock_fetch(monkeypatch, [_fake_event("e1", "Mystery event", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00")])
    client.post("/calendar/sync")

    from app.models import CalendarDraft
    from app.models import Session as SessionModel

    with client.application.app_context():
        draft_id = CalendarDraft.query.first().id

    r = client.post(f"/calendar/drafts/{draft_id}/resolve", data={"source_id": "2"}, follow_redirects=True)
    assert b"saved" in r.data.lower()

    with client.application.app_context():
        assert SessionModel.query.count() == 1
        assert CalendarDraft.query.get(draft_id).status == "resolved"


def test_ignoring_a_draft_does_not_create_a_session(client, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)

    _mock_fetch(monkeypatch, [_fake_event("e1", "Mystery event", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00")])
    client.post("/calendar/sync")

    from app.models import CalendarDraft

    with client.application.app_context():
        draft_id = CalendarDraft.query.first().id

    client.post(f"/calendar/drafts/{draft_id}/ignore")

    from app.models import Session as SessionModel

    with client.application.app_context():
        assert SessionModel.query.count() == 0
        assert CalendarDraft.query.get(draft_id).status == "ignored"


def test_historical_events_import_correctly(client, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client, rate="250", frm="2020-01-01")
    _connect_fake_account(client)
    client.post("/calendar/mappings/add", data={"title_pattern": "SBHS", "source_id": "1"})

    _mock_fetch(monkeypatch, [_fake_event("e1", "SBHS", "2020-05-01T16:50:00+00:00", "2020-05-01T18:30:00+00:00")])
    client.post("/calendar/sync")

    from app.models import Session as SessionModel

    with client.application.app_context():
        s = SessionModel.query.first()
        assert s.date == date(2020, 5, 1)


def test_multiple_events_mixed_sources_in_one_sync(client, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)
    client.post("/calendar/mappings/add", data={"title_pattern": "SBHS", "source_id": "1"})
    client.post("/calendar/mappings/add", data={"title_pattern": "SGHS", "source_id": "2"})

    _mock_fetch(
        monkeypatch,
        [
            _fake_event("e1", "SBHS", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00"),
            _fake_event("e2", "SGHS", "2026-08-14T19:00:00+00:00", "2026-08-14T20:00:00+00:00"),
            _fake_event("e3", "SBHS", "2026-08-15T16:50:00+00:00", "2026-08-15T18:30:00+00:00"),
        ],
    )
    r = client.post("/calendar/sync", follow_redirects=True)
    assert b"3 imported" in r.data

    from app.models import Session as SessionModel

    with client.application.app_context():
        assert SessionModel.query.count() == 3
        sbhs_count = SessionModel.query.filter_by(source_id=1).count()
        sghs_count = SessionModel.query.filter_by(source_id=2).count()
        assert sbhs_count == 2
        assert sghs_count == 1


def test_rate_change_across_dates_applies_correctly_to_calendar_sessions(client, monkeypatch):
    """Jan session uses 250, an August session (after a July rate change) uses 300 -- same as manual entry."""
    _create_owner(client)
    client.post("/sources/add", data={"name": "SBHS", "mode": "FIXED_HOURS"})
    from app.models import IncomeSource

    with client.application.app_context():
        sbhs_id = IncomeSource.query.filter_by(name="SBHS").first().id
    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "250", "effective_from": "2026-01-01", "effective_to": "2026-06-30"})
    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "300", "effective_from": "2026-07-01"})

    _connect_fake_account(client)
    client.post("/calendar/mappings/add", data={"title_pattern": "SBHS", "source_id": str(sbhs_id)})

    _mock_fetch(
        monkeypatch,
        [
            _fake_event("e1", "SBHS", "2026-01-15T16:50:00+00:00", "2026-01-15T18:30:00+00:00"),
            _fake_event("e2", "SBHS", "2026-08-15T16:50:00+00:00", "2026-08-15T18:30:00+00:00"),
        ],
    )
    client.post("/calendar/sync")

    from app.models import Session as SessionModel

    with client.application.app_context():
        jan_session = SessionModel.query.filter_by(date=date(2026, 1, 15)).first()
        aug_session = SessionModel.query.filter_by(date=date(2026, 8, 15)).first()
        assert jan_session.applied_rate == Decimal("250.0000")
        assert aug_session.applied_rate == Decimal("300.0000")


def test_calendar_event_with_no_rate_defined_becomes_draft_not_crash(client, monkeypatch):
    _create_owner(client)
    client.post("/sources/add", data={"name": "SBHS", "mode": "FIXED_HOURS"})
    # Deliberately no rate added.
    _connect_fake_account(client)
    client.post("/calendar/mappings/add", data={"title_pattern": "SBHS", "source_id": "1"})

    _mock_fetch(monkeypatch, [_fake_event("e1", "SBHS", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00")])
    r = client.post("/calendar/sync", follow_redirects=True)
    assert r.status_code == 200

    from app.models import CalendarDraft
    from app.models import Session as SessionModel

    with client.application.app_context():
        assert SessionModel.query.count() == 0
        assert CalendarDraft.query.count() == 1


def test_all_day_event_does_not_crash_sync(client, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)

    all_day_event = {"id": "e1", "summary": "Conference", "status": "confirmed", "start": {"date": "2026-08-14"}, "end": {"date": "2026-08-15"}}
    _mock_fetch(monkeypatch, [all_day_event])
    r = client.post("/calendar/sync", follow_redirects=True)
    assert r.status_code == 200

    from app.models import Session as SessionModel

    with client.application.app_context():
        assert SessionModel.query.count() == 0


# ---------------- Security: tokens never exposed ----------------


def test_calendar_page_never_renders_raw_token(client, monkeypatch):
    _create_owner(client)
    _connect_fake_account(client)
    r = client.get("/calendar")
    assert b"fake-refresh-token" not in r.data
    assert b"encrypted_refresh_token" not in r.data


def test_sync_log_never_stores_token_in_error_text(client, monkeypatch):
    _create_owner(client)
    _connect_fake_account(client)

    from app.routes import calendar as calendar_routes

    def boom(*a, **k):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(calendar_routes.google_calendar_client, "fetch_events", boom)
    client.post("/calendar/sync")

    from app.models import SyncLog

    with client.application.app_context():
        log = SyncLog.query.first()
        assert log.status == "error"
        assert "fake-refresh-token" not in (log.error_text or "")


# ---------------- Guest isolation still holds ----------------


def test_guest_cannot_reach_calendar_routes(client):
    _create_owner(client)
    client.get("/logout")
    client.get("/guest")
    for path in ["/calendar"]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["Location"] == "/guest"
