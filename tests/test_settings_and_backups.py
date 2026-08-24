import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.config import Config, TestConfig


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


# ---------------- PIN lock ----------------


def test_settings_page_shows_pin_off_by_default(client):
    _create_owner(client)
    r = client.get("/settings")
    assert b"PIN lock is off" in r.data


def test_set_pin_requires_4_to_8_digits(client):
    _create_owner(client)
    r = client.post("/settings/pin/set", data={"pin": "12", "confirm_pin": "12"}, follow_redirects=True)
    assert b"4-8 digits" in r.data

    r2 = client.post("/settings/pin/set", data={"pin": "abcd", "confirm_pin": "abcd"}, follow_redirects=True)
    assert b"4-8 digits" in r2.data


def test_set_pin_requires_matching_confirmation(client):
    _create_owner(client)
    r = client.post("/settings/pin/set", data={"pin": "1234", "confirm_pin": "5678"}, follow_redirects=True)
    assert b"do not match" in r.data


def test_set_pin_success(client):
    _create_owner(client)
    r = client.post("/settings/pin/set", data={"pin": "1234", "confirm_pin": "1234"}, follow_redirects=True)
    assert b"PIN lock is on" in r.data

    from app.models import User

    with client.application.app_context():
        user = User.query.first()
        assert user.pin_hash is not None
        assert user.check_pin("1234")
        assert not user.check_pin("0000")


def test_lock_requires_pin_to_be_set_first(client):
    _create_owner(client)
    r = client.post("/lock", follow_redirects=True)
    assert b"Set an app-lock PIN first" in r.data


def test_lock_then_owner_routes_redirect_to_unlock(client):
    _create_owner(client)
    client.post("/settings/pin/set", data={"pin": "1234", "confirm_pin": "1234"})
    client.post("/lock")

    for path in ["/", "/sessions", "/rates", "/students", "/reports", "/goals", "/calendar", "/settings"]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["Location"] == "/unlock", path


def test_wrong_pin_does_not_unlock(client):
    _create_owner(client)
    client.post("/settings/pin/set", data={"pin": "1234", "confirm_pin": "1234"})
    client.post("/lock")

    r = client.post("/unlock", data={"pin": "9999"}, follow_redirects=True)
    assert b"Incorrect PIN" in r.data

    r2 = client.get("/", follow_redirects=False)
    assert r2.headers["Location"] == "/unlock"


