"""Вітрина редизайну (етап 1): добірки, хіти продажів, події пошуку, дата додавання товару."""

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")


@pytest.fixture
def showcase_db(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")

    import config

    importlib.reload(config)

    from services.db_utils import init_db

    init_db()

    import services.collections as collections
    import services.events as events
    import services.hits as hits

    for mod in (collections, events, hits):
        importlib.reload(mod)

    yield collections, events, hits, db_path

    try:
        os.unlink(db_path)
    except OSError:
        pass


def _connect():
    from db_core import db_connect

    return db_connect()


# ─── Добірки ────────────────────────────────────────────────

def test_default_collections_seeded_once(showcase_db):
    collections, *_ = showcase_db
    items = collections.public_collections()
    assert [c["id"] for c in items] == ["new", "u100", "glow", "gift", "sale", "hits", "top"]
    assert all(c["mode"] == "auto" and c["rule"]["type"] in collections.RULE_TYPES for c in items)
    gift = next(c for c in items if c["id"] == "gift")
    assert gift["rule"] == {"type": "price_max", "max_price": 150, "exclude_custom": True}

    # Повторна ініціалізація не дублює сід і не затирає ручні зміни.
    conn = _connect()
    conn.execute("UPDATE collections SET name = 'Мої новинки' WHERE id = 'new'")
    conn.commit()
    collections.init_collections_table(conn)
    conn.commit()
    conn.close()
    items = collections.public_collections()
    assert len(items) == 7
    assert items[0]["name"] == "Мої новинки"


def test_manual_collection_and_hidden_ones(showcase_db):
    collections, *_ = showcase_db
    conn = _connect()
    conn.execute(
        "INSERT INTO collections (id, name, icon, color, sort_order, active, mode, rule) "
        "VALUES ('pick', 'Вибір Дениса', 'heart', 'nope', 1, 1, 'manual', '')"
    )
    conn.executemany(
        "INSERT INTO collection_products (collection_id, product_id, sort_order) VALUES ('pick', ?, ?)",
        [(12, 2), (5, 1)],
    )
    conn.execute("UPDATE collections SET active = 0 WHERE id = 'top'")
    conn.execute("UPDATE collections SET rule = 'broken' WHERE id = 'glow'")
    conn.commit()
    conn.close()
    collections._invalidate_cache()

    items = collections.public_collections()
    ids = [c["id"] for c in items]
    assert ids[0] == "pick"
    assert "top" not in ids  # вимкнена
    assert "glow" not in ids  # зламане правило не валить вітрину
    pick = items[0]
    assert pick["product_ids"] == [5, 12]
    assert pick["color"] == "blue"  # невідомий колір → дефолтний
    assert "rule" not in pick


# ─── Хіти продажів ──────────────────────────────────────────

def test_hits_count_only_accepted_orders(showcase_db):
    _, _, hits, _ = showcase_db
    conn = _connect()
    conn.executemany(
        "INSERT INTO orders (id, user_id, total_price, status) VALUES (?, 1, 100, ?)",
        [(1, "confirmed"), (2, "delivered"), (3, "new"), (4, "cancelled"), (5, "draft")],
    )
    conn.executemany(
        "INSERT INTO order_items (order_id, product_id, product_name, price, quantity) VALUES (?, ?, 'x', 50, ?)",
        [(1, 7, 1), (1, 8, 3), (2, 7, 4), (3, 9, 50), (4, 9, 50), (5, 9, 50)],
    )
    # Старе замовлення поза вікном у 90 днів не рахується.
    conn.execute(
        "INSERT INTO orders (id, user_id, total_price, status, ordered_at) "
        "VALUES (6, 1, 100, 'confirmed', datetime('now', '-200 days'))"
    )
    conn.execute(
        "INSERT INTO order_items (order_id, product_id, product_name, price, quantity) VALUES (6, 8, 'x', 50, 99)"
    )
    conn.commit()
    conn.close()

    assert hits.top_selling_products(days=90, limit=10) == [
        {"product_id": 7, "sold": 5},
        {"product_id": 8, "sold": 3},
    ]
    assert hits.top_selling_products(days=90, limit=1) == [{"product_id": 7, "sold": 5}]


# ─── Пошук ──────────────────────────────────────────────────

def test_normalize_query():
    import services.events as events

    assert events.normalize_query("  Фіджет   СПІРАЛЬ ") == "фіджет спіраль"
    assert events.normalize_query("a") == ""
    assert events.normalize_query(None) == ""
    assert len(events.normalize_query("x" * 200)) == events.QUERY_MAX_LEN


def test_popular_searches_fall_back_to_static(showcase_db):
    _, events, _, _ = showcase_db
    assert events.popular_searches() == list(events.STATIC_POPULAR)


def test_popular_searches_count_users_once_per_day(showcase_db):
    _, events, _, _ = showcase_db
    for _ in range(5):
        assert events.record_search(1, "Брелок", 4)  # один користувач — одна «вага» на добу
    for uid in (2, 3):
        events.record_search(uid, "фіджет", 3)
    for uid in (4, 5, 6):
        events.record_search(uid, "дракон", 0)  # без результатів — не показуємо
    events.record_search(7, "підставка", 2)
    events.record_search(8, "куб", 1)
    assert not events.record_search(0, "брелок", 4)  # без користувача не пишемо
    assert not events.record_search(9, "x", 4)  # закороткий запит

    conn = _connect()
    assert conn.execute("SELECT COUNT(*) FROM events WHERE query = 'брелок'").fetchone()[0] == 1
    conn.close()

    events.invalidate_popular_cache()
    top = events.popular_searches(limit=5)
    assert top[0] == "фіджет"
    assert set(top[1:4]) == {"брелок", "підставка", "куб"}
    assert "дракон" not in top


def test_repeat_search_updates_results_count(showcase_db):
    _, events, _, _ = showcase_db
    events.record_search(1, "флексі", 0)
    events.record_search(1, "флексі", 2)
    events.invalidate_popular_cache()
    assert "флексі" in events.popular_searches()


# ─── Товари ─────────────────────────────────────────────────

def test_row_to_product_exposes_created_at():
    from datetime import datetime, timezone

    from catalog_store import _row_to_product

    dt = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    assert _row_to_product({"id": 1, "name": "X", "created_at": dt})["createdAt"] == dt.isoformat()
    assert "createdAt" not in _row_to_product({"id": 2, "name": "Y", "created_at": None})
