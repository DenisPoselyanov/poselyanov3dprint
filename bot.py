"""
Denis 3D Print — Telegram Bot
"""

from datetime import datetime, timedelta
from aiohttp import web
import asyncio
import hashlib
import html
import hmac
import json
import logging
import os
import sqlite3
from pathlib import Path
from urllib.parse import parse_qsl
from urllib.parse import urlparse
try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:
    psycopg = None
    dict_row = None
import cloudinary
import cloudinary.uploader
import base64
import io
from PIL import Image
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, WebAppInfo,
    BotCommandScopeChat, BotCommandScopeAllPrivateChats
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# Завантажуємо змінні середовища з .env файлу, щоб не зберігати конфіденційні дані (як-от токен бота) прямо в коді.
from dotenv import load_dotenv
from rich_messages import (
    build_admin_order_notification,
    build_admin_order_with_status,
    build_broadcast_report,
    build_client_order_confirmation,
    build_client_price_quote,
    build_order_history,
    build_order_status,
    edit_rich_message,
    send_rich_message,
)
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

import config
from auth import (
    cors_headers,
    extract_init_data,
    is_admin_authorized,
    require_admin,
    resolve_request_user,
    validate_telegram_init_data,
)
from catalog_store import (
    CUSTOM_PRODUCTS_CACHE,
    CATEGORIES_CACHE,
    FILAMENTS_CACHE,
    PRODUCTS_CACHE,
    add_category,
    add_product,
    bootstrap_json_catalog,
    delete_category,
    delete_product,
    get_all_categories,
    get_all_products,
    get_category_by_id,
    get_filament_by_id,
    get_product_by_id,
    init_catalog_tables,
    is_contract_product,
    is_valid_category_id,
    load_filaments_file,
    reload_categories_cache,
    reload_filaments_cache,
    reload_products_cache,
    sync_filament_colors_table,
    update_category,
    update_filament,
    update_product,
    validate_product_prices,
)
from db_core import db_connect, is_postgres as _is_postgres, run_db, sql as _sql
from security_utils import is_safe_http_url, is_static_file_allowed, validate_stl_link

BOT_TOKEN = config.BOT_TOKEN
OWNER_ID = config.OWNER_ID
ORDERS_CHAT_ID = config.ORDERS_CHAT_ID
WEBAPP_URL = config.WEBAPP_URL
DB_FILE = config.DB_FILE
VALIDATE_INIT_DATA = config.VALIDATE_INIT_DATA
PROMOTION_ENABLED = config.PROMOTION_ENABLED
MAX_UPLOAD_BYTES = config.MAX_UPLOAD_BYTES

if not all([config.CLOUDINARY_CLOUD_NAME, config.CLOUDINARY_API_KEY, config.CLOUDINARY_API_SECRET]):
    logger_pre = logging.getLogger(__name__)
    logger_pre.warning("Cloudinary credentials missing — photo upload disabled until .env is configured")
else:
    cloudinary.config(
        cloud_name=config.CLOUDINARY_CLOUD_NAME,
        api_key=config.CLOUDINARY_API_KEY,
        api_secret=config.CLOUDINARY_API_SECRET,
    )

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
# Глушимо зайві логи
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def is_user_blocked(user_id: int) -> bool:
    if not user_id:
        return False
    conn = db_connect()
    row = conn.execute(_sql("SELECT blocked FROM users WHERE id = ?"), (user_id,)).fetchone()
    conn.close()
    return bool(row and row[0])


def is_gif_image(file_data: bytes, content_type: str = "") -> bool:
    if len(file_data) >= 6 and file_data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    return content_type.lower().startswith("image/gif")


def is_webp_image(file_data: bytes, content_type: str = "") -> bool:
    if len(file_data) >= 12 and file_data[:4] == b"RIFF" and file_data[8:12] == b"WEBP":
        return True
    return content_type.lower() == "image/webp"


def is_animated_image(file_data: bytes, content_type: str = "") -> bool:
    if not (is_gif_image(file_data, content_type) or is_webp_image(file_data, content_type)):
        return False
    try:
        with Image.open(io.BytesIO(file_data)) as img:
            return getattr(img, "n_frames", 1) > 1
    except Exception:
        return is_gif_image(file_data, content_type)


ANIMATED_MAX_MEGAPIXELS = 45.0
ANIMATED_MIN_WIDTH = 320
ANIMATED_MAX_FRAMES = 80
ANIMATED_RESAMPLE = Image.Resampling.BILINEAR

STATIC_MAX_WIDTH = 1200
STATIC_JPEG_QUALITY = 85
STATIC_WEBP_QUALITY = 85
STATIC_REENCODE_MIN_BYTES = 300 * 1024  # 300 KB


def _animated_frame_indices(n_frames: int, max_frames: int = ANIMATED_MAX_FRAMES) -> list[int]:
    if n_frames <= max_frames:
        return list(range(n_frames))
    step = max(1, (n_frames + max_frames - 1) // max_frames)
    return list(range(0, n_frames, step))


def prepare_animated_for_upload(file_data: bytes, content_type: str = "", max_megapixels: float = ANIMATED_MAX_MEGAPIXELS) -> bytes:
    """Зменшити анімований GIF/WebP, якщо сума пікселів у кадрах перевищує ліміт Cloudinary (50 MP)."""
    try:
        with Image.open(io.BytesIO(file_data)) as img:
            width, height = img.size
            n_frames = getattr(img, "n_frames", 1)
            frame_indices = _animated_frame_indices(n_frames)
            effective_frames = len(frame_indices)
            total_mp = (width * height * effective_frames) / 1_000_000
            needs_resize = total_mp > max_megapixels
            needs_reencode = needs_resize or effective_frames < n_frames
            if not needs_reencode:
                return file_data

            new_w, new_h = width, height
            if needs_resize:
                scale = (max_megapixels / total_mp) ** 0.5
                new_w = max(ANIMATED_MIN_WIDTH, int(width * scale))
                new_h = max(1, int(height * new_w / width))
            output_format = "WEBP" if is_webp_image(file_data, content_type) else "GIF"
            frame_step = frame_indices[1] - frame_indices[0] if len(frame_indices) > 1 else 1

            frames = []
            durations = []
            loop = img.info.get("loop", 0)

            for frame_idx in frame_indices:
                img.seek(frame_idx)
                frame = img.convert("RGBA")
                if needs_resize:
                    frame = frame.resize((new_w, new_h), ANIMATED_RESAMPLE)
                if output_format == "GIF":
                    frames.append(frame.convert("P", palette=Image.ADAPTIVE, colors=256))
                else:
                    frames.append(frame)
                durations.append(max(20, img.info.get("duration", 100) * frame_step))

            out = io.BytesIO()
            if output_format == "GIF":
                frames[0].save(
                    out,
                    format="GIF",
                    save_all=True,
                    append_images=frames[1:],
                    duration=durations,
                    loop=loop,
                    disposal=2,
                )
            else:
                frames[0].save(
                    out,
                    format="WEBP",
                    save_all=True,
                    append_images=frames[1:],
                    duration=durations,
                    loop=loop,
                    lossless=False,
                    quality=85,
                    method=4,
                )
            logger.info(
                f"{output_format} оптимізовано: {width}x{height}x{n_frames} ({total_mp:.1f} MP) "
                f"-> {new_w}x{new_h}x{effective_frames}"
            )
            return out.getvalue()
    except Exception as e:
        logger.warning(f"⚠️ Не вдалося оптимізувати анімоване зображення локально: {e}")
        return file_data


def prepare_static_for_upload(file_data: bytes, content_type: str = "") -> tuple[bytes, str]:
    """Стиск статичних зображень перед завантаженням (paste, файл, URL)."""
    try:
        with Image.open(io.BytesIO(file_data)) as img:
            if getattr(img, "n_frames", 1) > 1:
                return file_data, content_type

            width, height = img.size
            needs_resize = width > STATIC_MAX_WIDTH
            needs_compress = len(file_data) > STATIC_REENCODE_MIN_BYTES
            if not needs_resize and not needs_compress:
                return file_data, content_type

            work = img
            if needs_resize:
                new_w = STATIC_MAX_WIDTH
                new_h = max(1, int(height * new_w / width))
                work = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            has_alpha = work.mode in ("RGBA", "LA") or (
                work.mode == "P" and "transparency" in work.info
            )

            out = io.BytesIO()
            if has_alpha:
                if work.mode != "RGBA":
                    work = work.convert("RGBA")
                work.save(out, format="WEBP", quality=STATIC_WEBP_QUALITY, method=4)
                new_type = "image/webp"
            else:
                if work.mode != "RGB":
                    work = work.convert("RGB")
                work.save(out, format="JPEG", quality=STATIC_JPEG_QUALITY, optimize=True)
                new_type = "image/jpeg"

            optimized = out.getvalue()
            logger.info(
                f"Статичне фото оптимізовано: {width}x{height} "
                f"{len(file_data) // 1024}KB -> {len(optimized) // 1024}KB"
            )
            return optimized, new_type
    except Exception as e:
        logger.warning(f"⚠️ Не вдалося оптимізувати статичне зображення: {e}")
        return file_data, content_type


def normalize_image_content_type(file_data: bytes, content_type: str = "") -> str:
    if is_gif_image(file_data, content_type):
        return "image/gif"
    if is_webp_image(file_data, content_type):
        return "image/webp"
    return content_type


def get_admin_webapp_url(order_id: int | None = None) -> str | None:
    admin_url = (os.environ.get("ADMIN_WEBAPP_URL") or "").strip()
    if not admin_url or not admin_url.lower().startswith("https://"):
        return None
    if "ngrok" in admin_url and "bypass=admin" not in admin_url:
        sep = "&" if "?" in admin_url else "?"
        admin_url = f"{admin_url}{sep}bypass=admin"
    if order_id:
        sep = "&" if "?" in admin_url else "?"
        admin_url = f"{admin_url}{sep}order={order_id}"
    return admin_url


def fetch_image_bytes_from_url(url: str, max_bytes: int = MAX_UPLOAD_BYTES) -> tuple[bytes, str]:
    import urllib.request

    if not is_safe_http_url(url):
        raise ValueError("URL не дозволений для завантаження")

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "poselyanov3dprint-admin/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        content_type = resp.headers.get("Content-Type", "")
        chunks = []
        total = 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"Файл занадто великий (>{max_bytes // 1024 // 1024}MB)")
            chunks.append(chunk)
        return b"".join(chunks), content_type


