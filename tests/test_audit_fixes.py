"""Виправлення з аудиту: купон при злитті замовлень, подарунок вирішує сервер, позначка призу, SSRF у завантаженні фото."""

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

from db_core import db_connect  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "shop.db"))
    from services.db_utils import init_db

    init_db()
    yield tmp_path


def _item(pid=1, price=100, **extra):
    return {"product_id": pid, "product_name": f"Товар {pid}", "price": price, "quantity": 1, **extra}


def _seed_coupon(code="ONCE", one_per_user=1):
    conn = db_connect()
    conn.execute(
        "INSERT INTO coupons (code, type, value, min_order, uses_max, uses_count, one_per_user, active) "
        "VALUES (?, 'fixed', 10, 0, 100, 0, ?, 1)",
        (code, one_per_user),
    )
    conn.commit()
    conn.close()


# ─── Купон і злиття замовлень ───────────────────────────────

def test_order_with_coupon_is_not_merged_and_single_use_coupon_is_consumed(env):
    from services.coupons import CouponConsumptionError
    from services.orders import save_order

    _seed_coupon("ONCE", one_per_user=1)
    first_id, first_new = save_order(7, "@u", "U", [_item()], 100, "")
    assert first_new

    coupon_id, coupon_new = save_order(7, "@u", "U", [_item(2)], 90, "", coupon_code="ONCE", discount_amount=10)
    assert coupon_new and coupon_id != first_id

    # Раніше дозамова з тим самим купоном зливалась у «нове» замовлення й купон не списувався
    with pytest.raises(CouponConsumptionError):
        save_order(7, "@u", "U", [_item(3)], 90, "", coupon_code="ONCE", discount_amount=10)

    conn = db_connect()
    uses = conn.execute("SELECT COUNT(*) FROM coupon_uses WHERE code = 'ONCE'").fetchone()[0]
    conn.close()
    assert uses == 1


