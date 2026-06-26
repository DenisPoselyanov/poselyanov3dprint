"""Register python-telegram-bot handlers."""

from __future__ import annotations

from telegram import BotCommandScopeAllPrivateChats, BotCommandScopeChat, Update
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
    app.add_handler(CallbackQueryHandler(handlers.order_action, pattern=r"^(confirm|draft|cancel)_"))


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
