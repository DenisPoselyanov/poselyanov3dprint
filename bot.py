"""
Poselyanov 3D Print — Telegram Bot
"""

from datetime import datetime, timedelta, timezone
import aiohttp
from aiohttp import web
import asyncio
import concurrent.futures
import html
import json
import logging
import os
import re
import signal
import sys
import time
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
    WebAppInfo, LinkPreviewOptions,
)
from telegram.ext import (
    Application, ContextTypes,
)
from telegram.helpers import escape_markdown

# Завантажуємо змінні середовища з .env файлу, щоб не зберігати конфіденційні дані (як-от токен бота) прямо в коді.
from dotenv import load_dotenv
from rich_messages import (
    build_admin_order_notification,
    build_admin_order_with_status,
    build_admin_orders_batch,
    build_broadcast_report,
    build_client_order_confirmation,
    build_client_status_update,
    build_admin_coupon_created_notification,
    build_personal_coupon_notification,
    build_order_history,
    build_order_status,
    build_start_welcome,
    build_catalog_hint,
    build_no_orders,
    build_sales_empty,
    build_sales_list,
    build_mycoupons_empty,
    build_mycoupons_list,
    build_contact,
    build_admin_panel_hint,
    build_broadcast_help,
    build_cache_reloaded,
    build_contact_card,
    build_coupon_help,
    build_coupon_list,
    build_error_notice,
    build_manual_client_message,
    build_myid,
    build_notice,
    build_review_moderation,
    build_review_request,
    build_review_reminder,
    build_review_bonus,
    build_stats,
    edit_rich_message,
    escape,
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
    is_editor_user,
    require_admin,
    require_editor,
    resolve_request_user,
    validate_telegram_init_data,
)
from catalog_store import (
    CUSTOM_PRODUCTS_CACHE,
    CATEGORIES_CACHE,
    FILAMENTS_CACHE,
    PRODUCTS_CACHE,
    add_category,
    add_filament,
    add_product,
    bootstrap_json_catalog,
    bulk_update_products,
    duplicate_product,
    get_admin_product,
    get_admin_products,
    patch_product,
    set_product_status,
    delete_category,
    delete_filament,
    delete_product,
    filament_usage_counts,
    is_glow_filament,
    reorder_categories,
    get_all_categories,
    get_all_products,
    get_category_by_id,
    get_filament_by_id,
    get_product_by_id,
    get_product_variant,
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
    CouponConsumptionError,
    create_personal_coupon,
    add_coupon_users,
    channel_gated_coupons,
    check_coupon,
    check_promotion as _check_promotion_service,
    coupon_channel_gate,
    create_coupon,
    delete_coupon,
    get_coupon,
    get_my_coupons,
    list_coupons,
    remove_coupon_user,
    replace_coupon,
    set_coupon_active,
    update_coupon,
)
from services.collections import (
    create_collection,
    delete_collection,
    list_admin_collections,
    preview_collection,
    product_collection_ids,
    reorder_collections,
    set_collection_products,
    set_product_collections,
    update_collection,
    visible_collections,
)
from services.db_utils import init_db, is_gift_name, row_to_dict as _row_to_dict
from services.events import EVENT_TYPES, FUNNEL_EVENT_TYPES, popular_searches, record_funnel_event, record_search
from services.hits import bought_together, top_selling_products
from services.product_stats import admin_products_with_stats
from services.promotions import (
    create_promotion,
    delete_promotion,
    get_active_promotions,
    get_gift_rule,
    get_promotion,
    list_promotions_admin,
    public_promotions,
    set_promotion_active,
    update_promotion,
)
from services.reviews import (
    ReviewError,
    admin_list_reviews,
    admin_review_counts,
    delete_review,
    pending_review_count,
    set_admin_reply,
    get_review,
    mark_review_requested,
    mark_review_reminded,
    orders_due_review_request,
    orders_due_review_reminder,
    grant_review_bonus,
    bonus_config,
    pending_reviews,
    product_reviews,
    set_review_status,
    submit_review,
    with_stats,
)
from services.settings import (
    admin_settings,
    coupon_stacking_enabled,
    update_admin_settings,
)
from services.orders import (
    is_valid_transition,
    list_user_orders,
    update_order_fields,
    delete_order,
    find_gift_product_id,
    get_idempotent_order,
    get_order_with_items,
    get_orders_sharing_channel_message,
    get_orders_with_items_batch,
    count_orders_by_status,
    get_print_queue,
    next_status_after_print,
    set_item_printed,
    expand_status_filter,
    list_orders,
    save_idempotent_order,
    save_order,
    set_order_channel_message_id,
    set_orders_channel_message_ids,
    contact_button_target as _contact_button_target,
    update_order_pricing,
    update_order_status,
    user_order_lock as _user_order_lock,
)
from services.stats import get_stats
from services.status_messages import list_status_messages, mark_status_message_deleted, record_status_message
import services.broadcasts as broadcasts_svc
import services.channel_posts as channel_posts_svc
import services.post_cards as post_cards_svc
import services.promo_cards as promo_cards_svc
import services.ai_suggestions as ai_suggestions_svc
import services.giveaways as giveaways_svc
from services.users import (
    export_users,
    get_user,
    segment_counts,
    users_to_csv,
    get_all_users,
    get_user_display_name,
    is_user_blocked,
    list_users,
    record_activity,
    record_start,
    save_user,
    save_user_id,
    set_blocked,
)
from services.validation import validate_checkout_fields, validate_order_payload
from services.notifications import notify_coupon_created
from handlers.register import (
    TELEGRAM_ALLOWED_UPDATES,
    configure_bot_commands,
    configure_editor_menus,
    register_telegram_handlers,
)
from handlers.rate_limit import allow_coupon_check, allow_delivery_lookup, allow_event, allow_review_upload, command_cooldown
from routes.setup import register_http_routes
import services.delivery as delivery
from services.manual_orders import orders_to_csv, parse_manual_order
from services.overview import get_overview

BOT_TOKEN = config.BOT_TOKEN
OWNER_ID = config.OWNER_ID
ORDERS_CHAT_ID = config.ORDERS_CHAT_ID
WEBAPP_URL = config.WEBAPP_URL
DB_FILE = config.DB_FILE
VALIDATE_INIT_DATA = config.VALIDATE_INIT_DATA
PROMOTION_ENABLED = config.PROMOTION_ENABLED
MAX_UPLOAD_BYTES = config.MAX_UPLOAD_BYTES
REVIEW_PHOTO_MAX_BYTES = 5 * 1024 * 1024
REVIEW_REQUEST_POLL_SEC = 1800
MAX_UPLOAD_REQUEST_BYTES = config.MAX_UPLOAD_REQUEST_BYTES
CLOUDINARY_UPLOAD_TIMEOUT = 60

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
        admin_url = f"{admin_url}{sep}bypass=admin&token={config.ADMIN_BYPASS_TOKEN}"
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


def _cloudinary_upload_with_timeout(file_data: bytes, upload_opts: dict):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(cloudinary.uploader.upload, file_data, **upload_opts)
        return future.result(timeout=CLOUDINARY_UPLOAD_TIMEOUT)


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
            result = _cloudinary_upload_with_timeout(file_data, upload_opts)
        except concurrent.futures.TimeoutError:
            return {"ok": False, "error": "Час очікування завантаження на Cloudinary вичерпано"}
        except Exception as first_error:
            if is_animated and "Megapixels" in str(first_error):
                file_data = prepare_animated_for_upload(original_data, content_type, max_megapixels=30.0)
                try:
                    result = _cloudinary_upload_with_timeout(file_data, upload_opts)
                except concurrent.futures.TimeoutError:
                    return {"ok": False, "error": "Час очікування завантаження на Cloudinary вичерпано"}
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


VIDEO_CONTENT_TYPES = ("video/mp4", "video/quicktime", "video/webm")


def upload_video_to_cloudinary_sync(file_data: bytes, filename: str = "product_video"):
    """Відео товару в галерею (етап 2 редизайну). Вітрина бере легкий mp4 через трансформацію Cloudinary."""
    if len(file_data) > MAX_UPLOAD_BYTES:
        return {"ok": False, "error": f"Відео занадто велике. Максимум {MAX_UPLOAD_BYTES // 1024 // 1024}MB"}
    try:
        result = _cloudinary_upload_with_timeout(file_data, {
            "folder": "poselyanov3dprint",
            "resource_type": "video",
            "public_id": f"{filename}_{int(datetime.now().timestamp())}",
        })
    except concurrent.futures.TimeoutError:
        return {"ok": False, "error": "Час очікування завантаження на Cloudinary вичерпано"}
    except Exception as e:
        logger.error(f"❌ Помилка завантаження відео на Cloudinary: {e}")
        return {"ok": False, "error": str(e)}
    return {"ok": True, "url": result.get("secure_url"), "duration": result.get("duration"), "size": result.get("bytes")}


async def handle_options(request: web.Request):
    return web.Response(status=200, headers=cors_headers(request))


async def handle_health(request: web.Request):
    return web.json_response(
        {"ok": True, "validate_init_data": VALIDATE_INIT_DATA, "catalog_backend": config.CATALOG_BACKEND},
        headers=cors_headers(request),
    )


def _order_ok_response(
    request: web.Request, order_id: int, *, duplicate: bool = False, is_new: bool = True
) -> web.Response:
    # is_new=false — позиції додано до ще не підтвердженого замовлення (воно зберігає старий номер)
    body: dict = {"ok": True, "order_id": int(order_id), "is_new": bool(is_new)}
    if duplicate:
        body["duplicate"] = True
    return web.json_response(body, headers=cors_headers(request))


# ─── ХЕНДЛЕРИ ───────────────────────────────────────────────

async def auto_register_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user and not user.is_bot:
        await run_db(save_user, user)
        await run_db(record_activity, user.id)


@command_cooldown(config.COMMAND_COOLDOWN_SEC)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram import ReplyKeyboardRemove

    user = update.effective_user
    if user and not user.is_bot:
        await run_db(record_start, user.id, user.first_name or "", (user.username or "").lstrip("@"))

    # Розіграш: /start gw_<id>[_r<хто запросив>] — замість привітання
    gw = giveaways_svc.parse_start_param(context.args[0]) if context.args else None
    if gw and user:
        await giveaway_entry_flow(context.bot, update.message.chat_id, user.id, gw[0], gw[1])
        return

    # Видаляємо стару нижню клавіатуру, якщо вона була
    tmp = await update.message.reply_text("⏳", reply_markup=ReplyKeyboardRemove())
    await tmp.delete()

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🛍️ Відкрити каталог",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])

    await send_rich_message(
        context.bot,
        update.message.chat_id,
        build_start_welcome(),
        reply_markup=markup,
    )

async def catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🛍️ Відкрити каталог",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])
    await send_rich_message(
        context.bot,
        update.message.chat_id,
        build_catalog_hint(),
        reply_markup=markup,
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
    contact_buttons = []
    for uid, name, username in recent:
        raw_username = str(username or "").strip()
        has_handle = raw_username.startswith("@") and raw_username[1:].strip() not in ("", "невідомо", "—")
        if has_handle:
            continue
        target = _contact_button_target(uid, username)
        if not target:
            continue
        label = (str(name or "").strip() or f"ID {uid}")[:32]
        kind, value = target
        if kind == "url":
            contact_buttons.append(InlineKeyboardButton(f"💬 {label}", url=value))
        else:
            contact_buttons.append(InlineKeyboardButton(f"💬 {label}", callback_data=f"contact_{value}"))

    reply_markup = None
    if contact_buttons:
        rows = [contact_buttons[i:i + 2] for i in range(0, len(contact_buttons), 2)]
        reply_markup = InlineKeyboardMarkup(rows)

    stats_html = build_stats(
        user_count, order_count, order_confirmed, order_draft, order_cancelled, earned,
        recent, top_products, coupon_stats, total_discount,
    )
    await send_rich_message(context.bot, update.message.chat_id, stats_html, reply_markup=reply_markup)


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != OWNER_ID:
        return

    args = context.args
    reply = update.message.reply_to_message

    if not reply:
        await send_rich_message(context.bot, update.message.chat_id, build_broadcast_help())
        return

    target_user_id = None
    product_id = None

    for arg in args:
        a = str(arg).strip().lower()
        if a.startswith("u:"):
            try:
                target_user_id = int(a[2:])
            except ValueError:
                await send_rich_message(context.bot, update.message.chat_id, build_error_notice(
                    "Невірний формат user_id", "Приклад: /broadcast u:123456789", title="Розсилка не запущена"))
                return
        else:
            try:
                product_id = int(arg)
            except ValueError:
                await send_rich_message(context.bot, update.message.chat_id, build_error_notice(
                    "Невірний аргумент", "Дозволено тільки u:<user_id> та product_id", title="Розсилка не запущена"))
                return

    product = get_product_by_id(product_id) if product_id else None
    if product_id and not product:
        await send_rich_message(context.bot, update.message.chat_id, build_error_notice(
            f"Товар з ID {product_id} не знайдено", title="Розсилка не запущена"))
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
            await send_rich_message(context.bot, update.message.chat_id, build_error_notice(
                f"Користувач {target_user_id} не знайдений у базі бота (або заблокований)",
                title="Розсилка не запущена"))
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
            if broadcasts_svc.is_undeliverable_error(e):
                set_blocked(user_id, True)
            failed += 1

    audience = f"user {target_user_id}" if target_user_id else "всіх"
    report_html = build_broadcast_report(sent, failed, audience)
    await send_rich_message(context.bot, update.message.chat_id, report_html)

# Команда для отримання Telegram ID користувача, яка може бути корисною для адміністраторів при налаштуванні замовлень або вирішенні проблем з користувачами. Вона відповідає повідомленням з ID користувача у форматі Markdown.
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_rich_message(context.bot, update.message.chat_id, build_myid(update.message.from_user.id))

# --- Промокоди «лише для підписників каналу» -------------------------------------

_CHANNEL_SUB_CACHE: dict[tuple, tuple[float, bool]] = {}
_CHANNEL_SUB_TTL_SEC = 60.0
_CHANNEL_SUBSCRIBED_STATUSES = {"creator", "owner", "administrator", "member"}


def resolve_coupon_channel(coupon_channel_raw: str | None):
    """chat_id для перевірки: канал купона, інакше глобальний SUBSCRIPTION_CHANNEL."""
    return config.normalize_channel_id(coupon_channel_raw) or config.SUBSCRIPTION_CHANNEL_CHAT_ID


async def is_channel_subscriber(user_id: int, chat_id=None) -> bool:
    """Чи підписаний користувач на канал/групу chat_id.

    chat_id None → канал не заданий → без обмежень (True).
    Помилка getChatMember (бот не в каналі, приватність тощо) → вважаємо не підписаним.
    """
    if not chat_id:
        return True

    uid = int(user_id or 0)
    if uid <= 0:
        return False

    now = time.monotonic()
    key = (chat_id, uid)
    cached = _CHANNEL_SUB_CACHE.get(key)
    if cached and now - cached[0] < _CHANNEL_SUB_TTL_SEC:
        return cached[1]

    bot = getattr(app_state.bot_app, "bot", None)
    if bot is None:
        return False

    try:
        member = await bot.get_chat_member(chat_id, uid)
        status = getattr(member, "status", "")
        ok = status in _CHANNEL_SUBSCRIBED_STATUSES or (
            status == "restricted" and getattr(member, "is_member", False)
        )
    except Exception as e:
        logger.warning("get_chat_member(%s, %s) failed: %s", chat_id, uid, e)
        ok = False

    _CHANNEL_SUB_CACHE[key] = (now, ok)
    return ok


def channel_sub_required_message(coupon_channel_raw: str | None = None) -> str:
    link = config.channel_link_for(coupon_channel_raw or "") or config.subscription_channel_link()
    if link:
        return f"Цей промокод лише для підписників каналу. Підпишись: {link}"
    return "Цей промокод доступний лише підписникам каналу ❌"


# Команда для перегляду персональних купонів користувача
async def mycoupons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    rows = await run_db(get_my_coupons, user_id)

    if rows:
        codes = [(r["code"] if isinstance(r, dict) else r[0]) for r in rows]
        gated = await run_db(channel_gated_coupons, codes)
        if gated:
            visible = []
            for r in rows:
                code = (r["code"] if isinstance(r, dict) else r[0]).upper()
                if code in gated:
                    chat_id = resolve_coupon_channel(gated[code])
                    if chat_id and not await is_channel_subscriber(user_id, chat_id):
                        continue
                visible.append(r)
            rows = visible

    if not rows:
        await send_rich_message(
            context.bot,
            update.message.chat_id,
            build_mycoupons_empty(),
        )
        return

    await send_rich_message(
        context.bot,
        update.message.chat_id,
        build_mycoupons_list(rows, stacking_enabled=coupon_stacking_enabled()),
    )

# /sales — реальні акції магазину (з адмінки, services/promotions.py)
async def sales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    promotions = get_active_promotions()

    if not promotions:
        await send_rich_message(
            context.bot,
            update.message.chat_id,
            build_sales_empty(),
        )
        return

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🛍️ Відкрити каталог", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    await send_rich_message(
        context.bot,
        update.message.chat_id,
        build_sales_list(promotions),
        reply_markup=markup,
    )


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
        await send_rich_message(
            context.bot,
            update.message.chat_id,
            build_no_orders(),
        )
        return

    status_html = build_order_status(order)
    await send_rich_message(context.bot, update.message.chat_id, status_html)


