"""Віджети чату, тимчасові кроки підтвердження й оновлення постів каналу (ROADMAP.md, п. 2–15)."""

import asyncio
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")

OWNER = 777
CLIENT = 555

PRODUCTS = {
    1: {"id": 1, "name": "Флексі-дракон", "price": 250, "cat": "toy", "hot": True, "description": "іграшка антистрес"},
    2: {"id": 2, "name": "Органайзер для кабелів", "price": 120, "oldPrice": 150, "cat": "home"},
    3: {"id": 3, "name": "Ваза «Хвиля»", "price": 480, "cat": "home", "description": "декор для дому"},
    4: {"id": 4, "name": "Брелок із написом", "price": 90, "cat": "gift", "custom_fields": [{"id": "text"}]},
    5: {"id": 5, "name": "Фігурка на замовлення", "price": 0, "cat": "toy", "contractPrice": True},
    6: {"id": 6, "name": "Ялинкова іграшка", "price": 60, "cat": "home", "description": "новорічний декор"},
    7: {"id": 7, "name": "Підставка під ноутбук", "price": 900, "cat": "home", "status": "hidden"},
}


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
    conn.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, stl_link TEXT)")
    conn.commit()
    conn.close()

    import services.channel_posts as posts
    import services.chat_widgets as cw
    import services.order_widgets as ow
    import services.orders as orders
    import services.settings as settings
    import widget_handlers as wh
    import widget_messages as wm

    for mod in (orders, settings, ow, wm, cw, posts, wh):
        importlib.reload(mod)
    monkeypatch.setattr(wh, "get_product_by_id", lambda pid: PRODUCTS.get(int(pid)))
    monkeypatch.setattr(wh, "get_all_products", lambda: [p for p in PRODUCTS.values() if p.get("status", "active") == "active"])
    yield SimpleNamespace(orders=orders, settings=settings, ow=ow, cw=cw, posts=posts, wh=wh)
    try:
        os.unlink(db_path)
    except OSError:
        pass


class FakeBot:
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

    async def edit_message_text(self, **kw):
        self.calls.append(("editMessageText", kw))

    async def delete_message(self, **kw):
        self.calls.append(("deleteMessage", kw))

    async def send_message(self, **kw):
        self.calls.append(("sendMessage", kw))
        return SimpleNamespace(message_id=self._mid())

    async def pin_chat_message(self, **kw):
        self.calls.append(("pinChatMessage", kw))

    def names(self):
        return [name for name, _ in self.calls]

    def last(self, name):
        return [kw for n, kw in self.calls if n == name][-1]


def _order(env, user_id=CLIENT, **extra):
    items = [{"product_id": 1, "product_name": "Флексі-дракон", "price": 250, "quantity": 2},
             {"product_id": 2, "product_name": "Органайзер <b>", "price": 120, "quantity": 1}]
    fields = {"delivery_method": "np", "city": "Київ", "branch": "№12", "recipient_name": "Олена",
              "phone": "+380501234567", "pay_method": "card", **extra}
    order_id, _ = env.orders.save_order(user_id, "@u", "Тест", items, 620, "", extra=fields)
    return order_id


def _query(user_id, data, message_id=0, qid="q"):
    return {"id": qid, "from": {"id": user_id}, "chat": {"id": user_id}, "message_id": message_id, "data": data}


def _answer(tg):
    return json.loads(tg.last("answerWidgetQuery").get("data") or "{}")


# ─── Налаштування ────────────────────────────────────────────────────────────

def test_new_settings_defaults_and_validation(env):
    s = env.settings
    assert set(s.widget_modes().values()) == {"off", "owner"} and s.widget_mode("dash") == "owner" and s.order_steps_enabled() and s.channel_autoupdate_enabled()
    assert not env.cw.enabled_for(OWNER, "cart") and env.cw.enabled_for(OWNER, "dash")
    assert not s.update_admin_settings({"widget_modes": {"cart": "nope"}})["ok"]
    out = s.update_admin_settings({"widget_modes": {"cart": "owner"}, "order_steps_enabled": False,
                                   "channel_posts_autoupdate": False})["settings"]
    assert out["widget_modes"]["cart"] == "owner" and out["widget_modes"]["hits"] == "off" and not out["order_steps_enabled"] and not out["channel_posts_autoupdate"]
    assert env.cw.enabled_for(OWNER, "cart") and not env.cw.enabled_for(CLIENT, "cart") and not env.cw.enabled_for(OWNER, "hits")
    assert not s.update_admin_settings({"widget_modes": {"dash": "all"}})["ok"]
    assert not s.update_admin_settings({"widget_modes": {"nope": "all"}})["ok"]
    s.update_admin_settings({"widget_modes": {"cart": "all"}})
    assert env.cw.enabled_for(CLIENT, "cart")


