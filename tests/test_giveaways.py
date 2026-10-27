"""Розіграші (GIVEAWAYS.md): участь, друзі, жеребкування з вагами, приз у кошику, заміна переможця, HTTP."""

import asyncio
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")

import services.giveaways as gw  # noqa: E402
import services.users as users  # noqa: E402
from db_core import db_connect  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "shop.db"))
    from services.db_utils import init_db

    init_db()
    for uid in (1, 2, 3, 4, 5):
        users.record_start(uid, f"U{uid}", f"u{uid}")
    _sql("UPDATE users SET joined_at = '2020-01-01 00:00:00'")
    yield tmp_path


def _sql(query, params=()):
    conn = db_connect()
    conn.execute(query, params)
    conn.commit()
    conn.close()


def _payload(**over):
    data = {
        "product_id": 42, "prize_value": 350, "winners_count": 1,
        "ends_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "shipping_payer": "shop", "referral_bonus": True, "referral_limit": 2, "text": "Розіграш дракона!",
    }
    data.update(over)
    return data


def _create(**over):
    payload, err = gw.validate_giveaway(_payload(**over))
    assert err is None, err
    payload["title"] = payload["title"] or "Дракон"
    gid = gw.create_giveaway(payload, "@chan")
    gw.set_message_id(gid, 100)
    return gid


def _new_user(uid):
    users.record_start(uid, f"N{uid}", "")


@pytest.mark.parametrize("over,needle", [
    ({"product_id": 0}, "товар"),
    ({"prize_type": "amount", "prize_value": 0}, "суму"),
    ({"prize_type": "amount", "product_id": 0, "title": ""}, "назву"),
    ({"prize_type": "lottery"}, "тип"),
    ({"winners_count": 0}, "Переможців"),
    ({"winners_count": 21}, "Переможців"),
    ({"ends_at": datetime.now(timezone.utc).isoformat()}, "5 хвилин"),
    ({"ends_at": (datetime.now(timezone.utc) + timedelta(days=91)).isoformat()}, "90"),
    ({"ends_at": "завтра"}, "дата"),
    ({"shipping_payer": "nobody"}, "доставку"),
    ({"referral_limit": 0}, "Ліміт"),
    ({"text": " "}, "текст"),
    ({"photo": "http://x/a.jpg"}, "https"),
])
def test_validation(over, needle):
    payload, err = gw.validate_giveaway(_payload(**over))
    assert payload is None and needle.lower() in err.lower()


def test_start_param_and_links():
    assert gw.parse_start_param("gw_12") == (12, 0)
    assert gw.parse_start_param("gw_12_r777") == (12, 777)
    assert gw.parse_start_param("ch_12") is None
    assert gw.parse_start_param("gw_x") is None
    assert gw.join_link("bot", 5, 9) == "https://t.me/bot?start=gw_5_r9"
    assert gw.prize_link("bot") == "https://t.me/bot?startapp=prize"
    assert gw.channel_url("@chan") == "https://t.me/chan"
    assert gw.channel_url("-1001234") == ""


def test_product_prize_value_optional_amount_without_product():
    payload, err = gw.validate_giveaway(_payload(prize_value=0))
    assert err is None and payload["prize_type"] == "product" and payload["prize_value"] == 0
    payload, err = gw.validate_giveaway(_payload(prize_type="amount", product_id=0, title="Сертифікат", prize_value=500))
    assert err is None and payload["product_id"] == 0 and payload["prize_value"] == 500


def test_join_numbers_and_duplicates(env):
    gid = _create()
    assert gw.join(gid, 1)["number"] == 1
    assert gw.join(gid, 2)["number"] == 2
    again = gw.join(gid, 1)
    assert again["ok"] and again["already"] and again["number"] == 1
    assert gw.join(9999, 1)["error"] == "not_found"


def test_join_closed_after_end_or_cancel(env):
    gid = _create()
    gw.join(gid, 1)
    _sql("UPDATE giveaways SET ends_at = '2020-01-01 00:00:00' WHERE id = ?", (gid,))
    assert gw.join(gid, 2)["error"] == "closed"
    assert gw.join(gid, 1)["already"]  # учасник бачить свій номер і після завершення
    gid2 = _create()
    assert gw.cancel_giveaway(gid2) and not gw.cancel_giveaway(gid2)
    assert gw.join(gid2, 1)["error"] == "closed"


def test_referral_counts_for_existing_bot_users_once(env):
    gid = _create()
    gw.join(gid, 1)
    gw.join(gid, 2, referrer_id=1)  # користувач, що вже був у боті, теж друг
    _new_user(10)
    gw.join(gid, 10, referrer_id=1)
    gw.join(gid, 10, referrer_id=2)  # повторно — вже в розіграші, запрошення 2 не зараховується
    gw.join(gid, 3, referrer_id=2)
    gw.join(gid, 1, referrer_id=2)  # 1 вже учасник — запрошення 2 не зараховується
    _new_user(11)
    gw.join(gid, 11, referrer_id=11)  # сам себе — ні
    _new_user(12)
    gw.join(gid, 12, referrer_id=999)  # запросив не учасник — ні
    assert gw.get_entry(gid, 2)["referred_by"] == 1
    assert gw.get_entry(gid, 10)["referred_by"] == 1
    assert gw.get_entry(gid, 1)["referred_by"] is None
    assert gw.get_entry(gid, 11)["referred_by"] is None
    assert gw.get_entry(gid, 12)["referred_by"] is None
    g = gw.get_giveaway(gid)
    assert gw.tickets_preview(g, 1) == 3 and gw.tickets_preview(g, 2) == 2