# /contact — контакти
async def contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Написати Денису", url="https://t.me/denisposelyanov")
    ]])
    await send_rich_message(
        context.bot,
        update.message.chat_id,
        build_contact(),
        reply_markup=markup,
    )

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
        await send_rich_message(
            context.bot,
            update.message.chat_id,
            build_no_orders(),
        )
        conn.close()
        return

    items_by_order = {}
    if orders:
        order_ids = [o["id"] for o in orders]
        placeholders = ",".join("?" * len(order_ids))
        all_items = conn.execute(
            _sql(f"""
                SELECT order_id, product_id, product_name, price, quantity, filament
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

    async def reply(body: str):
        await send_rich_message(context.bot, message.chat_id, body)

    args = context.args
    if not args:
        await reply(build_coupon_help())
        return

    sub = args[0].lower()

    if sub == 'add' and len(args) >= 4:
        code  = args[1].upper()
        ctype = args[2].lower()  # 'percent' або 'fixed'
        try:
            value = int(args[3])
        except ValueError:
            await reply(build_error_notice("Значення має бути числом", title="Купон не створено"))
            return

        min_order = 0; uses_max = 0; one_per_user = 0; expires_at = None
        personal_user_id = None
        allowed_user_ids = None
        for opt in args[4:]:
            if opt.startswith('min='):
                min_order = int(opt[4:])
            elif opt.startswith('max='):
                uses_max = int(opt[4:])
            elif opt == 'once':
                one_per_user = 1
            elif opt.startswith('expires='):
                expires_at = opt[8:]
            elif opt.startswith('users='):
                allowed_user_ids = opt[6:]
            elif opt.startswith('user='):
                personal_user_id = int(opt[5:])

        payload = {
            "code": code,
            "type": ctype,
            "value": value,
            "min_order": min_order,
            "uses_max": uses_max,
            "one_per_user": one_per_user,
            "active": 1,
            "expires_at": expires_at,
            "personal_user_id": personal_user_id,
        }
        if allowed_user_ids is not None:
            payload["allowed_user_ids"] = allowed_user_ids

        result = replace_coupon(payload)
        if not result.get("ok"):
            await reply(build_error_notice(result.get('error', 'Помилка'), title="Купон не створено"))
            return

        coupon = result.get("coupon") or {}
        label = f"{value}%" if ctype == 'percent' else f"{value} ₴"
        allowed_count = len(coupon.get("allowed_user_ids") or [])
        items = [f"🏷️ <b><code>{escape(code)}</code></b> — знижка {label}"]
        if allowed_count == 1:
            items.append("Доступ лише для 1 користувача")
        elif allowed_count:
            items.append(f"Доступ лише для {allowed_count} користувачів")
        elif personal_user_id:
            items.append(f"Доступ лише для ID <code>{int(personal_user_id)}</code>")

        if result.get("created"):
            notifications = await notify_coupon_created(
                coupon,
                source="coupon_cmd",
                notify_user_ids=result.get("added_user_ids"),
            )
            notified = notifications.get("users_notified") or []
            failed = notifications.get("users_failed") or []
            if notified:
                if len(notified) == 1:
                    items.append(f"📨 Повідомлення надіслано юзеру <code>{notified[0]}</code>")
                else:
                    items.append(f"📨 Повідомлення надіслано {len(notified)} юзерам")
            elif failed or (personal_user_id and notifications.get("user_sent") is False):
                items.append("⚠️ Повідомлення не доставлено (юзер не писав боту або заблокував)")

        await reply(build_notice("✅", "Купон створено", items=items))

    elif sub == 'adduser' and len(args) >= 3:
        code = args[1].upper()
        try:
            new_user_id = int(args[2])
        except ValueError:
            await reply(build_error_notice("Telegram user ID має бути числом", title="Помилка"))
            return

        result = add_coupon_users(code, [new_user_id])
        if not result.get("ok"):
            await reply(build_error_notice(result.get('error', 'Помилка'), title="Помилка"))
            return

        coupon = result.get("coupon") or {}
        added = result.get("added_user_ids") or []
        if not added:
            await reply(build_notice(
                "ℹ️", "Доступ вже є",
                f"Юзер <code>{new_user_id}</code> вже має доступ до купона <code>{escape(code)}</code>",
            ))
            return

        notifications = await notify_coupon_created(
            coupon,
            source="coupon_cmd",
            notify_user_ids=added,
        )
        items = []
        if notifications.get("users_notified"):
            items.append(f"📨 Повідомлення надіслано юзеру <code>{new_user_id}</code>")
        elif notifications.get("users_failed"):
            items.append("⚠️ ID додано, але повідомлення не доставлено")
        await reply(build_notice(
            "✅", "Користувача додано",
            f"<code>{new_user_id}</code> → купон <code>{escape(code)}</code>",
            items=items or None,
        ))

    elif sub == 'list':
        rows = await run_db(list_coupons)
        if not rows:
            await reply(build_notice("🎟️", "Купонів ще немає", "Створи перший: <code>/coupon add КОД percent 10</code>"))
            return
        await reply(build_coupon_list(rows))

    elif sub in ('disable', 'enable') and len(args) >= 2:
        code = args[1].upper()
        result = set_coupon_active(code, sub == 'enable')
        if not result.get("ok"):
            await reply(build_error_notice(result.get('error', 'Помилка'), title="Помилка"))
            return
        await reply(build_notice(
            "✅" if sub == 'enable' else "🚫",
            "Купон увімкнено" if sub == 'enable' else "Купон вимкнено",
            f"<code>{escape(code)}</code>",
        ))

    elif sub == 'delete' and len(args) >= 2:
        code = args[1].upper()
        result = delete_coupon(code)
        if not result.get("ok"):
            await reply(build_error_notice(result.get('error', 'Помилка'), title="Помилка"))
            return
        await reply(build_notice("🗑️", "Купон видалено", f"<code>{escape(code)}</code>"))

    else:
        await reply(build_error_notice("Невірний формат команди", "Довідка: /coupon", title="Помилка"))

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
        if not is_gift_name(i.get("product_name"))
    ]


def _discount_split(order: dict) -> tuple[int, int]:
    """(знижка купона, акційна знижка) для замовлення з БД.

    Замовлення, створені до появи сумування, мають лише discount_amount —
    там знижка цілком належить купону, якщо він був, інакше акції.
    """
    total = int(order.get("discount_amount") or 0)
    promotion = int(order.get("promotion_discount") or 0)
    if promotion > 0:
        return max(0, total - promotion), min(promotion, total)
    if order.get("coupon_code"):
        return total, 0
    return 0, total


def _client_block_from_db(order: dict, db_items: list[dict]) -> dict:
    coupon_code = order.get("coupon_code")
    coupon_discount, promotion_discount = _discount_split(order)
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
            if not is_gift_name(i.get("product_name"))
        ],
        "total_price": int(order.get("total_price") or 0),
        "coupon_code": coupon_code,
        "coupon_discount": coupon_discount,
        "promotion_discount": promotion_discount,
        "gift": order.get("gift_product_name"),
        "comment": order.get("comment"),
        "price_pending": bool(order.get("price_pending")),
    }


async def _refresh_client_confirmation_after_pricing(uid_int: int, order_id: int) -> int:
    """Видаляє старе підтвердження клієнту та надсилає оновлене з актуальною ціною."""
    bot = app_state.bot_app.bot
    existing = confirmation_messages.get(uid_int)

    order_ids = [order_id]
    if existing and len(existing.get("orders", [])) > 1:
        admin_batch = admin_channel_messages.get(uid_int)
        batch_ids = (admin_batch or {}).get("order_ids")
        if batch_ids and order_id in batch_ids:
            order_ids = list(batch_ids)

    orders_batch: list[dict] = []
    for oid in order_ids:
        o, db_items = await run_db(get_order_with_items, oid)
        if not o:
            continue
        if o.get("status") not in ("new", "draft"):
            continue
        orders_batch.append(_client_block_from_db(o, db_items))

    if not orders_batch:
        o, db_items = await run_db(get_order_with_items, order_id)
        if not o:
            raise ValueError("Замовлення не знайдено")
        orders_batch = [_client_block_from_db(o, db_items)]

    confirm_html = build_client_order_confirmation(orders_batch)

    if existing:
        try:
            await bot.delete_message(chat_id=uid_int, message_id=existing["message_id"])
        except Exception as e:
            logger.warning(
                "Не вдалось видалити старе підтвердження %s для %s: %s",
                existing["message_id"], uid_int, e,
            )

    msg_id = await send_rich_message(bot, uid_int, confirm_html)
    confirmation_messages[uid_int] = {
        "message_id": msg_id,
        "orders": orders_batch,
        "html": confirm_html,
        "time": datetime.now(),
        "format": "rich",
    }
    return msg_id


def _make_contact_button(user_id, username: str, first_name: str) -> InlineKeyboardButton | None:
    target = _contact_button_target(user_id, username)
    if not target:
        return None
    label = f"💬 Написати {first_name or 'клієнт'}"
    kind, value = target
    if kind == "url":
        return InlineKeyboardButton(label, url=value)
    return InlineKeyboardButton(label, callback_data=f"contact_{value}")


def _build_single_order_channel_markup(
    order_id: int,
    price_pending: bool,
    user_id,
    username: str,
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
    write_btn = _make_contact_button(user_id, username, first_name)
    if write_btn:
        channel_rows.append([write_btn])
    return InlineKeyboardMarkup(channel_rows)


def _build_order_status_channel_markup(
    order_id: int,
    status: str,
    *,
    price_pending: bool,
    user_id,
    username: str,
    first_name: str,
) -> InlineKeyboardMarkup | None:
    write_button = _make_contact_button(user_id, username, first_name or "клієнт")

    if status == "confirmed" or status in FULFILMENT_STATUS_LABELS:
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
    return _build_single_order_channel_markup(order_id, price_pending, user_id, username, first_name)


FULFILMENT_STATUS_LABELS = {
    "printing": "🖨️ Друкується",
    "ready": "📦 Готове до відправки",
    "shipped": "🚚 Відправлено",
    "delivered": "🎉 Отримано",
}


async def _sync_order_status_to_telegram(order_id: int, action: str) -> None:
    """Синхронізує статус замовлення з повідомленням у Telegram-каналі (як order_action)."""
    status_map = {
        "confirm": "confirmed", "draft": "draft", "cancel": "cancelled",
        "printing": "printing", "ready": "ready", "shipped": "shipped", "delivered": "delivered",
    }
    label_map = {
        "confirm": "✅ Підтверджено",
        "draft": "❓ Під питанням",
        "cancel": "❌ Відмінено",
        "printing": "🖨️ Друкується",
        "ready": "📦 Готове до відправки",
        "shipped": "🚚 Відправлено",
        "delivered": "🎉 Отримано",
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
    first_name = order.get("first_name") or ""

    if len(batch_order_ids) > 1 and channel_message_id:
        batch_html, batch_markup = _build_admin_batch(
            batch_order_ids,
            order.get("username") or "невідомо",
            order.get("user_id"),
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
        if not is_gift_name(i.get("product_name"))
    ]
    admin_html = build_admin_order_notification(
        order_id=int(order["id"]),
        username=order.get("username") or "невідомо",
        user_id=order.get("user_id"),
        first_name=order.get("first_name"),
        items=admin_items,
        total_price=int(order.get("total_price") or 0),
        coupon_code=order.get("coupon_code"),
        discount_amount=int(order.get("discount_amount") or 0),
        coupon_discount=_discount_split(order)[0] or None,
        promotion_discount=_discount_split(order)[1] or None,
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
        user_id=order.get("user_id"),
        username=order.get("username") or "невідомо",
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
    elif status in FULFILMENT_STATUS_LABELS:
        sl = FULFILMENT_STATUS_LABELS[status]
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
        if not is_gift_name(i.get("product_name"))
    ]
    gift_name = order.get("gift_product_name")
    return {
        "order_id": order_id,
        "items": admin_items,
        "total_price": int(order.get("total_price") or 0),
        "discount_amount": int(order.get("discount_amount") or 0),
        "promotion_discount": _discount_split(order)[1],
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
    user_id,
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

    batch_html = build_admin_orders_batch(
        username, sections, user_id=user_id, first_name=first_name,
    )

    rows = []
    for s in sections:
        if s.get("status") in ("confirmed", "cancelled", *FULFILMENT_STATUS_LABELS):
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
    write_btn = _make_contact_button(user_id, username, first_name)
    if write_btn:
        rows.append([write_btn])
    markup = InlineKeyboardMarkup(rows) if rows else None
    return batch_html, markup


ADMIN_EDIT_DEBOUNCE_SEC = 2.0
_admin_edit_tasks: dict[int, asyncio.Task] = {}


def _build_single_admin_order_payload(
    order_id: int,
    username: str,
    user_id,
    first_name: str,
) -> tuple[str, InlineKeyboardMarkup] | None:
    order, db_items = get_order_with_items(order_id)
    if not order:
        return None
    gift_db = order.get("gift_product_name")
    gift_product_id = find_gift_product_id(gift_db)
    order_discount = int(order.get("discount_amount") or 0)
    coupon_code_db = order.get("coupon_code")
    coupon_discount_db, promotion_discount_db = _discount_split(order)
    price_pending_db = bool(order.get("price_pending"))
    owner_html = build_admin_order_notification(
        order_id=order_id,
        username=username,
        user_id=user_id,
        first_name=first_name,
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
        order_id, price_pending_db, user_id, username, first_name,
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
    first_name: str,
) -> None:
    async def _flush() -> None:
        payload = _build_single_admin_order_payload(
            order_id, username, user_id, first_name,
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
            tg_username=None,
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
            user_id,
            admin_batch.get("first_name") or "",
        )
        await _apply_admin_channel_edit(
            user_id,
            owner_html=batch_html,
            channel_markup=batch_markup,
            order_ids=order_ids,
            username=admin_batch.get("username") or "невідомо",
            tg_username=None,
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
                    uid_for_batch, order_id, username, first_name,
                )
            elif not admin_batch and uid_for_batch:
                payload = _build_single_admin_order_payload(
                    order_id, username, uid_for_batch, first_name,
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
            user_id=user_id,
            first_name=first_name,
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
        write_btn = _make_contact_button(user_id, username, first_name)
        channel_markup = InlineKeyboardMarkup(channel_rows + ([[write_btn]] if write_btn else []))

        owner_rows = []
        if admin_url:
            owner_rows.append([InlineKeyboardButton("💰 Встановити ціну", web_app=WebAppInfo(url=admin_url))])
        owner_rows.append(status_buttons)
        if write_btn:
            owner_rows.append([write_btn])
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
def _safe_int(value, default: int = 0) -> int:
    """int() від даних клієнта без 500 на сміттєвому значенні."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def handle_order(request):
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="Bad JSON", headers=cors_headers(request))

    auth, err = resolve_request_user(request, data)
    if err is not None:
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
    client_total = _safe_int(data.get("total_price"))
    comment = (data.get("comment") or "").strip()
    gift = data.get("gift")
    coupon_code = (data.get("coupon_code") or "").strip() or None

    if coupon_code:
        # Той самий ліміт, що й у /check_coupon: замовлення з купоном теж відповідає, чи код існує
        if not allow_coupon_check(_safe_int(user_id)):
            return web.json_response(
                {"ok": False, "error": "Забагато спроб з купоном. Спробуй за хвилину"},
                status=429,
                headers=cors_headers(request),
            )
        needs_sub, coupon_channel = await run_db(coupon_channel_gate, coupon_code)
        chat_id = resolve_coupon_channel(coupon_channel)
        if needs_sub and chat_id and not await is_channel_subscriber(user_id, chat_id):
            return web.json_response(
                {"ok": False, "error": channel_sub_required_message(coupon_channel)},
                status=400,
                headers=cors_headers(request),
            )

    prize = None
    raw_prize = data.get("prize") if isinstance(data.get("prize"), dict) else None
    if raw_prize and _safe_int(raw_prize.get("winner_id")) > 0:
        prize = await run_db(giveaways_svc.get_active_prize, _safe_int(user_id), _safe_int(raw_prize.get("winner_id")))
        if not prize:
            return web.json_response(
                {"ok": False, "error": "Приз уже оформлено або його строк минув — онови магазин", "prize_gone": True},
                status=400,
                headers=cors_headers(request),
            )
        prize["variant_id"] = str(raw_prize.get("variant_id") or "").strip().lower()[:16]

    ok, result = await run_db(validate_order_payload, items, coupon_code, user_id, client_total, prize)
    if not ok:
        return web.json_response({"ok": False, "error": result}, status=400, headers=cors_headers(request))
    prize_claim = None
    if prize:
        title = prize["title"] or f"№{prize['giveaway_id']}"
        if prize["prize_type"] == "amount":
            note = f"🎁 Сертифікат розіграшу «{title}»: −{result['prize_discount']} ₴."
        else:
            note = f"🎁 Приз розіграшу «{title}»."
        note += (" 🚚 Доставку всієї посилки оплачує магазин." if prize["shipping_payer"] == "shop"
                 else " Доставку оплачує покупець.")
        prize_claim = {"winner_id": prize["winner_id"], "variant_id": result.get("prize_variant_id") or "",
                       "note": note}

    ok, checkout = validate_checkout_fields(data)
    if not ok:
        return web.json_response({"ok": False, "error": checkout}, status=400, headers=cors_headers(request))

    # Акцію для статистики визначає сервер, а не клієнт: знижка > подарунок > безкоштовна доставка
    gift_rule = get_gift_rule() if checkout.get("gift_product_id") else None
    attributed_promo = (
        result.get("promotion_id")
        or (gift_rule["id"] if gift_rule else "")
        or result.get("free_ship_promotion_id")
        or ""
    )
    if attributed_promo:
        checkout["promotion_id"] = attributed_promo
    else:
        checkout.pop("promotion_id", None)

    items = result["items"]
    total_price = result["total_price"]
    coupon_discount = result.get("coupon_discount", 0)
    promotion_discount = result.get("promotion_discount", 0)
    total_discount = coupon_discount + promotion_discount + result.get("prize_discount", 0)
    coupon_code = coupon_code if coupon_discount else None
    price_pending = result.get("price_pending", 0)

    for item in items:
        if not (item.get("comment") or "").strip():
            item["comment"] = comment

    # Формуємо назву для збереження в БД (всі товари через кому)
    product_name = ', '.join(i.get('product_name', '—') for i in items)

    idempotency_key = (data.get("idempotency_key") or "").strip() or None
    uid = int(user_id or 0)
    if uid > 0 and not idempotency_key:
        return web.json_response(
            {"ok": False, "error": "idempotency_key обов'язковий"},
            status=400,
            headers=cors_headers(request),
        )

    if idempotency_key and uid > 0:
        cached = await run_db(get_idempotent_order, uid, idempotency_key)
        if cached:
            logger.info(
                "♻️ Idempotent duplicate: user=%s order=#%s",
                uid, cached["order_id"],
            )
            return _order_ok_response(
                request, int(cached["order_id"]), duplicate=True, is_new=bool(cached.get("is_new", 1))
            )

    async with _user_order_lock(uid):
        if idempotency_key and uid > 0:
            cached = await run_db(get_idempotent_order, uid, idempotency_key)
            if cached:
                return _order_ok_response(
                request, int(cached["order_id"]), duplicate=True, is_new=bool(cached.get("is_new", 1))
            )

        try:
            order_id, is_new = await run_db(
                save_order,
                user_id or 0, username, first_name, items, total_price, comment,
                gift, coupon_code, total_discount, price_pending, promotion_discount,
                checkout, prize=prize_claim,
            )
        except CouponConsumptionError as e:
            return web.json_response(
                {"ok": False, "error": e.message},
                status=400,
                headers=cors_headers(request),
            )
        except Exception:
            logger.exception("save_order failed for user=%s", uid)
            return web.json_response(
                {"ok": False, "error": "Не вдалось зберегти замовлення"},
                status=500,
                headers=cors_headers(request),
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
    return _order_ok_response(request, order_id, is_new=is_new)


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
    except Exception:
        logger.exception("static file %s failed", file_path)
        return web.Response(status=500, text="Internal error", headers=cors_headers(request))

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
def _is_admin_viewer(request: web.Request) -> bool:
    """Чи запит від адміна (без 403 для звичайних покупців)."""
    init_data = request.headers.get("X-Telegram-Init-Data") or ""
    auth = validate_telegram_init_data(init_data) if init_data else None
    return is_admin_authorized(request, auth)


def _public_product(product: dict) -> dict:
    """STL-посилання — внутрішня інформація, покупцям не віддаємо."""
    return {k: v for k, v in product.items() if k != "stlLink"}


async def handle_get_products(request: web.Request):
    """Отримати список всіх товарів (з рейтингом і «продано» — REDESIGN.md, розд. 5.3)"""
    products = get_all_products()
    if not _is_admin_viewer(request):
        products = [_public_product(p) for p in products]
    products = await run_db(with_stats, products)
    return web.json_response(products, headers=cors_headers(request))


async def handle_get_categories(request: web.Request):
    """Отримати список категорій"""
    include_inactive = request.query.get("includeInactive", "false").lower() in ("1", "true", "yes")
    if include_inactive:
        auth, err = resolve_request_user(request, {})
        if err is not None:
            return err
        denied = require_editor(request, auth)
        if denied is not None:
            return denied
    categories = get_all_categories(active_only=not include_inactive)
    return web.json_response(categories, headers=cors_headers(request))


async def handle_get_filaments(request: web.Request):
    """Отримати список філаментів."""
    reload_filaments_cache()
    filaments = [{**f, "glow": is_glow_filament(f)} for f in FILAMENTS_CACHE]
    if _is_admin_viewer(request):
        # Для адмінки: у скількох товарах колір пропонується («у N товарах»)
        usage = await run_db(filament_usage_counts)
        filaments = [{**f, "used_by": usage.get(str(f.get("id")), 0)} for f in filaments]
    return web.json_response(filaments, headers=cors_headers(request))


async def handle_get_product(request: web.Request):
    """Отримати товар за ID"""
    product_id = _product_id_param(request)
    is_admin = _is_admin_viewer(request)
    # Адмін бачить і приховані та архівні товари (редактор); покупцям — лише активні.
    product = get_admin_product(product_id) if is_admin else get_product_by_id(product_id)
    if not product:
        return web.json_response({"error": "Не знайдено"}, status=404, headers=cors_headers(request))
    if not is_admin:
        product = _public_product(product)
    product = (await run_db(with_stats, [product]))[0]
    return web.json_response(product, headers=cors_headers(request))


def _product_id_param(request: web.Request) -> int:
    try:
        return int(request.match_info.get("id", 0))
    except (TypeError, ValueError):
        return 0


async def handle_get_product_reviews(request: web.Request):
    """Схвалені відгуки товару, нові першими (картка товару, блок «Відгуки»)."""
    product_id = _product_id_param(request)
    if not product_id:
        return web.json_response({"error": "Не знайдено"}, status=404, headers=cors_headers(request))
    data = await run_db(
        product_reviews, product_id, _query_int(request, "limit", 10), _query_int(request, "offset", 0)
    )
    return web.json_response(data, headers=cors_headers(request))


async def handle_get_product_related(request: web.Request):
    """«З цим купують»: товари, що найчастіше були в одному замовленні з цим."""
    product_id = _product_id_param(request)
    if not product_id:
        return web.json_response({"error": "Не знайдено"}, status=404, headers=cors_headers(request))
    ids = await run_db(bought_together, product_id, _query_int(request, "limit", 8))
    return web.json_response({"product_ids": ids}, headers=cors_headers(request))


async def handle_update_filament(request: web.Request):
    """Оновити існуючий філамент."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    try:
        filament_id = request.match_info.get('id', '')
        data = await request.json()
        result = update_filament(filament_id, data)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=cors_headers(request))


async def handle_create_filament(request: web.Request):
    """Додати колір філаменту (ID — з назви, якщо не передано)."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Очікується JSON-обʼєкт")
        result = add_filament(data)
        status = 201 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=cors_headers(request))


