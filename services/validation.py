"""Order validation for checkout."""

from __future__ import annotations

import re

from catalog_store import (
    CUSTOM_PRODUCTS_CACHE,
    FILAMENTS_CACHE,
    PRODUCTS_CACHE,
    get_product_variant,
    is_contract_product,
    is_glow_filament,
    reload_filaments_cache,
    reload_products_cache,
)
from services.coupons import check_coupon
from services.pricing import split_discounts
from services.promotions import (
    best_order_promotion,
    compute_order_raw_discount,
    free_shipping_promotion,
    get_gift_rule,
    promo_categories,
)

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


def _prize_line(prize: dict, products_by_id: dict) -> tuple[dict | None, str | None]:
    """Рядок товару-призу за 0 ₴. Варіант — зафіксований у розіграші, інакше обраний переможцем."""
    product = products_by_id.get(int(prize.get("product_id") or 0))
    if not product:
        return None, "Товар-приз зараз недоступний — напишіть нам, і ми все владнаємо"
    variant_id = variant_name = ""
    if product.get("variants"):
        variant = get_product_variant(product, prize.get("product_variant") or prize.get("variant_id"))
        if not variant or variant.get("contract"):
            return None, "Обери розмір чи варіант призу"
        variant_id, variant_name = variant["id"], variant["name"]
    name = product["name"]
    return {
        "product_id": int(product["id"]),
        "personalization": "",
        "personalization_note": "",
        "product_name": f"🎁 Приз розіграшу: {name}" + (f" · {variant_name}" if variant_name else ""),
        "variant_id": variant_id,
        "variant": variant_name,
        "price": 0,
        "quantity": 1,
        "customValue": "",
        "fromCustom": False,
        "filament_id": "",
        "filament_name": "",
        "is_contract_price": False,
        "is_prize": True,
        "comment": "",
    }, None


def resolve_order_gift(items: list, gift_product_id) -> tuple[int, str] | None:
    """Подарунок за акцією gift_threshold — лише якщо кошик справді проходить поріг.

    items — позиції після validate_order_payload. Повертає (id, назва для замовлення) або None:
    клієнт може лише попросити конкретний подарунок, а право на нього й сам товар перевіряє сервер."""
    try:
        gift_id = int(gift_product_id or 0)
    except (TypeError, ValueError):
        return None
    rule = get_gift_rule()
    if gift_id <= 0 or not rule:
        return None
    products_by_id = {p["id"]: p for p in PRODUCTS_CACHE}
    categories = promo_categories(rule)
    eligible = set()
    for it in items:
        if it.get("is_prize") or it.get("is_contract_price") or it.get("fromCustom"):
            continue
        product = products_by_id.get(int(it.get("product_id") or 0))
        if product and (not categories or str(product.get("cat") or "") in categories):
            eligible.add(int(product["id"]))
    if len(eligible) < int(rule.get("min_items") or 0):
        return None
    gift = products_by_id.get(gift_id)
    max_price = int(rule.get("max_gift_price") or 0)
    if not gift or not gift.get("gift"):
        return None
    if max_price and int(gift.get("price") or 0) > max_price:
        return None
    return gift_id, str(gift.get("name") or "").strip()[:200]


def validate_order_payload(items: list, coupon_code: str | None, user_id: int, client_total: int,
                           prize: dict | None = None):
    """Перерахунок суми на сервері. Повертає (ok, result_dict|error_message).

    prize — активний приз розіграшу (services.giveaways.get_active_prize + обраний variant_id):
    товар додається рядком за 0 ₴, сертифікат віднімається від підсумку після всіх знижок."""
    reload_products_cache()
    reload_filaments_cache()
    products_by_id = {p["id"]: p for p in PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE}
    items = items or []
    if not items and not (prize and prize.get("prize_type") == "product"):
        return False, "Порожній кошик"

    subtotal = 0
    cat_totals: dict[str, int] = {}
    normalized = []
    seen_line_keys: set[tuple] = set()

    for raw in items:
        if not isinstance(raw, dict):
            return False, "Невірний формат кошика"
        try:
            pid = int(raw.get("product_id") or raw.get("id") or 0)
            qty = int(raw.get("quantity", 1))
        except (TypeError, ValueError):
            return False, "Невірна кількість товару"
        if qty < 1 or qty > 99:
            return False, "Кількість товару має бути від 1 до 99"

        product = products_by_id.get(pid)
        is_contract = False

        if not product:
            return False, f"Невідомий товар (id {pid})"

        name = product["name"]
        variant_id = ""
        variant_name = ""
        if product.get("variants"):
            # Ціна — лише з обраного варіанта; без варіанта позицію не приймаємо
            variant = get_product_variant(product, raw.get("variant_id") or raw.get("variantId"))
            if not variant:
                return False, f"Обери варіант товару «{name}» ще раз — його змінили"
            variant_id = variant["id"]
            variant_name = variant["name"]
            if variant.get("contract"):
                price = 0
                is_contract = True
            else:
                price = int(variant["price"])
        elif is_contract_product(product):
            price = 0
            is_contract = True
        else:
            price = int(product["price"])
        if not is_contract and price <= 0:
            return False, f"Невалідна ціна товару «{product.get('name', pid)}»"

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
        line_key = (pid, variant_id, filament_id, custom_value)
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
                "product_name": f"{name} · {variant_name}" if variant_name else name,
                "variant_id": variant_id,
                "variant": variant_name,
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

    if prize and prize.get("prize_type") == "product":
        line, err = _prize_line(prize, products_by_id)
        if err:
            return False, err
        normalized.insert(0, line)

    raw_coupon = 0
    stack = False
    if coupon_code:
        # Ціна однієї штуки кожного товару (для купона на конкретний товар — найдорожча позиція)
        product_prices: dict[int, int] = {}
        for it in normalized:
            if not it["is_contract_price"] and not it.get("is_prize"):
                pid_ = int(it["product_id"])
                product_prices[pid_] = max(product_prices.get(pid_, 0), int(it["price"]))
        coupon_result = check_coupon(coupon_code, user_id, subtotal, product_prices)
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
    prize_discount = 0
    if prize and prize.get("prize_type") == "amount":
        prize_discount = min(int(prize.get("prize_value") or 0), server_total)
        if prize_discount <= 0:
            return False, "Додай товари в кошик — сертифікат діє на замовлення"
        server_total -= prize_discount
    free_ship = free_shipping_promotion(subtotal)

    if server_total != int(client_total):
        return False, f"Сума не збігається (клієнт {client_total}, сервер {server_total})"

    return True, {
        "items": normalized,
        "subtotal": subtotal,
        "coupon_discount": discount,
        "promotion_discount": promotion_discount,
        "prize_discount": prize_discount,
        "prize_variant_id": normalized[0]["variant_id"] if normalized and normalized[0].get("is_prize") else "",
        "total_price": server_total,
        # Акція, що дала знижку (для orders.promotion_id і статистики акцій) та акція безкоштовної доставки
        "promotion_id": best_promo["id"] if best_promo and promotion_discount > 0 else "",
        "free_ship_promotion_id": free_ship["id"] if free_ship else "",
        "price_pending": 1 if any(i.get("is_contract_price") for i in normalized) else 0,
    }
