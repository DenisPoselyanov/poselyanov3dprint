"""Product catalog storage: JSON files or Postgres/Supabase."""

from __future__ import annotations

import asyncio
import json
import logging
import os
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


def _row_to_product(row: dict) -> dict:
    photos = row.get("photos") or []
    if isinstance(photos, str):
        photos = json.loads(photos)
    custom_fields = row.get("custom_fields")
    if isinstance(custom_fields, str) and custom_fields.startswith("["):
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
    return p


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
            created_at TIMESTAMPTZ DEFAULT NOW()
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
        FILAMENTS_CACHE = [_row_to_filament(dict(r)) for r in rows]
        return True

    fp = Path(config.FILAMENTS_FILE)
    if not fp.exists():
        return False
    mtime = fp.stat().st_mtime
    if force or mtime != _filaments_mtime:
        FILAMENTS_CACHE = load_filaments_file()
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
        CATEGORIES_CACHE = [_row_to_category(dict(r)) for r in rows]
        return True

    fp = Path(config.CATEGORIES_FILE)
    if not fp.exists():
        return False
    mtime = fp.stat().st_mtime
    if force or mtime != _categories_mtime:
        CATEGORIES_CACHE = load_categories_file()
        _categories_mtime = mtime
        return True
    return False


def reload_products_cache(force: bool = False):
    global PRODUCTS_CACHE, CUSTOM_PRODUCTS_CACHE, _products_mtime, _custom_mtime
    if use_catalog_db():
        conn = db_connect(dict_rows=True)
        rows = conn.execute(
            "SELECT * FROM products WHERE active = true ORDER BY id"
        ).fetchall()
        conn.close()
        standard, custom = [], []
        for r in rows:
            p = _row_to_product(dict(r))
            if r.get("is_custom") or p.get("cat") == "custom":
                custom.append(p)
            else:
                standard.append(p)
        PRODUCTS_CACHE = standard
        CUSTOM_PRODUCTS_CACHE = custom
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
            if path == config.PRODUCTS_FILE:
                PRODUCTS_CACHE = data
                globals()["_products_mtime"] = mtime
            else:
                CUSTOM_PRODUCTS_CACHE = data
                globals()["_custom_mtime"] = mtime
            changed = True
            logger.info("Оновлено кеш: %s (%s товарів)", path, len(data))
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
        row = conn.execute(sql("SELECT * FROM products WHERE id = ? AND active = true"), (product_id,)).fetchone()
        conn.close()
        return _row_to_product(dict(row)) if row else None
    reload_products_cache()
    for p in PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE:
        if p.get("id") == product_id:
            return p
    return None


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
    for category in get_all_categories(active_only=False):
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
    _atomic_write_text(Path(config.PRODUCTS_FILE), json.dumps(PRODUCTS_CACHE, ensure_ascii=False, indent=2))
    _atomic_write_text(
        Path(config.CUSTOM_PRODUCTS_FILE),
        json.dumps(CUSTOM_PRODUCTS_CACHE, ensure_ascii=False, indent=2),
    )
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
    conn = db_connect()
    cur = conn.execute(
        """
        INSERT INTO products (
            category_id, name, emoji, mat, price, old_price, photos, custom_fields,
            hot, gift, filament_choice, luminous_filament_choice, pinned, stl_link, contract_price, is_custom, active
        ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
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
            product.get("custom_fields") if isinstance(product.get("custom_fields"), str) else json.dumps(product.get("custom_fields") or ""),
            bool(product.get("hot")),
            bool(product.get("gift")),
            bool(product.get("filamentChoice", True)),
            bool(product.get("luminousFilamentChoice")),
            bool(product.get("pinned")),
            product.get("stlLink", ""),
            bool(product.get("contractPrice")),
            is_custom,
        ),
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return int(new_id)


def add_product(data: dict):
    try:
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
        }
        if contract_price:
            product["contractPrice"] = True
        elif old_price:
            product["oldPrice"] = int(old_price)
        if is_custom:
            product["custom_fields"] = data.get("custom_fields", "")
        else:
            product["gift"] = False if contract_price else data.get("gift", False)
            product["filamentChoice"] = data.get("filamentChoice", True)
            if product["filamentChoice"] and data.get("luminousFilamentChoice"):
                product["luminousFilamentChoice"] = True

        if use_catalog_db():
            new_id = _insert_product_db(product, is_custom)
        else:
            target_list = CUSTOM_PRODUCTS_CACHE if is_custom else PRODUCTS_CACHE
            new_id = 101 if is_custom and not target_list else (max(p.get("id", 0) for p in target_list) + 1 if target_list else (101 if is_custom else 1))
            product["id"] = new_id
            target_list.append(product)
            save_products_to_file()

        logger.info("Товар додано: %s (ID: %s)", product["name"], new_id)
        reload_products_cache(force=True)
        return {"ok": True, "id": new_id}
    except Exception as e:
        logger.exception("Помилка додавання товару")
        return {"ok": False, "error": str(e)}


def update_product(product_id: int, data: dict):
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
            product["custom_fields"] = data.get("custom_fields", product.get("custom_fields", ""))
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
                    luminous_filament_choice = %s, pinned = %s, stl_link = %s, contract_price = %s, is_custom = %s
                WHERE id = %s
                """,
                (
                    product.get("cat"), product.get("name"), product.get("emoji"), product.get("mat"),
                    product.get("price"), product.get("oldPrice"),
                    json.dumps(product.get("photos") or []),
                    product.get("custom_fields") if isinstance(product.get("custom_fields"), str) else json.dumps(product.get("custom_fields") or ""),
                    bool(product.get("hot")), bool(product.get("gift")),
                    bool(product.get("filamentChoice", True)),
                    bool(product.get("luminousFilamentChoice")),
                    bool(product.get("pinned")),
                    product.get("stlLink", ""), bool(product.get("contractPrice")),
                    is_next_custom, product_id,
                ),
            )
            conn.commit()
            conn.close()
        else:
            if is_current_custom != is_next_custom:
                source = CUSTOM_PRODUCTS_CACHE if is_current_custom else PRODUCTS_CACHE
                dest = CUSTOM_PRODUCTS_CACHE if is_next_custom else PRODUCTS_CACHE
                source[:] = [p for p in source if p.get("id") != product_id]
                if product not in dest:
                    dest.append(product)
            save_products_to_file()

        logger.info("Товар оновлено: %s (ID: %s)", product["name"], product_id)
        reload_products_cache(force=True)
        return {"ok": True}
    except Exception as e:
        logger.exception("Помилка редагування товару")
        return {"ok": False, "error": str(e)}


