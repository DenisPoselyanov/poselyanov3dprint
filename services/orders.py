"""Order persistence and queries."""

from __future__ import annotations

import asyncio
import re

from catalog_store import CUSTOM_PRODUCTS_CACHE, PRODUCTS_CACHE
from db_core import db_connect, is_postgres as _is_postgres, sql as _sql
from services.coupons import (
    CouponConsumptionError,
    confirm_coupon_for_order,
    release_coupon_for_order,
    reserve_coupon,
)
from services.db_utils import row_to_dict

_user_order_locks: dict[int, asyncio.Lock] = {}


def _insert_order_items(conn, order_id: int, items: list) -> None:
    for item in items:
        fl = (item.get("filament_name") or item.get("filament_id") or "").strip()
        is_contract = 1 if item.get("is_contract_price") else 0
        item_comment = (item.get("comment") or "").strip()
        conn.execute(
            _sql("""
                INSERT INTO order_items
                (order_id, product_id, product_name, price, quantity, filament, is_contract_price, comment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """),
            (
                order_id,
                int(item.get("product_id") or item.get("id") or 0),
                item.get("product_name", "—"),
                int(item.get("price", 0)),
                int(item.get("quantity", 1)),
                fl,
                is_contract,
                item_comment or None,
            ),
        )


def _find_active_order(user_id: int) -> dict | None:
    if not user_id or int(user_id) <= 0:
        return None
    conn = db_connect(dict_rows=True)
    date_expr = "NOW() - INTERVAL '4 hours'" if _is_postgres() else "datetime('now', '-4 hours')"
    row = conn.execute(
        _sql(f"""
            SELECT id, total_price, price_pending, comment, gift_product_name
            FROM orders
            WHERE user_id = ? AND status IN ('new', 'draft')
              AND ordered_at > {date_expr}
            ORDER BY ordered_at DESC
            LIMIT 1
        """),
        (int(user_id),),
    ).fetchone()
    conn.close()
    return row_to_dict(row) if row else None


def save_order(
    user_id,
    username,
    first_name,
    items,
    total_price,
    comment,
    gift_product_name=None,
    coupon_code=None,
    discount_amount=0,
    price_pending=0,
    promotion_discount=0,
):
    conn = db_connect()
    uid = int(user_id or 0)
    active = _find_active_order(uid) if uid > 0 else None

    if active:
        order_id = int(active["id"])
        new_total = int(active.get("total_price") or 0) + int(total_price)
        new_price_pending = max(int(active.get("price_pending") or 0), int(price_pending or 0))

        old_gift = (active.get("gift_product_name") or "").strip()
        new_gift = (gift_product_name or "").strip()
        if new_gift and old_gift:
            merged_gift = f"{old_gift}, {new_gift}"
        else:
            merged_gift = new_gift or old_gift or None

        conn.execute(
            _sql("""
                UPDATE orders
                SET total_price = ?, price_pending = ?, gift_product_name = ?
                WHERE id = ?
            """),
            (new_total, new_price_pending, merged_gift, order_id),
        )

        _insert_order_items(conn, order_id, items)

        if gift_product_name:
            conn.execute(
                _sql("""
                    INSERT INTO order_items (order_id, product_id, product_name, price, quantity)
                    VALUES (?, 0, ?, 0, 1)
                """),
                (order_id, f"🎁 {gift_product_name} (безкоштовно)"),
            )

        conn.commit()
        conn.close()
        return order_id, False

    if _is_postgres():
        cursor = conn.execute(
            """
            INSERT INTO orders
            (user_id, username, first_name, total_price, comment, gift_product_name,
             coupon_code, discount_amount, promotion_discount, status, price_pending)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'new', %s)
            RETURNING id
            """,
            (
                user_id,
                username,
                first_name,
                total_price,
                comment,
                gift_product_name,
                coupon_code,
                discount_amount,
                int(promotion_discount or 0),
                int(price_pending or 0),
            ),
        )
        order_id = cursor.fetchone()[0]
    else:
        cursor = conn.execute(
            """
            INSERT INTO orders
            (user_id, username, first_name, total_price, comment, gift_product_name,
             coupon_code, discount_amount, promotion_discount, status, price_pending)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
            """,
            (
                user_id,
                username,
                first_name,
                total_price,
                comment,
                gift_product_name,
                coupon_code,
                discount_amount,
                int(promotion_discount or 0),
                int(price_pending or 0),
            ),
        )
        order_id = cursor.lastrowid

    _insert_order_items(conn, order_id, items)

    if gift_product_name:
        conn.execute(
            _sql("""
                INSERT INTO order_items (order_id, product_id, product_name, price, quantity)
                VALUES (?, 0, ?, 0, 1)
            """),
            (order_id, f"🎁 {gift_product_name} (безкоштовно)"),
        )

    if coupon_code:
        try:
            reserve_coupon(conn, coupon_code, user_id, order_id)
        except CouponConsumptionError:
            conn.rollback()
            conn.close()
            raise
    conn.commit()
    conn.close()
    return order_id, True