# ─── Збирачі ─────────────────────────────────────────────────────────────────

def test_all_widgets_fit_telegram_limits(env):
    cw, zsize = env.cw, env.ow.widget_zlib_size
    oid = _order(env)
    order, items = env.orders.get_order_with_items(oid)
    long_items = [{"product_name": f"Дуже довга унікальна назва товару номер {i} " * 2, "price": 100 + i, "quantity": 2}
                  for i in range(20)]
    promos = [{"type": "order_percent", "value": 10, "min_order": 500, "title": "Знижка на все " * 5, "emoji": "🔥",
               "ends_at": "2030-01-01T10:00:00", "accent": "green"}] * 5
    rows = cw.showcase_rows(list(PRODUCTS.values()))
    widgets = [
        cw.build_quote_widget(order, items)["html"],
        cw.build_quote_widget(order, long_items)["html"],
        cw.build_giveaway_widget({"id": 3, "title": "Дракон " * 30, "ends_at": "2030-01-01T10:00:00+00:00",
                                  "referral_bonus": 1}, cw.giveaway_state(10, 2, 7), "https://t.me/x?start=gw_3_r1")["html"],
        cw.build_promo_widget(promos)["html"],
        cw.build_showcase_widget("Хіти", rows, tabs=[["hits", "Хіти"], ["new", "Новинки"]], subtitle="x" * 200)["html"],
        cw.build_cart_widget(),
        cw.build_dash_widget("https://example.com/admin"),
    ]
    for html_text in widgets:
        assert zsize(html_text) <= 4096 and len(html_text.encode()) <= 32768
        for banned in ("<input", "<select", "<textarea", "color-mix", "fetch("):
            assert banned not in html_text


def test_quote_widget_escapes_and_shows_total(env):
    oid = _order(env, )
    order, items = env.orders.get_order_with_items(oid)
    widget = env.cw.build_quote_widget({**order, "discount_amount": 50, "gift_product_name": "Брелок"}, items)
    assert "Органайзер &lt;b&gt;" in widget["html"] and "Органайзер <b>" not in widget["html"]
    assert "620 ₴" in widget["html"] and "−50 ₴" in widget["html"] and "🎁 Брелок" in widget["html"]
    assert json.loads(widget["data"]) == {"a": 0} and str(oid) in widget["caption"]


def test_promo_lines(env):
    line = env.cw.promo_line
    assert line({"type": "order_percent", "value": 10, "min_order": 500}) == "−10% на замовлення від 500 ₴"
    assert line({"type": "order_fixed", "value": 100}) == "−100 ₴ на замовлення"
    assert "подарунок до 100 ₴" in line({"type": "gift_threshold", "min_items": 3, "max_gift_price": 100})
    assert line({"type": "info", "subtitle": "Привіт"}) == "Привіт"
    widget = env.cw.build_promo_widget([{"type": "info", "title": "А", "subtitle": "б"}, {"type": "info", "title": "В"}])
    assert widget["data"] == "" and widget["caption"] == ""  # назви акцій уже у віджеті


def test_showcase_rows_mark_quick_add(env):
    rows = {r[0]: r for r in env.cw.showcase_rows([PRODUCTS[1], PRODUCTS[2], PRODUCTS[4], PRODUCTS[5]])}
    assert rows[1][2:] == [250, 0, 1]
    assert rows[2][2:] == [120, 150, 1]          # стара ціна показується
    assert rows[4][4] == 0 and rows[5][4] == 0   # персоналізація й договірна — лише в застосунку
    assert len(env.cw.showcase_rows(list(PRODUCTS.values()))) == env.cw.SHOWCASE_MAX