def upload_photo_to_cloudinary_sync(file_data: bytes, filename: str = "product_photo", content_type: str = ""):
    """Завантажити фото на Cloudinary з оптимізацією (синхронно, для asyncio.to_thread)."""
    try:
        if len(file_data) > MAX_UPLOAD_BYTES:
            return {"ok": False, "error": f"Файл занадто великий ({len(file_data) / 1024 / 1024:.1f}MB). Максимум {MAX_UPLOAD_BYTES // 1024 // 1024}MB"}

        content_type = normalize_image_content_type(file_data, content_type)
        is_animated = is_animated_image(file_data, content_type)
        original_data = file_data
        if is_animated:
            file_data = prepare_animated_for_upload(original_data, content_type)
        else:
            file_data, content_type = prepare_static_for_upload(file_data, content_type)
            content_type = normalize_image_content_type(file_data, content_type)

        upload_opts = {
            "folder": "poselyanov3dprint",
            "resource_type": "image",
            "public_id": f"{filename}_{int(datetime.now().timestamp())}",
        }
        if not is_animated:
            # Оптимізація статичних зображень: стиск до 1000px ширини
            upload_opts.update(
                width=1000,
                crop="scale",
                quality="auto:good",
                fetch_format="auto",
            )
        else:
            upload_opts["flags"] = "lossy"

        try:
            result = cloudinary.uploader.upload(file_data, **upload_opts)
        except Exception as first_error:
            if is_animated and "Megapixels" in str(first_error):
                file_data = prepare_animated_for_upload(original_data, content_type, max_megapixels=30.0)
                result = cloudinary.uploader.upload(file_data, **upload_opts)
            else:
                raise first_error
        return {
            "ok": True, 
            "url": result.get("secure_url"),
            "width": result.get("width"),
            "height": result.get("height"),
            "size": result.get("bytes")
        }
    except Exception as e:
        logger.error(f"❌ Помилка завантаження на Cloudinary: {e}")
        err = str(e)
        if "Megapixels" in err:
            err = "GIF/WebP занадто великий для завантаження. Спробуй коротший або менший файл (до ~50 млн пікселів у всіх кадрах)."
        return {"ok": False, "error": err}


async def upload_photo_to_cloudinary(file_data: bytes, filename: str = "product_photo", content_type: str = ""):
    return await asyncio.to_thread(upload_photo_to_cloudinary_sync, file_data, filename, content_type)


async def handle_options(request: web.Request):
    return web.Response(status=200, headers=cors_headers(request))


async def handle_health(request: web.Request):
    return web.json_response(
        {"ok": True, "validate_init_data": VALIDATE_INIT_DATA, "catalog_backend": config.CATALOG_BACKEND},
        headers=cors_headers(request),
    )


def validate_order_payload(items: list, coupon_code: str | None, user_id: int, client_total: int):
    """Перерахунок суми на сервері. Повертає (ok, result_dict|error_message)."""
    reload_products_cache()
    reload_filaments_cache()
    products_by_id = {p["id"]: p for p in PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE}
    if not items:
        return False, "Порожній кошик"

    subtotal = 0
    normalized = []

    for raw in items:
        pid = int(raw.get("product_id") or raw.get("id") or 0)
        qty = max(1, min(99, int(raw.get("quantity", 1))))
        product = products_by_id.get(pid)
        is_contract = False

        if product:
            if is_contract_product(product):
                price = 0
                is_contract = True
            else:
                price = int(product["price"])
            name = product["name"]
        elif raw.get("fromCustom"):
            price = int(raw.get("price", 0))
            name = raw.get("product_name") or "—"
            if price <= 0:
                return False, f"Невідомий індивідуальний товар (id {pid})"
        else:
            return False, f"Невідомий товар (id {pid})"

        filament_id = str(raw.get("filament_id") or raw.get("filamentId") or "").strip()
        filament_name = ""
        if raw.get("fromCustom"):
            filament_id = ""
            filament_name = ""
        else:
            no_filament_choice = bool(
                product and product.get("filamentChoice") is False
            )
            if no_filament_choice:
                filament_id = ""
                filament_name = ""
            elif filament_id:
                meta = next((f for f in FILAMENTS_CACHE if f.get("id") == filament_id), None)
                if not meta:
                    return False, f"Невідомий колір філаменту ({filament_id})"
                if not meta.get("available"):
                    return False, f"Колір «{meta.get('name', '')}» зараз недоступний для замовлення"
                if str(filament_id or "").startswith("luminous") and not (
                    product and product.get("luminousFilamentChoice")
                ):
                    return False, "Цей колір недоступний для обраного товару"
                filament_name = str(meta.get("name") or "").strip()

        if not is_contract:
            subtotal += price * qty
        normalized.append({
            "product_id": pid,
            "product_name": name,
            "price": price,
            "quantity": qty,
            "customValue": raw.get("customValue") or "",
            "fromCustom": bool(raw.get("fromCustom")),
            "filament_id": filament_id,
            "filament_name": filament_name,
            "is_contract_price": is_contract,
        })

    discount = 0
    if coupon_code:
        coupon_result = check_coupon(coupon_code, user_id, subtotal)
        if not coupon_result.get("valid"):
            return False, coupon_result.get("message", "Невалідний купон")
        discount = int(coupon_result.get("discount", 0))

    after_coupon_total = max(0, subtotal - discount)
    promotion_discount = 0 if coupon_code else check_promotion(after_coupon_total)
    server_total = max(0, after_coupon_total - promotion_discount)

    if server_total != int(client_total):
        return False, f"Сума не збігається (клієнт {client_total}, сервер {server_total})"

    return True, {
        "items": normalized,
        "subtotal": subtotal,
        "coupon_discount": discount,
        "promotion_discount": promotion_discount,
        "total_price": server_total,
        "price_pending": 1 if any(i.get("is_contract_price") for i in normalized) else 0,
    }


# ─── БАЗА ДАНИХ ─────────────────────────────────────────────
def init_db():
    conn = db_connect()
    if _is_postgres():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                name TEXT,
                username TEXT,
                blocked INTEGER DEFAULT 0,
                joined_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                first_name TEXT,
                total_price INTEGER,
                comment TEXT,
                gift_product_name TEXT,
                status TEXT DEFAULT 'new',
                coupon_code TEXT,
                discount_amount INTEGER DEFAULT 0,
                ordered_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id BIGSERIAL PRIMARY KEY,
                order_id BIGINT,
                product_id BIGINT,
                product_name TEXT,
                price INTEGER,
                quantity INTEGER DEFAULT 1,
                filament TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                code TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                value INTEGER NOT NULL,
                min_order INTEGER DEFAULT 0,
                uses_max INTEGER DEFAULT 0,
                uses_count INTEGER DEFAULT 0,
                one_per_user INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                expires_at TIMESTAMPTZ,
                personal_user_id BIGINT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coupon_uses (
                id BIGSERIAL PRIMARY KEY,
                code TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                order_id BIGINT,
                used_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS filament_colors (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                hex TEXT,
                available INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("ALTER TABLE coupons ADD COLUMN IF NOT EXISTS personal_user_id BIGINT")
        conn.execute("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS filament TEXT DEFAULT ''")
        conn.execute("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS is_contract_price INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS price_pending INTEGER DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_coupon_uses_user_id ON coupon_uses(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_blocked ON users(blocked)")
        init_catalog_tables(conn)
    else:
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
                expires_at   TIMESTAMP,
                personal_user_id INTEGER
            )
        """)
        c_cols = [row[1] for row in conn.execute("PRAGMA table_info(coupons)").fetchall()]
        if "personal_user_id" not in c_cols:
            conn.execute("ALTER TABLE coupons ADD COLUMN personal_user_id INTEGER")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coupon_uses (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                code      TEXT NOT NULL,
                user_id   INTEGER NOT NULL,
                order_id  INTEGER,
                used_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS filament_colors (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                hex        TEXT,
                available  INTEGER NOT NULL DEFAULT 0
            )
        """)
        oi_cols = [row[1] for row in conn.execute("PRAGMA table_info(order_items)").fetchall()]
        if "filament" not in oi_cols:
            conn.execute("ALTER TABLE order_items ADD COLUMN filament TEXT DEFAULT ''")
        if "is_contract_price" not in oi_cols:
            conn.execute("ALTER TABLE order_items ADD COLUMN is_contract_price INTEGER DEFAULT 0")
        o_cols = [row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()]
        if "price_pending" not in o_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN price_pending INTEGER DEFAULT 0")

    for r in load_filaments_file(config.FILAMENTS_FILE):
        sync_filament_colors_table(conn, r)

    conn.commit()
    conn.close()

