"""Хіти продажів: топ товарів з реальних замовлень.

Рахуються лише замовлення, які магазин прийняв у роботу (не нові, не «під питанням»
і не скасовані), — щоб випадкові чи тестові оформлення не піднімали товар у хіти.
"""

from __future__ import annotations

import logging
import time

from db_core import db_connect, is_postgres as _is_postgres, sql as _sql

logger = logging.getLogger(__name__)

# Статуси, з яких рахуються продажі (нові статуси етапу 3 враховані наперед).
SOLD_STATUSES = ("confirmed", "printing", "ready", "shipped", "delivered")

DEFAULT_DAYS = 90
MAX_DAYS = 365
MAX_LIMIT = 50

_CACHE_TTL_SEC = 600.0
_cache: dict[tuple[int, int], tuple[float, list[dict]]] = {}


def _fetch_hits(days: int, limit: int) -> list[dict]:
    date_expr = f"NOW() - INTERVAL '{days} days'" if _is_postgres() else f"datetime('now', '-{days} days')"
    placeholders = ", ".join("?" for _ in SOLD_STATUSES)
    conn = db_connect()
    try:
        rows = conn.execute(
            _sql(f"""
                SELECT oi.product_id, SUM(oi.quantity) AS qty
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                WHERE o.status IN ({placeholders})
                  AND o.ordered_at > {date_expr}
                  AND oi.product_id IS NOT NULL
                GROUP BY oi.product_id
                ORDER BY qty DESC, oi.product_id ASC
                LIMIT ?
            """),
            (*SOLD_STATUSES, limit),
        ).fetchall()
    finally:
        conn.close()
    return [{"product_id": int(r[0]), "sold": int(r[1] or 0)} for r in rows if r[0] is not None]


def top_selling_products(days: int = DEFAULT_DAYS, limit: int = 12) -> list[dict]:
    """[{product_id, sold}] за останні `days` днів, найбільші продажі першими. Кеш ~10 хв."""
    days = max(1, min(int(days), MAX_DAYS))
    limit = max(1, min(int(limit), MAX_LIMIT))
    key = (days, limit)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and now - cached[0] < _CACHE_TTL_SEC:
        return cached[1]
    try:
        hits = _fetch_hits(days, limit)
    except Exception:
        logger.warning("hits: не вдалось порахувати топ продажів", exc_info=True)
        return []
    _cache[key] = (now, hits)
    return hits


def invalidate_hits_cache() -> None:
    _cache.clear()
