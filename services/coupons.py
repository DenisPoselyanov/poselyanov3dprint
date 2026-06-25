"""Coupon validation and CRUD."""

from __future__ import annotations

from datetime import datetime, timezone

import config
from db_core import db_connect, is_postgres, sql as _sql
from services.db_utils import row_to_dict


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
    except Exception as e:
        return {"ok": False, "error": str(e)}
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
    except Exception as e:
        return {"ok": False, "error": str(e)}
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
    except Exception as e:
        return {"ok": False, "error": str(e)}
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
    except Exception as e:
        return {"ok": False, "error": str(e)}
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
    except Exception as e:
        return {"ok": False, "error": str(e)}
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
