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
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# =============================================
BOT_TOKEN  = os.environ.get("BOT_TOKEN")
OWNER_ID   = int(os.environ.get("OWNER_ID", "718746623"))
ORDERS_CHAT_ID = int(os.environ.get("ORDERS_CHAT_ID", str(OWNER_ID)))
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://denisposelyanov.github.io/poselyanov3dprint/").strip()
# Для Render краще використовувати шлях на Persistent Disk, напр. /var/data/users.db
DB_FILE    = os.environ.get("DB_FILE", "users.db")
DB_BACKEND = os.environ.get("DB_BACKEND", "sqlite").strip().lower()
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
PRODUCTS_FILE = "products.json"
CUSTOM_PRODUCTS_FILE = "custom_products.json"
FILAMENTS_FILE = "filaments.json"
CATEGORIES_FILE = "categories.json"
# Локально: false (працює без initData). На проді: true
VALIDATE_INIT_DATA = os.environ.get("VALIDATE_INIT_DATA", "false").lower() in ("1", "true", "yes")
# Акція -10% на замовлення від 500 грн: true - увімкнено, false - вимкнено
PROMOTION_ENABLED = os.environ.get("PROMOTION_ENABLED", "true").lower() in ("1", "true", "yes")
CORS_ORIGINS = [
    o.strip() for o in os.environ.get(
        "CORS_ORIGINS",
        "https://denisposelyanov.github.io,http://localhost:8080,http://127.0.0.1:8080,http://localhost:5500,http://127.0.0.1:5500",
    ).split(",") if o.strip()
]


def normalize_origin(value: str) -> str:
    return (value or "").strip().rstrip("/").lower()
# Cloudinary налаштування
cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME", "df5stvc1c"),
    api_key=os.environ.get("CLOUDINARY_API_KEY", "452626753771953"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET", "Tyfsv4pkkdu3bxQyuEeKMVd_dJE")
)
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


def _is_postgres() -> bool:
    return DB_BACKEND == "postgres"


def _sql(query: str) -> str:
    # sqlite3 використовує ?, psycopg використовує %s
    return query if not _is_postgres() else query.replace("?", "%s")


def db_connect(dict_rows: bool = False):
    if _is_postgres():
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL/SUPABASE_DB_URL is required for DB_BACKEND=postgres")
        if psycopg is None:
            raise RuntimeError("psycopg is not installed. Add `psycopg[binary]` to requirements.")
        if dict_rows:
            return psycopg.connect(DATABASE_URL, row_factory=dict_row)
        return psycopg.connect(DATABASE_URL)

    conn = sqlite3.connect(DB_FILE)
    if dict_rows:
        conn.row_factory = sqlite3.Row
    return conn


# ─── ТОВАРИ ─────────────────────────────────────────────────

def load_products_file(path: str):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return []

_products_mtime = 0.0
_custom_mtime = 0.0
PRODUCTS_CACHE = load_products_file(PRODUCTS_FILE)
CUSTOM_PRODUCTS_CACHE = load_products_file(CUSTOM_PRODUCTS_FILE)
_categories_mtime = 0.0
CATEGORIES_CACHE: list[dict] = []

_filaments_mtime = 0.0
FILAMENTS_CACHE: list = []


def load_filaments_file(path: str = FILAMENTS_FILE):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return []


def reload_filaments_cache(force: bool = False):
    """Перезавантажує filaments.json при зміні файлу."""
    global FILAMENTS_CACHE, _filaments_mtime
    fp = Path(FILAMENTS_FILE)
    if not fp.exists():
        return False
    mtime = fp.stat().st_mtime
    if force or mtime != _filaments_mtime:
        FILAMENTS_CACHE = load_filaments_file()
        _filaments_mtime = mtime
        return True
    return False


def save_filaments_to_file():
    """Зберегти філаменти у JSON файл."""
    Path(FILAMENTS_FILE).write_text(json.dumps(FILAMENTS_CACHE, ensure_ascii=False, indent=2), encoding="utf-8")
    reload_filaments_cache(force=True)


def get_filament_by_id(filament_id: str):
    reload_filaments_cache()
    needle = str(filament_id or "").strip()
    if not needle:
        return None
    return next((f for f in FILAMENTS_CACHE if str(f.get("id")) == needle), None)


def update_filament(filament_id: str, data: dict):
    """Оновити існуючий філамент."""
    try:
        filament = get_filament_by_id(filament_id)
        if not filament:
            return {"ok": False, "error": "Філамент не знайдено"}

        if "name" in data:
            name = str(data.get("name", "")).strip()
            if not name:
                return {"ok": False, "error": "Назва філаменту не може бути порожньою"}
            filament["name"] = name
        if "hex" in data:
            hex_value = str(data.get("hex", "")).strip()
            if not hex_value.startswith("#") or len(hex_value) not in (4, 7):
                return {"ok": False, "error": "Колір має бути у HEX-форматі (#RGB або #RRGGBB)"}
            filament["hex"] = hex_value
        if "available" in data:
            filament["available"] = bool(data.get("available"))

        save_filaments_to_file()
        logger.info("🎨 Філамент оновлено: %s", filament.get("id"))
        return {"ok": True, "filament": filament}
    except Exception as e:
        logger.error(f"❌ Помилка оновлення філаменту: {e}")
        return {"ok": False, "error": str(e)}


