"""
Denis 3D Print — Telegram Bot
"""

from datetime import datetime, timedelta, timezone
from aiohttp import web
import asyncio
import html
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse
try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:
    psycopg = None
    dict_row = None
import cloudinary
import cloudinary.uploader
import io
from PIL import Image
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    Application, ContextTypes,
)

# Завантажуємо змінні середовища з .env файлу, щоб не зберігати конфіденційні дані (як-от токен бота) прямо в коді.
from dotenv import load_dotenv
from rich_messages import (
    build_admin_order_notification,
    build_admin_order_with_status,
    build_admin_orders_batch,
    build_broadcast_report,
    build_client_order_confirmation,
    build_client_price_quote,
    build_admin_coupon_created_notification,
    build_personal_coupon_notification,
    build_order_history,
    build_order_status,
    edit_rich_message,
    is_telegram_rate_limited,
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
from security_utils import is_safe_http_url, is_static_file_allowed
import app_state
from services.coupons import (
    check_coupon,
    check_promotion as _check_promotion_service,
    create_coupon,
    delete_coupon,
    get_coupon,
    get_my_coupons,
    list_coupons,
    replace_coupon,
    set_coupon_active,
    update_coupon,
)
from services.db_utils import init_db, row_to_dict as _row_to_dict
from services.orders import (
    delete_order,
    find_gift_product_id,
    get_idempotent_order,
    get_order_with_items,
    get_orders_sharing_channel_message,
    get_orders_with_items_batch,
    list_orders,
    save_idempotent_order,
    save_order,
    set_order_channel_message_id,
    set_orders_channel_message_ids,
    tg_username_from_order as _tg_username_from_order,
    update_order_pricing,
    update_order_status,
    user_order_lock as _user_order_lock,
)
from services.stats import get_stats
from services.users import get_all_users, is_user_blocked, save_user, save_user_id, set_blocked
from services.validation import validate_order_payload
from services.notifications import notify_coupon_created
from handlers.register import configure_bot_commands, register_telegram_handlers
from routes.setup import register_http_routes

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


def check_promotion(cart_total: int):
    if not PROMOTION_ENABLED:
        return 0
    return _check_promotion_service(cart_total)


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


def _order_ok_response(request: web.Request, order_id: int, *, duplicate: bool = False) -> web.Response:
    body: dict = {"ok": True, "order_id": int(order_id)}
    if duplicate:
        body["duplicate"] = True
    return web.json_response(body, headers=cors_headers(request))


# ─── ХЕНДЛЕРИ ───────────────────────────────────────────────

async def auto_register_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user and not user.is_bot:
        await run_db(save_user, user)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    (
        user_count,
        order_count,
        order_confirmed,
        order_draft,
        order_cancelled,
        earned,
        recent,
        top_products,
        coupon_stats,
        total_discount,
    ) = await run_db(get_stats)
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

    if coupon_stats:
        lines.append(f"\n🎟️ *Купони (топ-3):*")
        for code, uses, disc in coupon_stats:
            lines.append(f"• `{code}` — {uses} раз, -{disc} ₴")
        lines.append(f"💸 Всього знижок: *{total_discount} ₴*")

    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


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
        known_users = {uid for (uid,) in await run_db(get_all_users)}
        if target_user_id not in known_users:
            await update.message.reply_text(
                f"❌ Користувач `{target_user_id}` не знайдений у базі бота (або заблокований).",
                parse_mode='Markdown'
            )
            return
        users = [(target_user_id,)]
    else:
        users = await run_db(get_all_users)

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

# Команда для перегляду персональних купонів користувача
async def mycoupons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    rows = await run_db(get_my_coupons, user_id)

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
    user = update.effective_user
    message = update.effective_message
    if not user or not message or user.id != OWNER_ID:
        return

    args = context.args
    if not args:
        await message.reply_text(
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
            "<code>/coupon enable КОД</code> — увімкнути\n"
            "<code>/coupon delete КОД</code> — видалити",
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
            await message.reply_text("❌ Значення має бути числом")
            return

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

        result = replace_coupon({
            "code": code,
            "type": ctype,
            "value": value,
            "min_order": min_order,
            "uses_max": uses_max,
            "one_per_user": one_per_user,
            "active": 1,
            "expires_at": expires_at,
            "personal_user_id": personal_user_id,
        })
        if not result.get("ok"):
            await message.reply_text(f"❌ {result.get('error', 'Помилка')}")
            return

        coupon = result.get("coupon") or {}
        label = f"{value}%" if ctype == 'percent' else f"{value} ₴"
        user_str = f" для юзера `{personal_user_id}`" if personal_user_id else ""
        reply_lines = [f"✅ Купон `{code}` створено! Знижка {label}{user_str}"]

        if result.get("created"):
            notifications = await notify_coupon_created(coupon, source="coupon_cmd")
            if personal_user_id:
                if notifications.get("user_sent"):
                    reply_lines.append(f"📨 Повідомлення надіслано юзеру `{personal_user_id}`")
                elif notifications.get("user_sent") is False:
                    reply_lines.append(
                        "⚠️ Купон створено, але повідомлення не доставлено "
                        "(юзер не писав боту або заблокував)"
                    )

        await message.reply_text("\n".join(reply_lines), parse_mode='Markdown')

    elif sub == 'list':
        rows = await run_db(list_coupons)
        if not rows:
            await message.reply_text("Купонів ще немає")
            return
        lines = ["🎟️ *Всі купони:*\n"]
        for c in rows:
            label = f"{c['value']}%" if c['type'] == 'percent' else f"{c['value']} ₴"
            status = "✅" if c.get('active') else "🚫"
            uses_count = int(c.get('uses_count') or 0)
            uses_max = int(c.get('uses_max') or 0)
            uses_str = f"{uses_count}/{uses_max}" if uses_max else f"{uses_count}/∞"
            exp = f" · до {c.get('expires_at')}" if c.get('expires_at') else ""
            lines.append(f"{status} `{c['code']}` — {label} · {uses_str}{exp}")
        await message.reply_text("\n".join(lines), parse_mode='Markdown')

    elif sub in ('disable', 'enable') and len(args) >= 2:
        code = args[1].upper()
        result = set_coupon_active(code, sub == 'enable')
        if not result.get("ok"):
            await message.reply_text(f"❌ {result.get('error', 'Помилка')}")
            return
        icon = "✅" if sub == 'enable' else "🚫"
        await message.reply_text(f"{icon} Купон `{code}` {'увімкнено' if sub == 'enable' else 'вимкнено'}", parse_mode='Markdown')

    elif sub == 'delete' and len(args) >= 2:
        code = args[1].upper()
        result = delete_coupon(code)
        if not result.get("ok"):
            await message.reply_text(f"❌ {result.get('error', 'Помилка')}")
            return
        await message.reply_text(f"🗑️ Купон `{code}` видалено", parse_mode='Markdown')

    else:
        await message.reply_text("❌ Невірний формат команди")

def _admin_items_from_db(db_items: list[dict]) -> list[dict]:
    return [
        {
            "product_id": int(i.get("product_id") or 0),
            "product_name": i.get("product_name", "—"),
            "price": int(i.get("price") or 0),
            "quantity": int(i.get("quantity") or 1),
            "filament_name": i.get("filament"),
            "is_contract_price": bool(i.get("is_contract_price")),
            "comment": i.get("comment") or "",
        }
        for i in db_items
        if not str(i.get("product_name", "")).startswith("🎁")
    ]


def _client_block_from_db(order: dict, db_items: list[dict]) -> dict:
    discount_amount = int(order.get("discount_amount") or 0)
    coupon_code = order.get("coupon_code")
    if coupon_code:
        coupon_discount = discount_amount
        promotion_discount = 0
    else:
        coupon_discount = 0
        promotion_discount = discount_amount
    return {
        "items": [
            {
                "product_name": i.get("product_name", "—"),
                "price": int(i.get("price") or 0),
                "quantity": int(i.get("quantity") or 1),
                "filament_name": i.get("filament"),
                "is_contract_price": bool(i.get("is_contract_price")),
                "comment": i.get("comment") or "",
            }
            for i in db_items
            if not str(i.get("product_name", "")).startswith("🎁")
        ],
        "total_price": int(order.get("total_price") or 0),
        "coupon_code": coupon_code,
        "coupon_discount": coupon_discount,
        "promotion_discount": promotion_discount,
        "gift": order.get("gift_product_name"),
        "comment": order.get("comment"),
        "price_pending": bool(order.get("price_pending")),
    }


def _build_single_order_channel_markup(
    order_id: int,
    price_pending: bool,
    tg_username: str | None,
    first_name: str,
) -> InlineKeyboardMarkup:
    status_buttons = [
        InlineKeyboardButton("✅ Підтвердити", callback_data=f"confirm_{order_id}"),
        InlineKeyboardButton("❓ Під питанням", callback_data=f"draft_{order_id}"),
        InlineKeyboardButton("❌ Відміна", callback_data=f"cancel_{order_id}"),
    ]
    channel_rows = []
    admin_url = get_admin_webapp_url(order_id) if price_pending else None
    if admin_url:
        channel_rows.append([InlineKeyboardButton("💰 Встановити ціну", url=admin_url)])
    channel_rows.append(status_buttons)
    if tg_username:
        channel_rows.append([InlineKeyboardButton(f"💬 Написати {first_name}", url=f"https://t.me/{tg_username}")])
    return InlineKeyboardMarkup(channel_rows)


def _build_order_status_channel_markup(
    order_id: int,
    status: str,
    *,
    price_pending: bool,
    tg_username: str | None,
    first_name: str,
) -> InlineKeyboardMarkup | None:
    write_button = None
    if tg_username:
        write_button = InlineKeyboardButton(
            f"💬 Написати {first_name or 'клієнт'}",
            url=f"https://t.me/{tg_username}",
        )

    if status == "confirmed":
        return InlineKeyboardMarkup([[write_button]]) if write_button else None
    if status == "cancelled":
        return None
    if status == "draft":
        buttons = [
            InlineKeyboardButton("✅ Підтвердити", callback_data=f"confirm_{order_id}"),
            InlineKeyboardButton("❌ Відмінити", callback_data=f"cancel_{order_id}"),
        ]
        rows = []
        admin_url = get_admin_webapp_url(order_id) if price_pending else None
        if admin_url:
            rows.append([InlineKeyboardButton("💰 Встановити ціну", url=admin_url)])
        rows.append(buttons)
        if write_button:
            rows.append([write_button])
        return InlineKeyboardMarkup(rows)
    return _build_single_order_channel_markup(order_id, price_pending, tg_username, first_name)


async def _sync_order_status_to_telegram(order_id: int, action: str) -> None:
    """Синхронізує статус замовлення з повідомленням у Telegram-каналі (як order_action)."""
    status_map = {"confirm": "confirmed", "draft": "draft", "cancel": "cancelled"}
    label_map = {
        "confirm": "✅ Підтверджено",
        "draft": "❓ Під питанням",
        "cancel": "❌ Відмінено",
    }
    if action not in status_map:
        return

    update_order_status(order_id, status_map[action])
    order, items = get_order_with_items(order_id)
    if not order:
        return

    if action in ("confirm", "cancel"):
        affected_user_id = order.get("user_id")
        if affected_user_id:
            confirmation_messages.pop(int(affected_user_id), None)

    channel_message_id = order.get("channel_message_id")
    batch_order_ids = get_orders_sharing_channel_message(order_id)
    tg_username = _tg_username_from_order(order)
    first_name = order.get("first_name") or ""

    if len(batch_order_ids) > 1 and channel_message_id:
        batch_html, batch_markup = _build_admin_batch(
            batch_order_ids,
            order.get("username") or "невідомо",
            tg_username,
            first_name,
        )
        try:
            await edit_rich_message(
                bot_app.bot,
                ORDERS_CHAT_ID,
                int(channel_message_id),
                batch_html,
                reply_markup=batch_markup,
            )
        except Exception as e:
            logger.warning("Не вдалось оновити батч-повідомлення каналу для #%s: %s", order_id, e)
        return

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
            "comment": i.get("comment") or "",
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
        status_label=label_map[action],
        linked=linked,
        price_pending=bool(order.get("price_pending")),
    )
    markup = _build_order_status_channel_markup(
        order_id,
        status_map[action],
        price_pending=bool(order.get("price_pending")),
        tg_username=tg_username,
        first_name=first_name,
    )

    if not channel_message_id:
        logger.warning("Немає channel_message_id для замовлення #%s — пропускаємо оновлення каналу", order_id)
        return

    try:
        await edit_rich_message(
            bot_app.bot,
            ORDERS_CHAT_ID,
            int(channel_message_id),
            admin_html,
            reply_markup=markup,
        )
    except Exception as e:
        logger.warning("Не вдалось оновити повідомлення каналу для #%s: %s", order_id, e)


def _build_admin_batch_section_data(order_id: int, order: dict | None = None, items: list | None = None) -> dict | None:
    """Дані одного замовлення для build_admin_orders_batch (читає з БД)."""
    if order is None or items is None:
        order, items = get_order_with_items(order_id)
    if not order:
        return None
    status = order.get("status", "new")
    if status == "confirmed":
        sl = "✅ Підтверджено"
    elif status == "cancelled":
        sl = "❌ Відмінено"
    elif status == "draft":
        sl = "❓ Під питанням"
    else:
        sl = None
    admin_items = [
        {
            "product_id": int(i.get("product_id") or 0),
            "product_name": i.get("product_name", "—"),
            "price": int(i.get("price") or 0),
            "quantity": int(i.get("quantity") or 1),
            "filament_name": i.get("filament"),
            "is_contract_price": bool(i.get("is_contract_price")),
            "comment": i.get("comment") or "",
        }
        for i in items
        if not str(i.get("product_name", "")).startswith("🎁")
    ]
    gift_name = order.get("gift_product_name")
    return {
        "order_id": order_id,
        "items": admin_items,
        "total_price": int(order.get("total_price") or 0),
        "discount_amount": int(order.get("discount_amount") or 0),
        "coupon_code": order.get("coupon_code"),
        "gift": gift_name,
        "gift_product_id": find_gift_product_id(gift_name),
        "comment": order.get("comment"),
        "price_pending": bool(order.get("price_pending")),
        "status": status,
        "status_label": sl,
    }


def _build_admin_batch(
    order_ids: list[int],
    username: str,
    tg_username: str | None,
    first_name: str,
) -> tuple:
    """Повертає (html, markup) для батч-повідомлення адмін-каналу."""
    batch_data = get_orders_with_items_batch(order_ids)
    sections = []
    for oid in order_ids:
        entry = batch_data.get(int(oid))
        if not entry:
            continue
        order, items = entry
        sec = _build_admin_batch_section_data(int(oid), order, items)
        if sec is not None:
            sections.append(sec)

    batch_html = build_admin_orders_batch(username, sections)

    rows = []
    for s in sections:
        if s.get("status") in ("confirmed", "cancelled"):
            continue
        oid = s["order_id"]
        if s.get("price_pending"):
            admin_url_o = get_admin_webapp_url(oid)
            if admin_url_o:
                rows.append([InlineKeyboardButton(f"💰 Ціна #{oid}", url=admin_url_o)])
        rows.append([
            InlineKeyboardButton(f"✅ #{oid}", callback_data=f"confirm_{oid}"),
            InlineKeyboardButton(f"❓ #{oid}", callback_data=f"draft_{oid}"),
            InlineKeyboardButton(f"❌ #{oid}", callback_data=f"cancel_{oid}"),
        ])
    if tg_username:
        rows.append([InlineKeyboardButton(f"💬 Написати {first_name}", url=f"https://t.me/{tg_username}")])
    markup = InlineKeyboardMarkup(rows) if rows else None
    return batch_html, markup


ADMIN_EDIT_DEBOUNCE_SEC = 2.0
_admin_edit_tasks: dict[int, asyncio.Task] = {}


def _build_single_admin_order_payload(
    order_id: int,
    username: str,
    tg_username: str | None,
    first_name: str,
) -> tuple[str, InlineKeyboardMarkup] | None:
    order, db_items = get_order_with_items(order_id)
    if not order:
        return None
    gift_db = order.get("gift_product_name")
    gift_product_id = find_gift_product_id(gift_db)
    order_discount = int(order.get("discount_amount") or 0)
    coupon_code_db = order.get("coupon_code")
    coupon_discount_db = order_discount if coupon_code_db else 0
    promotion_discount_db = 0 if coupon_code_db else order_discount
    price_pending_db = bool(order.get("price_pending"))
    owner_html = build_admin_order_notification(
        order_id=order_id,
        username=username,
        items=_admin_items_from_db(db_items),
        total_price=int(order.get("total_price") or 0),
        coupon_code=coupon_code_db,
        discount_amount=order_discount,
        coupon_discount=coupon_discount_db if coupon_discount_db else None,
        promotion_discount=promotion_discount_db if promotion_discount_db else None,
        gift=gift_db,
        gift_product_id=gift_product_id,
        comment=order.get("comment") or None,
        price_pending=price_pending_db,
    )
    channel_markup = _build_single_order_channel_markup(
        order_id, price_pending_db, tg_username, first_name,
    )
    return owner_html, channel_markup


async def _apply_admin_channel_edit(
    user_id: int,
    *,
    owner_html: str,
    channel_markup,
    order_ids: list[int],
    username: str,
    tg_username: str | None,
    first_name: str,
    fallback_html: str | None = None,
    fallback_markup=None,
) -> None:
    admin_batch = admin_channel_messages.get(user_id)
    if not admin_batch:
        return
    now_admin = datetime.now()
    try:
        await edit_rich_message(
            bot_app.bot,
            admin_batch["chat_id"],
            admin_batch["message_id"],
            owner_html,
            reply_markup=channel_markup,
        )
        admin_channel_messages[user_id] = {
            **admin_batch,
            "time": now_admin,
            "order_ids": order_ids,
            "username": username,
            "tg_username": tg_username,
            "first_name": first_name,
        }
        set_orders_channel_message_ids(order_ids, admin_batch["message_id"])
        logger.info("✅ Оновлено адмін-повідомлення для замовлень %s", order_ids)
    except Exception as e:
        if is_telegram_rate_limited(e):
            logger.warning(
                "Адмін-повідомлення тимчасово недоступне (rate limit), пропускаємо: %s", e
            )
            return
        logger.error("❌ Редагування адмін-повідомлення не вдалось: %s", e)
        if not fallback_html:
            return
        try:
            msg_id = await send_rich_message(
                bot_app.bot,
                ORDERS_CHAT_ID,
                fallback_html,
                reply_markup=fallback_markup,
            )
            admin_channel_messages[user_id] = {
                "message_id": msg_id,
                "chat_id": ORDERS_CHAT_ID,
                "time": now_admin,
                "order_ids": order_ids,
                "username": username,
                "tg_username": tg_username,
                "first_name": first_name,
            }
            set_orders_channel_message_ids(order_ids, msg_id)
            logger.info("✅ Надіслано нове адмін-повідомлення (fallback)")
        except Exception as e2:
            if is_telegram_rate_limited(e2):
                logger.warning("Адмін fallback тимчасово недоступний (rate limit): %s", e2)
            else:
                logger.error("❌ Помилка надсилання в канал: %s", e2)


def _schedule_admin_channel_edit(user_id: int, flush_coro) -> None:
    async def _debounced() -> None:
        try:
            await asyncio.sleep(ADMIN_EDIT_DEBOUNCE_SEC)
            await flush_coro()
        except asyncio.CancelledError:
            pass
        finally:
            if _admin_edit_tasks.get(user_id) is asyncio.current_task():
                _admin_edit_tasks.pop(user_id, None)

    old = _admin_edit_tasks.pop(user_id, None)
    if old and not old.done():
        old.cancel()
    _admin_edit_tasks[user_id] = asyncio.create_task(_debounced())


def _schedule_admin_single_order_edit(
    user_id: int,
    order_id: int,
    username: str,
    tg_username: str | None,
    first_name: str,
) -> None:
    async def _flush() -> None:
        payload = _build_single_admin_order_payload(
            order_id, username, tg_username, first_name,
        )
        if not payload:
            return
        owner_html, channel_markup = payload
        await _apply_admin_channel_edit(
            user_id,
            owner_html=owner_html,
            channel_markup=channel_markup,
            order_ids=[order_id],
            username=username,
            tg_username=tg_username,
            first_name=first_name,
            fallback_html=owner_html,
            fallback_markup=channel_markup,
        )

    _schedule_admin_channel_edit(user_id, _flush)


def _schedule_admin_batch_order_edit(
    user_id: int,
    *,
    fallback_html: str,
    fallback_markup,
) -> None:
    async def _flush() -> None:
        admin_batch = admin_channel_messages.get(user_id)
        if not admin_batch:
            return
        order_ids = admin_batch.get("order_ids") or []
        batch_html, batch_markup = _build_admin_batch(
            order_ids,
            admin_batch.get("username") or "невідомо",
            admin_batch.get("tg_username"),
            admin_batch.get("first_name") or "",
        )
        await _apply_admin_channel_edit(
            user_id,
            owner_html=batch_html,
            channel_markup=batch_markup,
            order_ids=order_ids,
            username=admin_batch.get("username") or "невідомо",
            tg_username=admin_batch.get("tg_username"),
            first_name=admin_batch.get("first_name") or "",
            fallback_html=fallback_html,
            fallback_markup=fallback_markup,
        )

    _schedule_admin_channel_edit(user_id, _flush)


async def _deliver_order_notifications(
    *,
    order_id: int,
    is_new: bool,
    user_id,
    username: str,
    first_name: str,
    tg_username: str | None,
    items: list,
    total_price: int,
    coupon_code: str | None,
    coupon_discount: int,
    promotion_discount: int,
    total_discount: int,
    comment: str,
    gift,
    price_pending: int,
) -> None:
    """Telegram-повідомлення після збереження замовлення (фоново, не блокує HTTP)."""
    if not bot_app:
        return
    try:
        if not is_new:
            order, db_items = get_order_with_items(order_id)
            if not order:
                return

            if user_id:
                try:
                    now = datetime.now()
                    order_block = _client_block_from_db(order, db_items)
                    confirm_html = build_client_order_confirmation([order_block])
                    existing = confirmation_messages.get(user_id)
                    if existing:
                        try:
                            await edit_rich_message(
                                bot_app.bot,
                                user_id,
                                existing["message_id"],
                                confirm_html,
                            )
                            confirmation_messages[user_id] = {
                                "message_id": existing["message_id"],
                                "orders": [order_block],
                                "html": confirm_html,
                                "time": now,
                                "format": "rich",
                            }
                        except Exception as e:
                            if is_telegram_rate_limited(e):
                                logger.warning(
                                    "Підтвердження клієнту тимчасово недоступне (rate limit): %s", e
                                )
                            else:
                                msg_id = await send_rich_message(bot_app.bot, user_id, confirm_html)
                                confirmation_messages[user_id] = {
                                    "message_id": msg_id,
                                    "orders": [order_block],
                                    "html": confirm_html,
                                    "time": now,
                                    "format": "rich",
                                }
                    else:
                        msg_id = await send_rich_message(bot_app.bot, user_id, confirm_html)
                        confirmation_messages[user_id] = {
                            "message_id": msg_id,
                            "orders": [order_block],
                            "html": confirm_html,
                            "time": now,
                            "format": "rich",
                        }
                except Exception as e:
                    logger.error(f"Помилка оновлення підтвердження клієнту: {e}")

            uid_for_batch = user_id if (user_id and int(user_id) > 0) else None
            admin_batch = admin_channel_messages.get(uid_for_batch) if uid_for_batch else None

            if admin_batch and uid_for_batch:
                _schedule_admin_single_order_edit(
                    uid_for_batch, order_id, username, tg_username, first_name,
                )
            elif not admin_batch and uid_for_batch:
                payload = _build_single_admin_order_payload(
                    order_id, username, tg_username, first_name,
                )
                if payload:
                    owner_html, channel_markup = payload
                    try:
                        msg_id = await send_rich_message(
                            bot_app.bot, ORDERS_CHAT_ID, owner_html, reply_markup=channel_markup,
                        )
                        admin_channel_messages[uid_for_batch] = {
                            "message_id": msg_id,
                            "chat_id": ORDERS_CHAT_ID,
                            "time": datetime.now(),
                            "order_ids": [order_id],
                            "username": username,
                            "tg_username": tg_username,
                            "first_name": first_name,
                        }
                        set_order_channel_message_id(order_id, msg_id)
                        logger.info(f"✅ Надіслано оновлене замовлення #{order_id} в канал")
                    except Exception as e:
                        if is_telegram_rate_limited(e):
                            logger.warning("Надсилання в канал тимчасово недоступне (rate limit): %s", e)
                        else:
                            logger.error(f"❌ Помилка надсилання в канал: {e}")
            return

        if user_id:
            try:
                now = datetime.now()
                existing = confirmation_messages.get(user_id)
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
                    except Exception as e:
                        if is_telegram_rate_limited(e):
                            logger.warning(
                                "Підтвердження клієнту тимчасово недоступне (rate limit): %s", e
                            )
                        else:
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
                "comment": i.get("comment") or "",
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
        admin_url = get_admin_webapp_url(order_id) if price_pending else None

        channel_rows = []
        if admin_url:
            channel_rows.append([InlineKeyboardButton("💰 Встановити ціну", url=admin_url)])
        channel_rows.append(status_buttons)
        if tg_username:
            channel_rows.append([InlineKeyboardButton(f"💬 Написати {first_name}", url=f"https://t.me/{tg_username}")])
        channel_markup = InlineKeyboardMarkup(channel_rows)

        owner_rows = []
        if admin_url:
            owner_rows.append([InlineKeyboardButton("💰 Встановити ціну", web_app=WebAppInfo(url=admin_url))])
        owner_rows.append(status_buttons)
        if tg_username:
            owner_rows.append([InlineKeyboardButton(f"💬 Написати {first_name}", url=f"https://t.me/{tg_username}")])
        owner_markup = InlineKeyboardMarkup(owner_rows)

        now_admin = datetime.now()
        uid_for_batch = user_id if (user_id and int(user_id) > 0) else None
        admin_batch = admin_channel_messages.get(uid_for_batch) if uid_for_batch else None
        use_batch = False

        if admin_batch and (now_admin - admin_batch["time"]) < timedelta(hours=4):
            for _oid in admin_batch.get("order_ids", []):
                _o, _ = get_order_with_items(_oid)
                if _o and _o.get("status") in ("new", "draft"):
                    use_batch = True
                    break

        if use_batch:
            batch_order_ids = admin_batch["order_ids"] + [order_id]
            admin_channel_messages[uid_for_batch] = {
                **admin_batch,
                "time": now_admin,
                "order_ids": batch_order_ids,
                "username": admin_batch.get("username") or username,
                "tg_username": admin_batch.get("tg_username") or tg_username,
                "first_name": admin_batch.get("first_name") or first_name,
            }
            set_order_channel_message_id(order_id, admin_batch.get("message_id"))
            _schedule_admin_batch_order_edit(
                uid_for_batch,
                fallback_html=owner_html,
                fallback_markup=channel_markup,
            )
            logger.info(f"✅ Заплановано оновлення батчу адмін-каналу: {batch_order_ids}")
        else:
            if admin_batch:
                admin_channel_messages.pop(uid_for_batch, None)
            try:
                msg_id = await send_rich_message(bot_app.bot, ORDERS_CHAT_ID, owner_html, reply_markup=channel_markup)
                if uid_for_batch:
                    admin_channel_messages[uid_for_batch] = {
                        "message_id": msg_id, "chat_id": ORDERS_CHAT_ID, "time": now_admin,
                        "order_ids": [order_id],
                        "username": username, "tg_username": tg_username, "first_name": first_name,
                    }
                set_order_channel_message_id(order_id, msg_id)
                logger.info(f"✅ Надіслано в чат замовлень: {ORDERS_CHAT_ID}")
            except Exception as e:
                logger.error(f"❌ Помилка надсилання в канал: {e}")
                if OWNER_ID and OWNER_ID != ORDERS_CHAT_ID:
                    try:
                        await send_rich_message(bot_app.bot, OWNER_ID, owner_html, reply_markup=owner_markup)
                        logger.info("✅ Надіслано резервно до власника (канал недоступний)")
                    except Exception as e2:
                        logger.error(f"❌ Резервне надсилання власнику теж не вдалось: {e2}")
    except Exception as e:
        logger.error("Помилка фонової доставки повідомлень для #%s: %s", order_id, e)


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
    if await run_db(is_user_blocked, user_id):
        return web.json_response({"ok": False, "error": "Замовлення недоступне"}, status=403, headers=cors_headers(request))

    first_name = auth.get("first_name") or data.get("first_name", "")
    tg_username = auth.get("username") or data.get("tg_username")
    await run_db(
        save_user_id,
        int(user_id or 0),
        first_name,
        (tg_username or "").lstrip("@"),
    )
    username = data.get("user") or (f"@{tg_username}" if tg_username else "невідомо")

    items = data.get("items", [])
    client_total = int(data.get("total_price", 0))
    comment = (data.get("comment") or "").strip()
    gift = data.get("gift")
    coupon_code = (data.get("coupon_code") or "").strip() or None

    ok, result = await run_db(validate_order_payload, items, coupon_code, user_id, client_total)
    if not ok:
        return web.json_response({"ok": False, "error": result}, status=400, headers=cors_headers(request))

    items = result["items"]
    total_price = result["total_price"]
    coupon_discount = result.get("coupon_discount", 0)
    promotion_discount = result.get("promotion_discount", 0)
    total_discount = coupon_discount + promotion_discount
    coupon_code = coupon_code if coupon_discount else None
    price_pending = result.get("price_pending", 0)

    for item in items:
        if not (item.get("comment") or "").strip():
            item["comment"] = comment

    # Формуємо назву для збереження в БД (всі товари через кому)
    product_name = ', '.join(i.get('product_name', '—') for i in items)

    idempotency_key = (data.get("idempotency_key") or "").strip() or None
    uid = int(user_id or 0)

    if idempotency_key and uid > 0:
        cached = await run_db(get_idempotent_order, uid, idempotency_key)
        if cached:
            logger.info(
                "♻️ Idempotent duplicate: user=%s order=#%s",
                uid, cached["order_id"],
            )
            return _order_ok_response(request, int(cached["order_id"]), duplicate=True)

    async with _user_order_lock(uid):
        if idempotency_key and uid > 0:
            cached = await run_db(get_idempotent_order, uid, idempotency_key)
            if cached:
                return _order_ok_response(request, int(cached["order_id"]), duplicate=True)

        order_id, is_new = await run_db(
            save_order,
            user_id or 0, username, first_name, items, total_price, comment,
            gift, coupon_code, total_discount, price_pending,
        )

        if idempotency_key and uid > 0:
            try:
                await run_db(save_idempotent_order, uid, idempotency_key, order_id, is_new)
            except Exception as e:
                logger.error("Не вдалось зберегти idempotency key: %s", e)

    # Логування нового замовлення з інформацією про ID замовлення, назву товару, загальну суму, інформацію про купон і знижку (якщо є) і ім'я користувача. Це дозволяє відстежувати всі замовлення, які надходять через веб-додаток, і отримувати повну інформацію про них для подальшої обробки.
    coupon_info = f"  🏷️ {coupon_code} −{coupon_discount}₴" if coupon_code else ""
    promotion_info = f"  🔥 Акція −{promotion_discount}₴" if promotion_discount > 0 else ""
    if is_new:
        logger.info(f"📦 ЗАМОВЛЕННЯ #{order_id}  {product_name}  {total_price}₴{coupon_info}{promotion_info}  від {username}")
    else:
        logger.info(f"📦 ДОДАНО до #{order_id}  {product_name}  +{total_price}₴  від {username}")

    if not is_new:
        order, _ = await run_db(get_order_with_items, order_id)
        if not order:
            return web.json_response(
                {"ok": False, "error": "Замовлення не знайдено"},
                status=500,
                headers=cors_headers(request),
            )

    asyncio.create_task(_deliver_order_notifications(
        order_id=order_id,
        is_new=is_new,
        user_id=user_id,
        username=username,
        first_name=first_name,
        tg_username=tg_username,
        items=items,
        total_price=total_price,
        coupon_code=coupon_code,
        coupon_discount=coupon_discount,
        promotion_discount=promotion_discount,
        total_discount=total_discount,
        comment=comment,
        gift=gift,
        price_pending=price_pending,
    ))
    return _order_ok_response(request, order_id)


# ─── АДМІН HANDLERS ─────────────────────────────────────────

async def handle_index(request: web.Request):
    """Отримати HTML головної сторінки"""
    try:
        html = Path("index.html").read_text(encoding="utf-8")
        headers = cors_headers(request)
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return web.Response(text=html, content_type='text/html', headers=headers)
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
    """Отримати HTML адмін панелі.

    Telegram WebApp відкриває цей URL через звичайний GET-запит браузера —
    initData на цьому етапі ще недоступний серверу (він є лише на клієнті
    через window.Telegram.WebApp.initData). Автентифікація відбувається
    на рівні API-ендпоінтів через заголовок X-Telegram-Init-Data.
    """
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
    orders = await run_db(list_orders, pending_price_only=pending_only, limit=limit)
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
    order, items = await run_db(get_order_with_items, order_id)
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

        result = await run_db(update_order_pricing, order_id, item_prices)
        if not result.get("ok"):
            return web.json_response(result, status=400, headers=cors_headers(request))

        user_id_from_result = result.get("user_id")

        if data.get("notify_client") and user_id_from_result:
            uid_int = int(user_id_from_result)
            order, items = await run_db(get_order_with_items, order_id)
            if order and items:
                quote_html = build_client_price_quote(order_id, int(order.get("total_price") or 0), items)
                existing_confirm = confirmation_messages.get(uid_int)
                edited = False
                if existing_confirm:
                    try:
                        await edit_rich_message(
                            bot_app.bot, uid_int, existing_confirm["message_id"], quote_html
                        )
                        confirmation_messages.pop(uid_int, None)
                        edited = True
                    except Exception as e:
                        logger.warning(f"Не вдалось відредагувати підтвердження клієнту, надсилаємо нове: {e}")
                if not edited:
                    try:
                        await send_rich_message(bot_app.bot, uid_int, quote_html)
                        confirmation_messages.pop(uid_int, None)
                    except Exception as e:
                        logger.error(f"Помилка повідомлення клієнту про ціну: {e}")

        # Оновлюємо повідомлення в адмін-каналі — прибираємо «договірна» та ставимо актуальну суму
        if user_id_from_result:
            uid = int(user_id_from_result)
            admin_batch = admin_channel_messages.get(uid)
            if admin_batch:
                try:
                    order_ids = admin_batch.get("order_ids") or [order_id]
                    if len(order_ids) == 1:
                        upd_order, upd_items = get_order_with_items(order_ids[0])
                        if upd_order and upd_items:
                            gift_name = upd_order.get("gift_product_name")
                            disc = int(upd_order.get("discount_amount") or 0)
                            cpn = upd_order.get("coupon_code")
                            new_html = build_admin_order_notification(
                                order_id=order_ids[0],
                                username=admin_batch.get("username") or "",
                                items=_admin_items_from_db(upd_items),
                                total_price=int(upd_order.get("total_price") or 0),
                                coupon_code=cpn,
                                discount_amount=disc,
                                coupon_discount=disc if cpn else None,
                                promotion_discount=None if cpn else (disc or None),
                                gift=gift_name,
                                gift_product_id=find_gift_product_id(gift_name),
                                comment=upd_order.get("comment") or None,
                                price_pending=bool(upd_order.get("price_pending")),
                            )
                            new_markup = _build_single_order_channel_markup(
                                order_ids[0],
                                bool(upd_order.get("price_pending")),
                                admin_batch.get("tg_username"),
                                admin_batch.get("first_name") or "",
                            )
                            await edit_rich_message(
                                bot_app.bot,
                                admin_batch["chat_id"],
                                admin_batch["message_id"],
                                new_html,
                                reply_markup=new_markup,
                            )
                    else:
                        batch_html, batch_markup = _build_admin_batch(
                            order_ids,
                            admin_batch.get("username") or "",
                            admin_batch.get("tg_username"),
                            admin_batch.get("first_name") or "",
                        )
                        await edit_rich_message(
                            bot_app.bot,
                            admin_batch["chat_id"],
                            admin_batch["message_id"],
                            batch_html,
                            reply_markup=batch_markup,
                        )
                    logger.info(f"✅ Оновлено ціну в адмін-каналі для замовлення #{order_id}")
                except Exception as e:
                    logger.error(f"❌ Помилка оновлення ціни в адмін-каналі: {e}")

        return web.json_response(result, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_update_order_status(request: web.Request):
    """Змінити статус замовлення та синхронізувати з Telegram-каналом."""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied

    try:
        order_id = int(request.match_info.get("id", 0))
        data = await request.json()
        status = (data.get("status") or "").strip().lower()
        action_map = {
            "confirmed": "confirm",
            "cancelled": "cancel",
            "draft": "draft",
        }
        if status not in action_map:
            return web.json_response(
                {"ok": False, "error": "Невідомий статус. Дозволено: confirmed, cancelled, draft"},
                status=400,
                headers=cors_headers(request),
            )

        order, _ = await run_db(get_order_with_items, order_id)
        if not order:
            return web.json_response(
                {"ok": False, "error": "Замовлення не знайдено"},
                status=404,
                headers=cors_headers(request),
            )

        await _sync_order_status_to_telegram(order_id, action_map[status])
        return web.json_response({"ok": True, "status": status}, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_delete_order(request: web.Request):
    """Видалити замовлення."""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied

    try:
        order_id = int(request.match_info.get("id", 0))
        result = await run_db(delete_order, order_id)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=cors_headers(request))


async def handle_get_coupons(request: web.Request):
    """Список купонів для адмін-панелі."""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied
    return web.json_response(await run_db(list_coupons), headers=cors_headers(request))


async def handle_create_coupon(request: web.Request):
    """Створити купон."""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied

    try:
        data = await request.json()
        result = await run_db(create_coupon, data)
        if not result.get("ok"):
            return web.json_response(result, status=400, headers=cors_headers(request))

        notifications = await notify_coupon_created(result.get("coupon") or {}, source="admin_panel")
        result["notifications"] = notifications
        return web.json_response(result, status=201, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_update_coupon(request: web.Request):
    """Оновити купон."""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied

    try:
        code = request.match_info.get("code", "")
        data = await request.json()

        if "active" in data and len(data) == 1:
            result = await run_db(set_coupon_active, code, bool(data.get("active")))
        else:
            result = await run_db(update_coupon, code, data)

        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_delete_coupon(request: web.Request):
    """Видалити купон."""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied

    try:
        code = request.match_info.get("code", "")
        result = await run_db(delete_coupon, code)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_upload_photo(request: web.Request):
    """Завантажити фото на Cloudinary з повною обробкою помилок"""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    denied = require_admin(request, auth)
    if denied:
        return denied

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
    denied = require_admin(request, auth)
    if denied:
        return denied

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
        result = await run_db(check_coupon, code, user_id, cart_total)

    return web.json_response(result, headers=cors_headers(request))


async def reload_products_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not message or user.id != OWNER_ID:
        return
    reload_products_cache(force=True)
    reload_filaments_cache(force=True)
    await message.reply_text(
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
        await run_db(update_order_status, order_id, "confirmed")
        label = "✅ Підтверджено"
    elif action == "draft":
        await run_db(update_order_status, order_id, "draft")
        label = "❓ Під питанням"
    elif action == "cancel":
        await run_db(update_order_status, order_id, "cancelled")
        label = "❌ Відмінено"
    else:
        return

    order, items = await run_db(get_order_with_items, order_id)
    if not order:
        return

    if action in ("confirm", "cancel"):
        affected_user_id = order.get("user_id")
        if affected_user_id:
            confirmation_messages.pop(int(affected_user_id), None)

    # ── Батч-повідомлення адмін-каналу ───────────────────────
    uid_for_admin_batch = int(order.get("user_id") or 0) or None
    admin_batch = admin_channel_messages.get(uid_for_admin_batch) if uid_for_admin_batch else None
    if admin_batch and order_id in admin_batch.get("order_ids", []):
        full_ids = list(admin_batch["order_ids"])
        batch_html, batch_markup = _build_admin_batch(
            full_ids,
            admin_batch.get("username") or order.get("username") or "невідомо",
            admin_batch.get("tg_username"),
            admin_batch.get("first_name") or "",
        )
        if action in ("confirm", "cancel"):
            new_ids = [oid for oid in full_ids if oid != order_id]
            if new_ids:
                admin_channel_messages[uid_for_admin_batch] = {**admin_batch, "order_ids": new_ids}
            else:
                admin_channel_messages.pop(uid_for_admin_batch, None)
        try:
            await edit_rich_message(
                context.bot, query.message.chat_id, query.message.message_id,
                batch_html, reply_markup=batch_markup,
            )
        except Exception:
            pass
        return

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
            "comment": i.get("comment") or "",
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

confirmation_messages = app_state.confirmation_messages
admin_channel_messages = app_state.admin_channel_messages
bot_app = app_state.bot_app


def main():
    global bot_app
    config.validate_startup_config()
    init_db()
    bootstrap_json_catalog(force=True)
    logger.info(
        "🔧 Режим: VALIDATE_INIT_DATA=%s | товарів: %s + %s custom | філаментів: %s | категорій: %s",
        VALIDATE_INIT_DATA, len(PRODUCTS_CACHE), len(CUSTOM_PRODUCTS_CACHE), len(FILAMENTS_CACHE), len(CATEGORIES_CACHE),
    )

    app_state.bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app = app_state.bot_app

    register_telegram_handlers(bot_app, sys.modules[__name__])

    async def run():
        http_app = web.Application(client_max_size=MAX_UPLOAD_BYTES)
        register_http_routes(http_app, sys.modules[__name__])
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
            user_commands = [
                ("catalog", "🛍️ Відкрити каталог"),
                ("history", "📦 Мої замовлення"),
                ("status", "📋 Статус замовлення"),
                ("mycoupons", "🎟️ Мої купони"),
                ("sales", "🔥 Акції"),
                ("contact", "📬 Контакти"),
            ]
            await configure_bot_commands(bot_app, owner_id=OWNER_ID, user_commands=user_commands)
            await asyncio.Event().wait()

    asyncio.run(run())

if __name__ == '__main__':
    main()