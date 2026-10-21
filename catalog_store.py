"""Product catalog storage: JSON files or Postgres/Supabase."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import config
from db_core import db_connect, is_postgres, sql

logger = logging.getLogger(__name__)

_json_lock = asyncio.Lock()
_products_mtime = 0.0
_custom_mtime = 0.0
_categories_mtime = 0.0
_filaments_mtime = 0.0

PRODUCTS_CACHE: list[dict] = []
CUSTOM_PRODUCTS_CACHE: list[dict] = []
CATEGORIES_CACHE: list[dict] = []
FILAMENTS_CACHE: list[dict] = []

# Статуси товару: active — у магазині, hidden — прихований, archive — в архіві (м'яке видалення).
PRODUCT_STATUSES = ("active", "hidden", "archive")
# JSON-бекенд: приховані й архівні товари (стандартні й «індивідуальні») лежать окремо від кешів вітрини,
# щоб PRODUCTS_CACHE / CUSTOM_PRODUCTS_CACHE і далі містили лише активні товари.
INACTIVE_PRODUCTS_CACHE: list[dict] = []
INACTIVE_CUSTOM_PRODUCTS_CACHE: list[dict] = []


def _replace_cache_list(cache: list, items: list) -> None:
    """Keep imported references valid (e.g. bot.py `from catalog_store import FILAMENTS_CACHE`)."""
    cache.clear()
    cache.extend(items)


def use_catalog_db() -> bool:
    backend = config.CATALOG_BACKEND
    return backend in ("postgres", "supabase") or (
        backend not in ("json",) and is_postgres()
    )


def _atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def load_products_file(path: str):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return []


def load_filaments_file(path: str = config.FILAMENTS_FILE):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return []


def load_categories_file(path: str = config.CATEGORIES_FILE):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return []


def _serialize_custom_fields(value) -> str:
    """Рядок зберігаємо як є, список чи обʼєкт — як JSON; порожнє значення — порожній рядок (не '""')."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


# Опис і примітка в картці товару (REDESIGN.md, розд. 3.4 — «Згорнутий»)
DESCRIPTION_MAX = 3000
NOTE_MAX = 300
VIDEO_URL_MAX = 500
# Персоналізація індивідуального товару: підпис поля, ліміт символів, підказка, плейсхолдер, верхній регістр
PERS_LABEL_MAX = 60
PERS_TEXT_MAX = 200
PERS_LIMIT_MAX = 200


def clean_text(value, max_len: int) -> str:
    """Текст з адмінки: без \\r, без пробілів по краях, не довший за max_len. Абзаци зберігаються."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text[:max_len].rstrip()


def clean_video_url(value) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if not url.startswith("https://") or len(url) > VIDEO_URL_MAX or any(ch in url for ch in ' "<>'):
        raise ValueError("Посилання на відео має бути https:// (mp4)")
    return url


def normalize_custom_fields(value):
    """Поле персоналізації: рядок (лише підпис — старий формат) або {label, max, hint, placeholder, upper}.

    Без підпису поля немає ("" ). Якщо задано лише підпис — зберігаємо рядком,
    щоб старий формат лишався зворотно сумісним.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw.startswith("{"):
            return raw[:PERS_LABEL_MAX]
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return raw[:PERS_LABEL_MAX]
    if not isinstance(value, dict):
        return ""
    label = clean_text(value.get("label"), PERS_LABEL_MAX)
    if not label:
        return ""
    out: dict = {"label": label}
    try:
        max_len = int(value.get("max") or 0)
    except (TypeError, ValueError):
        max_len = 0
    if max_len > 0:
        out["max"] = min(max_len, PERS_LIMIT_MAX)
    for key in ("hint", "placeholder"):
        text = clean_text(value.get(key), PERS_TEXT_MAX)
        if text:
            out[key] = text
    if value.get("upper") in (True, 1, "1", "true", "on"):
        out["upper"] = True
    return out if len(out) > 1 else label


def _apply_product_texts(product: dict, data: dict) -> None:
    """Опис, примітка й відео: порожнє значення прибирає поле з товару."""
    for key, max_len in (("description", DESCRIPTION_MAX), ("note", NOTE_MAX)):
        if key in data:
            text = clean_text(data.get(key), max_len)
            if text:
                product[key] = text
            else:
                product.pop(key, None)
    if "video" in data:
        url = clean_video_url(data.get("video"))
        if url:
            product["video"] = url
        else:
            product.pop("video", None)


def _row_status(row: dict) -> str:
    """Статус рядка БД / JSON-товару; старі записи без `status` рахуються за `active`."""
    status = str(row.get("status") or "").strip().lower()
    if status in PRODUCT_STATUSES:
        # Запис, знятий старим кодом (active=false без зміни status), лишається архівним.
        if status == "active" and row.get("active") is False:
            return "archive"
        return status
    return "active" if row.get("active", True) else "archive"


def product_status(product: dict | None) -> str:
    return _row_status(product or {})