def user_order_lock(user_id: int) -> asyncio.Lock:
    uid = int(user_id or 0)
    if uid not in _user_order_locks:
        _user_order_locks[uid] = asyncio.Lock()
    return _user_order_locks[uid]


def get_idempotent_order(user_id: int, idempotency_key: str) -> dict | None:
    if not idempotency_key or not user_id or int(user_id) <= 0:
        return None
    conn = db_connect(dict_rows=True)
    row = conn.execute(
        _sql("""
            SELECT order_id, is_new
            FROM order_idempotency
            WHERE user_id = ? AND idempotency_key = ?
        """),
        (int(user_id), idempotency_key),
    ).fetchone()
    conn.close()
    return row_to_dict(row) if row else None


def save_idempotent_order(user_id: int, idempotency_key: str, order_id: int, is_new: bool) -> None:
    if not idempotency_key or not user_id or int(user_id) <= 0:
        return
    conn = db_connect()
    is_new_val = 1 if is_new else 0
    if _is_postgres():
        conn.execute(
            _sql("""
                INSERT INTO order_idempotency (user_id, idempotency_key, order_id, is_new)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (user_id, idempotency_key) DO NOTHING
            """),
            (int(user_id), idempotency_key, int(order_id), is_new_val),
        )
    else:
        conn.execute(
            _sql("""
                INSERT OR IGNORE INTO order_idempotency (user_id, idempotency_key, order_id, is_new)
                VALUES (?, ?, ?, ?)
            """),
            (int(user_id), idempotency_key, int(order_id), is_new_val),
        )
    conn.commit()
    conn.close()


def update_order_status(order_id: int, status: str) -> None:
    conn = db_connect(dict_rows=True)
    row = conn.execute(
        _sql("SELECT status FROM orders WHERE id = ?"),
        (order_id,),
    ).fetchone()
    if not row:
        conn.close()
        return

    old_status = row["status"] if isinstance(row, dict) else row[0]
    conn.execute(_sql("UPDATE orders SET status = ? WHERE id = ?"), (status, order_id))

    if status == "confirmed" and old_status != "confirmed":
        confirm_coupon_for_order(conn, order_id)
    elif status == "cancelled" and old_status in ("new", "draft"):
        release_coupon_for_order(conn, order_id)

    conn.commit()
    conn.close()


def set_order_channel_message_id(order_id: int, message_id: int | None) -> None:
    conn = db_connect()
    conn.execute(
        _sql("UPDATE orders SET channel_message_id = ? WHERE id = ?"),
        (message_id, order_id),
    )
    conn.commit()
    conn.close()


def set_orders_channel_message_ids(order_ids: list[int], message_id: int | None) -> None:
    if not order_ids:
        return
    conn = db_connect()
    placeholders = ", ".join("?" * len(order_ids))
    conn.execute(
        _sql(f"UPDATE orders SET channel_message_id = ? WHERE id IN ({placeholders})"),
        (message_id, *order_ids),
    )
    conn.commit()
    conn.close()


