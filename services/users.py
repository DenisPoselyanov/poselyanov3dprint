"""User persistence and access helpers."""

from __future__ import annotations

import csv
import io

from db_core import db_connect, is_postgres as _is_postgres, sql as _sql


def _save_user_record(user_id: int, name: str, username: str) -> None:
    conn = db_connect()
    if _is_postgres():
        conn.execute(
            "INSERT INTO users (id, name, username) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (user_id, name, username),
        )
    else:
        conn.execute(
            """
            INSERT OR IGNORE INTO users (id, name, username)
            VALUES (?, ?, ?)
            """,
            (user_id, name, username),
        )
    conn.commit()
    conn.close()


def _now_sql() -> str:
    return "NOW()" if _is_postgres() else "CURRENT_TIMESTAMP"


def record_start(user_id: int, name: str = "", username: str = "") -> None:
    """Зафіксувати запуск бота (/start) — +1 до лічильника запусків."""
    if not user_id or int(user_id) <= 0:
        return
    handle = f"@{username.lstrip('@')}" if (username or "").strip() else ""
    save_user_id(user_id, name, username)
    conn = db_connect()
    conn.execute(
        _sql(
            f"""
            UPDATE users
            SET start_count = start_count + 1,
                last_start_at = {_now_sql()},
                name = CASE WHEN ? <> '' THEN ? ELSE name END,
                username = CASE WHEN ? <> '' THEN ? ELSE username END
            WHERE id = ?
            """
        ),
        (name or "", name or "", handle, handle, int(user_id)),
    )
    conn.commit()
    conn.close()


def record_activity(user_id: int) -> None:
    """Зафіксувати будь-яку взаємодію з ботом — +1 до лічильника активності."""
    if not user_id or int(user_id) <= 0:
        return
    conn = db_connect()
    conn.execute(
        _sql(
            f"""
            UPDATE users
            SET activity_count = activity_count + 1,
                last_seen_at = {_now_sql()}
            WHERE id = ?
            """
        ),
        (int(user_id),),
    )
    conn.commit()
    conn.close()


_USERS_SORT_COLUMNS = {
    "starts": "u.start_count",
    "activity": "u.activity_count",
    "joined": "u.joined_at",
    "last_start": "u.last_start_at",
    "last_seen": "u.last_seen_at",
    "last_activity": "COALESCE(u.last_seen_at, u.last_start_at, u.joined_at)",
    "orders": "COALESCE(p.cnt, 0)",
    "spent": "COALESCE(p.spent, 0)",
    "name": "u.name",
}

# Сегменти «Клієнтів» і «Розсилок». Покупка = замовлення у статусах confirmed…delivered (SOLD_STATUSES,
# як виторг «Огляду»); сума = SUM(total_price) таких замовлень.
SEGMENT_KEYS = ("all", "buyers", "non_buyers", "inactive", "vip")
SEGMENT_LABELS = {
    "all": "Усі",
    "buyers": "Купували",
    "non_buyers": "Не купували",
    "inactive": "Неактивні 30+ днів",
    "vip": "Від 1000 ₴",
}
VIP_MIN_SPENT = 1000
INACTIVE_DAYS = 30

_LAST_ACTIVITY_SQL = "COALESCE(u.last_seen_at, u.last_start_at, u.joined_at)"


def normalize_segment(segment) -> str:
    seg = str(segment or "all").strip().lower()
    return seg if seg in SEGMENT_KEYS else "all"


def _sold_statuses() -> tuple:
    from services.hits import SOLD_STATUSES

    return tuple(SOLD_STATUSES)


def _purchases_join() -> tuple[str, tuple]:
    """LEFT JOIN-підзапит із покупками клієнта (cnt, spent, last_order_at, all_cnt) та його параметри."""
    statuses = _sold_statuses()
    marks = ", ".join("?" * len(statuses))
    sql = (
        "LEFT JOIN (SELECT user_id, "
        f"SUM(CASE WHEN status IN ({marks}) THEN 1 ELSE 0 END) AS cnt, "
        f"SUM(CASE WHEN status IN ({marks}) THEN COALESCE(total_price, 0) ELSE 0 END) AS spent, "
        f"MAX(CASE WHEN status IN ({marks}) THEN ordered_at END) AS last_order_at, "
        "COUNT(*) AS all_cnt FROM orders GROUP BY user_id) p ON p.user_id = u.id"
    )
    return sql, statuses * 3


def _inactive_clause() -> str:
    cutoff = (
        f"NOW() - INTERVAL '{INACTIVE_DAYS} days'"
        if _is_postgres()
        else f"datetime('now', '-{INACTIVE_DAYS} days')"
    )
    return f"{_LAST_ACTIVITY_SQL} < {cutoff}"