async def handle_delete_filament(request: web.Request):
    """Видалити колір. Якщо він пропонується в товарах — 409 з used_by; ?force=1 видаляє попри це."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    filament_id = request.match_info.get('id', '')
    force = request.query.get("force", "").lower() in ("1", "true", "yes")
    result = await run_db(delete_filament, filament_id, force)
    if result.get("ok"):
        status = 200
    elif "used_by" in result:
        status = 409
    elif result.get("error") == "Філамент не знайдено":
        status = 404
    else:
        status = 400
    return web.json_response(result, status=status, headers=cors_headers(request))


async def handle_reorder_categories(request: web.Request):
    """Порядок категорій одним запитом: {"ids": ["toy", "key", …]}."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    try:
        data = await request.json()
        ids = data.get("ids") if isinstance(data, dict) else None
        result = await run_db(reorder_categories, ids)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=cors_headers(request))


async def handle_create_category(request: web.Request):
    """Створити нову категорію"""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
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
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
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
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
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
    if err is not None:
        return err
    # Локально без валідації дозволяємо без перевірки OWNER_ID
    denied = require_admin(request, auth)
    if denied is not None:
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
    if err is not None:
        return err
    # Локально без валідації дозволяємо без перевірки OWNER_ID
    denied = require_admin(request, auth)
    if denied is not None:
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
    if err is not None:
        return err
    # Локально без валідації дозволяємо без перевірки OWNER_ID
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        product_id = int(request.match_info.get('id', 0))
        result = delete_product(product_id)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=cors_headers(request))


async def _admin_guard(request: web.Request):
    """None, якщо запит від адміна; інакше готова відповідь 401/403."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    return require_admin(request, auth)


async def _editor_guard(request: web.Request):
    """None, якщо запит від адміна або редактора (ADMIN_EDITOR_IDS)."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    return require_editor(request, auth)


async def handle_admin_whoami(request: web.Request):
    """Роль користувача в адмінці: admin (повний доступ) або editor (лише пропозиції ШІ)."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    if is_admin_authorized(request, auth):
        role = "admin"
    elif is_editor_user(auth):
        role = "editor"
    else:
        return web.json_response({"error": "Forbidden"}, status=403, headers=cors_headers(request))
    return web.json_response({"ok": True, "role": role}, headers=cors_headers(request))


async def _json_body(request: web.Request):
    """Тіло запиту як dict або (None, відповідь 400)."""
    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        return None, web.json_response({"ok": False, "error": "Невірний запит"}, status=400, headers=cors_headers(request))
    return data, None


def _result_response(request: web.Request, result: dict, ok_status: int = 200):
    return web.json_response(result, status=ok_status if result.get("ok") else 400, headers=cors_headers(request))


async def handle_get_admin_products(request: web.Request):
    """Усі товари всіх статусів зі статистикою (перегляди, кошик, продажі) для таблиці в адмінці."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    products = await run_db(lambda: admin_products_with_stats(get_admin_products()))
    return web.json_response(products, headers=cors_headers(request))


async def handle_set_product_status(request: web.Request):
    """Статус товару: active / hidden / archive."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    result = await run_db(set_product_status, _product_id_param(request), str(data.get("status") or ""))
    return _result_response(request, result)


async def handle_patch_product(request: web.Request):
    """Швидке оновлення: ціна, стара ціна, hot / pinned / gift."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    result = await run_db(patch_product, _product_id_param(request), data)
    return _result_response(request, result)


async def handle_bulk_products(request: web.Request):
    """Масові дії: {ids, action: discount|clear_discount|category|status, value}."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    result = await run_db(bulk_update_products, data.get("ids"), str(data.get("action") or ""), data.get("value"))
    return _result_response(request, result)


async def handle_duplicate_product(request: web.Request):
    """Копія товару (прихована)."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    result = await run_db(duplicate_product, _product_id_param(request))
    return _result_response(request, result, 201)


def _include_test(request: web.Request) -> bool:
    """Тестові замовлення в адмінці приховані, доки не передано include_test=1 (або test=only)."""
    return request.query.get("include_test", "").lower() in ("1", "true", "yes") or _only_test(request)


def _only_test(request: web.Request) -> bool:
    return request.query.get("test", "").lower() == "only"


async def handle_get_orders(request: web.Request):
    """Список замовлень для адмін-панелі."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    pending_only = request.query.get("pending_price", "false").lower() in ("1", "true", "yes")
    try:
        limit = min(200, max(1, int(request.query.get("limit", "50") or 50)))
    except ValueError:
        limit = 50
    statuses = expand_status_filter(request.query.get("status"))
    orders = await run_db(
        list_orders,
        pending_price_only=pending_only,
        limit=limit,
        statuses=statuses or None,
        include_test=_include_test(request),
        only_test=_only_test(request),
    )
    return web.json_response(orders, headers=cors_headers(request))


async def handle_get_print_queue(request: web.Request):
    """Черга друку: позиції confirmed/printing, згруповані за кольором."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    return web.json_response(await run_db(get_print_queue), headers=cors_headers(request))


async def handle_set_item_printed(request: web.Request):
    """Відмітка «надруковано»: перша -> printing, усі -> ready (клієнт отримує повідомлення)."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        item_id = int(request.match_info.get("id", 0))
        data = await request.json()
        printed = bool(data.get("printed"))
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "Невірний запит"}, status=400, headers=cors_headers(request))

    state = await run_db(set_item_printed, item_id, printed)
    if not state:
        return web.json_response(
            {"ok": False, "error": "Позицію не знайдено"}, status=404, headers=cors_headers(request)
        )
    result = {"ok": True, **state}
    order_id = state["order_id"]
    # Статус перечитуємо: за цей час його могла змінити інша відмітка чи адмін.
    order, _ = await run_db(get_order_with_items, order_id)
    current_status = (order or {}).get("status") or state["status"]
    target = next_status_after_print(current_status, state["total"], state["printed"])
    if target and is_valid_transition(current_status, target):
        await _sync_order_status_to_telegram(order_id, target)
        result["status"] = target
        result["client_notified"] = await _notify_client_status(order or {"id": order_id}, target)
    return web.json_response(result, headers=cors_headers(request))


async def handle_create_manual_order(request: web.Request):
    """Замовлення вручну (Instagram, OLX, особисто): одразу підтверджене, без повідомлення клієнту."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Невірний запит"}, status=400, headers=cors_headers(request))
    ok, parsed = parse_manual_order(data if isinstance(data, dict) else {})
    if not ok:
        return web.json_response({"ok": False, "error": parsed}, status=400, headers=cors_headers(request))

    def _create() -> int:
        order_id, _ = save_order(
            parsed["user_id"], parsed["username"], parsed["first_name"], parsed["items"],
            parsed["total_price"], parsed["comment"], extra=parsed["extra"], merge=False,
        )
        if parsed["status"] != "new":
            update_order_status(order_id, parsed["status"])
        return order_id

    order_id = await run_db(_create)
    return web.json_response({"ok": True, "id": order_id}, headers=cors_headers(request))


async def handle_send_order_message(request: web.Request):
    """Надіслати клієнту в бот текст (шаблон з адмінки, відредагований адміном)."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        order_id = int(request.match_info.get("id", 0))
        data = await request.json()
        text = str(data.get("text") or "").strip()
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "Невірний запит"}, status=400, headers=cors_headers(request))
    if not text or len(text) > 2000:
        return web.json_response(
            {"ok": False, "error": "Текст має бути від 1 до 2000 символів"}, status=400, headers=cors_headers(request)
        )

    order, _ = await run_db(get_order_with_items, order_id)
    if not order:
        return web.json_response(
            {"ok": False, "error": "Замовлення не знайдено"}, status=404, headers=cors_headers(request)
        )
    user_id = int(order.get("user_id") or 0)
    if user_id <= 0:
        return web.json_response(
            {"ok": False, "error": "У цього замовлення немає Telegram-клієнта — надішліть повідомлення вручну"},
            status=409,
            headers=cors_headers(request),
        )
    paragraphs = build_manual_client_message(text)
    try:
        await send_rich_message(bot_app.bot, user_id, paragraphs)
    except Exception as e:
        logger.warning("Не вдалось надіслати повідомлення клієнту %s (замовлення #%s): %s", user_id, order_id, e)
        return web.json_response(
            {"ok": False, "error": "Клієнт не отримав повідомлення (можливо, заблокував бота)"},
            status=502,
            headers=cors_headers(request),
        )
    return web.json_response({"ok": True}, headers=cors_headers(request))


async def handle_export_orders_csv(request: web.Request):
    """CSV усіх (або відфільтрованих за status) замовлень."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    statuses = expand_status_filter(request.query.get("status"))
    rows = await run_db(list_orders, limit=5000, statuses=statuses or None)
    body = chr(0xFEFF) + orders_to_csv(rows)  # BOM, щоб Excel розпізнав UTF-8
    headers = {
        **cors_headers(request),
        "Content-Disposition": 'attachment; filename="orders.csv"',
    }
    return web.Response(body=body.encode("utf-8"), content_type="text/csv", charset="utf-8", headers=headers)


async def handle_get_overview(request: web.Request):
    """Огляд: KPI, виторг по днях, воронка, топ товарів, «потребує уваги»."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    try:
        days = int(request.query.get("days", "7") or 7)
    except ValueError:
        days = 7
    return web.json_response(await run_db(get_overview, days), headers=cors_headers(request))


async def handle_get_order_counts(request: web.Request):
    """Лічильники замовлень для вкладок і бічного меню адмінки."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    counts = await run_db(count_orders_by_status, _include_test(request), _only_test(request))
    try:  # лічильник «Відгуки» в меню; збій не повинен ламати лічильники замовлень
        counts["reviews_pending"] = await run_db(pending_review_count)
    except Exception:
        logger.warning("order counts: не вдалось порахувати відгуки на модерації", exc_info=True)
    return web.json_response(counts, headers=cors_headers(request))


async def handle_update_order(request: web.Request):
    """ТТН, внутрішня нотатка (клієнт її не бачить) і позначка «тестове» (не рахується в статистиці)."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        order_id = int(request.match_info.get("id", 0))
        data = await request.json()
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "Невірний запит"}, status=400, headers=cors_headers(request))
    fields = {k: data[k] for k in ("ttn", "internal_note", "is_test") if k in data}
    if not fields:
        return web.json_response(
            {"ok": False, "error": "Нічого оновлювати: очікуються ttn, internal_note або is_test"},
            status=400,
            headers=cors_headers(request),
        )
    if "ttn" in fields:
        fields["ttn"] = re.sub(r"\s+", "", str(fields["ttn"] or ""))
        if fields["ttn"] and not re.fullmatch(r"\d{14}", fields["ttn"]):
            return web.json_response(
                {"ok": False, "error": "ТТН Нової пошти — рівно 14 цифр"},
                status=400,
                headers=cors_headers(request),
            )
    if not await run_db(update_order_fields, order_id, fields):
        return web.json_response(
            {"ok": False, "error": "Замовлення не знайдено"}, status=404, headers=cors_headers(request)
        )
    return web.json_response({"ok": True}, headers=cors_headers(request))


async def _review_item_names(order_id: int) -> list[tuple[str, int]]:
    """Назви товарів замовлення — щоб у запиті відгуку було зрозуміло, про що мова (без номера)."""
    try:
        _, items = await run_db(get_order_with_items, int(order_id))
    except Exception:
        return []
    return [(str(i.get("product_name") or ""), int(i["product_id"])) for i in items if not i.get("is_gift") and int(i.get("product_id") or 0) > 0]


async def _send_test_review_request(order: dict) -> None:
    """Тестове замовлення: клієнт одразу отримує запит відгуку (без очікування доставки)."""
    user_id = int(order.get("user_id") or 0)
    if user_id <= 0:
        return
    try:
        await run_db(mark_review_requested, int(order["id"]))
        await send_rich_message(
            bot_app.bot,
            user_id,
            build_review_request(int(order["id"]), None, await _review_item_names(order["id"])),
            reply_markup=_review_request_markup(int(order["id"])),
        )
    except Exception as e:
        logger.warning("Запит відгуку для тестового #%s не доставлено: %s", order.get("id"), e)


async def _notify_client_status(order: dict, status: str, ttn: str | None = None) -> bool:
    """Повідомлення клієнту про новий статус. Помилка доставки не блокує зміну статусу."""
    user_id = int(order.get("user_id") or 0)
    # Старі замовлення (адмін закриває хвіст, відмічає друк заднім числом) — клієнта не турбуємо
    ordered_at = str(order.get("ordered_at") or "")[:10]
    try:
        if ordered_at and (datetime.now() - datetime.strptime(ordered_at, "%Y-%m-%d")).days > 7:
            logger.info("Замовлення #%s старше 7 днів — клієнта про статус %s не сповіщаємо", order.get("id"), status)
            return False
    except ValueError:
        pass
    if order.get("is_test"):
        # Тестове замовлення: статуси друку/доставки клієнту не шлемо, лише запит відгуку при підтвердженні
        if status == "confirmed":
            await _send_test_review_request(order)
            return True
        return False
    html_text = build_client_status_update(int(order["id"]), status, ttn)
    if user_id <= 0 or not html_text:
        return False
    try:
        markup = None
        if status == "delivered":
            try:
                if any(p["order_id"] == int(order["id"]) for p in await run_db(pending_reviews, user_id)):
                    markup = _review_request_markup(int(order["id"]))
            except Exception:
                logger.warning("Не вдалось перевірити відгуки для кнопки в #%s", order.get("id"), exc_info=True)
        message_id = await send_rich_message(bot_app.bot, user_id, html_text, reply_markup=markup)
        try:
            await run_db(record_status_message, int(order["id"]), user_id, message_id, status)
        except Exception as e:
            logger.warning("Не вдалось записати ID повідомлення про статус #%s: %s", order.get("id"), e)
        return True
    except Exception as e:
        logger.warning("Не вдалось сповістити клієнта %s про статус #%s: %s", user_id, order.get("id"), e)
        return False


async def handle_get_my_orders(request: web.Request):
    """Замовлення поточного покупця (за initData) для «Моїх замовлень»."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    try:
        user_id = int(auth.get("user_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    if user_id <= 0:
        return web.json_response({"orders": []}, headers=cors_headers(request))
    orders = await run_db(list_user_orders, user_id, _query_int(request, "limit", 30))
    return web.json_response({"orders": orders}, headers=cors_headers(request))


async def handle_get_pending_reviews(request: web.Request):
    """Що покупець ще може оцінити (товари доставлених замовлень без відгуку)."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    items = await run_db(pending_reviews, auth.get("user_id") or 0)
    return web.json_response({"items": items, "bonus": bonus_config()}, headers=cors_headers(request))


def _share_photo_url(url: str) -> str:
    """JPEG-версія фото для картки в чаті; у GIF Cloudinary бере перший кадр."""
    if "res.cloudinary.com" in url and "/upload/" in url:
        return url.replace("/upload/", "/upload/f_jpg,w_800,c_limit/", 1)
    return url


async def handle_prepare_share(request: web.Request):
    """Готує повідомлення-картку товару (фото + кнопка) для Telegram.WebApp.shareMessage."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    try:
        user_id = int(auth.get("user_id") or 0)
        data = await request.json()
        product_id = int(data.get("product_id"))
        photo_index = max(0, int(data.get("photo_index") or 0))
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "bad_request"}, status=400, headers=cors_headers(request))
    product = get_product_by_id(product_id) if user_id > 0 else None
    photos = (product or {}).get("photos") or []
    if not product or not photos:
        return web.json_response({"ok": False, "error": "no_photo"}, status=404, headers=cors_headers(request))
    photo = _share_photo_url(photos[photo_index if photo_index < len(photos) else 0])
    link = f"https://t.me/{config.BOT_USERNAME}?startapp=product_{product_id}"
    if photo_index > 0:
        link += f"_p{photo_index}"
    result = {
        "type": "photo",
        "id": f"p{product_id}",
        "photo_url": photo,
        "thumbnail_url": photo,
        "caption": f"{product.get('name', '')} — Poselyanov 3D Print",
        "reply_markup": {"inline_keyboard": [[{"text": "🛍️ Відкрити в магазині", "url": link}]]},
    }
    payload = {
        "user_id": user_id,
        "result": result,
        "allow_user_chats": True,
        "allow_bot_chats": True,
        "allow_group_chats": True,
        "allow_channel_chats": True,
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/savePreparedInlineMessage", json=payload
            ) as resp:
                body = await resp.json(content_type=None)
        if not isinstance(body, dict) or not body.get("ok"):
            raise RuntimeError((body or {}).get("description") if isinstance(body, dict) else "bad_response")
        return web.json_response(
            {"ok": True, "id": body["result"]["id"]}, headers=cors_headers(request)
        )
    except Exception as e:
        logger.warning("savePreparedInlineMessage для товару %s не вдалось: %s", product_id, str(e).replace(BOT_TOKEN, "***"))
        return web.json_response({"ok": False, "error": "prepare_failed"}, status=502, headers=cors_headers(request))


