"""Тематичні добірки на головній магазину («Стопка»).

Добірка буває двох видів:
  auto   — склад рахує фронтенд за правилом `rule` (ціна, новинки, знижка, хіти…);
  manual — склад задано вручну в `collection_products`.

Керування — з адмінки (REDESIGN.md, розд. 4.11): CRUD, порядок, склад і правила нижче в цьому файлі.
При першому запуску засіваються 7 типових.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone

from db_core import db_connect, is_postgres, sql as _sql
from services.db_utils import row_to_dict
from services.slugs import unique_slug

logger = logging.getLogger(__name__)

# Ключі іконок — з набору PATHS фронтенду; кольори — токени дизайн-системи.
ICONS = ("sparkle", "tag", "moonglow", "gift", "percent", "flame", "star", "heart", "home", "cube",
         "box", "clock", "pin", "wand", "truck")
COLORS = ("violet", "green", "teal", "orange", "red", "pink", "amber", "blue")
MODES = ("auto", "manual")
# Типи правил для mode=auto. Параметри лежать поруч у тому ж JSON:
#   new       {"days": 30}                 — додані за останні N днів
#   price_max {"max_price": 100, "exclude_custom": false}
#   price_min {"min_price": 300}
#   glow      {}                           — світяться в темряві
#   sale      {}                           — зі старою ціною
#   hits      {"limit": 12}                — топ продажів (GET /api/hits)
RULE_TYPES = ("new", "price_max", "price_min", "glow", "sale", "hits")

DEFAULT_COLLECTIONS: list[dict] = [
    {"id": "new", "name": "Новинки", "description": "Свіжі надходження", "icon": "sparkle", "color": "violet",
     "rule": {"type": "new", "days": 30}},
    {"id": "u100", "name": "До 100 ₴", "description": "Дрібнички за копійки", "icon": "tag", "color": "green",
     "rule": {"type": "price_max", "max_price": 100}},
    {"id": "glow", "name": "Світяться вночі", "description": "Сяють у темряві", "icon": "moonglow", "color": "teal",
     "rule": {"type": "glow"}},
    {"id": "gift", "name": "Подарунок до 150 ₴", "description": "Ідеї, що точно сподобаються", "icon": "gift",
     "color": "orange", "rule": {"type": "price_max", "max_price": 150, "exclude_custom": True}},
    {"id": "sale", "name": "Зі знижкою", "description": "Стара ціна й −% на картці", "icon": "percent", "color": "red",
     "rule": {"type": "sale"}},
    {"id": "hits", "name": "Хіти", "description": "Те, що замовляють найчастіше", "icon": "flame", "color": "pink",
     "rule": {"type": "hits", "limit": 12}},
    {"id": "top", "name": "Від 300 ₴", "description": "Великі й ефектні речі", "icon": "star", "color": "amber",
     "rule": {"type": "price_min", "min_price": 300}},
]

_CACHE_TTL_SEC = 60.0
_cache: dict = {"ts": 0.0, "rows": None}


# ─── СХЕМА ──────────────────────────────────────────────────

def init_collections_table(conn) -> None:
    """Створити таблиці добірок і засіяти 7 типових при першому запуску."""
    product_id_type = "BIGINT" if is_postgres() else "INTEGER"
    conn.execute("""
        CREATE TABLE IF NOT EXISTS collections (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            icon        TEXT NOT NULL DEFAULT 'sparkle',
            color       TEXT NOT NULL DEFAULT 'blue',
            sort_order  INTEGER NOT NULL DEFAULT 100,
            active      INTEGER NOT NULL DEFAULT 1,
            mode        TEXT NOT NULL DEFAULT 'auto',
            rule        TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS collection_products (
            collection_id TEXT NOT NULL,
            product_id    {product_id_type} NOT NULL,
            sort_order    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (collection_id, product_id)
        )
    """)

    row = conn.execute("SELECT COUNT(*) FROM collections").fetchone()
    count = int(next(iter(row.values())) if isinstance(row, dict) else row[0])
    if count == 0:
        for order, c in enumerate(DEFAULT_COLLECTIONS, start=1):
            conn.execute(
                _sql("""
                    INSERT INTO collections (id, name, description, icon, color, sort_order, active, mode, rule)
                    VALUES (?, ?, ?, ?, ?, ?, 1, 'auto', ?)
                """),
                (c["id"], c["name"], c["description"], c["icon"], c["color"], order * 10,
                 json.dumps(c["rule"], ensure_ascii=False)),
            )
    _invalidate_cache()


# ─── ЧИТАННЯ ────────────────────────────────────────────────

def _invalidate_cache() -> None:
    _cache["ts"] = 0.0
    _cache["rows"] = None


def _parse_rule(raw) -> dict | None:
    if isinstance(raw, dict):
        rule = raw
    else:
        try:
            rule = json.loads(raw or "")
        except (TypeError, ValueError):
            return None
    if not isinstance(rule, dict) or rule.get("type") not in RULE_TYPES:
        return None
    return rule


def _normalize(row: dict, product_ids: list[int]) -> dict | None:
    mode = row.get("mode") if row.get("mode") in MODES else "auto"
    item = {
        "id": str(row.get("id") or ""),
        "name": row.get("name") or "",
        "description": row.get("description") or "",
        "icon": row.get("icon") if row.get("icon") in ICONS else "sparkle",
        "color": row.get("color") if row.get("color") in COLORS else "blue",
        "sort_order": int(row.get("sort_order") or 0),
        "mode": mode,
    }
    if mode == "manual":
        item["product_ids"] = product_ids
    else:
        rule = _parse_rule(row.get("rule"))
        if rule is None:
            logger.warning("collections: некоректне правило добірки %s — пропускаю", item["id"])
            return None
        item["rule"] = rule
    return item


def _fetch_active() -> list[dict]:
    conn = db_connect(dict_rows=True)
    try:
        rows = [
            row_to_dict(r)
            for r in conn.execute(
                "SELECT id, name, description, icon, color, sort_order, mode, rule "
                "FROM collections WHERE active = 1 ORDER BY sort_order ASC, id ASC"
            ).fetchall()
        ]
        links = [
            row_to_dict(r)
            for r in conn.execute(
                "SELECT collection_id, product_id FROM collection_products ORDER BY sort_order ASC, product_id ASC"
            ).fetchall()
        ]
    finally:
        conn.close()
    by_collection: dict[str, list[int]] = {}
    for link in links:
        by_collection.setdefault(str(link["collection_id"]), []).append(int(link["product_id"]))
    result = []
    for row in rows:
        item = _normalize(row, by_collection.get(str(row.get("id")), []))
        if item:
            result.append(item)
    return result


def public_collections() -> list[dict]:
    """Активні добірки для вітрини. Порожні відсіює фронтенд, коли порахує склад."""
    now = time.monotonic()
    if _cache["rows"] is not None and (now - _cache["ts"]) < _CACHE_TTL_SEC:
        return _cache["rows"]
    try:
        rows = _fetch_active()
    except Exception:
        # Не кешуємо фолбек — щойно БД відповість, повернемось до реальних добірок.
        logger.warning("collections: не вдалось прочитати таблицю, використовую типові", exc_info=True)
        return [
            {**{k: v for k, v in c.items() if k != "rule"}, "sort_order": i * 10, "mode": "auto", "rule": c["rule"]}
            for i, c in enumerate(DEFAULT_COLLECTIONS, start=1)
        ]
    _cache["rows"] = rows
    _cache["ts"] = now
    return rows


# ═══ ВІТРИНА: лише видимі товари ═════════════════════════════

def visible_collections() -> list[dict]:
    """Публічний список: у ручних добірках лише активні товари, порожні ручні добірки прибрано.
    Авто-добірки віддаються як є — склад рахує фронтенд (хіти залежать від продажів)."""
    rows = public_collections()
    try:
        from catalog_store import get_all_products
        active_ids = {int(p["id"]) for p in get_all_products() if p.get("id") is not None}
    except Exception:
        logger.warning("collections: не вдалось отримати активні товари, віддаю добірки без фільтра", exc_info=True)
        return rows
    result = []
    for item in rows:
        if item.get("mode") == "manual":
            ids = [pid for pid in item.get("product_ids", []) if pid in active_ids]
            if not ids:
                continue
            item = {**item, "product_ids": ids}
        result.append(item)
    return result


# ═══ АДМІНКА: правила, перевірка, CRUD ═══════════════════════
# Семантика правил дзеркалить `collectionProducts` в index.html — змінюючи одне, змінюй друге.

NAME_MAX = 40
DESCRIPTION_MAX = 120
MAX_MANUAL_PRODUCTS = 500
RESERVED_IDS = ("order", "preview")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def _clean_int(value, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(f"Вкажіть число: {name}")
    try:
        number = int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        raise ValueError(f"Вкажіть число: {name}") from None
    if number < lo or number > hi:
        raise ValueError(f"{name}: від {lo} до {hi}")
    return number


def validate_rule(rule) -> dict:
    """Нормалізоване правило з відомих полів. ValueError — зрозуміле повідомлення."""
    if not isinstance(rule, dict) or rule.get("type") not in RULE_TYPES:
        raise ValueError("Невідомий тип правила")
    kind = rule["type"]
    if kind == "new":
        return {"type": kind, "days": _clean_int(rule.get("days", 30), "Днів", 1, 365)}
    if kind == "price_max":
        return {"type": kind, "max_price": _clean_int(rule.get("max_price"), "Ціна до, ₴", 1, 100_000),
                "exclude_custom": bool(rule.get("exclude_custom"))}
    if kind == "price_min":
        return {"type": kind, "min_price": _clean_int(rule.get("min_price"), "Ціна від, ₴", 1, 100_000)}
    if kind == "hits":
        return {"type": kind, "limit": _clean_int(rule.get("limit", 12), "Кількість хітів", 1, 48)}
    return {"type": kind}


def rule_label(rule: dict | None) -> str:
    """Короткий опис правила для списку: «Авто: до 100 ₴»."""
    r = rule or {}
    kind = r.get("type")
    if kind == "new":
        return f"Авто: новинки за {r.get('days', 30)} дн."
    if kind == "price_max":
        return f"Авто: до {r.get('max_price', 0)} ₴" + (", без індивідуальних" if r.get("exclude_custom") else "")
    if kind == "price_min":
        return f"Авто: від {r.get('min_price', 0)} ₴"
    if kind == "glow":
        return "Авто: світяться вночі"
    if kind == "sale":
        return "Авто: зі знижкою"
    if kind == "hits":
        return f"Авто: хіти продажів (до {r.get('limit', 12)})"
    return "Авто"


def _parse_created(value) -> float | None:
    """createdAt товару -> timestamp; як parseServerDate у фронтенді: без пояса часу вважаємо UTC."""
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _is_contract(p: dict) -> bool:
    return bool(p.get("contractPrice"))


def _price(p: dict) -> float:
    try:
        return float(p.get("price") or 0)
    except (TypeError, ValueError):
        return 0.0


def _discount_pct(p: dict) -> int:
    """Як discountPct у фронтенді: 0, якщо договірна ціна або старої ціни немає / вона не вища."""
    try:
        old = float(p.get("oldPrice") or 0)
    except (TypeError, ValueError):
        return 0
    if _is_contract(p) or not old or _price(p) >= old:
        return 0
    return round((1 - _price(p) / old) * 100)


def matching_products(rule: dict, pool: list[dict], hit_ids: list[int] | None = None) -> list[dict]:
    """Активні товари, що підходять під правило (порядок як у магазині)."""
    kind = (rule or {}).get("type")
    if kind == "new":
        days = float(rule.get("days") or 30)
        now = time.time()
        found = []
        for p in pool:
            ts = _parse_created(p.get("createdAt"))
            if ts is not None and now - ts < days * 86400:
                found.append((ts, p))
        return [p for _, p in sorted(found, key=lambda x: -x[0])]
    if kind == "price_max":
        limit = rule.get("max_price") or 0
        return [p for p in pool if not _is_contract(p) and _price(p) <= limit
                and not (rule.get("exclude_custom") and p.get("cat") == "custom")]
    if kind == "price_min":
        floor = rule.get("min_price") or 0
        return [p for p in pool if not _is_contract(p) and _price(p) >= floor]
    if kind == "glow":
        return [p for p in pool if p.get("luminousFilamentChoice")]
    if kind == "sale":
        return sorted((p for p in pool if _discount_pct(p) > 0), key=lambda p: -_discount_pct(p))
    if kind == "hits":
        by_id = {p["id"]: p for p in pool}
        limit = int(rule.get("limit") or 12)
        out: list[dict] = []
        seen: set = set()
        # як hitProducts у фронтенді: продажі, потім позначені «Хіт» у звичайному каталозі
        for pid in hit_ids or []:
            if pid in by_id and pid not in seen and len(out) < limit:
                seen.add(pid)
                out.append(by_id[pid])
        for p in pool:
            if p.get("hot") and p.get("cat") != "custom" and p["id"] not in seen and len(out) < limit:
                seen.add(p["id"])
                out.append(p)
        return out
    return []


def _catalog_snapshot() -> tuple[list[dict], dict[int, dict], list[int]]:
    """(активні товари, усі товари за id, id хітів за продажами)."""
    from catalog_store import get_admin_products, product_status
    from services.hits import top_selling_products

    everything = {int(p["id"]): p for p in get_admin_products() if p.get("id") is not None}
    pool = [p for p in everything.values() if product_status(p) == "active"]
    try:
        hit_ids = [int(h["product_id"]) for h in top_selling_products(90, 24)]
    except Exception:
        hit_ids = []
    return pool, everything, hit_ids


def _read_all() -> tuple[list[dict], dict[str, list[int]]]:
    conn = db_connect(dict_rows=True)
    try:
        rows = [row_to_dict(r) for r in conn.execute(
            "SELECT id, name, description, icon, color, sort_order, active, mode, rule "
            "FROM collections ORDER BY sort_order ASC, id ASC").fetchall()]
        links = [row_to_dict(r) for r in conn.execute(
            "SELECT collection_id, product_id FROM collection_products ORDER BY sort_order ASC, product_id ASC").fetchall()]
    finally:
        conn.close()
    members: dict[str, list[int]] = {}
    for link in links:
        members.setdefault(str(link["collection_id"]), []).append(int(link["product_id"]))
    return rows, members


def _admin_item(row: dict, members: list[int], pool_ids: set, pool: list[dict], hit_ids: list[int]) -> dict:
    mode = row.get("mode") if row.get("mode") in MODES else "auto"
    rule = _parse_rule(row.get("rule"))
    item = {
        "id": str(row["id"]),
        "name": row.get("name") or "",
        "description": row.get("description") or "",
        "icon": row.get("icon") if row.get("icon") in ICONS else "sparkle",
        "color": row.get("color") if row.get("color") in COLORS else "blue",
        "sort_order": int(row.get("sort_order") or 0),
        "active": bool(row.get("active")),
        "mode": mode,
        "rule": rule,
    }
    if mode == "manual":
        item["product_ids"] = members
        item["count"] = sum(1 for pid in members if pid in pool_ids)
        item["hidden_count"] = len(members) - item["count"]
        item["label"] = "Вручну"
    else:
        item["count"] = len(matching_products(rule, pool, hit_ids)) if rule else 0
        item["hidden_count"] = 0
        item["label"] = rule_label(rule)
    return item


def list_admin_collections() -> list[dict]:
    """Усі добірки (і вимкнені) з кількістю товарів, що бачить покупець."""
    rows, members = _read_all()
    pool, _everything, hit_ids = _catalog_snapshot()
    pool_ids = {int(p["id"]) for p in pool}
    return [_admin_item(r, members.get(str(r["id"]), []), pool_ids, pool, hit_ids) for r in rows]


def preview_collection(data: dict) -> dict:
    """«Зараз підходить: N товарів» для форми, ще до збереження."""
    pool, everything, hit_ids = _catalog_snapshot()
    pool_ids = {int(p["id"]) for p in pool}
    try:
        if (data or {}).get("mode") == "manual":
            ids = _clean_product_ids(data.get("product_ids"), everything)
            found = [everything[i] for i in ids if i in pool_ids]
        else:
            found = matching_products(validate_rule(data.get("rule")), pool, hit_ids)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "count": len(found), "product_ids": [p["id"] for p in found[:12]]}


def _clean_product_ids(raw, everything: dict[int, dict]) -> list[int]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("product_ids має бути списком")
    if len(raw) > MAX_MANUAL_PRODUCTS:
        raise ValueError(f"Не більше {MAX_MANUAL_PRODUCTS} товарів у добірці")
    ids: list[int] = []
    for value in raw:
        try:
            pid = int(value)
        except (TypeError, ValueError):
            raise ValueError("Невірний ID товару") from None
        if pid not in everything:
            raise ValueError(f"Товару {pid} не існує")
        if pid not in ids:
            ids.append(pid)
    return ids


def _clean_fields(data: dict, current: dict | None) -> dict:
    """Перевірені поля для запису. `current` — наявна добірка (для часткового оновлення)."""
    out: dict = {}
    if current is None or "name" in data:
        name = " ".join(str(data.get("name") or "").split())
        if not name:
            raise ValueError("Вкажіть назву добірки")
        if len(name) > NAME_MAX:
            raise ValueError(f"Назва не довша за {NAME_MAX} символів")
        out["name"] = name
    if "description" in data:
        description = " ".join(str(data.get("description") or "").split())
        if len(description) > DESCRIPTION_MAX:
            raise ValueError(f"Опис не довший за {DESCRIPTION_MAX} символів")
        out["description"] = description
    if "icon" in data or current is None:
        icon = data.get("icon", "sparkle")
        if icon not in ICONS:
            raise ValueError("Невідома іконка")
        out["icon"] = icon
    if "color" in data or current is None:
        color = data.get("color", "blue")
        if color not in COLORS:
            raise ValueError("Невідомий колір")
        out["color"] = color
    if "active" in data:
        out["active"] = bool(data.get("active"))
    mode = data.get("mode", current["mode"] if current else "manual")
    if mode not in MODES:
        raise ValueError("Режим: auto або manual")
    if current is None or "mode" in data:
        out["mode"] = mode
    if mode == "auto":
        if "rule" in data:
            out["rule"] = validate_rule(data.get("rule"))
        elif current is None or current.get("mode") != "auto" or not current.get("rule"):
            raise ValueError("Для автоматичної добірки потрібне правило")
    elif current is None or "mode" in data or "rule" in data:
        out["rule"] = None
    return out


def _next_sort_order(conn) -> int:
    row = conn.execute("SELECT MAX(sort_order) FROM collections").fetchone()
    top = next(iter(row.values())) if isinstance(row, dict) else row[0]
    return int(top or 0) + 10


def _write_members(conn, collection_id: str, ids: list[int]) -> None:
    conn.execute(_sql("DELETE FROM collection_products WHERE collection_id = ?"), (collection_id,))
    for order, pid in enumerate(ids, start=1):
        conn.execute(
            _sql("INSERT INTO collection_products (collection_id, product_id, sort_order) VALUES (?, ?, ?)"),
            (collection_id, pid, order),
        )


def _find(collection_id: str) -> dict | None:
    rows, _members = _read_all()
    for row in rows:
        if str(row["id"]) == collection_id:
            return {**row, "rule": _parse_rule(row.get("rule"))}
    return None


def _admin_view(collection_id: str) -> dict | None:
    for item in list_admin_collections():
        if item["id"] == collection_id:
            return item
    return None


def create_collection(data: dict) -> dict:
    try:
        fields = _clean_fields(data, None)
        _pool, everything, _hits = _catalog_snapshot()
        ids = _clean_product_ids(data.get("product_ids"), everything) if fields["mode"] == "manual" else []
        rows, _members = _read_all()
        taken = [str(r["id"]) for r in rows] + list(RESERVED_IDS)
        raw_id = str(data.get("id") or "").strip().lower()
        if raw_id:
            if not _ID_RE.match(raw_id) or raw_id in RESERVED_IDS:
                raise ValueError("ID: латиниця, цифри, дефіс (до 32 символів)")
            if raw_id in taken:
                raise ValueError("Добірка з таким ID вже є")
            new_id = raw_id
        else:
            new_id = unique_slug(fields["name"], taken, fallback="collection")
        conn = db_connect()
        try:
            conn.execute(
                _sql("""INSERT INTO collections (id, name, description, icon, color, sort_order, active, mode, rule)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""),
                (new_id, fields["name"], fields.get("description", ""), fields["icon"], fields["color"],
                 _next_sort_order(conn), 1 if fields.get("active", True) else 0, fields["mode"],
                 json.dumps(fields["rule"], ensure_ascii=False) if fields.get("rule") else ""),
            )
            _write_members(conn, new_id, ids)
            conn.commit()
        finally:
            conn.close()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.exception("collections: не вдалось створити добірку")
        return {"ok": False, "error": str(e)}
    _invalidate_cache()
    return {"ok": True, "id": new_id, "collection": _admin_view(new_id)}


