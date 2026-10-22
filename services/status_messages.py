"""Журнал повідомлень про статус, надісланих клієнтам, — щоб їх можна було видалити, якщо надіслали помилково."""
from __future__ import annotations

from db_core import db_connect, sql as _sql


def record_status_message(order_id: int, user_id: int, message_id: int | None, status: str) -> None:
    if not message_id:
        return
    conn = db_connect()
    conn.execute(
        _sql("INSERT INTO client_status_messages (order_id, user_id, message_id, status) VALUES (?, ?, ?, ?)"),
        (int(order_id), int(user_id), int(message_id), status),
    )
    conn.commit()
    conn.close()


def list_status_messages(order_id: int | None = None) -> list[dict]:
    """Ще не видалені повідомлення (за замовленням або всі)."""
    conn = db_connect(dict_rows=True)
    sql = "SELECT id, order_id, user_id, message_id, status FROM client_status_messages WHERE deleted = 0"
    params: tuple = ()
    if order_id is not None:
        sql += " AND order_id = ?"
        params = (int(order_id),)
    rows = conn.execute(_sql(sql + " ORDER BY id"), params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_status_message_deleted(row_id: int) -> None:
    conn = db_connect()
    conn.execute(_sql("UPDATE client_status_messages SET deleted = 1 WHERE id = ?"), (int(row_id),))
    conn.commit()
    conn.close()