# Зберігаємо користувача при першому контакті з ботом (або ігноруємо, якщо вже є)
def save_user(user):
    conn = db_connect()
    if _is_postgres():
        conn.execute(
            "INSERT INTO users (id, name, username) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (user.id, user.first_name, f"@{user.username}" if user.username else "—"),
        )
    else:
        conn.execute("""
            INSERT OR IGNORE INTO users (id, name, username)
            VALUES (?, ?, ?)
        """, (user.id, user.first_name, f"@{user.username}" if user.username else "—"))
    conn.commit()
    conn.close()

# Функція для збереження замовлення в базі даних, яка приймає всі необхідні дані про замовлення (користувача, товари, загальну суму, коментар, подарунок і купон), зберігає їх у відповідних таблицях (orders і order_items) і повертає ID створеного замовлення для подальшого використання в логах і кнопках.
def save_order(user_id, username, first_name, items, total_price, comment, gift_product_name=None, coupon_code=None, discount_amount=0, price_pending=0):
    conn = db_connect()
    if _is_postgres():
        cursor = conn.execute("""
            INSERT INTO orders (user_id, username, first_name, total_price, comment, gift_product_name, coupon_code, discount_amount, status, price_pending)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'new', %s)
            RETURNING id
        """, (user_id, username, first_name, total_price, comment, gift_product_name, coupon_code, discount_amount, int(price_pending or 0)))
        order_id = cursor.fetchone()[0]
    else:
        cursor = conn.execute("""
            INSERT INTO orders (user_id, username, first_name, total_price, comment, gift_product_name, coupon_code, discount_amount, status, price_pending)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
        """, (user_id, username, first_name, total_price, comment, gift_product_name, coupon_code, discount_amount, int(price_pending or 0)))
        order_id = cursor.lastrowid
    for item in items:
        fl = (item.get("filament_name") or item.get("filament_id") or "").strip()
        is_contract = 1 if item.get("is_contract_price") else 0
        conn.execute(_sql("""
            INSERT INTO order_items (order_id, product_id, product_name, price, quantity, filament, is_contract_price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """), (
            order_id,
            int(item.get("product_id") or item.get("id") or 0),
            item.get("product_name", "—"),
            int(item.get("price", 0)),
            int(item.get("quantity", 1)),
            fl,
            is_contract,
        ))

    # Якщо є подарунок, додаємо його як окремий рядок в order_items з ціною 0 і спеціальною назвою для зручності відображення в звітах і повідомленнях
    if gift_product_name:
        conn.execute(_sql("""
            INSERT INTO order_items (order_id, product_id, product_name, price, quantity)
            VALUES (?, 0, ?, 0, 1)
        """), (order_id, f"🎁 {gift_product_name} (безкоштовно)"))

    # Якщо замовлення було оформлено з купоном, оновлюємо лічильник використань цього купона і додаємо запис в таблицю coupon_uses для відстеження, хто і коли його використовував. Це дозволяє реалізувати обмеження на кількість використань купона і використання одним користувачем, а також отримувати статистику по купонам.
    if coupon_code:
        conn.execute(_sql(
            "UPDATE coupons SET uses_count = uses_count + 1 WHERE code = ?"),
            (coupon_code.upper(),)
        )
        conn.execute(_sql(
            "INSERT INTO coupon_uses (code, user_id, order_id) VALUES (?, ?, ?)"),
            (coupon_code.upper(), user_id, order_id)
        )
    conn.commit()
    conn.close()
    return order_id

# Функція для отримання статистики по користувачах і замовленнях, яка використовується в адмінській команді /stats для відображення актуальної інформації про діяльність бота.
def get_stats():
    conn = db_connect()
    row = conn.execute("""
        SELECT
            (SELECT COUNT(*) FROM users) AS user_count,
            (SELECT COUNT(*) FROM orders) AS order_count,
            (SELECT COUNT(*) FROM orders WHERE status = 'confirmed') AS order_confirmed,
            (SELECT COUNT(*) FROM orders WHERE status = 'draft') AS order_draft,
            (SELECT COUNT(*) FROM orders WHERE status = 'cancelled') AS order_cancelled,
            (SELECT COALESCE(SUM(total_price), 0) FROM orders WHERE status = 'confirmed') AS earned,
            (SELECT COALESCE(SUM(discount_amount), 0) FROM orders WHERE status = 'confirmed') AS total_discount
    """).fetchone()
    user_count, order_count, order_confirmed, order_draft, order_cancelled, earned, total_discount = row
    recent = conn.execute(
        "SELECT name, username FROM users ORDER BY joined_at DESC LIMIT 10"
    ).fetchall()
    top_products = conn.execute("""
        SELECT oi.product_name, SUM(oi.quantity) as cnt
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        WHERE o.status = 'confirmed'
        GROUP BY oi.product_name
        ORDER BY cnt DESC
        LIMIT 5
    """).fetchall()
    coupon_stats = conn.execute("""
        SELECT c.code, c.uses_count,
               COALESCE(SUM(o.discount_amount), 0) as total_discount
        FROM coupons c
        LEFT JOIN orders o ON o.coupon_code = c.code AND o.status = 'confirmed'
        GROUP BY c.code
        ORDER BY c.uses_count DESC
        LIMIT 3
    """).fetchall()
    conn.close()
    return user_count, order_count, order_confirmed, order_draft, order_cancelled, earned, recent, top_products, coupon_stats, total_discount

# Функція для перевірки купона при оформленні замовлення, яка враховує всі умови використання купона (активність, термін дії, мінімальна сума замовлення, обмеження на кількість використань і використання одним користувачем) і повертає результат у вигляді словника з інформацією про валідність купона, тип і значення знижки, а також повідомлення для клієнта.
def check_coupon(code: str, user_id: int, cart_total: int):
    conn = db_connect(dict_rows=True)
    row = conn.execute(_sql(
        "SELECT code, type, value, min_order, uses_max, uses_count, one_per_user, active, expires_at, personal_user_id FROM coupons WHERE code = ?"), (code.upper(),)
    ).fetchone()

    if not row:
        conn.close()
        return {"valid": False, "message": "Купон не знайдено ❌"}

    c = dict(row)

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
        used = conn.execute(_sql(
            "SELECT 1 FROM coupon_uses WHERE code = ? AND user_id = ?"),
            (c['code'], user_id)
        ).fetchone()
        if used:
            conn.close()
            return {"valid": False, "message": "Ти вже використовував цей купон ❌"}

    if c.get('personal_user_id') and user_id and int(c['personal_user_id']) != int(user_id):
        conn.close()
        return {"valid": False, "message": "Цей купон призначений іншому користувачу ❌"}

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

def check_promotion(cart_total: int):
    """
    Перевіряє, чи діє акція -10% на замовлення від 500 грн
    Повертає розмір знижки, якщо акція діє
    """
    if not PROMOTION_ENABLED:
        return 0
    
    PROMOTION_MIN_AMOUNT = 500  # мінімальна сума для акції
    PROMOTION_DISCOUNT_RATE = 0.10  # 10% знижка
    
    if cart_total >= PROMOTION_MIN_AMOUNT:
        discount = int(cart_total * PROMOTION_DISCOUNT_RATE)
        return discount
    return 0


def _row_to_dict(row) -> dict:
    """Convert sqlite3.Row / psycopg dict-row to a plain JSON-serializable dict."""
    if isinstance(row, dict):
        d = row
    else:
        try:
            d = dict(row)
        except Exception:
            d = {k: row[k] for k in row.keys()}
    result = {}
    for k, v in d.items():
        if isinstance(v, datetime):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result


def update_order_status(order_id: int, status: str):
    conn = db_connect()
    conn.execute(_sql("UPDATE orders SET status = ? WHERE id = ?"), (status, order_id))
    conn.commit()
    conn.close()


def get_order_with_items(order_id: int):
    conn = db_connect(dict_rows=True)
    order = conn.execute(_sql("""
        SELECT id, user_id, username, first_name, total_price, comment,
               gift_product_name, coupon_code, discount_amount, status, ordered_at, price_pending
        FROM orders WHERE id = ?
    """), (order_id,)).fetchone()
    if not order:
        conn.close()
        return None, []
    items = conn.execute(_sql("""
        SELECT id, product_id, product_name, price, quantity, filament, is_contract_price
        FROM order_items WHERE order_id = ?
    """), (order_id,)).fetchall()
    conn.close()
    return _row_to_dict(order), [_row_to_dict(i) for i in items]


def list_orders(*, pending_price_only: bool = False, limit: int = 50):
    conn = db_connect(dict_rows=True)
    sql = """
        SELECT id, user_id, username, first_name, total_price, comment,
               gift_product_name, coupon_code, discount_amount, status, ordered_at, price_pending
        FROM orders
    """
    params: list = []
    if pending_price_only:
        sql += " WHERE price_pending = ?"
        params.append(1)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(_sql(sql), tuple(params)).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def update_order_pricing(order_id: int, item_prices: dict[int, int]) -> dict:
    conn = db_connect(dict_rows=True)
    order = conn.execute(_sql("""
        SELECT id, discount_amount, user_id, first_name
        FROM orders WHERE id = ?
    """), (order_id,)).fetchone()
    if not order:
        conn.close()
        return {"ok": False, "error": "Замовлення не знайдено"}

    for item_id, price in item_prices.items():
        new_price = max(0, int(price))
        conn.execute(_sql("""
            UPDATE order_items
            SET price = ?, is_contract_price = 0
            WHERE id = ? AND order_id = ?
        """), (new_price, int(item_id), order_id))

    items = conn.execute(_sql("""
        SELECT id, product_name, price, quantity, is_contract_price
        FROM order_items WHERE order_id = ?
    """), (order_id,)).fetchall()

    subtotal = sum(
        int(i.get("price") or 0) * int(i.get("quantity") or 1)
        for i in items
        if not str(i.get("product_name", "")).startswith("🎁")
    )
    discount = int(order.get("discount_amount") or 0)
    total = max(0, subtotal - discount)
    still_pending = any(
        int(i.get("is_contract_price") or 0)
        for i in items
        if not str(i.get("product_name", "")).startswith("🎁")
    )
    conn.execute(_sql("""
        UPDATE orders SET total_price = ?, price_pending = ? WHERE id = ?
    """), (total, 1 if still_pending else 0, order_id))
    conn.commit()
    conn.close()
    return {
        "ok": True,
        "total_price": total,
        "price_pending": still_pending,
        "user_id": order.get("user_id"),
        "first_name": order.get("first_name"),
    }