def _segment_clause(segment: str) -> str:
    """SQL-умова сегмента для запиту, де є `users u` і `p` (див. _purchases_join)."""
    if segment == "buyers":
        return "COALESCE(p.cnt, 0) > 0"
    if segment == "non_buyers":
        return "COALESCE(p.cnt, 0) = 0"
    if segment == "vip":
        return f"COALESCE(p.spent, 0) >= {VIP_MIN_SPENT}"
    if segment == "inactive":
        return _inactive_clause()
    return ""


def _search_clause(search: str) -> tuple[str, list]:
    term = (search or "").strip()
    if not term:
        return "", []
    like = f"%{term.lower()}%"
    clause = "(LOWER(COALESCE(u.name, '')) LIKE ? OR LOWER(COALESCE(u.username, '')) LIKE ?"
    params: list = [like, like]
    digits = term.lstrip("@").strip()
    if digits.isdigit():
        clause += " OR u.id = ?"
        params.append(int(digits))
    clause += ")"
    return clause, params


def _build_where(search: str, blocked: str, segment: str) -> tuple[str, list]:
    where: list[str] = []
    params: list = []
    s_clause, s_params = _search_clause(search)
    if s_clause:
        where.append(s_clause)
        params.extend(s_params)
    if blocked == "only":
        where.append("u.blocked = 1")
    elif blocked == "active":
        where.append("u.blocked = 0")
    seg_clause = _segment_clause(segment)
    if seg_clause:
        where.append(seg_clause)
    return (("WHERE " + " AND ".join(where)) if where else ""), params


def _iso(value) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def segment_counts(search: str = "", blocked: str = "all") -> dict:
    """Кількість клієнтів у кожному сегменті — одним агрегованим запитом.
    `search`/`blocked` звужують вибірку так само, як у списку, щоб картки збігались із таблицею."""
    join_sql, join_params = _purchases_join()
    where_sql, where_params = _build_where(search, blocked, "all")
    conn = db_connect()
    try:
        row = conn.execute(
            _sql(
                f"""
                SELECT COUNT(*),
                       COALESCE(SUM(CASE WHEN COALESCE(p.cnt, 0) > 0 THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN COALESCE(p.cnt, 0) = 0 THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN {_inactive_clause()} THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN COALESCE(p.spent, 0) >= {VIP_MIN_SPENT} THEN 1 ELSE 0 END), 0)
                FROM users u {join_sql} {where_sql}
                """
            ),
            tuple(join_params) + tuple(where_params),
        ).fetchone()
    finally:
        conn.close()
    return dict(zip(SEGMENT_KEYS, (int(v or 0) for v in row)))


def segment_user_ids(segment: str, include_blocked: bool = False) -> list[int]:
    """Telegram ID клієнтів сегмента (для розсилки: без тих, хто заблокував бота)."""
    segment = normalize_segment(segment)
    join_sql, join_params = _purchases_join()
    where_sql, where_params = _build_where("", "all" if include_blocked else "active", segment)
    conn = db_connect()
    try:
        rows = conn.execute(
            _sql(f"SELECT u.id FROM users u {join_sql} {where_sql} ORDER BY u.id"),
            tuple(join_params) + tuple(where_params),
        ).fetchall()
    finally:
        conn.close()
    return [int(r[0]) for r in rows if int(r[0]) > 0]


_USER_SELECT = """
    SELECT u.id, u.name, u.username, u.blocked, u.joined_at, u.start_count, u.last_start_at,
           u.activity_count, u.last_seen_at,
           COALESCE(p.all_cnt, 0) AS orders_count,
           COALESCE(p.spent, 0)   AS orders_total,
           COALESCE(p.cnt, 0)     AS purchases_count,
           p.last_order_at        AS last_order_at,
           {last_activity}        AS last_activity_at
    FROM users u {join_sql}
"""


def _row_to_user(r) -> dict:
    return {
        "id": int(r[0]),
        "name": r[1] or "",
        "username": r[2] or "",
        "blocked": bool(r[3]),
        "joined_at": _iso(r[4]),
        "start_count": int(r[5] or 0),
        "last_start_at": _iso(r[6]),
        "activity_count": int(r[7] or 0),
        "last_seen_at": _iso(r[8]),
        "orders_count": int(r[9] or 0),
        "orders_total": int(r[10] or 0),
        "purchases_count": int(r[11] or 0),
        "last_order_at": _iso(r[12]),
        "last_activity_at": _iso(r[13]),
    }


