"""Register python-telegram-bot handlers."""

from __future__ import annotations

import logging

from telegram import (
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    MenuButtonWebApp,
    Update,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from handlers.errors import telegram_error_handler

TELEGRAM_ALLOWED_UPDATES = ["message", "callback_query"]

logger = logging.getLogger(__name__)


def register_telegram_handlers(app: Application, handlers) -> None:
    """Attach Telegram command, message, and callback handlers."""
    app.add_error_handler(telegram_error_handler)
    app.add_handler(TypeHandler(Update, handlers.auto_register_user), group=-1)

    app.add_handler(CommandHandler("catalog", handlers.catalog))
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("myid", handlers.myid))
    app.add_handler(CommandHandler("stats", handlers.stats))
    app.add_handler(CommandHandler("broadcast", handlers.broadcast))
    app.add_handler(CommandHandler("coupon", handlers.coupon_cmd))
    app.add_handler(CommandHandler("admin", handlers.admin_cmd))
    app.add_handler(CommandHandler("history", handlers.history))
    app.add_handler(CommandHandler("mycoupons", handlers.mycoupons))
    app.add_handler(CommandHandler("sales", handlers.sales))
    app.add_handler(CommandHandler("status", handlers.status_cmd))
    app.add_handler(CommandHandler("contact", handlers.contact))
    app.add_handler(CommandHandler("reload_products", handlers.reload_products_cmd))
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^(?:/mycoupons|🎟️ Мої купони)$"),
            handlers.mycoupons,
        )
    )
    app.add_handler(CallbackQueryHandler(handlers.contact_action, pattern=r"^contact_\d+$"))
    app.add_handler(CallbackQueryHandler(handlers.order_action, pattern=r"^(confirm|draft|cancel)_"))
    app.add_handler(CallbackQueryHandler(handlers.review_action, pattern=r"^rev_(pub|hide)_\d+$"))
    app.add_handler(CallbackQueryHandler(handlers.giveaway_join_action, pattern=r"^gw_join_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(handlers.giveaway_owner_action, pattern=r"^gw_(rr|ext)_\d+$"))


async def configure_editor_menus(app: Application, *, editor_ids, admin_url: str | None) -> None:
    """Меню редакторів: команда /admin і кнопка меню чату з адмінкою. Без bypass-токена: лише Telegram initData."""
    for chat_id in editor_ids:
        try:
            await app.bot.set_my_commands(
                commands=[
                    ("admin", "📊 Адмін панель"),
                    ("catalog", "🛍️ Відкрити каталог"),
                    ("myid", "🪪 Мій ID"),
                ],
                scope=BotCommandScopeChat(chat_id=chat_id),
            )
            if admin_url and admin_url.lower().startswith("https://"):
                await app.bot.set_chat_menu_button(
                    chat_id=chat_id,
                    menu_button=MenuButtonWebApp(text="Адмінка", web_app=WebAppInfo(url=admin_url)),
                )
        except Exception as exc:  # чат ще не відкривали (немає /start) або Telegram відмовив
            logger.warning("Меню редактора %s не налаштовано: %s", chat_id, exc)


async def configure_bot_commands(app: Application, *, owner_id: int, user_commands: list[tuple[str, str]]) -> None:
    """Set admin and user command menus."""
    await app.bot.set_my_commands(
        commands=[
            ("catalog", "🛍️ Відкрити каталог"),
            ("admin", "📊 Адмін панель"),
            ("stats", "📊 Статистика"),
            ("coupon", "🎟️ Керування купонами"),
            ("broadcast", "📨 Розсилка"),
            ("history", "📦 Мої замовлення"),
            ("status", "📋 Статус замовлення"),
            ("mycoupons", "🎟️ Мої купони"),
            ("sales", "🔥 Акції"),
            ("contact", "📬 Контакти"),
            ("myid", "🪪 Мій ID"),
        ],
        scope=BotCommandScopeChat(chat_id=owner_id),
    )
    await app.bot.set_my_commands(commands=user_commands)
    await app.bot.set_my_commands(
        commands=user_commands,
        scope=BotCommandScopeAllPrivateChats(),
    )
