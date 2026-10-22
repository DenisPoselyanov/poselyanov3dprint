"""Відгуки покупців: подання, захист від зловживань, запит відгуку через 3 дні."""

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

GOOD_PHOTO = "https://res.cloudinary.com/mycloud/image/upload/v1/poselyanov3dprint/review_7_1700000000.jpg"


@pytest.fixture
def env(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")

    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "mycloud")

    import config

    importlib.reload(config)
    from services.db_utils import init_db

    init_db()

    import services.orders as orders
    import services.reviews as reviews
    import services.settings as settings

    importlib.reload(orders)
    importlib.reload(settings)
    importlib.reload(reviews)
    yield orders, reviews, settings
    try:
        os.unlink(db_path)
    except OSError:
        pass


def _delivered_order(orders, user_id=7, product_id=11):
    items = [{"product_id": product_id, "product_name": "Брелок", "price": 80, "quantity": 1}]
    order_id, _ = orders.save_order(user_id, "@u", "Тест", items, 80, "")
    orders.update_order_status(order_id, "delivered")
    return order_id


def test_pending_lists_only_delivered_unreviewed(env):
    orders, reviews, _ = env
    done = _delivered_order(orders)
    items = [{"product_id": 12, "product_name": "Інше", "price": 50, "quantity": 1}]
    orders.save_order(7, "@u", "Тест", items, 50, "")  # ще new
    assert reviews.pending_reviews(7) == [{"order_id": done, "product_id": 11, "product_name": "Брелок"}]
    assert reviews.pending_reviews(8) == []
    assert reviews.pending_reviews(0) == []


def test_submit_goes_to_moderation_and_blocks_duplicates(env):
    orders, reviews, _ = env
    oid = _delivered_order(orders)
    result = reviews.submit_review(
        7, "Іван Петренко", oid, 11, 5, "Супер",
        [
            GOOD_PHOTO,
            "https://evil.example/x.jpg",
            "https://res.cloudinary.com/other/image/upload/poselyanov3dprint/review_7_1.jpg",
            "https://res.cloudinary.com/mycloud/image/upload/v1/poselyanov3dprint/review_8_1.jpg",
            GOOD_PHOTO + "?x=1",
            "https://res.cloudinary.com@evil.example/mycloud/image/upload/v1/poselyanov3dprint/review_7_1.jpg",
        ],
    )
    assert result["status"] == "pending"
    review = reviews.get_review(result["id"])
    assert review["photos"] == [GOOD_PHOTO]
    assert reviews.product_reviews(11)["total"] == 0  # ще не схвалено
    assert reviews.pending_reviews(7) == []  # вже оцінено
    with pytest.raises(reviews.ReviewError):
        reviews.submit_review(7, "Іван", oid, 11, 4, "ще раз")


@pytest.mark.parametrize("rating", [0, 6, "x", None])
def test_submit_rejects_bad_rating(env, rating):
    orders, reviews, _ = env
    oid = _delivered_order(orders)
    with pytest.raises(reviews.ReviewError):
        reviews.submit_review(7, "І", oid, 11, rating)


def test_submit_rejects_foreign_or_undelivered_or_wrong_product(env):
    orders, reviews, _ = env
    oid = _delivered_order(orders)
    with pytest.raises(reviews.ReviewError):
        reviews.submit_review(8, "Чужий", oid, 11, 5)
    with pytest.raises(reviews.ReviewError):
        reviews.submit_review(7, "І", oid, 99, 5)
    items = [{"product_id": 13, "product_name": "Х", "price": 10, "quantity": 1}]
    fresh, _ = orders.save_order(7, "@u", "Тест", items, 10, "")
    with pytest.raises(reviews.ReviewError):
        reviews.submit_review(7, "І", fresh, 13, 5)


def test_auto_publish_setting_and_moderation(env):
    orders, reviews, settings = env
    oid = _delivered_order(orders)
    settings.set_setting(settings.REVIEWS_AUTO_PUBLISH_KEY, "1")
    result = reviews.submit_review(7, "Іван Петренко", oid, 11, 4, "Ок")
    assert result["status"] == "approved"
    shown = reviews.product_reviews(11)
    assert shown["total"] == 1 and shown["reviews"][0]["author"] == "Іван"
    assert reviews.set_review_status(result["id"], "hidden") is True
    assert reviews.product_reviews(11)["total"] == 0
    assert reviews.set_review_status(result["id"], "bogus") is False
    assert reviews.set_review_status(9999, "approved") is False


def test_review_request_due_after_three_days(env):
    orders, reviews, _ = env
    from db_core import db_connect

    oid = _delivered_order(orders)
    assert reviews.orders_due_review_request() == []  # щойно доставлено
    conn = db_connect()
    conn.execute("UPDATE orders SET delivered_at = datetime('now', '-4 days') WHERE id = ?", (oid,))
    conn.commit()
    conn.close()
    assert reviews.orders_due_review_request() == [{"order_id": oid, "user_id": 7}]
    reviews.mark_review_requested(oid)
    assert reviews.orders_due_review_request() == []


def test_review_request_skipped_when_everything_reviewed(env):
    orders, reviews, _ = env
    from db_core import db_connect

    oid = _delivered_order(orders)
    reviews.submit_review(7, "І", oid, 11, 5)
    conn = db_connect()
    conn.execute("UPDATE orders SET delivered_at = datetime('now', '-4 days') WHERE id = ?", (oid,))
    conn.commit()
    conn.close()
    assert reviews.orders_due_review_request() == []