def list_users(
    search: str = "",
    sort: str = "starts",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    blocked: str = "all",
    segment: str = "all",
) -> dict:
    """Список користувачів для адмін-панелі: лічильники, покупки (orders_count — усі замовлення,
    purchases_count/orders_total — лише confirmed…delivered), остання активність, сегмент."""
    sort_col = _USERS_SORT_COLUMNS.get(sort, "u.start_count")
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    limit = max(1, min(200, int(limit or 50)))
    offset = max(0, int(offset or 0))
    segment = normalize_segment(segment)

    join_sql, join_params = _purchases_join()
    where_sql, where_params = _build_where(search, blocked, segment)

    conn = db_connect()
    try:
        total = conn.execute(
            _sql(f"SELECT COUNT(*) FROM users u {join_sql} {where_sql}"),
            tuple(join_params) + tuple(where_params),
        ).fetchone()[0]
        rows = conn.execute(
            _sql(
                _USER_SELECT.format(last_activity=_LAST_ACTIVITY_SQL, join_sql=join_sql)
                + f" {where_sql} ORDER BY ({sort_col} IS NULL), {sort_col} {direction}, u.id DESC LIMIT ? OFFSET ?"
            ),
            tuple(join_params) + tuple(where_params) + (limit, offset),
        ).fetchall()
    finally:
        conn.close()

    return {
        "users": [_row_to_user(r) for r in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "segment": segment,
    }


def export_users(search: str = "", blocked: str = "all", segment: str = "all", limit: int = 20000) -> list[dict]:
    """Усі клієнти вибірки (для CSV): найактивніші нещодавно — зверху."""
    join_sql, join_params = _purchases_join()
    where_sql, where_params = _build_where(search, blocked, normalize_segment(segment))
    conn = db_connect()
    try:
        rows = conn.execute(
            _sql(
                _USER_SELECT.format(last_activity=_LAST_ACTIVITY_SQL, join_sql=join_sql)
                + f" {where_sql} ORDER BY ({_LAST_ACTIVITY_SQL} IS NULL), {_LAST_ACTIVITY_SQL} DESC, u.id DESC LIMIT ?"
            ),
            tuple(join_params) + tuple(where_params) + (int(limit),),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_user(r) for r in rows]


def get_user(user_id: int) -> dict | None:
    conn = db_connect()
    try:
        row = conn.execute(
            _sql("SELECT id, name, username, blocked FROM users WHERE id = ?"), (int(user_id),)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {"id": int(row[0]), "name": row[1] or "", "username": row[2] or "", "blocked": bool(row[3])}


def users_to_csv(users: list[dict]) -> str:
    """CSV клієнтів (UTF-8, `;`, захист від формул — як orders.csv)."""
    from services.manual_orders import _csv_cell

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        ["ID", "Імʼя", "Username", "Статус", "Замовлень", "Сума, ₴", "Остання активність", "Перший запуск", "Запусків"]
    )
    for u in users:
        username = str(u.get("username") or "")
        writer.writerow(
            [_csv_cell(v) for v in (
                u["id"], u.get("name"), username.lstrip("@") if username.startswith("@") else "",
                "заблокував бота" if u.get("blocked") else "активний",
                u.get("purchases_count", 0), u.get("orders_total", 0),
                u.get("last_activity_at"), u.get("joined_at"), u.get("start_count", 0),
            )]
        )
    return buf.getvalue()


def save_user_id(user_id: int, name: str = "", username: str = "") -> None:
    if not user_id or user_id <= 0:
        return
    _save_user_record(user_id, name or "", f"@{username}" if username else "—")


def save_user(user) -> None:
    save_user_id(
        user.id,
        user.first_name or "",
        (user.username or "").lstrip("@"),
    )


def is_user_blocked(user_id: int) -> bool:
    if not user_id or int(user_id) <= 0:
        return False
    conn = db_connect()
    row = conn.execute(_sql("SELECT blocked FROM users WHERE id = ?"), (int(user_id),)).fetchone()
    conn.close()
    return bool(row and row[0])


def set_blocked(user_id, blocked: bool) -> None:
    conn = db_connect()
    conn.execute(_sql("UPDATE users SET blocked = ? WHERE id = ?"), (int(blocked), user_id))
    conn.commit()
    conn.close()


def get_user_display_name(user_id: int) -> str:
    if not user_id or int(user_id) <= 0:
        return "клієнт"
    conn = db_connect()
    row = conn.execute(_sql("SELECT name FROM users WHERE id = ?"), (int(user_id),)).fetchone()
    conn.close()
    if row and row[0]:
        return str(row[0]).strip()
    return f"ID {int(user_id)}"


def get_all_users():
    conn = db_connect()
    users = conn.execute("SELECT id FROM users WHERE blocked = 0").fetchall()
    conn.close()
    return users
