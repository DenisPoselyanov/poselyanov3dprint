"""Глобальні налаштування магазину (key/value у БД).

Зберігає перемикачі, які власник міняє з адмін-панелі без редеплою.
«Сумування купонів з акціями» — глобальний рубильник, без якого галочка на
окремому купоні не діє; поріг показу «N продано» в картці товару; режим
публікації відгуків.
"""

from __future__ import annotations

import logging
import time

from db_core import db_connect, is_postgres, sql as _sql

logger = logging.getLogger(__name__)

COUPON_STACKING_KEY = "coupon_stacking_enabled"
# «N продано» в картці товару показується лише від цього порогу (REDESIGN.md, розд. 5.3)
SOLD_COUNT_MIN_KEY = "sold_count_min"
# Публікувати відгуки одразу чи після схвалення (форма відгуку — етап 3)
REVIEWS_AUTO_PUBLISH_KEY = "reviews_auto_publish"

# Дефолти — стан «як було до появи фічі».
DEFAULTS: dict[str, str] = {
    COUPON_STACKING_KEY: "0",
    SOLD_COUNT_MIN_KEY: "5",
    REVIEWS_AUTO_PUBLISH_KEY: "0",
}
SOLD_COUNT_MIN_LIMIT = 10_000

_CACHE_TTL_SEC = 20.0
_cache: dict = {"ts": 0.0, "rows": None}


def init_settings_table(conn) -> None:
    if is_postgres():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    _invalidate_cache()


def _invalidate_cache() -> None:
    _cache["ts"] = 0.0
    _cache["rows"] = None


def _fetch_all() -> dict[str, str]:
    conn = db_connect(dict_rows=True)
    try:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    finally:
        conn.close()
    result: dict[str, str] = {}
    for row in rows:
        if isinstance(row, dict):
            result[str(row["key"])] = str(row["value"])
        else:
            result[str(row[0])] = str(row[1])
    return result


def _cached() -> dict[str, str]:
    now = time.monotonic()
    if _cache["rows"] is not None and (now - _cache["ts"]) < _CACHE_TTL_SEC:
        return _cache["rows"]
    try:
        rows = _fetch_all()
    except Exception:
        # Не кешуємо фолбек — щойно БД відповість, повернемось до реальних налаштувань.
        logger.warning("settings: не вдалось прочитати таблицю, використовую дефолти", exc_info=True)
        return dict(DEFAULTS)
    merged = {**DEFAULTS, **rows}
    _cache["rows"] = merged
    _cache["ts"] = now
    return merged


def get_setting(key: str, default: str | None = None) -> str:
    return _cached().get(key, DEFAULTS.get(key, default if default is not None else ""))


def get_bool(key: str) -> bool:
    return str(get_setting(key)).strip().lower() in ("1", "true", "yes", "on")


def set_setting(key: str, value: str) -> None:
    value = str(value)
    conn = db_connect()
    try:
        if is_postgres():
            conn.execute(
                _sql(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"
                ),
                (key, value),
            )
        else:
            conn.execute(
                _sql(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "updated_at = CURRENT_TIMESTAMP"
                ),
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()
    _invalidate_cache()


def coupon_stacking_enabled() -> bool:
    """Чи дозволено взагалі сумувати купон з акційною знижкою."""
    return get_bool(COUPON_STACKING_KEY)


def sold_count_min() -> int:
    try:
        return max(0, min(int(get_setting(SOLD_COUNT_MIN_KEY)), SOLD_COUNT_MIN_LIMIT))
    except (TypeError, ValueError):
        return int(DEFAULTS[SOLD_COUNT_MIN_KEY])


def public_settings() -> dict:
    """Налаштування, які потрібні вітрині."""
    return {"coupon_stacking_enabled": coupon_stacking_enabled()}


def admin_settings() -> dict:
    return {
        "coupon_stacking_enabled": coupon_stacking_enabled(),
        "sold_count_min": sold_count_min(),
        "reviews_auto_publish": get_bool(REVIEWS_AUTO_PUBLISH_KEY),
    }


def update_admin_settings(data: dict) -> dict:
    updates: dict[str, str] = {}
    for key in (COUPON_STACKING_KEY, REVIEWS_AUTO_PUBLISH_KEY):
        if key in data:
            updates[key] = "1" if data.get(key) in (1, True, "1", "true", "on", "yes") else "0"
    if SOLD_COUNT_MIN_KEY in data:
        try:
            threshold = int(data.get(SOLD_COUNT_MIN_KEY))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Поріг «продано» має бути числом"}
        if not 0 <= threshold <= SOLD_COUNT_MIN_LIMIT:
            return {"ok": False, "error": f"Поріг «продано» — від 0 до {SOLD_COUNT_MIN_LIMIT}"}
        updates[SOLD_COUNT_MIN_KEY] = str(threshold)
    try:
        for key, value in updates.items():
            set_setting(key, value)
    except Exception:
        logger.exception("update_admin_settings failed")
        return {"ok": False, "error": "Помилка бази даних. Спробуйте пізніше."}
    return {"ok": True, "settings": admin_settings()}
