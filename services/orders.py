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

# Ланцюжок виконання: далі йти можна лише вперед; cancelled/draft — з будь-якого.
FULFILMENT_CHAIN = ("confirmed", "printing", "ready", "shipped", "delivered")
ORDER_STATUSES = ("new", "draft", "cancelled", *FULFILMENT_CHAIN)
# Статуси, у яких купон уже списано, а замовлення прийняте в роботу.
ACCEPTED_STATUSES = frozenset(FULFILMENT_CHAIN)

# 8 статусів -> 4 кроки трекера в Mini App (None — трекер не показується).
TRACKER_STEPS = ("accepted", "printing", "shipped", "delivered")
STATUS_TO_STEP = {
    "new": "accepted",
    "draft": "accepted",
    "confirmed": "accepted",
    "printing": "printing",
    "ready": "printing",
    "shipped": "shipped",
    "delivered": "delivered",
    "cancelled": None,
}

# Поля замовлення, які можна міняти через update_order_fields.
EDITABLE_ORDER_FIELDS = frozenset({
    "delivery_method", "city", "city_ref", "branch", "branch_ref", "recipient_name",
    "phone", "pay_method", "ttn", "internal_note", "source", "promotion_id", "gift_product_id",
})
_DELIVERY_METHODS = ("np", "up", "self")
_PAY_METHODS = ("card", "cod")
_SOURCES = ("miniapp", "instagram", "olx", "telegram", "offline")


def is_valid_transition(old_status: str, new_status: str) -> bool:
    """Чи дозволений перехід: вперед по ланцюжку; cancelled/draft — завжди."""
    if new_status not in ORDER_STATUSES or old_status == new_status:
        return False
    if new_status in ("cancelled", "draft"):
        return True
    if old_status not in FULFILMENT_CHAIN:
        return True  # new/draft/cancelled -> будь-який крок ланцюжка
    return FULFILMENT_CHAIN.index(new_status) > FULFILMENT_CHAIN.index(old_status)


def _clean_extra(extra: dict | None) -> dict:
    """Лише дозволені поля, з нормалізацією переліків і обрізанням довжини."""
    out: dict = {}
    for key, value in (extra or {}).items():
        if key not in EDITABLE_ORDER_FIELDS:
            continue
        if key == "gift_product_id":
            try:
                out[key] = int(value) if value else None
            except (TypeError, ValueError):
                out[key] = None
            continue
        text = str(value or "").strip()[:500]
        if key == "delivery_method" and text not in _DELIVERY_METHODS:
            text = ""
        elif key == "pay_method" and text not in _PAY_METHODS:
            text = ""
        elif key == "source" and text not in _SOURCES:
            text = "miniapp"
        out[key] = text
    return out