def find_gift_product_id(gift_name: str | None) -> int | None:
    if not gift_name:
        return None
    name = str(gift_name).strip().lower()
    for p in PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE:
        if str(p.get("name", "")).strip().lower() == name and p.get("id"):
            return int(p["id"])
    return None

def set_blocked(user_id, blocked: bool):
    conn = db_connect()
    conn.execute(_sql("UPDATE users SET blocked = ? WHERE id = ?"), (int(blocked), user_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = db_connect()
    users = conn.execute("SELECT id FROM users WHERE blocked = 0").fetchall()
    conn.close()
    return users


# ─── ХЕНДЛЕРИ ───────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.message.from_user)

    from telegram import ReplyKeyboardRemove
    # Видаляємо стару нижню клавіатуру, якщо вона була
    tmp = await update.message.reply_text("⏳", reply_markup=ReplyKeyboardRemove())
    await tmp.delete()

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🛍️ Відкрити каталог",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])

    await update.message.reply_text(
        "👋 Привіт! Я — Poselyanov 3D Print\n\n"
        "Роблю 3D-принти на замовлення:\n"
        "Натисни кнопку нижче щоб переглянути каталог 👇",
        reply_markup=markup
    )

async def catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🛍️ Відкрити каталог",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])
    await update.message.reply_text(
        "🛍️ Каталог Poselyanov 3D Print 👇",
        reply_markup=markup
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != OWNER_ID:
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
    if update.message.from_user.id != OWNER_ID:
        return

    args = context.args
    reply = update.message.reply_to_message

    if not reply:
        await update.message.reply_text(
            "Відповідай (reply) на повідомлення командою:\n"
            "`/broadcast` — усім\n"
            "`/broadcast <product_id>` — усім + кнопка товару\n"
            "`/broadcast u:<user_id>` — конкретному юзеру\n"
            "`/broadcast u:<user_id> <product_id>` — конкретному юзеру + кнопка товару\n\n"
            "💡 Оформлюй повідомлення в Telegram з новим rich-форматуванням "
            "(заголовки, списки, цитати) — воно збережеться при розсилці.",
            parse_mode='Markdown'
        )
        return

    target_user_id = None
    product_id = None

    for arg in args:
        a = str(arg).strip().lower()
        if a.startswith("u:"):
            try:
                target_user_id = int(a[2:])
            except ValueError:
                await update.message.reply_text("❌ Невірний формат user_id. Приклад: `/broadcast u:123456789`", parse_mode='Markdown')
                return
        else:
            try:
                product_id = int(arg)
            except ValueError:
                await update.message.reply_text("❌ Невірний аргумент. Дозволено тільки `u:<user_id>` та `product_id`.", parse_mode='Markdown')
                return

    product = get_product_by_id(product_id) if product_id else None
    if product_id and not product:
        await update.message.reply_text(f"❌ Товар з ID `{product_id}` не знайдено.", parse_mode='Markdown')
        return

    if product:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(
            "🛍️ Переглянути товар",
            url=f"https://t.me/poselyanov3dprint_bot?startapp=product_{product_id}"
        )]])
        photo_url = product.get('photos', [None])[0]
    else:
        markup = None
        photo_url = None

    if target_user_id:
        known_users = {uid for (uid,) in get_all_users()}
        if target_user_id not in known_users:
            await update.message.reply_text(
                f"❌ Користувач `{target_user_id}` не знайдений у базі бота (або заблокований).",
                parse_mode='Markdown'
            )
            return
        users = [(target_user_id,)]
    else:
        users = get_all_users()

    sent, failed = 0, 0
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
            if config.BROADCAST_DELAY_SEC > 0:
                await asyncio.sleep(config.BROADCAST_DELAY_SEC)
        except Exception as e:
            logger.warning(f"Broadcast error for {user_id}: {e}")
            if "bot was blocked" in str(e) or "user is deactivated" in str(e):
                set_blocked(user_id, True)
            failed += 1

    audience = f"user {target_user_id}" if target_user_id else "всіх"
    report_html = build_broadcast_report(sent, failed, audience)
    await send_rich_message(context.bot, update.message.chat_id, report_html)

# Команда для отримання Telegram ID користувача, яка може бути корисною для адміністраторів при налаштуванні замовлень або вирішенні проблем з користувачами. Вона відповідає повідомленням з ID користувача у форматі Markdown.
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Твій Telegram ID: `{update.message.from_user.id}`",
        parse_mode='Markdown'
    )

# Функція для отримання активних купонів, які можна використати при оформленні замовлення. Вона витягує з бази даних всі купони, які відповідають умовам активності (не вичерпано, не прострочено, особисті або публічні) і повертає їх у вигляді списку рядків для відображення користувачу.
def get_my_coupons(user_id: int):
    """Повертає купони, доступні користувачу:
    - персональні (personal_user_id = user_id)
    - публічні (personal_user_id IS NULL)
    з урахуванням строку дії, ліміту використань та one_per_user.
    """
    conn = db_connect()
    date_expr = "NOW()" if _is_postgres() else "datetime('now')"
    rows = conn.execute(_sql(f"""
        SELECT
            c.code,
            c.type,
            c.value,
            c.min_order,
            c.uses_max,
            c.uses_count,
            c.one_per_user,
            c.expires_at,
            EXISTS(
                SELECT 1
                FROM coupon_uses cu
                WHERE cu.code = c.code AND cu.user_id = ?
            ) AS used_by_user
        FROM coupons c
        WHERE c.active = 1
          AND (c.personal_user_id IS NULL OR c.personal_user_id = ?)
          AND (c.expires_at IS NULL OR c.expires_at > {date_expr})
          AND (c.uses_max = 0 OR c.uses_count < c.uses_max)
        ORDER BY c.personal_user_id DESC, c.code ASC
    """), (user_id, user_id)).fetchall()
    conn.close()
    return rows

# Команда для перегляду персональних купонів користувача, яка витягує з бази даних всі активні купони, прив'язані до цього користувача, і формує зручне текстове повідомлення з деталями кожного купона (тип знижки, умови використання, термін дії) для відправки користувачу. Якщо купонів немає, вона надсилає відповідне повідомлення з підказкою слідкувати за новинами для отримання знижок.
async def mycoupons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    rows = get_my_coupons(user_id)

    if not rows:
        await update.message.reply_text(
            "🎟️ У тебе поки немає персональних купонів\n\n"
            "Слідкуй за новинами — іноді ми даруємо знижки! 🎁"
        )
        return

    months = ['січ','лют','бер','кві','тра','чер','лип','сер','вер','жов','лис','гру']

    text = "🎟️ <b>Твої купони:</b>\n" + "─" * 22 + "\n\n"

    for code, ctype, value, min_order, uses_max, uses_count, one_per_user, expires_at, used_by_user in rows:
        label = f"{value}%" if ctype == 'percent' else f"{value} ₴"
        text += f"🏷️ <b><code>{code}</code></b> — знижка {label}\n"

        if min_order:
            text += f"   • Від суми: {min_order} ₴\n"

        if one_per_user:
            text += f"   • {'⛔ Вже використано' if used_by_user else '⚡ Одноразовий'}\n"
        elif uses_max:
            left = max(0, uses_max - uses_count)
            text += f"   • Залишилось використань: {left}\n"

        if expires_at:
            try:
                dt = datetime.strptime(expires_at[:10], "%Y-%m-%d")
                exp_fmt = f"{dt.day} {months[dt.month-1]} {dt.year}"
            except Exception:
                exp_fmt = str(expires_at)[:10]
            text += f"   • Діє до: {exp_fmt}\n"
        else:
            text += f"   • Безстроковий ♾️\n"

        text += "\n"

    text += "─" * 22 + "\n"
    text += "Введи код в кошику при оформленні замовлення 🛍️"

    await update.message.reply_text(text, parse_mode='HTML')

# /sales — поточні акції з products.json
async def sales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sale_items = [p for p in PRODUCTS_CACHE if p.get('oldPrice')]

    if not sale_items:
        await update.message.reply_text(
            "😔 Зараз акцій немає\n\n"
            "Слідкуй за оновленнями — знижки з'являться незабаром! 🔔"
        )
        return

    text = "🔥 <b>Поточні акції:</b>\n" + "─" * 22 + "\n\n"

    for p in sale_items:
        emoji = p.get('emoji', '📦')
        name = p.get('name', '—')
        price = p.get('price', 0)
        old_price = p.get('oldPrice', 0)
        discount = old_price - price
        percent = round(discount / old_price * 100)

        text += f"{emoji} <b>{name}</b>\n"
        text += f"   💸 <s>{old_price} ₴</s> → <b>{price} ₴</b>\n"
        text += f"   🏷️ Економія: {discount} ₴ ({percent}%)\n\n"

    text += "─" * 22 + "\n"
    text += "Відкрий каталог щоб замовити 👇"

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🛍️ Відкрити каталог", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    await update.message.reply_text(text, parse_mode='HTML', reply_markup=markup)


