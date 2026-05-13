"""
Denis 3D Print — Telegram Bot
"""

from datetime import datetime, timedelta
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
    CallbackQueryHandler, filters, ContextTypes
)

# =============================================
BOT_TOKEN  = os.environ.get("BOT_TOKEN")
OWNER_ID   = -1003739884073
WEBAPP_URL = "https://denisposelyanov.github.io/poselyanov3dprint/"
DB_FILE    = "users.db"
PRODUCTS_FILE = "products.json"
# =============================================

logging.basicConfig(
    format='%(asctime)s  %(message)s',
    datefmt='%H:%M:%S',
    level=logging.INFO
)
# Глушимо зайві логи
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ─── ТОВАРИ ─────────────────────────────────────────────────

def load_products():
    if Path(PRODUCTS_FILE).exists():
        return json.loads(Path(PRODUCTS_FILE).read_text(encoding='utf-8'))
    return []

PRODUCTS_CACHE = load_products()  # ← завантажується один раз при старті

def get_product_by_id(product_id: int):
    for p in PRODUCTS_CACHE:
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
            status       TEXT DEFAULT 'pending',
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
    cursor = conn.execute("""
        INSERT INTO orders (user_id, username, product_name, price, comment, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
    """, (user_id, username, product_name, price, comment))
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return order_id

def get_stats():
    conn = sqlite3.connect(DB_FILE)
    user_count       = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    order_count      = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    order_confirmed  = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'confirmed'").fetchone()[0]
    order_draft      = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'draft'").fetchone()[0]
    order_cancelled  = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'cancelled'").fetchone()[0]
    earned           = conn.execute("SELECT SUM(price) FROM orders WHERE status = 'confirmed'").fetchone()[0] or 0
    recent           = conn.execute(
        "SELECT name, username FROM users ORDER BY joined_at DESC LIMIT 10"
    ).fetchall()
    conn.close()
    return user_count, order_count, order_confirmed, order_draft, order_cancelled, earned, recent

def update_order_status(order_id: int, status: str):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()

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

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != 718746623:
        return
    user_count, order_count, order_confirmed, order_draft, order_cancelled, earned, recent = get_stats()
    lines = [
        f"📊 *Статистика*\n",
        f"👥 Користувачів: *{user_count}*",
        f"📦 Замовлень всього: *{order_count}*",
        f"  ✅ Підтверджених: *{order_confirmed}*",
        f"  ❓ Під питанням: *{order_draft}*",
        f"  ❌ Відмінених: *{order_cancelled}*\n",
        f"💰 Зароблено: *{earned} ₴*\n",
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

    product_name = data.get('product_name', '—')
    price        = data.get('price', '—')
    comment      = data.get('comment', '').strip()
    username     = data.get('user', 'невідомо')
    user_id      = data.get('user_id')
    first_name   = data.get('first_name', '')

    order_id = save_order(user_id or 0, username, product_name, price, comment)

    logger.info(f"📦 ЗАМОВЛЕННЯ  #{order_id}  {product_name}  {price}₴  від {username}")

    # Підтвердження клієнту
    
    if user_id:
        try:
            now = datetime.now()
            existing = confirmation_messages.get(user_id)
            footer = "\n[Денис](https://t.me/denisposelyanov) зв'язується з тобою найближчим часом 🙌"

            # Формуємо рядок товару
            line = f"📦 *{product_name}* — {price} ₴"
            if comment:
                line += f" · _{comment}_"

            # Формуємо текст для нового повідомлення
            confirm_text = f"✅ *Замовлення прийнято!*\n\n{line}\n\n"
            confirm_text += footer

            if existing and now - existing["time"] < timedelta(hours=4):
                try:
                    new_text = existing["text"].replace(footer, "")
                    new_text += f"{line}\n\n"
                    new_text += footer

                    await bot_app.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=existing["message_id"],
                        text=new_text,
                        parse_mode='Markdown'
                    )
                    confirmation_messages[user_id]["text"] = new_text
                    confirmation_messages[user_id]["time"] = now

                except Exception:
                    # Повідомлення видалено — надсилаємо нове
                    sent = await bot_app.bot.send_message(
                        chat_id=user_id,
                        text=confirm_text,
                        parse_mode='Markdown'
                    )
                    confirmation_messages[user_id] = {
                        "message_id": sent.message_id,
                        "text": confirm_text,
                        "time": now
                    }

            else:
                sent = await bot_app.bot.send_message(
                    chat_id=user_id,
                    text=confirm_text,
                    parse_mode='Markdown'
                )
                confirmation_messages[user_id] = {
                    "message_id": sent.message_id,
                    "text": confirm_text,
                    "time": now
                }
        except Exception as e:
            logger.error(f"Помилка підтвердження клієнту: {e}")

    owner_text = (
        f"🔔 *НОВЕ ЗАМОВЛЕННЯ #{order_id}*\n\n"
        f"📦 Товар: *{product_name}*\n"
        f"💰 Ціна: *{price} ₴*\n"
        f"👤 Від: {username}\n"
    )
    if comment:
        owner_text += f"📝 Коментар: _{comment}_\n"

    status_buttons = [
        InlineKeyboardButton("✅ Підтвердити",     callback_data=f"confirm_{order_id}"),
        InlineKeyboardButton("❓ Під питанням", callback_data=f"draft_{order_id}"),
        InlineKeyboardButton("❌ Відміна",      callback_data=f"cancel_{order_id}"),
    ]
    tg_username = data.get('tg_username')
    if tg_username:
        markup = InlineKeyboardMarkup([
            status_buttons,
            [InlineKeyboardButton(f"💬 Написати {first_name}", url=f"https://t.me/{tg_username}")]
        ])
    else:
        markup = InlineKeyboardMarkup([status_buttons])

    try:
        await bot_app.bot.send_message(
            chat_id=OWNER_ID,
            text=owner_text,
            parse_mode='Markdown',
            reply_markup=markup
        )
        logger.info(f"✅ Надіслано в канал")
    except Exception as e:
        logger.error(f"❌ Помилка надсилання в канал: {e}")

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

