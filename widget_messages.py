"""Відправка й оновлення HTML-віджетів (Bot API 10.4) — методи, яких ще немає в python-telegram-bot.

Виклики йдуть через `bot.do_api_request`, як `sendRichMessage` у rich_messages.py.
`sync_order_widget` тримає віджет замовлення в актуальному стані; помилку кидає назовні —
той, хто викликає, сам вирішує, чи слати запасне текстове повідомлення.
"""

from __future__ import annotations

import asyncio
import logging

from db_core import run_db
from rich_messages import _message_id, _serialize_markup, _telegram_retry, is_telegram_rate_limited
from services import order_widgets as ow
from services.orders import get_order_with_items
from services.settings import review_bonus_enabled

logger = logging.getLogger(__name__)

_locks: dict[int, asyncio.Lock] = {}


async def send_widget(bot, chat_id, html: str, *, chat_data: str = "", caption: str = "",
                      allow_queries: bool = True, reply_markup=None) -> int | None:
    payload = {"chat_id": chat_id, "html": html, "allow_queries": allow_queries}
    if chat_data:
        payload["chat_data"] = chat_data
    if caption:
        payload["caption"] = caption
    markup = _serialize_markup(reply_markup)
    if markup:
        payload["reply_markup"] = markup

    async def _send():
        return await bot.do_api_request("sendWidget", payload)

    return _message_id(await _telegram_retry(_send, label="sendWidget"))


async def edit_widget_data(bot, chat_id, message_id: int, chat_data: str) -> None:
    payload = {"chat_id": chat_id, "message_id": int(message_id), "chat_data": chat_data}

    async def _edit():
        return await bot.do_api_request("editMessageWidgetData", payload)

    await _telegram_retry(_edit, label="editMessageWidgetData")


async def edit_widget_caption(bot, chat_id, message_id: int, caption: str) -> None:
    async def _edit():
        return await bot.edit_message_caption(chat_id=chat_id, message_id=int(message_id), caption=caption)

    await _telegram_retry(_edit, label="editMessageCaption (widget)")


async def answer_widget_query(bot, widget_query_id: str, data: str = "") -> None:
    payload = {"widget_query_id": widget_query_id}
    if data:
        payload["data"] = data
    await bot.do_api_request("answerWidgetQuery", payload)


async def _delete_quietly(bot, chat_id, message_id) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=int(message_id))
    except Exception as e:
        logger.info("delete_message %s/%s: %s", chat_id, message_id, e)


async def sync_order_widget(bot, order_id: int, *, create: bool = False, resend: bool = False) -> str:
    """Привести віджет замовлення у відповідність до БД.

    Повертає "sent" (надіслано новий), "updated" (оновлено дані) або "" (віджета немає й не треба).
    create — можна надіслати перший віджет (якщо запуск увімкнено для покупця);
    resend — видалити старий і надіслати новий унизу чату (/status).
    """
    order_id = int(order_id)
    lock = _locks.setdefault(order_id, asyncio.Lock())
    async with lock:
        order, items = await run_db(get_order_with_items, order_id)
        if not order:
            return ""
        user_id = int(order.get("user_id") or 0)
        if user_id <= 0:
            return ""
        row = await run_db(ow.get_widget, order_id)
        if not row and not (create and ow.widget_enabled_for(user_id)):
            return ""
        tracking = await run_db(ow.get_tracking, order_id)
        widget = ow.build_widget(
            order, items, tracking, review_bonus=review_bonus_enabled() and not order.get("is_test")
        )

        if row and row["html_hash"] == widget["hash"] and not resend:
            try:
                if row["data"] != widget["data"]:
                    await edit_widget_data(bot, user_id, row["message_id"], widget["data"])
                if row["caption"] != widget["caption"]:
                    await edit_widget_caption(bot, user_id, row["message_id"], widget["caption"])
                await run_db(ow.update_widget_state, order_id, widget["data"], widget["caption"])
                return "updated"
            except Exception as e:
                if is_telegram_rate_limited(e):
                    raise
                # Повідомлення видалили або воно недоступне — нижче шлемо віджет заново
                logger.warning("Віджет замовлення #%s не оновився, надсилаю новий: %s", order_id, e)

        message_id = await send_widget(
            bot, user_id, widget["html"], chat_data=widget["data"], caption=widget["caption"]
        )
        if not message_id:
            raise RuntimeError("sendWidget не повернув message_id")
        if row:
            await _delete_quietly(bot, user_id, row["message_id"])
        await run_db(ow.save_widget, order_id, user_id, message_id, widget["hash"], widget["data"], widget["caption"])
        return "sent"


async def send_order_note(bot, order_id: int, user_id: int, text: str) -> int | None:
    """Короткий рядок-сповіщення про подію; попередній такий рядок цього замовлення видаляється."""
    row = await run_db(ow.get_widget, int(order_id))
    message = await bot.send_message(chat_id=int(user_id), text=text)
    if row:
        await _delete_quietly(bot, user_id, row.get("note_message_id"))
        await run_db(ow.set_widget_note, int(order_id), message.message_id)
    return message.message_id
