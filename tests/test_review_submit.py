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

GOOD_PHOTO = "https://res.cloudinary.com/x/image/upload/poselyanov3dprint/review_1.jpg"


@pytest.fixture
def env(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")

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
    result = reviews.submit_review(7, "Іван Петренко", oid, 11, 5, "Супер", [GOOD_PHOTO, "https://evil.example/x.jpg"])
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
