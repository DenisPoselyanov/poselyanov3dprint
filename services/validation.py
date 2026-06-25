"""Order validation for checkout."""

from __future__ import annotations

from catalog_store import (
    CUSTOM_PRODUCTS_CACHE,
    FILAMENTS_CACHE,
    PRODUCTS_CACHE,
    is_contract_product,
    reload_filaments_cache,
    reload_products_cache,
)
from services.coupons import check_coupon, check_promotion


def validate_order_payload(items: list, coupon_code: str | None, user_id: int, client_total: int):
    """Перерахунок суми на сервері. Повертає (ok, result_dict|error_message)."""
    reload_products_cache()
    reload_filaments_cache()
    products_by_id = {p["id"]: p for p in PRODUCTS_CACHE + CUSTOM_PRODUCTS_CACHE}
    if not items:
        return False, "Порожній кошик"

    subtotal = 0
    normalized = []

    for raw in items:
        pid = int(raw.get("product_id") or raw.get("id") or 0)
        qty = max(1, min(99, int(raw.get("quantity", 1))))
        product = products_by_id.get(pid)
        is_contract = False

        if product:
            if is_contract_product(product):
                price = 0
                is_contract = True
            else:
                price = int(product["price"])
            name = product["name"]
        elif raw.get("fromCustom"):
            price = int(raw.get("price", 0))
            name = raw.get("product_name") or "—"
            if price <= 0:
                return False, f"Невідомий індивідуальний товар (id {pid})"
        else:
            return False, f"Невідомий товар (id {pid})"

        filament_id = str(raw.get("filament_id") or raw.get("filamentId") or "").strip()
        filament_name = ""
        if raw.get("fromCustom"):
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
                if str(filament_id or "").startswith("luminous") and not (
                    product and product.get("luminousFilamentChoice")
                ):
                    return False, "Цей колір недоступний для обраного товару"
                filament_name = str(meta.get("name") or "").strip()

        if not is_contract:
            subtotal += price * qty
        normalized.append(
            {
                "product_id": pid,
                "product_name": name,
                "price": price,
                "quantity": qty,
                "customValue": raw.get("customValue") or "",
                "fromCustom": bool(raw.get("fromCustom")),
                "filament_id": filament_id,
                "filament_name": filament_name,
                "is_contract_price": is_contract,
                "comment": (raw.get("comment") or "").strip(),
            }
        )

    discount = 0
    if coupon_code:
        coupon_result = check_coupon(coupon_code, user_id, subtotal)
        if not coupon_result.get("valid"):
            return False, coupon_result.get("message", "Невалідний купон")
        discount = int(coupon_result.get("discount", 0))

    after_coupon_total = max(0, subtotal - discount)
    promotion_discount = 0 if coupon_code else check_promotion(after_coupon_total)
    server_total = max(0, after_coupon_total - promotion_discount)

    if server_total != int(client_total):
        return False, f"Сума не збігається (клієнт {client_total}, сервер {server_total})"

    return True, {
        "items": normalized,
        "subtotal": subtotal,
        "coupon_discount": discount,
        "promotion_discount": promotion_discount,
        "total_price": server_total,
        "price_pending": 1 if any(i.get("is_contract_price") for i in normalized) else 0,
    }