async def handle_upload_review_photo(request: web.Request):
    """Фото до відгуку від покупця: лише зображення, з лімітами розміру й кількості."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    try:
        user_id = int(auth.get("user_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    if user_id <= 0:
        return web.json_response({"ok": False, "error": "invalid_init_data"}, status=403, headers=cors_headers(request))
    if not allow_review_upload(user_id):
        return web.json_response({"ok": False, "error": "Забагато фото, спробуй пізніше"}, status=429, headers=cors_headers(request))
    try:
        form = await request.post()
        file_field = form.get("file")
        if not file_field or not isinstance(file_field, web.FileField):
            return web.json_response({"ok": False, "error": "Файл не знайдено"}, status=400, headers=cors_headers(request))
        file_data = file_field.file.read()
        if len(file_data) > REVIEW_PHOTO_MAX_BYTES:
            return web.json_response(
                {"ok": False, "error": f"Фото завелике. Максимум {REVIEW_PHOTO_MAX_BYTES // 1024 // 1024}MB"},
                status=413,
                headers=cors_headers(request),
            )
        content_type = file_field.content_type or ""
        # Pillow розбирає файл покупця — у потоці, щоб не блокувати сервер
        if not content_type.startswith("image/") or await asyncio.to_thread(is_animated_image, file_data, content_type):
            return web.json_response({"ok": False, "error": "Потрібне звичайне фото"}, status=400, headers=cors_headers(request))
        content_type = await asyncio.to_thread(normalize_image_content_type, file_data, content_type)
        result = await upload_photo_to_cloudinary(file_data, f"review_{user_id}", content_type)
        if not result.get("ok"):
            return web.json_response({"ok": False, "error": "Не вдалось завантажити фото"}, status=400, headers=cors_headers(request))
        return web.json_response({"ok": True, "url": result["url"]}, headers=cors_headers(request))
    except Exception:
        logger.exception("review photo upload failed for user=%s", user_id)
        return web.json_response({"ok": False, "error": "Не вдалось завантажити фото"}, status=400, headers=cors_headers(request))


def _review_moderation_html(review: dict, product_name: str) -> str:
    return build_review_moderation(review, product_name, review.get("product_id"))


def _review_moderation_markup(review_id: int, status: str) -> InlineKeyboardMarkup:
    if status == "approved":
        row = [InlineKeyboardButton("🙈 Сховати", callback_data=f"rev_hide_{review_id}")]
    elif status == "hidden":
        row = [InlineKeyboardButton("✅ Опублікувати", callback_data=f"rev_pub_{review_id}")]
    else:
        row = [
            InlineKeyboardButton("✅ Опублікувати", callback_data=f"rev_pub_{review_id}"),
            InlineKeyboardButton("🙈 Сховати", callback_data=f"rev_hide_{review_id}"),
        ]
    return InlineKeyboardMarkup([row])


async def _award_review_bonus(review_id: int) -> dict | None:
    """Купон за опублікований відгук: видаємо (раз на замовлення) і дублюємо клієнту в бот."""
    try:
        bonus = await run_db(grant_review_bonus, review_id)
    except Exception:
        logger.exception("Бонус за відгук #%s: помилка видачі", review_id)
        return None
    if not bonus:
        return None
    try:
        markup = None
        if bonus.get("link"):
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("🎟 Застосувати купон", url=bonus["link"])]])
        await send_rich_message(
            bot_app.bot, bonus["user_id"],
            build_review_bonus(bonus["percent"], bonus["code"], bonus["expires_at"]),
            reply_markup=markup,
        )
    except Exception as e:
        logger.warning("Купон за відгук #%s не доставлено клієнту: %s", review_id, e)
    return bonus


async def handle_post_review(request: web.Request):
    """Відгук покупця: лише на товар із доставленого замовлення, один на пару (замовлення, товар)."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Bad JSON"}, status=400, headers=cors_headers(request))
    auth, err = resolve_request_user(request, data if isinstance(data, dict) else {})
    if err is not None:
        return err
    if not isinstance(data, dict):
        return web.json_response({"ok": False, "error": "Bad JSON"}, status=400, headers=cors_headers(request))
    try:
        result = await run_db(
            submit_review,
            auth.get("user_id") or 0,
            auth.get("first_name") or "",
            data.get("order_id"),
            data.get("product_id"),
            data.get("rating"),
            data.get("text"),
            data.get("photos"),
        )
    except ReviewError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))

    review = await run_db(get_review, result["id"])
    if review:
        product_name = next(
            (p.get("name") for p in PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE if p.get("id") == review["product_id"]),
            f"товар {review['product_id']}",
        )
        try:
            await send_rich_message(
                bot_app.bot,
                ORDERS_CHAT_ID,
                _review_moderation_html(review, product_name),
                reply_markup=_review_moderation_markup(review["id"], review["status"]),
            )
        except Exception as e:
            logger.warning("Не вдалось надіслати відгук #%s на модерацію: %s", result["id"], e)
    if result["status"] == "approved":
        bonus = await _award_review_bonus(result["id"])
        if bonus:
            result["bonus"] = {k: bonus[k] for k in ("code", "percent", "expires_at")}
    return web.json_response({"ok": True, **result}, headers=cors_headers(request))


def _catalog_product_map() -> dict[int, dict]:
    """{id: товар} для товарів усіх статусів (відгук може стосуватись прихованого чи архівного)."""
    from catalog_store import INACTIVE_CUSTOM_PRODUCTS_CACHE, INACTIVE_PRODUCTS_CACHE

    out = {}
    for p in PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE + INACTIVE_PRODUCTS_CACHE + INACTIVE_CUSTOM_PRODUCTS_CACHE:
        try:
            out[int(p.get("id"))] = p
        except (TypeError, ValueError):
            continue
    return out


def _review_review_id(request: web.Request) -> int:
    try:
        return int(request.match_info.get("id", 0))
    except (TypeError, ValueError):
        return 0


def _review_not_found(request: web.Request):
    return web.json_response({"ok": False, "error": "Відгук не знайдено"}, status=404, headers=cors_headers(request))


async def handle_get_admin_reviews(request: web.Request):
    """Відгуки для сторінки «Відгуки»: ?status=pending|approved|hidden|all&q=&rating=&limit=.
    Разом із лічильниками (вкладки, середній рейтинг, кількість на модерації)."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    status = request.query.get("status", "all")
    if status not in ("pending", "approved", "hidden", "all"):
        return web.json_response({"ok": False, "error": "Невірний статус"}, status=400, headers=cors_headers(request))
    products = _catalog_product_map()
    names = {pid: str(p.get("name") or "") for pid, p in products.items()}
    reviews = await run_db(
        admin_list_reviews,
        status,
        request.query.get("q", ""),
        request.query.get("rating"),
        _query_int(request, "limit", 100),
        names,
    )
    for item in reviews:
        p = products.get(item["product_id"])
        photos = p.get("photos") if p else None
        item["product_name"] = (p.get("name") if p else None) or f"Товар #{item['product_id']}"
        item["product_photo"] = photos[0] if isinstance(photos, list) and photos else ""
        item["product_emoji"] = (p.get("emoji") if p else "") or ""
    counts = await run_db(admin_review_counts)
    return web.json_response({"reviews": reviews, "counts": counts}, headers=cors_headers(request))


async def handle_set_admin_review_status(request: web.Request):
    """PUT /api/admin/reviews/{id}/status {status: approved|hidden|pending}."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    status = data.get("status")
    if status not in ("pending", "approved", "hidden"):
        return web.json_response({"ok": False, "error": "Невірний статус"}, status=400, headers=cors_headers(request))
    review_id = _review_review_id(request)
    if not await run_db(get_review, review_id):
        return _review_not_found(request)
    await run_db(set_review_status, review_id, status)
    if status == "approved":
        await _award_review_bonus(review_id)
    return web.json_response(
        {"ok": True, "counts": await run_db(admin_review_counts)}, headers=cors_headers(request)
    )


async def handle_bulk_admin_reviews(request: web.Request):
    """POST /api/admin/reviews/bulk {action: approve|hide|delete, ids: [..]} — масова модерація (до 500 за раз)."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    action = data.get("action")
    raw_ids = data.get("ids")
    if action not in ("approve", "hide", "delete") or not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 500:
        return web.json_response({"ok": False, "error": "Невірний запит"}, status=400, headers=cors_headers(request))
    ids: list[int] = []
    for raw in raw_ids:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in ids:
            ids.append(value)
    done = 0
    for review_id in ids:
        review = await run_db(get_review, review_id)
        if not review:
            continue
        if action == "delete":
            changed = await run_db(delete_review, review_id)
        else:
            status = "approved" if action == "approve" else "hidden"
            if review["status"] == status:
                continue
            changed = await run_db(set_review_status, review_id, status)
            if changed and status == "approved":
                await _award_review_bonus(review_id)
        if changed:
            done += 1
    return web.json_response(
        {"ok": True, "done": done, "counts": await run_db(admin_review_counts)}, headers=cors_headers(request)
    )


async def handle_set_admin_review_reply(request: web.Request):
    """PUT /api/admin/reviews/{id}/reply {reply}: ≤1000 символів, порожня відповідь прибирає її."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    try:
        found = await run_db(set_admin_reply, _review_review_id(request), data.get("reply"))
    except ReviewError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))
    if not found:
        return _review_not_found(request)
    return web.json_response({"ok": True}, headers=cors_headers(request))


async def handle_delete_admin_review(request: web.Request):
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    if not await run_db(delete_review, _review_review_id(request)):
        return _review_not_found(request)
    return web.json_response(
        {"ok": True, "counts": await run_db(admin_review_counts)}, headers=cors_headers(request)
    )


async def review_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопки «Опублікувати / Сховати» під відгуком у чаті замовлень."""
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        return
    _, action, raw_id = query.data.split("_", 2)
    review_id = int(raw_id)
    status = "approved" if action == "pub" else "hidden"
    if not await run_db(set_review_status, review_id, status):
        return
    if status == "approved":
        await _award_review_bonus(review_id)
    try:
        await query.edit_message_reply_markup(reply_markup=_review_moderation_markup(review_id, status))
    except Exception as e:
        logger.warning("review_action: не вдалось оновити кнопки #%s: %s", review_id, e)


def _review_request_markup(order_id: int) -> InlineKeyboardMarkup:
    """Оцінка одним дотиком: кнопка зірок відкриває форму відгуку з уже обраною оцінкою."""
    base = f"https://t.me/{config.BOT_USERNAME}?startapp=review_{int(order_id)}"
    stars = [InlineKeyboardButton(f"{n}⭐", url=f"{base}_{n}") for n in range(1, 6)]
    return InlineKeyboardMarkup([stars, [InlineKeyboardButton("✍️ Написати відгук", url=base)]])


async def _send_review_nudges(due: list[dict], mark, build) -> int:
    sent = 0
    for item in due:
        await run_db(mark, item["order_id"])  # позначаємо навіть при помилці, щоб не спамити
        try:
            await send_rich_message(
                bot_app.bot,
                item["user_id"],
                build(
                    item["order_id"],
                    None if item.get("is_test") else bonus_config(),
                    await _review_item_names(item["order_id"]),
                ),
                reply_markup=_review_request_markup(item["order_id"]),
            )
            sent += 1
        except Exception as e:
            logger.warning("Запит відгуку для #%s не доставлено: %s", item["order_id"], e)
    return sent


async def send_due_review_requests() -> int:
    """Запит відгуку через добу після `delivered`, далі — одне нагадування, якщо відгуку немає."""
    sent = await _send_review_nudges(
        await run_db(orders_due_review_request), mark_review_requested, build_review_request
    )
    sent += await _send_review_nudges(
        await run_db(orders_due_review_reminder), mark_review_reminded, build_review_reminder
    )
    return sent


async def _review_request_loop() -> None:
    while True:
        try:
            await send_due_review_requests()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("review request loop failed")
        await asyncio.sleep(REVIEW_REQUEST_POLL_SEC)


async def handle_get_order(request: web.Request):
    """Деталі замовлення з позиціями."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    order_id = int(request.match_info.get("id", 0))
    order, items = await run_db(get_order_with_items, order_id)
    if not order:
        return web.json_response({"error": "Не знайдено"}, status=404, headers=cors_headers(request))
    return web.json_response({"order": order, "items": items}, headers=cors_headers(request))


async def handle_update_order_pricing(request: web.Request):
    """Оновити ціни позицій замовлення."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
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

        if data.get("notify_client"):
            uid_int = int(user_id_from_result or 0)
            if uid_int <= 0:
                result["client_notified"] = False
                result["notify_error"] = "У замовлення немає Telegram ID клієнта"
                logger.warning(
                    "Не можу надіслати ціну клієнту для #%s: user_id відсутній", order_id
                )
            elif not app_state.bot_app:
                result["client_notified"] = False
                result["notify_error"] = "Бот не ініціалізовано"
                logger.error("bot_app не ініціалізовано — не можу надіслати ціну клієнту")
            else:
                try:
                    await _refresh_client_confirmation_after_pricing(uid_int, order_id)
                    result["client_notified"] = True
                    logger.info(
                        "✅ Оновлено підтвердження клієнту %s після ціни замовлення #%s",
                        uid_int, order_id,
                    )
                except Exception as e:
                    result["client_notified"] = False
                    result["notify_error"] = str(e)
                    logger.error(
                        "Помилка повідомлення клієнту %s про ціну #%s: %s",
                        uid_int, order_id, e,
                    )
                    if "blocked" in str(e).lower() or "Forbidden" in str(e):
                        await run_db(set_blocked, uid_int, True)

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
                                username=admin_batch.get("username") or upd_order.get("username") or "",
                                user_id=uid,
                                first_name=admin_batch.get("first_name") or upd_order.get("first_name"),
                                items=_admin_items_from_db(upd_items),
                                total_price=int(upd_order.get("total_price") or 0),
                                coupon_code=cpn,
                                discount_amount=disc,
                                coupon_discount=_discount_split(upd_order)[0] or None,
                                promotion_discount=_discount_split(upd_order)[1] or None,
                                gift=gift_name,
                                gift_product_id=find_gift_product_id(gift_name),
                                comment=upd_order.get("comment") or None,
                                price_pending=bool(upd_order.get("price_pending")),
                            )
                            new_markup = _build_single_order_channel_markup(
                                order_ids[0],
                                bool(upd_order.get("price_pending")),
                                uid,
                                admin_batch.get("username") or upd_order.get("username") or "невідомо",
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
                            uid,
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
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        order_id = int(request.match_info.get("id", 0))
        data = await request.json()
        status = (data.get("status") or "").strip().lower()
        action_map = {
            "confirmed": "confirm",
            "cancelled": "cancel",
            "draft": "draft",
            **{s: s for s in FULFILMENT_STATUS_LABELS},
        }
        if status not in action_map:
            return web.json_response(
                {
                    "ok": False,
                    "error": "Невідомий статус. Дозволено: confirmed, printing, ready, shipped, delivered, cancelled, draft",
                },
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

        old_status = order.get("status") or "new"
        if not is_valid_transition(old_status, status):
            return web.json_response(
                {"ok": False, "error": f"Перехід {old_status} → {status} недозволений"},
                status=409,
                headers=cors_headers(request),
            )

        ttn = re.sub(r"\s+", "", str(data.get("ttn") or order.get("ttn") or ""))
        if status == "shipped":
            if not ttn:
                return web.json_response(
                    {"ok": False, "error": "Для статусу shipped потрібна ТТН"},
                    status=400,
                    headers=cors_headers(request),
                )
            if not re.fullmatch(r"\d{14}", ttn):
                return web.json_response(
                    {"ok": False, "error": "ТТН Нової пошти — рівно 14 цифр"},
                    status=400,
                    headers=cors_headers(request),
                )
            await run_db(update_order_fields, order_id, {"ttn": ttn})

        await _sync_order_status_to_telegram(order_id, action_map[status])

        notified = await _notify_client_status(order, status, ttn)
        return web.json_response(
            {"ok": True, "status": status, "client_notified": notified},
            headers=cors_headers(request),
        )
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_delete_status_messages(request: web.Request):
    """DELETE /api/orders/{id}/status-messages — видалити в клієнта надіслані повідомлення про статус
    (?order_id=all не підтримується свідомо: {id} = 0 означає «усі ще не видалені»)."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    try:
        order_id = int(request.match_info.get("id", 0))
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "Невірний запит"}, status=400, headers=cors_headers(request))
    rows = await run_db(list_status_messages, order_id or None)
    deleted, failed = 0, 0
    for row in rows:
        try:
            await bot_app.bot.delete_message(chat_id=row["user_id"], message_id=row["message_id"])
            deleted += 1
        except Exception as e:
            # Повідомлення могло бути видалене раніше або старше 48 год — тоді просто знімаємо з обліку
            logger.info("delete_message %s/%s: %s", row["user_id"], row["message_id"], e)
            failed += 1
        await run_db(mark_status_message_deleted, row["id"])
    return web.json_response({"ok": True, "deleted": deleted, "failed": failed}, headers=cors_headers(request))


