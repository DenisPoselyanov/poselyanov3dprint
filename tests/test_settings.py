import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")


@pytest.fixture
def settings_db(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")

    import config

    importlib.reload(config)

    from services.db_utils import init_db

    init_db()

    import services.settings as settings

    importlib.reload(settings)
    yield settings

    try:
        os.unlink(db_path)
    except OSError:
        pass


def test_coupon_stacking_off_by_default(settings_db):
    assert settings_db.coupon_stacking_enabled() is False


def test_update_admin_settings_toggles_stacking(settings_db):
    result = settings_db.update_admin_settings({"coupon_stacking_enabled": True})
    assert result["ok"] is True
    assert result["settings"]["coupon_stacking_enabled"] is True
    assert settings_db.coupon_stacking_enabled() is True

    settings_db.update_admin_settings({"coupon_stacking_enabled": False})
    assert settings_db.coupon_stacking_enabled() is False


def test_update_admin_settings_ignores_unknown_keys(settings_db):
    result = settings_db.update_admin_settings({"whatever": 1})
    assert result["ok"] is True
    assert result["settings"]["coupon_stacking_enabled"] is False


def test_public_settings_shape(settings_db):
    settings_db.update_admin_settings({"coupon_stacking_enabled": "1"})
    assert settings_db.public_settings() == {"coupon_stacking_enabled": True}