def update_collection(collection_id: str, data: dict) -> dict:
    try:
        current = _find(collection_id)
        if current is None:
            return {"ok": False, "error": "Добірку не знайдено", "not_found": True}
        fields = _clean_fields(data, current)
        mode = fields.get("mode", current["mode"])
        ids = None
        if "product_ids" in data:
            if mode != "manual":
                raise ValueError("Склад задається вручну лише в режимі «Вручну»")
            _pool, everything, _hits = _catalog_snapshot()
            ids = _clean_product_ids(data.get("product_ids"), everything)
        sets, params = [], []
        for key in ("name", "description", "icon", "color", "mode"):
            if key in fields:
                sets.append(f"{key} = ?")
                params.append(fields[key])
        if "active" in fields:
            sets.append("active = ?")
            params.append(1 if fields["active"] else 0)
        if "rule" in fields:
            sets.append("rule = ?")
            params.append(json.dumps(fields["rule"], ensure_ascii=False) if fields["rule"] else "")
        conn = db_connect()
        try:
            if sets:
                conn.execute(_sql(f"UPDATE collections SET {', '.join(sets)} WHERE id = ?"), (*params, collection_id))
            if ids is not None:
                _write_members(conn, collection_id, ids)
            conn.commit()
        finally:
            conn.close()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.exception("collections: не вдалось оновити добірку")
        return {"ok": False, "error": str(e)}
    _invalidate_cache()
    return {"ok": True, "id": collection_id, "collection": _admin_view(collection_id)}


