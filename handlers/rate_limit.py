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


_EVENT_WINDOW_SEC = 60.0
_EVENT_MAX_PER_WINDOW = 30
_event_calls: dict[int, list[float]] = {}


def allow_event(user_id: int) -> bool:
    """Не більше _EVENT_MAX_PER_WINDOW подій аналітики на користувача за хвилину."""
    now = time.monotonic()
    recent = [t for t in _event_calls.get(user_id, ()) if now - t < _EVENT_WINDOW_SEC]
    if len(recent) >= _EVENT_MAX_PER_WINDOW:
        _event_calls[user_id] = recent
        return False
    recent.append(now)
    _event_calls[user_id] = recent
    if len(_event_calls) > 10_000:
        # Не даємо словнику рости безмежно: викидаємо тих, хто давно мовчить.
        for uid in [u for u, ts in _event_calls.items() if not ts or now - ts[-1] >= _EVENT_WINDOW_SEC]:
            _event_calls.pop(uid, None)
    return True


_LOOKUP_MAX_PER_WINDOW = 90
_lookup_calls: dict[int, list[float]] = {}


def allow_delivery_lookup(user_id: int) -> bool:
    """Довідники міст/відділень: не більше 90 запитів на користувача за хвилину."""
    now = time.monotonic()
    recent = [t for t in _lookup_calls.get(user_id, ()) if now - t < _EVENT_WINDOW_SEC]
    if len(recent) >= _LOOKUP_MAX_PER_WINDOW:
        _lookup_calls[user_id] = recent
        return False
    recent.append(now)
    _lookup_calls[user_id] = recent
    if len(_lookup_calls) > 10_000:
        for uid in [u for u, ts in _lookup_calls.items() if not ts or now - ts[-1] >= _EVENT_WINDOW_SEC]:
            _lookup_calls.pop(uid, None)
    return True


def reset_rate_limits() -> None:
    """Скидає in-memory cooldown (корисно для тестів)."""
    _last_call.clear()
    _event_calls.clear()
    _lookup_calls.clear()