def _insert_order_items(conn, order_id: int, items: list) -> None:
    for item in items:
        fl = (item.get("filament_name") or item.get("filament_id") or "").strip()
        is_contract = 1 if item.get("is_contract_price") else 0
        item_comment = (item.get("comment") or "").strip()
        personalization = str(item.get("personalization") or "").strip()[:200]
        personalization_note = str(item.get("personalization_note") or "").strip()[:200]
        conn.execute(
            _sql("""
                INSERT INTO order_items
                (order_id, product_id, product_name, price, quantity, filament, is_contract_price, comment,
                 personalization, personalization_note, is_gift)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                personalization,
                personalization_note,
                1 if item.get("is_gift") else 0,
            ),
        )


def _apply_order_fields(conn, order_id: int, fields: dict) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(
        _sql(f"UPDATE orders SET {assignments} WHERE id = ?"),
        (*fields.values(), int(order_id)),
    )


def update_order_fields(order_id: int, fields: dict) -> bool:
    """Оновлює дозволені поля (ТТН, нотатка, доставка…). False — замовлення немає."""
    clean = _clean_extra(fields)
    conn = db_connect()
    row = conn.execute(_sql("SELECT id FROM orders WHERE id = ?"), (int(order_id),)).fetchone()
    if not row:
        conn.close()
        return False
    _apply_order_fields(conn, order_id, clean)
    conn.commit()
    conn.close()
    return True


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
    extra=None,
    merge=True,
):
    conn = db_connect()
    uid = int(user_id or 0)
    extra_fields = _clean_extra(extra)
    active = _find_active_order(uid) if uid > 0 and merge else None

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
                    INSERT INTO order_items (order_id, product_id, product_name, price, quantity, is_gift)
                    VALUES (?, 0, ?, 0, 1, 1)
                """),
                (order_id, f"🎁 {gift_product_name} (безкоштовно)"),
            )

        _apply_order_fields(conn, order_id, extra_fields)
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
                INSERT INTO order_items (order_id, product_id, product_name, price, quantity, is_gift)
                VALUES (?, 0, ?, 0, 1, 1)
            """),
            (order_id, f"🎁 {gift_product_name} (безкоштовно)"),
        )

    _apply_order_fields(conn, order_id, extra_fields)

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
    if status == "delivered" and old_status != "delivered":
        now_expr = "NOW()" if _is_postgres() else "CURRENT_TIMESTAMP"
        conn.execute(_sql(f"UPDATE orders SET delivered_at = {now_expr} WHERE id = ?"), (order_id,))

    if status in ACCEPTED_STATUSES and old_status not in ACCEPTED_STATUSES:
        confirm_coupon_for_order(conn, order_id)
    elif status == "cancelled" and old_status in ("new", "draft"):
        release_coupon_for_order(conn, order_id)

    conn.commit()
    conn.close()


def list_user_orders(user_id: int, limit: int = 30) -> list[dict]:
    """Замовлення покупця з позиціями — для «Моїх замовлень» у Mini App.

    Віддає лише публічні поля: без username, внутрішньої нотатки і STL.
    """
    uid = int(user_id or 0)
    if uid <= 0:
        return []
    conn = db_connect(dict_rows=True)
    orders = conn.execute(
        _sql("""
            SELECT id, total_price, status, ordered_at, delivery_method, city, branch,
                   pay_method, ttn, comment, coupon_code, discount_amount,
                   promotion_discount, gift_product_name, price_pending
            FROM orders
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """),
        (uid, int(limit)),
    ).fetchall()
    ids = [int(o["id"]) for o in orders]
    items_by_order: dict[int, list[dict]] = {}
    if ids:
        placeholders = ", ".join("?" * len(ids))
        items = conn.execute(
            _sql(f"""
                SELECT order_id, product_id, product_name, price, quantity, filament,
                       is_contract_price, personalization, personalization_note, is_gift
                FROM order_items
                WHERE order_id IN ({placeholders})
                ORDER BY id
            """),
            tuple(ids),
        ).fetchall()
        for item in items:
            row = row_to_dict(item)
            items_by_order.setdefault(int(row["order_id"]), []).append(row)
    conn.close()

    result = []
    for o in orders:
        row = row_to_dict(o)
        row["step"] = STATUS_TO_STEP.get(row.get("status") or "new")
        row["items"] = items_by_order.get(int(row["id"]), [])
        result.append(row)
    return result


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
    row = row_to_dict(row) if row else None
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
                   status, ordered_at, price_pending, channel_message_id,
                   delivery_method, city, branch, recipient_name, phone, pay_method,
                   ttn, internal_note, source, delivered_at
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
                   oi.filament, oi.is_contract_price, oi.comment, p.stl_link,
                   oi.personalization, oi.personalization_note, oi.is_gift
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


# Вкладки адмінки -> статуси. «В роботі» = підтверджені + друк + готові до відправки.
STATUS_GROUPS = {
    "new": ("new",),
    "draft": ("draft",),
    "work": ("confirmed", "printing", "ready"),
    "shipped": ("shipped",),
    "delivered": ("delivered",),
    "cancelled": ("cancelled",),
}

_LIST_COLUMNS = """
    id, user_id, username, first_name, total_price, comment,
    gift_product_name, coupon_code, discount_amount, promotion_discount,
    status, ordered_at, price_pending, delivery_method, city, branch,
    recipient_name, phone, pay_method, ttn, internal_note, source, delivered_at
