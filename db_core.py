"""Database connection helpers (SQLite / Postgres)."""

from __future__ import annotations

import sqlite3

import config

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:
    psycopg = None
    dict_row = None


def is_postgres() -> bool:
    return config.DB_BACKEND == "postgres"


def sql(query: str) -> str:
    return query if not is_postgres() else query.replace("?", "%s")


def db_connect(dict_rows: bool = False):
    if is_postgres():
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL/SUPABASE_DB_URL is required for DB_BACKEND=postgres")
        if psycopg is None:
            raise RuntimeError("psycopg is not installed. Add `psycopg[binary]` to requirements.")
        if dict_rows:
            return psycopg.connect(config.DATABASE_URL, row_factory=dict_row)
        return psycopg.connect(config.DATABASE_URL)

    conn = sqlite3.connect(config.DB_FILE)
    if dict_rows:
        conn.row_factory = sqlite3.Row
    return conn


async def run_db(func, *args, **kwargs):
    import asyncio

    return await asyncio.to_thread(func, *args, **kwargs)