reload_filaments_cache(force=True)


def load_categories_file(path: str = CATEGORIES_FILE):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return []


def reload_categories_cache(force: bool = False):
    """Перезавантажує categories.json при зміні файлу."""
    global CATEGORIES_CACHE, _categories_mtime
    fp = Path(CATEGORIES_FILE)
    if not fp.exists():
        return False
    mtime = fp.stat().st_mtime
    if force or mtime != _categories_mtime:
        CATEGORIES_CACHE = load_categories_file()
        _categories_mtime = mtime
        return True
    return False


reload_categories_cache(force=True)


def reload_products_cache(force: bool = False):
    """Перезавантажує products.json / custom_products.json якщо файл змінився."""
    global PRODUCTS_CACHE, CUSTOM_PRODUCTS_CACHE, _products_mtime, _custom_mtime
    changed = False
    for path, attr_mtime, attr_cache in (
        (PRODUCTS_FILE, "_products_mtime", "PRODUCTS_CACHE"),
        (CUSTOM_PRODUCTS_FILE, "_custom_mtime", "CUSTOM_PRODUCTS_CACHE"),
    ):
        fp = Path(path)
        if not fp.exists():
            continue
        mtime = fp.stat().st_mtime
        current_mtime = _products_mtime if path == PRODUCTS_FILE else _custom_mtime
        if force or mtime != current_mtime:
            data = load_products_file(path)
            if path == PRODUCTS_FILE:
                PRODUCTS_CACHE = data
                _products_mtime = mtime
            else:
                CUSTOM_PRODUCTS_CACHE = data
                _custom_mtime = mtime
            changed = True
            logger.info("🔄 Оновлено кеш: %s (%s товарів)", path, len(data))
    return changed


def get_product_by_id(product_id: int):
    reload_products_cache()
    if not product_id:
        return None
    for p in PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE:
        if p.get("id") == product_id:
            return p
    return None


# ─── АДМІН ФУНКЦІЇ ──────────────────────────────────────────

def get_all_products():
    """Отримати всі товари (products + custom_products змішані)"""
    reload_products_cache()
    return PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE


def get_all_categories(active_only: bool = True):
    reload_categories_cache()
    categories = sorted(CATEGORIES_CACHE, key=lambda c: c.get("order", 999))
    if active_only:
        return [c for c in categories if c.get("active", True)]
    return categories


def get_category_by_id(category_id: str):
    if not category_id:
        return None
    category_id = str(category_id).strip()
    for category in get_all_categories(active_only=False):
        if category.get("id") == category_id:
            return category
    return None


def is_valid_category_id(category_id: str):
    category = get_category_by_id(category_id)
    return bool(category and category.get("active", True))