def test_cart_rules_and_totals(env):
    cw = env.cw
    items, err = cw.change_cart({}, "a", 1, PRODUCTS[1])
    items, err = cw.change_cart(items, "+", 1, PRODUCTS[1])
    assert items == {1: 2} and not err
    assert cw.change_cart(items, "a", 4, PRODUCTS[4])[1]        # персоналізований — не можна
    assert cw.change_cart(items, "a", 7, PRODUCTS[7])[1]        # прихований — не можна
    assert cw.change_cart(items, "a", 99, None)[1]
    full = {i: 1 for i in range(100, 100 + cw.CART_MAX_LINES)}
    assert cw.change_cart(full, "a", 1, PRODUCTS[1])[1]         # ліміт позицій
    capped, _ = cw.change_cart({1: cw.CART_MAX_QTY}, "+", 1, PRODUCTS[1])
    assert capped[1] == cw.CART_MAX_QTY
    assert cw.change_cart({1: 1}, "-", 1, PRODUCTS[1])[0] == {}
    assert cw.change_cart({1: 3, 2: 1}, "x", 1, PRODUCTS[1])[0] == {2: 1}

    state = cw.cart_state({1: 2, 2: 1, 7: 1}, PRODUCTS.get, lambda subtotal, cats: 62 if cats == {"toy": 500, "home": 120} else 0)
    assert [line[0] for line in state["i"]] == [1, 2]            # прихований товар випав
    assert (state["s"], state["d"], state["t"]) == (620, 62, 558)
    assert cw.cart_count(state) == 3 and cw.cart_start_param(state) == "cart_1x2-2x1"
    assert len(cw.cart_start_param({"i": [[99990 + i, "", 1, 20] for i in range(cw.CART_MAX_LINES)]})) <= 64
    assert cw.cart_caption({"i": []}) == "🛒 Кошик порожній"


def test_cart_table_roundtrip(env):
    cw = env.cw
    assert cw.get_cart(CLIENT) == {"items": {}, "message_id": None}
    cw.save_cart(CLIENT, {1: 2, 2: 0}, 501)
    assert cw.get_cart(CLIENT) == {"items": {1: 2}, "message_id": 501}
    cw.save_cart(CLIENT, {3: 1})                     # message_id не чіпаємо
    assert cw.get_cart(CLIENT) == {"items": {3: 1}, "message_id": 501}


def test_pick_gifts_respects_budget_and_keywords(env):
    cw = env.cw
    products = list(PRODUCTS.values())
    kid = cw.pick_gifts("kid", "b2", "none", products, categories={"toy": "Іграшки"})
    assert kid[0]["id"] == 1
    assert all(p["id"] not in (5, 7) for p in kid)            # договірний і прихований не пропонуємо
    ny = cw.pick_gifts("any", "b1", "ny", products)
    assert ny[0]["id"] == 6
    home = cw.pick_gifts("her", "b2", "home", products)
    assert home[0]["id"] == 3
    assert cw.pick_gifts("any", "b4", "none", products)        # бюджет розширюється, якщо порожньо
    assert cw.gift_summary("kid", "b2", "ny") == "Дитині · 200–500 ₴ · Новий рік"


def test_dash_state_is_compact(env):
    state = env.cw.dash_state({"orders": 3, "revenue": 1500, "started": 4}, {"orders": 20, "revenue": 9000, "started": 30},
                              queue=2, pending_price=1, searches=["дракон", "органайзер для кабелів та іншого"], stamp="14:02")
    assert state["t"] == [3, 1500, 4] and state["w"][0] == 20 and state["q"] == 2 and len(state["s"][1]) <= 18
    env.cw.dumps(state)


# ─── Відправка й запити ──────────────────────────────────────────────────────

def test_quote_sent_only_when_enabled_and_priced(env):
    oid = _order(env)
    tg = FakeBot()
    assert not asyncio.run(env.wh.send_quote(tg, oid)) and not tg.calls        # вимкнено
    env.settings.update_admin_settings({"widget_modes": {k: "all" for k in ("quote", "giveaway", "promo", "cart", "hits", "gift")}})
    assert asyncio.run(env.wh.send_quote(tg, oid))
    payload = tg.last("sendWidget")
    assert payload["chat_id"] == CLIENT and payload["allow_queries"] and "Кошторис" in payload["caption"]
    env.orders.update_order_status(oid, "confirmed")
    assert not asyncio.run(env.wh.send_quote(tg, oid))                         # уже підтверджене — не питаємо
    assert not asyncio.run(env.wh.send_quote(FakeBot(fail={"sendWidget"}), _order(env)))


