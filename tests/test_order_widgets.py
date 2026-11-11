"""Віджет «Живий статус замовлення»: HTML і дані, облік у БД, трекінг НП, відправка та оновлення."""

import asyncio
import importlib
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")

OWNER = 777


@pytest.fixture
def env(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("OWNER_ID", str(OWNER))

    import config

    importlib.reload(config)
    from db_core import db_connect
    from services.db_utils import init_db

    init_db()
    init_db()  # міграція ідемпотентна
    conn = db_connect()
    # get_order_with_items приєднує products; у sqlite-тестах каталогу в БД немає
    conn.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, stl_link TEXT)")
    conn.commit()
    conn.close()

    import services.orders as orders
    import services.settings as settings
    import services.order_widgets as ow
    import widget_messages as wm

    for mod in (orders, settings, ow, wm):
        importlib.reload(mod)
    yield SimpleNamespace(orders=orders, settings=settings, ow=ow, wm=wm)
    try:
        os.unlink(db_path)
    except OSError:
        pass


def _make_order(env, user_id=OWNER, n_items=2, **extra):
    items = [
        {"product_id": i + 1, "product_name": f"Товар {i + 1}", "price": 100 + i, "quantity": 1 + i % 2}
        for i in range(n_items)
    ]
    fields = {"delivery_method": "np", "city": "Київ", "branch": "Відділення №12", "recipient_name": "Олена К.",
              "phone": "+380501234567", "pay_method": "card", **extra}
    order_id, _ = env.orders.save_order(user_id, "@u", "Тест", items, 450, "", extra=fields)
    return order_id


