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

# Завантажуємо змінні середовища з .env файлу, щоб не зберігати конфіденційні дані (як-от токен бота) прямо в коді.
from dotenv import load_dotenv
load_dotenv()

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
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id           INTEGER,
            username          TEXT,
            first_name        TEXT,
            total_price       INTEGER,
            comment           TEXT,
            gift_product_name TEXT,
            status            TEXT DEFAULT 'new',
            coupon_code       TEXT,
            discount_amount   INTEGER DEFAULT 0,
            ordered_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS order_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id     INTEGER,
            product_id   INTEGER,
            product_name TEXT,
            price        INTEGER,
            quantity     INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS coupons (
            code         TEXT PRIMARY KEY,
            type         TEXT NOT NULL,      -- 'percent' або 'fixed'
            value        INTEGER NOT NULL,   -- 20 або 50 (грн)
            min_order    INTEGER DEFAULT 0,
            uses_max     INTEGER DEFAULT 0,  -- 0 = необмежено
            uses_count   INTEGER DEFAULT 0,
            one_per_user INTEGER DEFAULT 0,  -- 1 = один раз на юзера
            active       INTEGER DEFAULT 1,
            expires_at   TIMESTAMP           -- NULL = без терміну
        )
    """)

    # Таблиця для зберігання інформації про використання купонів, щоб можна було відстежувати, хто і коли їх використовував, а також для реалізації обмежень на кількість використань і використання одним користувачем.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS coupon_uses (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            code      TEXT NOT NULL,
            user_id   INTEGER NOT NULL,
            order_id  INTEGER,
            used_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

# Зберігаємо користувача при першому контакті з ботом (або ігноруємо, якщо вже є)
def save_user(user):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        INSERT OR IGNORE INTO users (id, name, username)
        VALUES (?, ?, ?)
    """, (user.id, user.first_name, f"@{user.username}" if user.username else "—"))
    conn.commit()
    conn.close()

# Функція для збереження замовлення в базі даних, яка приймає всі необхідні дані про замовлення (користувача, товари, загальну суму, коментар, подарунок і купон), зберігає їх у відповідних таблицях (orders і order_items) і повертає ID створеного замовлення для подальшого використання в логах і кнопках.
def save_order(user_id, username, first_name, items, total_price, comment, gift_product_name=None, coupon_code=None, discount_amount=0):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.execute("""
        INSERT INTO orders (user_id, username, first_name, total_price, comment, gift_product_name, coupon_code, discount_amount, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')
    """, (user_id, username, first_name, total_price, comment, gift_product_name, coupon_code, discount_amount))
    order_id = cursor.lastrowid
    for item in items:
        conn.execute("""
            INSERT INTO order_items (order_id, product_id, product_name, price, quantity)
            VALUES (?, ?, ?, ?, ?)
        """, (order_id, item.get('id', 0), item.get('product_name', '—'), item.get('price', 0), item.get('quantity', 1)))

    # Якщо є подарунок, додаємо його як окремий рядок в order_items з ціною 0 і спеціальною назвою для зручності відображення в звітах і повідомленнях
    if gift_product_name:
        conn.execute("""
            INSERT INTO order_items (order_id, product_id, product_name, price, quantity)
            VALUES (?, 0, ?, 0, 1)
        """, (order_id, f"🎁 {gift_product_name} (безкоштовно)"))

    # Якщо замовлення було оформлено з купоном, оновлюємо лічильник використань цього купона і додаємо запис в таблицю coupon_uses для відстеження, хто і коли його використовував. Це дозволяє реалізувати обмеження на кількість використань купона і використання одним користувачем, а також отримувати статистику по купонам.
    if coupon_code:
        conn.execute(
            "UPDATE coupons SET uses_count = uses_count + 1 WHERE code = ?",
            (coupon_code.upper(),)
        )
        conn.execute(
            "INSERT INTO coupon_uses (code, user_id, order_id) VALUES (?, ?, ?)",
            (coupon_code.upper(), user_id, order_id)
        )
    conn.commit()
    conn.close()
    return order_id

