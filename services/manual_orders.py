"""Замовлення, створені адміном вручну (Instagram, OLX, особисто), і CSV-експорт."""

from __future__ import annotations

import csv
import io
import re

from services.orders import ORDER_STATUSES
from services.validation import normalize_phone

MANUAL_SOURCES = ("instagram", "olx", "telegram", "offline")
MAX_ITEMS = 30
MAX_QTY = 99
MAX_PRICE = 1_000_000

STATUS_LABELS_UA = {
    "new": "Нове",
    "draft": "Під питанням",
    "confirmed": "Підтверджено",
    "printing": "Друкується",
    "ready": "Готове",
    "shipped": "Відправлено",
    "delivered": "Отримано",
    "cancelled": "Скасовано",
}


def _int(value, default=0) -> int:
    try:
        return int(float(str(value).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return default


def parse_manual_order(data: dict) -> tuple[bool, dict | str]:
    """Перевіряє тіло POST /api/orders. Ok -> kwargs для save_order + статус; інакше текст помилки."""
    name = str(data.get("customer_name") or "").strip()[:100]
    if not name:
        return False, "Вкажіть імʼя клієнта"

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return False, "Додайте хоча б одну позицію"
    if len(raw_items) > MAX_ITEMS:
        return False, f"Забагато позицій (максимум {MAX_ITEMS})"

    items: list[dict] = []
    total = 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            return False, "Невірна позиція"
        product_name = str(raw.get("product_name") or "").strip()[:200]
        qty = _int(raw.get("quantity"), 1)
        price = _int(raw.get("price"), -1)
        if not product_name:
            return False, "У позиції немає назви товару"
        if not 1 <= qty <= MAX_QTY:
            return False, f"Кількість має бути від 1 до {MAX_QTY}"
        if not 0 <= price <= MAX_PRICE:
            return False, "Вкажіть ціну позиції"
        items.append(
            {
                "product_id": _int(raw.get("product_id"), 0),
                "product_name": product_name,
                "price": price,
                "quantity": qty,
                "filament_name": str(raw.get("filament") or "").strip()[:80],
                "comment": str(raw.get("comment") or "").strip()[:300],
                "personalization": str(raw.get("personalization") or "").strip()[:200],
            }
        )
        total += price * qty

    source = str(data.get("source") or "offline").strip().lower()
    if source not in MANUAL_SOURCES:
        return False, "Невідоме джерело замовлення"

    username = str(data.get("username") or "").strip().lstrip("@")[:64]
    if username and not re.fullmatch(r"[A-Za-z0-9_]{3,64}", username):
        return False, "Невірний @username"
    user_id = _int(data.get("user_id"), 0)
    if user_id < 0:
        return False, "Невірний Telegram ID"

    phone_raw = str(data.get("phone") or "").strip()
    phone = normalize_phone(phone_raw) if phone_raw else ""
    if phone_raw and not phone:
        return False, "Телефон має бути у форматі +380XXXXXXXXX"

    extra = {
        "source": source,
        "delivery_method": str(data.get("delivery_method") or ""),
        "city": str(data.get("city") or ""),
        "branch": str(data.get("branch") or ""),
        "recipient_name": str(data.get("recipient_name") or name),
        "phone": phone or "",
        "pay_method": str(data.get("pay_method") or ""),
        "internal_note": str(data.get("internal_note") or ""),
    }

    status = str(data.get("status") or "confirmed").strip().lower()
    if status not in ORDER_STATUSES:
        return False, "Невідомий статус"

    return True, {
        "user_id": user_id,
        "username": f"@{username}" if username else "",
        "first_name": name,
        "items": items,
        "total_price": total,
        "comment": str(data.get("comment") or "").strip()[:1000],
        "extra": extra,
        "status": status,
    }


_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")
_CSV_LEADING_NOISE = " \u00a0\u200b\ufeff"


def _csv_cell(value) -> str:
    """Захист від формул у Excel/Sheets: перевіряємо перший значущий символ (OWASP)."""
    text = "" if value is None else str(value)
    if text.lstrip(_CSV_LEADING_NOISE).startswith(_CSV_FORMULA_TRIGGERS):
        return "'" + text
    return text


def orders_to_csv(orders: list[dict]) -> str:
    """CSV (UTF-8, ; як роздільник — так Excel в укр. локалі відкриває одразу в колонках)."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        ["№", "Дата", "Статус", "Клієнт", "Username", "Телефон", "Джерело", "Товари", "Сума, ₴",
         "Знижка, ₴", "Купон", "Доставка", "Місто", "Відділення", "ТТН", "Оплата", "Коментар", "Нотатка"]
    )
    for o in orders:
        products = "; ".join(
            f"{i.get('product_name')} ×{i.get('quantity')}" + (f" ({i['filament']})" if i.get("filament") else "")
            for i in o.get("items") or []
        )
        writer.writerow(
            [_csv_cell(v) for v in (
                o.get("id"), o.get("ordered_at"), STATUS_LABELS_UA.get(o.get("status") or "new", o.get("status")),
                o.get("first_name"), o.get("username"), o.get("phone"), o.get("source"), products,
                o.get("total_price"), o.get("discount_amount"), o.get("coupon_code"), o.get("delivery_method"),
                o.get("city"), o.get("branch"), o.get("ttn"), o.get("pay_method"), o.get("comment"),
                o.get("internal_note"),
            )]
        )
    return buf.getvalue()
