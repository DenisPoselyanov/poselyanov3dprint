"""Admin statistics queries."""

from __future__ import annotations

from db_core import db_connect


def get_stats():
    conn = db_connect()
    row = conn.execute("""
        SELECT
            (SELECT COUNT(*) FROM users) AS user_count,
            (SELECT COUNT(*) FROM orders) AS order_count,
            (SELECT COUNT(*) FROM orders WHERE status = 'confirmed') AS order_confirmed,
            (SELECT COUNT(*) FROM orders WHERE status = 'draft') AS order_draft,
            (SELECT COUNT(*) FROM orders WHERE status = 'cancelled') AS order_cancelled,
            (SELECT COALESCE(SUM(total_price), 0) FROM orders WHERE status = 'confirmed') AS earned,
            (SELECT COALESCE(SUM(discount_amount), 0) FROM orders WHERE status = 'confirmed') AS total_discount
    """).fetchone()
    user_count, order_count, order_confirmed, order_draft, order_cancelled, earned, total_discount = row
    recent = conn.execute(
        "SELECT name, username FROM users ORDER BY joined_at DESC LIMIT 10"
    ).fetchall()
    top_products = conn.execute("""
        SELECT oi.product_name, SUM(oi.quantity) as cnt
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        WHERE o.status = 'confirmed'
        GROUP BY oi.product_name
        ORDER BY cnt DESC
        LIMIT 5
    """).fetchall()
    coupon_stats = conn.execute("""
        SELECT c.code, c.uses_count,
               COALESCE(SUM(o.discount_amount), 0) as total_discount
        FROM coupons c
        LEFT JOIN orders o ON o.coupon_code = c.code AND o.status = 'confirmed'
        GROUP BY c.code
        ORDER BY c.uses_count DESC
        LIMIT 3
    """).fetchall()
    conn.close()
    return (
        user_count,
        order_count,
        order_confirmed,
        order_draft,
        order_cancelled,
        earned,
        recent,
        top_products,
        coupon_stats,
        total_discount,
    )
