import pytest

from app import create_app
from app.config import TestConfig


@pytest.fixture
def app():
    return create_app(TestConfig)


@pytest.fixture
def client(app):
    return app.test_client()


def _create_owner(client):
    return client.post(
        "/setup",
        data={"name": "Murtaza", "email": "murtaza@example.com", "password": "testpass123", "confirm": "testpass123"},
    )


def test_ask_requires_login(client):
    r = client.post("/assistant/ask", json={"question": "how much did I earn this month?"})
    assert r.status_code == 302


def test_ask_blocked_for_guest(client):
    client.get("/guest")
    r = client.post("/assistant/ask", json={"question": "how much did I earn this month?"})
    assert r.status_code == 302


def test_ask_returns_answer_for_owner(client):
    _create_owner(client)
    r = client.post("/assistant/ask", json={"question": "how much did I earn this month?"})
    assert r.status_code == 200
    data = r.get_json()
    assert "answer" in data
    assert "PKR" in data["answer"]


def test_ask_rejects_empty_question(client):
    _create_owner(client)
    r = client.post("/assistant/ask", json={"question": "   "})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_ask_rejects_overlong_question(client):
    _create_owner(client)
    r = client.post("/assistant/ask", json={"question": "x" * 301})
    assert r.status_code == 400


def test_examples_endpoint(client):
    _create_owner(client)
    r = client.get("/assistant/examples")
    assert r.status_code == 200
    assert len(r.get_json()["examples"]) > 0


def test_dashboard_renders_assistant_widget(client):
    _create_owner(client)
    r = client.get("/")
    assert r.status_code == 200
    assert b"assistant-input" in r.data
    assert b"Ask about your earnings" in r.data
