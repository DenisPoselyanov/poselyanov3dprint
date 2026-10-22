"""Пул з'єднань Postgres і кеш каталогу з БД."""

import gc

import catalog_store
import db_core


class FakeConn:
    closed = False

    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def execute(self, *_a, **_k):
        return "result"

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class FakePool:
    def __init__(self):
        self.returned = []

    def putconn(self, conn):
        self.returned.append(conn)


def test_close_returns_connection_to_pool_once():
    pool, conn = FakePool(), FakeConn()
    wrapped = db_core.PooledConnection(conn, pool)
    assert wrapped.execute("SELECT 1") == "result"
    wrapped.close()
    wrapped.close()
    assert pool.returned == [conn]
    assert wrapped.closed


def test_forgotten_connection_returns_to_pool_on_gc():
    pool, conn = FakePool(), FakeConn()
    db_core.PooledConnection(conn, pool)
    gc.collect()
    assert pool.returned == [conn]


def test_context_manager_commits_or_rolls_back():
    pool = FakePool()
    ok = FakeConn()
    with db_core.PooledConnection(ok, pool):
        pass
    assert ok.committed and pool.returned == [ok]

    bad = FakeConn()
    try:
        with db_core.PooledConnection(bad, pool):
            raise ValueError
    except ValueError:
        pass
    assert bad.rolled_back and pool.returned[-1] is bad


def test_db_catalog_is_cached_until_forced(monkeypatch):
    calls = []

    class Rows:
        def fetchall(self):
            return []

    class Conn:
        def execute(self, *_a):
            calls.append(1)
            return Rows()

        def close(self):
            pass

    monkeypatch.setattr(catalog_store, "use_catalog_db", lambda: True)
    monkeypatch.setattr(catalog_store, "db_connect", lambda **_k: Conn())
    catalog_store.invalidate_catalog_cache()
    try:
        catalog_store.reload_products_cache()
        catalog_store.reload_products_cache()
        assert len(calls) == 1
        catalog_store.reload_products_cache(force=True)
        assert len(calls) == 2
        # зміна категорії скидає кеш товарів
        catalog_store.reload_categories_cache(force=True)
        catalog_store.reload_products_cache()
        assert len(calls) == 4
    finally:
        catalog_store.invalidate_catalog_cache()
