"""Database connection helpers (SQLite / Postgres)."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading

import config

try:
    import psycopg
    from psycopg.rows import dict_row, tuple_row
except Exception:
    psycopg = None
    dict_row = None
    tuple_row = None

try:
    from psycopg_pool import ConnectionPool
except Exception:
    ConnectionPool = None

logger = logging.getLogger(__name__)

# Нове з'єднання з Supabase (TLS + авторизація) коштує ~0,4 с — тримаємо кілька відкритими.
PG_POOL_MIN = int(os.environ.get("PG_POOL_MIN", "1"))
PG_POOL_MAX = int(os.environ.get("PG_POOL_MAX", "8"))

_pool = None
_pool_url = None
_pool_lock = threading.Lock()


def is_postgres() -> bool:
    return config.DB_BACKEND == "postgres"


def sql(query: str) -> str:
    return query if not is_postgres() else query.replace("?", "%s")


def _get_pool():
    """Пул на поточний DATABASE_URL (тести й скрипти можуть його змінювати)."""
    global _pool, _pool_url
    if ConnectionPool is None:
        return None
    with _pool_lock:
        if _pool is None or _pool_url != config.DATABASE_URL:
            if _pool is not None:
                _pool.close()
            _pool = ConnectionPool(
                config.DATABASE_URL,
                min_size=PG_POOL_MIN,
                max_size=PG_POOL_MAX,
                max_idle=300,
                max_lifetime=1800,
                timeout=15,
                # Без серверних prepared statements — сумісно і з transaction-режимом пулера Supabase
                kwargs={"prepare_threshold": None},
                # Перевірка перед видачею: пулер Supabase рве давно неактивні з'єднання
                check=ConnectionPool.check_connection,
                name="poselyanov3dprint",
                open=True,
            )
            _pool_url = config.DATABASE_URL
        return _pool


def close_pool() -> None:
    global _pool, _pool_url
    with _pool_lock:
        if _pool is not None:
            _pool.close()
        _pool = None
        _pool_url = None


class PooledConnection:
    """Обгортка з'єднання з пулу: `close()` повертає його в пул, а не закриває.

    Код по всьому проєкту працює з `conn = db_connect(); …; conn.close()` — так він лишається без змін.
    Незакрите з'єднання (виняток до close) повернеться в пул, коли обгортку прибере збирач сміття.
    """

    __slots__ = ("_conn", "_pool")

    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                # Як і з psycopg.connect: незакомічені зміни відкочуються. Явний rollback не дає
                # пулу писати попередження «rolling back returned connection» на кожному читанні.
                info = getattr(conn, "info", None)
                if info is not None and info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                    conn.rollback()
                self._pool.putconn(conn)
            except Exception:
                logger.warning("Не вдалось повернути з'єднання в пул", exc_info=True)

    def __getattr__(self, name):
        conn = self._conn
        if conn is None:
            raise psycopg.OperationalError("the connection is closed")
        return getattr(conn, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._conn is not None:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    @property
    def closed(self) -> bool:
        return self._conn is None or self._conn.closed


def db_connect(dict_rows: bool = False):
    if is_postgres():
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL/SUPABASE_DB_URL is required for DB_BACKEND=postgres")
        if psycopg is None:
            raise RuntimeError("psycopg is not installed. Add `psycopg[binary]` to requirements.")
        pool = _get_pool()
        if pool is None:
            if dict_rows:
                return psycopg.connect(config.DATABASE_URL, row_factory=dict_row)
            return psycopg.connect(config.DATABASE_URL)
        conn = pool.getconn()
        conn.row_factory = dict_row if dict_rows else tuple_row
        return PooledConnection(conn, pool)

    conn = sqlite3.connect(config.DB_FILE)
    if dict_rows:
        conn.row_factory = sqlite3.Row
    return conn


async def run_db(func, *args, **kwargs):
    import asyncio

    return await asyncio.to_thread(func, *args, **kwargs)