def test_referral_ignored_when_bonus_off(env):
    gid = _create(referral_bonus=False)
    gw.join(gid, 1)
    _new_user(10)
    gw.join(gid, 10, referrer_id=1)
    assert gw.get_entry(gid, 10)["referred_by"] is None
    assert gw.tickets_preview(gw.get_giveaway(gid), 1) == 1


def test_compute_weights_limit_and_unsubscribed_friends():
    entries = [{"user_id": 1, "referred_by": None, "is_new": 0}] + [
        {"user_id": 10 + i, "referred_by": 1, "is_new": 1} for i in range(5)
    ] + [{"user_id": 20, "referred_by": 2, "is_new": 1}, {"user_id": 2, "referred_by": None, "is_new": 0}]
    eligible = {1, 2, 10, 11, 12, 13, 14}  # друг 20 відписався
    w = gw.compute_weights(entries, eligible, bonus=True, limit=3)
    assert w[1] == 4  # 5 друзів, але ліміт 3
    assert w[2] == 1  # єдиний друг відписався
    assert 20 not in w
    assert gw.compute_weights(entries, eligible, bonus=False, limit=3)[1] == 1


def test_weighted_sample_unique_and_biased():
    rng = random.Random(1)
    picked = gw.weighted_sample({1: 1, 2: 1, 3: 1}, 5, rng)
    assert sorted(picked) == [1, 2, 3]
    assert gw.weighted_sample({}, 3, rng) == []
    wins = sum(gw.weighted_sample({1: 20, 2: 1}, 1, rng)[0] == 1 for _ in range(500))
    assert wins > 420  # очікувано ~95%


def _draw(gid, eligible=None, rng=None):
    assert gw.claim_for_draw(gid)
    assert not gw.claim_for_draw(gid)  # другий виклик не проведе жеребкування вдруге
    cands = gw.draw_candidates(gid, (5,))
    elig = eligible if eligible is not None else {int(c["user_id"]) for c in cands}
    return gw.draw(gid, elig, cands, rng or random.Random(3))


def test_draw_excludes_blocked_owner_and_saves_reserve(env):
    gid = _create(winners_count=2)
    for uid in (1, 2, 3, 4, 5):
        gw.join(gid, uid)
    users.set_blocked(4, True)
    res = _draw(gid)
    assert res["eligible"] == 3
    assert len(res["winners"]) == 2 and len(res["reserves"]) == 1
    assert not ({4, 5} & set(res["winners"] + res["reserves"]))
    g = gw.get_giveaway(gid)
    assert g["status"] == "drawn" and g["eligible_count"] == 3
    item = gw.list_giveaways()[0]
    assert [w["role"] for w in item["winners"]] == ["winner", "winner", "reserve"]
    assert item["stats"]["entries"] == 5


def test_due_and_recover(env):
    gid = _create()
    assert gw.due_giveaway_ids() == []
    _sql("UPDATE giveaways SET ends_at = '2020-01-01 00:00:00' WHERE id = ?", (gid,))
    assert gw.due_giveaway_ids() == [gid]
    assert gw.claim_for_draw(gid)
    assert gw.recover_interrupted() == 1
    assert gw.get_giveaway(gid)["status"] == "active"


def _order(order_id, user_id, status="new"):
    _sql("INSERT INTO orders (id, user_id, username, first_name, total_price, status) VALUES (?, ?, '', '', 0, ?)",
         (order_id, user_id, status))


def _claim(winner_id, user_id, order_id):
    conn = db_connect()
    try:
        ok = gw.claim_in_tx(conn, winner_id, user_id, order_id)
        conn.commit()
        return ok
    finally:
        conn.close()


def _published_winner(**over):
    gid = _create(**over)
    for uid in (1, 2, 3):
        gw.join(gid, uid)
    _draw(gid)
    gw.claim_publish(gid)
    (w,) = gw.pending_winners(gid)
    return gid, int(w["id"]), int(w["user_id"])