def test_merged_order_accumulates_discounts(env):
    from services.orders import save_order

    order_id, _ = save_order(8, "@u", "U", [_item()], 90, "", discount_amount=10, promotion_discount=10)
    merged_id, is_new = save_order(8, "@u", "U", [_item(2)], 95, "", discount_amount=5, promotion_discount=5)
    assert merged_id == order_id and not is_new

    conn = db_connect()
    total, discount, promo = conn.execute(
        "SELECT total_price, discount_amount, promotion_discount FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    conn.close()
    assert (total, discount, promo) == (185, 15, 15)


def test_prize_line_is_flagged_in_my_orders(env):
    from services.orders import list_user_orders, save_order

    prize = _item(5, 0, product_name="🎁 Приз розіграшу: Лампа", is_prize=True)
    save_order(9, "@u", "U", [prize, _item()], 100, "")
    items = list_user_orders(9)[0]["items"]
    flags = {it["product_id"]: it["is_prize"] for it in items}
    assert flags == {5: 1, 1: 0}


# ─── Подарунок за акцією ─────────────────────────────────────

@pytest.fixture
def gift_catalog(monkeypatch):
    import services.validation as v

    products = [
        {"id": 1, "name": "Брелок А", "cat": "key", "price": 80},
        {"id": 2, "name": "Брелок Б", "cat": "key", "price": 80},
        {"id": 3, "name": "Брелок В", "cat": "key", "price": 80},
        {"id": 4, "name": "Ваза", "cat": "home", "price": 900},
        {"id": 10, "name": "Міні-котик", "cat": "toy", "price": 60, "gift": True},
        {"id": 11, "name": "Дорогий подарунок", "cat": "toy", "price": 500, "gift": True},
    ]
    monkeypatch.setattr(v, "PRODUCTS_CACHE", products)
    monkeypatch.setattr(v, "get_gift_rule", lambda: {"id": "gift_3", "category": "key", "min_items": 3, "max_gift_price": 100})
    return v


def _lines(*pids):
    return [{"product_id": pid, "is_contract_price": False, "fromCustom": False} for pid in pids]


def test_gift_granted_when_threshold_met(gift_catalog):
    assert gift_catalog.resolve_order_gift(_lines(1, 2, 3), 10) == (10, "Міні-котик")


@pytest.mark.parametrize("pids,gift_id", [
    ((1, 2), 10),          # поріг не досягнуто
    ((1, 1, 2, 4), 10),    # той самий товар двічі й товар іншої категорії не рахуються
    ((1, 2, 3), 11),       # дорожчий за max_gift_price
    ((1, 2, 3), 4),        # товар не позначений як подарунок
    ((1, 2, 3), "abc"),    # сміття замість id
])
def test_gift_rejected(gift_catalog, pids, gift_id):
    assert gift_catalog.resolve_order_gift(_lines(*pids), gift_id) is None


def test_gift_rejected_without_active_rule(gift_catalog, monkeypatch):
    monkeypatch.setattr(gift_catalog, "get_gift_rule", lambda: None)
    assert gift_catalog.resolve_order_gift(_lines(1, 2, 3), 10) is None


def test_order_payload_with_bad_product_id_is_rejected_not_crashed():
    from services.validation import validate_order_payload

    ok, err = validate_order_payload([{"product_id": "abc", "quantity": 1}], None, 1, 0)
    assert not ok and isinstance(err, str)


# ─── SSRF у /api/upload-photo-url ────────────────────────────

def test_public_only_resolver_blocks_cgnat_and_metadata():
    import bot

    class FakeInner:
        def __init__(self, ip):
            self.ip = ip

        async def resolve(self, host, port=0, family=0):
            return [{"hostname": host, "host": self.ip, "port": port, "family": 2, "proto": 0, "flags": 0}]

        async def close(self):
            pass

    async def resolve(ip):
        resolver = bot._PublicOnlyResolver()  # ThreadedResolver потребує запущеного циклу
        resolver._inner = FakeInner(ip)
        return await resolver.resolve("evil.example", 80)

    for ip in ("100.100.100.200", "100.64.0.1", "169.254.169.254", "10.0.0.1", "127.0.0.1"):
        with pytest.raises(OSError):
            asyncio.run(resolve(ip))
    assert asyncio.run(resolve("93.184.216.34"))


def test_fetch_image_rejects_loopback_host(monkeypatch):
    import bot

    monkeypatch.setattr(bot, "is_safe_http_url", lambda url: True)
    with pytest.raises(ValueError):
        asyncio.run(bot.fetch_image_bytes_from_url("http://127.0.0.1:9/x.jpg"))


def test_fetch_image_revalidates_every_redirect(monkeypatch):
    """Перша адреса дозволена, редирект — на внутрішню: її не відкриваємо."""
    import aiohttp
    from aiohttp import web

    import bot

    hits = []

    async def scenario():
        async def start(request):
            raise web.HTTPFound(f"http://127.0.0.1:{port}/internal")

        async def internal(request):
            hits.append(request.path)
            return web.Response(body=b"secret", content_type="image/png")

        app = web.Application()
        app.router.add_get("/x.jpg", start)
        app.router.add_get("/internal", internal)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        nonlocal port
        port = site._server.sockets[0].getsockname()[1]
        try:
            with pytest.raises(ValueError, match="не дозволений"):
                await bot.fetch_image_bytes_from_url(f"http://127.0.0.1:{port}/x.jpg")
        finally:
            await runner.cleanup()

    port = 0
    # Тестовий сервер локальний, тож резолвер пускаємо будь-куди, а «внутрішність» визначає is_safe_http_url
    monkeypatch.setattr(bot, "_PublicOnlyResolver", aiohttp.ThreadedResolver)
    monkeypatch.setattr(bot, "is_safe_http_url", lambda url: "/internal" not in url)
    asyncio.run(scenario())
    assert hits == []