def delete_collection(collection_id: str) -> dict:
    try:
        if _find(collection_id) is None:
            return {"ok": False, "error": "Добірку не знайдено", "not_found": True}
        conn = db_connect()
        try:
            conn.execute(_sql("DELETE FROM collection_products WHERE collection_id = ?"), (collection_id,))
            conn.execute(_sql("DELETE FROM collections WHERE id = ?"), (collection_id,))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.exception("collections: не вдалось видалити добірку")
        return {"ok": False, "error": str(e)}
    _invalidate_cache()
    return {"ok": True}


def reorder_collections(ids) -> dict:
    """Порядок одним запитом: усі ID мають існувати, без дублів; не названі йдуть далі у поточному порядку."""
    try:
        if not isinstance(ids, list) or not ids:
            raise ValueError("Передайте список ID добірок")
        rows, _members = _read_all()
        existing = [str(r["id"]) for r in rows]
        wanted = [str(i) for i in ids]
        if len(set(wanted)) != len(wanted):
            raise ValueError("ID добірок повторюються")
        unknown = [i for i in wanted if i not in existing]
        if unknown:
            raise ValueError("Невідомі добірки: " + ", ".join(unknown))
        final = wanted + [i for i in existing if i not in wanted]
        conn = db_connect()
        try:
            for position, cid in enumerate(final, start=1):
                conn.execute(_sql("UPDATE collections SET sort_order = ? WHERE id = ?"), (position * 10, cid))
            conn.commit()
        finally:
            conn.close()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.exception("collections: не вдалось змінити порядок")
        return {"ok": False, "error": str(e)}
    _invalidate_cache()
    return {"ok": True, "ids": final}