def test_prize_claim_cancel_and_reroll(env):
    gid, wid, uid = _published_winner()
    assert gw.active_prizes(uid) == []  # ще не видано
    prize = gw.issue_prize(wid)
    assert prize["ok"] and prize["user_id"] == uid
    assert not gw.issue_prize(wid)["ok"]  # повторно не видається
    (active,) = gw.active_prizes(uid)
    assert active["winner_id"] == wid and active["prize_type"] == "product" and active["product_id"] == 42
    assert gw.get_active_prize(uid, wid) and not gw.get_active_prize(uid + 100, wid)

    _order(77, uid)
    assert _claim(wid, uid, 77)
    assert not _claim(wid, uid, 78)  # двічі не забрати
    assert gw.active_prizes(uid) == []
    w = next(x for x in gw.list_winners([gid])[gid] if x["id"] == wid)
    assert w["claimed"] and w["order_id"] == 77
    assert "оформив" in gw.reroll(wid)["error"]
    assert "оформив" in gw.extend_prize(wid)["error"]

    # Скасоване замовлення повертає приз
    _sql("UPDATE orders SET status = 'cancelled' WHERE id = 77")
    assert [p["winner_id"] for p in gw.active_prizes(uid)] == [wid]
    rr = gw.reroll(wid)
    assert rr["ok"] and rr["published"] and rr["user_id"] != uid
    assert gw.active_prizes(uid) == []
    assert {x["status"] for x in gw.list_winners([gid])[gid] if x["user_id"] == uid} == {"replaced"}


def test_reroll_reserve_exhausted(env):
    gid = _create(winners_count=1)
    gw.join(gid, 1)
    _draw(gid)
    (w,) = gw.pending_winners(gid)
    assert gw.reroll(int(w["id"]))["error"] == "Резерв закінчився"


def test_expired_flag_extend_and_owner_notice(env):
    gid, wid, uid = _published_winner()
    gw.issue_prize(wid, hours=-1)
    assert gw.list_winners([gid])[gid][0]["expired"]
    assert gw.active_prizes(uid) == [] and not _claim(wid, uid, 5)
    (exp,) = gw.expired_unnotified()
    assert exp["id"] == wid and gw.mark_expired_notified(wid) and not gw.mark_expired_notified(wid)
    assert gw.expired_unnotified() == []

    res = gw.extend_prize(wid)
    assert res["ok"] and res["deadline"] > datetime.now(timezone.utc) + timedelta(hours=23)
    assert [p["winner_id"] for p in gw.active_prizes(uid)] == [wid]
    assert not gw.list_winners([gid])[gid][0]["expired"]


