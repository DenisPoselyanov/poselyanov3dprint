"""
Denis 3D Print — Telegram Bot
==============================
Отримує замовлення з Mini App та надсилає повідомлення власнику.

Встановлення:
  pip install python-telegram-bot==20.7

Запуск:
  python bot.py
"""

import json
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# =============================================
# НАЛАШТУВАННЯ — заміни на свої значення!
# =============================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")   # напр. 7123456789:AAF_abc...
OWNER_ID  = 718746623                    # твій Telegram ID (як дізнатись — нижче)
WEBAPP_URL = "https://github.com/DenisPoselyanov/poselyanov3dprint"  # URL твого Mini App

# =============================================


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[
        InlineKeyboardButton(
            "🛍️ Відкрити каталог",
            web_app={"url": WEBAPP_URL}
        )
    ]]
    markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 Привіт! Я — Poselyanov 3D Print\n\n"
        "Роблю 3D-принти на замовлення:\n"
        "🧸 Іграшки · 🔑 Брелки · 🧠 Антистрес\n\n"
        "Натисни кнопку нижче щоб переглянути каталог 👇",
        reply_markup=markup
    )


# --- Отримання замовлення з Mini App ---
async def web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = json.loads(update.message.web_app_data.data)
    except Exception:
        logger.error("Не вдалось розібрати дані з Mini App")
        return

    if data.get('action') != 'order':
        return

    product_name = data.get('product_name', '—')
    price        = data.get('price', '—')
    comment      = data.get('comment', '').strip()
    client       = data.get('user', '—')

    # Повідомлення клієнту — підтвердження
    confirm_text = (
        f"✅ Замовлення прийнято!\n\n"
        f"📦 *{product_name}*\n"
        f"💰 {price} ₴\n"
    )
    if comment:
        confirm_text += f"📝 Коментар: {comment}\n"
    confirm_text += "\nДенис зв'яжеться з тобою найближчим часом 🙌"

    await update.message.reply_text(confirm_text, parse_mode='Markdown')

    # Повідомлення власнику
    owner_text = (
        f"🔔 *НОВЕ ЗАМОВЛЕННЯ*\n\n"
        f"📦 Товар: *{product_name}*\n"
        f"💰 Ціна: *{price} ₴*\n"
        f"👤 Від: {client}\n"
    )
    if comment:
        owner_text += f"📝 Коментар: _{comment}_\n"

    # Кнопка "Написати клієнту"
    user = update.message.from_user
    if user.username:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(
            f"💬 Написати {user.first_name}",
            url=f"https://t.me/{user.username}"
        )]])
    else:
        # Додай ID щоб хоч якось зв'язатись
        owner_msg += f"⚠️ Немає username, ID: `{user.id}`\n"
        markup = None

    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=owner_text,
        parse_mode='Markdown',
        reply_markup=markup
    )


# --- /myid — щоб дізнатись свій Telegram ID ---
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Твій Telegram ID: `{update.message.from_user.id}`",
        parse_mode='Markdown'
    )


# --- Запуск ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))

    logger.info("Бот запущений!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
