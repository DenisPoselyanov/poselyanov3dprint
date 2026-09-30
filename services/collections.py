"""Тематичні добірки на головній магазину («Стопка»).

Добірка буває двох видів:
  auto   — склад рахує фронтенд за правилом `rule` (ціна, новинки, знижка, хіти…);
  manual — склад задано вручну в `collection_products`.

Керування з адмінки з'явиться на етапі 5 редизайну (REDESIGN.md, розд. 4.11);
до того добірки змінюються лише в БД. При першому запуску засіваються 7 типових.
"""

from __future__ import annotations

import json
import logging
import time

from db_core import db_connect, is_postgres, sql as _sql
from services.db_utils import row_to_dict

logger = logging.getLogger(__name__)

# Ключі іконок — з набору PATHS фронтенду; кольори — токени дизайн-системи.
ICONS = ("sparkle", "tag", "moonglow", "gift", "percent", "flame", "star", "heart", "home", "cube")
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