def test_reminders_24_and_3_hours(env):
    gid, wid, uid = _published_winner()
    gw.issue_prize(wid, hours=30)
    assert gw.due_reminders() == []
    gw.issue_prize  # noqa: B018
    _sql("UPDATE giveaway_winners SET claim_deadline = ? WHERE id = ?",
         ((datetime.now(timezone.utc) + timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S"), wid))
    (r,) = gw.due_reminders()
    assert r["flag"] == "reminded_24" and r["hours"] == 24 and r["winner_id"] == wid
    assert gw.mark_reminded(wid, "reminded_24") and not gw.mark_reminded(wid, "reminded_24")
    assert gw.due_reminders() == []
    _sql("UPDATE giveaway_winners SET claim_deadline = ? WHERE id = ?",
         ((datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"), wid))
    (r,) = gw.due_reminders()
    assert r["flag"] == "reminded_3"
    gw.mark_reminded(wid, "reminded_3")
    assert gw.due_reminders() == []


def test_missed_24h_reminder_sends_only_last(env):
    gid, wid, uid = _published_winner()
    gw.issue_prize(wid, hours=2)
    (r,) = gw.due_reminders()
    assert r["flag"] == "reminded_3"
    assert gw.mark_reminded(wid, "reminded_3")
    assert gw.due_reminders() == []  # 24-годинне вже не надсилається


# ── Бот і HTTP ─────────────────────────────────────────────────────

class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_photo(self, **kw):
        self.calls.append(("photo", kw))
        return SimpleNamespace(message_id=55)

    async def send_message(self, **kw):
        self.calls.append(("message", kw))
        return SimpleNamespace(message_id=56)

    async def edit_message_reply_markup(self, **kw):
        self.calls.append(("edit", kw))

    async def pin_chat_message(self, **kw):
        self.calls.append(("pin", kw))


def _member(subscribed: dict):
    async def state(chat_id, user_id):
        return subscribed.get(int(user_id), False)

    return state


def test_entry_flow_subscription(env, monkeypatch):
    import bot

    gid = _create()
    fake = FakeBot()
    subs = {1: False}
    monkeypatch.setattr(bot, "_channel_member_state", _member(subs))

    out = asyncio.run(bot.giveaway_entry_flow(fake, 1, 1, gid))
    assert out == "not_subscribed" and gw.get_entry(gid, 1) is None
    kb = fake.calls[-1][1]["reply_markup"].inline_keyboard
    assert kb[0][0].url == "https://t.me/chan" and kb[1][0].callback_data == f"gw_join_{gid}_0"
    assert asyncio.run(bot.giveaway_entry_flow(fake, 1, 1, gid, recheck=True)) == "not_subscribed"

    subs[1] = True
    assert asyncio.run(bot.giveaway_entry_flow(fake, 1, 1, gid)) == "entered"
    assert "№1" in fake.calls[-1][1]["text"] and "start=gw_" in fake.calls[-1][1]["text"]
    assert asyncio.run(bot.giveaway_entry_flow(fake, 1, 1, gid)) == "already"


def test_draw_aborts_when_telegram_cannot_check(env, monkeypatch):
    import bot

    gid = _create()
    gw.join(gid, 1)
    gw.join(gid, 2)

    async def broken(chat_id, user_id):
        return None

    async def nosleep(_):
        return None

    monkeypatch.setattr(bot, "_channel_member_state", broken)
    monkeypatch.setattr(bot.asyncio, "sleep", nosleep)
    res = asyncio.run(bot.run_giveaway_draw(gid, notify_owner=False))
    assert not res["ok"] and gw.get_giveaway(gid)["status"] == "active"

    monkeypatch.setattr(bot, "_channel_member_state", _member({1: True, 2: False}))
    res = asyncio.run(bot.run_giveaway_draw(gid, notify_owner=False))
    assert res["ok"] and res["winners"] == [1] and res["eligible"] == 1


def test_http_full_cycle(env, monkeypatch):
    import auth
    import bot
    import config
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from routes.setup import register_http_routes

    fake = FakeBot()
    monkeypatch.setattr(config, "VALIDATE_INIT_DATA", True)
    monkeypatch.setattr(config, "ALLOW_LOCAL_NETWORK", False)
    monkeypatch.setattr(config, "ADMIN_BYPASS_TOKEN", "tok")
    monkeypatch.setattr(auth, "is_local_dev_origin", lambda request: False)
    monkeypatch.setattr(bot, "bot_app", SimpleNamespace(bot=fake))
    product = {"id": 42, "name": "Дракон", "price": 350, "photos": ["https://res.cloudinary.com/x/image/upload/v1/a.jpg"]}
    monkeypatch.setattr(bot, "get_product_by_id", lambda pid: product if pid == 42 else None)
    monkeypatch.setattr(bot, "_channel_member_state", _member({1: True, 2: True}))

    async def nosleep(_):
        return None

    monkeypatch.setattr(bot.asyncio, "sleep", nosleep)
    adm = {"X-Admin-Bypass-Token": "tok"}

    async def steps(client):
        assert (await client.get("/api/admin/giveaways")).status == 403
        r = await client.post("/api/admin/giveaways", json=_payload(product_id=7), headers=adm)
        assert r.status == 404
        r = await client.post("/api/admin/giveaways", json=_payload(channel="t.me/mychan", pin=True), headers=adm)
        data = await r.json()
        assert r.status == 200 and data["ok"] and data["url"] == "https://t.me/mychan/55"
        g = data["giveaway"]
        assert g["title"] == "Дракон" and g["channel"] == "@mychan" and g["message_id"] == 55
        kind, post = fake.calls[0]
        assert kind == "photo" and "f_jpg" in post["photo"]
        assert post["reply_markup"].inline_keyboard[0][0].url.endswith(f"start=gw_{g['id']}")

        gw.join(g["id"], 1)
        gw.join(g["id"], 2)
        r = await client.post(f"/api/admin/giveaways/{g['id']}/publish", headers=adm)
        assert r.status == 409  # жеребкування ще не було
        r = await client.post(f"/api/admin/giveaways/{g['id']}/draw", headers=adm)
        g = (await r.json())["giveaway"]
        assert r.status == 200 and g["status"] == "drawn"
        r = await client.post(f"/api/admin/giveaways/{g['id']}/draw", headers=adm)
        assert r.status == 409

        r = await client.post(f"/api/admin/giveaways/{g['id']}/publish", headers=adm)
        data = await r.json()
        assert r.status == 200 and data["delivered"] == 1
        results = [kw for k, kw in fake.calls if k == "photo" and kw["chat_id"] == "@mychan" and getattr(kw["photo"], "filename", "") == "results.png"]
        assert "Підсумки" in results[0]["caption"] and results[0]["reply_parameters"].message_id == 55
        prize_msgs = [kw for k, kw in fake.calls if k == "message" and kw["chat_id"] in (1, 2)]
        assert prize_msgs[0]["reply_markup"].inline_keyboard[0][0].url.endswith("startapp=prize")
        assert "оплачуємо ми" in prize_msgs[0]["text"] and "чекає у вашому кошику" in prize_msgs[0]["text"]

        winner = data["giveaway"]["winners"][0]
        r = await client.post(f"/api/admin/giveaways/{g['id']}/winners/{winner['id']}/reroll", headers=adm)
        data = await r.json()
        assert r.status == 200 and data["delivered"] is True
        new = [w for w in data["giveaway"]["winners"] if w["role"] == "winner" and w["status"] == "notified"]
        assert new and new[0]["user_id"] != winner["user_id"]

        listing = await (await client.get("/api/admin/giveaways", headers=adm)).json()
        assert listing["giveaways"][0]["stats"]["entries"] == 2

    async def go():
        app = web.Application()
        register_http_routes(app, bot)
        async with TestClient(TestServer(app)) as client:
            await steps(client)

    asyncio.run(go())


# ── Купон на конкретний товар ──────────────────────────────────────

def test_product_coupon_only_for_its_product(env, monkeypatch):
    from services import coupons

    monkeypatch.setattr(coupons, "_product_name", lambda pid: "Дракон")
    coupons.create_coupon({"code": "GPRIZE1", "type": "fixed", "value": 400, "uses_max": 1, "product_id": 42,
                           "allowed_user_ids": [1]})
    # Без товару в кошику — не діє, з поясненням
    res = coupons.check_coupon("GPRIZE1", 1, 500, {7: 500})
    assert not res["valid"] and "Дракон" in res["message"]
    # З товаром — знімає ціну ОДНІЄЇ штуки (найдорожчий розмір), а не всю суму кошика
    res = coupons.check_coupon("GPRIZE1", 1, 900, {42: 350, 7: 550})
    assert res["valid"] and res["raw_discount"] == 350 and res["product_id"] == 42
    # Ліміт купона нижчий за ціну — знижка не більша за ліміт
    assert coupons.coupon_raw_discount({"type": "fixed", "value": 300, "product_id": 42}, 900, {42: 350}) == 300
    assert coupons.coupon_raw_discount({"type": "fixed", "value": 300, "product_id": 42}, 900, {}) == 0
    # Звичайний купон не змінився
    assert coupons.coupon_raw_discount({"type": "fixed", "value": 300}, 900, {42: 100}) == 300
    # Інший користувач — ні
    assert not coupons.check_coupon("GPRIZE1", 2, 500, {42: 350})["valid"]


def test_order_validation_with_product_coupon(env, monkeypatch):
    from services import coupons, validation

    products = [{"id": 42, "name": "Дракон", "price": 350, "cat": "toy"}, {"id": 7, "name": "Брелок", "price": 80, "cat": "key"}]
    monkeypatch.setattr(validation, "PRODUCTS_CACHE", products)
    monkeypatch.setattr(validation, "CUSTOM_PRODUCTS_CACHE", [])
    monkeypatch.setattr(validation, "reload_products_cache", lambda: None)
    monkeypatch.setattr(validation, "reload_filaments_cache", lambda: None)
    monkeypatch.setattr(validation, "compute_order_raw_discount", lambda *a: 0)
    coupons.create_coupon({"code": "GPRIZE2", "type": "fixed", "value": 350, "uses_max": 1, "product_id": 42,
                           "allowed_user_ids": [1]})
    items = [{"product_id": 42, "quantity": 2}, {"product_id": 7, "quantity": 1}]
    ok, res = validation.validate_order_payload(items, "GPRIZE2", 1, 780 - 350)
    assert ok, res
    assert res["coupon_discount"] == 350 and res["total_price"] == 430
    ok, err = validation.validate_order_payload([{"product_id": 7, "quantity": 1}], "GPRIZE2", 1, 80)
    assert not ok and "лише на" in err


def _catalog(monkeypatch):
    from services import validation

    products = [
        {"id": 42, "name": "Дракон", "price": 350, "cat": "toy",
         "variants": [{"id": "s", "name": "S", "price": 300}, {"id": "l", "name": "L", "price": 450},
                      {"id": "x", "name": "XL", "contract": True}]},
        {"id": 7, "name": "Брелок", "price": 80, "cat": "key"},
    ]
    monkeypatch.setattr(validation, "PRODUCTS_CACHE", products)
    monkeypatch.setattr(validation, "CUSTOM_PRODUCTS_CACHE", [])
    monkeypatch.setattr(validation, "reload_products_cache", lambda: None)
    monkeypatch.setattr(validation, "reload_filaments_cache", lambda: None)
    monkeypatch.setattr(validation, "compute_order_raw_discount", lambda *a: 0)
    return validation


def test_order_with_product_prize(env, monkeypatch):
    from services import orders
    from services.orders import save_order

    validation = _catalog(monkeypatch)
    gid, wid, uid = _published_winner()
    gw.issue_prize(wid)
    prize = gw.get_active_prize(uid, wid)

    ok, err = validation.validate_order_payload([], None, uid, 0, {**prize, "variant_id": ""})
    assert not ok and "варіант" in err
    ok, err = validation.validate_order_payload([], None, uid, 0, {**prize, "variant_id": "x"})
    assert not ok  # договірний варіант не може бути призом
    ok, err = validation.validate_order_payload([], None, uid, 0, None)
    assert not ok and "Порожній" in err

    # Лише приз: замовлення за 0 ₴; варіант обирає переможець
    ok, res = validation.validate_order_payload([], None, uid, 0, {**prize, "variant_id": "l"})
    assert ok, res
    line = res["items"][0]
    assert line["is_prize"] and line["price"] == 0 and line["variant_id"] == "l" and "Приз" in line["product_name"]
    assert res["total_price"] == 0 and res["prize_variant_id"] == "l"

    # Приз + докупка: платиться лише докупка
    ok, res = validation.validate_order_payload([{"product_id": 7, "quantity": 2}], None, uid, 160,
                                                {**prize, "variant_id": "s"})
    assert ok and res["subtotal"] == 160 and len(res["items"]) == 2

    claim = {"winner_id": wid, "variant_id": "s", "note": "🎁 Приз розіграшу. 🚚 Доставку оплачує магазин."}
    order_id, is_new = save_order(uid, "@u", "U", res["items"], res["total_price"], "", prize=claim)
    assert is_new and gw.active_prizes(uid) == []
    conn = db_connect()
    note = conn.execute("SELECT internal_note FROM orders WHERE id = ?", (order_id,)).fetchone()[0]
    items = conn.execute("SELECT product_name, price, variant_id FROM order_items WHERE order_id = ?",
                         (order_id,)).fetchall()
    conn.close()
    assert "Доставку оплачує магазин" in note
    assert any("Приз" in name and price == 0 and vid == "s" for name, price, vid in items)
    with pytest.raises(orders.CouponConsumptionError):  # той самий клас, що бачить save_order
        save_order(uid, "@u", "U", res["items"], res["total_price"], "", prize=claim, merge=False)


def test_order_with_fixed_variant_and_amount_prize(env, monkeypatch):
    validation = _catalog(monkeypatch)
    gid, wid, uid = _published_winner(product_variant="S")
    assert gw.get_giveaway(gid)["product_variant"] == "s"
    gw.issue_prize(wid)
    prize = gw.get_active_prize(uid, wid)
    ok, res = validation.validate_order_payload([], None, uid, 0, {**prize, "variant_id": "l"})
    assert ok and res["prize_variant_id"] == "s"  # зафіксований варіант важливіший за вибір клієнта

    gid2 = _create(prize_type="amount", product_id=0, title="Сертифікат", prize_value=500)
    gw.join(gid2, 4)
    _draw(gid2)
    gw.claim_publish(gid2)
    (w2,) = gw.pending_winners(gid2)
    gw.issue_prize(int(w2["id"]))
    cert = gw.get_active_prize(4, int(w2["id"]))
    ok, err = validation.validate_order_payload([], None, 4, 0, cert)
    assert not ok and "Порожній" in err
    # Сертифікат більший за замовлення — різниця згорає
    ok, res = validation.validate_order_payload([{"product_id": 7, "quantity": 1}], None, 4, 0, cert)
    assert ok and res["prize_discount"] == 80 and res["total_price"] == 0
    ok, res = validation.validate_order_payload([{"product_id": 7, "quantity": 9}], None, 4, 220, cert)
    assert ok and res["prize_discount"] == 500 and res["total_price"] == 220


def test_http_amount_prize_and_contract_product(env, monkeypatch):
    import auth
    import bot
    import config
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from routes.setup import register_http_routes

    fake = FakeBot()
    monkeypatch.setattr(config, "VALIDATE_INIT_DATA", True)
    monkeypatch.setattr(config, "ALLOW_LOCAL_NETWORK", False)
    monkeypatch.setattr(config, "ADMIN_BYPASS_TOKEN", "tok")
    monkeypatch.setattr(auth, "is_local_dev_origin", lambda request: False)
    monkeypatch.setattr(bot, "bot_app", SimpleNamespace(bot=fake))
    catalog = {
        42: {"id": 42, "name": "Дракон", "price": 350, "variants": [{"id": "s", "name": "S", "price": 300},
                                                                   {"id": "l", "name": "L", "price": 450}]},
        9: {"id": 9, "name": "Під замовлення", "price": 0, "contractPrice": True},
    }
    monkeypatch.setattr(bot, "get_product_by_id", lambda pid: catalog.get(pid))
    monkeypatch.setattr(bot, "_channel_member_state", _member({1: True}))

    async def nosleep(_):
        return None

    monkeypatch.setattr(bot.asyncio, "sleep", nosleep)
    adm = {"X-Admin-Bypass-Token": "tok"}

    async def steps(client):
        r = await client.post("/api/admin/giveaways", json=_payload(product_id=9), headers=adm)
        assert r.status == 400 and "ціни" in (await r.json())["error"]
        # Товар із розмірами: вартість призу — найдорожчий розмір
        r = await client.post("/api/admin/giveaways", json=_payload(prize_value=0), headers=adm)
        assert (await r.json())["giveaway"]["prize_value"] == 450
        # Сума без товару: пост без фото, назва за замовчуванням, купон на будь-яке замовлення
        r = await client.post("/api/admin/giveaways", headers=adm, json=_payload(
            prize_type="amount", product_id=0, prize_value=200, title="", shipping_payer="winner"))
        assert r.status == 400  # без товару потрібна назва
        r = await client.post("/api/admin/giveaways", headers=adm, json=_payload(
            prize_type="amount", product_id=0, prize_value=200, title="Сертифікат на 200 ₴", shipping_payer="winner"))
        g = (await r.json())["giveaway"]
        assert g["prize_type"] == "amount" and fake.calls[-1][0] == "message"
        gw.join(g["id"], 1)
        await client.post(f"/api/admin/giveaways/{g['id']}/draw", headers=adm)
        data = await (await client.post(f"/api/admin/giveaways/{g['id']}/publish", headers=adm)).json()
        assert data["delivered"] == 1
        prize = [kw for k, kw in fake.calls if k == "message" and kw["chat_id"] == 1][-1]
        assert "200 ₴</b> на будь-яке замовлення" in prize["text"] and "застосовано" in prize["text"]
        assert prize["reply_markup"].inline_keyboard[0][0].url.endswith("startapp=prize")
        # Зафіксований варіант: вартість призу — його ціна, назва з варіантом
        r = await client.post("/api/admin/giveaways", json=_payload(prize_value=0, product_variant="zz"), headers=adm)
        assert r.status == 400 and "варіант" in (await r.json())["error"]
        r = await client.post("/api/admin/giveaways", json=_payload(prize_value=0, product_variant="s"), headers=adm)
        g = (await r.json())["giveaway"]
        assert g["prize_value"] == 300 and g["title"] == "Дракон · S" and g["product_variant"] == "s"

    async def go():
        app = web.Application()
        register_http_routes(app, bot)
        async with TestClient(TestServer(app)) as client:
            await steps(client)

    asyncio.run(go())


# ── Приз у Mini App: /my/prizes, оформлення, нагадування, кнопки власника ──

def _signed(token: str, user_id: int) -> str:
    import hashlib
    import hmac
    import json
    import time
    from urllib.parse import urlencode

    fields = {"user": json.dumps({"id": user_id, "first_name": "U"}), "auth_date": str(int(time.time()))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})


def test_http_my_prizes_and_order_with_prize(env, monkeypatch):
    import auth
    import bot
    import config
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from routes.setup import register_http_routes

    validation = _catalog(monkeypatch)
    product = next(p for p in validation.PRODUCTS_CACHE if p["id"] == 42)
    monkeypatch.setattr(bot, "get_product_by_id", lambda pid: {**product, "photos": ["https://x/a.jpg"]} if pid == 42 else None)
    monkeypatch.setattr(config, "VALIDATE_INIT_DATA", True)
    monkeypatch.setattr(config, "ALLOW_LOCAL_NETWORK", False)
    monkeypatch.setattr(config, "BOT_TOKEN", "123:test")
    monkeypatch.setattr(auth, "is_local_dev_origin", lambda request: False)

    async def no_notify(**kw):
        return None

    monkeypatch.setattr(bot, "_deliver_order_notifications", no_notify)
    gid, wid, uid = _published_winner()
    gw.issue_prize(wid)
    hdr = {"X-Telegram-Init-Data": _signed("123:test", uid)}
    other = {"X-Telegram-Init-Data": _signed("123:test", 999)}

    def order(key, **over):
        body = {"items": [], "total_price": 0, "idempotency_key": key, "delivery_method": "self",
                "prize": {"winner_id": wid, "variant_id": "l"}}
        body.update(over)
        return body

    async def steps(client):
        assert (await client.get("/my/prizes")).status == 403
        assert (await (await client.get("/my/prizes", headers=other)).json())["prizes"] == []
        (p,) = (await (await client.get("/my/prizes", headers=hdr)).json())["prizes"]
        assert p["winner_id"] == wid and p["type"] == "product" and p["variant"] is None
        assert [v["id"] for v in p["variants"]] == ["s", "l"]  # договірний варіант не пропонується
        assert p["deadline"] and p["shipping_payer"] == "shop" and p["channel_url"] == "https://t.me/chan"
        # Чужий приз не оформити
        r = await client.post("/order", json=order("k0"), headers=other)
        assert r.status == 400 and (await r.json())["prize_gone"]
        r = await client.post("/order", json=order("k1"), headers=hdr)
        data = await r.json()
        assert r.status == 200 and data["ok"], data
        assert (await (await client.get("/my/prizes", headers=hdr)).json())["prizes"] == []
        r = await client.post("/order", json=order("k2"), headers=hdr)
        assert r.status == 400
        assert (await client.post("/my/share-prize", json={"winner_id": wid}, headers=hdr)).status == 404  # листівки ще немає

    async def go():
        app = web.Application()
        register_http_routes(app, bot)
        async with TestClient(TestServer(app)) as client:
            await steps(client)

    asyncio.run(go())
    w = next(x for x in gw.list_winners([gid])[gid] if x["id"] == wid)
    assert w["claimed"] and w["order_id"]


def test_prize_tick_reminder_owner_notice_and_buttons(env, monkeypatch):
    import app_state
    import bot

    fake = FakeBot()
    monkeypatch.setattr(bot, "bot_app", SimpleNamespace(bot=fake))
    monkeypatch.setattr(app_state, "bot_app", SimpleNamespace(bot=fake))
    monkeypatch.setattr(bot, "OWNER_ID", 777)
    monkeypatch.setattr(bot, "get_product_by_id", lambda pid: {"id": 42, "name": "Дракон", "price": 350})
    gid, wid, uid = _published_winner()
    gw.issue_prize(wid, hours=2)
    asyncio.run(bot._giveaway_prize_tick())
    (kind, msg), = fake.calls
    assert msg["chat_id"] == uid and "Дракон" in msg["text"] and "3 год" in msg["text"]
    assert msg["reply_markup"].inline_keyboard[0][0].url.endswith("startapp=prize")
    asyncio.run(bot._giveaway_prize_tick())
    assert len(fake.calls) == 1  # двічі не нагадує

    _sql("UPDATE giveaway_winners SET claim_deadline = '2020-01-01 00:00:00' WHERE id = ?", (wid,))
    asyncio.run(bot._giveaway_prize_tick())
    owner = fake.calls[-1][1]
    assert owner["chat_id"] == 777 and "не забрав" in owner["text"]
    buttons = [b.callback_data for b in owner["reply_markup"].inline_keyboard[0]]
    assert buttons == [f"gw_rr_{wid}", f"gw_ext_{wid}"]
    asyncio.run(bot._giveaway_prize_tick())
    assert fake.calls[-1][1] is owner  # повідомлення про строк — один раз

    answers, edits = [], []

    async def answer(*a, **kw):
        answers.append((a, kw))

    async def edit(text, **kw):
        edits.append(text)

    def update(data, user_id=777):
        q = SimpleNamespace(data=data, answer=answer, edit_message_text=edit,
                            message=SimpleNamespace(text_html="⌛ Розіграш"))
        return SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=user_id))

    asyncio.run(bot.giveaway_owner_action(update(f"gw_ext_{wid}", user_id=5), None))
    assert answers[-1][0] == ("Недоступно",) and not edits
    asyncio.run(bot.giveaway_owner_action(update(f"gw_ext_{wid}"), None))
    assert "подовжено" in edits[-1].lower() and [p["winner_id"] for p in gw.active_prizes(uid)] == [wid]
    assert "подовжено" in fake.calls[-1][1]["text"] and fake.calls[-1][1]["chat_id"] == uid
    asyncio.run(bot.giveaway_owner_action(update(f"gw_rr_{wid}"), None))
    assert "перекинуто" in edits[-1] and gw.active_prizes(uid) == []


# ── Макет розіграшу ────────────────────────────────────────────────

def test_render_giveaway_card_sizes():
    import io

    from PIL import Image

    import services.post_cards as pc

    for title in ("Лампа", "Дуже довга назва призу " * 6, ""):
        png = pc.render_giveaway_card(None, title=title, ends="30.11 о 20:00")
        assert Image.open(io.BytesIO(png)).size == (1080, 1080)
    assert Image.open(io.BytesIO(pc.render_giveaway_card(b"junk", title="x", ends=""))).size == (1080, 1080)


def test_http_giveaway_template_card(env, monkeypatch):
    import auth
    import bot
    import config
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from routes.setup import register_http_routes

    fake = FakeBot()
    monkeypatch.setattr(config, "VALIDATE_INIT_DATA", True)
    monkeypatch.setattr(config, "ALLOW_LOCAL_NETWORK", False)
    monkeypatch.setattr(config, "ADMIN_BYPASS_TOKEN", "tok")
    monkeypatch.setattr(auth, "is_local_dev_origin", lambda request: False)
    monkeypatch.setattr(bot, "bot_app", SimpleNamespace(bot=fake))
    product = {"id": 42, "name": "Дракон", "price": 350, "photos": ["https://res.cloudinary.com/x/image/upload/v1/a.jpg"]}
    monkeypatch.setattr(bot, "get_product_by_id", lambda pid: product if pid == 42 else None)
    downloaded = []

    async def fake_download(url):
        downloaded.append(url)
        return None

    monkeypatch.setattr(bot, "_download_card_photo", fake_download)
    adm = {"X-Admin-Bypass-Token": "tok"}

    async def steps(client):
        url = "/api/admin/giveaway-card"
        assert (await client.post(url, json=_payload())).status == 403
        assert (await client.post(url, json=_payload(ends_at="bad"), headers=adm)).status == 400
        r = await client.post(url, json=_payload(), headers=adm)
        assert r.status == 200 and r.headers["Content-Type"] == "image/png"
        assert downloaded == ["https://res.cloudinary.com/x/image/upload/v1/a.jpg"]
        r = await client.post("/api/admin/giveaways", json=_payload(card="template", pin=False), headers=adm)
        return r.status, await r.json()

    async def run():
        app = web.Application()
        register_http_routes(app, bot)
        async with TestClient(TestServer(app)) as client:
            return await steps(client)

    status, data = asyncio.run(run())
    assert status == 200 and data["ok"]
    kind, kw = fake.calls[0]
    assert kind == "photo" and kw["photo"].filename == "giveaway.png"
    assert kw["reply_markup"].inline_keyboard[0][0].text == "🎁 Взяти участь"


def test_render_results_card_sizes():
    import io

    from PIL import Image

    import services.post_cards as pc

    for names in (["Julia 🌸"], ["@a", "Б", "В"], ["Дуже довге ім'я " * 5] * 9, []):
        png = pc.render_results_card(names, prize="Приз " * 30)
        assert Image.open(io.BytesIO(png)).size == (1080, 1080)
