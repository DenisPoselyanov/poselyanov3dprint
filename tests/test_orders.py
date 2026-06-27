"""Tests for order contact URL helpers and coupon lifecycle on status change."""

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
def order_db(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")

    import config

    importlib.reload(config)

    from services.db_utils import init_db

    init_db()

    import services.orders as orders

    importlib.reload(orders)

    yield orders, db_path

    try:
        os.unlink(db_path)
    except OSError:
        pass


def _seed_coupon_order(conn):
    conn.execute(
        """
        INSERT INTO coupons
        (code, type, value, min_order, uses_max, uses_count, one_per_user, active)
        VALUES ('LIFE', 'fixed', 10, 0, 5, 0, 0, 1)
        """
    )
    conn.execute(
        """
        INSERT INTO orders (id, user_id, username, first_name, total_price, status, coupon_code)
        VALUES (1, 1, 'u', 'U', 90, 'new', 'LIFE')
        """
    )
    conn.execute(
        """
        INSERT INTO coupon_uses (code, user_id, order_id, status)
        VALUES ('LIFE', 1, 1, 'pending')
        """
    )
    conn.commit()


def test_update_order_status_confirms_coupon(order_db):
    orders, _ = order_db
    from db_core import db_connect

    conn = db_connect()
    _seed_coupon_order(conn)
    conn.close()

    orders.update_order_status(1, "confirmed")

    conn = db_connect()
    uses_count = conn.execute("SELECT uses_count FROM coupons WHERE code = 'LIFE'").fetchone()[0]
    status = conn.execute("SELECT status FROM coupon_uses WHERE order_id = 1").fetchone()[0]
    conn.close()

    assert uses_count == 1
    assert status == "confirmed"


def test_update_order_status_cancel_releases_coupon(order_db):
    orders, _ = order_db
    from db_core import db_connect

    conn = db_connect()
    _seed_coupon_order(conn)
    conn.close()

    orders.update_order_status(1, "cancelled")

    conn = db_connect()
    count = conn.execute("SELECT COUNT(*) FROM coupon_uses WHERE order_id = 1").fetchone()[0]
    uses_count = conn.execute("SELECT uses_count FROM coupons WHERE code = 'LIFE'").fetchone()[0]
    conn.close()

    assert count == 0
    assert uses_count == 0


from services.orders import (
    contact_button_target,
    tg_contact_keyboard_url,
    tg_contact_url,
    tg_contact_url_from_order,
)


def test_tg_contact_url_with_username():
    assert tg_contact_url(123, "@MihoDmitriev") == "https://t.me/MihoDmitriev"


def test_tg_contact_url_with_handle_without_at():
    assert tg_contact_url(123, "MihoDmitriev") == "tg://user?id=123"


def test_tg_contact_url_first_name_not_handle():
    assert tg_contact_url(987654321, "Саня") == "tg://user?id=987654321"
    assert tg_contact_url(987654321, "Николай") == "tg://user?id=987654321"


def test_tg_contact_url_fallback_to_user_id():
    assert tg_contact_url(987654321, "невідомо") == "tg://user?id=987654321"
    assert tg_contact_url(987654321, "—") == "tg://user?id=987654321"
    assert tg_contact_url(987654321, None) == "tg://user?id=987654321"


def test_tg_contact_url_no_data():
    assert tg_contact_url(None, None) is None
    assert tg_contact_url(0, "невідомо") is None


def test_tg_contact_keyboard_url_with_username():
    assert tg_contact_keyboard_url(123, "@MihoDmitriev") == "https://t.me/MihoDmitriev"


def test_tg_contact_keyboard_url_skips_user_id_links():
    assert tg_contact_keyboard_url(987654321, "невідомо") is None
    assert tg_contact_keyboard_url(987654321, "Саня") is None
    assert tg_contact_keyboard_url(987654321, None) is None
    assert tg_contact_keyboard_url(None, None) is None


def test_contact_button_target_callback_without_username():
    assert contact_button_target(987654321, "Саня") == ("callback", "987654321")
    assert contact_button_target(987654321, "невідомо") == ("callback", "987654321")


def test_contact_button_target_url_with_username():
    assert contact_button_target(123, "@MihoDmitriev") == ("url", "https://t.me/MihoDmitriev")


def test_tg_contact_url_from_order():
    order = {"user_id": 555, "username": "@client"}
    assert tg_contact_url_from_order(order) == "https://t.me/client"

    order_no_handle = {"user_id": 555, "username": "невідомо"}
    assert tg_contact_url_from_order(order_no_handle) == "tg://user?id=555"
