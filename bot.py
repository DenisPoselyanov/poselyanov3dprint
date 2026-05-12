"""
Denis 3D Print — Telegram Bot
"""

from aiohttp import web
import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

# =============================================
BOT_TOKEN  = os.environ.get("BOT_TOKEN")
OWNER_ID   = -1003739884073
WEBAPP_URL = "https://denisposelyanov.github.io/poselyanov3dprint/"
DB_FILE    = "users.db"
PRODUCTS_FILE = "products.json"
# =============================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ─── ТОВАРИ ─────────────────────────────────────────────────

def load_products():
    if Path(PRODUCTS_FILE).exists():
        return json.loads(Path(PRODUCTS_FILE).read_text(encoding='utf-8'))
    return []

def get_product_by_id(product_id: int):
    for p in load_products():
        if p['id'] == product_id:
            return p
    return None


# ─── БАЗА ДАНИХ ─────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id        INTEGER PRIMARY KEY,
            name      TEXT,
            username  TEXT,
            blocked   INTEGER DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            username     TEXT,
            product_name TEXT,
            price        INTEGER,
            comment      TEXT,
            ordered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_user(user):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        INSERT OR IGNORE INTO users (id, name, username)
        VALUES (?, ?, ?)
    """, (user.id, user.first_name, f"@{user.username}" if user.username else "—"))
    conn.commit()
    conn.close()

def save_order(user_id, username, product_name, price, comment):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        INSERT INTO orders (user_id, username, product_name, price, comment)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, username, product_name, price, comment))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect(DB_FILE)
    user_count  = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    order_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    recent      = conn.execute(
        "SELECT name, username FROM users ORDER BY joined_at DESC LIMIT 10"
    ).fetchall()
    conn.close()
    return user_count, order_count, recent

def set_blocked(user_id, blocked: bool):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("UPDATE users SET blocked = ? WHERE id = ?", (int(blocked), user_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_FILE)
    users = conn.execute("SELECT id FROM users WHERE blocked = 0").fetchall()
    conn.close()
    return users


# ─── ХЕНДЛЕРИ ───────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.message.from_user)

    keyboard = [[
        KeyboardButton(
            "🛍️ Відкрити каталог",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]]
    await update.message.reply_text(
        "👋 Привіт! Я — Poselyanov 3D Print\n\n"
        "Роблю 3D-принти на замовлення:\n"
        "Натисни кнопку нижче щоб переглянути каталог 👇",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )


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
    user         = update.message.from_user
    client       = f"@{user.username}" if user.username else user.first_name

    save_order(
        user.id,
        f"@{user.username}" if user.username else "—",
        product_name,
        price,
        comment
    )

    # Підтвердження клієнту
    confirm_text = (
        f"✅ Замовлення прийнято!\n\n"
        f"📦 *{product_name}*\n"
        f"💰 {price} ₴\n"
    )
    if comment:
        confirm_text += f"📝 Коментар: {comment}\n"
    confirm_text += "\n[Денис](https://t.me/denisposelyanov) зв'яжеться з тобою найближчим часом 🙌"

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

    if user.username:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(
            f"💬 Написати {user.first_name}",
            url=f"https://t.me/{user.username}"
        )]])
    else:
        owner_text += f"⚠️ Немає username, ID: `{user.id}`\n"
        markup = None

    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=owner_text,
        parse_mode='Markdown',
        reply_markup=markup
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != 718746623:
        return
    user_count, order_count, recent = get_stats()
    lines = [
        f"📊 *Статистика*\n",
        f"👥 Користувачів: *{user_count}*",
        f"📦 Замовлень: *{order_count}*\n",
        f"🕐 Останні користувачі:",
    ]
    for name, username in recent:
        lines.append(f"• {name} {username}")
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != 718746623:
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Використання: `/broadcast <id товару> <текст>`\n\n"
            "Приклад: `/broadcast 1 Новий хіт в каталозі! 🔥`",
            parse_mode='Markdown'
        )
        return

    try:
        product_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ ID товару має бути числом.", parse_mode='Markdown')
        return

    text = ' '.join(args[1:])
    product = get_product_by_id(product_id)

    if not product:
        await update.message.reply_text(f"❌ Товар з ID `{product_id}` не знайдено.", parse_mode='Markdown')
        return

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🛍️ Переглянути товар",
            url=f"https://t.me/poselyanov3dprint_bot?startapp=product_{product_id}"
        )
    ]])

    photo_url = product.get('photos', [])
    photo_url = photo_url[0] if photo_url else None

    users = get_all_users()
    sent, failed = 0, 0

    for (user_id,) in users:
        try:
            if photo_url:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=photo_url,
                    caption=text,
                    reply_markup=markup
                )
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=markup
                )
            sent += 1
        except Exception:
            set_blocked(user_id, True)
            failed += 1

    await update.message.reply_text(
        f"📨 Розсилка завершена\n✅ Надіслано: *{sent}*\n❌ Заблоковано: *{failed}*",
        parse_mode='Markdown'
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Твій Telegram ID: `{update.message.from_user.id}`",
        parse_mode='Markdown'
    )

#Новий HTTP хендлер для прийому замовлень з веб-додатку
async def handle_order(request):
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="Bad JSON")

    logger.info(f"=== HTTP ЗАМОВЛЕННЯ: {data} ===")

    product_name = data.get('product_name', '—')
    price        = data.get('price', '—')
    comment      = data.get('comment', '').strip()
    username     = data.get('user', 'невідомо')
    user_id      = data.get('user_id')
    first_name   = data.get('first_name', '')

    save_order(user_id or 0, username, product_name, price, comment)

    owner_text = (
        f"🔔 *НОВЕ ЗАМОВЛЕННЯ*\n\n"
        f"📦 Товар: *{product_name}*\n"
        f"💰 Ціна: *{price} ₴*\n"
        f"👤 Від: {username}\n"
    )
    if comment:
        owner_text += f"📝 Коментар: _{comment}_\n"

    tg_username = data.get('tg_username')
    if tg_username:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(
            f"💬 Написати {first_name}",
            url=f"https://t.me/{tg_username}"
        )]])
    else:
        markup = None

    try:
        await bot_app.bot.send_message(
            chat_id=OWNER_ID,
            text=owner_text,
            parse_mode='Markdown',
            reply_markup=markup
        )
        logger.info("=== ПОВІДОМЛЕННЯ НАДІСЛАНО В КАНАЛ ===")
    except Exception as e:
        logger.error(f"=== ПОМИЛКА НАДСИЛАННЯ: {e} ===")

    return web.Response(
        text="ok",
        headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type'
        }
    )
async def handle_options(request):
    return web.Response(
        headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type'
        }
    )

# ─── ЗАПУСК ─────────────────────────────────────────────────

bot_app = None

def main():
    global bot_app
    init_db()

    bot_app = Application.builder().token(BOT_TOKEN).build()

    bot_app.add_handler(CommandHandler("start",     start))
    bot_app.add_handler(CommandHandler("myid",      myid))
    bot_app.add_handler(CommandHandler("stats",     stats))
    bot_app.add_handler(CommandHandler("broadcast", broadcast))
    bot_app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))

    async def run():
        # HTTP сервер
        http_app = web.Application()
        http_app.router.add_post('/order', handle_order)
        http_app.router.add_route('OPTIONS', '/order', handle_options)
        runner = web.AppRunner(http_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080)))
        await site.start()
        logger.info("HTTP сервер запущений на порту 8080")

        # Telegram polling
        async with bot_app:
            await bot_app.initialize()
            await bot_app.start()
            await bot_app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            logger.info("Бот запущений!")
            await asyncio.Event().wait()

    asyncio.run(run())

if __name__ == '__main__':
    main()