def get_orders_sharing_channel_message(order_id: int) -> list[int]:
    conn = db_connect(dict_rows=True)
    row = conn.execute(
        _sql("SELECT channel_message_id FROM orders WHERE id = ?"),
        (order_id,),
    ).fetchone()
    if not row or not row.get("channel_message_id"):
        conn.close()
        return [order_id]
    msg_id = row["channel_message_id"]
    rows = conn.execute(
        _sql("SELECT id FROM orders WHERE channel_message_id = ? ORDER BY id"),
        (msg_id,),
    ).fetchall()
    conn.close()
    ids = [int(r["id"]) for r in rows]
    return ids if ids else [order_id]


def tg_username_from_order(order: dict) -> str | None:
    username = (order.get("username") or "").strip()
    if username.startswith("@"):
        handle = username[1:].strip()
        if handle and handle != "невідомо":
            return handle
    return None


_TG_USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{4,31}$")


def _normalize_tg_handle(username: str | None) -> str | None:
    """Повертає handle лише для справжнього @username (не ім'я клієнта)."""
    raw = (username or "").strip()
    if not raw.startswith("@"):
        return None
    handle = raw[1:].strip()
    if not handle or handle in ("невідомо", "—"):
        return None
    if not _TG_USERNAME_RE.match(handle):
        return None
    return handle


def tg_contact_url(user_id: int | None, username: str | None = None) -> str | None:
    handle = _normalize_tg_handle(username)
    if handle:
        return f"https://t.me/{handle}"
    uid = int(user_id or 0)
    return f"tg://user?id={uid}" if uid > 0 else None


def tg_contact_keyboard_url(user_id: int | None, username: str | None = None) -> str | None:
    """URL для InlineKeyboardButton «Написати».

    Лише https://t.me/handle — tg://user?id= у кнопках дає Button_user_privacy_restricted,
    якщо у користувача обмеження приватності.
    """
    handle = _normalize_tg_handle(username)
    if handle:
        return f"https://t.me/{handle}"
    return None


def contact_button_target(user_id: int | None, username: str | None = None) -> tuple[str, str] | None:
    """('url', https://t.me/...) або ('callback', user_id) для кнопки «Написати»."""
    handle = _normalize_tg_handle(username)
    if handle:
        return ("url", f"https://t.me/{handle}")
    uid = int(user_id or 0)
    if uid > 0:
        return ("callback", str(uid))
    return None


def tg_contact_url_from_order(order: dict) -> str | None:
    return tg_contact_url(order.get("user_id"), order.get("username"))


def tg_contact_keyboard_url_from_order(order: dict) -> str | None:
    return tg_contact_keyboard_url(order.get("user_id"), order.get("username"))


def get_order_with_items(order_id: int):
    conn = db_connect(dict_rows=True)
    order = conn.execute(
        _sql("""
            SELECT id, user_id, username, first_name, total_price, comment,
                   gift_product_name, coupon_code, discount_amount, promotion_discount,
                   status, ordered_at, price_pending, channel_message_id
            FROM orders WHERE id = ?
        """),
        (order_id,),
    ).fetchone()
    if not order:
        conn.close()
        return None, []
    items = conn.execute(
        _sql("""
            SELECT oi.id, oi.product_id, oi.product_name, oi.price, oi.quantity,
                   oi.filament, oi.is_contract_price, oi.comment, p.stl_link
            FROM order_items oi
            LEFT JOIN products p ON p.id = oi.product_id
            WHERE oi.order_id = ?
        """),
        (order_id,),
    ).fetchall()
    conn.close()
    return row_to_dict(order), [row_to_dict(i) for i in items]