async def order_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "done":
        return

    action, order_id = query.data.split("_", 1)
    order_id = int(order_id)

    # Шукаємо кнопку "Написати ..." з оригінального повідомлення
    write_button = None
    if query.message.reply_markup:
        for row in query.message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.url and "t.me/" in btn.url:
                    write_button = btn

    if action == "confirm":
        update_order_status(order_id, "confirmed")
        label = "✅ Підтверджено"
        # Залишаємо кнопку "Написати" якщо є
        markup = InlineKeyboardMarkup([[write_button]]) if write_button else None
    elif action == "draft":
        update_order_status(order_id, "draft")
        label = "❓ Під питанням"
        buttons = [
            InlineKeyboardButton("✅ Підтвердити", callback_data=f"confirm_{order_id}"),
            InlineKeyboardButton("❌ Відмінити",   callback_data=f"cancel_{order_id}"),
        ]
        # Додаємо кнопку "Написати" якщо є
        markup = InlineKeyboardMarkup(
            [buttons, [write_button]] if write_button else [buttons]
        )
    elif action == "cancel":
        update_order_status(order_id, "cancelled")
        label = "❌ Відмінено"
        markup = None  # всі кнопки зникають
    else:
        return

    await query.edit_message_text(
        text=query.message.text + f"\n\n*Статус: {label}*",
        parse_mode='Markdown',
        reply_markup=markup
    )


# ─── ЗАПУСК ─────────────────────────────────────────────────

bot_app = None

confirmation_messages = {}  # {user_id: {"message_id": int, "text": str, "time": datetime}}

def main():
    global bot_app
    init_db()

    bot_app = Application.builder().token(BOT_TOKEN).build()

    bot_app.add_handler(CommandHandler("start",     start))
    bot_app.add_handler(CommandHandler("myid",      myid))
    bot_app.add_handler(CommandHandler("stats",     stats))
    bot_app.add_handler(CommandHandler("broadcast", broadcast))
    bot_app.add_handler(CallbackQueryHandler(order_action, pattern=r"^(confirm|draft|cancel)_"))

    async def run():
        http_app = web.Application()
        http_app.router.add_post('/order', handle_order)
        http_app.router.add_route('OPTIONS', '/order', handle_options)
        runner = web.AppRunner(http_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080)))
        await site.start()
        logger.info("🌐 HTTP сервер запущено  →  порт 8080")

        async with bot_app:
            await bot_app.initialize()
            await bot_app.start()
            await bot_app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            logger.info("🤖 Бот запущено  →  очікую замовлення...")
            await asyncio.Event().wait()

    asyncio.run(run())

if __name__ == '__main__':
    main()