# Функція для отримання статистики по користувачах і замовленнях, яка використовується в адмінській команді /stats для відображення актуальної інформації про діяльність бота.
def get_stats():
    conn = sqlite3.connect(DB_FILE)
    user_count       = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    order_count      = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    order_confirmed  = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'confirmed'").fetchone()[0]
    order_draft      = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'draft'").fetchone()[0]
    order_cancelled  = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'cancelled'").fetchone()[0]
    earned = conn.execute("SELECT SUM(total_price) FROM orders WHERE status = 'confirmed'").fetchone()[0] or 0
    recent           = conn.execute(
        "SELECT name, username FROM users ORDER BY joined_at DESC LIMIT 10"
    ).fetchall()

    # Витягуємо топ-5 найпопулярніших товарів серед підтверджених замовлень
    top_products = conn.execute("""
        SELECT oi.product_name, SUM(oi.quantity) as cnt
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        WHERE o.status = 'confirmed'
        GROUP BY oi.product_name
        ORDER BY cnt DESC
        LIMIT 5
    """).fetchall()

    # Витягуємо статистику по купонам: код, кількість використань і загальну суму знижки, яку вони надали. Це дозволяє адміністраторам оцінити ефективність кожного купона і приймати рішення про їх подальше використання або модифікацію.
    coupon_stats = conn.execute("""
        SELECT c.code, c.uses_count,
               COALESCE(SUM(o.discount_amount), 0) as total_discount
        FROM coupons c
        LEFT JOIN orders o ON o.coupon_code = c.code AND o.status = 'confirmed'
        GROUP BY c.code
        ORDER BY c.uses_count DESC
        LIMIT 3
    """).fetchall()
    total_discount = conn.execute(
        "SELECT COALESCE(SUM(discount_amount), 0) FROM orders WHERE status = 'confirmed'"
    ).fetchone()[0]
    conn.close()
    return user_count, order_count, order_confirmed, order_draft, order_cancelled, earned, recent, top_products, coupon_stats, total_discount

