"""Події з вітрини (аналітика) і популярні пошукові запити.

Фронт пише `search` (коли покупець зупинився після введення ~1 с або тапнув по
результату) і події воронки `catalog_open` / `product_view` / `add_to_cart`
(раз на добу на користувача [і товар]). «Оформили» береться з таблиці orders.
"""

from __future__ import annotations

import logging
import re
import time

from db_core import db_connect, is_postgres, sql as _sql

logger = logging.getLogger(__name__)

FUNNEL_EVENT_TYPES = ("catalog_open", "product_view", "add_to_cart")
EVENT_TYPES = ("search", *FUNNEL_EVENT_TYPES, "broadcast_open")

QUERY_MIN_LEN = 2
QUERY_MAX_LEN = 64

# Заглушка, поки статистики мало (менше 3 запитів з результатами).
STATIC_POPULAR = ("fidget", "флексі", "світиться", "органайзер", "подарунок")
POPULAR_MIN_REAL = 3

_POPULAR_TTL_SEC = 600.0
_popular_cache: dict[tuple[int, int], tuple[float, list[str]]] = {}

_WS_RE = re.compile(r"\s+")


def init_events_table(conn) -> None:
    if is_postgres():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id         BIGSERIAL PRIMARY KEY,
                type       TEXT NOT NULL,
                user_id    BIGINT,
                product_id BIGINT,
                query      TEXT,
                value      INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                type       TEXT NOT NULL,
                user_id    INTEGER,
                product_id INTEGER,
                query      TEXT,
                value      INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type_created ON events(type, created_at)")


def normalize_query(raw) -> str:
    """Нижній регістр, один пробіл між словами, не довше QUERY_MAX_LEN. Порожній рядок — невалідний запит."""
    q = _WS_RE.sub(" ", str(raw or "")).strip().lower()[:QUERY_MAX_LEN].strip()
    return q if len(q) >= QUERY_MIN_LEN else ""


def record_search(user_id: int, query: str, results: int) -> bool:
    """Записати пошук. Один користувач рахується раз на запит на добу —
    повтор за день лише оновлює кількість результатів."""
    q = normalize_query(query)
    if not q or not user_id:
        return False
    results = max(0, min(int(results or 0), 10_000))
    today_expr = "CURRENT_DATE" if is_postgres() else "date('now')"
    conn = db_connect()
    try:
        row = conn.execute(
            _sql(f"""
                SELECT id FROM events
                WHERE type = 'search' AND user_id = ? AND query = ? AND created_at >= {today_expr}
                LIMIT 1
            """),
            (int(user_id), q),
        ).fetchone()
        if row:
            conn.execute(_sql("UPDATE events SET value = ? WHERE id = ?"), (results, row[0]))
        else:
            conn.execute(
                _sql("INSERT INTO events (type, user_id, query, value) VALUES ('search', ?, ?, ?)"),
                (int(user_id), q, results),
            )
        conn.commit()
    finally:
        conn.close()
    return True


def record_funnel_event(user_id: int, event_type: str, product_id: int | None = None) -> bool:
    """Подія воронки; повтор того ж (тип, користувач, товар) за добу не пишеться."""
    if event_type not in FUNNEL_EVENT_TYPES or not user_id:
        return False
    pid = int(product_id) if product_id else None
    if event_type != "catalog_open" and not pid:
        return False
    today_expr = "CURRENT_DATE" if is_postgres() else "date('now')"
    conn = db_connect()
    try:
        row = conn.execute(
            _sql(f"""
                SELECT id FROM events
                WHERE type = ? AND user_id = ? AND COALESCE(product_id, 0) = ?
                  AND created_at >= {today_expr}
                LIMIT 1
            """),
            (event_type, int(user_id), pid or 0),
        ).fetchone()
        if row:
            return False
        conn.execute(
            _sql("INSERT INTO events (type, user_id, product_id) VALUES (?, ?, ?)"),
            (event_type, int(user_id), pid),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def _fetch_popular(days: int, limit: int) -> list[str]:
    date_expr = f"NOW() - INTERVAL '{days} days'" if is_postgres() else f"datetime('now', '-{days} days')"
    conn = db_connect()
    try:
        rows = conn.execute(
            _sql(f"""
                SELECT query, COUNT(*) AS n
                FROM events
                WHERE type = 'search' AND value > 0 AND created_at > {date_expr}
                GROUP BY query
                ORDER BY n DESC, query ASC
                LIMIT ?
            """),
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows if r[0]]


def popular_searches(days: int = 30, limit: int = 5) -> list[str]:
    """Топ запитів з результатами за `days` днів; при малій статистиці доповнюємо заглушкою."""
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 20))
    key = (days, limit)
    now = time.monotonic()
    cached = _popular_cache.get(key)
    if cached and now - cached[0] < _POPULAR_TTL_SEC:
        return cached[1]
    try:
        real = _fetch_popular(days, limit)
    except Exception:
        logger.warning("events: не вдалось прочитати популярні запити", exc_info=True)
        return list(STATIC_POPULAR[:limit])
    result = list(real)
    if len(real) < POPULAR_MIN_REAL:
        result += [q for q in STATIC_POPULAR if q not in result]
    result = result[:limit]
    _popular_cache[key] = (now, result)
    return result


def invalidate_popular_cache() -> None:
    _popular_cache.clear()