def test_quote_agree_confirms_only_own_order(env):
    oid = _order(env)
    confirmed = []

    async def confirm(order_id):
        confirmed.append(order_id)
        env.orders.update_order_status(order_id, "confirmed")
        return True

    env.wh.hooks["confirm_order"] = confirm
    env.settings.update_admin_settings({"widget_modes": {"quote": "all"}})
    tg = FakeBot()
    assert asyncio.run(env.wh.handle_widget_query(tg, _query(999, f"q:a:{oid}", 42)))
    assert not confirmed and _answer(tg) == {}                                  # чужий користувач
    asyncio.run(env.wh.handle_widget_query(tg, _query(CLIENT, f"q:a:{oid}", 42)))
    assert confirmed == [oid] and _answer(tg) == {"a": 1}
    assert tg.last("editMessageWidgetData")["message_id"] == 42
    asyncio.run(env.wh.handle_widget_query(tg, _query(CLIENT, f"q:a:{oid}", 42)))
    assert confirmed == [oid] and _answer(tg) == {"a": 1}                       # повторний дотик нічого не ламає
    items = [{"product_id": 1, "product_name": "Договірний", "price": 0, "quantity": 1}]
    free, _ = env.orders.save_order(CLIENT, "@u", "Тест", items, 0, "", extra={"delivery_method": "self"},
                                    price_pending=1, merge=False)                # ціна ще не встановлена — не погоджуємо
    assert env.orders.get_order_with_items(free)[0]["price_pending"] == 1
    asyncio.run(env.wh.handle_widget_query(tg, _query(CLIENT, f"q:a:{free}", 43)))
    assert free not in confirmed and _answer(tg) == {}


def test_unknown_widget_query_is_left_for_order_widget(env):
    tg = FakeBot()
    assert not asyncio.run(env.wh.handle_widget_query(tg, _query(CLIENT, "r", 1)))
    assert not tg.calls


def test_cart_flow_from_showcase_and_inside_cart(env):
    env.settings.update_admin_settings({"widget_modes": {k: "all" for k in ("quote", "giveaway", "promo", "cart", "hits", "gift")}})
    wh, cw, tg = env.wh, env.cw, FakeBot()
    asyncio.run(wh.handle_widget_query(tg, _query(CLIENT, "c:a:1", 10)))        # «+» у вітрині
    assert _answer(tg) == {"n": 1, "m": ""}
    cart_message = cw.get_cart(CLIENT)["message_id"]
    assert cart_message and tg.names().count("sendWidget") == 1                 # кошик надіслано вперше
    sent = json.loads(tg.last("sendWidget")["chat_data"])
    assert sent["i"] == [[1, "Флексі-дракон", 250, 1]] and sent["t"] == 250

    asyncio.run(wh.handle_widget_query(tg, _query(CLIENT, "c:a:2", 10)))        # другий товар — той самий кошик
    assert tg.names().count("sendWidget") == 1 and tg.last("editMessageWidgetData")["message_id"] == cart_message

    asyncio.run(wh.handle_widget_query(tg, _query(CLIENT, "c:+:1", cart_message)))  # «+» у самому кошику
    state = _answer(tg)
    assert state["s"] == 620 and state["t"] == 620 - state["d"] and cw.get_cart(CLIENT)["items"] == {1: 2, 2: 1}
    asyncio.run(wh.handle_widget_query(tg, _query(CLIENT, "c:a:4", 10)))        # не можна одним дотиком
    assert _answer(tg)["m"] and cw.get_cart(CLIENT)["items"] == {1: 2, 2: 1}
    asyncio.run(wh.handle_widget_query(tg, _query(CLIENT, "c:-:2", cart_message)))
    assert cw.get_cart(CLIENT)["items"] == {1: 2}


def test_cart_ignored_when_widgets_disabled(env):
    tg = FakeBot()
    asyncio.run(env.wh.handle_widget_query(tg, _query(CLIENT, "c:a:1", 10)))
    assert _answer(tg) == {} and env.cw.get_cart(CLIENT)["items"] == {}


