"""Статистика товарів для таблиці в адмінці: перегляди, додавання в кошик, продажі.

Перегляди й кошик — події `product_view` / `add_to_cart` з `events` (раз на добу на користувача й товар,
тож це «людино-дні», а не сирі кліки). Продажі — сума `order_items.quantity` у замовленнях зі статусами
`confirmed…delivered` (ті самі, що в хітах). Усе — двома агрегованими запитами на весь каталог, без N+1.
"""

from __future__ import annotations

import logging

from db_core import db_connect, sql as _sql
from services.hits import SOLD_STATUSES

logger = logging.getLogger(__name__)


def product_admin_stats() -> dict[int, dict[str, int]]:
    """{product_id: {views, cart, sold}}; товари без подій у словнику відсутні."""
    stats: dict[int, dict[str, int]] = {}

    def slot(pid) -> dict[str, int]:
        return stats.setdefault(int(pid), {"views": 0, "cart": 0, "sold": 0})

    placeholders = ", ".join("?" for _ in SOLD_STATUSES)
    conn = db_connect()
    try:
        events = conn.execute(
            _sql("""
                SELECT product_id, type, COUNT(*)
                FROM events
                WHERE type IN ('product_view', 'add_to_cart') AND product_id IS NOT NULL
                GROUP BY product_id, type
            """)
        ).fetchall()
        sold = conn.execute(
            _sql(f"""
                SELECT oi.product_id, SUM(oi.quantity)
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                WHERE o.status IN ({placeholders}) AND oi.product_id IS NOT NULL AND oi.product_id > 0
                GROUP BY oi.product_id
            """),
            SOLD_STATUSES,
        ).fetchall()
    finally:
        conn.close()
    for pid, event_type, count in events:
        slot(pid)["views" if event_type == "product_view" else "cart"] = int(count or 0)
    for pid, qty in sold:
        slot(pid)["sold"] = int(qty or 0)
    return stats


def admin_products_with_stats(products: list[dict]) -> list[dict]:
    """Копії товарів з полями `views`, `cart_adds`, `sold` (нулі, якщо подій немає)."""
    try:
        stats = product_admin_stats()
    except Exception:
        logger.warning("product_stats: не вдалось порахувати статистику товарів", exc_info=True)
        stats = {}
    out = []
    for p in products:
        s = stats.get(int(p.get("id") or 0)) or {}
        out.append({**p, "views": s.get("views", 0), "cart_adds": s.get("cart", 0), "sold": s.get("sold", 0)})
    return out
