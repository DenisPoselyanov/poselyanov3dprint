"""Статуси замовлень, нові колонки та вибірка «Моїх замовлень»."""

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
def orders(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")

    import config

    importlib.reload(config)
    from services.db_utils import init_db

    init_db()
    init_db()  # міграція колонок ідемпотентна

    import services.orders as mod

    importlib.reload(mod)
    yield mod
    try:
        os.unlink(db_path)
    except OSError:
        pass


def _row(oid):
    """Рядок замовлення напряму (get_order_with_items у sqlite потребує таблиці products)."""
    from db_core import db_connect

    conn = db_connect(dict_rows=True)
    order = dict(conn.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone())
    items = [dict(r) for r in conn.execute("SELECT * FROM order_items WHERE order_id = ?", (oid,)).fetchall()]
    conn.close()
    return order, items


def _make_order(orders, user_id=7, **extra):
    items = [
        {"product_id": 1, "product_name": "Брелок", "price": 80, "quantity": 2,
         "personalization": "Олег", "personalization_note": "золотий"},
    ]
    order_id, _ = orders.save_order(
        user_id, "@u", "Тест", items, 160, "", gift_product_name="Подарунок", extra=extra or None
    )
    return order_id


@pytest.mark.parametrize(
    "old,new,ok",
    [
        ("new", "confirmed", True),
        ("new", "printing", True),
        ("confirmed", "printing", True),
        ("printing", "confirmed", False),
        ("shipped", "ready", False),
        ("delivered", "delivered", False),
        ("delivered", "cancelled", True),
        ("printing", "draft", True),
        ("cancelled", "confirmed", True),
        ("new", "bogus", False),
    ],
)
def test_transitions(orders, old, new, ok):
    assert orders.is_valid_transition(old, new) is ok


def test_extra_fields_saved_and_sanitized(orders):
    oid = _make_order(
        orders, delivery_method="np", pay_method="bogus", city="Київ",
        phone="+380501234567", evil="x", source="olx",
    )
    order, items = _row(oid)
    assert order["delivery_method"] == "np"
    assert order["pay_method"] == ""
    assert order["city"] == "Київ"
    assert order["source"] == "olx"
    assert "evil" not in order
    main = [i for i in items if not i["product_name"].startswith("🎁")][0]
    assert main["quantity"] == 2


def test_update_fields_ttn(orders):
    oid = _make_order(orders)
    assert orders.update_order_fields(oid, {"ttn": " 20450000000000 ", "id": 99}) is True
    order, _ = _row(oid)
    assert order["ttn"] == "20450000000000"
    assert order["id"] == oid
    assert orders.update_order_fields(99999, {"ttn": "1"}) is False


def test_delivered_sets_timestamp(orders):
    oid = _make_order(orders)
    orders.update_order_status(oid, "delivered")
    order, _ = _row(oid)
    assert order["status"] == "delivered"
    assert order["delivered_at"]


def test_list_user_orders_scoped_and_public(orders):
    mine = _make_order(orders, user_id=7)
    _make_order(orders, user_id=8)
    orders.update_order_status(mine, "ready")
    rows = orders.list_user_orders(7)
    assert [r["id"] for r in rows] == [mine]
    row = rows[0]
    assert row["step"] == "printing"
    assert "internal_note" not in row and "username" not in row
    assert any(i["is_gift"] for i in row["items"])
    personalised = [i for i in row["items"] if i["personalization"]]
    assert personalised[0]["personalization"] == "Олег"
    assert orders.list_user_orders(0) == []


def test_client_status_message():
    from rich_messages import build_client_status_update

    assert "20450" in build_client_status_update(5, "shipped", "20450")
    assert build_client_status_update(5, "new") is None


def test_status_filter_groups(orders):
    assert orders.expand_status_filter("work") == ["confirmed", "printing", "ready"]
    assert orders.expand_status_filter("shipped, bogus ,delivered") == ["shipped", "delivered"]
    assert orders.expand_status_filter("") == []


def test_counts_and_filtered_list(orders):
    a, b, c, d = (_make_order(orders, user_id=i) for i in (1, 2, 3, 4))
    orders.update_order_status(a, "confirmed")
    orders.update_order_status(b, "printing")
    orders.update_order_status(c, "shipped")
    counts = orders.count_orders_by_status()
    assert counts["all"] == 4
    assert counts["new"] == 1
    assert counts["work"] == 2
    assert counts["shipped"] == 1
    assert counts["delivered"] == 0

    work = orders.list_orders(statuses=orders.expand_status_filter("work"))
    assert sorted(r["id"] for r in work) == sorted([a, b])
    row = work[0]
    assert row["items"] and row["items"][0]["product_name"]
    assert "ttn" in row and "internal_note" in row
    assert {r["id"] for r in orders.list_orders()} == {a, b, c, d}


def test_internal_note_saved(orders):
    oid = _make_order(orders)
    assert orders.update_order_fields(oid, {"internal_note": "  оплата на карту  "}) is True
    order, _ = _row(oid)
    assert order["internal_note"] == "оплата на карту"


def test_sharing_channel_message_on_sqlite(orders):
    a = _make_order(orders, user_id=1)
    b = _make_order(orders, user_id=2)
    assert orders.get_orders_sharing_channel_message(a) == [a]
    orders.set_orders_channel_message_ids([a, b], 555)
    assert orders.get_orders_sharing_channel_message(a) == [a, b]
