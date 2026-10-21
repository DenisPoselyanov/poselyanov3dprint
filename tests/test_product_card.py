"""Картка товару (редизайн, етап 2): опис і примітка, відео, персоналізація, відгуки, «продано», «З цим купують»."""

import importlib
import json
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

from catalog_store import _row_to_product, clean_text, clean_video_url, normalize_custom_fields  # noqa: E402


# ─── Персоналізація ─────────────────────────────────────────

def test_custom_fields_string_stays_backward_compatible():
    assert normalize_custom_fields("  Номер автомобіля ") == "Номер автомобіля"
    assert normalize_custom_fields("") == ""
    assert normalize_custom_fields(None) == ""
    # Лише підпис в обʼєкті — зберігаємо рядком, як раніше
    assert normalize_custom_fields({"label": "Імʼя"}) == "Імʼя"


def test_custom_fields_object_is_cleaned():
    value = normalize_custom_fields({
        "label": "Імʼя на брелоку", "max": "12", "hint": "  Кирилиця чи латиниця ",
        "placeholder": "Наприклад, Софія", "upper": "on", "junk": "x",
    })
    assert value == {
        "label": "Імʼя на брелоку", "max": 12, "hint": "Кирилиця чи латиниця",
        "placeholder": "Наприклад, Софія", "upper": True,
    }
    assert normalize_custom_fields({"label": "Номер", "max": 9999})["max"] == 200
    assert normalize_custom_fields({"label": "Номер", "max": "abc", "hint": "h"}) == {"label": "Номер", "hint": "h"}
    assert normalize_custom_fields({"label": "", "max": 10}) == ""
    # JSON-рядок з адмінки теж розбирається
    assert normalize_custom_fields('{"label": "Номер", "upper": true}') == {"label": "Номер", "upper": True}


def test_row_to_product_reads_json_custom_fields_and_texts():
    product = _row_to_product({
        "id": 5, "name": "Брелок", "category_id": "custom",
        "custom_fields": json.dumps({"label": "Імʼя", "max": 10}, ensure_ascii=False),
        "description": "Перший абзац\n\nДругий", "note": " Відтінок може відрізнятись ", "video": "",
    })
    assert product["custom_fields"] == {"label": "Імʼя", "max": 10}
    assert product["description"] == "Перший абзац\n\nДругий"
    assert product["note"] == "Відтінок може відрізнятись"
    assert "video" not in product


def test_clean_text_and_video_url():
    assert clean_text("a\r\nb\r\n\r\nc  ", 100) == "a\nb\n\nc"
    assert clean_text("x" * 50, 10) == "x" * 10
    assert clean_video_url("") == ""
    assert clean_video_url(" https://res.cloudinary.com/v.mp4 ") == "https://res.cloudinary.com/v.mp4"
    for bad in ("http://x/v.mp4", "javascript:alert(1)", 'https://x/"onerror=1'):
        with pytest.raises(ValueError):
            clean_video_url(bad)


# ─── Збереження в JSON-каталозі ─────────────────────────────

@pytest.fixture
def json_catalog(monkeypatch, tmp_path):
    (tmp_path / "products.json").write_text("[]", encoding="utf-8")
    (tmp_path / "custom_products.json").write_text("[]", encoding="utf-8")
    (tmp_path / "categories.json").write_text(json.dumps([
        {"id": "key", "name": "Брелки", "emoji": "🔑", "order": 1, "active": True},
        {"id": "custom", "name": "Індивідуальне", "emoji": "✏️", "order": 2, "active": True},
    ], ensure_ascii=False), encoding="utf-8")
    import config
    import catalog_store

    monkeypatch.setattr(config, "CATALOG_BACKEND", "json")
    monkeypatch.setattr(config, "PRODUCTS_FILE", str(tmp_path / "products.json"))
    monkeypatch.setattr(config, "CUSTOM_PRODUCTS_FILE", str(tmp_path / "custom_products.json"))
    monkeypatch.setattr(config, "CATEGORIES_FILE", str(tmp_path / "categories.json"))
    monkeypatch.setattr(catalog_store, "use_catalog_db", lambda: False)
    catalog_store.reload_categories_cache(force=True)
    catalog_store.reload_products_cache(force=True)
    yield catalog_store
    catalog_store.PRODUCTS_CACHE.clear()
    catalog_store.CUSTOM_PRODUCTS_CACHE.clear()
    catalog_store.CATEGORIES_CACHE.clear()


def test_product_description_note_video_roundtrip(json_catalog):
    cs = json_catalog
    res = cs.add_product({
        "cat": "key", "name": "Брелок", "price": 80, "mat": "PLA",
        "description": "Опис\r\nдругий рядок", "note": "Примітка", "video": "https://res.cloudinary.com/x/v.mp4",
    })
    assert res["ok"], res
    p = cs.get_product_by_id(res["id"])
    assert (p["description"], p["note"], p["video"]) == ("Опис\nдругий рядок", "Примітка", "https://res.cloudinary.com/x/v.mp4")

    # Порожнє значення прибирає поле; не передане — лишається як було
    assert cs.update_product(res["id"], {"note": "  ", "video": ""})["ok"]
    p = cs.get_product_by_id(res["id"])
    assert "note" not in p and "video" not in p and p["description"] == "Опис\nдругий рядок"

    # Невалідне відео — помилка без змін у товарі
    bad = cs.update_product(res["id"], {"name": "Інша назва", "video": "http://x/v.mp4"})
    assert not bad["ok"]
    assert cs.get_product_by_id(res["id"])["name"] == "Брелок"


