"""
Tests for the exact production crashes this app hit on Vercel:
  - PIN_AUTO_LOCK_MINUTES="" -> int("") crash
  - SECRET_KEY="" -> Flask treats it as "no secret key", crashing /setup
  - os.makedirs(app.instance_path) on Vercel's read-only filesystem

Config reads os.environ at *module import time* (class-body level), so
these tests set env vars via monkeypatch and then importlib.reload the
config module (and, where create_app() behavior matters, the app package
too) rather than assuming a live re-read on every access.
"""
import importlib
import os

import pytest


@pytest.fixture(autouse=True)
def _reload_config_after_each_test():
    """Undo the module-level reloads this file does, so other test files
    always see the real Config class reflecting the real environment."""
    yield
    import app.config as config_mod

    importlib.reload(config_mod)


def _fresh_config(monkeypatch, **env):
    """Set the given env vars (None means 'unset'), then reload app.config
    and return the freshly-evaluated Config class."""
    for key in ("SECRET_KEY", "FLASK_ENV", "DATABASE_URL", "PIN_AUTO_LOCK_MINUTES",
                "SESSION_COOKIE_SECURE", "VERCEL", "BACKUP_DIR", "GOOGLE_REDIRECT_URI"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        if value is not None:
            monkeypatch.setenv(key, value)

    import app.config as config_mod

    importlib.reload(config_mod)
    return config_mod.Config


# --------------------------------------------------------------------------
# PIN_AUTO_LOCK_MINUTES -- blank / invalid / valid
# --------------------------------------------------------------------------

def test_pin_auto_lock_minutes_blank_falls_back_to_default(monkeypatch):
    cfg = _fresh_config(monkeypatch, PIN_AUTO_LOCK_MINUTES="")
    assert cfg.PIN_AUTO_LOCK_MINUTES == 10


def test_pin_auto_lock_minutes_missing_falls_back_to_default(monkeypatch):
    cfg = _fresh_config(monkeypatch)
    assert cfg.PIN_AUTO_LOCK_MINUTES == 10


def test_pin_auto_lock_minutes_non_numeric_falls_back_to_default(monkeypatch):
    cfg = _fresh_config(monkeypatch, PIN_AUTO_LOCK_MINUTES="not-a-number")
    assert cfg.PIN_AUTO_LOCK_MINUTES == 10


def test_pin_auto_lock_minutes_valid_value_is_used(monkeypatch):
    cfg = _fresh_config(monkeypatch, PIN_AUTO_LOCK_MINUTES="30")
    assert cfg.PIN_AUTO_LOCK_MINUTES == 30


# --------------------------------------------------------------------------
# SECRET_KEY -- blank / missing / set, and its interaction with production
# --------------------------------------------------------------------------

def test_secret_key_blank_falls_back_to_placeholder(monkeypatch):
    cfg = _fresh_config(monkeypatch, SECRET_KEY="")
    assert cfg.SECRET_KEY == "dev-key-change-me-in-.env"


def test_secret_key_missing_falls_back_to_placeholder(monkeypatch):
    cfg = _fresh_config(monkeypatch)
    assert cfg.SECRET_KEY == "dev-key-change-me-in-.env"


def test_secret_key_real_value_is_used(monkeypatch):
    cfg = _fresh_config(monkeypatch, SECRET_KEY="a-real-production-secret")
    assert cfg.SECRET_KEY == "a-real-production-secret"


def test_blank_secret_key_in_production_refuses_to_start_with_clear_error(monkeypatch):
    cfg = _fresh_config(monkeypatch, SECRET_KEY="", FLASK_ENV="production", DATABASE_URL="sqlite:///:memory:")
    import importlib as _il

    import app as app_pkg

    _il.reload(app_pkg)
    with pytest.raises(RuntimeError, match="Refusing to start"):
        app_pkg.create_app(cfg)


def test_blank_secret_key_in_development_does_not_crash_setup(monkeypatch):
    cfg = _fresh_config(monkeypatch, SECRET_KEY="", FLASK_ENV="development", DATABASE_URL="sqlite:///:memory:")
    import importlib as _il

    import app as app_pkg

    _il.reload(app_pkg)
    app = app_pkg.create_app(cfg)
    client = app.test_client()
    r = client.get("/setup")
    assert r.status_code == 200  # not the old "no secret key was set" RuntimeError


# --------------------------------------------------------------------------
# SESSION_COOKIE_SECURE -- production-aware default
# --------------------------------------------------------------------------

def test_session_cookie_secure_defaults_true_in_production_when_unset(monkeypatch):
    cfg = _fresh_config(monkeypatch, FLASK_ENV="production", SECRET_KEY="real-key")
    assert cfg.SESSION_COOKIE_SECURE is True


def test_session_cookie_secure_defaults_false_in_development_when_unset(monkeypatch):
    cfg = _fresh_config(monkeypatch, FLASK_ENV="development")
    assert cfg.SESSION_COOKIE_SECURE is False


def test_session_cookie_secure_explicit_false_respected_even_in_production(monkeypatch):
    cfg = _fresh_config(monkeypatch, FLASK_ENV="production", SECRET_KEY="real-key", SESSION_COOKIE_SECURE="false")
    assert cfg.SESSION_COOKIE_SECURE is False


# --------------------------------------------------------------------------
# Read-only Vercel filesystem -- os.makedirs(instance_path) must never run
# --------------------------------------------------------------------------

def test_instance_path_never_created_when_on_serverless(monkeypatch):
    cfg = _fresh_config(monkeypatch, VERCEL="1", SECRET_KEY="real-key", DATABASE_URL="sqlite:////tmp/vercel_test.db")
    assert cfg.ON_SERVERLESS is True

    import importlib as _il

    import app as app_pkg

    _il.reload(app_pkg)

    original_makedirs = os.makedirs

    def _forbidden_makedirs(path, *args, **kwargs):
        if "instance" in str(path):
            raise AssertionError(f"os.makedirs must never be called for instance_path when ON_SERVERLESS, got: {path}")
        return original_makedirs(path, *args, **kwargs)

    monkeypatch.setattr(os, "makedirs", _forbidden_makedirs)
    app = app_pkg.create_app(cfg)  # must not raise
    assert app.config["ON_SERVERLESS"] is True
