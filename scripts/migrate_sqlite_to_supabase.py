import argparse
import sqlite3
from typing import Sequence

import psycopg


TABLES_IN_ORDER = [
    "users",
    "orders",
    "order_items",
    "coupons",
    "coupon_uses",
    "filament_colors",
]


def rows_from_sqlite(sqlite_path: str, table_name: str):
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def truncate_tables(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE coupon_uses, order_items, orders, coupons, filament_colors, users RESTART IDENTITY CASCADE"
        )


def insert_rows(pg_conn, table_name: str, rows: Sequence[dict]):
    if not rows:
        return 0

    columns = list(rows[0].keys())
    placeholders = ", ".join(["%s"] * len(columns))
    col_list = ", ".join(columns)
    query = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})"

    values = [tuple(row[col] for col in columns) for row in rows]
    with pg_conn.cursor() as cur:
        cur.executemany(query, values)
    return len(rows)


def sync_sequences(pg_conn):
    # Вирівнюємо послідовності для таблиць з BIGSERIAL після імпорту існуючих id.
    with pg_conn.cursor() as cur:
        cur.execute("SELECT setval(pg_get_serial_sequence('orders', 'id'), COALESCE(MAX(id), 1), TRUE) FROM orders")
        cur.execute("SELECT setval(pg_get_serial_sequence('order_items', 'id'), COALESCE(MAX(id), 1), TRUE) FROM order_items")
        cur.execute("SELECT setval(pg_get_serial_sequence('coupon_uses', 'id'), COALESCE(MAX(id), 1), TRUE) FROM coupon_uses")
        cur.execute("SELECT setval(pg_get_serial_sequence('products', 'id'), COALESCE(MAX(id), 1), TRUE) FROM products")


def main():
    parser = argparse.ArgumentParser(description="Migrate all data from SQLite users.db to Supabase Postgres.")
    parser.add_argument("--sqlite-path", required=True, help="Path to source sqlite DB file (users.db)")
    parser.add_argument("--database-url", required=True, help="Supabase Postgres connection string")
    parser.add_argument(
        "--truncate-first",
        action="store_true",
        help="Truncate target tables before import (recommended for one-shot migration)",
    )
    args = parser.parse_args()

    with psycopg.connect(args.database_url) as pg_conn:
        if args.truncate_first:
            truncate_tables(pg_conn)

        totals = {}
        for table in TABLES_IN_ORDER:
            rows = rows_from_sqlite(args.sqlite_path, table)
            totals[table] = insert_rows(pg_conn, table, rows)

        sync_sequences(pg_conn)
        pg_conn.commit()

    print("Migration completed.")
    for table in TABLES_IN_ORDER:
        print(f"{table}: {totals.get(table, 0)} rows")


if __name__ == "__main__":
    main()