def test_showcase_query_returns_rows_and_cart_count(env, monkeypatch):
    import services.hits as hits

    monkeypatch.setattr(hits, "top_selling_products", lambda days, limit: [{"product_id": 3, "sold": 9},
                                                                           {"product_id": 1, "sold": 5},
                                                                           {"product_id": 2, "sold": 1},
                                                                           {"product_id": 7, "sold": 1}])
    env.cw.save_cart(CLIENT, {1: 2})
    tg = FakeBot()
    asyncio.run(env.wh.handle_widget_query(tg, _query(CLIENT, "h:hits", 5)))
    out = _answer(tg)
    assert [r[0] for r in out["l"]] == [3, 1, 2] and out["n"] == 2 and out["h"]


def test_dash_query_is_owner_only(env, monkeypatch):
    monkeypatch.setattr(env.wh, "dash_state", lambda: {"t": [1, 2, 3], "w": [4, 5, 6], "q": 0, "pp": 0, "s": [], "u": ""})
    tg = FakeBot()
    asyncio.run(env.wh.handle_widget_query(tg, _query(CLIENT, "d", 5)))
    assert _answer(tg) == {}
    asyncio.run(env.wh.handle_widget_query(tg, _query(OWNER, "d", 5)))
    assert _answer(tg)["t"] == [1, 2, 3]


def test_ephemeral_reply_payload_and_failure(env):
    query = SimpleNamespace(id="cb1", from_user=SimpleNamespace(id=CLIENT), message=SimpleNamespace(chat_id=-100123))
    tg = FakeBot()
    assert asyncio.run(env.wh.send_ephemeral(tg, query, "Привіт", replace=True))
    payload = tg.last("sendMessage")
    assert payload["chat_id"] == -100123
    assert payload["ephemeral_message_parameters"] == {"callback_query_id": "cb1", "replace_callback_query_message": True}
    assert asyncio.run(env.wh.send_ephemeral(FakeBot(fail={"sendMessage"}), query, "x")) is None


def test_coupons_and_tickets_texts(env):
    wh = env.wh
    assert "немає" in wh.coupons_text([])
    text = wh.coupons_text([{"code": "HELLO<", "type": "percent", "value": 10, "min_order": 300}, ("FIX", "fixed", 50, 0)])
    assert "HELLO&lt;" in text and "−10% від 300 ₴" in text and "−50 ₴" in text
    g = {"id": 3, "title": "Дракон", "referral_bonus": 1}
    assert "ще не береш участі" in wh.my_tickets_text(g, None, CLIENT)
    mine = wh.my_tickets_text(g, {"no": 7, "t": 2, "n": 40}, CLIENT)
    assert "№7" in mine and f"r{CLIENT}" in mine


# ─── Пости каналу ────────────────────────────────────────────────────────────

def test_product_note_rules(env):
    note = env.posts.product_note
    assert note(None, 250) == env.posts.NOTE_UNAVAILABLE
    assert note({"price": 250, "status": "hidden"}, 250) == env.posts.NOTE_UNAVAILABLE
    assert note({"price": 250}, 250) == "" and note({"price": 250}, None) == ""
    assert note({"price": 200, "oldPrice": 250}, 250) == "🔥 Зараз 200 ₴ замість 250 ₴"
    assert note({"price": 300}, 250) == "💰 Актуальна ціна: 300 ₴"
    assert note({"price": 300, "contractPrice": True}, 250) == ""


def test_render_post_keeps_order_links(env):
    posts = env.posts
    body, buttons, err = posts.render_post({"id": 1, "product_id": 2, "text": "Заголовок\nопис <1>", "kind": "photo",
                                            "links_mode": 0}, "💰 Актуальна ціна: 300 ₴")
    assert not err and buttons and body.startswith("<b>Заголовок</b>\nопис &lt;1&gt;\n\n<i>💰")
    body, buttons, _ = posts.render_post({"id": 1, "product_id": 2, "text": "Т", "kind": "", "links_mode": -1}, "")
    assert not buttons and "startapp=ch_1_product_2" in body          # старий пост: посилання в тексті
    _, _, err = posts.render_post({"id": 1, "product_id": 2, "text": "x" * 1020, "kind": "photo", "links_mode": 0}, "нота")
    assert err