# Функція для перевірки купона при оформленні замовлення, яка враховує всі умови використання купона (активність, термін дії, мінімальна сума замовлення, обмеження на кількість використань і використання одним користувачем) і повертає результат у вигляді словника з інформацією про валідність купона, тип і значення знижки, а також повідомлення для клієнта.
def check_coupon(code: str, user_id: int, cart_total: int):
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT * FROM coupons WHERE code = ?", (code.upper(),)
    ).fetchone()

    if not row:
        conn.close()
        return {"valid": False, "message": "Купон не знайдено ❌"}

    # Створюємо словник з даними купона для зручності доступу до полів за іменами. Це дозволяє легко перевіряти умови використання купона і формувати повідомлення для клієнта.
    cols = [d[0] for d in conn.execute("SELECT * FROM coupons LIMIT 0").description]
    c = dict(zip(cols, row))

    if not c['active']:
        conn.close()
        return {"valid": False, "message": "Купон вже не активний ❌"}

    if c['expires_at'] and datetime.now() > datetime.fromisoformat(c['expires_at']):
        conn.close()
        return {"valid": False, "message": "Термін купону закінчився ❌"}

    if c['min_order'] and cart_total < c['min_order']:
        conn.close()
        return {"valid": False, "message": f"Мінімальна сума замовлення: {c['min_order']} ₴ ❌"}

    if c['uses_max'] and c['uses_count'] >= c['uses_max']:
        conn.close()
        return {"valid": False, "message": "Купон вичерпано ❌"}

    if c['one_per_user'] and user_id:
        used = conn.execute(
            "SELECT 1 FROM coupon_uses WHERE code = ? AND user_id = ?",
            (c['code'], user_id)
        ).fetchone()
        if used:
            conn.close()
            return {"valid": False, "message": "Ти вже використовував цей купон ❌"}

    conn.close()

    discount = c['value'] if c['type'] == 'fixed' else round(cart_total * c['value'] / 100)
    discount = min(discount, cart_total)  # не більше суми кошика

    label = f"-{c['value']}%" if c['type'] == 'percent' else f"-{c['value']} ₴"
    return {
        "valid": True,
        "type": c['type'],
        "value": c['value'],
        "discount": discount,
        "message": f"Купон застосовано! Знижка {label} ✅"
    }

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
    user_count, order_count, order_confirmed, order_draft, order_cancelled, earned, recent, top_products, coupon_stats, total_discount = get_stats()
    lines = [
        f"📊 *Статистика*\n",
        f"👥 Користувачів: *{user_count}*",
        f"📦 Замовлень всього: *{order_count}*",
        f"✅ Підтверджених: *{order_confirmed}*",
        f"❓ Під питанням: *{order_draft}*",
        f"❌ Відмінених: *{order_cancelled}*\n",
        f"💰 Зароблено: *{earned} ₴*\n",
        f"🕐 Останні користувачі:",
    ]
    for name, username in recent:
        lines.append(f"• {name} {username}")

    # Виводимо топ-5 найпопулярніших товарів серед підтверджених замовлень    
    if top_products:
        lines.append(f"\n🏆 *Топ товари:*")
        for name, cnt in top_products:
            lines.append(f"• {name} — {cnt} шт")
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

    # Виводимо статистику по купонам, якщо вони є, включаючи код купона, кількість використань і загальну суму знижки, яку вони надали. Це дозволяє адміністраторам оцінити ефективність кожного купона і приймати рішення про їх подальше використання або модифікацію.
    if coupon_stats:
        lines.append(f"\n🎟️ *Купони (топ-3):*")
        for code, uses, disc in coupon_stats:
            lines.append(f"• `{code}` — {uses} раз, -{disc} ₴")
        lines.append(f"💸 Всього знижок: *{total_discount} ₴*")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != 718746623:
        return

    # Якщо команда є відповіддю на повідомлення, беремо текст звідти, ігноруючи аргументи після ID. Інакше беремо текст з аргументів.
    args = context.args
    reply = update.message.reply_to_message
    
    # Якщо є відповідь на повідомлення, використовуємо її текст, ігноруючи додаткові аргументи після ID. Інакше беремо текст з аргументів.
    if not reply:
        await update.message.reply_text(
            "Відповідай (reply) на повідомлення командою:\n"
            "`/broadcast` — без товару\n"
            "`/broadcast 1` — з кнопкою товару",
            parse_mode='Markdown'
        )
        return

    # Якщо в аргументах є ID товару, намагаємося його знайти. Якщо товар не знайдено, повідомляємо адміністратору і припиняємо розсилку.
    product_id = int(args[0]) if args else None
    product = get_product_by_id(product_id) if product_id else None

    if product_id and not product:
        await update.message.reply_text(f"❌ Товар з ID `{product_id}` не знайдено.", parse_mode='Markdown')
        return

    if product:
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🛍️ Переглянути товар",
                url=f"https://t.me/poselyanov3dprint_bot?startapp=product_{product_id}"
            )
        ]])
        photo_url = product.get('photos', [None])[0]
    else:
        markup = None
        photo_url = None

    users = get_all_users()
    sent, failed = 0, 0

    # Логування кількості користувачів, яким буде надіслано розсилку. Це допоможе відстежувати охоплення та ефективність розсилки, а також виявляти потенційні проблеми з доставкою.
    logger.info(f"📨 Розсилка → {len(users)} користувачів")

    for (user_id,) in users:
        try:
            if reply.photo:
                await context.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=reply.chat.id,
                    message_id=reply.message_id,
                    reply_markup=markup
                )
            elif photo_url:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=photo_url,
                    caption=reply.text or "",
                    caption_entities=reply.entities or [],
                    reply_markup=markup
                )
            else:
                await context.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=reply.chat.id,
                    message_id=reply.message_id,
                    reply_markup=markup
                )
            sent += 1
        except Exception as e:
            logger.warning(f"Broadcast error for {user_id}: {e}")
            if "bot was blocked" in str(e) or "user is deactivated" in str(e):
                set_blocked(user_id, True)
            failed += 1
    # Після завершення розсилки надсилаємо адміністратору звіт про кількість успішних і неуспішних доставок. Це дозволяє адміністратору оцінити ефективність розсилки та виявити потенційні проблеми з доставкою, такі як блокування бота користувачами.
    await update.message.reply_text(
        f"📨 Розсилка завершена\n✅ Надіслано: *{sent}*\n❌ Заблоковано: *{failed}*",
        parse_mode='Markdown'
    )

# Команда для отримання Telegram ID користувача, яка може бути корисною для адміністраторів при налаштуванні замовлень або вирішенні проблем з користувачами. Вона відповідає повідомленням з ID користувача у форматі Markdown.
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Твій Telegram ID: `{update.message.from_user.id}`",
        parse_mode='Markdown'
    )

