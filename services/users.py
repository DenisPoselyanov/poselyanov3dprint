"""User persistence and access helpers."""

from __future__ import annotations

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
    "orders": "orders_count",
    "spent": "orders_total",
    "name": "u.name",
}


def _iso(value) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def list_users(
    search: str = "",
    sort: str = "starts",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    blocked: str = "all",
) -> dict:
    """Список користувачів для адмін-панелі з лічильниками та статистикою замовлень."""
    sort_col = _USERS_SORT_COLUMNS.get(sort, "u.start_count")
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    limit = max(1, min(200, int(limit or 50)))
    offset = max(0, int(offset or 0))

    where = []
    params: list = []
    term = (search or "").strip()
    if term:
        like = f"%{term.lower()}%"
        clause = "(LOWER(COALESCE(u.name, '')) LIKE ? OR LOWER(COALESCE(u.username, '')) LIKE ?"
        params.extend([like, like])
        digits = term.lstrip("@").strip()
        if digits.isdigit():
            clause += " OR u.id = ?"
            params.append(int(digits))
        clause += ")"
        where.append(clause)
    if blocked == "only":
        where.append("u.blocked = 1")
    elif blocked == "active":
        where.append("u.blocked = 0")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    conn = db_connect()
    total = conn.execute(
        _sql(f"SELECT COUNT(*) FROM users u {where_sql}"), tuple(params)
    ).fetchone()[0]

    rows = conn.execute(
        _sql(
            f"""
            SELECT u.id,
                   u.name,
                   u.username,
                   u.blocked,
                   u.joined_at,
                   u.start_count,
                   u.last_start_at,
                   u.activity_count,
                   u.last_seen_at,
                   COALESCE(o.orders_count, 0)  AS orders_count,
                   COALESCE(o.orders_total, 0)  AS orders_total
            FROM users u
            LEFT JOIN (
                SELECT user_id,
                       COUNT(*) AS orders_count,
                       SUM(CASE WHEN status = 'confirmed' THEN COALESCE(total_price, 0) ELSE 0 END) AS orders_total
                FROM orders
                GROUP BY user_id
            ) o ON o.user_id = u.id
            {where_sql}
            ORDER BY ({sort_col} IS NULL), {sort_col} {direction}, u.id DESC
            LIMIT ? OFFSET ?
            """
        ),
        tuple(params) + (limit, offset),
    ).fetchall()
    conn.close()

    users = [
        {
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
        }
        for r in rows
    ]
    return {"users": users, "total": int(total), "limit": limit, "offset": offset}


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