class FakeBot:
    """Запамʼятовує виклики API; message_id зростає з кожною відправкою."""

    def __init__(self, fail: set[str] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail or set()
        self._next = 100

    def _mid(self):
        self._next += 1
        return self._next

    async def do_api_request(self, endpoint, payload):
        self.calls.append((endpoint, payload))
        if endpoint in self.fail:
            raise RuntimeError(f"{endpoint} failed")
        return {"message_id": self._mid()} if endpoint.startswith("send") else True

    async def edit_message_caption(self, **kw):
        self.calls.append(("editMessageCaption", kw))

    async def delete_message(self, **kw):
        self.calls.append(("deleteMessage", kw))

    async def send_message(self, **kw):
        self.calls.append(("sendMessage", kw))
        return SimpleNamespace(message_id=self._mid())

    def names(self):
        return [name for name, _ in self.calls]


# ─── Налаштування запуску ────────────────────────────────────────────────────

def test_mode_defaults_to_off_and_validates(env):
    assert env.settings.order_widget_mode() == "off"
    assert not env.ow.widget_enabled_for(OWNER)
    assert not env.settings.update_admin_settings({"order_widget_mode": "everyone"})["ok"]
    assert env.settings.update_admin_settings({"order_widget_mode": "owner"})["settings"]["order_widget_mode"] == "owner"
    assert env.ow.widget_enabled_for(OWNER) and not env.ow.widget_enabled_for(5)
    env.settings.update_admin_settings({"order_widget_mode": "all"})
    assert env.ow.widget_enabled_for(5)


# ─── HTML, дані, підпис ──────────────────────────────────────────────────────

def test_widget_fits_limits_even_with_many_long_names(env):
    oid = _make_order(env)
    order, _ = env.orders.get_order_with_items(oid)
    names = ["Органайзер для кабелів настільний", "Підставка для навушників із гачком", "Тримач для телефона в авто",
             "Гачки настінні самоклейні, набір", "Підставка під ноутбук, складана", "Брелок із написом на замовлення",
             "Горщик для сукулентів геометричний", "Тримач для зубних щіток сімейний", "Підставка під гарячий посуд",
             "Кліпси для пакетів, набір із шести", "Органайзер для спецій на дверцята", "Тримач рушників магнітний",
             "Форма для печива «Зірка» велика", "Підставка для ножів універсальна", "Кришка для чашки з вушками"]
    items = [{"product_name": n, "price": 137 + i * 13, "quantity": 1 + i % 3} for i, n in enumerate(names)]
    widget = env.ow.build_widget(order, items)
    assert env.ow.widget_zlib_size(widget["html"]) <= env.ow.WIDGET_ZLIB_MAX
    assert len(widget["html"].encode()) <= env.ow.WIDGET_HTML_MAX
    assert "15 товарів" in widget["html"] and "у застосунку" in widget["html"]
    assert "color-mix" not in widget["html"] and "<input" not in widget["html"]


def test_html_hash_ignores_status_but_not_items(env):
    oid = _make_order(env)
    order, items = env.orders.get_order_with_items(oid)
    base = env.ow.build_widget(order, items)
    shipped = env.ow.build_widget({**order, "status": "shipped", "ttn": "20450718331264", "is_paid": 1}, items)
    assert shipped["hash"] == base["hash"] and shipped["data"] != base["data"]
    repriced = env.ow.build_widget(order, [{**items[0], "price": 999}, *items[1:]])
    assert repriced["hash"] != base["hash"]


def test_html_escapes_names_and_bakes_delivery(env):
    oid = _make_order(env)
    order, items = env.orders.get_order_with_items(oid)
    html_text = env.ow.build_widget_html(order, [{**items[0], "product_name": "<script>x</script>"}])
    assert "<script>x</script>" not in html_text and "&lt;script&gt;" in html_text
    assert "Відділення №12" in html_text and "Оплата карткою" in html_text
    self_html = env.ow.build_widget_html({**order, "delivery_method": "self"}, items)
    assert "Самовивіз" in self_html and '"of":4' in self_html and '"of":5' in html_text


def test_data_caption_and_notes(env):
    oid = _make_order(env)
    order, _ = env.orders.get_order_with_items(oid)
    assert json.loads(env.ow.build_widget_data(order)) == {"s": "new", "rb": 1}
    shipped = {**order, "status": "shipped", "ttn": "20450718331264", "is_paid": 1}
    tracking = {"state": "b", "free_until": "2026-11-19"}
    data = json.loads(env.ow.build_widget_data(shipped, tracking, review_bonus=False))
    assert data == {"s": "shipped", "p": 1, "n": "20450718331264", "c": "b", "f": "19 листопада"}
    assert env.ow.build_caption(shipped, tracking) == f"Замовлення №{oid} · Чекає у відділенні"
    assert env.ow.build_caption({**order, "status": "ready", "delivery_method": "self"}).endswith("Можна забирати")
    assert env.ow.status_note({**order, "status": "printing"}) == f"🖨️ Замовлення №{oid} друкується"
    assert "можна забирати" in env.ow.status_note({**order, "status": "ready", "delivery_method": "self"})
    assert env.ow.status_note(order) is None  # про «нове» окремо не сповіщаємо
    assert env.ow.status_note(shipped, "np_remind", tracking).endswith("безкоштовно до 19 листопада")


def test_contract_price_label(env):
    oid = _make_order(env)
    order, items = env.orders.get_order_with_items(oid)
    assert env.ow.total_label({**order, "price_pending": 1, "total_price": 0}, items) == "Договірна ціна"
    assert env.ow.total_label({**order, "price_pending": 1}, items) == "450 ₴ + договірна"
    assert env.ow.total_label({**order, "total_price": 2130}, items) == "2 130 ₴"


# ─── Оплата ──────────────────────────────────────────────────────────────────

def test_is_paid_is_editable_and_returned(env):
    oid = _make_order(env)
    assert not env.orders.get_order_with_items(oid)[0]["is_paid"]
    assert env.orders.update_order_fields(oid, {"is_paid": True})
    assert env.orders.get_order_with_items(oid)[0]["is_paid"] == 1
    assert env.orders.list_user_orders(OWNER)[0]["is_paid"] == 1


# ─── Нова Пошта ──────────────────────────────────────────────────────────────

def test_np_state_and_reminders(env):
    ow = env.ow
    assert [ow.np_state(c) for c in (1, 5, 7, 8, 9, 11, 102, 103, 0)] == ["", "w", "b", "b", "d", "d", "r", "r", ""]
    assert ow.free_until_from_np("2026-11-20") == "2026-11-19"
    assert ow.free_until_from_np("20.11.2026") == "2026-11-19" and ow.free_until_from_np("") == ""
    today = date(2026, 11, 15)
    assert ow.due_reminder("2026-11-13", 0, today) == 0
    assert ow.due_reminder("2026-11-12", 0, today) == 3
    assert ow.due_reminder("2026-11-12", 3, today) == 0
    assert ow.due_reminder("2026-11-10", 3, today) == 5
    assert ow.due_reminder("2026-11-01", 5, today) == 0 and ow.due_reminder("", 0, today) == 0


def test_tracking_rows_and_trackable_list(env):
    oid = _make_order(env)
    assert env.ow.list_trackable_orders() == []
    env.orders.update_order_fields(oid, {"ttn": "20450718331264"})
    env.orders.update_order_status(oid, "shipped")
    row = env.ow.list_trackable_orders()[0]
    assert row["id"] == oid and row["state"] is None and row["phone"] == "+380501234567"
    env.ow.save_tracking(oid, ttn="20450718331264", state="b", np_code=7, arrived_on="2026-11-12")
    env.ow.save_tracking(oid, reminded=3)
    saved = env.ow.get_tracking(oid)
    assert (saved["state"], saved["arrived_on"], saved["reminded"]) == ("b", "2026-11-12", 3)
    assert env.ow.list_trackable_orders()[0]["tracked_ttn"] == "20450718331264"


def test_np_track_normalizes_response(monkeypatch):
    from services import delivery

    calls = []

    async def fake_call(model, method, props):
        calls.append((model, method, props))
        return [{"Number": "20450718331264", "StatusCode": "7", "Status": "Прибув у відділення",
                 "ActualDeliveryDate": "2026-11-12 10:20:30", "DatePayedKeeping": "2026-11-20 00:00:00"},
                {"Number": "20450718331265", "StatusCode": "", "Status": "Номер не знайдено"}]

    monkeypatch.setattr(delivery, "_np_call", fake_call)
    out = asyncio.run(delivery.np_track([("2045 0718 3312 64", "+380501234567"), ("123", ""), ("20450718331265", "")]))
    assert calls[0][:2] == ("TrackingDocument", "getStatusDocuments")
    assert calls[0][2]["Documents"] == [{"DocumentNumber": "20450718331264", "Phone": "380501234567"},
                                        {"DocumentNumber": "20450718331265", "Phone": ""}]
    assert out["20450718331264"] == {"ttn": "20450718331264", "code": 7, "text": "Прибув у відділення",
                                     "arrived_on": "2026-11-12", "received_on": "", "paid_keeping_from": "2026-11-20"}
    assert out["20450718331265"]["code"] == 0


# ─── Відправка й оновлення ───────────────────────────────────────────────────

def test_sync_sends_nothing_when_disabled(env):
    oid = _make_order(env)
    bot = FakeBot()
    assert asyncio.run(env.wm.sync_order_widget(bot, oid, create=True)) == ""
    assert bot.calls == []


def test_sync_creates_then_updates_data_only(env):
    env.settings.update_admin_settings({"order_widget_mode": "owner"})
    oid = _make_order(env)
    bot = FakeBot()
    assert asyncio.run(env.wm.sync_order_widget(bot, oid, create=True)) == "sent"
    name, payload = bot.calls[0]
    assert name == "sendWidget" and payload["chat_id"] == OWNER and payload["allow_queries"] is True
    assert json.loads(payload["chat_data"])["s"] == "new" and payload["caption"].endswith("Очікує підтвердження")
    row = env.ow.get_widget(oid)
    assert row["message_id"] == 101 and env.ow.find_widget_by_message(OWNER, 101)["order_id"] == oid

    assert asyncio.run(env.wm.sync_order_widget(bot, oid)) == "updated"  # нічого не змінилось — без викликів
    assert bot.names() == ["sendWidget"]

    env.orders.update_order_status(oid, "printing")
    env.orders.update_order_fields(oid, {"is_paid": 1})
    assert asyncio.run(env.wm.sync_order_widget(bot, oid)) == "updated"
    assert bot.names() == ["sendWidget", "editMessageWidgetData", "editMessageCaption"]
    assert json.loads(bot.calls[1][1]["chat_data"]) == {"s": "printing", "p": 1, "rb": 1}
    assert bot.calls[1][1]["message_id"] == 101


def test_sync_without_create_does_not_start_widget(env):
    env.settings.update_admin_settings({"order_widget_mode": "all"})
    oid = _make_order(env, user_id=5)
    bot = FakeBot()
    assert asyncio.run(env.wm.sync_order_widget(bot, oid)) == ""  # старе замовлення без віджета — текстовий варіант
    assert bot.calls == []


def test_resend_and_recovery_replace_message(env):
    env.settings.update_admin_settings({"order_widget_mode": "owner"})
    oid = _make_order(env)
    bot = FakeBot()
    asyncio.run(env.wm.sync_order_widget(bot, oid, create=True))
    asyncio.run(env.wm.send_order_note(bot, oid, OWNER, "note"))
    note_id = env.ow.get_widget(oid)["note_message_id"]

    assert asyncio.run(env.wm.sync_order_widget(bot, oid, resend=True)) == "sent"  # /status
    row = env.ow.get_widget(oid)
    assert row["message_id"] == 103 and row["note_message_id"] == note_id
    assert ("deleteMessage", {"chat_id": OWNER, "message_id": 101}) in bot.calls

    env.orders.update_order_status(oid, "confirmed")
    broken = FakeBot(fail={"editMessageWidgetData"})
    assert asyncio.run(env.wm.sync_order_widget(broken, oid)) == "sent"  # повідомлення видалили — шлемо заново
    assert broken.names()[:2] == ["editMessageWidgetData", "sendWidget"]


def test_send_failure_propagates_for_fallback(env):
    env.settings.update_admin_settings({"order_widget_mode": "owner"})
    oid = _make_order(env)
    with pytest.raises(RuntimeError):
        asyncio.run(env.wm.sync_order_widget(FakeBot(fail={"sendWidget"}), oid, create=True))
    assert env.ow.get_widget(oid) is None


def test_note_replaces_previous_note(env):
    env.settings.update_admin_settings({"order_widget_mode": "owner"})
    oid = _make_order(env)
    bot = FakeBot()
    asyncio.run(env.wm.sync_order_widget(bot, oid, create=True))
    first = asyncio.run(env.wm.send_order_note(bot, oid, OWNER, "перше"))
    second = asyncio.run(env.wm.send_order_note(bot, oid, OWNER, "друге"))
    assert ("deleteMessage", {"chat_id": OWNER, "message_id": first}) in bot.calls
    assert env.ow.get_widget(oid)["note_message_id"] == second


def test_active_orders_for_status_command(env):
    a = _make_order(env)
    env.orders.update_order_status(a, "delivered")
    b, c = _make_order(env), _make_order(env, user_id=5)  # нове замовлення того ж покупця після завершеного
    assert a != b and env.ow.list_active_order_ids(OWNER) == [b]
    assert env.ow.list_active_order_ids(5) == [c]


# ─── Логіка в bot.py: сповіщення, трекінг НП, оплата ─────────────────────────

@pytest.fixture
def botenv(env, monkeypatch):
    import app_state
    import bot

    fake = FakeBot()
    holder = SimpleNamespace(bot=fake)
    monkeypatch.setattr(app_state, "bot_app", holder)
    monkeypatch.setattr(bot, "bot_app", holder)
    monkeypatch.setattr(bot, "OWNER_ID", OWNER)

    async def fake_sync(order_id, action):
        env.orders.update_order_status(order_id, {"delivered": "delivered"}[action])

    monkeypatch.setattr(bot, "_sync_order_status_to_telegram", fake_sync)
    return SimpleNamespace(**vars(env), bot=bot, tg=fake)


def _texts(tg):
    return [kw["text"] for name, kw in tg.calls if name == "sendMessage"]


def test_status_change_updates_widget_and_sends_one_note(botenv):
    botenv.settings.update_admin_settings({"order_widget_mode": "owner"})
    oid = _make_order(botenv)
    assert asyncio.run(botenv.bot._client_widget(oid, create=True))
    before, _ = botenv.orders.get_order_with_items(oid)
    botenv.orders.update_order_status(oid, "printing")
    assert asyncio.run(botenv.bot._notify_client_status(before, "printing"))
    assert _texts(botenv.tg) == [f"🖨️ Замовлення №{oid} друкується"]
    assert "sendRichMessage" not in botenv.tg.names() and "editMessageWidgetData" in botenv.tg.names()

    botenv.orders.update_order_status(oid, "ready")
    asyncio.run(botenv.bot._notify_client_status(before, "ready"))
    assert _texts(botenv.tg)[-1] == f"📦 Замовлення №{oid} готове"
    assert botenv.tg.names().count("deleteMessage") == 1  # попередній рядок-сповіщення прибрано


def test_status_change_without_widget_keeps_text_messages(botenv):
    oid = _make_order(botenv)  # режим off
    before, _ = botenv.orders.get_order_with_items(oid)
    botenv.orders.update_order_status(oid, "printing")
    assert asyncio.run(botenv.bot._notify_client_status(before, "printing"))
    assert botenv.tg.names() == ["sendRichMessage"]


def test_widget_failure_falls_back_to_text(botenv):
    botenv.settings.update_admin_settings({"order_widget_mode": "owner"})
    oid = _make_order(botenv)
    botenv.tg.fail = {"sendWidget"}
    assert asyncio.run(botenv.bot._client_widget(oid, create=True)) is False


def _ship(botenv, oid, ttn="20450718331264"):
    botenv.orders.update_order_fields(oid, {"ttn": ttn})
    botenv.orders.update_order_status(oid, "shipped")


def _track(botenv, monkeypatch, code, **extra):
    async def fake_track(docs):
        return {ttn: {"ttn": ttn, "code": code, "text": "НП", "arrived_on": "", "received_on": "",
                      "paid_keeping_from": "", **extra} for ttn, _ in docs}

    monkeypatch.setattr(botenv.bot.delivery, "np_track", fake_track)
    return asyncio.run(botenv.bot.track_np_orders())


def test_np_tracking_arrival_reminder_and_auto_delivered(botenv, monkeypatch):
    botenv.settings.update_admin_settings({"order_widget_mode": "owner"})
    oid = _make_order(botenv)
    asyncio.run(botenv.bot._client_widget(oid, create=True))
    _ship(botenv, oid)

    assert _track(botenv, monkeypatch, 5) == 1  # у дорозі: віджет оновлено тихо
    assert _texts(botenv.tg) == []
    assert botenv.ow.get_tracking(oid)["state"] == "w"

    _track(botenv, monkeypatch, 7, paid_keeping_from="2026-11-20")
    assert _texts(botenv.tg) == [f"📦 Посилка №{oid} прибула у відділення"]
    assert json.loads(botenv.ow.get_widget(oid)["data"])["f"] == "19 листопада"
    _track(botenv, monkeypatch, 7, paid_keeping_from="2026-11-20")
    assert len(_texts(botenv.tg)) == 1  # повторна перевірка без змін не спамить

    botenv.ow.save_tracking(oid, arrived_on="2020-01-01")  # давно чекає → нагадування + сповіщення власнику
    _track(botenv, monkeypatch, 7, paid_keeping_from="2026-11-20")
    texts = _texts(botenv.tg)
    assert texts[1].startswith(f"⏰ Посилка №{oid} чекає у відділенні") and "посилка вже 5 дн." in texts[2]
    assert botenv.ow.get_tracking(oid)["reminded"] == 5
    _track(botenv, monkeypatch, 7, paid_keeping_from="2026-11-20")
    assert len(_texts(botenv.tg)) == 3

    _track(botenv, monkeypatch, 9)
    assert botenv.orders.get_order_with_items(oid)[0]["status"] == "delivered"
    assert _texts(botenv.tg)[-1] == f"🎉 Замовлення №{oid} отримано"
    assert botenv.ow.list_trackable_orders() == []


def test_np_return_alerts_owner_and_keeps_status(botenv, monkeypatch):
    botenv.settings.update_admin_settings({"order_widget_mode": "owner"})
    oid = _make_order(botenv)
    asyncio.run(botenv.bot._client_widget(oid, create=True))
    _ship(botenv, oid)
    _track(botenv, monkeypatch, 103)
    texts = _texts(botenv.tg)
    assert texts[0] == f"↩️ Посилка №{oid} повертається відправнику" and "повертається відправнику" in texts[1]
    assert botenv.orders.get_order_with_items(oid)[0]["status"] == "shipped"
    assert json.loads(botenv.ow.get_widget(oid)["data"])["c"] == "r"


def test_np_tracking_skips_users_outside_rollout(botenv, monkeypatch):
    botenv.settings.update_admin_settings({"order_widget_mode": "owner"})
    oid = _make_order(botenv, user_id=5)
    _ship(botenv, oid)
    assert _track(botenv, monkeypatch, 9) == 0
    assert botenv.orders.get_order_with_items(oid)[0]["status"] == "shipped"


def test_paid_button_and_action(botenv):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    botenv.settings.update_admin_settings({"order_widget_mode": "owner"})
    oid = _make_order(botenv)
    asyncio.run(botenv.bot._client_widget(oid, create=True))
    order, _ = botenv.orders.get_order_with_items(oid)
    assert botenv.bot._paid_button(order).callback_data == f"paid_{oid}"
    assert botenv.bot._paid_button({**order, "pay_method": "cod"}) is None

    edited = {}

    class Query:
        data = f"paid_{oid}"
        from_user = SimpleNamespace(id=OWNER)
        message = SimpleNamespace(reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💬", url="https://t.me/x")], [botenv.bot._paid_button(order)]]))

        async def answer(self, *a, **k):
            pass

        async def edit_message_reply_markup(self, reply_markup=None):
            edited["rows"] = reply_markup.inline_keyboard

    asyncio.run(botenv.bot.paid_action(SimpleNamespace(callback_query=Query()), None))
    assert botenv.orders.get_order_with_items(oid)[0]["is_paid"] == 1
    assert edited["rows"][1][0].callback_data == f"unpaid_{oid}" and edited["rows"][0][0].url
    assert json.loads(botenv.ow.get_widget(oid)["data"])["p"] == 1

    Query.from_user = SimpleNamespace(id=5)
    Query.data = f"unpaid_{oid}"
    asyncio.run(botenv.bot.paid_action(SimpleNamespace(callback_query=Query()), None))
    assert botenv.orders.get_order_with_items(oid)[0]["is_paid"] == 1  # не власник — без змін


def test_widget_query_refreshes_tracking_once(botenv, monkeypatch):
    botenv.settings.update_admin_settings({"order_widget_mode": "owner"})
    oid = _make_order(botenv)
    asyncio.run(botenv.bot._client_widget(oid, create=True))
    _ship(botenv, oid)
    hits = []

    async def fake_track(docs):
        hits.append(docs)
        return {}

    monkeypatch.setattr(botenv.bot.delivery, "np_track", fake_track)
    botenv.bot._np_refreshed_at.clear()
    mid = botenv.ow.get_widget(oid)["message_id"]
    update = SimpleNamespace(api_kwargs={"widget_query": {"id": "q1", "from": {"id": OWNER}, "message_id": mid, "data": "r"}})
    ctx = SimpleNamespace(bot=botenv.tg)
    asyncio.run(botenv.bot.widget_query_action(update, ctx))
    asyncio.run(botenv.bot.widget_query_action(update, ctx))  # друге одразу — без запиту до НП
    assert len(hits) == 1 and botenv.tg.names().count("answerWidgetQuery") == 2
    asyncio.run(botenv.bot.widget_query_action(SimpleNamespace(api_kwargs={}), ctx))  # звичайне оновлення — ігноруємо
