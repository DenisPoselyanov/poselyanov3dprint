"""Coupon validation and CRUD."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import config
from db_core import db_connect, is_postgres, sql as _sql
from services.db_utils import row_to_dict

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
    }


def _validate_coupon_row(c: dict, user_id: int, *, check_min_order: int | None = None) -> str | None:
    if not c:
        return "Купон не знайдено ❌"
    if not c.get("active"):
        return "Купон вже не активний ❌"

    expires = _parse_expires_at(c.get("expires_at"))
    if expires and datetime.now(timezone.utc) > expires:
        return "Термін купону закінчився ❌"

    if check_min_order is not None and c.get("min_order") and check_min_order < c["min_order"]:
        return f"Мінімальна сума замовлення: {c['min_order']} ₴ ❌"

    if c.get("uses_max") and c.get("uses_count", 0) >= c["uses_max"]:
        return "Купон вичерпано ❌"

    if c.get("personal_user_id") and user_id and int(c["personal_user_id"]) != int(user_id):
        return "Цей купон призначений іншому користувачу ❌"

    return None


def consume_coupon(conn, code: str, user_id: int, order_id: int) -> None:
    """Atomically reserve coupon use inside an open DB transaction."""
    code = code.upper()
    uid = int(user_id or 0)

    if is_postgres():
        row = conn.execute(
            """
            SELECT code, type, value, min_order, uses_max, uses_count, one_per_user,
                   active, expires_at, personal_user_id
            FROM coupons WHERE code = %s FOR UPDATE
            """,
            (code,),
        ).fetchone()
    else:
        row = conn.execute(
            _sql(
                "SELECT code, type, value, min_order, uses_max, uses_count, one_per_user, "
                "active, expires_at, personal_user_id FROM coupons WHERE code = ?"
            ),
            (code,),
        ).fetchone()

    c = _coupon_row_to_dict(row)
    err = _validate_coupon_row(c, uid)
    if err:
        raise CouponConsumptionError(err)

    if c.get("one_per_user") and uid:
        used = conn.execute(
            _sql("SELECT 1 FROM coupon_uses WHERE code = ? AND user_id = ?"),
            (code, uid),
        ).fetchone()
        if used:
            raise CouponConsumptionError("Ти вже використовував цей купон ❌")

    if is_postgres():
        updated = conn.execute(
            """
            UPDATE coupons
            SET uses_count = uses_count + 1
            WHERE code = %s AND (uses_max = 0 OR uses_count < uses_max)
            RETURNING uses_count
            """,
            (code,),
        ).fetchone()
        if not updated:
            raise CouponConsumptionError("Купон вичерпано ❌")
    else:
        cur = conn.execute(
            _sql(
                """
                UPDATE coupons
                SET uses_count = uses_count + 1
                WHERE code = ? AND (uses_max = 0 OR uses_count < uses_max)
                """
            ),
            (code,),
        )
        if cur.rowcount == 0:
            raise CouponConsumptionError("Купон вичерпано ❌")

    if c.get("one_per_user") and uid:
        if is_postgres():
            inserted = conn.execute(
                """
                INSERT INTO coupon_uses (code, user_id, order_id)
                SELECT %s, %s, %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM coupon_uses WHERE code = %s AND user_id = %s
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
                    INSERT INTO coupon_uses (code, user_id, order_id)
                    SELECT ?, ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1 FROM coupon_uses WHERE code = ? AND user_id = ?
                    )
                    """
                ),
                (code, uid, order_id, code, uid),
            )
            if cur.rowcount == 0:
                raise CouponConsumptionError("Ти вже використовував цей купон ❌")
    else:
        conn.execute(
            _sql("INSERT INTO coupon_uses (code, user_id, order_id) VALUES (?, ?, ?)"),
            (code, uid, order_id),
        )


def check_coupon(code: str, user_id: int, cart_total: int):
    conn = db_connect(dict_rows=True)
    row = conn.execute(
        _sql(
            "SELECT code, type, value, min_order, uses_max, uses_count, one_per_user, "
            "active, expires_at, personal_user_id FROM coupons WHERE code = ?"
        ),
        (code.upper(),),
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

    if c["uses_max"] and c["uses_count"] >= c["uses_max"]:
        conn.close()
        return {"valid": False, "message": "Купон вичерпано ❌"}

    if c["one_per_user"] and user_id:
        used = conn.execute(
            _sql("SELECT 1 FROM coupon_uses WHERE code = ? AND user_id = ?"),
            (c["code"], user_id),
        ).fetchone()
        if used:
            conn.close()
            return {"valid": False, "message": "Ти вже використовував цей купон ❌"}

    if c.get("personal_user_id") and user_id and int(c["personal_user_id"]) != int(user_id):
        conn.close()
        return {"valid": False, "message": "Цей купон призначений іншому користувачу ❌"}

    conn.close()

    discount = c["value"] if c["type"] == "fixed" else round(cart_total * c["value"] / 100)
    discount = min(discount, cart_total)

    label = f"-{c['value']}%" if c["type"] == "percent" else f"-{c['value']} ₴"
    return {
        "valid": True,
        "type": c["type"],
        "value": c["value"],
        "discount": discount,
        "message": f"Купон застосовано! Знижка {label} ✅",
    }


def check_promotion(cart_total: int):
    if not config.PROMOTION_ENABLED:
        return 0

    promotion_min_amount = 500
    promotion_discount_rate = 0.10

    if cart_total >= promotion_min_amount:
        return int(cart_total * promotion_discount_rate)
    return 0


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
    except (TypeError, ValueError):
        return None, "Невірні числові параметри купона"

    expires_at = data.get("expires_at")
    if expires_at is not None:
        expires_at = str(expires_at).strip() or None

    personal_user_id = data.get("personal_user_id")
    if personal_user_id in ("", None):
        personal_user_id = None
    else:
        try:
            personal_user_id = int(personal_user_id)
        except (TypeError, ValueError):
            return None, "personal_user_id має бути числом"

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
    }, None


def get_coupon(code: str) -> dict | None:
    conn = db_connect(dict_rows=True)
    row = conn.execute(
        _sql(
            "SELECT code, type, value, min_order, uses_max, uses_count, one_per_user, active, "
            "expires_at, personal_user_id FROM coupons WHERE code = ?"
        ),
        (code.upper(),),
    ).fetchone()
    conn.close()
    return row_to_dict(row) if row else None


def list_coupons() -> list[dict]:
    conn = db_connect(dict_rows=True)
    rows = conn.execute(
        _sql(
            "SELECT code, type, value, min_order, uses_max, uses_count, one_per_user, active, "
            "expires_at, personal_user_id FROM coupons ORDER BY active DESC, code ASC"
        )
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


def create_coupon(data: dict) -> dict:
    payload, err = _normalize_coupon_payload(data)
    if err:
        return {"ok": False, "error": err}
    if get_coupon(payload["code"]):
        return {"ok": False, "error": "Купон з таким кодом вже існує"}

    conn = db_connect()
    try:
        conn.execute(
            _sql("""
                INSERT INTO coupons
                (code, type, value, min_order, uses_max, uses_count, one_per_user, active, expires_at, personal_user_id)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
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
            ),
        )
        conn.commit()
    except Exception:
        logger.exception("create_coupon failed for code=%s", payload.get("code"))
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()

    coupon = get_coupon(payload["code"])
    return {"ok": True, "created": True, "coupon": coupon}


