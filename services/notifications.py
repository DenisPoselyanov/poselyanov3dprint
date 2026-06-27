"""Telegram notification helpers."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import app_state
import config
from rich_messages import (
    build_admin_coupon_created_notification,
    build_personal_coupon_notification,
    send_rich_message,
)
from services.users import set_blocked

logger = logging.getLogger(__name__)


async def notify_coupon_created(
    coupon: dict,
    *,
    source: str = "admin_panel",
    notify_user_ids: list[int] | None = None,
) -> dict:
    result: dict = {
        "admin_sent": False,
        "users_notified": [],
        "users_failed": [],
        "user_sent": None,
        "user_error": None,
    }
    if not coupon or not app_state.bot_app or not config.OWNER_ID:
        return result

    bot = app_state.bot_app.bot
    allowed_user_ids = list(coupon.get("allowed_user_ids") or [])
    admin_html = build_admin_coupon_created_notification(
        coupon["code"],
        coupon["type"],
        int(coupon["value"]),
        min_order=int(coupon.get("min_order") or 0),
        uses_max=int(coupon.get("uses_max") or 0),
        one_per_user=int(coupon.get("one_per_user") or 0),
        expires_at=coupon.get("expires_at"),
        personal_user_id=coupon.get("personal_user_id"),
        allowed_user_ids=allowed_user_ids,
        source=source,
    )
    try:
        await send_rich_message(bot, config.OWNER_ID, admin_html)
        result["admin_sent"] = True
    except Exception as e:
        logger.warning("Admin coupon notification failed: %s", e)

    if notify_user_ids is not None:
        target_ids = [int(uid) for uid in notify_user_ids]
    elif allowed_user_ids:
        target_ids = [int(uid) for uid in allowed_user_ids]
    elif coupon.get("personal_user_id"):
        target_ids = [int(coupon["personal_user_id"])]
    else:
        return result

    if not target_ids:
        return result

    coupon_html = build_personal_coupon_notification(
        coupon["code"],
        coupon["type"],
        int(coupon["value"]),
        min_order=int(coupon.get("min_order") or 0),
        one_per_user=int(coupon.get("one_per_user") or 0),
        expires_at=coupon.get("expires_at"),
    )
    catalog_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛍️ Відкрити каталог", web_app=WebAppInfo(url=config.WEBAPP_URL))]]
    )

    for personal_user_id in target_ids:
        try:
            await send_rich_message(
                bot, int(personal_user_id), coupon_html, reply_markup=catalog_markup
            )
            result["users_notified"].append(int(personal_user_id))
        except Exception as e:
            logger.warning("Coupon notification failed for %s: %s", personal_user_id, e)
            if "bot was blocked" in str(e) or "user is deactivated" in str(e):
                set_blocked(int(personal_user_id), True)
            result["users_failed"].append(int(personal_user_id))

    if len(target_ids) == 1:
        if result["users_notified"]:
            result["user_sent"] = True
        elif result["users_failed"]:
            result["user_sent"] = False
            result["user_error"] = "Юзер не писав боту або заблокував"

    return result