# Команда для управління купонами, яка дозволяє адміністраторам створювати, переглядати, активувати і деактивувати купони зі знижками. Вона підтримує різні формати знижок (відсоткові і фіксовані), а також додаткові параметри для обмеження використання купонів (мінімальна сума замовлення, максимальна кількість використань, використання одним користувачем і термін дії). Команда має підкоманди для кожної операції (add, list, disable, enable) і відповідає повідомленнями з результатами операцій у форматі Markdown.
async def coupon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != 718746623:
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Команди:\n"
            "`/coupon add КОД percent 20` — знижка 20%\n"
            "`/coupon add КОД fixed 50` — знижка 50 ₴\n"
            "  опції: `min=200` `max=10` `once` `expires=2025-12-31`\n"
            "`/coupon list` — всі купони\n"
            "`/coupon disable КОД` — вимкнути купон\n"
            "`/coupon enable КОД` — увімкнути купон",
            parse_mode='Markdown'
        )
        return

    sub = args[0].lower()

    if sub == 'add' and len(args) >= 4:
        code  = args[1].upper()
        ctype = args[2].lower()  # 'percent' або 'fixed'
        try:
            value = int(args[3])
        except ValueError:
            await update.message.reply_text("❌ Значення має бути числом")
            return

        # Опціональні параметри
        min_order = 0; uses_max = 0; one_per_user = 0; expires_at = None
        for opt in args[4:]:
            if opt.startswith('min='):
                min_order = int(opt[4:])
            elif opt.startswith('max='):
                uses_max = int(opt[4:])
            elif opt == 'once':
                one_per_user = 1
            elif opt.startswith('expires='):
                expires_at = opt[8:]

        conn = sqlite3.connect(DB_FILE)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO coupons
                (code, type, value, min_order, uses_max, one_per_user, active, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """, (code, ctype, value, min_order, uses_max, one_per_user, expires_at))
            conn.commit()
            label = f"{value}%" if ctype == 'percent' else f"{value} ₴"
            await update.message.reply_text(f"✅ Купон `{code}` створено! Знижка {label}", parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ Помилка: {e}")
        finally:
            conn.close()

    elif sub == 'list':
        conn = sqlite3.connect(DB_FILE)
        rows = conn.execute("SELECT code, type, value, uses_count, uses_max, active, expires_at FROM coupons").fetchall()
        conn.close()
        if not rows:
            await update.message.reply_text("Купонів ще немає")
            return
        lines = ["🎟️ *Всі купони:*\n"]
        for code, ctype, value, uses_count, uses_max, active, expires_at in rows:
            label = f"{value}%" if ctype == 'percent' else f"{value} ₴"
            status = "✅" if active else "🚫"
            uses_str = f"{uses_count}/{uses_max}" if uses_max else f"{uses_count}/∞"
            exp = f" · до {expires_at}" if expires_at else ""
            lines.append(f"{status} `{code}` — {label} · {uses_str}{exp}")
        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

    elif sub in ('disable', 'enable') and len(args) >= 2:
        code = args[1].upper()
        active = 1 if sub == 'enable' else 0
        conn = sqlite3.connect(DB_FILE)
        conn.execute("UPDATE coupons SET active = ? WHERE code = ?", (active, code))
        conn.commit()
        conn.close()
        icon = "✅" if active else "🚫"
        await update.message.reply_text(f"{icon} Купон `{code}` {'увімкнено' if active else 'вимкнено'}", parse_mode='Markdown')

    else:
        await update.message.reply_text("❌ Невірний формат команди")

#Новий HTTP хендлер для прийому замовлень з веб-додатку
async def handle_order(request):
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="Bad JSON")

    # Витягуємо дані замовлення
    items       = data.get('items', [])
    total_price = data.get('total_price', 0)
    comment     = data.get('comment', '').strip()
    username    = data.get('user', 'невідомо')
    user_id     = data.get('user_id')
    first_name  = data.get('first_name', '')
    gift        = data.get('gift')  # може бути None або словником з даними подарунка
    coupon_code     = data.get('coupon_code')
    discount_amount = data.get('discount_amount', 0)

    # Формуємо назву для збереження в БД (всі товари через кому)
    product_name = ', '.join(i.get('product_name', '—') for i in items)

    # Зберігаємо замовлення в базі даних і отримуємо його ID для подальшого використання в логах і кнопках
    order_id = save_order(user_id or 0, username, first_name, items, total_price, comment, gift, coupon_code, discount_amount)

    # Логування замовлення
    logger.info(f"📦 ЗАМОВЛЕННЯ #{order_id}  {product_name}  {total_price}₴  від {username}")

    # Підтвердження клієнту
    
    if user_id:
        try:
            now = datetime.now()
            existing = confirmation_messages.get(user_id)
            footer = "\n[Денис](https://t.me/denisposelyanov) зв'яжеться з тобою найближчим часом 🙌"

            # Формуємо текст для підтвердження клієнту
            items_lines = '\n'.join(
                f"  • {i.get('product_name','—')} × {i.get('quantity',1)}"
                for i in items
            )
            line = f"📦 *Товари:*\n{items_lines}"
            gift = data.get('gift')
            if gift:
                line += f"\n  🎁 {gift} — *безкоштовно*"
            line += f"\n💰 *Разом: {total_price} ₴*"
            if comment:
                line += f"\n📝 _{comment}_"
            
            # Формуємо текст для нового повідомлення
            confirm_text = f"✅ *Замовлення прийнято!*\n\n{line}\n"
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

    items_text = ''
    for i in items:
        line = f"  • {i.get('product_name','—')} × {i.get('quantity',1)} — {i.get('price',0) * i.get('quantity',1)} ₴"
        if i.get('customValue'):
            line += f" _{i['customValue']}_"
        items_text += line + '\n'

    # Формуємо текст для власника з деталізацією замовлення і кнопками для зміни статусу
    owner_text = (
        f"🔔 <b>НОВЕ ЗАМОВЛЕННЯ #{order_id}</b>\n\n"
        f"👤 Від: {username}\n\n"
        f"📦 <b>Товари:</b>\n{items_text}\n"
        f"💰 <b>Разом: {total_price} ₴</b>\n"
    )
    gift = data.get('gift')

    # Якщо є подарунок, додаємо його в текст замовлення для власника
    if gift:
        owner_text += f"🎁 Подарунок: {gift} — безкоштовно\n"

    # Якщо замовлення було оформлено з купоном, додаємо інформацію про купон і знижку в текст замовлення для власника
    if coupon_code:
        owner_text += f"🎟️ Купон: <b>{coupon_code}</b> (-{discount_amount} ₴)\n"

    # Якщо клієнт залишив коментар, додаємо його в текст замовлення для власника
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
            parse_mode='HTML',
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

# Новий HTTP хендлер для обробки CORS preflight запитів, який відповідає на OPTIONS запити з необхідними заголовками для дозволу крос-доменних запитів від веб-додатку. Це забезпечує правильну взаємодію між веб-додатком і сервером бота при оформленні замовлення, дозволяючи веб-додатку виконувати POST запити до цього хендлера без проблем з CORS.
async def handle_options(request):
    return web.Response(
        headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type'
        }
    )

# Новий HTTP хендлер для перевірки купона, який приймає код купона, ID користувача і загальну суму кошика, виконує всі необхідні перевірки валідності купона і повертає результат у вигляді JSON-об'єкта з інформацією про валідність купона, тип і значення знижки, а також повідомлення для клієнта. Цей хендлер дозволяє веб-додатку динамічно перевіряти купони при оформленні замовлення і відображати відповідні повідомлення клієнту.
async def handle_check_coupon(request):
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="Bad JSON")

    code       = data.get('code', '').strip()
    user_id    = data.get('user_id', 0)
    cart_total = data.get('cart_total', 0)

    if not code:
        result = {"valid": False, "message": "Введи код купону"}
    else:
        result = check_coupon(code, user_id, cart_total)

    return web.Response(
        text=json.dumps(result, ensure_ascii=False),
        content_type='application/json',
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

    # Оновлюємо текст повідомлення, додаючи статус в кінець. Якщо кнопка "Написати" була, вона залишиться (крім випадку скасування, коли всі кнопки зникають).
    try:
        await query.edit_message_text(
            text=query.message.text + f"\n\n*Статус: {label}*",
            parse_mode='Markdown',
            reply_markup=markup
        )
    except Exception:
        pass


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
    bot_app.add_handler(CommandHandler("coupon", coupon_cmd))
    bot_app.add_handler(CallbackQueryHandler(order_action, pattern=r"^(confirm|draft|cancel)_"))

    async def run():
        http_app = web.Application()
        http_app.router.add_post('/order', handle_order)
        http_app.router.add_route('OPTIONS', '/order', handle_options)
        http_app.router.add_post('/check_coupon', handle_check_coupon)
        http_app.router.add_route('OPTIONS', '/check_coupon', handle_options)
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