async def handle_delete_order(request: web.Request):
    """Видалити замовлення."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
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
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    return web.json_response(await run_db(list_coupons), headers=cors_headers(request))


async def handle_create_coupon(request: web.Request):
    """Створити купон."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        data = await request.json()
        result = await run_db(create_coupon, data)
        if not result.get("ok"):
            return web.json_response(result, status=400, headers=cors_headers(request))

        notifications = await notify_coupon_created(
            result.get("coupon") or {},
            source="admin_panel",
            notify_user_ids=result.get("added_user_ids"),
        )
        result["notifications"] = notifications
        return web.json_response(result, status=201, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_update_coupon(request: web.Request):
    """Оновити купон."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        code = request.match_info.get("code", "")
        data = await request.json()

        if "active" in data and len(data) == 1:
            result = await run_db(set_coupon_active, code, bool(data.get("active")))
        else:
            result = await run_db(update_coupon, code, data)

        if result.get("ok") and result.get("added_user_ids"):
            notifications = await notify_coupon_created(
                result.get("coupon") or {},
                source="admin_panel",
                notify_user_ids=result.get("added_user_ids"),
            )
            result["notifications"] = notifications

        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_add_coupon_users(request: web.Request):
    """Додати Telegram ID до whitelist купона."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        code = request.match_info.get("code", "")
        data = await request.json()
        user_ids = data.get("user_ids") or data.get("user_id")
        if user_ids is None:
            return web.json_response(
                {"ok": False, "error": "Потрібен user_ids або user_id"},
                status=400,
                headers=cors_headers(request),
            )

        result = await run_db(add_coupon_users, code, user_ids)
        if not result.get("ok"):
            return web.json_response(result, status=400, headers=cors_headers(request))

        if result.get("added_user_ids"):
            notifications = await notify_coupon_created(
                result.get("coupon") or {},
                source="admin_panel",
                notify_user_ids=result.get("added_user_ids"),
            )
            result["notifications"] = notifications

        return web.json_response(result, status=200, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_remove_coupon_user(request: web.Request):
    """Прибрати Telegram ID з whitelist купона."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        code = request.match_info.get("code", "")
        user_id = int(request.match_info.get("user_id", 0))
        result = await run_db(remove_coupon_user, code, user_id)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_delete_coupon(request: web.Request):
    """Видалити купон."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        code = request.match_info.get("code", "")
        result = await run_db(delete_coupon, code)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_get_public_promotions(request: web.Request):
    """Активні акції для вітрини (без авторизації)."""
    return web.json_response(await run_db(public_promotions), headers=cors_headers(request))


def _query_int(request: web.Request, name: str, default: int) -> int:
    try:
        return int(request.query.get(name, default))
    except (TypeError, ValueError):
        return default


async def handle_get_config(request: web.Request):
    """Публічна конфігурація магазину: доступні способи доставки."""
    return web.json_response(
        {
            "delivery_methods": delivery.available_methods(),
            "np_lookup": delivery.np_lookup_enabled(),
        },
        headers=cors_headers(request),
    )


async def _delivery_lookup(request: web.Request, fetch):
    """Спільна обгортка довідників: авторизація, ліміт, відмова API -> текстове поле у фронті."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    try:
        user_id = int(auth.get("user_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    if not allow_delivery_lookup(user_id):
        return web.json_response({"ok": False, "error": "rate_limited"}, status=429, headers=cors_headers(request))
    try:
        items = await fetch()
    except delivery.DeliveryUnavailable:
        return web.json_response(
            {"ok": False, "error": "unavailable", "items": []}, status=503, headers=cors_headers(request)
        )
    payload = items if isinstance(items, dict) else {"items": items}
    return web.json_response({"ok": True, **payload}, headers=cors_headers(request))


async def handle_np_cities(request: web.Request):
    """Пошук міста, смт чи села для Нової Пошти: ?q="""
    return await _delivery_lookup(request, lambda: delivery.np_cities(request.query.get("q", "")))


async def handle_np_warehouses(request: web.Request):
    """Відділення й поштомати НП: ?city=<ref>&kind=all|branch|postomat&q=<пошук>"""
    return await _delivery_lookup(
        request,
        lambda: delivery.np_warehouses(
            request.query.get("city", ""), request.query.get("kind", "all"), request.query.get("q", "")
        ),
    )


async def handle_get_hits(request: web.Request):
    """Топ продажів з реальних замовлень: хіти на головній, добірка «Хіти», сортування «Популярні»."""
    hits = await run_db(
        top_selling_products, _query_int(request, "days", 90), _query_int(request, "limit", 12)
    )
    return web.json_response({"hits": hits}, headers=cors_headers(request))


async def handle_get_collections(request: web.Request):
    """Активні добірки для головної («Стопка»)."""
    return web.json_response({"collections": await run_db(visible_collections)}, headers=cors_headers(request))


def _collection_response(request: web.Request, result: dict, ok_status: int = 200):
    status = ok_status if result.get("ok") else (404 if result.get("not_found") else 400)
    return web.json_response(result, status=status, headers=cors_headers(request))


async def handle_get_admin_collections(request: web.Request):
    """Усі добірки (і вимкнені) для адмінки: склад, режим, кількість товарів, що бачить покупець."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    return web.json_response({"collections": await run_db(list_admin_collections)}, headers=cors_headers(request))


async def handle_create_collection(request: web.Request):
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    return _collection_response(request, await run_db(create_collection, data), 201)


async def handle_update_collection(request: web.Request):
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    return _collection_response(request, await run_db(update_collection, request.match_info.get("id", ""), data))


async def handle_delete_collection(request: web.Request):
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    return _collection_response(request, await run_db(delete_collection, request.match_info.get("id", "")))


async def handle_reorder_collections(request: web.Request):
    """Порядок добірок: {"ids": ["new", "glow", …]}."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    return _collection_response(request, await run_db(reorder_collections, data.get("ids")))


async def handle_preview_collection(request: web.Request):
    """«Зараз підходить: N товарів» для форми добірки: {mode, rule | product_ids}."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    return _collection_response(request, await run_db(preview_collection, data))


async def handle_set_collection_products(request: web.Request):
    """Упорядкований склад ручної добірки: {"product_ids": [3, 1, 2]}."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    return _collection_response(
        request, await run_db(set_collection_products, request.match_info.get("id", ""), data.get("product_ids"))
    )


async def handle_get_product_collections(request: web.Request):
    """ID ручних добірок товару (секція «Добірки» в редакторі)."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    ids = await run_db(product_collection_ids, _product_id_param(request))
    return web.json_response({"ok": True, "collections": ids}, headers=cors_headers(request))


async def handle_set_product_collections(request: web.Request):
    """Ручні добірки товару: {"collections": ["gift-ideas", …]}."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    return _collection_response(
        request, await run_db(set_product_collections, _product_id_param(request), data.get("collections"))
    )


async def handle_get_popular_searches(request: web.Request):
    """«Шукають найчастіше» на екрані пошуку."""
    queries = await run_db(
        popular_searches, _query_int(request, "days", 30), _query_int(request, "limit", 5)
    )
    return web.json_response({"queries": queries}, headers=cors_headers(request))


async def handle_post_event(request: web.Request):
    """Подія аналітики з вітрини: `search` і події воронки."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Bad JSON"}, status=400, headers=cors_headers(request))
    if not isinstance(data, dict) or data.get("type") not in EVENT_TYPES:
        return web.json_response({"ok": False, "error": "unknown_event"}, status=400, headers=cors_headers(request))

    auth, err = resolve_request_user(request, data)
    if err is not None:
        return err
    try:
        user_id = int(auth.get("user_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    if not user_id:
        return web.json_response({"ok": True, "recorded": False}, headers=cors_headers(request))
    if not allow_event(user_id):
        return web.json_response({"ok": False, "error": "rate_limited"}, status=429, headers=cors_headers(request))

    if data["type"] == "broadcast_open":
        try:
            bid = int(data.get("broadcast_id") or 0)
        except (TypeError, ValueError):
            bid = 0
        recorded = await run_db(broadcasts_svc.record_click, user_id, bid)
        return web.json_response({"ok": True, "recorded": recorded}, headers=cors_headers(request))

    if data["type"] == "channel_open":
        try:
            post_id = int(data.get("post_id") or 0)
        except (TypeError, ValueError):
            post_id = 0
        recorded = await run_db(channel_posts_svc.record_click, user_id, post_id)
        return web.json_response({"ok": True, "recorded": recorded}, headers=cors_headers(request))

    if data["type"] in FUNNEL_EVENT_TYPES:
        try:
            product_id = int(data.get("product_id") or 0) or None
        except (TypeError, ValueError):
            product_id = None
        recorded = await run_db(record_funnel_event, user_id, data["type"], product_id)
        return web.json_response({"ok": True, "recorded": recorded}, headers=cors_headers(request))

    try:
        results = int(data.get("results") or 0)
    except (TypeError, ValueError):
        results = 0
    recorded = await run_db(record_search, user_id, data.get("query"), results)
    return web.json_response({"ok": True, "recorded": recorded}, headers=cors_headers(request))


async def handle_get_promotions(request: web.Request):
    """Повний список акцій для адмін-панелі."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    return web.json_response(await run_db(list_promotions_admin), headers=cors_headers(request))


async def handle_get_users(request: web.Request):
    """Список користувачів бота з лічильниками запусків для адмін-панелі."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    q = request.query
    try:
        limit = int(q.get("limit", "50") or 50)
        offset = int(q.get("offset", "0") or 0)
    except ValueError:
        return web.json_response(
            {"error": "limit/offset мають бути числами"}, status=400, headers=cors_headers(request)
        )

    result = await run_db(
        list_users,
        search=(q.get("search") or "")[:64],
        sort=q.get("sort") or "starts",
        order=q.get("order") or "desc",
        limit=limit,
        offset=offset,
        blocked=q.get("blocked") or "all",
        segment=q.get("segment") or "all",
    )
    if q.get("counts") in ("1", "true"):
        result["segment_counts"] = await run_db(
            segment_counts, (q.get("search") or "")[:64], q.get("blocked") or "all"
        )
    return web.json_response(result, headers=cors_headers(request))


async def handle_export_users_csv(request: web.Request):
    """CSV клієнтів (з урахуванням segment/search/blocked)."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    q = request.query
    rows = await run_db(
        export_users, (q.get("search") or "")[:64], q.get("blocked") or "all", q.get("segment") or "all"
    )
    body = chr(0xFEFF) + users_to_csv(rows)
    headers = {**cors_headers(request), "Content-Disposition": 'attachment; filename="clients.csv"'}
    return web.Response(body=body.encode("utf-8"), content_type="text/csv", charset="utf-8", headers=headers)


async def handle_create_personal_coupon(request: web.Request):
    """Персональний купон -10% для клієнта (одноразовий, лише йому, 30 днів)."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    try:
        user_id = int(request.match_info.get("id", 0))
    except (TypeError, ValueError):
        user_id = 0
    if user_id <= 0 or not await run_db(get_user, user_id):
        return web.json_response({"ok": False, "error": "Клієнта не знайдено"}, status=404, headers=cors_headers(request))
    result = await run_db(create_personal_coupon, user_id)
    return web.json_response(result, status=200 if result.get("ok") else 400, headers=cors_headers(request))


async def handle_send_user_message(request: web.Request):
    """Надіслати клієнту в бот текст (наприклад, код купона)."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    try:
        user_id = int(request.match_info.get("id", 0))
        data = await request.json()
        text = str(data.get("text") or "").strip()
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "Невірний запит"}, status=400, headers=cors_headers(request))
    if not text or len(text) > 2000 or user_id <= 0:
        return web.json_response(
            {"ok": False, "error": "Текст має бути від 1 до 2000 символів"}, status=400, headers=cors_headers(request)
        )
    if not await run_db(get_user, user_id):
        return web.json_response({"ok": False, "error": "Клієнта не знайдено"}, status=404, headers=cors_headers(request))
    paragraphs = build_manual_client_message(text)
    try:
        if bot_app is None:
            raise RuntimeError("bot is not running")
        await send_rich_message(bot_app.bot, user_id, paragraphs)
    except Exception as e:
        logger.warning("Не вдалось надіслати повідомлення клієнту %s: %s", user_id, e)
        if broadcasts_svc.is_undeliverable_error(e):
            await run_db(set_blocked, user_id, True)
        return web.json_response(
            {"ok": False, "error": "Клієнт не отримав повідомлення (можливо, заблокував бота)"},
            status=502, headers=cors_headers(request),
        )
    return web.json_response({"ok": True}, headers=cors_headers(request))


# ─── РОЗСИЛКИ ────────────────────────────────────────────────

def _make_broadcast_sender():
    """Функція відправки одному отримувачу. Єдине місце, що реально б'є в Telegram -
    тести й перегляд підміняють її (monkeypatch bot._make_broadcast_sender)."""
    from rich_messages import _telegram_retry

    async def send_one(user_id: int, b: dict):
        markup = None
        if b.get("button_label") and b.get("button_target"):
            url = broadcasts_svc.button_url(b["id"], b["button_target"])
            if url:
                markup = InlineKeyboardMarkup([[InlineKeyboardButton(b["button_label"], url=url)]])
        tg_bot = bot_app.bot
        if b.get("photo"):
            await _telegram_retry(
                lambda: tg_bot.send_photo(chat_id=user_id, photo=b["photo"], caption=b["text"], reply_markup=markup),
                label="broadcast photo",
            )
        else:
            await _telegram_retry(
                lambda: tg_bot.send_message(chat_id=user_id, text=b["text"], reply_markup=markup),
                label="broadcast message",
            )

    return send_one


_broadcast_tasks: set = set()


def _spawn_broadcast(bid: int) -> None:
    task = asyncio.create_task(broadcasts_svc.send_broadcast(bid, _make_broadcast_sender()))
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)


async def _broadcast_scheduler_loop() -> None:
    """Раз на 30 с відправляє розсилки, час яких настав (захист від дубля - claim у send_broadcast)."""
    while True:
        try:
            await broadcasts_svc.run_due_broadcasts(_make_broadcast_sender())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("broadcast scheduler failed")
        await asyncio.sleep(30)


async def _admin_guard(request: web.Request):
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    return require_admin(request, auth)


async def handle_get_broadcasts(request: web.Request):
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    items = await run_db(broadcasts_svc.list_broadcasts)
    return web.json_response({"broadcasts": items}, headers=cors_headers(request))


async def handle_preview_broadcast(request: web.Request):
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    try:
        data = await request.json()
    except Exception:
        data = {}
    result = await run_db(broadcasts_svc.preview_recipients, (data or {}).get("segment"))
    return web.json_response(result, headers=cors_headers(request))


async def handle_create_broadcast(request: web.Request):
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Bad JSON"}, status=400, headers=cors_headers(request))
    result = await run_db(broadcasts_svc.create_broadcast, data if isinstance(data, dict) else {})
    if not result.get("ok"):
        return web.json_response(result, status=400, headers=cors_headers(request))
    if result.pop("send_now", False):
        _spawn_broadcast(result["id"])
    return web.json_response(result, headers=cors_headers(request))


async def handle_delete_broadcast(request: web.Request):
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    try:
        bid = int(request.match_info.get("id", 0))
    except (TypeError, ValueError):
        bid = 0
    outcome = await run_db(broadcasts_svc.delete_broadcast, bid)
    if outcome == "deleted":
        return web.json_response({"ok": True}, headers=cors_headers(request))
    if outcome == "locked":
        return web.json_response(
            {"ok": False, "error": "Видалити можна лише відкладену розсилку або чернетку"},
            status=409, headers=cors_headers(request),
        )
    return web.json_response({"ok": False, "error": "Не знайдено"}, status=404, headers=cors_headers(request))


def _channel_product_id(request: web.Request) -> int:
    try:
        return int(request.match_info.get("id", 0))
    except (TypeError, ValueError):
        return 0


async def handle_get_channel_posts(request: web.Request):
    """Канал з налаштувань, опис товару для шаблону й попередні публікації (з переходами)."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    product_id = _channel_product_id(request)
    posts = await run_db(channel_posts_svc.list_product_posts, product_id)
    channel = await run_db(channel_posts_svc.get_channel)
    product = await run_db(get_product_by_id, product_id)
    return web.json_response(
        {"channel": channel, "posts": posts, "description": (product or {}).get("description") or ""},
        headers=cors_headers(request),
    )


CARD_PHOTO_MAX_BYTES = 15 * 1024 * 1024


def _card_photo_url(url: str) -> str:
    """Більша JPEG-версія фото для картки 1080×1080; не-Cloudinary URL лишається як є."""
    if "res.cloudinary.com" in url and "/upload/" in url:
        return url.replace("/upload/", "/upload/f_jpg,w_1400,c_limit/", 1)
    return url


class _PublicOnlyResolver(aiohttp.abc.AbstractResolver):
    """DNS-резолвер, що віддає лише публічні IP: перевірка й з'єднання йдуть з одного й того ж резолву (без DNS rebinding)."""

    def __init__(self):
        self._inner = aiohttp.ThreadedResolver()

    async def resolve(self, host, port=0, family=0):
        import ipaddress

        infos = await self._inner.resolve(host, port, family)
        ok = []
        for info in infos:
            ip = ipaddress.ip_address(info["host"])
            if not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                ok.append(info)
        if not ok:
            raise OSError(f"Хост {host} вказує на непублічну адресу")
        return ok

    async def close(self):
        await self._inner.close()


async def _download_card_photo(url: str) -> bytes | None:
    """Завантажує фото товару для картки (лише http/https, до 15 МБ, таймаут 10 с). Помилка → None.
    Хост перевіряється на приватні/loopback/link-local адреси (SSRF), редиректи — вручну, до 3, з перевіркою кожного."""
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(resolver=_PublicOnlyResolver())) as session:
            target = _card_photo_url(url)
            for _ in range(4):
                if not await asyncio.to_thread(is_safe_http_url, target):
                    logger.warning("Фото для картки: небезпечний URL відхилено")
                    return None
                async with session.get(target, allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location") or ""
                        target = str(resp.url.join(resp.url.__class__(location))) if location else ""
                        if not target:
                            return None
                        continue
                    if resp.status != 200:
                        return None
                    if (resp.content_length or 0) > CARD_PHOTO_MAX_BYTES:
                        return None
                    buf = bytearray()
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        buf.extend(chunk)
                        if len(buf) > CARD_PHOTO_MAX_BYTES:
                            return None
                    return bytes(buf)
            return None
    except Exception as e:
        logger.warning("Фото для картки не завантажилось: %s", e)
        return None


async def _render_product_card(product: dict, params: dict) -> bytes:
    """PNG-картка товару: фото береться на сервері з product['photos'] (URL від клієнта не приймаємо)."""
    photos = [u for u in (product.get("photos") or []) if u]
    photo_bytes = None
    if photos:
        idx = params["photo_index"] if 0 <= params["photo_index"] < len(photos) else 0
        photo_bytes = await _download_card_photo(photos[idx])
    title = params["title"] or str(product.get("name") or "")
    return await asyncio.to_thread(
        post_cards_svc.render_card, photo_bytes,
        template=params["template"], title=title, price=params["price"],
        old_price=params["old_price"], badge=params["badge"], tag=params["tag"],
        subtitle=params["subtitle"],
    )


async def handle_channel_card(request: web.Request):
    """PNG-превʼю картки товару (те саме зображення піде в канал)."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    try:
        data = await request.json()
    except Exception:
        data = None
    params, err = post_cards_svc.normalize_card_params(data)
    if err:
        return web.json_response({"ok": False, "error": err}, status=400, headers=cors_headers(request))
    product = await run_db(get_product_by_id, _channel_product_id(request))
    if not product:
        return web.json_response({"ok": False, "error": "Товар не знайдено"}, status=404, headers=cors_headers(request))
    try:
        png = await _render_product_card(product, params)
    except Exception:
        logger.exception("Не вдалось намалювати картку товару %s", product.get("id"))
        return web.json_response({"ok": False, "error": "Не вдалось створити картку"}, status=500, headers=cors_headers(request))
    headers = {**cors_headers(request), "Cache-Control": "no-store"}
    return web.Response(body=png, content_type="image/png", headers=headers)


def _redact_token(e: Exception) -> str:
    """Текст помилки для логу без токена бота (мережеві помилки можуть містити URL з ним)."""
    text = str(e)
    return text.replace(BOT_TOKEN, "***") if BOT_TOKEN else text


def _channel_error_text(e: Exception) -> str:
    msg = str(e).lower()
    if "chat not found" in msg:
        return "Канал не знайдено. Перевірте @username або ID і що бот доданий у канал"
    if "not enough rights" in msg or "not a member" in msg or "administrator" in msg or "forbidden" in msg:
        return "Бот не може писати в канал: додайте його адміном з правом «Публікація повідомлень»"
    return "Telegram не прийняв пост, спробуйте ще раз"


async def handle_publish_channel_post(request: web.Request):
    """Пост товару в канал: фото + текст + кнопки «Замовити» і «Усі товари»."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        return web.json_response({"ok": False, "error": "Bad JSON"}, status=400, headers=cors_headers(request))

    product_id = _channel_product_id(request)
    product = await run_db(get_product_by_id, product_id)
    if not product:
        return web.json_response(
            {"ok": False, "error": "Публікувати можна лише товар, що є в магазині"}, status=404, headers=cors_headers(request)
        )
    channel = channel_posts_svc.normalize_channel(data.get("channel"))
    if not channel:
        return web.json_response(
            {"ok": False, "error": "Вкажіть канал: @username або ID на кшталт -100…"}, status=400, headers=cors_headers(request)
        )
    photos = product.get("photos") or []
    photos = [u for u in photos if u]
    if data.get("photos_mode") == "one" and photos:
        try:
            idx = int(data.get("photo_index") or 0)
        except (TypeError, ValueError):
            idx = 0
        photos = [photos[idx] if 0 <= idx < len(photos) else photos[0]]
    album = [_share_photo_url(u) for u in photos[: channel_posts_svc.MAX_ALBUM_PHOTOS]]
    photo = album[0] if album else ""
    card_png = None
    if isinstance(data.get("card"), dict):
        # Режим картки: єдине фото — згенерована PNG-картка (альбом і photos_mode ігноруються)
        card_params, card_err = post_cards_svc.normalize_card_params(data.get("card"))
        if card_err:
            return web.json_response({"ok": False, "error": card_err}, status=400, headers=cors_headers(request))
        try:
            card_png = await _render_product_card(product, card_params)
        except Exception:
            logger.exception("Не вдалось намалювати картку товару %s", product_id)
            return web.json_response({"ok": False, "error": "Не вдалось створити картку"}, status=500, headers=cors_headers(request))
        album = []
        photo = "card"
    links_mode = data.get("buttons_mode") == "links"
    text, text_err = channel_posts_svc.validate_text(
        data.get("text"), bool(photo), channel_posts_svc.LINKS_TEXT_LEN if links_mode else 0
    )
    if text_err:
        return web.json_response({"ok": False, "error": text_err}, status=400, headers=cors_headers(request))

    tg_bot = getattr(bot_app, "bot", None)
    if tg_bot is None:
        return web.json_response({"ok": False, "error": "Бот не запущено"}, status=502, headers=cors_headers(request))

    post_id = await run_db(channel_posts_svc.create_post, product_id, channel, text)
    body = channel_posts_svc.caption_html(text)
    if links_mode:
        # Посилання в самому тексті: усе в одному повідомленні, навіть для альбому
        markup = None
        body += channel_posts_svc.links_html(post_id, product_id)
    else:
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛒 Замовити", url=channel_posts_svc.product_link(post_id, product_id)),
            InlineKeyboardButton("🛍 Усі товари", url=channel_posts_svc.shop_link(post_id)),
        ]])
    silent = bool(data.get("silent"))
    from rich_messages import _telegram_retry

    try:
        if len(album) > 1:
            # Альбом не може мати inline-кнопок: підпис на першому фото, кнопки — окремим повідомленням під ним
            from telegram import InputMediaPhoto

            media = [InputMediaPhoto(album[0], caption=body, parse_mode="HTML")] + [InputMediaPhoto(u) for u in album[1:]]
            sent = await _telegram_retry(
                lambda: tg_bot.send_media_group(chat_id=channel, media=media, disable_notification=silent),
                label="channel post album",
            )
            message = sent[0]
            if markup is not None:
                await _telegram_retry(
                    lambda: tg_bot.send_message(
                        chat_id=channel, text="👇 Оформити замовлення", reply_markup=markup, disable_notification=True,
                    ),
                    label="channel post buttons",
                )
        elif photo:
            from telegram import InputFile

            message = await _telegram_retry(
                lambda: tg_bot.send_photo(
                    chat_id=channel,
                    photo=InputFile(io.BytesIO(card_png), filename="card.png") if card_png else photo,
                    caption=body, parse_mode="HTML",
                    reply_markup=markup, disable_notification=silent,
                ),
                label="channel post photo",
            )
        else:
            message = await _telegram_retry(
                lambda: tg_bot.send_message(
                    chat_id=channel, text=body, parse_mode="HTML",
                    reply_markup=markup, disable_notification=silent,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                ),
                label="channel post",
            )
    except Exception as e:
        await run_db(channel_posts_svc.delete_post, post_id)
        logger.warning("Пост товару %s у канал %s не вдався: %s", product_id, channel, _redact_token(e))
        return web.json_response({"ok": False, "error": _channel_error_text(e)}, status=502, headers=cors_headers(request))

    await run_db(channel_posts_svc.set_message_id, post_id, message.message_id)
    if channel != await run_db(channel_posts_svc.get_channel):
        await run_db(channel_posts_svc.set_channel, channel)
    pinned = False
    if data.get("pin"):
        try:
            await tg_bot.pin_chat_message(chat_id=channel, message_id=message.message_id, disable_notification=True)
            pinned = True
        except Exception as e:
            logger.warning("Не вдалось закріпити пост %s у %s: %s", post_id, channel, _redact_token(e))
    return web.json_response(
        {
            "ok": True,
            "id": post_id,
            "url": channel_posts_svc.post_url(channel, message.message_id),
            "pinned": pinned,
            "channel": channel,
        },
        headers=cors_headers(request),
    )


