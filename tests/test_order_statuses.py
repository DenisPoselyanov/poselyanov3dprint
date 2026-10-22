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


def _two_item_order(orders, user_id, color_a, color_b):
    items = [
        {"product_id": 1, "product_name": "Брелок", "price": 80, "quantity": 2, "filament_name": color_a},
        {"product_id": 2, "product_name": "Лампа", "price": 200, "quantity": 1, "filament_name": color_b},
    ]
    oid, _ = orders.save_order(user_id, "@u", "Тест", items, 360, "")
    orders.update_order_status(oid, "confirmed")
    return oid


def test_print_queue_groups_by_color_and_skips_unconfirmed(orders):
    a = _two_item_order(orders, 1, "Чорний", "Червоний")
    b = _two_item_order(orders, 2, "Чорний", "Чорний")
    _make_order(orders, user_id=3)  # new — у чергу не потрапляє
    queue = orders.get_print_queue()
    colors = {g["filament"]: g for g in queue["groups"]}
    assert set(colors) == {"Чорний", "Червоний"}
    assert len(colors["Чорний"]["items"]) == 3
    assert queue["kpi"] == {"parts": 6, "orders": 2, "colors": 2}
    assert {i["order_id"] for g in queue["groups"] for i in g["items"]} == {a, b}


def test_print_flags_drive_status(orders):
    oid = _two_item_order(orders, 1, "Чорний", "Червоний")
    first, second = [r["id"] for r in _row(oid)[1]]

    state = orders.set_item_printed(first, True)
    assert state == {"order_id": oid, "status": "confirmed", "total": 2, "printed": 1}
    assert orders.next_status_after_print(state["status"], state["total"], state["printed"]) == "printing"

    orders.update_order_status(oid, "printing")
    state = orders.set_item_printed(second, True)
    assert orders.next_status_after_print(state["status"], state["total"], state["printed"]) == "ready"
    orders.update_order_status(oid, "ready")
    assert orders.get_print_queue()["groups"] == []  # готове вже не в черзі

    state = orders.set_item_printed(first, False)
    assert orders.next_status_after_print("printing", 2, 0) is None
    assert orders.next_status_after_print("shipped", 2, 2) is None
    assert orders.set_item_printed(99999, True) is None


def test_manual_order_does_not_merge_into_open_order(orders):
    first = _make_order(orders, user_id=5)
    second, is_new = orders.save_order(
        5, "@u", "Тест", [{"product_id": 0, "product_name": "Інше", "price": 50, "quantity": 1}], 50, "",
        extra={"source": "instagram"}, merge=False,
    )
    assert is_new and second != first
    assert _row(first)[0]["source"] != "instagram"


def test_stale_channel_button_cannot_move_order_back(orders):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    import bot
    from db_core import db_connect

    conn = db_connect()
    conn.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, stl_link TEXT)")
    conn.commit()
    conn.close()

    oid = _make_order(orders)
    orders.update_order_status(oid, "printing")

    query = MagicMock()
    query.answer = AsyncMock()
    query.data = f"confirm_{oid}"
    query.from_user.id = bot.OWNER_ID
    query.message.reply_markup = None
    update = MagicMock(callback_query=query)

    asyncio.run(bot.order_action(update, MagicMock()))
    assert _row(oid)[0]["status"] == "printing"


def test_status_messages_log(orders):
    import services.status_messages as sm

    sm.record_status_message(62, 111, 5001, "printing")
    sm.record_status_message(63, 222, 5002, "ready")
    sm.record_status_message(63, 222, None, "ready")  # без message_id не пишемо
    assert [r["message_id"] for r in sm.list_status_messages()] == [5001, 5002]
    assert [r["order_id"] for r in sm.list_status_messages(63)] == [63]
    sm.mark_status_message_deleted(sm.list_status_messages(62)[0]["id"])
    assert sm.list_status_messages(62) == []


def test_test_orders_can_be_excluded(orders):
    a, b = (_make_order(orders, user_id=i) for i in (1, 2))
    orders.update_order_fields(b, {"is_test": 1})
    assert orders.count_orders_by_status()["all"] == 2
    assert orders.count_orders_by_status(include_test=False)["all"] == 1
    assert {r["id"] for r in orders.list_orders(include_test=False)} == {a}
    assert {r["id"] for r in orders.list_orders()} == {a, b}
