import re

import pytest

from app import create_app
from app.config import Config


class CSRFEnabledTestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = True
    SECRET_KEY = "csrf-test-secret-key"


@pytest.fixture
def app():
    return create_app(CSRFEnabledTestConfig)


@pytest.fixture
def client(app):
    return app.test_client()


def test_post_without_csrf_token_is_rejected(client):
    r = client.post(
        "/setup",
        data={"name": "Murtaza", "email": "murtaza@example.com", "password": "testpass123", "confirm": "testpass123"},
    )
    # Graceful recovery, not a dead-end raw error page: redirected with a
    # flash message, and -- this is the actual security guarantee --
    # the account must NOT have been created.
    assert r.status_code == 302
    assert "/login" in r.headers["Location"] or "/setup" in r.headers["Location"]

    r2 = client.get(r.headers["Location"], follow_redirects=True)
    assert b"took a bit too long" in r2.data or b"refresh" in r2.data

    from app.models import User

    with client.application.app_context():
        assert User.query.count() == 0  # the account must NOT have been created


def test_post_with_valid_csrf_token_succeeds(client):
    get_resp = client.get("/setup")
    match = re.search(r'name="csrf_token" value="([^"]+)"', get_resp.data.decode())
    assert match, "Setup form must render a csrf_token field"
    token = match.group(1)

    r = client.post(
        "/setup",
        data={
            "name": "Murtaza",
            "email": "murtaza@example.com",
            "password": "testpass123",
            "confirm": "testpass123",
            "csrf_token": token,
        },
    )
    assert r.status_code == 302

    from app.models import User

    with client.application.app_context():
        assert User.query.count() == 1


def test_post_with_forged_csrf_token_is_rejected(client):
    r = client.post(
        "/setup",
        data={
            "name": "Murtaza",
            "email": "murtaza@example.com",
            "password": "testpass123",
            "confirm": "testpass123",
            "csrf_token": "totally-forged-value",
        },
    )
    assert r.status_code == 302  # graceful redirect, not a raw 400 page

    from app.models import User

    with client.application.app_context():
        assert User.query.count() == 0


def test_csrf_error_on_json_endpoint_returns_json_not_a_redirect(client):
    """
    AJAX endpoints (quick-add, assistant) send/parse JSON and would break
    if a CSRF failure handed them a redirect to an HTML page instead.
    """
    r = client.post(
        "/sessions/parse-preview",
        json={"text": "Sbhs(7)", "date": "2026-08-10"},
        headers={"X-CSRFToken": "totally-forged-value"},
    )
    assert r.status_code == 400
    assert r.is_json
    assert "error" in r.get_json()
