"""Order validation for checkout."""

from __future__ import annotations

import re

from catalog_store import (
    CUSTOM_PRODUCTS_CACHE,
    FILAMENTS_CACHE,
    PRODUCTS_CACHE,
    is_contract_product,
    is_glow_filament,
    reload_filaments_cache,
    reload_products_cache,
)
from services.coupons import check_coupon
from services.pricing import split_discounts
from services.promotions import best_order_promotion, compute_order_raw_discount, free_shipping_promotion

CUSTOM_VALUE_MAX = 300
_NOTE_SEPARATOR = " · побажання:"


def split_personalization(custom_value: str) -> tuple[str, str]:
    """«текст · побажання: …» -> (текст, побажання)."""
    text, sep, note = custom_value.partition(_NOTE_SEPARATOR)
    return text.strip()[:200], (note.strip()[:200] if sep else "")


def normalize_phone(raw: str | None) -> str | None:
    """Український номер у вигляді +380XXXXXXXXX; None — якщо не схоже на номер."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 10 and digits.startswith("0"):
        digits = "38" + digits
    elif len(digits) == 9:
        digits = "380" + digits
    if len(digits) == 12 and digits.startswith("380"):
        return "+" + digits
    return None


def validate_checkout_fields(data: dict) -> tuple[bool, dict | str]:
    """Поля доставки/контакту з оформлення. Порожній delivery_method — стара поведінка (без вимог)."""
    method = str(data.get("delivery_method") or "").strip()
    fields: dict = {"source": "miniapp"}
    if data.get("promotion_id"):
        fields["promotion_id"] = str(data["promotion_id"])[:64]
    if data.get("gift_product_id"):
        fields["gift_product_id"] = data["gift_product_id"]
    pay = str(data.get("pay_method") or "").strip()
    if pay in ("card", "cod"):
        fields["pay_method"] = pay
    if not method:
        return True, fields
    if method not in ("np", "up", "self"):
        return False, "Невідомий спосіб доставки"
    fields["delivery_method"] = method
    name = str(data.get("recipient_name") or "").strip()
    phone = normalize_phone(data.get("phone"))
    if name:
        fields["recipient_name"] = name[:100]
    if phone:
        fields["phone"] = phone
    if method == "self":
        return True, fields
    if not name:
        return False, "Вкажи ім'я та прізвище отримувача"
    if not phone:
        return False, "Для доставки потрібен номер телефону"
    city = str(data.get("city") or "").strip()
    branch = str(data.get("branch") or "").strip()
    if not city:
        return False, "Обери місто доставки"
    if not branch:
        return False, "Обери відділення або поштомат"
    fields.update(
        city=city[:200],
        city_ref=str(data.get("city_ref") or "").strip()[:100],
        branch=branch[:300],
        branch_ref=str(data.get("branch_ref") or "").strip()[:100],
    )
    if pay not in ("card", "cod"):
        fields["pay_method"] = "card"
    return True, fields


def validate_order_payload(items: list, coupon_code: str | None, user_id: int, client_total: int):
    """Перерахунок суми на сервері. Повертає (ok, result_dict|error_message)."""
    reload_products_cache()
    reload_filaments_cache()
    products_by_id = {p["id"]: p for p in PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE}
    if not items:
        return False, "Порожній кошик"

    subtotal = 0
    cat_totals: dict[str, int] = {}
    normalized = []
    seen_line_keys: set[tuple] = set()

    for raw in items:
        pid = int(raw.get("product_id") or raw.get("id") or 0)
        try:
            qty = int(raw.get("quantity", 1))
        except (TypeError, ValueError):
            return False, "Невірна кількість товару"
        if qty < 1 or qty > 99:
            return False, "Кількість товару має бути від 1 до 99"

        product = products_by_id.get(pid)
        is_contract = False

        if not product:
            return False, f"Невідомий товар (id {pid})"

        if is_contract_product(product):
            price = 0
            is_contract = True
        else:
            price = int(product["price"])
            if price <= 0:
                return False, f"Невалідна ціна товару «{product.get('name', pid)}»"
        name = product["name"]

        filament_id = str(raw.get("filament_id") or raw.get("filamentId") or "").strip()
        filament_name = ""
        is_custom_product = bool(
            product.get("is_custom")
            or product.get("isCustom")
            or product.get("cat") == "custom"
            or raw.get("fromCustom")
        )
        if is_custom_product:
            filament_id = ""
            filament_name = ""
        else:
            no_filament_choice = bool(product and product.get("filamentChoice") is False)
            if no_filament_choice:
                filament_id = ""
                filament_name = ""
            elif filament_id:
                meta = next((f for f in FILAMENTS_CACHE if f.get("id") == filament_id), None)
                if not meta:
                    return False, f"Невідомий колір філаменту ({filament_id})"
                if not meta.get("available"):
                    return False, f"Колір «{meta.get('name', '')}» зараз недоступний для замовлення"
                if is_glow_filament(meta) and not (
                    product and product.get("luminousFilamentChoice")
                ):
                    return False, "Цей колір недоступний для обраного товару"
                filament_name = str(meta.get("name") or "").strip()

        # Персоналізація з картки товару: текст + побажання (до 200) — з запасом на форматування
        custom_value = str(raw.get("customValue") or "").strip()[:CUSTOM_VALUE_MAX]
        line_key = (pid, filament_id, custom_value)
        if line_key in seen_line_keys:
            return False, f"Дублікат товару в кошику (id {pid})"
        seen_line_keys.add(line_key)

        if not is_contract:
            subtotal += price * qty
            cat_key = str(product.get("cat") or "")
            cat_totals[cat_key] = cat_totals.get(cat_key, 0) + price * qty
        personalization, personalization_note = split_personalization(custom_value)
        normalized.append(
            {
                "product_id": pid,
                "personalization": personalization,
                "personalization_note": personalization_note,
                "product_name": name,
                "price": price,
                "quantity": qty,
                "customValue": custom_value,
                "fromCustom": is_custom_product,
                "filament_id": filament_id,
                "filament_name": filament_name,
                "is_contract_price": is_contract,
                "comment": (raw.get("comment") or "").strip(),
            }
        )

    raw_coupon = 0
    stack = False
    if coupon_code:
        coupon_result = check_coupon(coupon_code, user_id, subtotal)
        if not coupon_result.get("valid"):
            return False, coupon_result.get("message", "Невалідний купон")
        raw_coupon = int(coupon_result.get("raw_discount", coupon_result.get("discount", 0)))
        stack = bool(coupon_result.get("stacks_with_promotion"))

    raw_promotion = compute_order_raw_discount(subtotal, cat_totals)
    best_promo = best_order_promotion(subtotal, cat_totals)[0] if raw_promotion > 0 else None
    discount, promotion_discount = split_discounts(
        subtotal, raw_coupon, raw_promotion, stack=stack
    )
    server_total = max(0, subtotal - discount - promotion_discount)
    free_ship = free_shipping_promotion(subtotal)

    if server_total != int(client_total):
        return False, f"Сума не збігається (клієнт {client_total}, сервер {server_total})"

    return True, {
        "items": normalized,
        "subtotal": subtotal,
        "coupon_discount": discount,
        "promotion_discount": promotion_discount,
        "total_price": server_total,
        # Акція, що дала знижку (для orders.promotion_id і статистики акцій) та акція безкоштовної доставки
        "promotion_id": best_promo["id"] if best_promo and promotion_discount > 0 else "",
        "free_ship_promotion_id": free_ship["id"] if free_ship else "",
        "price_pending": 1 if any(i.get("is_contract_price") for i in normalized) else 0,
    }
