"""Глобальна обробка необроблених помилок Telegram handlers."""

from __future__ import annotations

import html
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
        err_text = html.escape(str(context.error or "unknown"))[:500]
        update_hint = ""
        if update is not None:
            update_id = getattr(update, "update_id", None)
            if update_id is not None:
                update_hint = f"\nupdate_id: <code>{update_id}</code>"

        await context.bot.send_message(
            chat_id=owner_id,
            text=f"⚠️ <b>Bot error</b>\n<code>{err_text}</code>{update_hint}",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Не вдалося сповістити власника про помилку")