def get_orders_with_items_batch(order_ids: list[int]) -> dict[int, tuple[dict, list[dict]]]:
    """Load multiple orders and their items in two queries."""
    if not order_ids:
        return {}

    unique_ids = list(dict.fromkeys(int(oid) for oid in order_ids if oid))
    if not unique_ids:
        return {}

    conn = db_connect(dict_rows=True)
    placeholders = ", ".join("?" * len(unique_ids))

    orders = conn.execute(
        _sql(f"""
            SELECT id, user_id, username, first_name, total_price, comment,
                   gift_product_name, coupon_code, discount_amount, promotion_discount,
                   status, ordered_at, price_pending, channel_message_id
            FROM orders WHERE id IN ({placeholders})
        """),
        tuple(unique_ids),
    ).fetchall()

    items = conn.execute(
        _sql(f"""
            SELECT oi.id, oi.order_id, oi.product_id, oi.product_name, oi.price, oi.quantity,
                   oi.filament, oi.is_contract_price, oi.comment, p.stl_link
            FROM order_items oi
            LEFT JOIN products p ON p.id = oi.product_id
            WHERE oi.order_id IN ({placeholders})
        """),
        tuple(unique_ids),
    ).fetchall()
    conn.close()

    items_by_order: dict[int, list[dict]] = {}
    for item in items:
        row = row_to_dict(item)
        oid = int(row["order_id"])
        items_by_order.setdefault(oid, []).append(row)

    result: dict[int, tuple[dict, list[dict]]] = {}
    for order in orders:
        row = row_to_dict(order)
        oid = int(row["id"])
        result[oid] = (row, items_by_order.get(oid, []))
    return result


def delete_order(order_id: int):
    conn = db_connect()
    row = conn.execute(_sql("SELECT id FROM orders WHERE id = ?"), (order_id,)).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": "Замовлення не знайдено"}
    release_coupon_for_order(conn, order_id)
    conn.execute(_sql("DELETE FROM order_items WHERE order_id = ?"), (order_id,))
    conn.execute(_sql("DELETE FROM orders WHERE id = ?"), (order_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


def list_orders(*, pending_price_only: bool = False, limit: int = 50):
    conn = db_connect(dict_rows=True)
    sql = """
        SELECT id, user_id, username, first_name, total_price, comment,
               gift_product_name, coupon_code, discount_amount, promotion_discount,
               status, ordered_at, price_pending
        FROM orders
    """
    params: list = []
    if pending_price_only:
        sql += " WHERE price_pending = ?"
        params.append(1)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(_sql(sql), tuple(params)).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


def update_order_pricing(order_id: int, item_prices: dict[int, int]) -> dict:
    conn = db_connect(dict_rows=True)
    order = conn.execute(
        _sql("""
            SELECT id, discount_amount, user_id, first_name
            FROM orders WHERE id = ?
        """),
        (order_id,),
    ).fetchone()
    if not order:
        conn.close()
        return {"ok": False, "error": "Замовлення не знайдено"}

    for item_id, price in item_prices.items():
        new_price = max(0, int(price))
        conn.execute(
            _sql("""
                UPDATE order_items
                SET price = ?, is_contract_price = 0
                WHERE id = ? AND order_id = ?
            """),
            (new_price, int(item_id), order_id),
        )

    items = conn.execute(
        _sql("""
            SELECT id, product_name, price, quantity, is_contract_price
            FROM order_items WHERE order_id = ?
        """),
        (order_id,),
    ).fetchall()

    subtotal = sum(
        int(i.get("price") or 0) * int(i.get("quantity") or 1)
        for i in items
        if not str(i.get("product_name", "")).startswith("🎁")
    )
    discount = int(order.get("discount_amount") or 0)
    total = max(0, subtotal - discount)
    still_pending = any(
        int(i.get("is_contract_price") or 0)
        for i in items
        if not str(i.get("product_name", "")).startswith("🎁")
    )
    conn.execute(
        _sql("UPDATE orders SET total_price = ?, price_pending = ? WHERE id = ?"),
        (total, 1 if still_pending else 0, order_id),
    )
    conn.commit()
    conn.close()
    return {
        "ok": True,
        "total_price": total,
        "price_pending": still_pending,
        "user_id": order.get("user_id"),
        "first_name": order.get("first_name"),
    }


def find_gift_product_id(gift_name: str | None) -> int | None:
    if not gift_name:
        return None
    name = str(gift_name).strip().lower()
    for p in PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE:
        if str(p.get("name", "")).strip().lower() == name and p.get("id"):
            return int(p["id"])
    return None