# ─── АКЦІЇ → ПОСТ У КАНАЛ ────────────────────────────────────────

async def _promo_card_context(request: web.Request):
    """(акція, {id категорії: назва}, відповідь-помилка) для адмін-ендпоінтів поста акції."""
    promo = await run_db(get_promotion, str(request.match_info.get("id", "")))
    if not promo:
        return None, None, web.json_response({"ok": False, "error": "Акцію не знайдено"}, status=404, headers=cors_headers(request))
    cats = await run_db(get_all_categories, False)
    return promo, {str(c.get("id")): c.get("name") or str(c.get("id")) for c in cats}, None


async def handle_get_promo_channel_post(request: web.Request):
    """Типові параметри картинки й текст поста з налаштувань акції + канал і довідники для форми."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    promo, cats, err_resp = await _promo_card_context(request)
    if err_resp is not None:
        return err_resp
    params, _ = promo_cards_svc.normalize_promo_params({}, promo, cats)
    return web.json_response(
        {
            "ok": True,
            "channel": await run_db(channel_posts_svc.get_channel),
            "params": params,
            "text": promo_cards_svc.default_post_text(promo, params),
            "motifs": [{"key": k, "label": v[0], "tag": v[1]} for k, v in promo_cards_svc.MOTIFS.items()],
            "accents": list(promo_cards_svc.ACCENTS),
        },
        headers=cors_headers(request),
    )


async def handle_promo_channel_card(request: web.Request):
    """PNG-превʼю картинки акції (те саме зображення піде в канал)."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        return web.json_response({"ok": False, "error": "Bad JSON"}, status=400, headers=cors_headers(request))
    promo, cats, err_resp = await _promo_card_context(request)
    if err_resp is not None:
        return err_resp
    params, err = promo_cards_svc.normalize_promo_params(data, promo, cats)
    if err:
        return web.json_response({"ok": False, "error": err}, status=400, headers=cors_headers(request))
    try:
        png = await asyncio.to_thread(promo_cards_svc.render_promo_card, **params)
    except Exception:
        logger.exception("Не вдалось намалювати картинку акції %s", promo.get("id"))
        return web.json_response({"ok": False, "error": "Не вдалось створити картинку"}, status=500, headers=cors_headers(request))
    return web.Response(body=png, content_type="image/png", headers={**cors_headers(request), "Cache-Control": "no-store"})


async def handle_publish_promo_post(request: web.Request):
    """Пост акції в канал: картинка з налаштувань акції + підпис + кнопка «Відкрити магазин»."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        return web.json_response({"ok": False, "error": "Bad JSON"}, status=400, headers=cors_headers(request))
    promo, cats, err_resp = await _promo_card_context(request)
    if err_resp is not None:
        return err_resp
    channel = channel_posts_svc.normalize_channel(data.get("channel"))
    if not channel:
        return web.json_response(
            {"ok": False, "error": "Вкажіть канал: @username або ID на кшталт -100…"}, status=400, headers=cors_headers(request)
        )
    params, err = promo_cards_svc.normalize_promo_params(data.get("card"), promo, cats)
    if err:
        return web.json_response({"ok": False, "error": err}, status=400, headers=cors_headers(request))
    text, text_err = channel_posts_svc.validate_text(data.get("text"), True, 0)
    if text_err:
        return web.json_response({"ok": False, "error": text_err}, status=400, headers=cors_headers(request))
    try:
        png = await asyncio.to_thread(promo_cards_svc.render_promo_card, **params)
    except Exception:
        logger.exception("Не вдалось намалювати картинку акції %s", promo.get("id"))
        return web.json_response({"ok": False, "error": "Не вдалось створити картинку"}, status=500, headers=cors_headers(request))
    tg_bot = getattr(bot_app, "bot", None)
    if tg_bot is None:
        return web.json_response({"ok": False, "error": "Бот не запущено"}, status=502, headers=cors_headers(request))

    from rich_messages import _telegram_retry
    from telegram import InputFile

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛍 Відкрити магазин", url=promo_cards_svc.shop_link())]])
    silent = bool(data.get("silent"))
    try:
        message = await _telegram_retry(
            lambda: tg_bot.send_photo(
                chat_id=channel, photo=InputFile(io.BytesIO(png), filename="promo.png"),
                caption=channel_posts_svc.caption_html(text), parse_mode="HTML",
                reply_markup=markup, disable_notification=silent,
            ),
            label="channel promo post",
        )
    except Exception as e:
        logger.warning("Пост акції %s у канал %s не вдався: %s", promo.get("id"), channel, _redact_token(e))
        return web.json_response({"ok": False, "error": _channel_error_text(e)}, status=502, headers=cors_headers(request))
    if channel != await run_db(channel_posts_svc.get_channel):
        await run_db(channel_posts_svc.set_channel, channel)
    pinned = False
    if data.get("pin"):
        try:
            await tg_bot.pin_chat_message(chat_id=channel, message_id=message.message_id, disable_notification=True)
            pinned = True
        except Exception as e:
            logger.warning("Не вдалось закріпити пост акції у %s: %s", channel, _redact_token(e))
    return web.json_response(
        {"ok": True, "url": channel_posts_svc.post_url(channel, message.message_id), "pinned": pinned, "channel": channel},
        headers=cors_headers(request),
    )


# ─── РОЗІГРАШІ (GIVEAWAYS.md) ───────────────────────────────────

async def _channel_member_state(chat_id, user_id: int) -> bool | None:
    """Підписаний / ні / None — Telegram не відповів (бот не адмін каналу тощо)."""
    tg_bot = getattr(app_state.bot_app, "bot", None)
    if tg_bot is None or not chat_id:
        return None
    try:
        member = await tg_bot.get_chat_member(chat_id, int(user_id))
    except Exception as e:
        logger.warning("giveaway get_chat_member(%s, %s) failed: %s", chat_id, user_id, _redact_token(e))
        return None
    status = getattr(member, "status", "")
    return status in _CHANNEL_SUBSCRIBED_STATUSES or (status == "restricted" and getattr(member, "is_member", False))


def _giveaway_entered_text(g: dict, number: int, tickets: int, user_id: int) -> str:
    title = html.escape(g["title"])
    lines = [
        f"🎁 Ви берете участь у розіграші «{title}»!",
        f"Ваш номер: <b>№{number}</b>",
        f"Підсумки: {giveaways_svc.format_kyiv(g['ends_at'])} (за Києвом).",
        "",
        "Не відписуйтесь від каналу до кінця розіграшу — інакше участь анулюється.",
    ]
    if g["referral_bonus"]:
        link = giveaways_svc.join_link(config.BOT_USERNAME, g["id"], user_id)
        lines += [
            "",
            f"👥 Запрошуйте друзів: +1 шанс за кожного "
            f"{'нового в боті друга' if g.get('referral_scope') == 'new' else 'друга'}, що підпишеться й візьме участь "
            f"(до +{g['referral_limit']}).",
            f"Ваше посилання: {html.escape(link)}",
            f"Зараз шансів: <b>{tickets}</b>",
        ]
    return "\n".join(lines)


def _giveaway_entered_markup(g: dict, user_id: int) -> InlineKeyboardMarkup:
    from urllib.parse import quote

    rows = []
    if g["referral_bonus"]:
        link = giveaways_svc.join_link(config.BOT_USERNAME, g["id"], user_id)
        share = (f"https://t.me/share/url?url={quote(link, safe='')}"
                 f"&text={quote('Беру участь у розіграші — приєднуйся 🎁', safe='')}")
        rows.append([InlineKeyboardButton("📨 Запросити друзів", url=share)])
    rows.append([InlineKeyboardButton("🛍️ Відкрити магазин", web_app=WebAppInfo(url=WEBAPP_URL))])
    return InlineKeyboardMarkup(rows)


async def giveaway_entry_flow(tg_bot, chat_id: int, user_id: int, giveaway_id: int, referrer_id: int = 0,
                              *, recheck: bool = False) -> str:
    """Участь у розіграші з бота. Повертає 'entered' | 'already' | 'not_subscribed' | 'closed' | 'error'."""
    from rich_messages import _telegram_retry

    async def say(text: str, markup=None):
        await _telegram_retry(
            lambda: tg_bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=markup,
                                        link_preview_options=LinkPreviewOptions(is_disabled=True)),
            label="giveaway reply",
        )

    g = await run_db(giveaways_svc.get_giveaway, giveaway_id)
    if not g or g["status"] == "cancelled":
        await say("Цей розіграш не знайдено або його скасовано.")
        return "closed"
    entry = await run_db(giveaways_svc.get_entry, g["id"], user_id)
    if entry:
        tickets = await run_db(giveaways_svc.tickets_preview, g, user_id)
        await say(_giveaway_entered_text(g, int(entry["number"]), tickets, user_id), _giveaway_entered_markup(g, user_id))
        return "already"
    if not giveaways_svc.is_open(g):
        await say(f"Розіграш «{html.escape(g['title'])}» уже завершено. Слідкуйте за каналом — будуть нові 🎁")
        return "closed"

    chat = config.normalize_channel_id(g["channel"])
    subscribed = await _channel_member_state(chat, user_id)
    if subscribed is None:
        await say("Не вдалося перевірити підписку. Спробуйте трохи пізніше 🙏")
        return "error"
    if not subscribed:
        if recheck:
            return "not_subscribed"
        rows = []
        link = config.channel_link_for(g["channel"])
        if link:
            rows.append([InlineKeyboardButton("📢 Підписатися на канал", url=link)])
        rows.append([InlineKeyboardButton("✅ Я підписався", callback_data=f"gw_join_{g['id']}_{int(referrer_id or 0)}")])
        await say(
            f"🎁 Розіграш «{html.escape(g['title'])}»\n\nЩоб узяти участь, підпишіться на канал і натисніть «Я підписався».",
            InlineKeyboardMarkup(rows),
        )
        return "not_subscribed"

    res = await run_db(giveaways_svc.join, g["id"], user_id, referrer_id)
    if not res.get("ok"):
        if res.get("error") == "closed":
            await say(f"Розіграш «{html.escape(g['title'])}» уже завершено.")
            return "closed"
        await say("Не вдалося записати участь, спробуйте ще раз.")
        return "error"
    tickets = await run_db(giveaways_svc.tickets_preview, g, user_id)
    await say(_giveaway_entered_text(g, int(res["number"]), tickets, user_id), _giveaway_entered_markup(g, user_id))
    return "already" if res.get("already") else "entered"


async def giveaway_join_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка «✅ Я підписався» під повідомленням про розіграш."""
    query = update.callback_query
    m = re.match(r"^gw_join_(\d+)_(\d+)$", query.data or "")
    if not m or not query.from_user:
        await query.answer()
        return
    outcome = await giveaway_entry_flow(
        context.bot, query.message.chat_id, query.from_user.id, int(m.group(1)), int(m.group(2)), recheck=True
    )
    if outcome == "not_subscribed":
        await query.answer("Підписку ще не видно. Підпишіться на канал і натисніть ще раз.", show_alert=True)
        return
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


def _giveaway_user_html(w: dict) -> str:
    username = (w.get("username") or "").strip().lstrip("@")
    name = html.escape(f"@{username}" if username else (w.get("name") or "").strip() or "Учасник")
    return f'<a href="tg://user?id={int(w["user_id"])}">{name}</a>'


async def _notify_owner(text: str) -> None:
    tg_bot = getattr(app_state.bot_app, "bot", None)
    if tg_bot is None or not OWNER_ID:
        return
    admin_url = get_admin_webapp_url()
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Відкрити адмінку", web_app=WebAppInfo(url=admin_url))]]) \
        if admin_url else None
    try:
        await tg_bot.send_message(chat_id=OWNER_ID, text=text, parse_mode="HTML", reply_markup=markup,
                                  link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception as e:
        logger.warning("Не вдалось написати власнику про розіграш: %s", _redact_token(e))


async def run_giveaway_draw(giveaway_id: int, *, notify_owner: bool = True) -> dict:
    """Жеребкування: повторна перевірка підписки → вибір з вагами → чернетка результатів."""
    if not await run_db(giveaways_svc.claim_for_draw, giveaway_id):
        return {"ok": False, "error": "Розіграш уже завершено або скасовано"}
    try:
        g = await run_db(giveaways_svc.get_giveaway, giveaway_id)
        candidates = await run_db(giveaways_svc.draw_candidates, giveaway_id, (OWNER_ID,))
        if not candidates:
            # власник виключений з жеребкування, крім випадку, коли він єдиний учасник
            candidates = await run_db(giveaways_svc.draw_candidates, giveaway_id)
        chat = config.normalize_channel_id(g["channel"])
        eligible: set[int] = set()
        errors = 0
        for c in candidates:
            state = await _channel_member_state(chat, int(c["user_id"]))
            if state is None:
                errors += 1
            elif state:
                eligible.add(int(c["user_id"]))
            await asyncio.sleep(0.05)
        if candidates and errors * 2 > len(candidates):
            await run_db(giveaways_svc.release_draw, giveaway_id)
            msg = "Telegram не дав перевірити підписку учасників. Бот має бути адміном каналу."
            if notify_owner:
                await _notify_owner(f"⚠️ Розіграш «{html.escape(g['title'])}»: {msg} Спробую ще раз за хвилину.")
            return {"ok": False, "error": msg}
        result = await run_db(giveaways_svc.draw, giveaway_id, eligible, candidates)
    except Exception:
        logger.exception("giveaway %s draw failed", giveaway_id)
        await run_db(giveaways_svc.release_draw, giveaway_id)
        return {"ok": False, "error": "Жеребкування не вдалося, спробуйте ще раз"}

    if notify_owner:
        winners = (await run_db(giveaways_svc.list_winners, [giveaway_id])).get(giveaway_id, [])
        names = [f"{w['position']}. {_giveaway_user_html(w)} (№{w['number']})" for w in winners if w["role"] == "winner"]
        await _notify_owner(
            f"🎲 Розіграш «{html.escape(g['title'])}» завершено.\n"
            f"Учасників: {len(candidates)}, допущено: {result['eligible']}.\n\n"
            + ("🏆 Переможці:\n" + "\n".join(names) if names else "Переможців немає — ніхто не виконав умови.")
            + "\n\nПеревірте й опублікуйте результати в адмінці → «Розіграші»."
        )
    return {"ok": True, **result}


async def _giveaway_loop() -> None:
    """Раз на хвилину: жеребкування, час яких настав; нагадування переможцям; минулі строки — адміну."""
    while True:
        try:
            for gid in await run_db(giveaways_svc.due_giveaway_ids):
                await run_giveaway_draw(gid)
            await _giveaway_prize_tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("giveaway loop failed")
        await asyncio.sleep(60)


def _prize_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "🎁 Забрати приз", url=giveaways_svc.prize_link(config.BOT_USERNAME))]])


async def _send_to_winner(user_id: int, text: str, label: str) -> bool:
    from rich_messages import _telegram_retry

    try:
        await _telegram_retry(
            lambda: bot_app.bot.send_message(chat_id=int(user_id), text=text, parse_mode="HTML",
                                             reply_markup=_prize_markup()),
            label=label,
        )
        return True
    except Exception as e:
        logger.warning("Переможцю %s не дійшло повідомлення (%s): %s", user_id, label, _redact_token(e))
        return False


async def _giveaway_prize_tick() -> None:
    for r in await run_db(giveaways_svc.due_reminders):
        if not await run_db(giveaways_svc.mark_reminded, r["winner_id"], r["flag"]):
            continue
        name = html.escape(await _prize_name(r))
        await _send_to_winner(
            r["user_id"],
            f"⏳ Нагадуємо: ваш приз <b>{name}</b> чекає в кошику ще менше {r['hours']} год — "
            f"до {giveaways_svc.format_kyiv(r['claim_deadline'])} (за Києвом).\n\n"
            "Натисніть кнопку, вкажіть доставку й оформіть замовлення 🎁",
            "giveaway reminder",
        )
    for r in await run_db(giveaways_svc.expired_unnotified):
        if not await run_db(giveaways_svc.mark_expired_notified, r["id"]):
            continue
        await _notify_owner_expired(r)


async def _notify_owner_expired(r: dict) -> None:
    tg_bot = getattr(app_state.bot_app, "bot", None)
    if tg_bot is None or not OWNER_ID:
        return
    text = (f"⌛ Розіграш «{html.escape(r['title'])}»: переможець №{r['position']} — "
            f"{_giveaway_user_html(r)} — не забрав приз вчасно.\n\nПерекинути приз першому з резерву "
            f"чи дати ще {giveaways_svc.EXTEND_HOURS} год?")
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Перекинути", callback_data=f"gw_rr_{int(r['id'])}"),
        InlineKeyboardButton(f"⏳ +{giveaways_svc.EXTEND_HOURS} год", callback_data=f"gw_ext_{int(r['id'])}"),
    ]])
    try:
        await tg_bot.send_message(chat_id=OWNER_ID, text=text, parse_mode="HTML", reply_markup=markup)
    except Exception as e:
        logger.warning("Не вдалось написати власнику про минулий строк призу: %s", _redact_token(e))


async def _reroll_and_notify(winner_id: int) -> dict:
    """Перекинути приз першому з резерву; якщо результати вже опубліковано — одразу повідомити нового."""
    res = await run_db(giveaways_svc.reroll, winner_id)
    if not res.get("ok"):
        return res
    res["delivered"] = None
    if res.get("published"):
        w = await run_db(giveaways_svc.get_winner, res["winner_id"])
        g = await run_db(giveaways_svc.get_giveaway, w["giveaway_id"])
        sent = await _send_giveaway_prize(g, res["winner_id"])
        res["delivered"] = bool(sent.get("delivered"))
    return res


async def _extend_and_notify(winner_id: int) -> dict:
    res = await run_db(giveaways_svc.extend_prize, winner_id)
    if res.get("ok"):
        g = await run_db(giveaways_svc.get_giveaway, res["giveaway_id"])
        res["delivered"] = await _send_to_winner(
            res["user_id"],
            f"⏳ Гарні новини: строк вашого призу з розіграшу «{html.escape(g['title'] if g else '')}» подовжено "
            f"до {giveaways_svc.format_kyiv(res['deadline'])} (за Києвом). Приз чекає в кошику 🎁",
            "giveaway extend",
        )
    return res


