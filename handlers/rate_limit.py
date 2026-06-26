"""Per-user cooldown для захисту від flood на популярних командах."""

from __future__ import annotations

import asyncio
import time
from functools import wraps
from typing import Callable, TypeVar

from telegram import Update
from telegram.ext import ContextTypes

F = TypeVar("F", bound=Callable)

_lock = asyncio.Lock()
_last_call: dict[tuple[int, str], float] = {}


def command_cooldown(seconds: float) -> Callable[[F], F]:
    """Ігнорує повторні виклики тієї ж команди від одного user_id протягом cooldown."""

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user = update.effective_user
            if not user:
                return await func(update, context)

            key = (user.id, func.__name__)
            now = time.monotonic()
            async with _lock:
                last = _last_call.get(key, 0.0)
                if now - last < seconds:
                    return
                _last_call[key] = now

            return await func(update, context)

        return wrapper  # type: ignore[return-value]

    return decorator


def reset_rate_limits() -> None:
    """Скидає in-memory cooldown (корисно для тестів)."""
    _last_call.clear()