def _row_to_product(row: dict) -> dict:
    photos = row.get("photos") or []
    if isinstance(photos, str):
        photos = json.loads(photos)
    custom_fields = row.get("custom_fields")
    if isinstance(custom_fields, str) and custom_fields.strip() in ('""', "null"):
        custom_fields = ""
    if isinstance(custom_fields, str) and custom_fields.startswith(("[", "{")):
        try:
            custom_fields = json.loads(custom_fields)
        except json.JSONDecodeError:
            pass

    p = {
        "id": int(row["id"]),
        "cat": row.get("category_id") or row.get("cat") or "toy",
        "name": row.get("name") or "",
        "emoji": row.get("emoji") or "📦",
        "mat": row.get("mat") or "",
        "price": int(row.get("price") or 0),
        "photos": photos,
        "hot": bool(row.get("hot")),
        "pinned": bool(row.get("pinned")),
        "stlLink": row.get("stl_link") or row.get("stlLink") or "",
    }
    if row.get("old_price") or row.get("oldPrice"):
        p["oldPrice"] = int(row.get("old_price") or row.get("oldPrice"))
    if row.get("contract_price") or row.get("contractPrice"):
        p["contractPrice"] = True
    if row.get("gift"):
        p["gift"] = bool(row.get("gift"))
    if row.get("filament_choice") is not None or row.get("filamentChoice") is not None:
        p["filamentChoice"] = bool(row.get("filament_choice", row.get("filamentChoice", True)))
    if row.get("luminous_filament_choice") or row.get("luminousFilamentChoice"):
        p["luminousFilamentChoice"] = bool(row.get("luminous_filament_choice", row.get("luminousFilamentChoice")))
    if custom_fields:
        p["custom_fields"] = custom_fields
    for key in ("description", "note", "video"):
        value = (row.get(key) or "").strip()
        if value:
            p[key] = value
    status = _row_status(row)
    if status != "active":
        p["status"] = status
    created_at = row.get("created_at") or row.get("createdAt")
    if created_at:
        # Для добірки «Новинки» і бейджа «Новинка» на вітрині.
        p["createdAt"] = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at)
    return p


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_to_category(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "emoji": row.get("emoji") or "📦",
        "badgeClass": row.get("badge_class") or row.get("badgeClass") or f"category-{row['id']}",
        "order": int(row.get("sort_order") if row.get("sort_order") is not None else row.get("order", 999)),
        "active": bool(row.get("active", True)),
        "quickSlot": row.get("quick_slot") if row.get("quick_slot") is not None else row.get("quickSlot"),
    }


def _row_to_filament(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "hex": row.get("hex") or "",
        "available": bool(row.get("available")),
    }


