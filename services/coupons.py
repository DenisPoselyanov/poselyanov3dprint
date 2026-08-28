"""Coupon validation and CRUD."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import config
from db_core import db_connect, is_postgres, sql as _sql
from services.db_utils import row_to_dict
from services.pricing import actual_discount_after_rounding

logger = logging.getLogger(__name__)

_DB_ERROR_MESSAGE = "Помилка бази даних. Спробуйте пізніше."


class CouponConsumptionError(Exception):
    """Coupon could not be consumed atomically during order save."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _parse_expires_at(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_allowed_user_ids(value) -> list[int]:
    """Parse allowed_user_ids from list, comma/newline-separated string, or None."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = re.split(r"[\s,;]+", str(value).strip())
    result: list[int] = []
    seen: set[int] = set()
    for item in raw_items:
        if item in ("", None):
            continue
        try:
            uid = int(item)
        except (TypeError, ValueError):
            continue
        if uid > 0 and uid not in seen:
            seen.add(uid)
            result.append(uid)
    return result


def _get_allowed_user_ids(conn, code: str) -> list[int]:
    rows = conn.execute(
        _sql("SELECT user_id FROM coupon_allowed_users WHERE code = ? ORDER BY user_id ASC"),
        (code.upper(),),
    ).fetchall()
    ids: list[int] = []
    for row in rows:
        if isinstance(row, dict):
            ids.append(int(row["user_id"]))
        else:
            ids.append(int(row[0]))
    return ids


def _coupon_has_whitelist(conn, code: str) -> bool:
    row = conn.execute(
        _sql("SELECT 1 FROM coupon_allowed_users WHERE code = ? LIMIT 1"),
        (code.upper(),),
    ).fetchone()
    return bool(row)


def _coupon_has_restrictions(conn, code: str, c: dict) -> bool:
    if c.get("personal_user_id"):
        return True
    return _coupon_has_whitelist(conn, code)


def _is_user_allowed(conn, code: str, c: dict, user_id: int) -> bool:
    uid = int(user_id or 0)
    if not uid:
        return not _coupon_has_restrictions(conn, code, c)

    if c.get("personal_user_id"):
        return int(c["personal_user_id"]) == uid

    if _coupon_has_whitelist(conn, code):
        row = conn.execute(
            _sql("SELECT 1 FROM coupon_allowed_users WHERE code = ? AND user_id = ?"),
            (code.upper(), uid),
        ).fetchone()
        return bool(row)

    return True


def _user_access_error(conn, code: str, c: dict, user_id: int) -> str | None:
    if _is_user_allowed(conn, code, c, user_id):
        return None
    return "Цей купон призначений іншому користувачу ❌"


def _sync_allowed_users(conn, code: str, allowed_user_ids: list[int]) -> None:
    code = code.upper()
    existing = set(_get_allowed_user_ids(conn, code))
    desired = set(allowed_user_ids)
    for uid in existing - desired:
        conn.execute(
            _sql("DELETE FROM coupon_allowed_users WHERE code = ? AND user_id = ?"),
            (code, uid),
        )
    for uid in desired - existing:
        if is_postgres():
            conn.execute(
                _sql(
                    "INSERT INTO coupon_allowed_users (code, user_id) VALUES (?, ?) "
                    "ON CONFLICT DO NOTHING"
                ),
                (code, uid),
            )
        else:
            conn.execute(
                _sql("INSERT OR IGNORE INTO coupon_allowed_users (code, user_id) VALUES (?, ?)"),
                (code, uid),
            )


def _attach_allowed_users(conn, coupons: list[dict] | dict | None) -> None:
    if not coupons:
        return
    single = isinstance(coupons, dict)
    items = [coupons] if single else coupons
    for item in items:
        code = item.get("code")
        if code:
            item["allowed_user_ids"] = _get_allowed_user_ids(conn, code)
    if single and items:
        coupons.update(items[0])


def _coupon_row_to_dict(row) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return {
        "code": row[0],
        "type": row[1],
        "value": row[2],
        "min_order": row[3],
        "uses_max": row[4],
        "uses_count": row[5],
        "one_per_user": row[6],
        "active": row[7],
        "expires_at": row[8],
        "personal_user_id": row[9],
        "stackable": row[10] if len(row) > 10 else 0,
        "require_channel_sub": row[11] if len(row) > 11 else 0,
    }


def _pending_uses_count(conn, code: str) -> int:
    row = conn.execute(
        _sql("SELECT COUNT(*) FROM coupon_uses WHERE code = ? AND status = 'pending'"),
        (code.upper(),),
    ).fetchone()
    if row is None:
        return 0
    if isinstance(row, dict):
        return int(next(iter(row.values())))
    return int(row[0])


def _user_has_coupon_hold(conn, code: str, user_id: int) -> bool:
    uid = int(user_id or 0)
    if not uid:
        return False
    row = conn.execute(
        _sql(
            "SELECT 1 FROM coupon_uses WHERE code = ? AND user_id = ? "
            "AND status IN ('pending', 'confirmed')"
        ),
        (code.upper(), uid),
    ).fetchone()
    return bool(row)


def _coupon_uses_exhausted(c: dict, pending_count: int) -> bool:
    uses_max = int(c.get("uses_max") or 0)
    if not uses_max:
        return False
    return int(c.get("uses_count", 0)) + pending_count >= uses_max


def _validate_coupon_row(
    c: dict,
    user_id: int,
    *,
    check_min_order: int | None = None,
    conn=None,
    code: str | None = None,
) -> str | None:
    if not c:
        return "Купон не знайдено ❌"
    if not c.get("active"):
        return "Купон вже не активний ❌"

    expires = _parse_expires_at(c.get("expires_at"))
    if expires and datetime.now(timezone.utc) > expires:
        return "Термін купону закінчився ❌"

    if check_min_order is not None and c.get("min_order") and check_min_order < c["min_order"]:
        return f"Мінімальна сума замовлення: {c['min_order']} ₴ ❌"

    coupon_code = code or c.get("code") or ""
    pending_count = _pending_uses_count(conn, coupon_code) if conn is not None and coupon_code else 0
    if _coupon_uses_exhausted(c, pending_count):
        return "Купон вичерпано ❌"

    if conn is not None and coupon_code:
        err = _user_access_error(conn, coupon_code, c, user_id)
        if err:
            return err
    elif c.get("personal_user_id") and user_id and int(c["personal_user_id"]) != int(user_id):
        return "Цей купон призначений іншому користувачу ❌"

    return None


def reserve_coupon(conn, code: str, user_id: int, order_id: int) -> None:
    """Atomically reserve coupon use inside an open DB transaction."""
    code = code.upper()
    uid = int(user_id or 0)

    if is_postgres():
        row = conn.execute(
            """
            SELECT code, type, value, min_order, uses_max, uses_count, one_per_user,
                   active, expires_at, personal_user_id, stackable
            FROM coupons WHERE code = %s FOR UPDATE
            """,
            (code,),
        ).fetchone()
    else:
        row = conn.execute(
            _sql(
                "SELECT code, type, value, min_order, uses_max, uses_count, one_per_user, "
                "active, expires_at, personal_user_id, stackable FROM coupons WHERE code = ?"
            ),
            (code,),
        ).fetchone()

    c = _coupon_row_to_dict(row)
    err = _validate_coupon_row(c, uid, conn=conn, code=code)
    if err:
        raise CouponConsumptionError(err)

    if c.get("one_per_user") and uid and _user_has_coupon_hold(conn, code, uid):
        raise CouponConsumptionError("Ти вже використовував цей купон ❌")

    pending_count = _pending_uses_count(conn, code)
    if _coupon_uses_exhausted(c, pending_count):
        raise CouponConsumptionError("Купон вичерпано ❌")

    if c.get("one_per_user") and uid:
        if is_postgres():
            inserted = conn.execute(
                """
                INSERT INTO coupon_uses (code, user_id, order_id, status)
                SELECT %s, %s, %s, 'pending'
                WHERE NOT EXISTS (
                    SELECT 1 FROM coupon_uses
                    WHERE code = %s AND user_id = %s AND status IN ('pending', 'confirmed')
                )
                RETURNING id
                """,
                (code, uid, order_id, code, uid),
            ).fetchone()
            if not inserted:
                raise CouponConsumptionError("Ти вже використовував цей купон ❌")
        else:
            cur = conn.execute(
                _sql(
                    """
                    INSERT INTO coupon_uses (code, user_id, order_id, status)
                    SELECT ?, ?, ?, 'pending'
                    WHERE NOT EXISTS (
                        SELECT 1 FROM coupon_uses
                        WHERE code = ? AND user_id = ? AND status IN ('pending', 'confirmed')
                    )
                    """
                ),
                (code, uid, order_id, code, uid),
            )
            if cur.rowcount == 0:
                raise CouponConsumptionError("Ти вже використовував цей купон ❌")
    else:
        conn.execute(
            _sql(
                "INSERT INTO coupon_uses (code, user_id, order_id, status) VALUES (?, ?, ?, 'pending')"
            ),
            (code, uid, order_id),
        )


def confirm_coupon_for_order(conn, order_id: int) -> None:
    """Finalize a pending coupon reservation when an order is confirmed."""
    row = conn.execute(
        _sql("SELECT id, code, user_id, status FROM coupon_uses WHERE order_id = ? LIMIT 1"),
        (order_id,),
    ).fetchone()
    if not row:
        return

    if isinstance(row, dict):
        cu_id = int(row["id"])
        code = str(row["code"]).upper()
        status = row.get("status") or "confirmed"
    else:
        cu_id = int(row[0])
        code = str(row[1]).upper()
        status = row[3] if len(row) > 3 else "confirmed"

    if status == "confirmed":
        return
    if status != "pending":
        return

    if is_postgres():
        conn.execute(
            "SELECT code FROM coupons WHERE code = %s FOR UPDATE",
            (code,),
        )
    else:
        conn.execute(_sql("SELECT code FROM coupons WHERE code = ?"), (code,))

    conn.execute(_sql("UPDATE coupons SET uses_count = uses_count + 1 WHERE code = ?"), (code,))
    conn.execute(
        _sql("UPDATE coupon_uses SET status = 'confirmed' WHERE id = ?"),
        (cu_id,),
    )


def release_coupon_for_order(conn, order_id: int) -> None:
    """Release a pending coupon reservation (e.g. on order cancellation)."""
    conn.execute(
        _sql("DELETE FROM coupon_uses WHERE order_id = ? AND status = 'pending'"),
        (order_id,),
    )


def consume_coupon(conn, code: str, user_id: int, order_id: int) -> None:
    """Backward-compatible alias: reserve only (final use happens on order confirm)."""
    reserve_coupon(conn, code, user_id, order_id)


def coupon_raw_discount(coupon: dict, subtotal: int) -> int:
    """Знижка купона до округлення підсумку — рахується від суми кошика."""
    subtotal = max(0, int(subtotal or 0))
    value = int(coupon.get("value") or 0)
    if coupon.get("type") == "fixed":
        return min(value, subtotal)
    # Округлення вниз — так само, як в акціях і на вітрині (index.html).
    return subtotal * value // 100


def coupon_stacks_with_promotion(coupon: dict) -> bool:
    """Чи додається цей купон до акційної знижки (галочка + глобальний перемикач)."""
    from services.settings import coupon_stacking_enabled

    return bool(coupon.get("stackable")) and coupon_stacking_enabled()


def check_coupon(code: str, user_id: int, cart_total: int):
    conn = db_connect(dict_rows=True)
    code = code.upper()
    row = conn.execute(
        _sql(
            "SELECT code, type, value, min_order, uses_max, uses_count, one_per_user, "
            "active, expires_at, personal_user_id, stackable FROM coupons WHERE code = ?"
        ),
        (code,),
    ).fetchone()

    if not row:
        conn.close()
        return {"valid": False, "message": "Купон не знайдено ❌"}

    c = dict(row)

    if not c["active"]:
        conn.close()
        return {"valid": False, "message": "Купон вже не активний ❌"}

    expires = _parse_expires_at(c.get("expires_at"))
    if expires and datetime.now(timezone.utc) > expires:
        conn.close()
        return {"valid": False, "message": "Термін купону закінчився ❌"}

    if c["min_order"] and cart_total < c["min_order"]:
        conn.close()
        return {"valid": False, "message": f"Мінімальна сума замовлення: {c['min_order']} ₴ ❌"}

    pending_count = _pending_uses_count(conn, c["code"])
    if _coupon_uses_exhausted(c, pending_count):
        conn.close()
        return {"valid": False, "message": "Купон вичерпано ❌"}

    if c["one_per_user"] and user_id and _user_has_coupon_hold(conn, c["code"], user_id):
        conn.close()
        return {"valid": False, "message": "Ти вже використовував цей купон ❌"}

    err = _user_access_error(conn, code, c, user_id)
    if err:
        conn.close()
        return {"valid": False, "message": err}

    conn.close()

    raw_discount = coupon_raw_discount(c, cart_total)
    discount = actual_discount_after_rounding(cart_total, raw_discount)
    stacks = coupon_stacks_with_promotion(c)

    label = f"-{c['value']}%" if c["type"] == "percent" else f"-{c['value']} ₴"
    message = f"Купон застосовано! Знижка {label} ✅"
    if stacks:
        message = f"Купон застосовано! Знижка {label} — сумується з акцією 🔥"
    return {
        "valid": True,
        "type": c["type"],
        "value": c["value"],
        "discount": discount,
        "raw_discount": raw_discount,
        "stackable": bool(c.get("stackable")),
        "stacks_with_promotion": stacks,
        "message": message,
    }


def check_promotion(cart_total: int):
    """Автоматична знижка від активних акцій (налаштовуються в адмін-панелі)."""
    if not config.PROMOTION_ENABLED:
        return 0

    from services.promotions import compute_order_discount

    return compute_order_discount(cart_total)


def coupon_requires_channel_sub(code: str) -> bool:
    """Чи потрібна цьому купону підписка на канал (перевіряється в bot.py через getChatMember)."""
    if not code:
        return False
    conn = db_connect()
    row = conn.execute(
        _sql("SELECT require_channel_sub FROM coupons WHERE code = ?"),
        (code.upper(),),
    ).fetchone()
    conn.close()
    if not row:
        return False
    val = row["require_channel_sub"] if isinstance(row, dict) else row[0]
    return bool(val)


def channel_gated_codes(codes: list[str]) -> set[str]:
    """З переданого списку кодів повертає ті, що вимагають підписки на канал."""
    normalized = sorted({c.upper() for c in codes if c})
    if not normalized:
        return set()
    conn = db_connect()
    placeholders = ", ".join("?" for _ in normalized)
    rows = conn.execute(
        _sql(
            f"SELECT code FROM coupons WHERE require_channel_sub = 1 AND code IN ({placeholders})"
        ),
        tuple(normalized),
    ).fetchall()
    conn.close()
    result: set[str] = set()
    for r in rows:
        result.add((r["code"] if isinstance(r, dict) else r[0]).upper())
    return result


def _normalize_coupon_payload(data: dict, *, code_override: str | None = None) -> tuple[dict | None, str | None]:
    code = (code_override or data.get("code") or "").strip().upper()
    if not code:
        return None, "Код купона обов'язковий"

    ctype = str(data.get("type") or "").strip().lower()
    if ctype not in ("percent", "fixed"):
        return None, "Тип має бути percent або fixed"

    try:
        value = int(data.get("value"))
    except (TypeError, ValueError):
        return None, "Значення знижки має бути числом"
    if value <= 0:
        return None, "Значення знижки має бути більше 0"
    if ctype == "percent" and value > 100:
        return None, "Відсоткова знижка не може перевищувати 100"

    try:
        min_order = max(0, int(data.get("min_order") or 0))
        uses_max = max(0, int(data.get("uses_max") or 0))
        one_per_user = 1 if data.get("one_per_user") in (1, True, "1", "true") else 0
        active = 1 if data.get("active", 1) in (1, True, "1", "true") else 0
        stackable = 1 if data.get("stackable") in (1, True, "1", "true", "on") else 0
        require_channel_sub = 1 if data.get("require_channel_sub") in (1, True, "1", "true", "on") else 0
    except (TypeError, ValueError):
        return None, "Невірні числові параметри купона"

    expires_at = data.get("expires_at")
    if expires_at is not None:
        expires_at = str(expires_at).strip() or None

    allowed_user_ids = _parse_allowed_user_ids(data.get("allowed_user_ids"))

    personal_user_id = data.get("personal_user_id")
    if personal_user_id in ("", None):
        personal_user_id = None
    else:
        try:
            personal_user_id = int(personal_user_id)
        except (TypeError, ValueError):
            return None, "personal_user_id має бути числом"

    if allowed_user_ids:
        personal_user_id = None
    elif personal_user_id:
        allowed_user_ids = [personal_user_id]

    return {
        "code": code,
        "type": ctype,
        "value": value,
        "min_order": min_order,
        "uses_max": uses_max,
        "one_per_user": one_per_user,
        "active": active,
        "expires_at": expires_at,
        "personal_user_id": personal_user_id,
        "stackable": stackable,
        "require_channel_sub": require_channel_sub,
        "allowed_user_ids": allowed_user_ids,
    }, None


def get_coupon(code: str) -> dict | None:
    conn = db_connect(dict_rows=True)
    row = conn.execute(
        _sql(
            "SELECT code, type, value, min_order, uses_max, uses_count, one_per_user, active, "
            "expires_at, personal_user_id, stackable, require_channel_sub FROM coupons WHERE code = ?"
        ),
        (code.upper(),),
    ).fetchone()
    if not row:
        conn.close()
        return None
    coupon = row_to_dict(row)
    _attach_allowed_users(conn, coupon)
    conn.close()
    return coupon


def list_coupons() -> list[dict]:
    conn = db_connect(dict_rows=True)
    rows = conn.execute(
        _sql(
            "SELECT code, type, value, min_order, uses_max, uses_count, one_per_user, active, "
            "expires_at, personal_user_id, stackable, require_channel_sub "
            "FROM coupons ORDER BY active DESC, code ASC"
        )
    ).fetchall()
    coupons = [row_to_dict(r) for r in rows]
    _attach_allowed_users(conn, coupons)
    conn.close()
    return coupons


def create_coupon(data: dict) -> dict:
    payload, err = _normalize_coupon_payload(data)
    if err:
        return {"ok": False, "error": err}
    if get_coupon(payload["code"]):
        return {"ok": False, "error": "Купон з таким кодом вже існує"}

    allowed_user_ids = payload.pop("allowed_user_ids", [])

    conn = db_connect()
    try:
        conn.execute(
            _sql("""
                INSERT INTO coupons
                (code, type, value, min_order, uses_max, uses_count, one_per_user, active, expires_at, personal_user_id, stackable, require_channel_sub)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
            """),
            (
                payload["code"],
                payload["type"],
                payload["value"],
                payload["min_order"],
                payload["uses_max"],
                payload["one_per_user"],
                payload["active"],
                payload["expires_at"],
                payload["personal_user_id"],
                payload["stackable"],
                payload["require_channel_sub"],
            ),
        )
        _sync_allowed_users(conn, payload["code"], allowed_user_ids)
        conn.commit()
    except Exception:
        logger.exception("create_coupon failed for code=%s", payload.get("code"))
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()

    coupon = get_coupon(payload["code"])
    return {"ok": True, "created": True, "coupon": coupon, "added_user_ids": allowed_user_ids}


def update_coupon(code: str, data: dict) -> dict:
    existing = get_coupon(code)
    if not existing:
        return {"ok": False, "error": "Не знайдено"}

    payload, err = _normalize_coupon_payload(data, code_override=code)
    if err:
        return {"ok": False, "error": err}

    allowed_user_ids = payload.pop("allowed_user_ids", [])
    previous_ids = set(existing.get("allowed_user_ids") or [])
    new_ids = set(allowed_user_ids)

    conn = db_connect()
    try:
        conn.execute(
            _sql("""
                UPDATE coupons SET
                    type = ?, value = ?, min_order = ?, uses_max = ?,
                    one_per_user = ?, active = ?, expires_at = ?, personal_user_id = ?,
                    stackable = ?, require_channel_sub = ?
                WHERE code = ?
            """),
            (
                payload["type"],
                payload["value"],
                payload["min_order"],
                payload["uses_max"],
                payload["one_per_user"],
                payload["active"],
                payload["expires_at"],
                payload["personal_user_id"],
                payload["stackable"],
                payload["require_channel_sub"],
                payload["code"],
            ),
        )
        _sync_allowed_users(conn, payload["code"], allowed_user_ids)
        conn.commit()
    except Exception:
        logger.exception("update_coupon failed for code=%s", code)
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()

    coupon = get_coupon(payload["code"])
    added_user_ids = sorted(new_ids - previous_ids)
    return {"ok": True, "created": False, "coupon": coupon, "added_user_ids": added_user_ids}


def replace_coupon(data: dict) -> dict:
    payload, err = _normalize_coupon_payload(data)
    if err:
        return {"ok": False, "error": err}

    allowed_user_ids = payload.pop("allowed_user_ids", [])
    existing = get_coupon(payload["code"])
    previous_ids = set((existing or {}).get("allowed_user_ids") or [])

    conn = db_connect()
    try:
        if existing:
            conn.execute(
                _sql("""
                    UPDATE coupons SET
                        type = ?, value = ?, min_order = ?, uses_max = ?,
                        one_per_user = ?, active = ?, expires_at = ?, personal_user_id = ?,
                        stackable = ?, require_channel_sub = ?
                    WHERE code = ?
                """),
                (
                    payload["type"],
                    payload["value"],
                    payload["min_order"],
                    payload["uses_max"],
                    payload["one_per_user"],
                    payload["active"],
                    payload["expires_at"],
                    payload["personal_user_id"],
                    payload["stackable"],
                    payload["require_channel_sub"],
                    payload["code"],
                ),
            )
            created = False
        else:
            conn.execute(
                _sql("""
                    INSERT INTO coupons
                    (code, type, value, min_order, uses_max, uses_count, one_per_user, active, expires_at, personal_user_id, stackable, require_channel_sub)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                """),
                (
                    payload["code"],
                    payload["type"],
                    payload["value"],
                    payload["min_order"],
                    payload["uses_max"],
                    payload["one_per_user"],
                    payload["active"],
                    payload["expires_at"],
                    payload["personal_user_id"],
                    payload["stackable"],
                    payload["require_channel_sub"],
                ),
            )
            created = True
        _sync_allowed_users(conn, payload["code"], allowed_user_ids)
        conn.commit()
    except Exception:
        logger.exception("replace_coupon failed for code=%s", payload.get("code"))
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()

    coupon = get_coupon(payload["code"])
    added_user_ids = sorted(set(allowed_user_ids) - previous_ids) if not created else allowed_user_ids
    return {"ok": True, "created": created, "coupon": coupon, "added_user_ids": added_user_ids}


def add_coupon_users(code: str, user_ids: list[int] | str) -> dict:
    code = code.upper()
    coupon = get_coupon(code)
    if not coupon:
        return {"ok": False, "error": "Не знайдено"}

    new_ids = _parse_allowed_user_ids(user_ids)
    if not new_ids:
        return {"ok": False, "error": "Потрібен хоча б один Telegram user ID"}

    existing = set(coupon.get("allowed_user_ids") or [])
    to_add = [uid for uid in new_ids if uid not in existing]

    conn = db_connect()
    try:
        if to_add:
            conn.execute(
                _sql("UPDATE coupons SET personal_user_id = NULL WHERE code = ?"),
                (code,),
            )
        for uid in to_add:
            if is_postgres():
                conn.execute(
                    _sql(
                        "INSERT INTO coupon_allowed_users (code, user_id) VALUES (?, ?) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    (code, uid),
                )
            else:
                conn.execute(
                    _sql("INSERT OR IGNORE INTO coupon_allowed_users (code, user_id) VALUES (?, ?)"),
                    (code, uid),
                )
        conn.commit()
    except Exception:
        logger.exception("add_coupon_users failed for code=%s", code)
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()

    updated = get_coupon(code)
    return {"ok": True, "coupon": updated, "added_user_ids": to_add}


def remove_coupon_user(code: str, user_id: int) -> dict:
    code = code.upper()
    coupon = get_coupon(code)
    if not coupon:
        return {"ok": False, "error": "Не знайдено"}

    uid = int(user_id)
    conn = db_connect()
    try:
        conn.execute(
            _sql("DELETE FROM coupon_allowed_users WHERE code = ? AND user_id = ?"),
            (code, uid),
        )
        conn.commit()
    except Exception:
        logger.exception("remove_coupon_user failed for code=%s user=%s", code, uid)
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()

    return {"ok": True, "coupon": get_coupon(code), "removed_user_id": uid}


def set_coupon_active(code: str, active: bool) -> dict:
    code = code.upper()
    if not get_coupon(code):
        return {"ok": False, "error": "Не знайдено"}
    conn = db_connect()
    try:
        conn.execute(_sql("UPDATE coupons SET active = ? WHERE code = ?"), (1 if active else 0, code))
        conn.commit()
    except Exception:
        logger.exception("set_coupon_active failed for code=%s", code)
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()
    return {"ok": True, "coupon": get_coupon(code)}


def delete_coupon(code: str) -> dict:
    code = code.upper()
    if not get_coupon(code):
        return {"ok": False, "error": "Не знайдено"}
    conn = db_connect()
    try:
        conn.execute(_sql("DELETE FROM coupon_allowed_users WHERE code = ?"), (code,))
        conn.execute(_sql("DELETE FROM coupon_uses WHERE code = ?"), (code,))
        conn.execute(_sql("DELETE FROM coupons WHERE code = ?"), (code,))
        conn.commit()
    except Exception:
        logger.exception("delete_coupon failed for code=%s", code)
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()
    return {"ok": True}


def get_my_coupons(user_id: int):
    """Купони, доступні користувачу (персональні, whitelist та публічні)."""
    conn = db_connect()
    date_expr = "NOW()" if is_postgres() else "datetime('now')"
    rows = conn.execute(
        _sql(f"""
            SELECT
                c.code,
                c.type,
                c.value,
                c.min_order,
                c.uses_max,
                c.uses_count,
                c.one_per_user,
                c.expires_at,
                c.stackable,
                EXISTS(
                    SELECT 1
                    FROM coupon_uses cu
                    WHERE cu.code = c.code AND cu.user_id = ? AND cu.status = 'confirmed'
                ) AS used_by_user
            FROM coupons c
            WHERE c.active = 1
              AND (
                    (
                        c.personal_user_id IS NULL
                        AND NOT EXISTS (
                            SELECT 1 FROM coupon_allowed_users cau WHERE cau.code = c.code
                        )
                    )
                    OR c.personal_user_id = ?
                    OR EXISTS (
                        SELECT 1 FROM coupon_allowed_users cau
                        WHERE cau.code = c.code AND cau.user_id = ?
                    )
              )
              AND (c.expires_at IS NULL OR c.expires_at > {date_expr})
              AND (
                    c.uses_max = 0
                    OR c.uses_count + (
                        SELECT COUNT(*) FROM coupon_uses cu2
                        WHERE cu2.code = c.code AND cu2.status = 'pending'
                    ) < c.uses_max
              )
            ORDER BY
                CASE WHEN EXISTS (
                    SELECT 1 FROM coupon_allowed_users cau WHERE cau.code = c.code
                ) OR c.personal_user_id IS NOT NULL THEN 0 ELSE 1 END,
                c.code ASC
        """),
        (user_id, user_id, user_id),
    ).fetchall()
    conn.close()
    return rows