"""


def expand_status_filter(raw: str | None) -> list[str]:
    """«work,shipped» або «printing» -> плоский список відомих статусів."""
    out: list[str] = []
    for part in str(raw or "").split(","):
        part = part.strip().lower()
        if not part:
            continue
        for status in STATUS_GROUPS.get(part, (part,)):
            if status in ORDER_STATUSES and status not in out:
                out.append(status)
    return out


def count_orders_by_status() -> dict[str, int]:
    """Лічильники для вкладок: по кожній групі, «all» і «pending_price»."""
    conn = db_connect(dict_rows=True)
    rows = conn.execute(_sql("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")).fetchall()
    pending = conn.execute(
        _sql("SELECT COUNT(*) AS n FROM orders WHERE price_pending = ?"), (1,)
    ).fetchone()
    conn.close()
    by_status = {str(row_to_dict(r)["status"] or "new"): int(row_to_dict(r)["n"]) for r in rows}
    counts = {
        group: sum(by_status.get(s, 0) for s in members)
        for group, members in STATUS_GROUPS.items()
    }
    counts["all"] = sum(by_status.values())
    counts["pending_price"] = int(row_to_dict(pending)["n"]) if pending else 0
    return counts


def list_orders(
    *,
    pending_price_only: bool = False,
    limit: int = 50,
    statuses: list[str] | None = None,
):
    conn = db_connect(dict_rows=True)
    sql = f"SELECT {_LIST_COLUMNS} FROM orders"
    where: list[str] = []
    params: list = []
    if pending_price_only:
        where.append("price_pending = ?")
        params.append(1)
    if statuses:
        where.append("status IN (" + ", ".join("?" * len(statuses)) + ")")
        params.extend(statuses)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    rows = [row_to_dict(r) for r in conn.execute(_sql(sql), tuple(params)).fetchall()]
    if rows:
        ids = [int(r["id"]) for r in rows]
        items = conn.execute(
            _sql(
                "SELECT order_id, product_name, quantity, filament, is_gift FROM order_items "
                "WHERE order_id IN (" + ", ".join("?" * len(ids)) + ") ORDER BY id"
            ),
            tuple(ids),
        ).fetchall()
        by_order: dict[int, list[dict]] = {}
        for item in items:
            d = row_to_dict(item)
            by_order.setdefault(int(d["order_id"]), []).append(
                {
                    "product_name": d.get("product_name"),
                    "quantity": int(d.get("quantity") or 1),
                    "filament": d.get("filament") or "",
                    "is_gift": bool(d.get("is_gift")),
                }
            )
        for row in rows:
            row["items"] = by_order.get(int(row["id"]), [])
    conn.close()
    return rows


PRINT_QUEUE_STATUSES = ("confirmed", "printing")


def _product_thumb(product_id) -> tuple[str, str]:
    """(фото, емодзі) товару з кешу каталогу — для рядка черги друку."""
    pid = str(product_id or "")
    for product in PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE:
        if str(product.get("id")) == pid:
            photos = product.get("photos") or []
            return (photos[0] if photos else ""), str(product.get("emoji") or "")
    return "", ""


def get_print_queue() -> dict:
    """Позиції замовлень confirmed/printing, згруповані за кольором (менше перезаправок)."""
    conn = db_connect(dict_rows=True)
    rows = conn.execute(
        _sql(
            "SELECT oi.id, oi.order_id, oi.product_id, oi.product_name, oi.quantity, oi.filament, "
            "oi.personalization, oi.personalization_note, oi.is_gift, oi.printed, o.status "
            "FROM order_items oi JOIN orders o ON o.id = oi.order_id "
            "WHERE o.status IN (" + ", ".join("?" * len(PRINT_QUEUE_STATUSES)) + ") "
            "ORDER BY oi.order_id, oi.id"
        ),
        PRINT_QUEUE_STATUSES,
    ).fetchall()
    conn.close()

    groups: dict[str, dict] = {}
    order_ids: set[int] = set()
    queued_parts = 0
    for row in rows:
        item = row_to_dict(row)
        color = (item.get("filament") or "").strip()
        printed = bool(item.get("printed"))
        photo, emoji = _product_thumb(item.get("product_id"))
        group = groups.setdefault(color, {"filament": color, "items": []})
        group["items"].append(
            {
                "id": int(item["id"]),
                "order_id": int(item["order_id"]),
                "product_name": item.get("product_name") or "—",
                "quantity": int(item.get("quantity") or 1),
                "personalization": item.get("personalization") or "",
                "personalization_note": item.get("personalization_note") or "",
                "is_gift": bool(item.get("is_gift")),
                "printed": printed,
                "photo": photo,
                "emoji": emoji,
            }
        )
        if not printed:
            order_ids.add(int(item["order_id"]))
            queued_parts += int(item.get("quantity") or 1)
    ordered = sorted(groups.values(), key=lambda g: (g["filament"] == "", g["filament"].lower()))
    for group in ordered:
        group["pending"] = sum(1 for i in group["items"] if not i["printed"])
    return {
        "groups": ordered,
        "kpi": {
            "parts": queued_parts,
            "orders": len(order_ids),
            "colors": sum(1 for g in ordered if g["pending"]),
        },
    }


def set_item_printed(item_id: int, printed: bool) -> dict | None:
    """Ставить/знімає відмітку «надруковано». None — позиції немає.

    Повертає стан замовлення, щоб викликач переключив статус: перша відмітка
    -> printing, усі позиції надруковані -> ready.
    """
    conn = db_connect(dict_rows=True)
    row = conn.execute(
        _sql("SELECT order_id FROM order_items WHERE id = ?"), (int(item_id),)
    ).fetchone()
    if not row:
        conn.close()
        return None
    order_id = int(row_to_dict(row)["order_id"])
    conn.execute(
        _sql("UPDATE order_items SET printed = ? WHERE id = ?"), (1 if printed else 0, int(item_id))
    )
    conn.commit()
    items = conn.execute(
        _sql("SELECT printed FROM order_items WHERE order_id = ?"), (order_id,)
    ).fetchall()
    status_row = conn.execute(
        _sql("SELECT status FROM orders WHERE id = ?"), (order_id,)
    ).fetchone()
    conn.close()
    flags = [bool(row_to_dict(i).get("printed")) for i in items]
    return {
        "order_id": order_id,
        "status": (row_to_dict(status_row).get("status") if status_row else None) or "new",
        "total": len(flags),
        "printed": sum(flags),
    }


def next_status_after_print(status: str, total: int, printed: int) -> str | None:
    """Статус, у який треба перевести замовлення після зміни відмітки; None — без змін."""
    if status not in PRINT_QUEUE_STATUSES or printed <= 0:
        return None
    if total and printed >= total:
        return "ready"
    return "printing" if status == "confirmed" else None


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