# /status — статус останнього замовлення
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = db_connect(dict_rows=True)
    order = conn.execute(_sql("""
        SELECT id, total_price, status, ordered_at
        FROM orders
        WHERE user_id = ?
        ORDER BY ordered_at DESC
        LIMIT 1
    """), (user_id,)).fetchone()
    conn.close()

    if not order:
        await update.message.reply_text(
            "📭 У тебе поки немає замовлень\n\n"
            "Відкрий каталог і зроби перше замовлення! 🛍️"
        )
        return

    status_html = build_order_status(order)
    await send_rich_message(context.bot, update.message.chat_id, status_html)


# /contact — контакти
async def contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📬 <b>Контакти</b>\n"
        "──────────────────────\n\n"
        "👤 <b>Денис Поселянов</b>\n"
        "💬 Написати особисто: @denisposelyanov\n"
        "🤖 Бот магазину: @poselyanov3dprint_bot\n\n"
        "⏰ Відповідає зазвичай протягом кількох годин\n"
    )
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Написати Денису", url="https://t.me/denisposelyanov")
    ]])
    await update.message.reply_text(text, parse_mode='HTML', reply_markup=markup)

# Команда для перегляду історії замовлень користувача, яка витягує останні 10 замовлень з бази даних і формує зручне текстове повідомлення з деталями кожного замовлення (статус, дата, товари, сума, коментарі) для відправки користувачу. Якщо замовлень немає, вона надсилає відповідне повідомлення з підказкою зробити перше замовлення.
async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = db_connect(dict_rows=True)

    orders = conn.execute(_sql("""
        SELECT id, total_price, status, ordered_at, comment, gift_product_name,
               coupon_code, discount_amount
        FROM orders
        WHERE user_id = ?
        ORDER BY ordered_at DESC
        LIMIT 10
    """), (user_id,)).fetchall()

    if not orders:
        await update.message.reply_text(
            "📭 У тебе поки немає замовлень\n\n"
            "Відкрий каталог і зроби перше замовлення! 🛍️"
        )
        conn.close()
        return

    items_by_order = {}
    if orders:
        order_ids = [o["id"] for o in orders]
        placeholders = ",".join("?" * len(order_ids))
        all_items = conn.execute(
            _sql(f"""
                SELECT order_id, product_name, price, quantity, filament
                FROM order_items
                WHERE order_id IN ({placeholders})
            """),
            tuple(order_ids),
        ).fetchall()
        for item in all_items:
            oid = item["order_id"] if isinstance(item, dict) else item[0]
            items_by_order.setdefault(oid, []).append(item)

    conn.close()

    history_html = build_order_history(
        orders, items_by_order, update.message.from_user.first_name
    )
    await send_rich_message(context.bot, update.message.chat_id, history_html)

# Команда для управління купонами, яка дозволяє адміністраторам створювати, переглядати, активувати і деактивувати купони зі знижками. Вона підтримує різні формати знижок (відсоткові і фіксовані), а також додаткові параметри для обмеження використання купонів (мінімальна сума замовлення, максимальна кількість використань, використання одним користувачем і термін дії). Команда має підкоманди для кожної операції (add, list, disable, enable) і відповідає повідомленнями з результатами операцій у форматі Markdown.
async def coupon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != OWNER_ID:
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "🎟️ <b>Управління купонами</b>\n"
            "──────────────────────\n\n"
            "<b>Створити купон:</b>\n"
            "<code>/coupon add КОД percent 20</code> — знижка 20%\n"
            "<code>/coupon add КОД fixed 50</code> — знижка 50 ₴\n\n"
            "<b>Додаткові опції:</b>\n"
            "• <code>min=200</code> — мінімальна сума замовлення\n"
            "• <code>max=10</code> — максимум використань\n"
            "• <code>once</code> — одноразовий (один юзер — один раз)\n"
            "• <code>expires=2025-12-31</code> — термін дії\n"
            "• <code>user=123456789</code> — персональний (тільки для цього юзера)\n\n"
            "<b>Приклади:</b>\n"
            "<code>/coupon add ЛІТО percent 15 min=300 expires=2026-08-31</code>\n"
            "<code>/coupon add VIP fixed 100 once user=718746623</code>\n\n"
            "<b>Інші команди:</b>\n"
            "<code>/coupon list</code> — всі купони\n"
            "<code>/coupon disable КОД</code> — вимкнути\n"
            "<code>/coupon enable КОД</code> — увімкнути",
            parse_mode='HTML'
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

        # Додаткові параметри з дефолтними значеннями:
        min_order = 0; uses_max = 0; one_per_user = 0; expires_at = None; personal_user_id = None
        for opt in args[4:]:
            if opt.startswith('min='):
                min_order = int(opt[4:])
            elif opt.startswith('max='):
                uses_max = int(opt[4:])
            elif opt == 'once':
                one_per_user = 1
            elif opt.startswith('expires='):
                expires_at = opt[8:]
            elif opt.startswith('user='):
                personal_user_id = int(opt[5:])

        conn = db_connect()
        try:
            if _is_postgres():
                conn.execute("""
                    INSERT INTO coupons
                    (code, type, value, min_order, uses_max, one_per_user, active, expires_at, personal_user_id)
                    VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s)
                    ON CONFLICT (code) DO UPDATE SET
                        type = EXCLUDED.type,
                        value = EXCLUDED.value,
                        min_order = EXCLUDED.min_order,
                        uses_max = EXCLUDED.uses_max,
                        one_per_user = EXCLUDED.one_per_user,
                        active = EXCLUDED.active,
                        expires_at = EXCLUDED.expires_at,
                        personal_user_id = EXCLUDED.personal_user_id
                """, (code, ctype, value, min_order, uses_max, one_per_user, expires_at, personal_user_id))
            else:
                conn.execute("""
                    INSERT OR REPLACE INTO coupons
                    (code, type, value, min_order, uses_max, one_per_user, active, expires_at, personal_user_id)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """, (code, ctype, value, min_order, uses_max, one_per_user, expires_at, personal_user_id))
            conn.commit()
            label = f"{value}%" if ctype == 'percent' else f"{value} ₴"
            user_str = f" для юзера `{personal_user_id}`" if personal_user_id else ""
            await update.message.reply_text(f"✅ Купон `{code}` створено! Знижка {label}{user_str}", parse_mode='Markdown')

        except Exception as e:
            await update.message.reply_text(f"❌ Помилка: {e}")
        finally:
            conn.close()

    elif sub == 'list':
        conn = db_connect()
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
        conn = db_connect()
        conn.execute(_sql("UPDATE coupons SET active = ? WHERE code = ?"), (active, code))
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
        return web.Response(status=400, text="Bad JSON", headers=cors_headers(request))

    auth, err = resolve_request_user(request, data)
    if err:
        return err

    user_id = auth.get("user_id") or data.get("user_id") or 0
    if is_user_blocked(user_id):
        return web.json_response({"ok": False, "error": "Замовлення недоступне"}, status=403, headers=cors_headers(request))

    first_name = auth.get("first_name") or data.get("first_name", "")
    tg_username = auth.get("username") or data.get("tg_username")
    username = data.get("user") or (f"@{tg_username}" if tg_username else "невідомо")

    items = data.get("items", [])
    client_total = int(data.get("total_price", 0))
    comment = (data.get("comment") or "").strip()
    gift = data.get("gift")
    coupon_code = (data.get("coupon_code") or "").strip() or None

    ok, result = validate_order_payload(items, coupon_code, user_id, client_total)
    if not ok:
        return web.json_response({"ok": False, "error": result}, status=400, headers=cors_headers(request))

    items = result["items"]
    total_price = result["total_price"]
    coupon_discount = result.get("coupon_discount", 0)
    promotion_discount = result.get("promotion_discount", 0)
    total_discount = coupon_discount + promotion_discount
    coupon_code = coupon_code if coupon_discount else None
    price_pending = result.get("price_pending", 0)

    # Формуємо назву для збереження в БД (всі товари через кому)
    product_name = ', '.join(i.get('product_name', '—') for i in items)

    # Зберігаємо замовлення в базі даних і отримуємо його ID для подальшого використання в логах і кнопках
    order_id = save_order(user_id or 0, username, first_name, items, total_price, comment, gift, coupon_code, total_discount, price_pending)

    # Логування нового замовлення з інформацією про ID замовлення, назву товару, загальну суму, інформацію про купон і знижку (якщо є) і ім'я користувача. Це дозволяє відстежувати всі замовлення, які надходять через веб-додаток, і отримувати повну інформацію про них для подальшої обробки.
    coupon_info = f"  🏷️ {coupon_code} −{coupon_discount}₴" if coupon_code else ""
    promotion_info = f"  🔥 Акція −{promotion_discount}₴" if promotion_discount > 0 else ""
    logger.info(f"📦 ЗАМОВЛЕННЯ #{order_id}  {product_name}  {total_price}₴{coupon_info}{promotion_info}  від {username}")

    # Підтвердження клієнту
    if user_id:
        try:
            now = datetime.now()
            existing = confirmation_messages.get(user_id)
            gift = data.get('gift')
            order_block = {
                "items": items,
                "total_price": total_price,
                "coupon_code": coupon_code,
                "coupon_discount": coupon_discount,
                "promotion_discount": promotion_discount,
                "gift": gift,
                "comment": comment,
                "price_pending": price_pending,
            }

            if existing and now - existing["time"] < timedelta(hours=4):
                orders_batch = existing.get("orders", []) + [order_block]
                confirm_html = build_client_order_confirmation(orders_batch)
                try:
                    await edit_rich_message(
                        bot_app.bot,
                        user_id,
                        existing["message_id"],
                        confirm_html,
                    )
                    confirmation_messages[user_id] = {
                        "message_id": existing["message_id"],
                        "orders": orders_batch,
                        "html": confirm_html,
                        "time": now,
                        "format": "rich",
                    }
                except Exception:
                    msg_id = await send_rich_message(
                        bot_app.bot, user_id, confirm_html
                    )
                    confirmation_messages[user_id] = {
                        "message_id": msg_id,
                        "orders": orders_batch,
                        "html": confirm_html,
                        "time": now,
                        "format": "rich",
                    }
            else:
                orders_batch = [order_block]
                confirm_html = build_client_order_confirmation(orders_batch)
                msg_id = await send_rich_message(
                    bot_app.bot, user_id, confirm_html
                )
                confirmation_messages[user_id] = {
                    "message_id": msg_id,
                    "orders": orders_batch,
                    "html": confirm_html,
                    "time": now,
                    "format": "rich",
                }
        except Exception as e:
            logger.error(f"Помилка підтвердження клієнту: {e}")

    gift = data.get('gift')
    gift_product_id = find_gift_product_id(gift)
    admin_items = [
        {
            "product_id": int(i.get("product_id") or 0),
            "product_name": i.get("product_name", "—"),
            "price": int(i.get("price", 0)),
            "quantity": int(i.get("quantity", 1)),
            "filament_name": i.get("filament_name"),
            "customValue": i.get("customValue"),
            "is_contract_price": bool(i.get("is_contract_price")),
        }
        for i in items
    ]
    owner_html = build_admin_order_notification(
        order_id=order_id,
        username=username,
        items=admin_items,
        total_price=total_price,
        coupon_code=coupon_code,
        discount_amount=total_discount,
        coupon_discount=coupon_discount if coupon_discount else None,
        promotion_discount=promotion_discount if promotion_discount else None,
        gift=gift,
        gift_product_id=gift_product_id,
        comment=comment or None,
        price_pending=bool(price_pending),
    )

    status_buttons = [
        InlineKeyboardButton("✅ Підтвердити",     callback_data=f"confirm_{order_id}"),
        InlineKeyboardButton("❓ Під питанням", callback_data=f"draft_{order_id}"),
        InlineKeyboardButton("❌ Відміна",      callback_data=f"cancel_{order_id}"),
    ]
    tg_username = data.get('tg_username')
    admin_url = get_admin_webapp_url(order_id) if price_pending else None

    # Клавіатура для каналу — без WebApp-кнопок (Telegram не підтримує їх у каналах)
    channel_rows = [status_buttons]
    if tg_username:
        channel_rows.append([InlineKeyboardButton(f"💬 Написати {first_name}", url=f"https://t.me/{tg_username}")])
    channel_markup = InlineKeyboardMarkup(channel_rows)

    # Клавіатура для особистого чату власника — з WebApp-кнопкою
    owner_rows = []
    if admin_url:
        owner_rows.append([InlineKeyboardButton("💰 Встановити ціну", web_app=WebAppInfo(url=admin_url))])
    owner_rows.append(status_buttons)
    if tg_username:
        owner_rows.append([InlineKeyboardButton(f"💬 Написати {first_name}", url=f"https://t.me/{tg_username}")])
    owner_markup = InlineKeyboardMarkup(owner_rows)

    try:
        await send_rich_message(
            bot_app.bot,
            ORDERS_CHAT_ID,
            owner_html,
            reply_markup=channel_markup,
        )
        logger.info(f"✅ Надіслано в чат замовлень: {ORDERS_CHAT_ID}")
    except Exception as e:
        logger.error(f"❌ Помилка надсилання в канал: {e}")
        if OWNER_ID and OWNER_ID != ORDERS_CHAT_ID:
            try:
                await send_rich_message(bot_app.bot, OWNER_ID, owner_html, reply_markup=owner_markup)
                logger.info("✅ Надіслано резервно до власника (канал недоступний)")
            except Exception as e2:
                logger.error(f"❌ Резервне надсилання власнику теж не вдалось: {e2}")

    # Якщо є договірна ціна — окремо сповіщаємо власника з WebApp-кнопкою
    if admin_url and OWNER_ID and OWNER_ID != ORDERS_CHAT_ID:
        try:
            await send_rich_message(bot_app.bot, OWNER_ID, owner_html, reply_markup=owner_markup)
            logger.info("✅ Надіслано власнику з кнопкою встановлення ціни")
        except Exception as e:
            logger.error(f"❌ Помилка надсилання власнику: {e}")

    return web.Response(text="ok", headers=cors_headers(request))