def test_correct_pin_unlocks_and_restores_access(client):
    _create_owner(client)
    client.post("/settings/pin/set", data={"pin": "1234", "confirm_pin": "1234"})
    client.post("/lock")

    r = client.post("/unlock", data={"pin": "1234"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"] == "/"

    r2 = client.get("/")
    assert r2.status_code == 200


def test_auto_lock_triggers_after_idle_timeout(client):
    _create_owner(client)
    client.post("/settings/pin/set", data={"pin": "1234", "confirm_pin": "1234"})
    client.application.config["PIN_AUTO_LOCK_MINUTES"] = 10

    with client.session_transaction() as sess:
        sess["last_activity"] = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()

    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"] == "/unlock"


def test_recent_activity_does_not_trigger_auto_lock(client):
    _create_owner(client)
    client.post("/settings/pin/set", data={"pin": "1234", "confirm_pin": "1234"})
    client.application.config["PIN_AUTO_LOCK_MINUTES"] = 10

    with client.session_transaction() as sess:
        sess["last_activity"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()

    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200


def test_removing_pin_disables_lock_enforcement(client):
    _create_owner(client)
    client.post("/settings/pin/set", data={"pin": "1234", "confirm_pin": "1234"})
    client.post("/settings/pin/remove")

    from app.models import User

    with client.application.app_context():
        assert User.query.first().pin_hash is None

    # Even with a stale 'locked' flag somehow set, no PIN means no enforcement.
    with client.session_transaction() as sess:
        sess["locked"] = True

    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200


def test_guest_never_subject_to_owner_pin_lock(client):
    _create_owner(client)
    client.post("/settings/pin/set", data={"pin": "1234", "confirm_pin": "1234"})
    client.post("/lock")
    client.get("/logout")  # session.clear() wipes locked/pin state for this browser session

    r = client.get("/guest")
    assert r.status_code == 200  # guest workspace never gated by the owner's PIN


def test_unlock_page_rejects_unauthenticated_visitor(client):
    _create_owner(client)
    client.get("/logout")
    r = client.get("/unlock", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/login")


# ---------------- Backups (need a real file-based DB) ----------------


@pytest.fixture
def file_app():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        backup_dir = os.path.join(tmp, "backups")

        class FileConfig(Config):
            TESTING = True
            WTF_CSRF_ENABLED = False
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
            BACKUP_DIR = backup_dir
            SECRET_KEY = "file-test-secret"

        yield create_app(FileConfig)


@pytest.fixture
def file_client(file_app):
    return file_app.test_client()


def test_backup_now_creates_downloadable_backup(file_client):
    _create_owner(file_client)
    r = file_client.post("/settings/backups/create", follow_redirects=True)
    assert b"Backup created" in r.data

    import re

    m = re.search(r"khidmat-backup-[\d-]+-[0-9a-f]{8}\.db\.enc", r.data.decode())
    filename = m.group(0)

    r2 = file_client.get(f"/settings/backups/{filename}/download")
    assert r2.status_code == 200
    assert r2.data[:16] == b"SQLite format 3\x00"


def test_backup_download_rejects_path_traversal(file_client):
    _create_owner(file_client)
    file_client.post("/settings/backups/create")

    for attempt in ["../../../etc/passwd", "..%2f..%2fetc%2fpasswd"]:
        r = file_client.get(f"/settings/backups/{attempt}/download", follow_redirects=True)
        assert b"root:" not in r.data
        assert b"not a valid backup filename" in r.data


def test_restore_reverts_data_and_creates_safety_backup(file_client):
    _create_owner(file_client)
    file_client.post("/sources/add", data={"name": "SBHS", "mode": "FIXED_HOURS"})
    from app.models import IncomeSource

    with file_client.application.app_context():
        sbhs_id = IncomeSource.query.filter_by(name="SBHS").first().id
    file_client.post("/rates/add", data={"source_id": sbhs_id, "rate": "250", "effective_from": "2026-01-01"})

    file_client.post("/settings/backups/create")
    import re

    r = file_client.get("/settings")
    backup_before = re.search(r"khidmat-backup-[\d-]+-[0-9a-f]{8}\.db\.enc", r.data.decode()).group(0)

    r2 = file_client.post("/sessions/parse-preview", json={"text": "Sbhs(7)", "date": "2026-08-14"})
    preview = r2.get_json()
    file_client.post("/sessions/confirm", json={"date": preview["date"], "raw_text": "Sbhs(7)", "items": preview["items"]})

    from app.models import Session as SessionModel

    with file_client.application.app_context():
        assert SessionModel.query.count() == 1

    r3 = file_client.post(f"/settings/backups/{backup_before}/restore", follow_redirects=True)
    assert b"restored" in r3.data.lower()

    with file_client.application.app_context():
        assert SessionModel.query.count() == 0  # reverted to pre-session state

    r4 = file_client.get("/settings")
    all_backups = set(re.findall(r"khidmat-backup-[\d-]+-[0-9a-f]{8}\.db\.enc", r4.data.decode()))
    assert len(all_backups) == 2  # original + automatic safety backup of pre-restore state


def test_restore_rejects_path_traversal(file_client):
    _create_owner(file_client)
    r = file_client.post("/settings/backups/..%2f..%2fetc%2fpasswd/restore", follow_redirects=True)
    assert b"not a valid backup filename" in r.data


def test_backup_actions_require_owner_auth(file_client):
    r = file_client.post("/settings/backups/create", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"] or "/setup" in r.headers["Location"]