async def giveaway_owner_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопки власника в повідомленні про минулий строк: gw_rr_<winner> / gw_ext_<winner>."""
    query = update.callback_query
    if not query:
        return
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        await query.answer("Недоступно", show_alert=True)
        return
    _, action, wid = query.data.split("_")
    if action == "rr":
        res = await _reroll_and_notify(int(wid))
        done = "🔄 Приз перекинуто першому з резерву." + (
            " ⚠️ Повідомлення новому переможцю не дійшло." if res.get("delivered") is False else "")
    else:
        res = await _extend_and_notify(int(wid))
        done = f"⏳ Строк подовжено до {giveaways_svc.format_kyiv(res['deadline'])}." if res.get("ok") else ""
    if not res.get("ok"):
        await query.answer(res.get("error") or "Не вдалось", show_alert=True)
        return
    await query.answer()
    try:
        await query.edit_message_text(f"{query.message.text_html}\n\n{done}", parse_mode="HTML")
    except Exception:
        pass


def _giveaway_product_price(product: dict | None) -> int:
    """Повна ціна товару-призу: найдорожчий варіант (переможець може обрати будь-який розмір)."""
    if not product or is_contract_product(product):
        return 0
    variants = [v for v in (product.get("variants") or []) if not v.get("contract")]
    if variants:
        return max(int(v.get("price") or 0) for v in variants)
    return max(0, int(product.get("price") or 0))


async def _prize_name(p: dict) -> str:
    """Назва призу для повідомлень: товар (+ зафіксований варіант) або «Сертифікат на N ₴»."""
    if p.get("prize_type", "product") != "product":
        return f"Сертифікат на {int(p['prize_value'])} ₴"
    product = await run_db(get_product_by_id, p["product_id"]) if p.get("product_id") else None
    if not product:
        return p.get("title") or "Приз"
    variant = get_product_variant(product, p.get("product_variant"))
    return product["name"] + (f" · {variant['name']}" if variant else "")


async def _send_giveaway_prize(g: dict, winner_id: int) -> dict:
    """Приз стає активним у магазині переможця + повідомлення йому. {'ok', 'delivered'}."""
    issued = await run_db(giveaways_svc.issue_prize, winner_id)
    if not issued.get("ok"):
        return {"ok": False, "error": issued.get("error"), "delivered": False}
    name = html.escape(await _prize_name(g))
    deadline = giveaways_svc.format_kyiv(issued["deadline"])
    shipping = ("🚚 Доставку всієї посилки оплачуємо ми." if g["shipping_payer"] == "shop"
                else "🚚 Доставка Новою Поштою — за тарифами перевізника.")
    if g.get("prize_type", "product") == "product":
        prize_line = f"Ваш приз: <b>{name}</b> — безкоштовно."
        how = ("Приз уже чекає у вашому кошику — натисніть кнопку нижче"
               + (", оберіть варіант" if not g.get("product_variant") else "")
               + ", вкажіть доставку й оформіть замовлення. Можна докупити й інші товари.")
    else:
        prize_line = f"Ваш приз: <b>{name}</b> на будь-яке замовлення."
        how = ("Сертифікат уже застосовано у вашому кошику — натисніть кнопку нижче, оберіть товари й оформіть "
               "замовлення. Якщо замовлення дешевше, різниця згорає.")
    text = (
        f"🎉 Вітаємо! Ви виграли в розіграші «{html.escape(g['title'])}»!\n\n"
        f"{prize_line}\n{shipping}\n\n{how}\n\n"
        f"⏳ Забрати приз можна до {deadline} (за Києвом)."
    )
    delivered = await _send_to_winner(issued["user_id"], text, "giveaway prize")
    return {"ok": True, "delivered": delivered}


def _giveaway_results_html(g: dict, winners: list[dict], entries: int) -> str:
    lines = [f"🎉 Підсумки розіграшу «{html.escape(g['title'])}»", "",
             f"Учасників: {entries} · виконали умови: {g['eligible_count']}", ""]
    win = [w for w in winners if w["role"] == "winner" and w["status"] != "replaced"]
    if win:
        lines.append("🏆 Переможці:")
        lines += [f"{w['position']}. {_giveaway_user_html(w)} — №{w['number']}" for w in win]
        lines += ["", "Вітаємо! Переможцям ми написали в боті 💬"]
    else:
        lines.append("Цього разу учасників, що виконали умови, не було.")
    return "\n".join(lines)


async def _giveaway_item(giveaway_id: int) -> dict | None:
    items = await run_db(giveaways_svc.list_giveaways, 200)
    return next((g for g in items if g["id"] == int(giveaway_id)), None)


def _giveaway_id(request: web.Request, key: str = "id") -> int:
    try:
        return int(request.match_info.get(key, 0))
    except (TypeError, ValueError):
        return 0


async def handle_get_giveaways(request: web.Request):
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    items = await run_db(giveaways_svc.list_giveaways)
    channel = await run_db(channel_posts_svc.get_channel)
    return web.json_response(
        {"giveaways": items, "channel": channel, "reserve_count": giveaways_svc.RESERVE_COUNT,
         "claim_hours": giveaways_svc.CLAIM_HOURS},
        headers=cors_headers(request),
    )


def _giveaway_prize_title(payload: dict, product: dict | None, variant: dict | None) -> str:
    """Назва призу: введена вручну, інакше товар (+ варіант) або «Сертифікат на N ₴»."""
    return payload["title"] or (
        str(product.get("name") or "") + (f" · {variant['name']}" if variant else "")
        if product else f"Сертифікат на {payload['prize_value']} ₴"
    )[: giveaways_svc.TITLE_MAX]


async def _render_giveaway_card(payload: dict, product: dict | None) -> bytes:
    """Макет розіграшу з фото призу: власне фото або перше фото товару (завантажується на сервері)."""
    url = payload["photo"] or next((u for u in ((product or {}).get("photos") or []) if u), "")
    photo_bytes = await _download_card_photo(url) if url else None
    return await asyncio.to_thread(
        post_cards_svc.render_giveaway_card, photo_bytes,
        title=payload["title"], ends=giveaways_svc.format_kyiv(payload["ends_at"]),
    )


async def handle_giveaway_card_preview(request: web.Request):
    """PNG-превʼю макета розіграшу (те саме зображення піде в канал)."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    payload, err = giveaways_svc.validate_giveaway(data)
    if err:
        return web.json_response({"ok": False, "error": err}, status=400, headers=cors_headers(request))
    product = await run_db(get_product_by_id, payload["product_id"]) if payload["product_id"] else None
    variant = get_product_variant(product, payload["product_variant"]) if product and payload["product_variant"] else None
    if payload["prize_type"] == "amount":
        variant = None
    payload["title"] = _giveaway_prize_title(payload, product, variant)
    try:
        png = await _render_giveaway_card(payload, product)
    except Exception:
        logger.exception("Не вдалось намалювати превʼю розіграшу")
        return web.json_response({"ok": False, "error": "Не вдалось створити картку"}, status=500, headers=cors_headers(request))
    return web.Response(body=png, content_type="image/png", headers={**cors_headers(request), "Cache-Control": "no-store"})


async def handle_create_giveaway(request: web.Request):
    """Створює розіграш і одразу публікує пост з кнопкою «Взяти участь» у канал."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    data, bad = await _json_body(request)
    if bad is not None:
        return bad
    payload, err = giveaways_svc.validate_giveaway(data)
    if err:
        return web.json_response({"ok": False, "error": err}, status=400, headers=cors_headers(request))
    product = await run_db(get_product_by_id, payload["product_id"]) if payload["product_id"] else None
    if payload["product_id"] and not product:
        return web.json_response({"ok": False, "error": "Приз має бути товаром, що є в магазині"},
                                 status=404, headers=cors_headers(request))
    variant = None
    if payload["prize_type"] == "product":
        if payload["product_variant"]:
            variant = get_product_variant(product, payload["product_variant"])
            if not variant or variant.get("contract"):
                return web.json_response({"ok": False, "error": "Обери варіант товару зі списку"},
                                         status=400, headers=cors_headers(request))
            payload["prize_value"] = int(variant["price"])
        else:
            payload["prize_value"] = _giveaway_product_price(product)
        if payload["prize_value"] <= 0:
            return web.json_response({"ok": False, "error": "У товару немає фіксованої ціни — обери інший"},
                                     status=400, headers=cors_headers(request))
    payload["title"] = _giveaway_prize_title(payload, product, variant)
    product = product or {}
    channel = channel_posts_svc.normalize_channel(data.get("channel")) or await run_db(channel_posts_svc.get_channel)
    if not channel:
        return web.json_response({"ok": False, "error": "Вкажіть канал: @username або ID на кшталт -100…"},
                                 status=400, headers=cors_headers(request))
    tg_bot = getattr(bot_app, "bot", None)
    if tg_bot is None:
        return web.json_response({"ok": False, "error": "Бот не запущено"}, status=502, headers=cors_headers(request))

    photo = payload["photo"] or next((_share_photo_url(u) for u in (product.get("photos") or []) if u), "")
    card_png = None
    if data.get("card") == "template":
        try:
            card_png = await _render_giveaway_card(payload, product)
        except Exception:
            logger.exception("Не вдалось намалювати картку розіграшу")
            return web.json_response({"ok": False, "error": "Не вдалось створити картку розіграшу"}, status=500,
                                     headers=cors_headers(request))
    gid = await run_db(giveaways_svc.create_giveaway, payload, channel)
    body = (html.escape(payload["text"])
            + f"\n\n🗓 Підсумки: {giveaways_svc.format_kyiv(payload['ends_at'])} (за Києвом)")
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(
        "🎁 Взяти участь", url=giveaways_svc.join_link(config.BOT_USERNAME, gid),
    )]])
    from rich_messages import _telegram_retry

    try:
        if card_png:
            from telegram import InputFile

            message = await _telegram_retry(
                lambda: tg_bot.send_photo(chat_id=channel, photo=InputFile(io.BytesIO(card_png), filename="giveaway.png"),
                                          caption=body, parse_mode="HTML", reply_markup=markup),
                label="giveaway post card",
            )
        elif photo:
            message = await _telegram_retry(
                lambda: tg_bot.send_photo(chat_id=channel, photo=photo, caption=body, parse_mode="HTML",
                                          reply_markup=markup),
                label="giveaway post photo",
            )
        else:
            message = await _telegram_retry(
                lambda: tg_bot.send_message(chat_id=channel, text=body, parse_mode="HTML", reply_markup=markup,
                                            link_preview_options=LinkPreviewOptions(is_disabled=True)),
                label="giveaway post",
            )
    except Exception as e:
        await run_db(giveaways_svc.delete_giveaway, gid)
        logger.warning("Пост розіграшу %s у %s не вдався: %s", gid, channel, _redact_token(e))
        return web.json_response({"ok": False, "error": _channel_error_text(e)}, status=502, headers=cors_headers(request))

    await run_db(giveaways_svc.set_message_id, gid, message.message_id)
    if data.get("pin"):
        try:
            await tg_bot.pin_chat_message(chat_id=channel, message_id=message.message_id, disable_notification=True)
        except Exception as e:
            logger.warning("Не вдалось закріпити пост розіграшу %s: %s", gid, _redact_token(e))
    return web.json_response(
        {"ok": True, "giveaway": await _giveaway_item(gid),
         "url": channel_posts_svc.post_url(channel, message.message_id)},
        headers=cors_headers(request),
    )


async def handle_draw_giveaway(request: web.Request):
    """«Завершити зараз» — жеребкування не чекаючи дати."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    gid = _giveaway_id(request)
    result = await run_giveaway_draw(gid, notify_owner=False)
    if not result.get("ok"):
        return web.json_response(result, status=409, headers=cors_headers(request))
    return web.json_response({"ok": True, "giveaway": await _giveaway_item(gid)}, headers=cors_headers(request))


async def handle_publish_giveaway(request: web.Request):
    """Пост з результатами в канал + купони й повідомлення переможцям."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    gid = _giveaway_id(request)
    item = await _giveaway_item(gid)
    if not item:
        return web.json_response({"ok": False, "error": "Не знайдено"}, status=404, headers=cors_headers(request))
    if not await run_db(giveaways_svc.claim_publish, gid):
        return web.json_response({"ok": False, "error": "Результати вже опубліковано або жеребкування ще не було"},
                                 status=409, headers=cors_headers(request))
    from rich_messages import _telegram_retry
    from telegram import ReplyParameters

    text = _giveaway_results_html(item, item["winners"], item["stats"].get("entries", 0))
    reply = ReplyParameters(message_id=item["message_id"], allow_sending_without_reply=True) \
        if item.get("message_id") else None
    card_png = None
    win = [w for w in item["winners"] if w["role"] == "winner" and w["status"] != "replaced"]
    if win and len(text) <= 1000:  # довший підпис Telegram не прийме під фото — тоді лише текст
        try:
            card_png = await asyncio.to_thread(
                post_cards_svc.render_results_card,
                [("@" + w["username"].strip().lstrip("@")) if (w.get("username") or "").strip().lstrip("@") not in ("", "—")
                 else (w.get("name") or "") for w in win],
                prize=item["title"],
            )
        except Exception:
            logger.exception("Не вдалось намалювати картку підсумків розіграшу %s", gid)
    try:
        if card_png:
            from telegram import InputFile

            message = await _telegram_retry(
                lambda: bot_app.bot.send_photo(chat_id=item["channel"], photo=InputFile(io.BytesIO(card_png), filename="results.png"),
                                               caption=text, parse_mode="HTML", reply_parameters=reply),
                label="giveaway results card",
            )
        else:
            message = await _telegram_retry(
                lambda: bot_app.bot.send_message(chat_id=item["channel"], text=text, parse_mode="HTML",
                                                 reply_parameters=reply,
                                                 link_preview_options=LinkPreviewOptions(is_disabled=True)),
                label="giveaway results",
            )
    except Exception as e:
        await run_db(giveaways_svc.unpublish, gid)
        logger.warning("Результати розіграшу %s не опубліковано: %s", gid, _redact_token(e))
        return web.json_response({"ok": False, "error": _channel_error_text(e)}, status=502, headers=cors_headers(request))
    await run_db(giveaways_svc.set_result_message_id, gid, message.message_id)
    if item.get("message_id"):
        try:
            await bot_app.bot.edit_message_reply_markup(chat_id=item["channel"], message_id=item["message_id"],
                                                        reply_markup=None)
        except Exception:
            pass

    g = await run_db(giveaways_svc.get_giveaway, gid)
    delivered = undelivered = 0
    for w in await run_db(giveaways_svc.pending_winners, gid):
        res = await _send_giveaway_prize(g, int(w["id"]))
        if res.get("delivered"):
            delivered += 1
        else:
            undelivered += 1
    return web.json_response(
        {"ok": True, "giveaway": await _giveaway_item(gid), "delivered": delivered, "undelivered": undelivered,
         "url": channel_posts_svc.post_url(item["channel"], message.message_id)},
        headers=cors_headers(request),
    )


async def handle_reroll_giveaway_winner(request: web.Request):
    """«Перекинути»: переможця замінює перший з резерву; після публікації — одразу купон і повідомлення."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    gid = _giveaway_id(request)
    wid = _giveaway_id(request, "winner_id")
    w = await run_db(giveaways_svc.get_winner, wid)
    if not w or int(w["giveaway_id"]) != gid:
        return web.json_response({"ok": False, "error": "Не знайдено"}, status=404, headers=cors_headers(request))
    res = await _reroll_and_notify(wid)
    if not res.get("ok"):
        return web.json_response(res, status=409, headers=cors_headers(request))
    return web.json_response({"ok": True, "giveaway": await _giveaway_item(gid), "delivered": res["delivered"]},
                             headers=cors_headers(request))


async def handle_extend_giveaway_winner(request: web.Request):
    """«Подовжити на 24 год» строк призу переможця."""
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    gid = _giveaway_id(request)
    wid = _giveaway_id(request, "winner_id")
    w = await run_db(giveaways_svc.get_winner, wid)
    if not w or int(w["giveaway_id"]) != gid:
        return web.json_response({"ok": False, "error": "Не знайдено"}, status=404, headers=cors_headers(request))
    res = await _extend_and_notify(wid)
    if not res.get("ok"):
        return web.json_response(res, status=409, headers=cors_headers(request))
    return web.json_response({"ok": True, "giveaway": await _giveaway_item(gid), "delivered": res["delivered"]},
                             headers=cors_headers(request))


PRIZE_CARD_MAX_BYTES = 3 * 1024 * 1024


def _prize_payload(p: dict, product: dict | None) -> dict:
    """Активний приз для Mini App: усе, щоб показати вікно перемоги й блок у кошику."""
    is_product = p["prize_type"] == "product"
    fixed = get_product_variant(product, p["product_variant"]) if product and p["product_variant"] else None
    choices = [] if fixed or not product else [
        {"id": v["id"], "name": v["name"], "price": int(v["price"])}
        for v in (product.get("variants") or []) if not v.get("contract")
    ]
    photos = (product or {}).get("photos") or []
    value = int(fixed["price"]) if fixed else int(p["prize_value"])
    return {
        "winner_id": p["winner_id"],
        "giveaway_id": p["giveaway_id"],
        "giveaway_title": p["title"],
        "type": p["prize_type"],
        "product_id": p["product_id"] if is_product else 0,
        "product_name": (product or {}).get("name") or p["title"],
        "photo": photos[0] if photos else "",
        "variant": {"id": fixed["id"], "name": fixed["name"]} if fixed else None,
        "variants": choices,
        "value": value,
        "deadline": p["claim_deadline"],
        "shipping_payer": p["shipping_payer"],
        "card_url": p["card_url"],
        "channel_url": giveaways_svc.channel_url(p["channel"]),
    }


def _prize_user(request: web.Request):
    """(user_id, None) або (0, відповідь-помилка) для /my/prize*."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return 0, err
    try:
        user_id = int(auth.get("user_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    if user_id <= 0:
        return 0, web.json_response({"ok": False, "error": "invalid_init_data"}, status=403,
                                    headers=cors_headers(request))
    return user_id, None


async def handle_get_my_prizes(request: web.Request):
    """Активні призи покупця: Mini App показує вікно перемоги й закріплений блок у кошику."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    user_id = _safe_int(auth.get("user_id"))
    if user_id <= 0:
        return web.json_response({"prizes": []}, headers=cors_headers(request))
    prizes = []
    for p in await run_db(giveaways_svc.active_prizes, user_id):
        product = await run_db(get_product_by_id, p["product_id"]) if p["product_id"] else None
        prizes.append(_prize_payload(p, product))
    return web.json_response({"prizes": prizes}, headers=cors_headers(request))


async def handle_upload_prize_card(request: web.Request):
    """Листівка «Мій приз» (малює Mini App) — на Cloudinary, для сторіс і збереження в галерею."""
    user_id, err = _prize_user(request)
    if err is not None:
        return err
    if not allow_review_upload(user_id):
        return web.json_response({"ok": False, "error": "Забагато спроб, спробуй пізніше"}, status=429,
                                 headers=cors_headers(request))
    try:
        form = await request.post()
        winner_id = _safe_int(form.get("winner_id"))
        file_field = form.get("file")
        if not isinstance(file_field, web.FileField):
            raise ValueError
        file_data = file_field.file.read(PRIZE_CARD_MAX_BYTES + 1)
    except Exception:
        return web.json_response({"ok": False, "error": "bad_request"}, status=400, headers=cors_headers(request))
    if len(file_data) > PRIZE_CARD_MAX_BYTES or (file_field.content_type or "") not in ("image/png", "image/jpeg"):
        return web.json_response({"ok": False, "error": "bad_file"}, status=400, headers=cors_headers(request))
    w = await run_db(giveaways_svc.get_winner, winner_id)
    if not w or int(w["user_id"]) != user_id or w["role"] != "winner" or w["status"] != "notified":
        return web.json_response({"ok": False, "error": "not_found"}, status=404, headers=cors_headers(request))
    if w.get("card_url"):
        return web.json_response({"ok": True, "url": w["card_url"]}, headers=cors_headers(request))
    result = await upload_photo_to_cloudinary(file_data, f"prize_{winner_id}", file_field.content_type)
    url = (result.get("url") or "") if result.get("ok") else ""
    if not url or not await run_db(giveaways_svc.set_card_url, winner_id, user_id, url):
        logger.warning("Листівка приза %s не збережена: cloudinary=%s url=%r", winner_id,
                       result.get("error") if isinstance(result, dict) else result, url)
        return web.json_response({"ok": False, "error": "upload_failed"}, status=502, headers=cors_headers(request))
    return web.json_response({"ok": True, "url": url}, headers=cors_headers(request))


