"""Дані для сторінки «Огляд» в адмінці: KPI з дельтою, виторг по днях, воронка, топ товарів, «потребує уваги»."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from catalog_store import FILAMENTS_CACHE
from db_core import db_connect, is_postgres, sql as _sql
from services.hits import SOLD_STATUSES
from services.orders import row_to_dict

ALL_TIME = 0
PERIODS = (1, 7, 30, ALL_TIME)
CHART_MIN_DAYS = 7
MONTHLY_AFTER_DAYS = 90  # «весь час» довший за це групується по місяцях
TOP_LIMIT = 5
ATTENTION_LIMIT = 5

try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo("Europe/Kyiv")
except Exception:  # немає tzdata — рахуємо за UTC+3
    _TZ = timezone(timedelta(hours=3))


def _db_ts(dt: datetime):
    """Параметр запиту: Postgres бере aware datetime, SQLite порівнює рядки UTC."""
    dt = dt.astimezone(timezone.utc)
    return dt if is_postgres() else dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_ts(raw) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(raw).replace(" ", "T")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _local_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=_TZ)


def _delta_pct(cur: float, prev: float) -> float | None:
    if not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def _distinct_users(conn, sql: str, params: tuple) -> int:
    row = conn.execute(_sql(sql), params).fetchone()
    return int(row_to_dict(row).get("n") or 0) if row else 0


def _period_stats(conn, start: datetime, end: datetime) -> dict:
    """Лічильники за [start, end): продажі, унікальні користувачі по кроках воронки."""
    s, e = _db_ts(start), _db_ts(end)
    placeholders = ", ".join("?" * len(SOLD_STATUSES))
    sales = row_to_dict(
        conn.execute(
            _sql(
                f"SELECT COUNT(*) AS n, COALESCE(SUM(total_price), 0) AS revenue FROM orders "
                f"WHERE status IN ({placeholders}) AND ordered_at >= ? AND ordered_at < ?"
            ),
            (*SOLD_STATUSES, s, e),
        ).fetchone()
    )
    orders = int(sales.get("n") or 0)
    revenue = int(sales.get("revenue") or 0)

    def events_users(event_type: str) -> int:
        return _distinct_users(
            conn,
            "SELECT COUNT(DISTINCT user_id) AS n FROM events WHERE type = ? AND created_at >= ? AND created_at < ?",
            (event_type, s, e),
        )

    ordered_users = _distinct_users(
        conn,
        "SELECT COUNT(DISTINCT user_id) AS n FROM orders "
        "WHERE user_id > 0 AND status NOT IN ('cancelled', 'draft') AND ordered_at >= ? AND ordered_at < ?",
        (s, e),
    )
    catalog_users = events_users("catalog_open")
    try:
        started = _distinct_users(
            conn, "SELECT COUNT(*) AS n FROM users WHERE last_start_at >= ? AND last_start_at < ?", (s, e)
        )
    except Exception:  # users.last_start_at ще не мігровано
        started = 0
    return {
        "revenue": revenue,
        "orders": orders,
        "avg_check": round(revenue / orders) if orders else 0,
        "started": started,
        "catalog": catalog_users,
        "cart": events_users("add_to_cart"),
        "ordered": ordered_users,
        "conversion": min(100.0, round(ordered_users / catalog_users * 100, 1)) if catalog_users else None,
    }


def _first_sale_day(conn, fallback: date) -> date:
    """Локальний день першого продажу (для періоду «весь час»)."""
    placeholders = ", ".join("?" * len(SOLD_STATUSES))
    row = row_to_dict(
        conn.execute(
            _sql(f"SELECT MIN(ordered_at) AS first FROM orders WHERE status IN ({placeholders})"),
            SOLD_STATUSES,
        ).fetchone()
    )
    ts = _parse_ts(row.get("first")) if row.get("first") else None
    return ts.astimezone(_TZ).date() if ts else fallback


def _month_series(chart_start: date, today: date) -> dict:
    series, d = {}, chart_start.replace(day=1)
    while d <= today:
        series[d] = 0
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return series


def get_overview(days: int = 7, now: datetime | None = None) -> dict:
    """days: 1/7/30 — останні N днів із порівнянням до попереднього; 0 — весь час (без порівняння)."""
    days = days if days in PERIODS else 7
    now = (now or datetime.now(timezone.utc)).astimezone(_TZ)
    today = now.date()
    end = now + timedelta(seconds=1)
    all_time = days == ALL_TIME

    conn = db_connect(dict_rows=True)
    try:
        if all_time:
            first_day = _first_sale_day(conn, today)
            start = _local_midnight(date(2000, 1, 1))
            chart_start_day = min(first_day, today - timedelta(days=CHART_MIN_DAYS - 1))
            span_days = (today - chart_start_day).days + 1
            by_month = span_days > MONTHLY_AFTER_DAYS
            cur = _period_stats(conn, start, end)
            prev = {key: None for key in cur}
        else:
            start = _local_midnight(today - timedelta(days=days - 1))
            prev_start = _local_midnight(today - timedelta(days=2 * days - 1))
            chart_start_day = today - timedelta(days=max(days, CHART_MIN_DAYS) - 1)
            by_month = False
            cur = _period_stats(conn, start, end)
            prev = _period_stats(conn, prev_start, start)
        chart_start = _local_midnight(chart_start_day)

        placeholders = ", ".join("?" * len(SOLD_STATUSES))
        rows = [
            row_to_dict(r)
            for r in conn.execute(
                _sql(
                    f"SELECT ordered_at, total_price FROM orders "
                    f"WHERE status IN ({placeholders}) AND ordered_at >= ? AND ordered_at < ?"
                ),
                (*SOLD_STATUSES, _db_ts(chart_start), _db_ts(end)),
            ).fetchall()
        ]
        if by_month:
            series = _month_series(chart_start_day, today)
        else:
            series = {chart_start_day + timedelta(days=i): 0 for i in range((today - chart_start_day).days + 1)}
        for row in rows:
            ts = _parse_ts(row.get("ordered_at"))
            if ts:
                key = ts.astimezone(_TZ).date()
                if by_month:
                    key = key.replace(day=1)
                if key in series:
                    series[key] += int(row.get("total_price") or 0)

        top = [
            row_to_dict(r)
            for r in conn.execute(
                _sql(
                    f"SELECT oi.product_name AS name, SUM(oi.quantity) AS qty, "
                    f"SUM(oi.price * oi.quantity) AS revenue "
                    f"FROM order_items oi JOIN orders o ON o.id = oi.order_id "
                    f"WHERE o.status IN ({placeholders}) AND o.ordered_at >= ? AND o.ordered_at < ? "
                    f"AND COALESCE(oi.is_gift, 0) = 0 "
                    f"GROUP BY oi.product_name ORDER BY qty DESC, revenue DESC LIMIT ?"
                ),
                (*SOLD_STATUSES, _db_ts(start), _db_ts(end), TOP_LIMIT),
            ).fetchall()
        ]

        new_orders = [
            row_to_dict(r)
            for r in conn.execute(
                _sql("SELECT id, first_name, ordered_at FROM orders WHERE status = 'new' ORDER BY id LIMIT ?"),
                (ATTENTION_LIMIT,),
            ).fetchall()
        ]
        new_total = _distinct_users(conn, "SELECT COUNT(*) AS n FROM orders WHERE status = ?", ("new",))
        pending_price = [
            row_to_dict(r)
            for r in conn.execute(
                _sql(
                    "SELECT id, first_name FROM orders WHERE price_pending = ? "
                    "AND status NOT IN ('cancelled', 'delivered') ORDER BY id LIMIT ?"
                ),
                (1, ATTENTION_LIMIT),
            ).fetchall()
        ]
    finally:
        conn.close()

    funnel_keys = (
        ("started", "Запустили бота"),
        ("catalog", "Відкрили каталог"),
        ("cart", "Додали в кошик"),
        ("ordered", "Оформили"),
    )
    funnel, prev_count = [], None
    for key, label in funnel_keys:
        count = cur[key]
        pct = None if prev_count in (None, 0) else min(100, round(count / prev_count * 100))
        funnel.append({"key": key, "label": label, "count": count, "pct": pct})
        prev_count = count

    return {
        "days": days,
        "kpi": {
            key: {"value": cur[key], "delta": _delta_pct(cur[key], prev[key]) if cur[key] is not None else None}
            for key in ("revenue", "orders", "avg_check")
        }
        | {
            "conversion": {
                "value": cur["conversion"],
                "delta": (
                    round(cur["conversion"] - prev["conversion"], 1)
                    if cur["conversion"] is not None and prev["conversion"] is not None
                    else None
                ),
            }
        },
        "chart_unit": "month" if by_month else "day",
        "chart": [{"date": d.isoformat(), "revenue": v} for d, v in series.items()],
        "funnel": funnel,
        "top_products": [
            {"name": r.get("name") or "—", "qty": int(r.get("qty") or 0), "revenue": int(r.get("revenue") or 0)}
            for r in top
        ],
        "attention": {
            "new_orders": {"count": new_total, "items": new_orders},
            "pending_price": {"count": len(pending_price), "items": pending_price},
            "filaments_out": [f.get("name") for f in FILAMENTS_CACHE if not f.get("available", True)],
        },
    }
