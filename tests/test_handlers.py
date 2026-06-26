import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from handlers.register import TELEGRAM_ALLOWED_UPDATES


def test_telegram_allowed_updates_filters_to_message_and_callback():
    assert TELEGRAM_ALLOWED_UPDATES == ["message", "callback_query"]


def test_telegram_error_handler_notifies_owner():
    from handlers.errors import telegram_error_handler

    bot = AsyncMock()
    bot.send_message = AsyncMock()
    context = MagicMock()
    context.error = ValueError("test failure")
    context.bot = bot
    context.application.bot_data = {"owner_id": 12345}

    update = MagicMock()
    update.update_id = 99

    asyncio.run(telegram_error_handler(update, context))

    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 12345
    assert "test failure" in kwargs["text"]


def test_telegram_error_handler_skips_without_owner():
    from handlers.errors import telegram_error_handler

    bot = AsyncMock()
    context = MagicMock()
    context.error = RuntimeError("boom")
    context.bot = bot
    context.application.bot_data = {}

    asyncio.run(telegram_error_handler(None, context))

    bot.send_message.assert_not_called()