async def handle_prepare_prize_share(request: web.Request):
    """Повідомлення «Мій приз» для Telegram.WebApp.shareMessage: листівка + кнопка на канал."""
    user_id, err = _prize_user(request)
    if err is not None:
        return err
    try:
        winner_id = int((await request.json()).get("winner_id"))
    except Exception:
        return web.json_response({"ok": False, "error": "bad_request"}, status=400, headers=cors_headers(request))
    w = await run_db(giveaways_svc.get_winner, winner_id)
    g = await run_db(giveaways_svc.get_giveaway, w["giveaway_id"]) if w else None
    if not g or int(w["user_id"]) != user_id or w["role"] != "winner" or not w.get("card_url"):
        return web.json_response({"ok": False, "error": "not_found"}, status=404, headers=cors_headers(request))
    name = await _prize_name(g)
    url = giveaways_svc.channel_url(g["channel"]) or f"https://t.me/{config.BOT_USERNAME}"
    result = {
        "type": "photo",
        "id": f"gw{winner_id}",
        "photo_url": w["card_url"],
        "thumbnail_url": w["card_url"],
        "caption": f"🎉 Я виграв(-ла) «{name}» у розіграші Poselyanov 3D Print! Бери участь і ти 🎁",
        "reply_markup": {"inline_keyboard": [[{"text": "🎁 Розіграші Poselyanov 3D", "url": url}]]},
    }
    payload = {"user_id": user_id, "result": result, "allow_user_chats": True, "allow_bot_chats": True,
               "allow_group_chats": True, "allow_channel_chats": True}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/savePreparedInlineMessage", json=payload
            ) as resp:
                body = await resp.json(content_type=None)
        if not isinstance(body, dict) or not body.get("ok"):
            raise RuntimeError((body or {}).get("description") if isinstance(body, dict) else "bad_response")
        return web.json_response({"ok": True, "id": body["result"]["id"]}, headers=cors_headers(request))
    except Exception as e:
        logger.warning("savePreparedInlineMessage для призу %s не вдалось: %s", winner_id,
                       str(e).replace(BOT_TOKEN, "***"))
        return web.json_response({"ok": False, "error": "prepare_failed"}, status=502, headers=cors_headers(request))


async def handle_cancel_giveaway(request: web.Request):
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    gid = _giveaway_id(request)
    if not await run_db(giveaways_svc.cancel_giveaway, gid):
        return web.json_response({"ok": False, "error": "Скасувати можна лише активний розіграш"},
                                 status=409, headers=cors_headers(request))
    g = await run_db(giveaways_svc.get_giveaway, gid)
    if g and g.get("message_id"):
        try:
            await bot_app.bot.edit_message_reply_markup(chat_id=g["channel"], message_id=g["message_id"],
                                                        reply_markup=None)
        except Exception:
            pass
    return web.json_response({"ok": True, "giveaway": await _giveaway_item(gid)}, headers=cors_headers(request))


async def handle_delete_giveaway(request: web.Request):
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    gid = _giveaway_id(request)
    g = await run_db(giveaways_svc.get_giveaway, gid)
    if not await run_db(giveaways_svc.purge_giveaway, gid):
        return web.json_response({"ok": False, "error": "Активний розіграш спершу скасуйте"},
                                 status=409, headers=cors_headers(request))
    if g and g.get("message_id") and g.get("status") != "cancelled":
        try:
            await bot_app.bot.edit_message_reply_markup(chat_id=g["channel"], message_id=g["message_id"],
                                                        reply_markup=None)
        except Exception:
            pass
    return web.json_response({"ok": True}, headers=cors_headers(request))


async def handle_get_ai_suggestions(request: web.Request):
    denied = await _editor_guard(request)
    if denied is not None:
        return denied
    items = await run_db(ai_suggestions_svc.list_suggestions)
    return web.json_response(
        {"items": items, "job": ai_suggestions_svc.job_state(), "enabled": ai_suggestions_svc.is_enabled()},
        headers=cors_headers(request),
    )


async def handle_generate_ai_suggestions(request: web.Request):
    denied = await _editor_guard(request)
    if denied is not None:
        return denied
    try:
        data = await request.json()
    except Exception:
        data = {}
    result = ai_suggestions_svc.start_generation(data if isinstance(data, dict) else {})
    status = 200 if result.get("ok") else 409 if result.get("error") == "Пошук уже йде" else 400
    return web.json_response(result, status=status, headers=cors_headers(request))


async def handle_ai_suggestion_photos(request: web.Request):
    """Фоновий пошук додаткових фото: `{"ids": [..]}` або порожнє тіло — для всіх нових з неповною галереєю."""
    denied = await _editor_guard(request)
    if denied is not None:
        return denied
    try:
        data = await request.json()
    except Exception:
        data = {}
    result = ai_suggestions_svc.start_photo_search(data if isinstance(data, dict) else {})
    status = 200 if result.get("ok") else 409 if result.get("error") == "Пошук уже йде" else 400
    return web.json_response(result, status=status, headers=cors_headers(request))


async def handle_update_ai_suggestion(request: web.Request):
    denied = await _editor_guard(request)
    if denied is not None:
        return denied
    try:
        sid = int(request.match_info.get("id", 0))
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Невірний запит"}, status=400, headers=cors_headers(request))
    if not isinstance(data, dict):
        data = {}
    # Редактор лише надсилає чернетку товару в слот «На перевірці»; решта змін — тільки адміну
    if isinstance(data.get("product"), dict):
        result = await run_db(ai_suggestions_svc.save_review_draft, sid, data["product"])
        status = 404 if result.get("error") == "Не знайдено" else 400 if not result.get("ok") else 200
        return web.json_response(result, status=status, headers=cors_headers(request))
    denied = await _admin_guard(request)
    if denied is not None:
        # Редактор може лише відхилити відкриту пропозицію (без productId, без інших змін).
        # Явна перевірка ролі: адмін сюди не потрапляє, а решта отримує 403 як раніше.
        editor_auth, _ = resolve_request_user(request, {})
        if is_editor_user(editor_auth) and set(data) == {"status"} and data.get("status") == "rejected":
            result = await run_db(ai_suggestions_svc.reject_open, sid)
            return web.json_response(result, status=200 if result.get("ok") else 400, headers=cors_headers(request))
        return denied
    if any(k in data for k in ("images", "license", "sourceStats")):
        result = await ai_suggestions_svc.update_suggestion(sid, data)
        status = 404 if result.get("error") == "Не знайдено" else 400 if not result.get("ok") else 200
        return web.json_response(result, status=status, headers=cors_headers(request))
    result = await run_db(ai_suggestions_svc.set_status, sid, str(data.get("status") or ""), data.get("productId"))
    if not result.get("ok"):
        status = 404 if result.get("error") == "Не знайдено" else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    return web.json_response(result, headers=cors_headers(request))


async def handle_add_ai_suggestion(request: web.Request):
    """Додати пропозицію вручну (без ключа Claude): JSON у форматі пропозицій ШІ."""
    denied = await _editor_guard(request)
    if denied is not None:
        return denied
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Невірний запит"}, status=400, headers=cors_headers(request))
    result = await ai_suggestions_svc.add_manual(data)
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status, headers=cors_headers(request))


async def handle_delete_ai_suggestion(request: web.Request):
    denied = await _admin_guard(request)
    if denied is not None:
        return denied
    try:
        sid = int(request.match_info.get("id", 0))
    except (TypeError, ValueError):
        sid = 0
    if await run_db(ai_suggestions_svc.delete_suggestion, sid):
        return web.json_response({"ok": True}, headers=cors_headers(request))
    return web.json_response({"ok": False, "error": "Не знайдено"}, status=404, headers=cors_headers(request))


async def handle_get_settings(request: web.Request):
    """Глобальні налаштування магазину для адмін-панелі."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied
    return web.json_response(await run_db(admin_settings), headers=cors_headers(request))


async def handle_update_settings(request: web.Request):
    """Змінити глобальні налаштування (наразі — сумування купонів з акціями)."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response(
            {"ok": False, "error": "Bad JSON"}, status=400, headers=cors_headers(request)
        )

    auth, err = resolve_request_user(request, data)
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    result = await run_db(update_admin_settings, data)
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status, headers=cors_headers(request))


async def handle_create_promotion(request: web.Request):
    """Створити акцію."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        data = await request.json()
        result = await run_db(create_promotion, data)
        status = 201 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_update_promotion(request: web.Request):
    """Оновити акцію або лише перемкнути її активність."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        promo_id = request.match_info.get("id", "")
        data = await request.json()
        if "active" in data and len(data) == 1:
            result = await run_db(set_promotion_active, promo_id, bool(data.get("active")))
        else:
            result = await run_db(update_promotion, promo_id, data)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_delete_promotion(request: web.Request):
    """Видалити акцію."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_admin(request, auth)
    if denied is not None:
        return denied

    try:
        promo_id = request.match_info.get("id", "")
        result = await run_db(delete_promotion, promo_id)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400, headers=cors_headers(request))


async def handle_upload_photo(request: web.Request):
    """Завантажити фото на Cloudinary з повною обробкою помилок"""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_editor(request, auth)
    if denied is not None:
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


async def handle_upload_video(request: web.Request):
    """Завантажити відео товару (mp4/mov/webm) на Cloudinary."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_editor(request, auth)
    if denied is not None:
        return denied
    try:
        form = await request.post()
    except web.HTTPRequestEntityTooLarge:
        return web.json_response(
            {"ok": False, "error": f"❌ Файл занадто великий. Максимум {MAX_UPLOAD_BYTES // 1024 // 1024}MB"},
            status=413, headers=cors_headers(request),
        )
    file_field = form.get("file")
    if not isinstance(file_field, web.FileField):
        return web.json_response({"ok": False, "error": "❌ Файл не знайдено"}, status=400, headers=cors_headers(request))
    content_type = (file_field.content_type or "").lower()
    if content_type not in VIDEO_CONTENT_TYPES:
        return web.json_response(
            {"ok": False, "error": "❌ Потрібне відео mp4, mov або webm"}, status=400, headers=cors_headers(request)
        )
    file_data = file_field.file.read()
    filename = (file_field.filename or "product_video").rsplit(".", 1)[0][:60] or "product_video"
    result = await asyncio.to_thread(upload_video_to_cloudinary_sync, file_data, filename)
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status, headers=cors_headers(request))


async def handle_upload_photo_url(request: web.Request):
    """Завантажити фото на Cloudinary за URL (зручно для анімованих GIF/WebP з інтернету)."""
    auth, err = resolve_request_user(request, {})
    if err is not None:
        return err
    denied = require_editor(request, auth)
    if denied is not None:
        return denied

    try:
        data = await request.json()
        url = str(data.get("url", "")).strip()
        if not url:
            return web.json_response({"ok": False, "error": "❌ Посилання не вказано"}, status=400, headers=cors_headers(request))
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return web.json_response({"ok": False, "error": "❌ Дозволені лише http/https посилання"}, status=400, headers=cors_headers(request))
        if not is_safe_http_url(url):
            return web.json_response({"ok": False, "error": "❌ Небезпечне посилання"}, status=400, headers=cors_headers(request))

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
    if err is not None:
        return err

    code = (data.get("code") or "").strip()
    user_id = auth.get("user_id") or data.get("user_id", 0)
    cart_total = _safe_int(data.get("cart_total"))
    if code and not allow_coupon_check(_safe_int(user_id)):
        return web.json_response(
            {"valid": False, "message": "Забагато спроб. Спробуй за хвилину"},
            status=429,
            headers=cors_headers(request),
        )

    channel_block = False
    coupon_channel = None
    if code:
        needs_sub, coupon_channel = await run_db(coupon_channel_gate, code)
        chat_id = resolve_coupon_channel(coupon_channel)
        if needs_sub and chat_id and not await is_channel_subscriber(user_id, chat_id):
            channel_block = True

    if not code:
        result = {"valid": False, "message": "Введи код купону"}
    elif channel_block:
        result = {"valid": False, "message": channel_sub_required_message(coupon_channel)}
    else:
        # Позиції кошика (для купона на конкретний товар). Ціни клієнтські — лише для показу знижки;
        # при оформленні validate_order_payload перераховує все з каталогу.
        product_prices = None
        if isinstance(data.get("items"), list):
            product_prices = {}
            for it in data["items"][:100]:
                if isinstance(it, dict):
                    pid = _safe_int(it.get("product_id"))
                    if pid > 0:
                        product_prices[pid] = max(product_prices.get(pid, 0), max(0, _safe_int(it.get("price"))))
        result = await run_db(check_coupon, code, user_id, cart_total, product_prices)

    return web.json_response(result, headers=cors_headers(request))


async def reload_products_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not message or user.id != OWNER_ID:
        return
    reload_products_cache(force=True)
    reload_filaments_cache(force=True)
    await send_rich_message(
        context.bot,
        message.chat_id,
        build_cache_reloaded(len(PRODUCTS_CACHE), len(CUSTOM_PRODUCTS_CACHE), len(FILAMENTS_CACHE)),
    )

async def contact_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Відкрити чат з клієнтом без @username (callback-кнопка в замовленні)."""
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("❌ Немає доступу", show_alert=True)
        return
    try:
        uid = int(query.data.split("_", 1)[1])
    except (IndexError, ValueError):
        await query.answer("❌ Невірний запит", show_alert=True)
        return
    name = await run_db(get_user_display_name, uid)
    contact_html = build_contact_card(uid, name)
    await query.answer()
    try:
        await send_rich_message(context.bot, query.from_user.id, contact_html)
    except Exception as e:
        logger.warning("contact_action send failed: %s", e)


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
                if btn.callback_data and btn.callback_data.startswith("contact_"):
                    write_button = btn
                elif btn.url and ("t.me/" in btn.url or btn.url.startswith("tg://user?id=")):
                    write_button = btn

    target_status = {"confirm": "confirmed", "draft": "draft", "cancel": "cancelled"}.get(action)
    if target_status:
        current, _ = await run_db(get_order_with_items, order_id)
        if current and not is_valid_transition(current.get("status") or "new", target_status):
            return  # застаріла кнопка: замовлення вже далі по ланцюжку

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

    if action == "confirm" and order.get("is_test"):
        await _send_test_review_request(order)

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
            uid_for_admin_batch,
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
        if not is_gift_name(i.get("product_name"))
    ]
    admin_html = build_admin_order_notification(
        order_id=int(order["id"]),
        username=order.get("username") or "невідомо",
        user_id=order.get("user_id"),
        first_name=order.get("first_name"),
        items=admin_items,
        total_price=int(order.get("total_price") or 0),
        coupon_code=order.get("coupon_code"),
        discount_amount=int(order.get("discount_amount") or 0),
        coupon_discount=_discount_split(order)[0] or None,
        promotion_discount=_discount_split(order)[1] or None,
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
    is_owner = update.message.from_user.id == OWNER_ID
    if not is_owner and update.message.from_user.id not in config.EDITOR_IDS:
        await send_rich_message(context.bot, update.message.chat_id, build_error_notice(
            "У тебе немає доступу до адмін-панелі", title="Немає доступу"))
        return

    admin_url = (os.environ.get("ADMIN_WEBAPP_URL") or "").strip()
    if not admin_url:
        await send_rich_message(context.bot, update.message.chat_id, build_error_notice(
            "Не задано ADMIN_WEBAPP_URL у .env",
            "Вкажи HTTPS-адресу адмінки, наприклад через ngrok",
            title="Адмін-панель недоступна"))
        return

    # Telegram Web App у кнопці підтримує тільки HTTPS URL.
    if not admin_url.lower().startswith("https://"):
        await send_rich_message(context.bot, update.message.chat_id, build_notice(
            "❌", "Невалідний ADMIN_WEBAPP_URL",
            "Telegram приймає для Web App тільки HTTPS-посилання.",
            f"Зараз: <code>{escape(admin_url)}</code>",
            items=[
                "Локально: підніми <code>ngrok http 8080</code> і встав HTTPS URL у <code>.env</code>",
                "Приклад: <code>ADMIN_WEBAPP_URL=https://&lt;id&gt;.ngrok-free.app/admin/panel</code>",
            ],
        ))
        return

    # Для dev-режиму на ngrok додаємо bypass з токеном.
    # Bypass-токен дає повний доступ — лише власнику; редактор входить через Telegram initData.
    if is_owner and "ngrok" in admin_url and "bypass=admin" not in admin_url:
        sep = "&" if "?" in admin_url else "?"
        admin_url = f"{admin_url}{sep}bypass=admin&token={config.ADMIN_BYPASS_TOKEN}"

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📊 Адмін панель",
            web_app=WebAppInfo(url=admin_url)
        )
    ]])

    await send_rich_message(
        context.bot,
        update.message.chat_id,
        build_admin_panel_hint(),
        reply_markup=markup,
    )
# ─── ЗАПУСК ─────────────────────────────────────────────────

confirmation_messages = app_state.confirmation_messages
admin_channel_messages = app_state.admin_channel_messages
bot_app = app_state.bot_app


def main():
    global bot_app
    config.validate_startup_config()
    init_db()
    try:
        interrupted = broadcasts_svc.recover_interrupted()
        if interrupted:
            logger.warning("Розсилок, перерваних рестартом, позначено failed: %s", interrupted)
    except Exception:
        logger.exception("broadcast recovery failed")
    try:
        stuck = giveaways_svc.recover_interrupted()
        if stuck:
            logger.warning("Розіграшів, перерваних посеред жеребкування: %s", stuck)
    except Exception:
        logger.exception("giveaway recovery failed")
    bootstrap_json_catalog(force=True)
    logger.info(
        "🔧 Режим: VALIDATE_INIT_DATA=%s | товарів: %s + %s custom | філаментів: %s | категорій: %s",
        VALIDATE_INIT_DATA, len(PRODUCTS_CACHE), len(CUSTOM_PRODUCTS_CACHE), len(FILAMENTS_CACHE), len(CATEGORIES_CACHE),
    )

    app_state.bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app = app_state.bot_app
    bot_app.bot_data["owner_id"] = OWNER_ID

    register_telegram_handlers(bot_app, sys.modules[__name__])

    async def run():
        http_app = web.Application(client_max_size=MAX_UPLOAD_REQUEST_BYTES)
        register_http_routes(http_app, sys.modules[__name__])
        runner = web.AppRunner(http_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080)))
        await site.start()
        logger.info("🌐 HTTP сервер запущено  →  порт 8080")

        stop_event = asyncio.Event()

        def _request_shutdown() -> None:
            if not stop_event.is_set():
                logger.info("Отримано сигнал зупинки, graceful shutdown...")
                stop_event.set()

        if sys.platform == "win32":
            signal.signal(signal.SIGINT, lambda _s, _f: _request_shutdown())
            signal.signal(signal.SIGTERM, lambda _s, _f: _request_shutdown())
        else:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, _request_shutdown)

        drop_pending = config.DROP_PENDING_UPDATES
        if drop_pending:
            logger.warning(
                "DROP_PENDING_UPDATES=true — updates під час рестарту будуть відкинуті"
            )

        async with bot_app:
            await bot_app.initialize()
            await bot_app.start()
            # Якщо раніше був увімкнений webhook — скидаємо перед polling.
            await bot_app.bot.delete_webhook(drop_pending_updates=drop_pending)
            # Критично для Render: короткий timeout зменшує "хвіст" старого процесу
            # під час rolling deploy, щоб не ловити довгі Conflict.
            await bot_app.updater.start_polling(
                allowed_updates=TELEGRAM_ALLOWED_UPDATES,
                drop_pending_updates=drop_pending,
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
            await configure_editor_menus(
                bot_app,
                editor_ids=sorted(config.EDITOR_IDS),
                admin_url=(os.environ.get("ADMIN_WEBAPP_URL") or "").strip(),
            )
            review_task = asyncio.create_task(_review_request_loop())
            broadcast_task = asyncio.create_task(_broadcast_scheduler_loop())
            giveaway_task = asyncio.create_task(_giveaway_loop())
            await stop_event.wait()
            review_task.cancel()
            broadcast_task.cancel()
            giveaway_task.cancel()

            logger.info("Зупиняю Telegram polling...")
            await bot_app.updater.stop()

        await runner.cleanup()
        logger.info("Graceful shutdown завершено")

    asyncio.run(run())

if __name__ == '__main__':
    main()