# ─── АДМІН HANDLERS ─────────────────────────────────────────

async def is_admin_check(request: web.Request) -> bool:
    """Перевірити чи користувач є адміном"""
    auth, err = resolve_request_user(request, await request.json() if request.content_type == 'application/json' else {})
    if err or not auth:
        return False
    return auth.get("user_id") == OWNER_ID


# Хендлер для отримання HTML адмін панелі. Він перевіряє, чи користувач є власником (адміністратором) за допомогою resolve_request_user і initData, який може бути переданий через query параметр, заголовок або cookie. Якщо користувач не є адміном, він повертає 403 Forbidden. Якщо користувач є адміном, він намагається прочитати файл admin-panel.html і повернути його вміст як HTML відповідь. Якщо файл не знайдено, він повертає 404 Not Found.
async def handle_index(request: web.Request):
    """Отримати HTML головної сторінки"""
    try:
        html = Path("index.html").read_text(encoding="utf-8")
        return web.Response(text=html, content_type='text/html', headers=cors_headers(request))
    except FileNotFoundError:
        return web.Response(status=404, text="Index file not found", headers=cors_headers(request))

async def handle_static(request: web.Request):
    """Обслуговування статичних файлів (allowlist)."""
    file_path = request.match_info.get('path', '')

    if not is_static_file_allowed(file_path):
        return web.Response(status=403, text="Access denied", headers=cors_headers(request))

    try:
        file = Path(file_path)
        if not file.exists():
            return web.Response(status=404, text="File not found", headers=cors_headers(request))
        
        # Визначаємо content type на основі розширення
        content_types = {
            '.html': 'text/html',
            '.css': 'text/css',
            '.js': 'application/javascript',
            '.json': 'application/json',
            '.svg': 'image/svg+xml',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.ico': 'image/x-icon',
            '.woff': 'font/woff',
            '.woff2': 'font/woff2',
            '.ttf': 'font/ttf',
        }
        
        ext = file.suffix.lower()
        content_type = content_types.get(ext, 'application/octet-stream')
        
        content = file.read_bytes()
        return web.Response(body=content, content_type=content_type, headers=cors_headers(request))
    except Exception as e:
        return web.Response(status=500, text=str(e), headers=cors_headers(request))

async def handle_admin_panel(request: web.Request):
    """Отримати HTML адмін панелі (лише для OWNER_ID)."""
    init_data = extract_init_data(request)
    auth = validate_telegram_init_data(init_data) if init_data else None

    if config.VALIDATE_INIT_DATA:
        if not auth or auth.get("user_id") != OWNER_ID:
            return web.Response(status=403, text="Forbidden", headers=cors_headers(request))
    elif config.LOCAL_DEV_MODE and not is_admin_authorized(request, auth):
        return web.Response(status=403, text="Forbidden", headers=cors_headers(request))

    try:
        html_content = Path("admin-panel.html").read_text(encoding="utf-8")
        headers = cors_headers(request)
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return web.Response(text=html_content, content_type='text/html', headers=headers)
    except FileNotFoundError:
        return web.Response(status=404, text="Admin panel file not found", headers=cors_headers(request))

# Хендлер для отримання списку всіх товарів. Він викликає функцію get_all_products, яка повертає список товарів з бази даних або файлу, і повертає його у вигляді JSON відповіді. Він також додає CORS заголовки до відповіді, щоб дозволити доступ з веб-додатку.
async def handle_get_products(request: web.Request):
    """Отримати список всіх товарів"""
    products = get_all_products()
    return web.json_response(products, headers=cors_headers(request))


async def handle_get_categories(request: web.Request):
    """Отримати список категорій"""
    include_inactive = request.query.get("includeInactive", "false").lower() in ("1", "true", "yes")
    categories = get_all_categories(active_only=not include_inactive)
    return web.json_response(categories, headers=cors_headers(request))


async def handle_get_filaments(request: web.Request):
    """Отримати список філаментів."""
    reload_filaments_cache()
    return web.json_response(FILAMENTS_CACHE, headers=cors_headers(request))


async def handle_get_product(request: web.Request):
    """Отримати товар за ID"""
    product_id = int(request.match_info.get('id', 0))
    product = get_product_by_id(product_id)
    if not product:
        return web.json_response({"error": "Не знайдено"}, status=404, headers=cors_headers(request))
    return web.json_response(product, headers=cors_headers(request))


async def handle_update_filament(request: web.Request):
    """Оновити існуючий філамент."""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied
    try:
        filament_id = request.match_info.get('id', '')
        data = await request.json()
        result = update_filament(filament_id, data)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=cors_headers(request))