def init_catalog_tables(conn) -> None:
    if not is_postgres():
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            emoji TEXT,
            badge_class TEXT,
            sort_order INTEGER DEFAULT 0,
            active BOOLEAN DEFAULT true,
            quick_slot INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id BIGSERIAL PRIMARY KEY,
            category_id TEXT REFERENCES categories(id),
            name TEXT NOT NULL,
            emoji TEXT,
            mat TEXT,
            price INTEGER DEFAULT 0,
            old_price INTEGER,
            photos JSONB DEFAULT '[]',
            custom_fields TEXT DEFAULT '',
            hot BOOLEAN DEFAULT false,
            gift BOOLEAN DEFAULT false,
            filament_choice BOOLEAN DEFAULT true,
            luminous_filament_choice BOOLEAN DEFAULT false,
            pinned BOOLEAN DEFAULT false,
            stl_link TEXT DEFAULT '',
            contract_price BOOLEAN DEFAULT false,
            is_custom BOOLEAN DEFAULT false,
            active BOOLEAN DEFAULT true,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            description TEXT DEFAULT '',
            note TEXT DEFAULT '',
            video TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS filaments (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            hex TEXT,
            available BOOLEAN DEFAULT true,
            sort_order INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_active ON products(active)")
    conn.execute(
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS luminous_filament_choice BOOLEAN DEFAULT false"
    )
    # Без DEFAULT у ADD COLUMN: старі товари лишаються без дати, а не стають «новинками» всі разом.
    conn.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ")
    conn.execute("ALTER TABLE products ALTER COLUMN created_at SET DEFAULT NOW()")
    # Етап 2 редизайну: опис і примітка в картці товару, відео в галереї
    for column in ("description", "note", "video"):
        conn.execute(f"ALTER TABLE products ADD COLUMN IF NOT EXISTS {column} TEXT DEFAULT ''")
    # Етап 5 редизайну: статус товару active/hidden/archive замість лише `active`.
    # Спершу без DEFAULT, щоб відрізнити старі рядки (NULL) і заповнити їх з `active`; повторний запуск нічого не міняє.
    conn.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS status TEXT")
    conn.execute("UPDATE products SET status = CASE WHEN active THEN 'active' ELSE 'archive' END WHERE status IS NULL")
    conn.execute("ALTER TABLE products ALTER COLUMN status SET DEFAULT 'active'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_status ON products(status)")
    # Раніше порожнє поле зберігалося як json.dumps("") == '""' і показувалося в адмінці як ✍️ "".
    conn.execute("UPDATE products SET custom_fields = '' WHERE custom_fields IN ('\"\"', 'null')")
    sync_products_id_sequence(conn)


def sync_products_id_sequence(conn) -> None:
    """Align products_id_seq after imports that insert explicit id values."""
    if not is_postgres():
        return
    conn.execute(
        "SELECT setval(pg_get_serial_sequence('products', 'id'), "
        "COALESCE((SELECT MAX(id) FROM products), 1), true)"
    )


def sync_filament_colors_table(conn, filament: dict) -> None:
    fid = filament.get("id")
    if not fid:
        return
    if is_postgres():
        conn.execute(
            """
            INSERT INTO filament_colors (id, name, hex, available)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET name = EXCLUDED.name, hex = EXCLUDED.hex, available = EXCLUDED.available
            """,
            (fid, filament.get("name"), filament.get("hex") or "", 1 if filament.get("available") else 0),
        )
    else:
        conn.execute(
            sql("INSERT OR REPLACE INTO filament_colors (id, name, hex, available) VALUES (?, ?, ?, ?)"),
            (fid, filament.get("name"), filament.get("hex") or "", 1 if filament.get("available") else 0),
        )


def reload_filaments_cache(force: bool = False):
    global FILAMENTS_CACHE, _filaments_mtime
    if use_catalog_db():
        conn = db_connect(dict_rows=True)
        rows = conn.execute(
            "SELECT id, name, hex, available FROM filaments ORDER BY sort_order, id"
        ).fetchall()
        conn.close()
        _replace_cache_list(FILAMENTS_CACHE, [_row_to_filament(dict(r)) for r in rows])
        return True

    fp = Path(config.FILAMENTS_FILE)
    if not fp.exists():
        return False
    mtime = fp.stat().st_mtime
    if force or mtime != _filaments_mtime:
        _replace_cache_list(FILAMENTS_CACHE, load_filaments_file())
        _filaments_mtime = mtime
        return True
    return False


def reload_categories_cache(force: bool = False):
    global CATEGORIES_CACHE, _categories_mtime
    if use_catalog_db():
        conn = db_connect(dict_rows=True)
        rows = conn.execute(
            "SELECT id, name, emoji, badge_class, sort_order, active, quick_slot FROM categories ORDER BY sort_order"
        ).fetchall()
        conn.close()
        _replace_cache_list(CATEGORIES_CACHE, [_row_to_category(dict(r)) for r in rows])
        return True

    fp = Path(config.CATEGORIES_FILE)
    if not fp.exists():
        return False
    mtime = fp.stat().st_mtime
    if force or mtime != _categories_mtime:
        _replace_cache_list(CATEGORIES_CACHE, load_categories_file())
        _categories_mtime = mtime
        return True
    return False


def reload_products_cache(force: bool = False):
    global PRODUCTS_CACHE, CUSTOM_PRODUCTS_CACHE, _products_mtime, _custom_mtime
    if use_catalog_db():
        conn = db_connect(dict_rows=True)
        rows = conn.execute(
            "SELECT * FROM products WHERE status = 'active' AND active = true ORDER BY id"
        ).fetchall()
        conn.close()
        standard, custom = [], []
        for r in rows:
            p = _row_to_product(dict(r))
            if r.get("is_custom") or p.get("cat") == "custom":
                custom.append(p)
            else:
                standard.append(p)
        _replace_cache_list(PRODUCTS_CACHE, standard)
        _replace_cache_list(CUSTOM_PRODUCTS_CACHE, custom)
        return True

    changed = False
    for path, mtime_attr, cache_name in (
        (config.PRODUCTS_FILE, "_products_mtime", "PRODUCTS_CACHE"),
        (config.CUSTOM_PRODUCTS_FILE, "_custom_mtime", "CUSTOM_PRODUCTS_CACHE"),
    ):
        fp = Path(path)
        if not fp.exists():
            continue
        mtime = fp.stat().st_mtime
        current = _products_mtime if path == config.PRODUCTS_FILE else _custom_mtime
        if force or mtime != current:
            data = load_products_file(path)
            active = [p for p in data if product_status(p) == "active"]
            inactive = [p for p in data if product_status(p) != "active"]
            if path == config.PRODUCTS_FILE:
                _replace_cache_list(PRODUCTS_CACHE, active)
                _replace_cache_list(INACTIVE_PRODUCTS_CACHE, inactive)
                globals()["_products_mtime"] = mtime
            else:
                _replace_cache_list(CUSTOM_PRODUCTS_CACHE, active)
                _replace_cache_list(INACTIVE_CUSTOM_PRODUCTS_CACHE, inactive)
                globals()["_custom_mtime"] = mtime
            changed = True
            logger.info("Оновлено кеш: %s (%s товарів, неактивних %s)", path, len(data), len(inactive))
    return changed


def bootstrap_json_catalog(force: bool = False):
    reload_products_cache(force=force)
    reload_filaments_cache(force=force)
    reload_categories_cache(force=force)


def get_product_by_id(product_id: int):
    if not product_id:
        return None
    if use_catalog_db():
        conn = db_connect(dict_rows=True)
        row = conn.execute(sql("SELECT * FROM products WHERE id = ? AND status = 'active' AND active = true"), (product_id,)).fetchone()
        conn.close()
        return _row_to_product(dict(row)) if row else None
    reload_products_cache()
    for p in PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE:
        if p.get("id") == product_id:
            return p
    return None


def get_product_any(product_id: int):
    """Товар будь-якого статусу (для адмінки): активний, прихований чи архівний."""
    if not product_id:
        return None
    if use_catalog_db():
        conn = db_connect(dict_rows=True)
        try:
            row = conn.execute(sql("SELECT * FROM products WHERE id = ?"), (product_id,)).fetchone()
        finally:
            conn.close()
        return _row_to_product(dict(row)) if row else None
    reload_products_cache()
    for p in PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE + INACTIVE_PRODUCTS_CACHE + INACTIVE_CUSTOM_PRODUCTS_CACHE:
        if p.get("id") == product_id:
            return p
    return None


def get_admin_products() -> list[dict]:
    """Усі товари всіх статусів для таблиці в адмінці; у кожного є `status`. Копії, кеш не чіпаємо."""
    if use_catalog_db():
        conn = db_connect(dict_rows=True)
        try:
            rows = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
        finally:
            conn.close()
        products = [_row_to_product(dict(r)) for r in rows]
    else:
        reload_products_cache()
        products = PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE + INACTIVE_PRODUCTS_CACHE + INACTIVE_CUSTOM_PRODUCTS_CACHE
        products = sorted(products, key=lambda p: p.get("id") or 0)
    return [{**p, "status": product_status(p)} for p in products]


def get_all_products():
    if use_catalog_db():
        reload_products_cache()
    else:
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
    if not CATEGORIES_CACHE:
        reload_categories_cache()
    for category in CATEGORIES_CACHE:
        if category.get("id") == category_id:
            return category
    return None


def is_valid_category_id(category_id: str):
    category = get_category_by_id(category_id)
    return bool(category and category.get("active", True))


def validate_product_prices(price, old_price=None, *, contract_price=False):
    if contract_price:
        return True, None
    price = int(price)
    if old_price:
        old_price = int(old_price)
        if price >= old_price:
            return False, "Акційна ціна має бути нижчою за стару (оригінальну) ціну"
    return True, None


def is_contract_product(product: dict | None) -> bool:
    return bool(product and product.get("contractPrice"))


def get_filament_by_id(filament_id: str):
    reload_filaments_cache()
    needle = str(filament_id or "").strip()
    if not needle:
        return None
    return next((f for f in FILAMENTS_CACHE if str(f.get("id")) == needle), None)


def save_products_to_file():
    if use_catalog_db():
        return
    standard = sorted(PRODUCTS_CACHE + INACTIVE_PRODUCTS_CACHE, key=lambda p: p.get("id") or 0)
    custom = sorted(CUSTOM_PRODUCTS_CACHE + INACTIVE_CUSTOM_PRODUCTS_CACHE, key=lambda p: p.get("id") or 0)
    _atomic_write_text(Path(config.PRODUCTS_FILE), json.dumps(standard, ensure_ascii=False, indent=2))
    _atomic_write_text(Path(config.CUSTOM_PRODUCTS_FILE), json.dumps(custom, ensure_ascii=False, indent=2))
    reload_products_cache(force=True)


def save_categories_to_file():
    if use_catalog_db():
        return
    _atomic_write_text(Path(config.CATEGORIES_FILE), json.dumps(CATEGORIES_CACHE, ensure_ascii=False, indent=2))
    reload_categories_cache(force=True)


def save_filaments_to_file():
    if use_catalog_db():
        return
    _atomic_write_text(Path(config.FILAMENTS_FILE), json.dumps(FILAMENTS_CACHE, ensure_ascii=False, indent=2))
    reload_filaments_cache(force=True)


def _insert_product_db(product: dict, is_custom: bool) -> int:
    status = product_status(product)
    conn = db_connect()
    cur = conn.execute(
        """
        INSERT INTO products (
            category_id, name, emoji, mat, price, old_price, photos, custom_fields,
            hot, gift, filament_choice, luminous_filament_choice, pinned, stl_link, contract_price, is_custom, active,
            status, description, note, video
        ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            product.get("cat"),
            product.get("name"),
            product.get("emoji", "📦"),
            product.get("mat", ""),
            product.get("price", 0),
            product.get("oldPrice"),
            json.dumps(product.get("photos") or []),
            _serialize_custom_fields(product.get("custom_fields")),
            bool(product.get("hot")),
            bool(product.get("gift")),
            bool(product.get("filamentChoice", True)),
            bool(product.get("luminousFilamentChoice")),
            bool(product.get("pinned")),
            product.get("stlLink", ""),
            bool(product.get("contractPrice")),
            is_custom,
            status == "active",
            status,
            product.get("description", ""),
            product.get("note", ""),
            product.get("video", ""),
        ),
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return int(new_id)


def add_product(data: dict):
    try:
        clean_video_url(data.get("video"))  # невалідне посилання — помилка ще до змін
        category_id = data.get("cat", "toy")
        if not is_valid_category_id(category_id):
            return {"ok": False, "error": "Невірна або неактивна категорія"}

        contract_price = bool(data.get("contractPrice"))
        price = 0 if contract_price else int(data.get("price", 0))
        old_price = None if contract_price else data.get("oldPrice")
        is_valid, error = validate_product_prices(price, old_price, contract_price=contract_price)
        if not is_valid:
            return {"ok": False, "error": error}

        is_custom = category_id == "custom"
        product = {
            "cat": category_id,
            "emoji": data.get("emoji", "📦"),
            "photos": data.get("photos", []),
            "name": data.get("name", ""),
            "mat": data.get("mat", ""),
            "price": price,
            "hot": data.get("hot", False),
            "pinned": data.get("pinned", False),
            "stlLink": data.get("stlLink", ""),
            "createdAt": _now_iso(),
        }
        new_status = str(data.get("status") or "active")
        if new_status not in PRODUCT_STATUSES:
            return {"ok": False, "error": "Невірний статус товару"}
        if new_status != "active":
            product["status"] = new_status
        _apply_product_texts(product, data)
        if contract_price:
            product["contractPrice"] = True
        elif old_price:
            product["oldPrice"] = int(old_price)
        if is_custom:
            product["custom_fields"] = normalize_custom_fields(data.get("custom_fields", ""))
        else:
            product["gift"] = False if contract_price else data.get("gift", False)
            product["filamentChoice"] = data.get("filamentChoice", True)
            if product["filamentChoice"] and data.get("luminousFilamentChoice"):
                product["luminousFilamentChoice"] = True

        if use_catalog_db():
            new_id = _insert_product_db(product, is_custom)
        else:
            # ID рахуємо і за неактивними товарами, щоб архівний товар не віддав свій номер новому.
            if is_custom:
                existing = CUSTOM_PRODUCTS_CACHE + INACTIVE_CUSTOM_PRODUCTS_CACHE
                new_id = max((p.get("id", 0) for p in existing), default=100) + 1
            else:
                existing = PRODUCTS_CACHE + INACTIVE_PRODUCTS_CACHE
                new_id = max((p.get("id", 0) for p in existing), default=0) + 1
            product["id"] = new_id
            if new_status == "active":
                (CUSTOM_PRODUCTS_CACHE if is_custom else PRODUCTS_CACHE).append(product)
            else:
                (INACTIVE_CUSTOM_PRODUCTS_CACHE if is_custom else INACTIVE_PRODUCTS_CACHE).append(product)
            save_products_to_file()

        logger.info("Товар додано: %s (ID: %s)", product["name"], new_id)
        reload_products_cache(force=True)
        return {"ok": True, "id": new_id}
    except Exception as e:
        logger.exception("Помилка додавання товару")
        return {"ok": False, "error": str(e)}


def update_product(product_id: int, data: dict):
    try:
        clean_video_url(data.get("video"))  # невалідне посилання — помилка ще до змін у кеші
        product = get_product_any(product_id)
        if not product:
            return {"ok": False, "error": "Товар не знайдено"}

        if "cat" in data and not is_valid_category_id(data.get("cat")):
            return {"ok": False, "error": "Невірна або неактивна категорія"}

        current_category = product.get("cat")
        next_category = data.get("cat", current_category)
        is_current_custom = current_category == "custom"
        is_next_custom = next_category == "custom"

        product.update({
            "emoji": data.get("emoji", product.get("emoji")),
            "photos": data.get("photos", product.get("photos", [])),
            "name": data.get("name", product.get("name")),
            "mat": data.get("mat", product.get("mat")),
            "hot": data.get("hot", product.get("hot", False)),
            "pinned": data.get("pinned", product.get("pinned", False)),
            "stlLink": data.get("stlLink", product.get("stlLink", "")),
            "cat": next_category,
        })
        _apply_product_texts(product, data)

        contract_price = bool(data.get("contractPrice"))
        if contract_price:
            product["contractPrice"] = True
            product["price"] = 0
            product.pop("oldPrice", None)
        else:
            product.pop("contractPrice", None)
            if data.get("price") is not None:
                product["price"] = int(data["price"])

        next_price = product["price"]
        if contract_price:
            next_old_price = None
        elif "oldPrice" in data:
            next_old_price = int(data["oldPrice"]) if data.get("oldPrice") else None
        else:
            next_old_price = product.get("oldPrice")
        is_valid, error = validate_product_prices(next_price, next_old_price, contract_price=contract_price)
        if not is_valid:
            return {"ok": False, "error": error}

        if not contract_price:
            if data.get("oldPrice"):
                product["oldPrice"] = int(data["oldPrice"])
            elif "oldPrice" in product and "oldPrice" in data and not data.get("oldPrice"):
                del product["oldPrice"]

        if not is_next_custom:
            product["gift"] = False if contract_price else data.get("gift", product.get("gift", False))
            if "filamentChoice" in data:
                product["filamentChoice"] = data.get("filamentChoice")
                if not product.get("filamentChoice"):
                    product.pop("luminousFilamentChoice", None)
            if "luminousFilamentChoice" in data:
                if product.get("filamentChoice", True) and data.get("luminousFilamentChoice"):
                    product["luminousFilamentChoice"] = True
                else:
                    product.pop("luminousFilamentChoice", None)
            product.pop("custom_fields", None)
        else:
            product["custom_fields"] = normalize_custom_fields(data.get("custom_fields", product.get("custom_fields", "")))
            product.pop("gift", None)
            product.pop("filamentChoice", None)
            product.pop("luminousFilamentChoice", None)

        if use_catalog_db():
            conn = db_connect()
            conn.execute(
                """
                UPDATE products SET
                    category_id = %s, name = %s, emoji = %s, mat = %s, price = %s, old_price = %s,
                    photos = %s::jsonb, custom_fields = %s, hot = %s, gift = %s, filament_choice = %s,
                    luminous_filament_choice = %s, pinned = %s, stl_link = %s, contract_price = %s, is_custom = %s,
                    description = %s, note = %s, video = %s
                WHERE id = %s
                """,
                (
                    product.get("cat"), product.get("name"), product.get("emoji"), product.get("mat"),
                    product.get("price"), product.get("oldPrice"),
                    json.dumps(product.get("photos") or []),
                    _serialize_custom_fields(product.get("custom_fields")),
                    bool(product.get("hot")), bool(product.get("gift")),
                    bool(product.get("filamentChoice", True)),
                    bool(product.get("luminousFilamentChoice")),
                    bool(product.get("pinned")),
                    product.get("stlLink", ""),
                    bool(product.get("contractPrice")),
                    is_next_custom,
                    product.get("description", ""),
                    product.get("note", ""),
                    product.get("video", ""),
                    product_id,
                ),
            )
            conn.commit()
            conn.close()
        else:
            if is_current_custom != is_next_custom:
                _json_rebalance()
            save_products_to_file()

        logger.info("Товар оновлено: %s (ID: %s)", product["name"], product_id)
        reload_products_cache(force=True)
        return {"ok": True}
    except Exception as e:
        logger.exception("Помилка редагування товару")
        return {"ok": False, "error": str(e)}


def delete_product(product_id: int):
    """М'яке видалення: товар іде в архів (його можна повернути через set_product_status)."""
    result = set_product_status(product_id, "archive")
    if result.get("ok"):
        logger.info("Товар в архіві (ID: %s)", product_id)
    return result


def _json_rebalance() -> None:
    """JSON-бекенд: розкласти всі товари по чотирьох списках за статусом і категорією (custom — окремий файл)."""
    everything = PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE + INACTIVE_PRODUCTS_CACHE + INACTIVE_CUSTOM_PRODUCTS_CACHE

    def pick(active: bool, custom: bool) -> list[dict]:
        return sorted(
            (p for p in everything
             if (product_status(p) == "active") == active and (p.get("cat") == "custom") == custom),
            key=lambda p: p.get("id") or 0,
        )

    _replace_cache_list(PRODUCTS_CACHE, pick(True, False))
    _replace_cache_list(CUSTOM_PRODUCTS_CACHE, pick(True, True))
    _replace_cache_list(INACTIVE_PRODUCTS_CACHE, pick(False, False))
    _replace_cache_list(INACTIVE_CUSTOM_PRODUCTS_CACHE, pick(False, True))


_DB_COLUMNS = {
    "price": "price", "oldPrice": "old_price", "hot": "hot", "pinned": "pinned",
    "gift": "gift", "cat": "category_id",
}


def _apply_changes(product: dict, changes: dict) -> None:
    """Змінити словник товару за `changes` (ключі як у JSON-каталозі); порожня стара ціна прибирає oldPrice."""
    for key, value in changes.items():
        if key == "status":
            if value == "active":
                product.pop("status", None)
            else:
                product["status"] = value
        elif key == "oldPrice" and not value:
            product.pop("oldPrice", None)
        else:
            product[key] = value


def _persist_changes(updates: list[tuple[dict, dict]]) -> None:
    """Записати зміни [(товар, changes)] одним проходом: одна транзакція (БД) або один запис файлів (JSON)."""
    if use_catalog_db():
        conn = db_connect()
        try:
            for product, changes in updates:
                sets, params = [], []
                for key, value in changes.items():
                    if key == "status":
                        sets += ["status = %s", "active = %s"]
                        params += [value, value == "active"]
                    elif key in _DB_COLUMNS:
                        sets.append(f"{_DB_COLUMNS[key]} = %s")
                        params.append((value or None) if key == "oldPrice" else value)
                if sets:
                    conn.execute(f"UPDATE products SET {', '.join(sets)} WHERE id = %s", (*params, product["id"]))
            conn.commit()
        finally:
            conn.close()
    else:
        _json_rebalance()
        save_products_to_file()
    reload_products_cache(force=True)


def set_product_status(product_id: int, status: str):
    if status not in PRODUCT_STATUSES:
        return {"ok": False, "error": "Невірний статус товару"}
    try:
        product = get_product_any(product_id)
        if not product:
            return {"ok": False, "error": "Товар не знайдено"}
        if product_status(product) != status:
            changes = {"status": status}
            _apply_changes(product, changes)
            _persist_changes([(product, changes)])
        return {"ok": True, "status": status}
    except Exception as e:
        logger.exception("Помилка зміни статусу товару")
        return {"ok": False, "error": str(e)}


def _int_field(value, name: str, *, allow_zero: bool = True) -> int:
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(f"Невірне значення: {name}")
    try:
        number = int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        raise ValueError(f"Невірне значення: {name}") from None
    if number < 0 or (number == 0 and not allow_zero) or number > 1_000_000:
        raise ValueError(f"Невірне значення: {name}")
    return number


def _patch_changes(product: dict, fields: dict) -> dict:
    """Перевірені зміни для швидкого оновлення (ціна, стара ціна, прапорці). ValueError — зрозуміле повідомлення."""
    if not isinstance(fields, dict) or not fields:
        raise ValueError("Немає що змінювати")
    unknown = set(fields) - {"price", "oldPrice", "hot", "pinned", "gift"}
    if unknown:
        raise ValueError("Недозволені поля: " + ", ".join(sorted(unknown)))
    is_contract = bool(product.get("contractPrice"))
    is_custom = product.get("cat") == "custom"
    changes: dict = {}
    for flag in ("hot", "pinned", "gift"):
        if flag in fields:
            if not isinstance(fields[flag], bool):
                raise ValueError(f"Невірне значення: {flag}")
            if flag == "gift" and fields[flag] and (is_custom or is_contract):
                raise ValueError("Подарунком не може бути договірний чи індивідуальний товар")
            changes[flag] = fields[flag]
    if "price" in fields or "oldPrice" in fields:
        if is_contract:
            raise ValueError("Договірна ціна: змініть її в редакторі товару")
        price = _int_field(fields["price"], "ціна", allow_zero=False) if "price" in fields else int(product.get("price") or 0)
        if "oldPrice" in fields:
            old = fields["oldPrice"]
            old_price = None if old in (None, "", 0, "0") else _int_field(old, "стара ціна", allow_zero=False)
        else:
            old_price = product.get("oldPrice")
        ok, error = validate_product_prices(price, old_price)
        if not ok:
            raise ValueError(error)
        if "price" in fields:
            changes["price"] = price
        if "oldPrice" in fields:
            changes["oldPrice"] = old_price
    return changes


def get_admin_product(product_id: int):
    product = get_product_any(product_id)
    return {**product, "status": product_status(product)} if product else None


def patch_product(product_id: int, fields: dict):
    """Швидке часткове оновлення: ціна, стара ціна, hot / pinned / gift (товар будь-якого статусу)."""
    try:
        product = get_product_any(product_id)
        if not product:
            return {"ok": False, "error": "Товар не знайдено"}
        changes = _patch_changes(product, fields)
        _apply_changes(product, changes)
        _persist_changes([(product, changes)])
        return {"ok": True, "product": get_admin_product(product_id)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.exception("Помилка швидкого оновлення товару")
        return {"ok": False, "error": str(e)}


BULK_ACTIONS = ("discount", "clear_discount", "category", "status")
MAX_BULK_IDS = 500


def bulk_update_products(ids, action: str, value=None):
    """Масова дія: discount (відсоток 1–90 від поточної/старої ціни), clear_discount, category, status.

    Договірні товари і «Індивідуальне» пропускаються там, де дія до них не стосується; вони йдуть у `skipped`.
    """
    try:
        if action not in BULK_ACTIONS:
            return {"ok": False, "error": "Невідома дія"}
        if not isinstance(ids, list) or not ids:
            return {"ok": False, "error": "Не вибрано жодного товару"}
        if len(ids) > MAX_BULK_IDS:
            return {"ok": False, "error": f"Забагато товарів (максимум {MAX_BULK_IDS})"}
        clean_ids: list[int] = []
        for raw in ids:
            if isinstance(raw, bool):
                return {"ok": False, "error": "Невірний ID товару"}
            try:
                pid = int(raw)
            except (TypeError, ValueError):
                return {"ok": False, "error": "Невірний ID товару"}
            if pid > 0 and pid not in clean_ids:
                clean_ids.append(pid)
        if not clean_ids:
            return {"ok": False, "error": "Не вибрано жодного товару"}

        percent = 0
        category_id = ""
        if action == "discount":
            try:
                percent = _int_field(value, "відсоток", allow_zero=False)
            except ValueError:
                return {"ok": False, "error": "Знижка — число від 1 до 90"}
            if percent > 90:
                return {"ok": False, "error": "Знижка — число від 1 до 90"}
        elif action == "category":
            category_id = str(value or "").strip()
            if not is_valid_category_id(category_id):
                return {"ok": False, "error": "Невірна або неактивна категорія"}
            if category_id == "custom":
                return {"ok": False, "error": "У «Індивідуальне» масово переносити не можна"}
        elif action == "status" and value not in PRODUCT_STATUSES:
            return {"ok": False, "error": "Невірний статус товару"}

        updates: list[tuple[dict, dict]] = []
        skipped: list[dict] = []
        missing: list[int] = []
        for pid in clean_ids:
            product = get_product_any(pid)
            if not product:
                missing.append(pid)
                continue
            changes: dict = {}
            reason = ""
            if action == "status":
                if product_status(product) != value:
                    changes = {"status": value}
            elif action == "category":
                if product.get("cat") == "custom":
                    reason = "індивідуальне"
                elif product.get("cat") != category_id:
                    changes = {"cat": category_id}
            elif product.get("contractPrice"):
                reason = "договірна ціна"
            elif action == "discount":
                base = int(product.get("oldPrice") or product.get("price") or 0)
                new_price = round(base * (100 - percent) / 100)
                if new_price < 1 or new_price >= base:
                    reason = "ціна занадто мала"
                else:
                    changes = {"price": new_price, "oldPrice": base}
            elif product.get("oldPrice"):  # clear_discount
                changes = {"price": int(product["oldPrice"]), "oldPrice": None}
            if reason:
                skipped.append({"id": pid, "reason": reason})
            elif changes:
                updates.append((product, changes))
        for product, changes in updates:
            _apply_changes(product, changes)
        if updates:
            _persist_changes(updates)
        return {"ok": True, "updated": len(updates), "skipped": skipped, "missing": missing}
    except Exception as e:
        logger.exception("Помилка масової дії над товарами")
        return {"ok": False, "error": str(e)}


def duplicate_product(product_id: int):
    """Копія товару: назва + « (копія)», статус «прихований» — щоб не з'явився в магазині до перевірки."""
    product = get_product_any(product_id)
    if not product:
        return {"ok": False, "error": "Товар не знайдено"}
    data = {k: copy.deepcopy(v) for k, v in product.items() if k not in ("id", "createdAt", "status")}
    data["name"] = (str(product.get("name") or "").strip() + " (копія)")[:200]
    data["status"] = "hidden"
    return add_product(data)


def add_category(data: dict):
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

        if use_catalog_db():
            conn = db_connect()
            conn.execute(
                """
                INSERT INTO categories (id, name, emoji, badge_class, sort_order, active, quick_slot)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (category_id, name, emoji, badge_class, category["order"], category["active"], category.get("quickSlot")),
            )
            conn.commit()
            conn.close()
        else:
            CATEGORIES_CACHE.append(category)
            save_categories_to_file()

        reload_categories_cache(force=True)
        return {"ok": True, "category": category}
    except Exception as e:
        logger.exception("Помилка додавання категорії")
        return {"ok": False, "error": str(e)}


def update_category(category_id: str, data: dict):
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
                products_to_recat = [p for p in get_all_products() if p.get("cat") == category_id]
                for product in products_to_recat:
                    product["cat"] = new_id
                if use_catalog_db() and products_to_recat:
                    conn = db_connect()
                    conn.execute(
                        "UPDATE products SET category_id = %s WHERE category_id = %s",
                        (new_id, category_id),
                    )
                    conn.commit()
                    conn.close()
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

        if use_catalog_db():
            conn = db_connect()
            conn.execute(
                """
                UPDATE categories SET id = %s, name = %s, emoji = %s, badge_class = %s,
                    sort_order = %s, active = %s, quick_slot = %s
                WHERE id = %s
                """,
                (
                    category["id"], category["name"], category["emoji"], category.get("badgeClass"),
                    category.get("order"), category.get("active"), category.get("quickSlot"), category_id,
                ),
            )
            conn.commit()
            conn.close()
        else:
            save_categories_to_file()
            if any(p.get("cat") == category_id for p in get_all_products()):
                save_products_to_file()

        reload_categories_cache(force=True)
        return {"ok": True, "category": category}
    except Exception as e:
        logger.exception("Помилка оновлення категорії")
        return {"ok": False, "error": str(e)}


def delete_category(category_id: str):
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

        if use_catalog_db():
            conn = db_connect()
            conn.execute("DELETE FROM categories WHERE id = %s", (category_id,))
            conn.commit()
            conn.close()
        else:
            CATEGORIES_CACHE[:] = [c for c in CATEGORIES_CACHE if c.get("id") != category_id]
            save_categories_to_file()

        reload_categories_cache(force=True)
        return {"ok": True}
    except Exception as e:
        logger.exception("Помилка видалення категорії")
        return {"ok": False, "error": str(e)}


def update_filament(filament_id: str, data: dict):
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

        if use_catalog_db():
            conn = db_connect()
            cur = conn.execute(
                "UPDATE filaments SET name = %s, hex = %s, available = %s WHERE id = %s",
                (filament.get("name"), filament.get("hex"), filament.get("available"), filament_id),
            )
            if is_postgres() and getattr(cur, "rowcount", 1) == 0:
                conn.execute(
                    """
                    INSERT INTO filaments (id, name, hex, available, sort_order)
                    VALUES (%s, %s, %s, %s, 999)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        hex = EXCLUDED.hex,
                        available = EXCLUDED.available
                    """,
                    (
                        filament_id,
                        filament.get("name"),
                        filament.get("hex"),
                        filament.get("available"),
                    ),
                )
            sync_filament_colors_table(conn, filament)
            conn.commit()
            conn.close()
        else:
            save_filaments_to_file()
            conn = db_connect()
            sync_filament_colors_table(conn, filament)
            conn.commit()
            conn.close()

        reload_filaments_cache(force=True)
        saved = get_filament_by_id(filament_id)
        if not saved:
            return {"ok": False, "error": "Не вдалося зберегти філамент"}
        logger.info("Філамент оновлено: %s", saved.get("id"))
        return {"ok": True, "filament": saved}
    except Exception as e:
        logger.exception("Помилка оновлення філаменту")
        return {"ok": False, "error": str(e)}


bootstrap_json_catalog(force=True)
