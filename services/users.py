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


def get_all_users():
    conn = db_connect()
    users = conn.execute("SELECT id FROM users WHERE blocked = 0").fetchall()
    conn.close()
    return users
