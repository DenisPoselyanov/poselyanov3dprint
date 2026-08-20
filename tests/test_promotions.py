"""Тести акцій, керованих з адмін-панелі."""

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
def promo_db(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")

    import config

    importlib.reload(config)

    from services.db_utils import init_db

    init_db()

    import services.promotions as promotions

    importlib.reload(promotions)
    promotions._invalidate_cache()

    yield promotions

    promotions._invalidate_cache()
    try:
        os.unlink(db_path)
    except OSError:
        pass


def test_defaults_are_seeded(promo_db):
    ids = {p["id"] for p in promo_db.list_promotions()}
    assert ids == {"gift_3_toys", "order_10_percent"}


def test_legacy_toy_category_migrates_to_key(promo_db):
    """Існуючі БД, засіяні до зміни акції, мають самі перейти з 'toy' на 'key'."""
    from db_core import db_connect

    conn = db_connect()
    conn.execute("UPDATE promotions SET category = 'toy' WHERE id = 'gift_3_toys'")
    conn.commit()
    conn.close()

    conn = db_connect()
    promo_db.init_promotions_table(conn)
    conn.commit()
    conn.close()
    rule = promo_db.get_gift_rule()
    assert rule["category"] == "key"


def test_legacy_toy_migration_skips_manually_edited_row(promo_db):
    """Якщо адмін уже сам переналаштував категорію — міграція її не чіпає."""
    assert promo_db.update_promotion("gift_3_toys", {"category": "gadget"})["ok"]

    from db_core import db_connect

    conn = db_connect()
    promo_db.init_promotions_table(conn)
    conn.commit()
    conn.close()
    rule = promo_db.get_gift_rule()
    assert rule["category"] == "gadget"


def test_default_discount_matches_legacy_behaviour(promo_db):
    assert promo_db.compute_order_discount(499) == 0
    assert promo_db.compute_order_discount(500) == 50
    # Кінцева сума округлюється вгору до 10 ₴
    assert promo_db.compute_order_discount(510) == 50


def test_disabled_promotion_gives_no_discount(promo_db):
    assert promo_db.set_promotion_active("order_10_percent", False)["ok"]
    assert promo_db.compute_order_discount(1000) == 0


def test_global_kill_switch(promo_db, monkeypatch):
    import config

    monkeypatch.setattr(config, "PROMOTION_ENABLED", False)
    promo_db._invalidate_cache()
    assert promo_db.compute_order_discount(1000) == 0
    assert promo_db.get_gift_rule() is None


def test_best_discount_wins(promo_db):
    result = promo_db.create_promotion({
        "id": "big_cart_15",
        "type": "order_percent",
        "title": "−15% від 1000 ₴",
        "value": 15,
        "min_order": 1000,
    })
    assert result["ok"], result
    # 1000 ₴: 10% = 100, 15% = 150 → перемагає найвигідніша для клієнта
    assert promo_db.compute_order_discount(1000) == 150
    # 800 ₴ ще не дотягує до порогу другої акції
    assert promo_db.compute_order_discount(800) == 80


def test_fixed_discount(promo_db):
    assert promo_db.set_promotion_active("order_10_percent", False)["ok"]
    assert promo_db.create_promotion({
        "id": "first_order_50",
        "type": "order_fixed",
        "title": "−50 ₴",
        "value": 50,
        "min_order": 300,
    })["ok"]
    assert promo_db.compute_order_discount(299) == 0
    assert promo_db.compute_order_discount(300) == 50


def test_gift_rule_from_admin(promo_db):
    rule = promo_db.get_gift_rule()
    assert rule["category"] == "key"
    assert rule["min_items"] == 3
    assert rule["max_gift_price"] == 100

    assert promo_db.update_promotion("gift_3_toys", {"min_items": 2, "max_gift_price": 150})["ok"]
    rule = promo_db.get_gift_rule()
    assert rule["min_items"] == 2
    assert rule["max_gift_price"] == 150

    assert promo_db.set_promotion_active("gift_3_toys", False)["ok"]
    assert promo_db.get_gift_rule() is None


def test_date_window(promo_db):
    assert promo_db.update_promotion(
        "order_10_percent", {"starts_at": "2099-01-01", "ends_at": "2099-12-31"}
    )["ok"]
    assert promo_db.compute_order_discount(1000) == 0

    assert promo_db.update_promotion(
        "order_10_percent", {"starts_at": "2000-01-01", "ends_at": "2099-12-31"}
    )["ok"]
    assert promo_db.compute_order_discount(1000) == 100


def test_validation_errors(promo_db):
    assert not promo_db.create_promotion({"id": "Погано!", "type": "order_percent", "title": "x", "value": 5})["ok"]
    assert not promo_db.create_promotion({"id": "ok_id", "type": "невідомо", "title": "x"})["ok"]
    assert not promo_db.create_promotion({"id": "ok_id", "type": "order_percent", "title": "", "value": 5})["ok"]
    assert not promo_db.create_promotion({"id": "ok_id", "type": "order_percent", "title": "x", "value": 0})["ok"]
    assert not promo_db.create_promotion({"id": "ok_id", "type": "order_percent", "title": "x", "value": 101})["ok"]
    assert not promo_db.create_promotion({"id": "ok_id", "type": "gift_threshold", "title": "x", "min_items": 0})["ok"]
    assert not promo_db.create_promotion({
        "id": "ok_id", "type": "order_percent", "title": "x", "value": 5,
        "starts_at": "2099-12-31", "ends_at": "2099-01-01",
    })["ok"]


def test_duplicate_id_rejected(promo_db):
    assert not promo_db.create_promotion({
        "id": "order_10_percent", "type": "order_percent", "title": "x", "value": 5,
    })["ok"]


def test_delete_promotion(promo_db):
    assert promo_db.delete_promotion("order_10_percent")["ok"]
    assert promo_db.get_promotion("order_10_percent") is None
    assert promo_db.compute_order_discount(1000) == 0
    assert not promo_db.delete_promotion("order_10_percent")["ok"]


def test_public_payload(promo_db):
    payload = promo_db.public_promotions()
    assert [p["id"] for p in payload["promotions"]] == ["gift_3_toys", "order_10_percent"]
    assert payload["gift_rule"] == {"category": "key", "min_items": 3, "max_gift_price": 100}


def test_info_promotion_has_no_discount(promo_db):
    assert promo_db.delete_promotion("order_10_percent")["ok"]
    assert promo_db.create_promotion({
        "id": "free_delivery_700",
        "type": "info",
        "title": "Безкоштовна доставка",
        "min_order": 700,
    })["ok"]
    assert promo_db.compute_order_discount(1000) == 0
    assert len(promo_db.public_promotions()["promotions"]) == 2


def test_check_promotion_uses_admin_settings(promo_db):
    import services.coupons as coupons

    importlib.reload(coupons)
    assert coupons.check_promotion(500) == 50
    assert promo_db.set_promotion_active("order_10_percent", False)["ok"]
    assert coupons.check_promotion(500) == 0