def delete_product(product_id: int):
    try:
        product = get_product_by_id(product_id)
        if not product:
            return {"ok": False, "error": "Товар не знайдено"}

        if use_catalog_db():
            conn = db_connect()
            conn.execute("UPDATE products SET active = false WHERE id = %s", (product_id,))
            conn.commit()
            conn.close()
        else:
            is_custom = product.get("cat") == "custom"
            target = CUSTOM_PRODUCTS_CACHE if is_custom else PRODUCTS_CACHE
            target[:] = [p for p in target if p.get("id") != product_id]
            save_products_to_file()

        logger.info("Товар видалено: %s (ID: %s)", product.get("name"), product_id)
        reload_products_cache(force=True)
        return {"ok": True}
    except Exception as e:
        logger.exception("Помилка видалення товару")
        return {"ok": False, "error": str(e)}


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
                for product in get_all_products():
                    if product.get("cat") == category_id:
                        product["cat"] = new_id
                        if use_catalog_db():
                            conn = db_connect()
                            conn.execute("UPDATE products SET category_id = %s WHERE category_id = %s", (new_id, category_id))
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
            conn.execute(
                "UPDATE filaments SET name = %s, hex = %s, available = %s WHERE id = %s",
                (filament.get("name"), filament.get("hex"), filament.get("available"), filament_id),
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
        logger.info("Філамент оновлено: %s", filament.get("id"))
        return {"ok": True, "filament": filament}
    except Exception as e:
        logger.exception("Помилка оновлення філаменту")
        return {"ok": False, "error": str(e)}


bootstrap_json_catalog(force=True)
