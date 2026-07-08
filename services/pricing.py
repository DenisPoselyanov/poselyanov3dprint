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