async def handle_create_category(request: web.Request):
    """Створити нову категорію"""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied

    try:
        data = await request.json()
        result = add_category(data)
        status = 201 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=cors_headers(request))


async def handle_update_category(request: web.Request):
    """Оновити категорію"""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied

    try:
        category_id = request.match_info.get('id', '')
        data = await request.json()
        result = update_category(category_id, data)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=cors_headers(request))


async def handle_delete_category(request: web.Request):
    """Видалити категорію"""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied

    try:
        category_id = request.match_info.get('id', '')
        result = delete_category(category_id)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=cors_headers(request))


async def handle_create_product(request: web.Request):
    """Створити новий товар"""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    # Локально без валідації дозволяємо без перевірки OWNER_ID
    denied = require_admin(request, auth)
    if denied:
        return denied

    try:
        data = await request.json()
        result = add_product(data)
        status = 201 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=cors_headers(request))


async def handle_update_product(request: web.Request):
    """Редагувати товар"""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    # Локально без валідації дозволяємо без перевірки OWNER_ID
    denied = require_admin(request, auth)
    if denied:
        return denied

    try:
        product_id = int(request.match_info.get('id', 0))
        data = await request.json()
        result = update_product(product_id, data)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=cors_headers(request))


async def handle_delete_product(request: web.Request):
    """Видалити товар"""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    # Локально без валідації дозволяємо без перевірки OWNER_ID
    denied = require_admin(request, auth)
    if denied:
        return denied

    try:
        product_id = int(request.match_info.get('id', 0))
        result = delete_product(product_id)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=cors_headers(request))


async def handle_get_orders(request: web.Request):
    """Список замовлень для адмін-панелі."""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied

    pending_only = request.query.get("pending_price", "false").lower() in ("1", "true", "yes")
    limit = min(100, max(1, int(request.query.get("limit", "50") or 50)))
    orders = list_orders(pending_price_only=pending_only, limit=limit)
    return web.json_response(orders, headers=cors_headers(request))


async def handle_get_order(request: web.Request):
    """Деталі замовлення з позиціями."""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied

    order_id = int(request.match_info.get("id", 0))
    order, items = get_order_with_items(order_id)
    if not order:
        return web.json_response({"error": "Не знайдено"}, status=404, headers=cors_headers(request))
    return web.json_response({"order": order, "items": items}, headers=cors_headers(request))


async def handle_update_order_pricing(request: web.Request):
    """Оновити ціни позицій замовлення."""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied

    try:
        order_id = int(request.match_info.get("id", 0))
        data = await request.json()
        raw_items = data.get("items") or []
        item_prices = {}
        for row in raw_items:
            item_id = int(row.get("id") or 0)
            if item_id:
                item_prices[item_id] = int(row.get("price") or 0)

        result = update_order_pricing(order_id, item_prices)
        if not result.get("ok"):
            return web.json_response(result, status=400, headers=cors_headers(request))

        if data.get("notify_client") and result.get("user_id"):
            order, items = get_order_with_items(order_id)
            if order and items:
                quote_html = build_client_price_quote(order_id, int(order.get("total_price") or 0), items)
                try:
                    await send_rich_message(bot_app.bot, int(result["user_id"]), quote_html)
                except Exception as e:
                    logger.error(f"Помилка повідомлення клієнту про ціну: {e}")

        return web.json_response(result, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_upload_photo(request: web.Request):
    """Завантажити фото на Cloudinary з повною обробкою помилок"""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    # Локально без валідації дозволяємо без перевірки OWNER_ID
    if VALIDATE_INIT_DATA and not is_admin_authorized(request, auth):
        return web.json_response({"ok": False, "error": "❌ Забачено доступу (тільки адмін)"}, status=403, headers=cors_headers(request))

    try:
        form = await request.post()
        file_field = form.get("file")

        if not file_field or not isinstance(file_field, web.FileField):
            return web.json_response(
                {"ok": False, "error": "❌ Файл не знайдено"},
                status=400,
                headers=cors_headers(request)
            )

        file_data = file_field.file.read()
        filename = (file_field.filename or f"product_{int(datetime.now().timestamp())}").rsplit('.', 1)[0]

        content_type = file_field.content_type or ''
        is_image = content_type.startswith('image/') if content_type else False
        if not is_image and not is_gif_image(file_data, content_type) and not is_webp_image(file_data, content_type):
            return web.json_response(
                {"ok": False, "error": f"❌ Не дозволений тип файлу: {content_type}. Тільки зображення"},
                status=400,
                headers=cors_headers(request)
            )
        content_type = normalize_image_content_type(file_data, content_type)

        result = await upload_photo_to_cloudinary(file_data, filename, content_type)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))

    except web.HTTPRequestEntityTooLarge:
        return web.json_response(
            {"ok": False, "error": f"❌ Файл занадто великий. Максимум {MAX_UPLOAD_BYTES // 1024 // 1024}MB"},
            status=413,
            headers=cors_headers(request)
        )
    except ValueError as e:
        logger.error(f"❌ Помилка валідації: {e}")
        return web.json_response(
            {"ok": False, "error": f"❌ Помилка: {str(e)}"}, 
            status=400, 
            headers=cors_headers(request)
        )
    except Exception as e:
        logger.error(f"❌ Помилка завантаження: {e}")
        return web.json_response(
            {"ok": False, "error": f"❌ Помилка завантаження: {str(e)}"}, 
            status=400, 
            headers=cors_headers(request)
        )


async def handle_upload_photo_url(request: web.Request):
    """Завантажити фото на Cloudinary за URL (зручно для анімованих GIF/WebP з інтернету)."""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    if VALIDATE_INIT_DATA and not is_admin_authorized(request, auth):
        return web.json_response({"ok": False, "error": "❌ Забачено доступу (тільки адмін)"}, status=403, headers=cors_headers(request))

    try:
        data = await request.json()
        url = str(data.get("url", "")).strip()
        if not url:
            return web.json_response({"ok": False, "error": "❌ Посилання не вказано"}, status=400, headers=cors_headers(request))
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return web.json_response({"ok": False, "error": "❌ Дозволені лише http/https посилання"}, status=400, headers=cors_headers(request))

        try:
            file_data, content_type = await asyncio.to_thread(fetch_image_bytes_from_url, url)
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))

        if not content_type.startswith("image/") and not is_gif_image(file_data, content_type) and not is_webp_image(file_data, content_type):
            return web.json_response({"ok": False, "error": "❌ За посиланням не зображення"}, status=400, headers=cors_headers(request))

        content_type = normalize_image_content_type(file_data, content_type)
        filename = Path(parsed.path).stem or "product_url"
        result = await upload_photo_to_cloudinary(file_data, filename, content_type)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        logger.error(f"❌ Помилка завантаження за URL: {e}")
        return web.json_response({"ok": False, "error": f"❌ Помилка: {str(e)}"}, status=400, headers=cors_headers(request))

# Новий HTTP хендлер для перевірки купона, ID користувача і загальну суму кошика, виконує всі необхідні перевірки валідності купона і повертає результат у вигляді JSON-об'єкта з інформацією про валідність купона, тип і значення знижки, а також повідомлення для клієнта. Цей хендлер дозволяє веб-додатку динамічно перевіряти купони при оформленні замовлення і відображати відповідні повідомлення клієнту.
async def handle_check_coupon(request):
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="Bad JSON", headers=cors_headers(request))

    auth, err = resolve_request_user(request, data)
    if err:
        return err

    code = (data.get("code") or "").strip()
    user_id = auth.get("user_id") or data.get("user_id", 0)
    cart_total = int(data.get("cart_total", 0))

    if not code:
        result = {"valid": False, "message": "Введи код купону"}
    else:
        result = check_coupon(code, user_id, cart_total)

    return web.json_response(result, headers=cors_headers(request))


async def reload_products_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    reload_products_cache(force=True)
    reload_filaments_cache(force=True)
    await update.message.reply_text(
        f"✅ Кеш оновлено: products={len(PRODUCTS_CACHE)}, custom={len(CUSTOM_PRODUCTS_CACHE)}, filaments={len(FILAMENTS_CACHE)}"
    )

