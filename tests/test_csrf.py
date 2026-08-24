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
    assert r.status_code == 400

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
    assert r.status_code == 400

    from app.models import User

    with client.application.app_context():
        assert User.query.count() == 0
