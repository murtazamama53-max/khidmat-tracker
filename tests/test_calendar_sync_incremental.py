"""
Tests for the two features added on top of the existing Calendar
architecture: syncToken incremental sync, and watch()/webhook push
notifications. Reuses the exact fixtures/helpers from test_calendar_routes.py
(_create_owner, _setup_sources_and_rates, _connect_fake_account, _fake_event)
so these tests exercise the real routes end to end, only mocking Google's
network boundary (google_calendar_client functions), never the reconciliation
logic itself.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.config import TestConfig
from app.services.google_calendar_client import SyncTokenExpiredError
from tests.test_calendar_routes import (
    _connect_fake_account,
    _create_owner,
    _fake_event,
    _setup_sources_and_rates,
)


class CalendarTestConfig(TestConfig):
    GOOGLE_CLIENT_ID = "fake-client-id.apps.googleusercontent.com"
    GOOGLE_CLIENT_SECRET = "fake-client-secret"
    GOOGLE_REDIRECT_URI = "https://khidmat.example.com/calendar/oauth/callback"


@pytest.fixture
def app():
    return create_app(CalendarTestConfig)


@pytest.fixture
def client(app):
    return app.test_client()


def _get_account(app):
    from app.models import CalendarAccount

    with app.app_context():
        return CalendarAccount.query.first()


def _account_id(app):
    return _get_account(app).id


def _refresh_account(app, account_id):
    from app.extensions import db
    from app.models import CalendarAccount

    with app.app_context():
        return db.session.get(CalendarAccount, account_id)


# --------------------------------------------------------------------------
# Incremental sync: syncToken is stored after a successful sync, and used
# (not a time window) on the next one.
# --------------------------------------------------------------------------


def test_first_sync_has_no_token_second_sync_uses_syncToken(client, app, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)
    client.post("/calendar/mappings/add", data={"title_pattern": "SBHS", "source_id": "1"})

    from app.routes import calendar as calendar_routes

    calls = []

    def fake_fetch(refresh_token, client_id, client_secret, calendar_id, time_min=None, time_max=None, sync_token=None):
        calls.append({"time_min": time_min, "time_max": time_max, "sync_token": sync_token})
        return [_fake_event("e1", "SBHS", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00")], "sync-token-abc123"

    monkeypatch.setattr(calendar_routes.google_calendar_client, "fetch_events", fake_fetch)

    client.post("/calendar/sync")
    assert len(calls) == 1
    assert calls[0]["sync_token"] is None  # first sync: full time-window fetch
    assert calls[0]["time_min"] is not None

    account_id = _account_id(app)
    account = _refresh_account(app, account_id)
    assert account.sync_token == "sync-token-abc123"

    client.post("/calendar/sync")
    assert len(calls) == 2
    assert calls[1]["sync_token"] == "sync-token-abc123"  # second sync: incremental, no time window
    assert calls[1]["time_min"] is None
    assert calls[1]["time_max"] is None


def test_incremental_sync_reconciles_using_the_same_logic_as_full_sync(client, app, monkeypatch):
    """An incremental fetch's events flow through the identical reconcile()
    path -- duplicate prevention, mapping, calculation are all unaffected
    by which fetch mode produced the events."""
    _create_owner(client)
    _setup_sources_and_rates(client, rate="250")
    _connect_fake_account(client)
    client.post("/calendar/mappings/add", data={"title_pattern": "SBHS", "source_id": "1"})

    from app.routes import calendar as calendar_routes

    monkeypatch.setattr(
        calendar_routes.google_calendar_client,
        "fetch_events",
        lambda *a, **k: ([], "token-1"),
    )
    client.post("/calendar/sync")  # bootstrap: get a sync token, no events yet

    monkeypatch.setattr(
        calendar_routes.google_calendar_client,
        "fetch_events",
        lambda *a, **k: ([_fake_event("e1", "SBHS", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00")], "token-2"),
    )
    client.post("/calendar/sync")  # incremental: one new event appears

    from app.models import Session as SessionModel

    with app.app_context():
        s = SessionModel.query.first()
        assert s is not None
        assert s.duration_minutes == 100  # 4:50-6:30 PM = 100 minutes, same calc engine
        assert str(s.calculated_amount) == "416.67"


# --------------------------------------------------------------------------
# 410 Gone: expired sync token falls back to a full resync automatically
# --------------------------------------------------------------------------


def test_expired_sync_token_falls_back_to_full_resync(client, app, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)
    client.post("/calendar/mappings/add", data={"title_pattern": "SBHS", "source_id": "1"})

    from app.routes import calendar as calendar_routes

    # Bootstrap a stored sync token.
    monkeypatch.setattr(calendar_routes.google_calendar_client, "fetch_events", lambda *a, **k: ([], "stale-token"))
    client.post("/calendar/sync")
    assert _refresh_account(app, _account_id(app)).sync_token == "stale-token"

    calls = []

    def fake_fetch(refresh_token, client_id, client_secret, calendar_id, time_min=None, time_max=None, sync_token=None):
        calls.append(sync_token)
        if sync_token:
            raise SyncTokenExpiredError("token expired")
        return [_fake_event("e1", "SBHS", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00")], "fresh-token"

    monkeypatch.setattr(calendar_routes.google_calendar_client, "fetch_events", fake_fetch)

    r = client.post("/calendar/sync", follow_redirects=True)

    assert calls == ["stale-token", None]  # tried incremental, got 410, fell back to full (sync_token=None)
    assert b"sync complete" in r.data.lower() or b"imported" in r.data.lower()

    account = _refresh_account(app, _account_id(app))
    assert account.sync_token == "fresh-token"  # the fallback's own new token is stored

    from app.models import Session as SessionModel

    with app.app_context():
        assert SessionModel.query.count() == 1  # the fallback's events were still imported


def test_expired_sync_token_does_not_crash_or_lose_the_sync_log(client, app, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)

    from app.routes import calendar as calendar_routes

    monkeypatch.setattr(calendar_routes.google_calendar_client, "fetch_events", lambda *a, **k: ([], "stale"))
    client.post("/calendar/sync")

    def fake_fetch(*a, **k):
        if k.get("sync_token"):
            raise SyncTokenExpiredError("expired")
        return [], "new-token"

    monkeypatch.setattr(calendar_routes.google_calendar_client, "fetch_events", fake_fetch)
    client.post("/calendar/sync")

    from app.models import SyncLog

    with app.app_context():
        logs = SyncLog.query.order_by(SyncLog.id).all()
        assert len(logs) == 2
        assert all(log.status == "success" for log in logs)


# --------------------------------------------------------------------------
# Webhook: header verification, resource-state handling, triggers sync
# --------------------------------------------------------------------------


def _enable_watch(client, app, monkeypatch, resource_id="google-resource-1", expiration_ms=None):
    from app.routes import calendar as calendar_routes

    if expiration_ms is None:
        expiration_ms = int((datetime.now(timezone.utc) + timedelta(days=6)).timestamp() * 1000)

    monkeypatch.setattr(
        calendar_routes.google_calendar_client,
        "create_watch_channel",
        lambda *a, **k: (resource_id, expiration_ms),
    )
    # url_for(_external=True) reflects the *actual* request's scheme, which
    # the test client defaults to plain http -- simulate https here since
    # that's what a real deployed (Vercel + ProxyFix) request looks like,
    # and it's specifically what /calendar/watch/enable requires.
    r = client.post("/calendar/watch/enable", base_url="https://localhost", follow_redirects=True)
    assert b"push sync enabled" in r.data.lower()
    return _refresh_account(app, _account_id(app))


def test_enable_watch_stores_channel_metadata(client, app, monkeypatch):
    _create_owner(client)
    _connect_fake_account(client)
    account = _enable_watch(client, app, monkeypatch)

    assert account.watch_channel_id is not None
    assert account.watch_resource_id == "google-resource-1"
    assert account.watch_channel_token is not None
    assert account.watch_expiration is not None


def test_enable_watch_refuses_over_plain_http(client, app):
    """Google requires a public HTTPS callback -- GOOGLE_REDIRECT_URI in
    tests is https, but the test client itself serves over plain http, so
    url_for(..., _external=True) here resolves to http:// and must be
    rejected with a clear message rather than silently registering a
    channel Google would reject anyway."""
    _create_owner(client)
    _connect_fake_account(client)
    r = client.post("/calendar/watch/enable", follow_redirects=True)
    assert b"https" in r.data.lower()

    account = _get_account(app)
    assert account.watch_channel_id is None


def test_webhook_rejects_missing_headers(client):
    r = client.post("/calendar/webhook")
    assert r.status_code == 400


def test_webhook_rejects_unrecognized_channel_id(client):
    r = client.post(
        "/calendar/webhook",
        headers={"X-Goog-Channel-ID": "nonexistent-channel", "X-Goog-Channel-Token": "whatever", "X-Goog-Resource-State": "exists"},
    )
    assert r.status_code == 404


def test_webhook_rejects_wrong_token_for_a_real_channel(client, app, monkeypatch):
    _create_owner(client)
    _connect_fake_account(client)
    account = _enable_watch(client, app, monkeypatch)

    r = client.post(
        "/calendar/webhook",
        headers={"X-Goog-Channel-ID": account.watch_channel_id, "X-Goog-Channel-Token": "forged-token", "X-Goog-Resource-State": "exists"},
    )
    assert r.status_code == 404  # same response as unrecognized -- does not leak that the channel ID was valid

    from app.models import SyncLog

    with app.app_context():
        assert SyncLog.query.count() == 0  # no sync was triggered by the forged request


def test_webhook_sync_handshake_does_not_trigger_a_sync(client, app, monkeypatch):
    _create_owner(client)
    _connect_fake_account(client)
    account = _enable_watch(client, app, monkeypatch)

    r = client.post(
        "/calendar/webhook",
        headers={"X-Goog-Channel-ID": account.watch_channel_id, "X-Goog-Channel-Token": account.watch_channel_token, "X-Goog-Resource-State": "sync"},
    )
    assert r.status_code == 200

    from app.models import SyncLog

    with app.app_context():
        assert SyncLog.query.count() == 0  # the initial handshake isn't a real change


def test_webhook_with_valid_token_triggers_incremental_sync(client, app, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)
    client.post("/calendar/mappings/add", data={"title_pattern": "SBHS", "source_id": "1"})
    account = _enable_watch(client, app, monkeypatch)

    from app.routes import calendar as calendar_routes

    monkeypatch.setattr(
        calendar_routes.google_calendar_client,
        "fetch_events",
        lambda *a, **k: ([_fake_event("e1", "SBHS", "2026-08-14T16:50:00+00:00", "2026-08-14T18:30:00+00:00")], "token-x"),
    )

    r = client.post(
        "/calendar/webhook",
        headers={"X-Goog-Channel-ID": account.watch_channel_id, "X-Goog-Channel-Token": account.watch_channel_token, "X-Goog-Resource-State": "exists"},
    )
    assert r.status_code == 200

    from app.models import Session as SessionModel
    from app.models import SyncLog

    with app.app_context():
        assert SessionModel.query.count() == 1  # the event was actually imported
        log = SyncLog.query.first()
        assert log.trigger == "webhook"
        assert log.status == "success"


def test_webhook_does_not_require_login_session(client, app, monkeypatch):
    """The whole point of header-token verification: Google has no
    session cookie for this app at all."""
    _create_owner(client)
    _connect_fake_account(client)
    account = _enable_watch(client, app, monkeypatch)
    client.get("/logout")  # explicitly no session

    r = client.post(
        "/calendar/webhook",
        headers={"X-Goog-Channel-ID": account.watch_channel_id, "X-Goog-Channel-Token": account.watch_channel_token, "X-Goog-Resource-State": "sync"},
    )
    assert r.status_code == 200


# --------------------------------------------------------------------------
# Disable / disconnect cleanup
# --------------------------------------------------------------------------


def test_disable_watch_stops_channel_and_clears_metadata(client, app, monkeypatch):
    _create_owner(client)
    _connect_fake_account(client)
    _enable_watch(client, app, monkeypatch)

    from app.routes import calendar as calendar_routes

    stop_calls = []
    monkeypatch.setattr(
        calendar_routes.google_calendar_client,
        "stop_watch_channel",
        lambda *a, **k: stop_calls.append(a),
    )

    r = client.post("/calendar/watch/disable", follow_redirects=True)
    assert b"push sync disabled" in r.data.lower()
    assert len(stop_calls) == 1

    account = _get_account(app)
    assert account.watch_channel_id is None
    assert account.watch_resource_id is None
    assert account.watch_channel_token is None
    assert account.watch_expiration is None


def test_disconnect_stops_watch_channel_too(client, app, monkeypatch):
    _create_owner(client)
    _connect_fake_account(client)
    _enable_watch(client, app, monkeypatch)

    from app.routes import calendar as calendar_routes

    stop_calls = []
    monkeypatch.setattr(
        calendar_routes.google_calendar_client,
        "stop_watch_channel",
        lambda *a, **k: stop_calls.append(a),
    )

    client.post("/calendar/disconnect")
    assert len(stop_calls) == 1

    from app.models import CalendarAccount

    with app.app_context():
        assert CalendarAccount.query.count() == 0


def test_webhook_after_disable_no_longer_works(client, app, monkeypatch):
    _create_owner(client)
    _connect_fake_account(client)
    account = _enable_watch(client, app, monkeypatch)
    channel_id, channel_token = account.watch_channel_id, account.watch_channel_token

    from app.routes import calendar as calendar_routes

    monkeypatch.setattr(calendar_routes.google_calendar_client, "stop_watch_channel", lambda *a, **k: None)
    client.post("/calendar/watch/disable")

    r = client.post(
        "/calendar/webhook",
        headers={"X-Goog-Channel-ID": channel_id, "X-Goog-Channel-Token": channel_token, "X-Goog-Resource-State": "exists"},
    )
    assert r.status_code == 404


# --------------------------------------------------------------------------
# Auto-renewal: a near-expiry channel is renewed as a side effect of sync
# --------------------------------------------------------------------------


def test_sync_auto_renews_a_soon_to_expire_watch_channel(client, app, monkeypatch):
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)

    # Enable watch with an expiration only 1 hour away (well inside the
    # renewal window).
    soon = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000)
    old_account = _enable_watch(client, app, monkeypatch, resource_id="old-resource", expiration_ms=soon)
    old_channel_id = old_account.watch_channel_id

    from app.routes import calendar as calendar_routes

    monkeypatch.setattr(calendar_routes.google_calendar_client, "fetch_events", lambda *a, **k: ([], "tok"))
    monkeypatch.setattr(
        calendar_routes.google_calendar_client,
        "create_watch_channel",
        lambda *a, **k: ("new-resource", int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp() * 1000)),
    )
    stopped = []
    monkeypatch.setattr(calendar_routes.google_calendar_client, "stop_watch_channel", lambda *a, **k: stopped.append(a))

    client.post("/calendar/sync")

    account = _get_account(app)
    assert account.watch_resource_id == "new-resource"
    assert account.watch_channel_id != old_channel_id  # replaced, not reused
    assert len(stopped) == 1  # the old channel was explicitly stopped


def test_sync_does_not_touch_watch_channel_when_not_enabled(client, app, monkeypatch):
    """No silent opt-in: if push sync was never enabled, a plain sync must
    never create a watch channel on its own."""
    _create_owner(client)
    _setup_sources_and_rates(client)
    _connect_fake_account(client)

    from app.routes import calendar as calendar_routes

    monkeypatch.setattr(calendar_routes.google_calendar_client, "fetch_events", lambda *a, **k: ([], "tok"))

    def fail_if_called(*a, **k):
        raise AssertionError("create_watch_channel must not be called when push sync was never enabled")

    monkeypatch.setattr(calendar_routes.google_calendar_client, "create_watch_channel", fail_if_called)

    client.post("/calendar/sync")  # must not raise

    account = _get_account(app)
    assert account.watch_channel_id is None