def save_products_to_file():
    """Зберегти товари в JSON файли"""
    Path(PRODUCTS_FILE).write_text(json.dumps(PRODUCTS_CACHE, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(CUSTOM_PRODUCTS_FILE).write_text(json.dumps(CUSTOM_PRODUCTS_CACHE, ensure_ascii=False, indent=2), encoding="utf-8")
    reload_products_cache(force=True)


def save_categories_to_file():
    """Зберегти категорії в JSON файл"""
    Path(CATEGORIES_FILE).write_text(json.dumps(CATEGORIES_CACHE, ensure_ascii=False, indent=2), encoding="utf-8")
    reload_categories_cache(force=True)


def add_product(data: dict):
    """Додати новий товар"""
    try:
        category_id = data.get("cat", "toy")
        if not is_valid_category_id(category_id):
            return {"ok": False, "error": "Невірна або неактивна категорія"}

        is_custom = category_id == "custom"
        target_list = CUSTOM_PRODUCTS_CACHE if is_custom else PRODUCTS_CACHE

        if not target_list:
            new_id = 101 if is_custom else 1
        else:
            new_id = max(p.get("id", 0) for p in target_list) + 1

        product = {
            "id": new_id,
            "cat": category_id,
            "emoji": data.get("emoji", "📦"),
            "photos": data.get("photos", []),
            "name": data.get("name", ""),
            "mat": data.get("mat", ""),
            "price": int(data.get("price", 0)),
        }

        if data.get("oldPrice"):
            product["oldPrice"] = int(data["oldPrice"])
        
        product["hot"] = data.get("hot", False)
        product["pinned"] = data.get("pinned", False)
        product["stlLink"] = data.get("stlLink", "")  # Додаємо поле для посилання на STL файл

        if is_custom:
            product["custom_fields"] = data.get("custom_fields", "")
        else:
            product["gift"] = data.get("gift", False)
            if "filamentChoice" in data:
                product["filamentChoice"] = data.get("filamentChoice", True)

        target_list.append(product)
        save_products_to_file()
        logger.info(f"✅ Товар додано: {product['name']} (ID: {new_id})")
        return {"ok": True, "id": new_id}

    except Exception as e:
        logger.error(f"❌ Помилка додавання товару: {e}")
        return {"ok": False, "error": str(e)}


def update_product(product_id: int, data: dict):
    """Редагувати існуючий товар"""
    try:
        product = get_product_by_id(product_id)
        if not product:
            return {"ok": False, "error": "Товар не знайдено"}

        if "cat" in data and not is_valid_category_id(data.get("cat")):
            return {"ok": False, "error": "Невірна або неактивна категорія"}

        current_category = product.get("cat")
        next_category = data.get("cat", current_category)
        is_current_custom = current_category == "custom"
        is_next_custom = next_category == "custom"

        if is_current_custom != is_next_custom:
            source_list = CUSTOM_PRODUCTS_CACHE if is_current_custom else PRODUCTS_CACHE
            destination_list = CUSTOM_PRODUCTS_CACHE if is_next_custom else PRODUCTS_CACHE
            source_list[:] = [p for p in source_list if p.get("id") != product_id]
            destination_list.append(product)

        product["cat"] = next_category

        product.update({
            "emoji": data.get("emoji", product.get("emoji")),
            "photos": data.get("photos", product.get("photos", [])),
            "name": data.get("name", product.get("name")),
            "mat": data.get("mat", product.get("mat")),
            "hot": data.get("hot", product.get("hot", False)),
            "pinned": data.get("pinned", product.get("pinned", False)),
            "stlLink": data.get("stlLink", product.get("stlLink", "")),  # Додаємо поле для посилання на STL файл
        })

        if data.get("price") is not None:
            product["price"] = int(data["price"])

        if data.get("oldPrice"):
            product["oldPrice"] = int(data["oldPrice"])
        elif "oldPrice" in product and not data.get("oldPrice"):
            del product["oldPrice"]

        if not is_next_custom:
            product["gift"] = data.get("gift", product.get("gift", False))
            if "filamentChoice" in data:
                product["filamentChoice"] = data.get("filamentChoice")
            product.pop("custom_fields", None)
        else:
            product["custom_fields"] = data.get("custom_fields", product.get("custom_fields", ""))
            product.pop("gift", None)
            product.pop("filamentChoice", None)

        save_products_to_file()
        logger.info(f"✏️ Товар оновлено: {product['name']} (ID: {product_id})")
        return {"ok": True}

    except Exception as e:
        logger.error(f"❌ Помилка редагування товару: {e}")
        return {"ok": False, "error": str(e)}


def add_category(data: dict):
    """Додати нову категорію"""
    try:
        category_id = str(data.get("id", "")).strip().lower()
        name = str(data.get("name", "")).strip()
        emoji = str(data.get("emoji", "")).strip() or "📦"
        badge_class = str(data.get("badgeClass", "")).strip() or f"category-{category_id}"

        if not category_id or not name:
            return {"ok": False, "error": "ID та назва категорії обов'язкові"}
        if get_category_by_id(category_id):
            return {"ok": False, "error": "Категорія з таким ID вже існує"}

        max_order = max((c.get("order", 0) for c in CATEGORIES_CACHE), default=0)
        category = {
            "id": category_id,
            "name": name,
            "emoji": emoji,
            "badgeClass": badge_class,
            "order": int(data.get("order", max_order + 1)),
            "active": bool(data.get("active", True)),
            "quickSlot": data.get("quickSlot"),
        }
        CATEGORIES_CACHE.append(category)
        save_categories_to_file()
        return {"ok": True, "category": category}
    except Exception as e:
        logger.error(f"❌ Помилка додавання категорії: {e}")
        return {"ok": False, "error": str(e)}


def update_category(category_id: str, data: dict):
    """Оновити категорію"""
    try:
        category = get_category_by_id(category_id)
        if not category:
            return {"ok": False, "error": "Категорія не знайдена"}

        if "id" in data:
            new_id = str(data.get("id", "")).strip().lower()
            if not new_id:
                return {"ok": False, "error": "ID категорії не може бути порожнім"}
            if new_id != category_id and get_category_by_id(new_id):
                return {"ok": False, "error": "Категорія з таким ID вже існує"}
            if new_id != category_id:
                for product in get_all_products():
                    if product.get("cat") == category_id:
                        product["cat"] = new_id
                category["id"] = new_id

        if "name" in data:
            name = str(data.get("name", "")).strip()
            if not name:
                return {"ok": False, "error": "Назва категорії не може бути порожньою"}
            category["name"] = name

        if "emoji" in data:
            category["emoji"] = str(data.get("emoji", "")).strip() or "📦"
        if "badgeClass" in data:
            category["badgeClass"] = str(data.get("badgeClass", "")).strip()
        if "order" in data:
            category["order"] = int(data.get("order", category.get("order", 0)))
        if "active" in data:
            category["active"] = bool(data.get("active"))
        if "quickSlot" in data:
            slot = data.get("quickSlot")
            category["quickSlot"] = int(slot) if slot is not None else None

        save_categories_to_file()
        save_products_to_file()
        return {"ok": True, "category": category}
    except Exception as e:
        logger.error(f"❌ Помилка оновлення категорії: {e}")
        return {"ok": False, "error": str(e)}


def delete_category(category_id: str):
    """Видалити категорію, якщо вона не використовується товарами"""
    try:
        category = get_category_by_id(category_id)
        if not category:
            return {"ok": False, "error": "Категорія не знайдена"}

        products_in_category = [p for p in get_all_products() if p.get("cat") == category_id]
        if products_in_category:
            return {
                "ok": False,
                "error": f"Не можна видалити категорію: у ній є товари ({len(products_in_category)})",
            }

        CATEGORIES_CACHE[:] = [c for c in CATEGORIES_CACHE if c.get("id") != category_id]
        save_categories_to_file()
        return {"ok": True}
    except Exception as e:
        logger.error(f"❌ Помилка видалення категорії: {e}")
        return {"ok": False, "error": str(e)}


def delete_product(product_id: int):
    """Видалити товар"""
    try:
        product = get_product_by_id(product_id)
        if not product:
            return {"ok": False, "error": "Товар не знайдено"}

        is_custom = product.get("cat") == "custom"
        target_list = CUSTOM_PRODUCTS_CACHE if is_custom else PRODUCTS_CACHE

        target_list[:] = [p for p in target_list if p.get("id") != product_id]
        save_products_to_file()
        logger.info(f"🗑️ Товар видалено: {product['name']} (ID: {product_id})")
        return {"ok": True}

    except Exception as e:
        logger.error(f"❌ Помилка видалення товару: {e}")
        return {"ok": False, "error": str(e)}


async def upload_photo_to_cloudinary(file_data: bytes, filename: str = "product_photo"):
    """Завантажити фото на Cloudinary з оптимізацією"""
    try:
        # Перевірка розміру файлу (максимум 25 MB)
        max_size = 25 * 1024 * 1024  # 25 MB
        if len(file_data) > max_size:
            return {"ok": False, "error": f"Файл занадто великий ({len(file_data) / 1024 / 1024:.1f}MB). Максимум 25MB"}
        
        # Оптимізація зображення: стиск до 1000px ширини, якість 85%
        result = cloudinary.uploader.upload(
            file_data,
            folder="poselyanov3dprint",
            resource_type="auto",
            public_id=f"{filename}_{int(datetime.now().timestamp())}",
            # Оптимізація
            width=1000,
            crop="scale",
            quality="auto:good",  # Автоматична якість
            fetch_format="auto"   # Автоматичний формат (webp для сучасних браузерів)
        )
        return {
            "ok": True, 
            "url": result.get("secure_url"),
            "width": result.get("width"),
            "height": result.get("height"),
            "size": result.get("bytes")
        }
    except Exception as e:
        logger.error(f"❌ Помилка завантаження на Cloudinary: {e}")
        return {"ok": False, "error": str(e)}



def validate_telegram_init_data(init_data: str) -> dict | None:
    if not init_data or not BOT_TOKEN:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if calc != received_hash:
            return None
        user = json.loads(parsed.get("user", "{}")) if parsed.get("user") else {}
        return {
            "user_id": user.get("id"),
            "username": user.get("username"),
            "first_name": user.get("first_name"),
        }
    except Exception:
        return None


def cors_headers(request: web.Request) -> dict:
    origin = request.headers.get("Origin", "")
    normalized_origin = normalize_origin(origin)
    normalized_allowed = {normalize_origin(o): o.rstrip("/") for o in CORS_ORIGINS if o.strip()}

    allow = "*"
    if normalized_origin and ("*" in CORS_ORIGINS or normalized_origin in normalized_allowed):
        allow = origin.rstrip("/")
    elif CORS_ORIGINS and CORS_ORIGINS[0] != "*":
        allow = CORS_ORIGINS[0].rstrip("/")
    return {
        "Access-Control-Allow-Origin": allow,
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data",
        "Vary": "Origin",
    }


async def handle_options(request: web.Request):
    """Обробник CORS preflight запитів"""
    return web.Response(status=200, headers=cors_headers(request))


def resolve_request_user(request: web.Request, data: dict) -> tuple[dict | None, web.Response | None]:
    init_data = request.headers.get("X-Telegram-Init-Data") or data.get("init_data") or ""
    auth = validate_telegram_init_data(init_data) if init_data else None

    if VALIDATE_INIT_DATA:
        if not auth or not auth.get("user_id"):
            return None, web.json_response(
                {"ok": False, "error": "invalid_init_data"},
                status=403,
                headers=cors_headers(request),
            )
        return auth, None

    if auth and auth.get("user_id"):
        return auth, None

    uid = data.get("user_id")
    if uid:
        return {
            "user_id": uid,
            "username": (data.get("tg_username") or data.get("username") or "").lstrip("@"),
            "first_name": data.get("first_name") or "",
        }, None

    return {"user_id": 0, "username": "", "first_name": ""}, None


def is_local_dev_origin(request: web.Request) -> bool:
    origin = request.headers.get("Origin", "").strip()
    if not origin:
        return False
    try:
        host = (urlparse(origin).hostname or "").lower()
        return host in {"localhost", "127.0.0.1"}
    except Exception:
        return False


def is_admin_authorized(request: web.Request, auth: dict | None) -> bool:
    # In Telegram context we still require strict owner validation.
    if auth and auth.get("user_id") == OWNER_ID:
        return True
    # For local browser-based development, allow requests from localhost origins.
    if is_local_dev_origin(request):
        return True
    return False


def validate_order_payload(items: list, coupon_code: str | None, user_id: int, client_total: int):
    """Перерахунок суми на сервері. Повертає (ok, result_dict|error_message)."""
    reload_products_cache()
    reload_filaments_cache()
    if not items:
        return False, "Порожній кошик"

    subtotal = 0
    normalized = []

    for raw in items:
        pid = int(raw.get("product_id") or raw.get("id") or 0)
        qty = max(1, min(99, int(raw.get("quantity", 1))))
        product = get_product_by_id(pid)

        if product:
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
                filament_name = str(meta.get("name") or "").strip()

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
        })

    discount = 0
    if coupon_code:
        coupon_result = check_coupon(coupon_code, user_id, subtotal)
        if not coupon_result.get("valid"):
            return False, coupon_result.get("message", "Невалідний купон")
        discount = int(coupon_result.get("discount", 0))

    # Обчислюємо суму після застосування купона
    after_coupon_total = max(0, subtotal - discount)
    
    # Перевіряємо акцію -10% на замовлення від 500 грн
    # Якщо купон застосовано, акція відключається (не підсумовується)
    promotion_discount = 0 if coupon_code else check_promotion(after_coupon_total)
    
    # Загальна сума з урахуванням обох знижок
    server_total = max(0, after_coupon_total - promotion_discount)
    
    if server_total != int(client_total):
        return False, f"Сума не збігається (клієнт {client_total}, сервер {server_total})"

    return True, {
        "items": normalized,
        "subtotal": subtotal,
        "coupon_discount": discount,
        "promotion_discount": promotion_discount,
        "total_price": server_total,
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_coupon_uses_user_id ON coupon_uses(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_blocked ON users(blocked)")
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

    for r in load_filaments_file():
        if _is_postgres():
            conn.execute(
                """
                INSERT INTO filament_colors (id, name, hex, available)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET name = EXCLUDED.name,
                    hex = EXCLUDED.hex,
                    available = EXCLUDED.available
                """,
                (
                    r.get("id"),
                    r.get("name"),
                    r.get("hex") or "",
                    1 if r.get("available") else 0,
                ),
            )
        else:
            conn.execute(
                "INSERT OR REPLACE INTO filament_colors (id, name, hex, available) VALUES (?, ?, ?, ?)",
                (
                    r.get("id"),
                    r.get("name"),
                    r.get("hex") or "",
                    1 if r.get("available") else 0,
                ),
            )

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
def save_order(user_id, username, first_name, items, total_price, comment, gift_product_name=None, coupon_code=None, discount_amount=0):
    conn = db_connect()
    if _is_postgres():
        cursor = conn.execute("""
            INSERT INTO orders (user_id, username, first_name, total_price, comment, gift_product_name, coupon_code, discount_amount, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'new')
            RETURNING id
        """, (user_id, username, first_name, total_price, comment, gift_product_name, coupon_code, discount_amount))
        order_id = cursor.fetchone()[0]
    else:
        cursor = conn.execute("""
            INSERT INTO orders (user_id, username, first_name, total_price, comment, gift_product_name, coupon_code, discount_amount, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')
        """, (user_id, username, first_name, total_price, comment, gift_product_name, coupon_code, discount_amount))
        order_id = cursor.lastrowid
    for item in items:
        fl = (item.get("filament_name") or item.get("filament_id") or "").strip()
        conn.execute(_sql("""
            INSERT INTO order_items (order_id, product_id, product_name, price, quantity, filament)
            VALUES (?, ?, ?, ?, ?, ?)
        """), (
            order_id,
            int(item.get("product_id") or item.get("id") or 0),
            item.get("product_name", "—"),
            int(item.get("price", 0)),
            int(item.get("quantity", 1)),
            fl,
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


def update_order_status(order_id: int, status: str):
    conn = db_connect()
    conn.execute(_sql("UPDATE orders SET status = ? WHERE id = ?"), (status, order_id))
    conn.commit()
    conn.close()

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

    args = context.args
    reply = update.message.reply_to_message

    if not reply:
        await update.message.reply_text(
            "Відповідай (reply) на повідомлення командою:\n"
            "`/broadcast` — усім\n"
            "`/broadcast <product_id>` — усім + кнопка товару\n"
            "`/broadcast u:<user_id>` — конкретному юзеру\n"
            "`/broadcast u:<user_id> <product_id>` — конкретному юзеру + кнопка товару",
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
        except Exception as e:
            logger.warning(f"Broadcast error for {user_id}: {e}")
            if "bot was blocked" in str(e) or "user is deactivated" in str(e):
                set_blocked(user_id, True)
            failed += 1

    audience = f"user `{target_user_id}`" if target_user_id else "всіх"
    await update.message.reply_text(
        f"📨 Розсилка завершена ({audience})\n✅ Надіслано: *{sent}*\n❌ Помилок/блокувань: *{failed}*",
        parse_mode='Markdown'
    )

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

    status_labels = {
        'new':       ('🕐', 'Очікує підтвердження', 'Денис скоро зв\'яжеться з тобою'),
        'confirmed': ('✅', 'Підтверджено', 'Замовлення прийнято у роботу!'),
        'cancelled': ('❌', 'Скасовано', 'Якщо є питання — напиши Денису'),
        'draft':     ('📝', 'Під питанням', 'Денис уточнює деталі замовлення'),
    }

    months = ['січ','лют','бер','кві','тра','чер','лип','сер','вер','жов','лис','гру']
    icon, label, hint = status_labels.get(order['status'], ('❔', order['status'], ''))

    try:
        dt = datetime.strptime(order['ordered_at'][:10], "%Y-%m-%d")
        date_fmt = f"{dt.day} {months[dt.month-1]} {dt.year}"
    except Exception:
        date_fmt = order['ordered_at'][:10] if order['ordered_at'] else '—'

    text = (
        f"📦 <b>Останнє замовлення #{order['id']}</b>\n"
        f"📅 {date_fmt}  ·  💰 {order['total_price']} ₴\n\n"
        f"{icon} <b>{label}</b>\n"
        f"<i>{hint}</i>"
    )
    await update.message.reply_text(text, parse_mode='HTML')


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

    status_labels = {
        'new':       ('🕐', 'Очікує'),
        'confirmed': ('✅', 'Підтверджено'),
        'cancelled': ('❌', 'Скасовано'),
        'draft':     ('📝', 'Під питанням'),
    }

    first_name = update.message.from_user.first_name
    text = f"📦 <b>Замовлення {first_name}:</b>\n"
    text += "─" * 22 + "\n\n"

    for o in orders:
        icon, label = status_labels.get(o['status'], ('❔', o['status']))
        date_raw = o['ordered_at'] or ''
        date = date_raw[:10] if date_raw else '—'

        # Дата у форматі "16 трав 2026"
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            months = ['січ','лют','бер','кві','тра','чер','лип','сер','вер','жов','лис','гру']
            date_fmt = f"{dt.day} {months[dt.month-1]} {dt.year}"
        except Exception:
            date_fmt = date

        text += f"{icon} <b>Замовлення #{o['id']}</b>  ·  {date_fmt}\n"

        # Товари
        items = conn.execute(_sql("""
            SELECT product_name, price, quantity, filament
            FROM order_items
            WHERE order_id = ?
        """), (o['id'],)).fetchall()

        for item in items:
            name = item['product_name']
            qty = item['quantity']
            price = item['price']
            fl = (item['filament'] or '').strip()
            if price == 0:
                # подарунок (вже містить emoji 🎁)
                text += f"   {name}\n"
            else:
                subtotal = price * qty
                qty_str = f" × {qty}" if qty > 1 else ""
                fl_str = f" · 🎨 {fl}" if fl else ""
                text += f"   • {name}{qty_str} — {subtotal} ₴{fl_str}\n"

        # Купон
        if o['coupon_code'] and o['discount_amount']:
            original = (o['total_price'] or 0) + (o['discount_amount'] or 0)
            text += f"   🏷️ Купон <code>{o['coupon_code']}</code>: −{o['discount_amount']} ₴\n"
            text += f"   💰 <b>Разом: {original} → {o['total_price']} ₴</b>\n"
        else:
            text += f"   💰 <b>Разом: {o['total_price']} ₴</b>\n"

        # Статус рядком
        text += f"   {icon} {label}\n"

        # Коментар
        if o['comment']:
            text += f"   📝 <i>{o['comment']}</i>\n"

        text += "\n"

    conn.close()

    # Підказка
    total_orders = len(orders)
    text += "─" * 22 + "\n"
    text += f"Показано останніх замовлень: <b>{total_orders}</b>"

    await update.message.reply_text(text, parse_mode='HTML')

# Команда для управління купонами, яка дозволяє адміністраторам створювати, переглядати, активувати і деактивувати купони зі знижками. Вона підтримує різні формати знижок (відсоткові і фіксовані), а також додаткові параметри для обмеження використання купонів (мінімальна сума замовлення, максимальна кількість використань, використання одним користувачем і термін дії). Команда має підкоманди для кожної операції (add, list, disable, enable) і відповідає повідомленнями з результатами операцій у форматі Markdown.
async def coupon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != 718746623:
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

    # Формуємо назву для збереження в БД (всі товари через кому)
    product_name = ', '.join(i.get('product_name', '—') for i in items)

    # Зберігаємо замовлення в базі даних і отримуємо його ID для подальшого використання в логах і кнопках
    order_id = save_order(user_id or 0, username, first_name, items, total_price, comment, gift, coupon_code, total_discount)

    # Логування нового замовлення з інформацією про ID замовлення, назву товару, загальну суму, інформацію про купон і знижку (якщо є) і ім'я користувача. Це дозволяє відстежувати всі замовлення, які надходять через веб-додаток, і отримувати повну інформацію про них для подальшої обробки.
    coupon_info = f"  🏷️ {coupon_code} −{coupon_discount}₴" if coupon_code else ""
    promotion_info = f"  🔥 Акція −{promotion_discount}₴" if promotion_discount > 0 else ""
    logger.info(f"📦 ЗАМОВЛЕННЯ #{order_id}  {product_name}  {total_price}₴{coupon_info}{promotion_info}  від {username}")

    # Підтвердження клієнту
    
    if user_id:
        try:
            now = datetime.now()
            existing = confirmation_messages.get(user_id)
            footer = "\n[Денис](https://t.me/denisposelyanov) зв'яжеться з тобою найближчим часом 🙌"

            # Формуємо текст для підтвердження клієнту
            items_lines = '\n'.join(
                f"  • {i.get('product_name','—')} × {i.get('quantity',1)}"
                + (f" · 🎨 {i['filament_name']}" if i.get('filament_name') else "")
                for i in items
            )
            line = f"📦 *Товари:*\n{items_lines}\n"
            gift = data.get('gift')

            # Якщо замовлення було оформлено з купоном або акцією, додаємо інформацію про знижки в текст підтвердження для клієнта
            if total_discount > 0:
                original_price = total_price + total_discount
                discount_lines = []
                if coupon_discount > 0 and coupon_code:
                    discount_lines.append(f"🏷️ *Купон {coupon_code}:* −{coupon_discount} ₴")
                if promotion_discount > 0:
                    discount_lines.append(f"🔥 *Акція -10%:* −{promotion_discount} ₴")
                line += "\n" + "\n".join(discount_lines)
                line += f"\n💰 *Разом: {original_price} → {total_price} ₴*"
            else:
                line += f"\n💰 *Разом: {total_price} ₴*"
            
            if gift:
                line += f"\n🎁 Подарунок: {gift} — *безкоштовно*"
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

    # Формуємо клікабельні назви товарів для адмін-повідомлення
    # (відкриває конкретний товар у боті через deep-link startapp=product_<id>)
    items_text = ''
    bot_link_base = "https://t.me/poselyanov3dprint_bot?startapp=product_"

    def _product_link(name: str, product_id: int | None):
        safe_name = html.escape(name or "—")
        if product_id:
            return f'<a href="{bot_link_base}{product_id}">{safe_name}</a>'
        return safe_name

    for i in items:
        product_name = i.get('product_name', '—')
        product_id = int(i.get('product_id') or 0)
        linked_name = _product_link(product_name, product_id)
        line = f"  • {linked_name} × {i.get('quantity',1)} — {i.get('price',0) * i.get('quantity',1)} ₴"
        if i.get('filament_name'):
            line += f" · 🎨 {html.escape(i['filament_name'])}"
        if i.get('customValue'):
            line += f" <i>{html.escape(i['customValue'])}</i>"
        items_text += line + '\n'

    # Формуємо текст замовлення для власника, включаючи інформацію про товари, загальну суму, коментар клієнта, подарунок і купон зі знижкою, якщо вони є. Це дозволяє власнику отримувати повну інформацію про замовлення і приймати рішення про його обробку.
    owner_text = (
        f"🔔 <b>НОВЕ ЗАМОВЛЕННЯ #{order_id}</b>\n\n"
        f"👤 Від: {username}\n\n"
        f"📦 <b>Товари:</b>\n{items_text}\n"
    )
    if total_discount > 0:
        original_price = total_price + total_discount
        discount_lines = []
        if coupon_discount > 0 and coupon_code:
            discount_lines.append(f"🏷️ Купон: <b>{coupon_code}</b> −{coupon_discount} ₴")
        if promotion_discount > 0:
            discount_lines.append(f"🔥 Акція -10%: −{promotion_discount} ₴")
        owner_text += "\n".join(discount_lines) + "\n"
        owner_text += f"💰 <b>Разом: {original_price} → {total_price} ₴</b>\n"
    else:
        owner_text += f"💰 <b>Разом: {total_price} ₴</b>\n"

    # Якщо замовлення було оформлено з купоном, додаємо інформацію про купон і знижку в текст замовлення для власника, щоб він бачив, який купон був використаний і яку знижку він надав. Це допомагає власнику краще розуміти замовлення і приймати рішення про його обробку.
    gift = data.get('gift')

    # Якщо є подарунок, додаємо клікабельне посилання (якщо знайдено відповідний товар)
    if gift:
        gift_name = str(gift).strip()
        gift_product = next(
            (p for p in (PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE)
             if str(p.get("name", "")).strip().lower() == gift_name.lower()),
            None,
        )
        if gift_product and gift_product.get("id"):
            gift_link = f'<a href="{bot_link_base}{int(gift_product.get("id"))}">{html.escape(gift_name)}</a>'
            owner_text += f"🎁 Подарунок: {gift_link} — безкоштовно\n"
        else:
            owner_text += f"🎁 Подарунок: {html.escape(gift_name)} — безкоштовно\n"

    # Якщо клієнт залишив коментар, додаємо його в текст замовлення для власника
    if comment:
        owner_text += f"📝 Коментар: <i>{html.escape(comment)}</i>\n"

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
            chat_id=ORDERS_CHAT_ID,
            text=owner_text,
            parse_mode='HTML',
            reply_markup=markup
        )
        logger.info(f"✅ Надіслано в чат замовлень: {ORDERS_CHAT_ID}")
    except Exception as e:
        logger.error(f"❌ Помилка надсилання в канал: {e}")

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
    """Обслуговування статичних файлів"""
    file_path = request.match_info.get('path', '')
    
    # Безпека: не дозволяємо доступ до файлів за межами поточної директорії
    if '..' in file_path or file_path.startswith('/'):
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
    """Отримати HTML адмін панелі"""
    init_data = (
        request.query.get("initData") or 
        request.headers.get("X-Telegram-Init-Data") or 
        request.cookies.get("tgInitData", "") or
        ""
    )

    # Перевіряємо, чи користувач є адміном
    try:
        html = Path("admin-panel.html").read_text(encoding="utf-8")
        return web.Response(text=html, content_type='text/html', headers=cors_headers(request))
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
    if VALIDATE_INIT_DATA and not is_admin_authorized(request, auth):
        return web.json_response({"error": "Forbidden"}, status=403, headers=cors_headers(request))
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
    if VALIDATE_INIT_DATA and not is_admin_authorized(request, auth):
        return web.json_response({"error": "Forbidden"}, status=403, headers=cors_headers(request))

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
    if VALIDATE_INIT_DATA and not is_admin_authorized(request, auth):
        return web.json_response({"error": "Forbidden"}, status=403, headers=cors_headers(request))

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
    if VALIDATE_INIT_DATA and not is_admin_authorized(request, auth):
        return web.json_response({"error": "Forbidden"}, status=403, headers=cors_headers(request))

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
    if VALIDATE_INIT_DATA and not is_admin_authorized(request, auth):
        return web.json_response({"error": "Forbidden"}, status=403, headers=cors_headers(request))

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
    if VALIDATE_INIT_DATA and not is_admin_authorized(request, auth):
        return web.json_response({"error": "Forbidden"}, status=403, headers=cors_headers(request))

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
    if VALIDATE_INIT_DATA and not is_admin_authorized(request, auth):
        return web.json_response({"error": "Forbidden"}, status=403, headers=cors_headers(request))

    try:
        product_id = int(request.match_info.get('id', 0))
        result = delete_product(product_id)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=cors_headers(request))


async def handle_upload_photo(request: web.Request):
    """Завантажити фото на Cloudinary з повною обробкою помилок"""
    auth, err = resolve_request_user(request, {})
    if err:
        return err
    # Локально без валідації дозволяємо без перевірки OWNER_ID
    if VALIDATE_INIT_DATA and not is_admin_authorized(request, auth):
        return web.json_response({"ok": False, "error": "❌ Забачено доступу (тільки адмін)"}, status=403, headers=cors_headers(request))

    try:
        reader = await request.multipart()
        field = await reader.next()

        if not field or field.name != 'file':
            return web.json_response(
                {"ok": False, "error": "❌ Файл не знайдено"}, 
                status=400, 
                headers=cors_headers(request)
            )

        file_data = await field.read()
        filename = (field.filename or f"product_{int(datetime.now().timestamp())}").rsplit('.', 1)[0]

        # Перевіримо, що це зображення
        content_type = field.headers.get('Content-Type', '')
        if not content_type or not content_type.startswith('image/'):
            return web.json_response(
                {"ok": False, "error": f"❌ Не дозволений тип файлу: {content_type}. Тільки зображення"}, 
                status=400, 
                headers=cors_headers(request)
            )

        result = await upload_photo_to_cloudinary(file_data, filename)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status, headers=cors_headers(request))
        
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

# Новий HTTP хендлер для перевірки купона, який приймає код купона, ID користувача і загальну суму кошика, виконує всі необхідні перевірки валідності купона і повертає результат у вигляді JSON-об'єкта з інформацією про валідність купона, тип і значення знижки, а також повідомлення для клієнта. Цей хендлер дозволяє веб-додатку динамічно перевіряти купони при оформленні замовлення і відображати відповідні повідомлення клієнту.
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
    if update.effective_user.id != 718746623:
        return
    reload_products_cache(force=True)
    reload_filaments_cache(force=True)
    await update.message.reply_text(
        f"✅ Кеш оновлено: products={len(PRODUCTS_CACHE)}, custom={len(CUSTOM_PRODUCTS_CACHE)}, filaments={len(FILAMENTS_CACHE)}"
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

    # Оновлюємо текст повідомлення зі статусом.
    # Важливо: для confirm/draft зберігаємо HTML (і всі посилання на товари),
    # а для cancel спеціально прибираємо HTML-посилання з тексту.
    try:
        if action == "cancel":
            # plain text без HTML-лінків
            base_text = query.message.text or ""
            new_text = base_text + f"\n\nСтатус: {label}"
            await query.edit_message_text(
                text=new_text,
                reply_markup=markup
            )
        else:
            # зберігаємо HTML-розмітку оригінального повідомлення
            base_html = getattr(query.message, "text_html", None) or html.escape(query.message.text or "")
            new_html = base_html + f"\n\n<b>Статус: {html.escape(label)}</b>"
            await query.edit_message_text(
                text=new_html,
                parse_mode='HTML',
                reply_markup=markup
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

confirmation_messages = {}  # {user_id: {"message_id": int, "text": str, "time": datetime}}

def main():
    global bot_app
    init_db()
    reload_products_cache(force=True)
    reload_filaments_cache(force=True)
    reload_categories_cache(force=True)
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
        http_app = web.Application()
        # API маршрути (перші, щоб не перехоплювалися статичними файлами)
        http_app.router.add_post('/order', handle_order)
        http_app.router.add_route('OPTIONS', '/order', handle_options)
        http_app.router.add_post('/check_coupon', handle_check_coupon)
        http_app.router.add_route('OPTIONS', '/check_coupon', handle_options)
        http_app.router.add_route('OPTIONS', '/api/{tail:.*}', handle_options)
        # Адмін API
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
        http_app.router.add_post('/api/upload-photo', handle_upload_photo)
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
                scope=BotCommandScopeChat(chat_id=718746623)  # тільки ти
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