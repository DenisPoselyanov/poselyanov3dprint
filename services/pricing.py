"""Shared pricing helpers."""

from __future__ import annotations


ROUNDING_STEP = 10


def round_price_up(total: int, step: int = ROUNDING_STEP) -> int:
    total = max(0, int(total or 0))
    step = int(step or 0)
    if step <= 1 or total == 0:
        return total
    return ((total + step - 1) // step) * step


def actual_discount_after_rounding(subtotal: int, raw_discount: int, step: int = ROUNDING_STEP) -> int:
    subtotal = max(0, int(subtotal or 0))
    raw_discount = min(max(0, int(raw_discount or 0)), subtotal)
    if raw_discount <= 0:
        return 0
    rounded_total = round_price_up(subtotal - raw_discount, step)
    return max(0, subtotal - rounded_total)


def split_discounts(
    subtotal: int,
    raw_coupon: int,
    raw_promotion: int,
    *,
    stack: bool,
    step: int = ROUNDING_STEP,
) -> tuple[int, int]:
    """Розкласти знижки на (купон, акція) з єдиним округленням у кінці.

    Обидві знижки рахуються від початкової суми кошика (subtotal), а не
    послідовно — інакше акційний поріг «від 500 ₴» переставав спрацьовувати
    після купона. Якщо сумування вимкнене, купон, як і раніше, скасовує акцію.

    Округлення (ціна догори до кроку) з'їдає кілька гривень знижки — цей
    залишок віднімаємо від акції, щоб купон завжди показував свій номінал.
    """
    subtotal = max(0, int(subtotal or 0))
    raw_coupon = max(0, int(raw_coupon or 0))
    raw_promotion = max(0, int(raw_promotion or 0))

    if raw_coupon > 0 and not stack:
        raw_promotion = 0

    combined = min(subtotal, raw_coupon + raw_promotion)
    total = actual_discount_after_rounding(subtotal, combined, step)
    if total <= 0:
        return 0, 0

    coupon = min(raw_coupon, total)
    promotion = total - coupon
    return coupon, promotion