def test_custom_product_saves_personalization_object(json_catalog):
    cs = json_catalog
    res = cs.add_product({
        "cat": "custom", "name": "Брелок з іменем", "price": 150, "mat": "PLA",
        "custom_fields": {"label": "Імʼя на брелоку", "max": 12, "upper": False},
    })
    assert res["ok"], res
    assert cs.get_product_by_id(res["id"])["custom_fields"] == {"label": "Імʼя на брелоку", "max": 12}


# ─── Відгуки, рейтинг, «продано», «З цим купують» ───────────

@pytest.fixture
def card_db(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")

    import config

    importlib.reload(config)
    from services.db_utils import init_db

    init_db()
    import services.hits as hits
    import services.reviews as reviews
    import services.settings as settings

    importlib.reload(hits)
    importlib.reload(settings)
    importlib.reload(reviews)
    yield reviews, hits, settings
    try:
        os.unlink(db_path)
    except OSError:
        pass


def _connect():
    from db_core import db_connect

    return db_connect()


def _seed_orders(conn):
    conn.executemany(
        "INSERT INTO orders (id, user_id, total_price, status) VALUES (?, 1, 100, ?)",
        [(1, "confirmed"), (2, "delivered"), (3, "new"), (4, "cancelled"), (5, "shipped")],
    )
    conn.executemany(
        "INSERT INTO order_items (order_id, product_id, product_name, price, quantity) VALUES (?, ?, 'x', 50, ?)",
        [(1, 7, 2), (1, 8, 1), (2, 7, 3), (2, 8, 1), (2, 9, 1), (3, 7, 40), (3, 10, 1), (4, 7, 40), (5, 7, 1), (5, 0, 1)],
    )


def test_stats_show_rating_only_for_approved_and_sold_from_threshold(card_db):
    reviews, _, settings = card_db
    conn = _connect()
    _seed_orders(conn)
    conn.executemany(
        "INSERT INTO reviews (product_id, order_id, user_id, author_name, rating, text, status) VALUES (?, ?, 1, 'A', ?, 't', ?)",
        [(7, 1, 5, "approved"), (7, 2, 4, "approved"), (7, 5, 1, "pending"), (8, 2, 5, "hidden")],
    )
    conn.commit()
    conn.close()

    products = [{"id": 7, "name": "A"}, {"id": 8, "name": "B"}, {"id": 11, "name": "C"}]
    out = {p["id"]: p for p in reviews.with_stats(products)}
    # 7: продано 2 + 3 + 1 = 6 (нові й скасовані не рахуються), 2 схвалені відгуки
    assert out[7] == {"id": 7, "name": "A", "rating_avg": 4.5, "rating_count": 2, "sold_count": 6}
    # 8: продано 2 — нижче порогу 5; відгук прихований — рейтингу немає
    assert out[8] == {"id": 8, "name": "B"}
    assert out[11] == {"id": 11, "name": "C"}
    assert "rating_avg" not in products[0]  # оригінали з кешу каталогу не змінюються

    assert settings.update_admin_settings({"sold_count_min": 2})["ok"]
    reviews.invalidate_stats_cache()
    assert reviews.with_stats([{"id": 8}])[0]["sold_count"] == 2
    assert not settings.update_admin_settings({"sold_count_min": "багато"})["ok"]
    assert not settings.update_admin_settings({"sold_count_min": -1})["ok"]


def test_product_reviews_public_shape(card_db):
    reviews, *_ = card_db
    conn = _connect()
    conn.executemany(
        "INSERT INTO reviews (product_id, order_id, user_id, author_name, rating, text, photos, status, admin_reply, created_at) "
        "VALUES (7, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 11, "Олена Петренко", 5, "Супер", json.dumps(["https://img/1.jpg", "http://bad", 5]), "approved", "Дякую!", "2026-09-01 10:00:00"),
            (2, 12, "", 4, "Добре", "[]", "approved", "", "2026-09-10 10:00:00"),
            (3, 13, "Прихований", 1, "Погано", "[]", "hidden", "", "2026-09-11 10:00:00"),
        ],
    )
    conn.commit()
    conn.close()

    data = reviews.product_reviews(7)
    assert data["total"] == 2
    first, second = data["reviews"]
    assert (first["author"], first["rating"], first["text"]) == ("Покупець", 4, "Добре")  # нові першими
    assert second["author"] == "Олена"  # лише імʼя, без прізвища
    assert second["photos"] == ["https://img/1.jpg"]
    assert second["reply"] == "Дякую!"
    assert "user_id" not in second
    assert reviews.product_reviews(7, limit=1, offset=1)["reviews"][0]["author"] == "Олена"


def test_reviews_one_per_order_and_product(card_db):
    conn = _connect()
    conn.execute("INSERT INTO reviews (product_id, order_id, rating) VALUES (7, 1, 5)")
    with pytest.raises(Exception):
        conn.execute("INSERT INTO reviews (product_id, order_id, rating) VALUES (7, 1, 4)")
    conn.close()


def test_bought_together_uses_accepted_orders(card_db):
    _, hits, _ = card_db
    conn = _connect()
    _seed_orders(conn)
    conn.commit()
    conn.close()
    # 8 — у двох прийнятих замовленнях разом із 7, 9 — в одному; 10 лише в новому, 0 — службовий рядок
    assert hits.bought_together(7) == [8, 9]
    assert hits.bought_together(9) == [7, 8]
    assert hits.bought_together(10) == []