def update_coupon(code: str, data: dict) -> dict:
    existing = get_coupon(code)
    if not existing:
        return {"ok": False, "error": "Не знайдено"}

    payload, err = _normalize_coupon_payload(data, code_override=code)
    if err:
        return {"ok": False, "error": err}

    conn = db_connect()
    try:
        conn.execute(
            _sql("""
                UPDATE coupons SET
                    type = ?, value = ?, min_order = ?, uses_max = ?,
                    one_per_user = ?, active = ?, expires_at = ?, personal_user_id = ?
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
                payload["code"],
            ),
        )
        conn.commit()
    except Exception:
        logger.exception("update_coupon failed for code=%s", code)
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()

    return {"ok": True, "created": False, "coupon": get_coupon(payload["code"])}


def replace_coupon(data: dict) -> dict:
    payload, err = _normalize_coupon_payload(data)
    if err:
        return {"ok": False, "error": err}

    existing = get_coupon(payload["code"])
    conn = db_connect()
    try:
        if existing:
            conn.execute(
                _sql("""
                    UPDATE coupons SET
                        type = ?, value = ?, min_order = ?, uses_max = ?,
                        one_per_user = ?, active = ?, expires_at = ?, personal_user_id = ?
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
                    payload["code"],
                ),
            )
            created = False
        else:
            conn.execute(
                _sql("""
                    INSERT INTO coupons
                    (code, type, value, min_order, uses_max, uses_count, one_per_user, active, expires_at, personal_user_id)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
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
                ),
            )
            created = True
        conn.commit()
    except Exception:
        logger.exception("replace_coupon failed for code=%s", payload.get("code"))
        return {"ok": False, "error": _DB_ERROR_MESSAGE}
    finally:
        conn.close()

    coupon = get_coupon(payload["code"])
    return {"ok": True, "created": created, "coupon": coupon}


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
    """Купони, доступні користувачу (персональні та публічні)."""
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
                EXISTS(
                    SELECT 1
                    FROM coupon_uses cu
                    WHERE cu.code = c.code AND cu.user_id = ?
                ) AS used_by_user
            FROM coupons c
            WHERE c.active = 1
              AND (c.personal_user_id IS NULL OR c.personal_user_id = ?)
              AND (c.expires_at IS NULL OR c.expires_at > {date_expr})
              AND (c.uses_max = 0 OR c.uses_count < c.uses_max)
            ORDER BY c.personal_user_id DESC, c.code ASC
        """),
        (user_id, user_id),
    ).fetchall()
    conn.close()
    return rows