async def order_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        return

    if query.data == "done":
        return

    action, order_id = query.data.split("_", 1)
    order_id = int(order_id)

    write_button = None
    if query.message.reply_markup:
        for row in query.message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.url and "t.me/" in btn.url:
                    write_button = btn

    if action == "confirm":
        update_order_status(order_id, "confirmed")
        label = "✅ Підтверджено"
    elif action == "draft":
        update_order_status(order_id, "draft")
        label = "❓ Під питанням"
    elif action == "cancel":
        update_order_status(order_id, "cancelled")
        label = "❌ Відмінено"
    else:
        return

    order, items = get_order_with_items(order_id)
    if not order:
        return

    if action == "cancel":
        cancelled_user_id = order.get("user_id")
        if cancelled_user_id:
            confirmation_messages.pop(int(cancelled_user_id), None)

    gift_product_id = find_gift_product_id(order.get("gift_product_name"))
    linked = action != "cancel"
    admin_items = [
        {
            "product_id": int(i.get("product_id") or 0),
            "product_name": i.get("product_name", "—"),
            "price": int(i.get("price") or 0),
            "quantity": int(i.get("quantity") or 1),
            "filament_name": i.get("filament"),
            "is_contract_price": bool(i.get("is_contract_price")),
        }
        for i in items
        if not str(i.get("product_name", "")).startswith("🎁")
    ]
    admin_html = build_admin_order_notification(
        order_id=int(order["id"]),
        username=order.get("username") or "невідомо",
        items=admin_items,
        total_price=int(order.get("total_price") or 0),
        coupon_code=order.get("coupon_code"),
        discount_amount=int(order.get("discount_amount") or 0),
        gift=order.get("gift_product_name"),
        gift_product_id=gift_product_id,
        comment=order.get("comment"),
        status_label=label,
        linked=linked,
        price_pending=bool(order.get("price_pending")),
    )

    admin_url = get_admin_webapp_url(order_id) if order.get("price_pending") else None
    if action == "confirm":
        markup = InlineKeyboardMarkup([[write_button]]) if write_button else None
    elif action == "draft":
        buttons = [
            InlineKeyboardButton("✅ Підтвердити", callback_data=f"confirm_{order_id}"),
            InlineKeyboardButton("❌ Відмінити",   callback_data=f"cancel_{order_id}"),
        ]
        rows = []
        if admin_url:
            rows.append([InlineKeyboardButton("💰 Встановити ціну", web_app=WebAppInfo(url=admin_url))])
        rows.append(buttons)
        if write_button:
            rows.append([write_button])
        markup = InlineKeyboardMarkup(rows)
    elif action == "cancel":
        markup = None
    else:
        markup = None

    try:
        await edit_rich_message(
            context.bot,
            query.message.chat_id,
            query.message.message_id,
            admin_html,
            reply_markup=markup,
        )
    except Exception:
        pass


# Команда для відкриття адмін панелі
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != OWNER_ID:
        await update.message.reply_text("❌ У вас немає доступу до адмін панелі")
        return

    admin_url = (os.environ.get("ADMIN_WEBAPP_URL") or "").strip()
    if not admin_url:
        await update.message.reply_text(
            "❌ Не задано ADMIN_WEBAPP_URL у `.env`.\n"
            "Вкажи HTTPS-адресу адмінки, наприклад через ngrok."
        )
        return

    # Telegram Web App у кнопці підтримує тільки HTTPS URL.
    if not admin_url.lower().startswith("https://"):
        await update.message.reply_text(
            "❌ `ADMIN_WEBAPP_URL` невалідний для Telegram Web App.\n"
            "Telegram приймає тільки HTTPS-посилання.\n\n"
            "Зараз у тебе: "
            f"`{admin_url}`\n\n"
            "Локально: підніми `ngrok http 8080` і встав HTTPS URL у `.env`,\n"
            "наприклад: `ADMIN_WEBAPP_URL=https://<id>.ngrok-free.app/admin/panel`",
            parse_mode='Markdown'
        )
        return

    # Для dev-режиму на ngrok додаємо bypass.
    if "ngrok" in admin_url and "bypass=admin" not in admin_url:
        sep = "&" if "?" in admin_url else "?"
        admin_url = f"{admin_url}{sep}bypass=admin"

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📊 Адмін панель",
            web_app=WebAppInfo(url=admin_url)
        )
    ]])

    await update.message.reply_text(
        "🔐 <b>Адмін панель для управління товарами</b>\n\n"
        "• ➕ Додавати нові товари\n"
        "• ✏️ Редагувати існуючі товари\n"
        "• 🗑️ Видаляти товари\n"
        "• 📸 Завантажувати фото на Cloudinary\n\n"
        "<i>Натисніть кнопку нижче щоб відкрити панель</i>",
        parse_mode='HTML',
        reply_markup=markup
    )
# ─── ЗАПУСК ─────────────────────────────────────────────────

bot_app = None

confirmation_messages = {}  # {user_id: {"message_id", "orders", "html", "time", "format"}}

def main():
    global bot_app
    init_db()
    bootstrap_json_catalog(force=True)
    logger.info(
        "🔧 Режим: VALIDATE_INIT_DATA=%s | товарів: %s + %s custom | філаментів: %s | категорій: %s",
        VALIDATE_INIT_DATA, len(PRODUCTS_CACHE), len(CUSTOM_PRODUCTS_CACHE), len(FILAMENTS_CACHE), len(CATEGORIES_CACHE),
    )

    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    bot_app.add_handler(CommandHandler("catalog", catalog))
    bot_app.add_handler(CommandHandler("start",     start))
    bot_app.add_handler(CommandHandler("myid",      myid))
    bot_app.add_handler(CommandHandler("stats",     stats))
    bot_app.add_handler(CommandHandler("broadcast", broadcast))
    bot_app.add_handler(CommandHandler("coupon", coupon_cmd))
    bot_app.add_handler(CommandHandler("admin",     admin_cmd))
    bot_app.add_handler(CommandHandler("history", history))
    bot_app.add_handler(CommandHandler("mycoupons", mycoupons))
    bot_app.add_handler(CommandHandler("sales",    sales))
    bot_app.add_handler(CommandHandler("status",   status_cmd))
    bot_app.add_handler(CommandHandler("contact",  contact))
    bot_app.add_handler(CommandHandler("reload_products", reload_products_cmd))
    # Додатково ловимо текстові варіанти кнопки/команди купонів,
    # якщо користувач надсилає саме текст, а не slash-команду.
    bot_app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^(?:/mycoupons|🎟️ Мої купони)$"),
            mycoupons,
        )
    )
    bot_app.add_handler(CallbackQueryHandler(order_action, pattern=r"^(confirm|draft|cancel)_"))

    async def run():
        http_app = web.Application(client_max_size=MAX_UPLOAD_BYTES)
        # API маршрути (перші, щоб не перехоплювалися статичними файлами)
        http_app.router.add_post('/order', handle_order)
        http_app.router.add_route('OPTIONS', '/order', handle_options)
        http_app.router.add_post('/check_coupon', handle_check_coupon)
        http_app.router.add_route('OPTIONS', '/check_coupon', handle_options)
        http_app.router.add_route('OPTIONS', '/api/{tail:.*}', handle_options)
        # Адмін API
        http_app.router.add_get('/health', handle_health)
        http_app.router.add_get('/admin/panel', handle_admin_panel)
        http_app.router.add_get('/api/products', handle_get_products)
        http_app.router.add_get('/api/categories', handle_get_categories)
        http_app.router.add_get('/api/filaments', handle_get_filaments)
        http_app.router.add_get('/api/products/{id}', handle_get_product)
        http_app.router.add_post('/api/categories', handle_create_category)
        http_app.router.add_put('/api/categories/{id}', handle_update_category)
        http_app.router.add_delete('/api/categories/{id}', handle_delete_category)
        http_app.router.add_put('/api/filaments/{id}', handle_update_filament)
        http_app.router.add_post('/api/products', handle_create_product)
        http_app.router.add_put('/api/products/{id}', handle_update_product)
        http_app.router.add_delete('/api/products/{id}', handle_delete_product)
        http_app.router.add_get('/api/orders', handle_get_orders)
        http_app.router.add_get('/api/orders/{id}', handle_get_order)
        http_app.router.add_put('/api/orders/{id}/pricing', handle_update_order_pricing)
        http_app.router.add_post('/api/upload-photo', handle_upload_photo)
        http_app.router.add_post('/api/upload-photo-url', handle_upload_photo_url)
        # Головна сторінка
        http_app.router.add_get('/', handle_index)
        # Статичні файли (останній, як catch-all)
        http_app.router.add_get('/{path:.*}', handle_static)
        runner = web.AppRunner(http_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080)))
        await site.start()
        logger.info("🌐 HTTP сервер запущено  →  порт 8080")

        async with bot_app:
            await bot_app.initialize()
            await bot_app.start()
            # Якщо раніше був увімкнений webhook — скидаємо перед polling.
            await bot_app.bot.delete_webhook(drop_pending_updates=True)
            # Критично для Render: короткий timeout зменшує "хвіст" старого процесу
            # під час rolling deploy, щоб не ловити довгі Conflict.
            await bot_app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
                timeout=10,
                poll_interval=0.5,
            )
            logger.info("🤖 Бот запущено  →  очікую замовлення...")
            # Встановлюємо адмін-команди для себе
            from telegram import BotCommandScopeChat
            await bot_app.bot.set_my_commands(
                commands=[
                    ("catalog",   "🛍️ Відкрити каталог"),
                    ("admin",     "📊 Адмін панель"),
                    ("stats",     "📊 Статистика"),
                    ("coupon",    "🎟️ Керування купонами"),
                    ("broadcast", "📨 Розсилка"),
                    ("history",   "📦 Мої замовлення"),
                    ("status",    "📋 Статус замовлення"),
                    ("mycoupons", "🎟️ Мої купони"),
                    ("sales",     "🔥 Акції"),
                    ("contact",   "📬 Контакти"),
                    ("myid",      "🪪 Мій ID"),
                ],
                scope=BotCommandScopeChat(chat_id=OWNER_ID)
            )

            # Команди для звичайних юзерів (окремо для всіх приватних чатів,
            # щоб /catalog гарантовано з'являвся у швидкому меню команд)
            user_commands = [
                ("catalog",   "🛍️ Відкрити каталог"),
                ("history",   "📦 Мої замовлення"),
                ("status",    "📋 Статус замовлення"),
                ("mycoupons", "🎟️ Мої купони"),
                ("sales",     "🔥 Акції"),
                ("contact",   "📬 Контакти"),
            ]
            await bot_app.bot.set_my_commands(commands=user_commands)
            await bot_app.bot.set_my_commands(
                commands=user_commands,
                scope=BotCommandScopeAllPrivateChats(),
            )
            await asyncio.Event().wait()

    asyncio.run(run())

if __name__ == '__main__':
    main()