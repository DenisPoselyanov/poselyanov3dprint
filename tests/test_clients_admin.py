"""Етап 5, слайс 6: «Клієнти» — сегменти, CSV, персональний купон, admin-only."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")

import services.coupons as coupons  # noqa: E402
import services.orders as orders  # noqa: E402
import services.users as users  # noqa: E402
from db_core import db_connect  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "shop.db"))
    from services.db_utils import init_db

    init_db()
    yield tmp_path


def _order(uid, total, status):
    oid, _ = orders.save_order(
        uid, "@u", "Т", [{"product_id": 1, "product_name": "X", "price": total, "quantity": 1}], total, ""
    )
    orders.update_order_status(oid, status)
    return oid


def _seed():
    users.record_start(1, "Купець", "buyer")        # купив 300
    users.record_start(2, "Багатий", "rich")        # 700 + 400 = 1100
    users.record_start(3, "Новачок", "newbie")      # без покупок
    users.record_start(4, "Скасував", "cancel")     # лише cancelled/new => не купував
    users.record_start(5, "Давно", "old")           # неактивний 60 днів, без покупок
    _order(1, 300, "confirmed")
    _order(2, 700, "delivered")
    _order(2, 400, "shipped")
    _order(4, 500, "cancelled")
    _order(4, 100, "new")
    conn = db_connect()
    conn.execute("UPDATE users SET last_seen_at = datetime('now','-60 days'), last_start_at = datetime('now','-60 days'), joined_at = datetime('now','-60 days') WHERE id = 5")
    conn.commit()
    conn.close()


def test_segment_counts_and_filters(env):
    _seed()
    counts = users.segment_counts()
    assert counts == {"all": 5, "buyers": 2, "non_buyers": 3, "inactive": 1, "vip": 1}
    for seg, n in counts.items():
        assert users.list_users(segment=seg)["total"] == n
    assert {u["id"] for u in users.list_users(segment="vip")["users"]} == {2}
    assert {u["id"] for u in users.list_users(segment="inactive")["users"]} == {5}
    assert users.list_users(segment="nonsense")["total"] == 5  # невідомий сегмент = усі


def test_list_users_has_purchases_and_last_activity(env):
    _seed()
    rows = {u["id"]: u for u in users.list_users()["users"]}
    assert rows[2]["purchases_count"] == 2 and rows[2]["orders_total"] == 1100
    assert rows[4]["purchases_count"] == 0 and rows[4]["orders_count"] == 2
    assert rows[1]["last_activity_at"] and rows[1]["last_order_at"]
    assert rows[3]["last_order_at"] == ""


def test_segment_counts_respect_blocked_and_search(env):
    _seed()
    users.set_blocked(1, True)
    assert users.segment_counts(blocked="active")["buyers"] == 1
    assert users.segment_counts(blocked="only")["all"] == 1
    assert users.segment_counts(search="rich")["all"] == 1
    assert 1 not in users.segment_user_ids("buyers")
    assert 1 in users.segment_user_ids("buyers", include_blocked=True)


def test_csv_bom_semicolon_and_formula_protection(env):
    users.record_start(10, "=HYPERLINK(\"x\")", "evil")
    users.record_start(11, "Оля", "olya")
    _order(11, 1500, "confirmed")
    text = users.users_to_csv(users.export_users())
    assert text.startswith("ID;Імʼя;Username;Статус;Замовлень;Сума, ₴")
    assert "'=HYPERLINK" in text and ";=HYPERLINK" not in text
    assert "11;Оля;olya;активний;1;1500;" in text
    only = users.users_to_csv(users.export_users(segment="vip"))
    assert "Оля" in only and "evil" not in only


def test_personal_coupon(env):
    users.record_start(77, "Клієнт", "k")
    res = coupons.create_personal_coupon(77)
    assert res["ok"] and res["link"].endswith("startapp=coupon_" + res["code"])
    c = coupons.get_coupon(res["code"])
    assert c["type"] == "percent" and c["value"] == 10 and c["uses_max"] == 1 and c["one_per_user"]
    assert c["allowed_user_ids"] == [77] and c["expires_at"]
    other = coupons.create_personal_coupon(77)
    assert other["code"] != res["code"]


def _http(env, monkeypatch, steps):
    import auth
    import bot
    import config
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from routes.setup import register_http_routes

    monkeypatch.setattr(config, "VALIDATE_INIT_DATA", True)
    monkeypatch.setattr(config, "ALLOW_LOCAL_NETWORK", False)
    monkeypatch.setattr(config, "ADMIN_BYPASS_TOKEN", "tok")
    monkeypatch.setattr(auth, "is_local_dev_origin", lambda request: False)

    async def go():
        app = web.Application()
        register_http_routes(app, bot)
        async with TestClient(TestServer(app)) as client:
            return await steps(client, {"X-Admin-Bypass-Token": "tok"})

    return asyncio.run(go())


def test_http_users_segment_csv_coupon_admin_only(env, monkeypatch):
    _seed()

    async def steps(client, adm):
        for method, url in (("get", "/api/admin/users"), ("get", "/api/admin/users.csv"),
                            ("post", "/api/admin/users/1/coupon"), ("post", "/api/admin/users/1/message")):
            assert (await getattr(client, method)(url)).status == 403
        r = await (await client.get("/api/admin/users?segment=vip&counts=1", headers=adm)).json()
        assert r["total"] == 1 and r["segment_counts"]["buyers"] == 2
        resp = await client.get("/api/admin/users.csv?segment=buyers", headers=adm)
        body = (await resp.read()).decode("utf-8")
        assert resp.status == 200 and body.startswith("﻿ID;") and "Купець" in body and "Новачок" not in body
        c = await client.post("/api/admin/users/3/coupon", headers=adm)
        data = await c.json()
        assert c.status == 200 and data["ok"] and data["code"]
        assert (await client.post("/api/admin/users/99999/coupon", headers=adm)).status == 404
        return True

    assert _http(env, monkeypatch, steps)


def test_http_send_user_message_graceful_without_bot(env, monkeypatch):
    users.record_start(5, "К", "k")

    async def steps(client, adm):
        r = await client.post("/api/admin/users/5/message", json={"text": "Привіт"}, headers=adm)
        assert r.status == 502  # бот не запущено — жодної реальної відправки
        r = await client.post("/api/admin/users/5/message", json={"text": ""}, headers=adm)
        assert r.status == 400
        return True

    assert _http(env, monkeypatch, steps)
