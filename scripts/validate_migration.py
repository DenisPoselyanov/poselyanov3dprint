import argparse
import sqlite3

import psycopg


TABLES = ["users", "orders", "order_items", "coupons", "coupon_uses", "filament_colors"]


def sqlite_count(sqlite_path: str, table: str) -> int:
    conn = sqlite3.connect(sqlite_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def postgres_count(pg_conn, table: str) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description="Validate SQLite -> Supabase migration counts.")
    parser.add_argument("--sqlite-path", required=True)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()

    has_mismatch = False
    with psycopg.connect(args.database_url) as pg_conn:
        for table in TABLES:
            src = sqlite_count(args.sqlite_path, table)
            dst = postgres_count(pg_conn, table)
            marker = "OK" if src == dst else "MISMATCH"
            if src != dst:
                has_mismatch = True
            print(f"{table}: sqlite={src}, postgres={dst} [{marker}]")

    if has_mismatch:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
