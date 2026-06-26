import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_CONFIG_KEYS = (
    "BOT_TOKEN",
    "OWNER_ID",
    "DB_BACKEND",
    "DATABASE_URL",
    "SUPABASE_DB_URL",
    "VALIDATE_INIT_DATA",
    "LOCAL_DEV_MODE",
    "ALLOW_LOCAL_NETWORK",
    "API_PUBLIC_URL",
)


@pytest.fixture
def config_module(monkeypatch):
    for key in _CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)

    def _load(env: dict[str, str]):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        import config

        return importlib.reload(config)

    yield _load

    monkeypatch.setenv("LOCAL_DEV_MODE", "true")
    monkeypatch.setenv("VALIDATE_INIT_DATA", "false")
    monkeypatch.delenv("DB_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    import config

    importlib.reload(config)


def test_validate_startup_config_requires_api_public_url_in_production(config_module):
    config = config_module(
        {
            "BOT_TOKEN": "test-token",
            "OWNER_ID": "123",
            "LOCAL_DEV_MODE": "false",
            "VALIDATE_INIT_DATA": "true",
            "API_PUBLIC_URL": "",
        }
    )
    with pytest.raises(RuntimeError, match="API_PUBLIC_URL is required"):
        config.validate_startup_config()


def test_validate_startup_config_allows_missing_api_public_url_in_local_dev(config_module):
    config = config_module(
        {
            "BOT_TOKEN": "test-token",
            "OWNER_ID": "123",
            "LOCAL_DEV_MODE": "true",
            "VALIDATE_INIT_DATA": "false",
            "API_PUBLIC_URL": "",
        }
    )
    config.validate_startup_config()


def test_validate_startup_config_postgres_requires_database_url(config_module):
    config = config_module(
        {
            "BOT_TOKEN": "test-token",
            "OWNER_ID": "123",
            "LOCAL_DEV_MODE": "true",
            "VALIDATE_INIT_DATA": "false",
            "DB_BACKEND": "postgres",
            "API_PUBLIC_URL": "https://api.example.com",
        }
    )
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        config.validate_startup_config()


def test_drop_pending_updates_defaults_to_false(config_module):
    config = config_module(
        {
            "BOT_TOKEN": "test-token",
            "OWNER_ID": "123",
            "LOCAL_DEV_MODE": "true",
            "VALIDATE_INIT_DATA": "false",
        }
    )
    assert config.DROP_PENDING_UPDATES is False


def test_drop_pending_updates_can_be_enabled(config_module):
    config = config_module(
        {
            "BOT_TOKEN": "test-token",
            "OWNER_ID": "123",
            "LOCAL_DEV_MODE": "true",
            "VALIDATE_INIT_DATA": "false",
            "DROP_PENDING_UPDATES": "true",
        }
    )
    assert config.DROP_PENDING_UPDATES is True