def _age(order_id, **cols):
    from db_core import db_connect

    conn = db_connect()
    for col, days in cols.items():
        conn.execute(f"UPDATE orders SET {col} = datetime('now', '-{days} days') WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()


def test_review_request_due_after_one_day(env):
    orders, reviews, _ = env
    oid = _delivered_order(orders)
    _age(oid, delivered_at=0)
    assert reviews.orders_due_review_request(1) == []  # ще не добу
    _age(oid, delivered_at=2)
    assert reviews.orders_due_review_request() == [{"order_id": oid, "user_id": 7}]


def test_review_reminder_once_and_only_without_review(env):
    orders, reviews, _ = env
    oid = _delivered_order(orders)
    _age(oid, delivered_at=8, review_requested_at=6)
    assert reviews.orders_due_review_reminder() == [{"order_id": oid, "user_id": 7}]
    reviews.mark_review_reminded(oid)
    assert reviews.orders_due_review_reminder() == []

    other = _delivered_order(orders, product_id=12)
    _age(other, delivered_at=8, review_requested_at=6)
    reviews.submit_review(7, "І", other, 12, 5)
    assert reviews.orders_due_review_reminder() == []  # відгук уже є

    stale = _delivered_order(orders, product_id=13)
    _age(stale, delivered_at=60, review_requested_at=50)
    assert reviews.orders_due_review_reminder() == []  # надто давнє замовлення

    fresh = _delivered_order(orders, product_id=14)
    _age(fresh, delivered_at=3, review_requested_at=2)
    assert reviews.orders_due_review_reminder() == []  # запит був нещодавно


def test_review_bonus_percent_rules(env):
    _, reviews, _ = env
    assert reviews.review_bonus_percent({"text": "Супер", "photos": []}) == 0
    assert reviews.review_bonus_percent({"text": "Дуже гарна якість друку, дякую", "photos": []}) == 5
    assert reviews.review_bonus_percent({"text": "", "photos": ["u"]}) == 10


def test_grant_bonus_only_when_approved_once_per_order(env):
    orders, reviews, settings = env
    oid = _delivered_order(orders)
    result = reviews.submit_review(7, "Іван", oid, 11, 5, "Дуже гарна якість друку, дякую", [GOOD_PHOTO])
    assert reviews.grant_review_bonus(result["id"]) is None  # ще на модерації
    reviews.set_review_status(result["id"], "approved")
    bonus = reviews.grant_review_bonus(result["id"])
    assert bonus["percent"] == 10 and bonus["user_id"] == 7 and bonus["code"].startswith("P")
    from services.coupons import get_coupon

    coupon = get_coupon(bonus["code"])
    assert coupon["value"] == 10 and coupon.get("allowed_user_ids") == [7]
    assert reviews.grant_review_bonus(result["id"]) is None  # вдруге не видаємо


def test_no_bonus_for_rating_only_review(env):
    orders, reviews, settings = env
    settings.set_setting(settings.REVIEWS_AUTO_PUBLISH_KEY, "1")
    oid = _delivered_order(orders)
    result = reviews.submit_review(7, "Іван", oid, 11, 5, "ок")
    assert reviews.grant_review_bonus(result["id"]) is None


def test_bonus_can_be_switched_off(env):
    orders, reviews, settings = env
    assert reviews.bonus_config()["photo_percent"] == 10
    assert settings.update_admin_settings({"review_bonus_enabled": False})["settings"]["review_bonus_enabled"] is False
    assert reviews.bonus_config() is None
    oid = _delivered_order(orders)
    result = reviews.submit_review(7, "Іван", oid, 11, 5, "Дуже гарна якість друку, дякую", [GOOD_PHOTO])
    reviews.set_review_status(result["id"], "approved")
    assert reviews.grant_review_bonus(result["id"]) is None
    settings.update_admin_settings({"review_bonus_enabled": True})
    assert reviews.grant_review_bonus(result["id"])["percent"] == 10


def test_test_orders_excluded_from_stats(env):
    orders, reviews, settings = env
    from services.stats import get_stats
    from services.manual_orders import parse_manual_order

    base = {"customer_name": "Я", "user_id": 7, "status": "confirmed",
            "items": [{"product_name": "X", "product_id": 11, "price": 100, "quantity": 1}]}
    ok, real = parse_manual_order(base)
    ok2, test = parse_manual_order({**base, "is_test": True})
    assert ok and ok2 and test["extra"]["is_test"] is True
    for parsed in (real, test):
        oid, _ = orders.save_order(parsed["user_id"], "", parsed["first_name"], parsed["items"],
                                   parsed["total_price"], "", extra=parsed["extra"], merge=False)
        orders.update_order_status(oid, parsed["status"])
    stats = get_stats()
    assert stats[1] == 1 and stats[5] == 100  # order_count, earned: лише справжнє
    assert any(o["is_test"] for o in orders.list_orders())


def test_mark_existing_order_as_test(env):
    orders, reviews, settings = env
    from services.stats import get_stats

    oid = _delivered_order(orders)
    before = get_stats()[1]
    assert orders.update_order_fields(oid, {"is_test": 1})
    assert get_stats()[1] == before - 1
    assert next(o for o in orders.list_orders() if o["id"] == oid)["is_test"] == 1
    assert orders.update_order_fields(oid, {"is_test": 0})
    assert get_stats()[1] == before
