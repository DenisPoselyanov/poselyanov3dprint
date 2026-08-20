"""Акції магазину: зберігання, CRUD та розрахунок знижок.

Акції керуються з адмін-панелі (вкладка «Акції»): кожну можна увімкнути/вимкнути
та налаштувати пороги. Сервер завжди залишається джерелом істини — фронтенд
отримує ті самі правила через /api/promotions і лише малює їх.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import config
from db_core import db_connect, is_postgres, sql as _sql
from services.db_utils import row_to_dict
from services.pricing import actual_discount_after_rounding

logger = logging.getLogger(__name__)

_DB_ERROR_MESSAGE = "Помилка бази даних. Спробуйте пізніше."

# Типи акцій:
#   order_percent   — % знижки на всю суму від порогу min_order
#   order_fixed     — фіксована знижка в ₴ на всю суму від порогу min_order
#   gift_threshold  — безкоштовний подарунок за N позицій із категорії
#   info            — банер без автоматичного ефекту (промокод, умови доставки тощо)
PROMO_TYPES = ("order_percent", "order_fixed", "gift_threshold", "info")
DISCOUNT_TYPES = ("order_percent", "order_fixed")

_ID_RE = re.compile(r"^[a-z0-9_]{2,40}$")

# Стартовий набір — рівно ті акції, що працювали до появи адмінки.
DEFAULT_PROMOTIONS: list[dict] = [
    {
        "id": "gift_3_toys",
        "type": "gift_threshold",
        "title": "Безкоштовний подарунок",
        "subtitle": "Додай 3+ іграшки в кошик і обери подарунок на свій смак",
        "emoji": "🎁",
        "badge": "",
        "active": 1,
        "priority": 10,
        "value": 0,
        "min_order": 0,
        "category": "toy",
        "min_items": 3,
        "max_gift_price": 100,
        "starts_at": None,
        "ends_at": None,
    },
    {
        "id": "order_10_percent",
        "type": "order_percent",
        "title": "−10% на замовлення",
        "subtitle": "При замовленні від 500 ₴ отримай знижку 10% на всю суму",
        "emoji": "🔖",
        "badge": "−10%",
        "active": 1,
        "priority": 20,
        "value": 10,
        "min_order": 500,
        "category": "",
        "min_items": 0,
        "max_gift_price": 0,
        "starts_at": None,
        "ends_at": None,
    },
]

_COLUMNS = (
    "id", "type", "title", "subtitle", "emoji", "badge", "active", "priority",
    "value", "min_order", "category", "min_items", "max_gift_price",
    "starts_at", "ends_at",
)

_CACHE_TTL_SEC = 20.0
_cache: dict = {"ts": 0.0, "rows": None}


# ─── СХЕМА ──────────────────────────────────────────────────

def init_promotions_table(conn) -> None:
    """Створити таблицю акцій і засіяти дефолтні акції при першому запуску."""
    if is_postgres():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS promotions (
                id             TEXT PRIMARY KEY,
                type           TEXT NOT NULL,
                title          TEXT NOT NULL DEFAULT '',
                subtitle       TEXT NOT NULL DEFAULT '',
                emoji          TEXT NOT NULL DEFAULT '🔥',
                badge          TEXT NOT NULL DEFAULT '',
                active         INTEGER NOT NULL DEFAULT 1,
                priority       INTEGER NOT NULL DEFAULT 100,
                value          INTEGER NOT NULL DEFAULT 0,
                min_order      INTEGER NOT NULL DEFAULT 0,
                category       TEXT NOT NULL DEFAULT '',
                min_items      INTEGER NOT NULL DEFAULT 0,
                max_gift_price INTEGER NOT NULL DEFAULT 0,
                starts_at      TIMESTAMPTZ,
                ends_at        TIMESTAMPTZ,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS promotions (
                id             TEXT PRIMARY KEY,
                type           TEXT NOT NULL,
                title          TEXT NOT NULL DEFAULT '',
                subtitle       TEXT NOT NULL DEFAULT '',
                emoji          TEXT NOT NULL DEFAULT '🔥',
                badge          TEXT NOT NULL DEFAULT '',
                active         INTEGER NOT NULL DEFAULT 1,
                priority       INTEGER NOT NULL DEFAULT 100,
                value          INTEGER NOT NULL DEFAULT 0,
                min_order      INTEGER NOT NULL DEFAULT 0,
                category       TEXT NOT NULL DEFAULT '',
                min_items      INTEGER NOT NULL DEFAULT 0,
                max_gift_price INTEGER NOT NULL DEFAULT 0,
                starts_at      TIMESTAMP,
                ends_at        TIMESTAMP,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promotions_active ON promotions(active)")

    row = conn.execute("SELECT COUNT(*) FROM promotions").fetchone()
    count = int(next(iter(row.values())) if isinstance(row, dict) else row[0])
    if count == 0:
        for promo in DEFAULT_PROMOTIONS:
            _insert_promotion(conn, promo)
    _invalidate_cache()


def _insert_promotion(conn, promo: dict) -> None:
    conn.execute(
        _sql("""
            INSERT INTO promotions
            (id, type, title, subtitle, emoji, badge, active, priority, value,
             min_order, category, min_items, max_gift_price, starts_at, ends_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """),
        tuple(promo.get(col) for col in _COLUMNS),
    )


# ─── ЧИТАННЯ ────────────────────────────────────────────────

def _invalidate_cache() -> None:
    _cache["ts"] = 0.0
    _cache["rows"] = None


def _fetch_all() -> list[dict]:
    conn = db_connect(dict_rows=True)
    try:
        rows = conn.execute(
            "SELECT " + ", ".join(_COLUMNS) + " FROM promotions ORDER BY priority ASC, id ASC"
        ).fetchall()
        return [_normalize_row(row_to_dict(r)) for r in rows]
    finally:
        conn.close()


def _normalize_row(row: dict) -> dict:
    return {
        "id": str(row.get("id") or ""),
        "type": str(row.get("type") or "info"),
        "title": row.get("title") or "",
        "subtitle": row.get("subtitle") or "",
        "emoji": row.get("emoji") or "🔥",
        "badge": row.get("badge") or "",
        "active": bool(row.get("active")),
        "priority": int(row.get("priority") or 0),
        "value": int(row.get("value") or 0),
        "min_order": int(row.get("min_order") or 0),
        "category": row.get("category") or "",
        "min_items": int(row.get("min_items") or 0),
        "max_gift_price": int(row.get("max_gift_price") or 0),
        "starts_at": row.get("starts_at") or None,
        "ends_at": row.get("ends_at") or None,
    }


def _cached_promotions() -> list[dict]:
    """Всі акції з короткочасним кешем. При збої БД — дефолтний набір."""
    now = time.monotonic()
    if _cache["rows"] is not None and (now - _cache["ts"]) < _CACHE_TTL_SEC:
        return _cache["rows"]
    try:
        rows = _fetch_all()
    except Exception:
        # Не кешуємо фолбек — щойно БД відповість, повернемось до реальних налаштувань.
        logger.warning("promotions: не вдалось прочитати таблицю, використовую дефолти", exc_info=True)
        return [_normalize_row(dict(p)) for p in DEFAULT_PROMOTIONS]
    _cache["rows"] = rows
    _cache["ts"] = now
    return rows


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_in_window(promo: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    starts = _parse_dt(promo.get("starts_at"))
    ends = _parse_dt(promo.get("ends_at"))
    if starts and now < starts:
        return False
    if ends and now > ends:
        return False
    return True


def get_active_promotions() -> list[dict]:
    """Акції, які зараз діють (увімкнені + у межах дат)."""
    if not config.PROMOTION_ENABLED:
        return []
    return [p for p in _cached_promotions() if p["active"] and _is_in_window(p)]


def list_promotions() -> list[dict]:
    """Повний список для адмін-панелі (разом з вимкненими)."""
    try:
        return _fetch_all()
    except Exception:
        logger.exception("list_promotions failed")
        return []


def get_promotion(promo_id: str) -> dict | None:
    promo_id = (promo_id or "").strip().lower()
    if not promo_id:
        return None
    conn = db_connect(dict_rows=True)
    try:
        row = conn.execute(
            _sql("SELECT " + ", ".join(_COLUMNS) + " FROM promotions WHERE id = ?"),
            (promo_id,),
        ).fetchone()
    finally:
        conn.close()
    return _normalize_row(row_to_dict(row)) if row else None


# ─── РОЗРАХУНОК ─────────────────────────────────────────────

def _raw_discount(promo: dict, subtotal: int) -> int:
    if subtotal < promo["min_order"]:
        return 0
    if promo["type"] == "order_percent":
        return int(subtotal * promo["value"] / 100)
    if promo["type"] == "order_fixed":
        return min(promo["value"], subtotal)
    return 0


def compute_order_discount(subtotal: int) -> int:
    """Найвигідніша для клієнта автоматична знижка на суму замовлення."""
    subtotal = max(0, int(subtotal or 0))
    if subtotal <= 0:
        return 0
    best = 0
    for promo in get_active_promotions():
        if promo["type"] not in DISCOUNT_TYPES:
            continue
        best = max(best, _raw_discount(promo, subtotal))
    if best <= 0:
        return 0
    return actual_discount_after_rounding(subtotal, best)


def get_gift_rule() -> dict | None:
    """Правило подарунка з найвищим пріоритетом або None, якщо акція вимкнена."""
    for promo in get_active_promotions():
        if promo["type"] == "gift_threshold" and promo["min_items"] > 0:
            return promo
    return None


def public_promotions() -> dict:
    """Payload для вітрини: активні акції + похідні правила."""
    active = get_active_promotions()
    gift = get_gift_rule()
    return {
        "promotions": active,
        "gift_rule": (
            {
                "category": gift["category"],
                "min_items": gift["min_items"],
                "max_gift_price": gift["max_gift_price"],
            }
            if gift
            else None
        ),
    }


# ─── ВАЛІДАЦІЯ ТА CRUD ──────────────────────────────────────

def _normalize_payload(data: dict, *, id_override: str | None = None) -> tuple[dict | None, str | None]:
    promo_id = (id_override or data.get("id") or "").strip().lower()
    if not _ID_RE.match(promo_id):
        return None, "ID акції: латиниця, цифри та _ (2–40 символів)"

    ptype = str(data.get("type") or "").strip().lower()
    if ptype not in PROMO_TYPES:
        return None, f"Тип має бути одним із: {', '.join(PROMO_TYPES)}"

    title = str(data.get("title") or "").strip()
    if not title:
        return None, "Назва акції обов'язкова"

    try:
        value = max(0, int(data.get("value") or 0))
        min_order = max(0, int(data.get("min_order") or 0))
        min_items = max(0, int(data.get("min_items") or 0))
        max_gift_price = max(0, int(data.get("max_gift_price") or 0))
        priority = max(0, int(data.get("priority") or 100))
    except (TypeError, ValueError):
        return None, "Невірні числові параметри акції"

    if ptype == "order_percent":
        if not 1 <= value <= 100:
            return None, "Відсоток знижки має бути від 1 до 100"
    elif ptype == "order_fixed":
        if value <= 0:
            return None, "Сума знижки має бути більшою за 0"
    elif ptype == "gift_threshold":
        if min_items <= 0:
            return None, "Вкажи, скільки товарів потрібно для подарунка"
        if max_gift_price <= 0:
            return None, "Вкажи максимальну ціну подарунка"

    starts_at = str(data.get("starts_at") or "").strip() or None
    ends_at = str(data.get("ends_at") or "").strip() or None
    for label, raw in (("Дата початку", starts_at), ("Дата завершення", ends_at)):
        if raw and _parse_dt(raw) is None:
            return None, f"{label}: невірний формат дати"
    if starts_at and ends_at and _parse_dt(ends_at) <= _parse_dt(starts_at):
        return None, "Дата завершення має бути пізнішою за дату початку"

    return {
        "id": promo_id,
        "type": ptype,
        "title": title,
        "subtitle": str(data.get("subtitle") or "").strip(),
        "emoji": str(data.get("emoji") or "🔥").strip()[:8] or "🔥",
        "badge": str(data.get("badge") or "").strip()[:16],
        "active": 1 if data.get("active", 1) in (1, True, "1", "true", "on") else 0,
        "priority": priority,
        "value": value,
        "min_order": min_order,
        "category": str(data.get("category") or "").strip(),
        "min_items": min_items,
        "max_gift_price": max_gift_price,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }, None


def create_promotion(data: dict) -> dict:
    payload, err = _normalize_payload(data)
    if err:
        return {"ok": False, "error": err}
    if get_promotion(payload["id"]):
        return {"ok": False, "error": "Акція з таким ID вже існує"}

    conn = db_connect()
    try:
        _insert_promotion(conn, payload)
        conn.commit()
    except Exception:
        logger.exception("create_promotion failed for id=%s", payload["id"])
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()

    _invalidate_cache()
    return {"ok": True, "created": True, "promotion": get_promotion(payload["id"])}


def update_promotion(promo_id: str, data: dict) -> dict:
    promo_id = (promo_id or "").strip().lower()
    existing = get_promotion(promo_id)
    if not existing:
        return {"ok": False, "error": "Акцію не знайдено"}

    merged = {**existing, "active": 1 if existing["active"] else 0, **data}
    payload, err = _normalize_payload(merged, id_override=promo_id)
    if err:
        return {"ok": False, "error": err}

    conn = db_connect()
    try:
        conn.execute(
            _sql("""
                UPDATE promotions SET
                    type = ?, title = ?, subtitle = ?, emoji = ?, badge = ?, active = ?,
                    priority = ?, value = ?, min_order = ?, category = ?, min_items = ?,
                    max_gift_price = ?, starts_at = ?, ends_at = ?
                WHERE id = ?
            """),
            (
                payload["type"], payload["title"], payload["subtitle"], payload["emoji"],
                payload["badge"], payload["active"], payload["priority"], payload["value"],
                payload["min_order"], payload["category"], payload["min_items"],
                payload["max_gift_price"], payload["starts_at"], payload["ends_at"],
                promo_id,
            ),
        )
        conn.commit()
    except Exception:
        logger.exception("update_promotion failed for id=%s", promo_id)
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()

    _invalidate_cache()
    return {"ok": True, "created": False, "promotion": get_promotion(promo_id)}


def set_promotion_active(promo_id: str, active: bool) -> dict:
    promo_id = (promo_id or "").strip().lower()
    if not get_promotion(promo_id):
        return {"ok": False, "error": "Акцію не знайдено"}
    conn = db_connect()
    try:
        conn.execute(
            _sql("UPDATE promotions SET active = ? WHERE id = ?"),
            (1 if active else 0, promo_id),
        )
        conn.commit()
    except Exception:
        logger.exception("set_promotion_active failed for id=%s", promo_id)
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()

    _invalidate_cache()
    return {"ok": True, "promotion": get_promotion(promo_id)}


def delete_promotion(promo_id: str) -> dict:
    promo_id = (promo_id or "").strip().lower()
    if not get_promotion(promo_id):
        return {"ok": False, "error": "Акцію не знайдено"}
    conn = db_connect()
    try:
        conn.execute(_sql("DELETE FROM promotions WHERE id = ?"), (promo_id,))
        conn.commit()
    except Exception:
        logger.exception("delete_promotion failed for id=%s", promo_id)
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()

    _invalidate_cache()
    return {"ok": True}