def test_posts_baseline_backfill_and_storage(env):
    posts = env.posts
    old = posts.create_post(1, "@ch", "Старий пост")                 # як до оновлення: без типу й ціни
    new = posts.create_post(2, "@ch", "Новий", kind="photo", links_mode=0, price=120)
    posts.set_message_id(old, 11)
    posts.set_message_id(new, 12)
    assert posts.posts_for_sync(1)[0]["price"] is None
    assert posts.backfill_baseline(PRODUCTS.get) == 1
    assert posts.posts_for_sync(1)[0]["price"] == 250 and posts.posts_for_sync(2)[0]["price"] == 120
    posts.set_note(new, "нота")
    assert posts.posts_for_sync(2)[0]["note"] == "нота"


# ─── Рівень бота ─────────────────────────────────────────────────────────────

@pytest.fixture
def botenv(env, monkeypatch):
    import app_state
    import bot

    fake = FakeBot()
    holder = SimpleNamespace(bot=fake)
    monkeypatch.setattr(app_state, "bot_app", holder)
    monkeypatch.setattr(bot, "bot_app", holder)
    monkeypatch.setattr(bot, "OWNER_ID", OWNER)
    synced = []

    async def fake_sync(order_id, action):
        synced.append((order_id, action))
        env.orders.update_order_status(order_id, {"confirm": "confirmed"}[action])

    monkeypatch.setattr(bot, "_sync_order_status_to_telegram", fake_sync)
    return SimpleNamespace(**vars(env), bot=bot, tg=fake, synced=synced)


class StepQuery:
    def __init__(self, data, user_id=OWNER):
        self.id = "cb"
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(chat_id=-1001, reply_markup=None, message_id=9)
        self.answers, self.markups, self.fail_edit = [], [], False

    async def answer(self, text=None, **kw):
        self.answers.append(text)

    async def edit_message_reply_markup(self, reply_markup=None, **kw):
        if self.fail_edit:
            raise RuntimeError("не вдалось")
        self.markups.append(reply_markup)

    async def edit_message_text(self, *a, **kw):                                # текст замовлення кроки не чіпають
        raise AssertionError("кроки не повинні міняти текст повідомлення")

    async def delete_message(self):
        raise AssertionError("кроки не повинні видаляти повідомлення замовлення")


def _callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def test_confirm_starts_steps_under_the_order_message(botenv):
    oid = _order(botenv)
    query = StepQuery(f"confirm_{oid}")
    asyncio.run(botenv.bot.order_action(SimpleNamespace(callback_query=query), SimpleNamespace(bot=botenv.tg)))
    assert "sendMessage" not in botenv.tg.names()                               # нових повідомлень у каналі немає
    markup = query.markups[0]
    assert f"st_p_{oid}" in _callbacks(markup) and f"st_x_{oid}" in _callbacks(markup)
    assert any("620 ₴" in b.text for row in markup.inline_keyboard for b in row)
    assert botenv.orders.get_order_with_items(oid)[0]["status"] == "new"       # ще не підтверджено


def test_steps_set_eta_and_confirm(botenv):
    oid = _order(botenv)
    price = StepQuery(f"st_p_{oid}")
    asyncio.run(botenv.bot.order_steps_action(SimpleNamespace(callback_query=price), None))
    assert f"st_e_{oid}_2" in _callbacks(price.markups[0]) and "Крок 2 із 2" in price.answers[-1]

    stranger = StepQuery(f"st_e_{oid}_2", user_id=5)
    asyncio.run(botenv.bot.order_steps_action(SimpleNamespace(callback_query=stranger), None))
    assert botenv.orders.get_order_with_items(oid)[0]["status"] == "new"

    done = StepQuery(f"st_e_{oid}_2")
    asyncio.run(botenv.bot.order_steps_action(SimpleNamespace(callback_query=done), None))
    order, _ = botenv.orders.get_order_with_items(oid)
    assert order["status"] == "confirmed" and order["eta_text"] == "2–3 дні"
    assert botenv.synced == [(oid, "confirm")] and "2–3 дні" in done.answers[-1]
    assert json.loads(botenv.ow.build_widget_data(order))["e"] == "2–3 дні"

    again = StepQuery(f"st_e_{oid}_1")                                          # застаріла кнопка
    asyncio.run(botenv.bot.order_steps_action(SimpleNamespace(callback_query=again), None))
    assert botenv.synced == [(oid, "confirm")]


