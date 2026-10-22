"""Глобальна обробка необроблених помилок Telegram handlers."""

from __future__ import annotations


import logging

from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логує traceback і сповіщає власника про критичні збої."""
    logger.error("Необроблена помилка в Telegram handler", exc_info=context.error)

    owner_id = context.application.bot_data.get("owner_id", 0)
    if not owner_id:
        return

    try:
        from rich_messages import build_bot_error, send_rich_message

        err_text = str(context.error or "unknown")[:500]
        update_id = getattr(update, "update_id", None) if update is not None else None
        await send_rich_message(context.bot, owner_id, build_bot_error(err_text, update_id))
    except Exception:
        logger.exception("Не вдалося сповістити власника про помилку")
