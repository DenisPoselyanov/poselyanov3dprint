import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from handlers.rate_limit import command_cooldown, reset_rate_limits
from rich_messages import format_date


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    reset_rate_limits()
    yield
    reset_rate_limits()


def test_command_cooldown_blocks_rapid_repeats():
    calls = 0

    @command_cooldown(1.0)
    async def handler(update, context):
        nonlocal calls
        calls += 1

    user = MagicMock()
    user.id = 42
    update = MagicMock()
    update.effective_user = user

    asyncio.run(handler(update, None))
    asyncio.run(handler(update, None))

    assert calls == 1


def test_command_cooldown_allows_after_interval(monkeypatch):
    calls = 0
    clock = {"now": 100.0}

    monkeypatch.setattr("handlers.rate_limit.time.monotonic", lambda: clock["now"])

    @command_cooldown(2.0)
    async def handler(update, context):
        nonlocal calls
        calls += 1

    user = MagicMock()
    user.id = 7
    update = MagicMock()
    update.effective_user = user

    asyncio.run(handler(update, None))
    clock["now"] += 2.1
    asyncio.run(handler(update, None))

    assert calls == 2


def test_format_date_escapes_invalid_input():
    result = format_date("<script>alert(1)</script>")
    assert "<" not in result
    assert "&lt;" in result


def test_format_date_parses_valid_iso_date():
    assert format_date("2026-06-26") == "26 чер 2026"


def _signed_init_data(token: str, fields: dict) -> str:
    import hashlib
    import hmac
    from urllib.parse import urlencode

    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})


def test_init_data_without_auth_date_is_rejected(monkeypatch):
    import time

    import auth
    import config

    monkeypatch.setattr(config, "BOT_TOKEN", "123:test")
    user = '{"id":42,"first_name":"A"}'
    assert auth.validate_telegram_init_data(_signed_init_data("123:test", {"user": user})) is None
    ok = auth.validate_telegram_init_data(
        _signed_init_data("123:test", {"user": user, "auth_date": str(int(time.time()))})
    )
    assert ok and ok["user_id"] == 42


def test_coupon_check_is_rate_limited():
    from handlers.rate_limit import allow_coupon_check

    assert all(allow_coupon_check(7) for _ in range(20))
    assert not allow_coupon_check(7)
    assert allow_coupon_check(8)