def set_collection_products(collection_id: str, ids) -> dict:
    """Упорядкований склад ручної добірки."""
    current = _find(collection_id)
    if current is None:
        return {"ok": False, "error": "Добірку не знайдено", "not_found": True}
    if current["mode"] != "manual":
        return {"ok": False, "error": "Склад задається вручну лише в режимі «Вручну»"}
    return update_collection(collection_id, {"product_ids": ids})


def product_collection_ids(product_id: int) -> list[str]:
    """ID ручних добірок, у які входить товар."""
    conn = db_connect(dict_rows=True)
    try:
        rows = conn.execute(
            _sql("""SELECT c.id FROM collections c JOIN collection_products cp ON cp.collection_id = c.id
                    WHERE cp.product_id = ? AND c.mode = 'manual' ORDER BY c.sort_order ASC, c.id ASC"""),
            (int(product_id),),
        ).fetchall()
    finally:
        conn.close()
    return [str(row_to_dict(r)["id"]) for r in rows]


def set_product_collections(product_id: int, ids) -> dict:
    """Ручні добірки товару: нові додаються в кінець, зайві прибираються. Авто-добірок не торкається."""
    try:
        from catalog_store import get_product_any

        if not get_product_any(int(product_id)):
            return {"ok": False, "error": "Товар не знайдено", "not_found": True}
        if not isinstance(ids, list):
            raise ValueError("collections має бути списком")
        wanted = list(dict.fromkeys(str(i) for i in ids))
        rows, members = _read_all()
        manual = {str(r["id"]) for r in rows if r.get("mode") == "manual"}
        unknown = [i for i in wanted if i not in manual]
        if unknown:
            raise ValueError("Не ручні або невідомі добірки: " + ", ".join(unknown))
        pid = int(product_id)
        conn = db_connect()
        try:
            for cid in manual:
                has = pid in members.get(cid, [])
                if cid in wanted and not has:
                    row = conn.execute(
                        _sql("SELECT MAX(sort_order) FROM collection_products WHERE collection_id = ?"), (cid,)
                    ).fetchone()
                    top = next(iter(row.values())) if isinstance(row, dict) else row[0]
                    conn.execute(
                        _sql("INSERT INTO collection_products (collection_id, product_id, sort_order) VALUES (?, ?, ?)"),
                        (cid, pid, int(top or 0) + 1),
                    )
                elif cid not in wanted and has:
                    conn.execute(
                        _sql("DELETE FROM collection_products WHERE collection_id = ? AND product_id = ?"), (cid, pid)
                    )
            conn.commit()
        finally:
            conn.close()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.exception("collections: не вдалось змінити добірки товару")
        return {"ok": False, "error": str(e)}
    _invalidate_cache()
    return {"ok": True, "collections": product_collection_ids(int(product_id))}
