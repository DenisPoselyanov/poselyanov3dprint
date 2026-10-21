"""Етап 5, слайс 5: «Купони» в адмінці — виторг по купону, лічильник «N з M», deep-link у магазин."""

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


@pytest.fixture
def env(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "shop.db"))
    from services.db_utils import init_db

    init_db()
    yield tmp_path


def _order(uid, total, status, code=None):
    oid, _ = orders.save_order(
        uid, "@u", "Тест", [{"product_id": 1, "product_name": "X", "price": total, "quantity": 1}], total, "",
        coupon_code=code, discount_amount=10 if code else 0,
    )
    orders.update_order_status(oid, status)
    return oid


def test_list_coupons_revenue_counts_sold_statuses_only(env):
    assert coupons.create_coupon({"code": "LETO10", "type": "percent", "value": 10, "uses_max": 5})["ok"]
    assert coupons.create_coupon({"code": "EMPTY", "type": "fixed", "value": 20})["ok"]
    _order(1, 400, "confirmed", "LETO10")
    _order(2, 600, "delivered", "leto10")  # регістр коду не важливий
    _order(3, 300, "new", "LETO10")        # ще не прийнято
    _order(4, 500, "cancelled", "LETO10")
    _order(5, 250, "printing")             # без купона
    by_code = {c["code"]: c for c in coupons.list_coupons()}
    assert by_code["LETO10"]["revenue"] == 1000 and by_code["LETO10"]["orders"] == 2
    assert by_code["LETO10"]["uses_max"] == 5 and "uses_count" in by_code["LETO10"]  # «N з M»
    assert by_code["EMPTY"]["revenue"] == 0 and by_code["EMPTY"]["orders"] == 0


def test_deep_link_uses_bot_username_and_skips_unsupported_codes(env, monkeypatch):
    import config

    monkeypatch.setattr(config, "BOT_USERNAME", "my_shop_bot")
    assert coupons.coupon_deep_link("leto10") == "https://t.me/my_shop_bot?startapp=coupon_LETO10"
    assert coupons.coupon_deep_link("SALE-20_x") == "https://t.me/my_shop_bot?startapp=coupon_SALE-20_X"
    # Telegram приймає в start_param лише A-Za-z0-9_- : для кирилиці/пробілів/зайвої довжини посилання немає
    assert coupons.coupon_deep_link("ЛІТО") == ""
    assert coupons.coupon_deep_link("A B") == ""
    assert coupons.coupon_deep_link("X" * 40) == ""
    assert coupons.coupon_deep_link("A") == ""
    assert coupons.create_coupon({"code": "LETO10", "type": "percent", "value": 10})["ok"]
    assert coupons.create_coupon({"code": "ЛІТО", "type": "percent", "value": 5})["ok"]
    by_code = {c["code"]: c for c in coupons.list_coupons()}
    assert by_code["LETO10"]["link"].endswith("startapp=coupon_LETO10")
    assert by_code["ЛІТО"]["link"] == ""


def test_bot_username_has_default_and_strips_at(monkeypatch):
    import importlib

    import config

    monkeypatch.setenv("BOT_USERNAME", "@other_bot")
    importlib.reload(config)
    assert config.BOT_USERNAME == "other_bot"
    monkeypatch.delenv("BOT_USERNAME", raising=False)
    importlib.reload(config)
    assert config.BOT_USERNAME == "poselyanov3dprint_bot"


def test_http_list_returns_revenue_and_link_for_admin_only(env, monkeypatch):
    import auth
    import bot
    import config
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from routes.setup import register_http_routes

    monkeypatch.setattr(config, "VALIDATE_INIT_DATA", True)
    monkeypatch.setattr(config, "ALLOW_LOCAL_NETWORK", False)
    monkeypatch.setattr(config, "ADMIN_BYPASS_TOKEN", "test-admin-token")
    monkeypatch.setattr(auth, "is_local_dev_origin", lambda request: False)
    assert coupons.create_coupon({"code": "VIP", "type": "percent", "value": 20, "allowed_user_ids": [11, 12]})["ok"]
    _order(11, 800, "confirmed", "VIP")

    async def go():
        app = web.Application()
        register_http_routes(app, bot)
        async with TestClient(TestServer(app)) as client:
            assert (await client.get("/api/coupons")).status == 403
            r = await client.get("/api/coupons", headers={"X-Admin-Bypass-Token": "test-admin-token"})
            assert r.status == 200
            return await r.json()

    row = next(c for c in asyncio.run(go()) if c["code"] == "VIP")
    assert row["revenue"] == 800 and row["orders"] == 1
    assert row["link"].endswith("startapp=coupon_VIP")
    assert sorted(row["allowed_user_ids"]) == [11, 12]