def test_steps_back_restores_buttons_and_fallback_when_edit_fails(botenv):
    oid = _order(botenv)
    back = StepQuery(f"st_x_{oid}")
    asyncio.run(botenv.bot.order_steps_action(SimpleNamespace(callback_query=back), None))
    assert botenv.orders.get_order_with_items(oid)[0]["status"] == "new"
    assert f"confirm_{oid}" in _callbacks(back.markups[0])                      # кнопки замовлення повернулись

    query = StepQuery(f"confirm_{oid}")                                          # кнопки змінити не вдалось
    query.fail_edit = True
    asyncio.run(botenv.bot.order_action(SimpleNamespace(callback_query=query), SimpleNamespace(bot=botenv.tg)))
    assert botenv.orders.get_order_with_items(oid)[0]["status"] == "confirmed"   # підтверджено одразу, як раніше


def test_steps_can_be_switched_off(botenv):
    botenv.settings.update_admin_settings({"order_steps_enabled": False})
    oid = _order(botenv)
    query = StepQuery(f"confirm_{oid}")
    asyncio.run(botenv.bot.order_action(SimpleNamespace(callback_query=query), SimpleNamespace(bot=botenv.tg)))
    assert botenv.orders.get_order_with_items(oid)[0]["status"] == "confirmed"
    assert "sendMessage" not in botenv.tg.names()


def test_client_agree_confirms_and_alerts_owner(botenv):
    oid = _order(botenv)
    assert asyncio.run(botenv.bot._confirm_order_by_client(oid))
    assert botenv.synced == [(oid, "confirm")]
    assert any("погодив ціну" in kw.get("text", "") for name, kw in botenv.tg.calls if name == "sendMessage")
    assert not asyncio.run(botenv.bot._confirm_order_by_client(oid))             # уже підтверджене


def test_channel_posts_follow_price_and_stock(botenv, monkeypatch):
    import catalog_store

    posts = botenv.posts
    current = {"price": 250, "status": "active"}
    monkeypatch.setattr(catalog_store, "get_product_any", lambda pid: {"id": pid, **current} if current else None)
    photo = posts.create_post(1, "@ch", "Дракон\nопис", kind="photo", links_mode=0, price=250)
    text = posts.create_post(1, "@ch", "Дракон", kind="text", links_mode=1, price=250)
    posts.set_message_id(photo, 21)
    posts.set_message_id(text, 22)

    assert asyncio.run(botenv.bot.sync_channel_posts([1])) == 0                  # нічого не змінилось
    current["price"] = 300
    assert asyncio.run(botenv.bot.sync_channel_posts([1])) == 2
    caption = botenv.tg.last("editMessageCaption")
    assert caption["message_id"] == 21 and "Актуальна ціна: 300 ₴" in caption["caption"] and caption["reply_markup"]
    edited = botenv.tg.last("editMessageText")
    assert edited["message_id"] == 22 and edited["reply_markup"] is None and "startapp=ch_" in edited["text"]
    assert asyncio.run(botenv.bot.sync_channel_posts([1])) == 0                  # повторно не редагуємо

    current["status"] = "hidden"
    asyncio.run(botenv.bot.sync_channel_posts([1]))
    assert posts.NOTE_UNAVAILABLE in botenv.tg.last("editMessageCaption")["caption"]
    current.update(price=250, status="active")
    asyncio.run(botenv.bot.sync_channel_posts([1]))
    assert "<i>" not in botenv.tg.last("editMessageCaption")["caption"]          # повернулась базова ціна — рядок зник

    botenv.settings.update_admin_settings({"channel_posts_autoupdate": False})
    current["price"] = 999
    assert asyncio.run(botenv.bot.sync_channel_posts([1])) == 0


# ─── Каталог для сторінки «Віджети» ──────────────────────────────────────────

def test_widget_catalog_previews(env):
    from services import widget_catalog

    previews = widget_catalog.previews()
    assert set(previews) == set(env.settings.WIDGET_KINDS)
    for key, item in previews.items():
        assert item["html"] and item["states"], key
        assert env.ow.widget_zlib_size(item["html"]) <= 4096, key
        for state in item["states"]:
            assert state["label"] and isinstance(state["caption"], str) and len(state["data"].encode()) <= 1024, key
    assert len(previews["status"]["states"]) >= 5 and len(previews["quote"]["states"]) == 2
