"""Shared database helpers."""

from __future__ import annotations

from datetime import datetime

from catalog_store import init_catalog_tables, load_filaments_file, sync_filament_colors_table
from db_core import db_connect, is_postgres as _is_postgres

import config


def row_to_dict(row) -> dict:
    """Convert sqlite3.Row / psycopg dict-row to a plain JSON-serializable dict."""
    if isinstance(row, dict):
        d = row
    else:
        try:
            d = dict(row)
        except Exception:
            d = {k: row[k] for k in row.keys()}
    result = {}
    for k, v in d.items():
        if isinstance(v, datetime):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result


def init_db() -> None:
    conn = db_connect()
    if _is_postgres():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                name TEXT,
                username TEXT,
                blocked INTEGER DEFAULT 0,
                joined_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                first_name TEXT,
                total_price INTEGER,
                comment TEXT,
                gift_product_name TEXT,
                status TEXT DEFAULT 'new',
                coupon_code TEXT,
                discount_amount INTEGER DEFAULT 0,
                ordered_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id BIGSERIAL PRIMARY KEY,
                order_id BIGINT,
                product_id BIGINT,
                product_name TEXT,
                price INTEGER,
                quantity INTEGER DEFAULT 1,
                filament TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                code TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                value INTEGER NOT NULL,
                min_order INTEGER DEFAULT 0,
                uses_max INTEGER DEFAULT 0,
                uses_count INTEGER DEFAULT 0,
                one_per_user INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                expires_at TIMESTAMPTZ,
                personal_user_id BIGINT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coupon_uses (
                id BIGSERIAL PRIMARY KEY,
                code TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                order_id BIGINT,
                used_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS filament_colors (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                hex TEXT,
                available INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_idempotency (
                user_id BIGINT NOT NULL,
                idempotency_key TEXT NOT NULL,
                order_id BIGINT NOT NULL,
                is_new INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (user_id, idempotency_key)
            )
        """)
        conn.execute("ALTER TABLE coupons ADD COLUMN IF NOT EXISTS personal_user_id BIGINT")
        conn.execute("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS filament TEXT DEFAULT ''")
        conn.execute("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS is_contract_price INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS comment TEXT DEFAULT ''")
        conn.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS price_pending INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS channel_message_id BIGINT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_coupon_uses_user_id ON coupon_uses(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_blocked ON users(blocked)")
        init_catalog_tables(conn)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id        INTEGER PRIMARY KEY,
                name      TEXT,
                username  TEXT,
                blocked   INTEGER DEFAULT 0,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER,
                username          TEXT,
                first_name        TEXT,
                total_price       INTEGER,
                comment           TEXT,
                gift_product_name TEXT,
                status            TEXT DEFAULT 'new',
                coupon_code       TEXT,
                discount_amount   INTEGER DEFAULT 0,
                ordered_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id     INTEGER,
                product_id   INTEGER,
                product_name TEXT,
                price        INTEGER,
                quantity     INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                code         TEXT PRIMARY KEY,
                type         TEXT NOT NULL,
                value        INTEGER NOT NULL,
                min_order    INTEGER DEFAULT 0,
                uses_max     INTEGER DEFAULT 0,
                uses_count   INTEGER DEFAULT 0,
                one_per_user INTEGER DEFAULT 0,
                active       INTEGER DEFAULT 1,
                expires_at   TIMESTAMP,
                personal_user_id INTEGER
            )
        """)
        c_cols = [row[1] for row in conn.execute("PRAGMA table_info(coupons)").fetchall()]
        if "personal_user_id" not in c_cols:
            conn.execute("ALTER TABLE coupons ADD COLUMN personal_user_id INTEGER")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coupon_uses (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                code      TEXT NOT NULL,
                user_id   INTEGER NOT NULL,
                order_id  INTEGER,
                used_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS filament_colors (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                hex        TEXT,
                available  INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_idempotency (
                user_id          INTEGER NOT NULL,
                idempotency_key  TEXT NOT NULL,
                order_id         INTEGER NOT NULL,
                is_new           INTEGER NOT NULL DEFAULT 1,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (user_id, idempotency_key)
            )
        """)
        oi_cols = [row[1] for row in conn.execute("PRAGMA table_info(order_items)").fetchall()]
        if "filament" not in oi_cols:
            conn.execute("ALTER TABLE order_items ADD COLUMN filament TEXT DEFAULT ''")
        if "is_contract_price" not in oi_cols:
            conn.execute("ALTER TABLE order_items ADD COLUMN is_contract_price INTEGER DEFAULT 0")
        if "comment" not in oi_cols:
            conn.execute("ALTER TABLE order_items ADD COLUMN comment TEXT DEFAULT ''")
        o_cols = [row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()]
        if "price_pending" not in o_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN price_pending INTEGER DEFAULT 0")
        if "channel_message_id" not in o_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN channel_message_id INTEGER")

    for r in load_filaments_file(config.FILAMENTS_FILE):
        sync_filament_colors_table(conn, r)

    conn.commit()
    conn.close()
