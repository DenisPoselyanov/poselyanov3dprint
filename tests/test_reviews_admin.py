"""Етап 5, слайс 4: сторінка «Відгуки» в адмінці (список, фільтри, модерація, відповідь, видалення)."""

import asyncio
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

PHOTO = "https://res.cloudinary.com/mycloud/image/upload/v1/poselyanov3dprint/review_7_1700000000.jpg"


@pytest.fixture
def env(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "mycloud")
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
    reviews.invalidate_stats_cache()
    yield orders, reviews
    reviews.invalidate_stats_cache()
    try:
        os.unlink(db_path)
    except OSError:
        pass


def _make(orders, reviews, user=7, product=11, rating=5, text="Супер", photos=None, name="Іван Петренко"):
    items = [{"product_id": product, "product_name": "Брелок", "price": 80, "quantity": 1}]
    oid, _ = orders.save_order(user, "@u", "Тест", items, 80, "")
    orders.update_order_status(oid, "delivered")
    res = reviews.submit_review(user, name, oid, product, rating, text, photos)
    return res["id"], oid


def test_list_filters_counts_and_order(env):
    orders, reviews = env
    a, _ = _make(orders, reviews, user=7, product=11, rating=5, text="Чудовий брелок", photos=[PHOTO])
    b, _ = _make(orders, reviews, user=8, product=12, rating=2, text="Зламався", name="Олена")
    c, _ = _make(orders, reviews, user=9, product=11, rating=4, text="Норм", name="Марк")
    reviews.set_review_status(a, "approved")
    reviews.set_review_status(c, "hidden")

    everything = reviews.admin_list_reviews("all")
    assert [r["id"] for r in everything] == [c, b, a]  # нові першими
    first = everything[2]
    assert first["photos"] == [PHOTO] and first["order_id"] and first["product_id"] == 11
    assert first["client_name"] == "Іван Петренко" and first["status"] == "approved"

    assert [r["id"] for r in reviews.admin_list_reviews("pending")] == [b]
    assert [r["id"] for r in reviews.admin_list_reviews("approved")] == [a]
    assert [r["id"] for r in reviews.admin_list_reviews("hidden")] == [c]
    assert [r["id"] for r in reviews.admin_list_reviews("all", rating=2)] == [b]
    assert [r["id"] for r in reviews.admin_list_reviews("all", rating="x")] == [c, b, a]  # сміття ігноруємо
    assert len(reviews.admin_list_reviews("all", limit=2)) == 2

    counts = reviews.admin_review_counts()
    assert counts == {"pending": 1, "approved": 1, "hidden": 1, "all": 3, "avg_rating": 5.0, "rated_count": 1}
    assert reviews.pending_review_count() == 1


def test_search_by_product_client_text_and_order(env):
    orders, reviews = env
    a, oid_a = _make(orders, reviews, user=7, product=11, text="Все добре", name="Іван Петренко")
    b, _ = _make(orders, reviews, user=8, product=12, text="Кривий друк", name="Олена Коваль")
    names = {11: "Брелок Дракон", 12: "Підставка"}

    def ids(q):
        return [r["id"] for r in reviews.admin_list_reviews("all", q=q, product_names=names)]

    assert ids("дракон") == [a]  # назва товару, регістр кирилиці
    assert ids("ОЛЕНА") == [b]  # клієнт
    assert ids("кривий") == [b]  # текст
    assert ids(f"#{oid_a}") == [a]  # № замовлення
    assert ids("нема такого") == []


def test_client_name_and_username_come_from_users(env):
    orders, reviews = env
    from db_core import db_connect

    conn = db_connect()
    conn.execute("INSERT INTO users (id, name, username) VALUES (7, 'Іван П.', '@ivan')")
    conn.commit()
    conn.close()
    a, _ = _make(orders, reviews, user=7)
    item = reviews.admin_list_reviews("all")[0]
    assert item["id"] == a and item["client_name"] == "Іван П." and item["client_username"] == "ivan"


def test_status_change_refreshes_public_rating(env):
    orders, reviews = env
    a, _ = _make(orders, reviews, rating=4)
    assert reviews.product_stats().get(11, {}).get("rating_count") is None
    reviews.set_review_status(a, "approved")
    assert reviews.product_stats()[11]["rating_count"] == 1
    assert reviews.product_reviews(11)["total"] == 1
    reviews.set_review_status(a, "hidden")
    assert reviews.product_stats().get(11, {}).get("rating_count") is None
    assert reviews.product_reviews(11)["total"] == 0


def test_reply_set_clear_and_public_visibility(env):
    orders, reviews = env
    a, _ = _make(orders, reviews)
    assert reviews.set_admin_reply(a, "  Дякуємо!  ") is True
    assert reviews.admin_list_reviews("all")[0]["reply"] == "Дякуємо!"
    assert reviews.product_reviews(11)["total"] == 0  # відповідь не публікує сам відгук
    reviews.set_review_status(a, "approved")
    assert reviews.product_reviews(11)["reviews"][0]["reply"] == "Дякуємо!"
    assert reviews.set_admin_reply(a, "") is True
    assert reviews.product_reviews(11)["reviews"][0]["reply"] == ""
    with pytest.raises(reviews.ReviewError):
        reviews.set_admin_reply(a, "x" * 1001)
    assert reviews.set_admin_reply(a, "x" * 1000) is True
    assert reviews.set_admin_reply(9999, "тест") is False


def test_delete_refreshes_aggregates(env):
    orders, reviews = env
    a, _ = _make(orders, reviews, rating=5)
    reviews.set_review_status(a, "approved")
    assert reviews.product_stats()[11]["rating_count"] == 1
    assert reviews.delete_review(a) is True
    assert reviews.product_stats().get(11, {}).get("rating_count") is None
    assert reviews.admin_review_counts()["all"] == 0
    assert reviews.delete_review(a) is False


# ─── HTTP ───────────────────────────────────────────────────

@pytest.fixture
def api(env, monkeypatch):
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
    # env перезавантажує services.reviews; привʼязуємо bot.py до тих самих функцій
    import services.reviews as reviews

    for name in ("admin_list_reviews", "admin_review_counts", "delete_review", "pending_review_count",
                 "set_admin_reply", "set_review_status", "get_review", "product_reviews", "ReviewError"):
        monkeypatch.setattr(bot, name, getattr(reviews, name))
    monkeypatch.setattr(
        bot, "PRODUCTS_CACHE",
        [{"id": 11, "name": "Брелок Дракон", "photos": ["https://x/p.jpg"], "emoji": "🐉"}],
    )
    monkeypatch.setattr(bot, "CUSTOM_PRODUCTS_CACHE", [])

    def run(coro_fn):
        async def go():
            app = web.Application()
            register_http_routes(app, bot)
            async with TestClient(TestServer(app)) as client:
                return await coro_fn(client)
        return asyncio.run(go())

    return run, {"X-Admin-Bypass-Token": "test-admin-token"}


def test_routes_require_admin(env, api):
    orders, reviews = env
    a, _ = _make(orders, reviews)
    run, _ = api

    async def scenario(client):
        out = []
        for method, url, body in [
            ("get", "/api/admin/reviews", None),
            ("put", f"/api/admin/reviews/{a}/status", {"status": "approved"}),
            ("put", f"/api/admin/reviews/{a}/reply", {"reply": "x"}),
            ("delete", f"/api/admin/reviews/{a}", None),
            ("get", "/api/orders/counts", None),
        ]:
            fn = getattr(client, method)
            resp = await (fn(url, json=body) if body is not None else fn(url))
            out.append(resp.status)
        return out

    assert run(scenario) == [403] * 5
    assert reviews.get_review(a)["status"] == "pending"  # нічого не змінилось


def test_http_flow(env, api):
    orders, reviews = env
    a, oid = _make(orders, reviews, product=11, rating=5, photos=[PHOTO])
    b, _ = _make(orders, reviews, user=8, product=99, rating=3, name="Олена")
    run, admin = api

    async def scenario(client):
        r = await client.get("/api/admin/reviews?status=pending", headers=admin)
        assert r.status == 200
        data = await r.json()
        assert data["counts"]["pending"] == 2 and data["counts"]["avg_rating"] is None
        by_id = {x["id"]: x for x in data["reviews"]}
        assert by_id[a]["product_name"] == "Брелок Дракон" and by_id[a]["product_photo"] == "https://x/p.jpg"
        assert by_id[a]["order_id"] == oid and by_id[a]["photos"] == [PHOTO]
        assert by_id[b]["product_name"] == "Товар #99" and by_id[b]["product_photo"] == ""

        r = await client.get("/api/admin/reviews?q=дракон", headers=admin)
        assert [x["id"] for x in (await r.json())["reviews"]] == [a]  # пошук за назвою з каталогу
        r = await client.get("/api/admin/reviews?status=bogus", headers=admin)
        assert r.status == 400

        r = await client.get("/api/orders/counts", headers=admin)
        assert (await r.json())["reviews_pending"] == 2

        r = await client.put(f"/api/admin/reviews/{a}/status", json={"status": "approved"}, headers=admin)
        assert r.status == 200 and (await r.json())["counts"]["approved"] == 1
        r = await client.put(f"/api/admin/reviews/{a}/status", json={"status": "nope"}, headers=admin)
        assert r.status == 400
        r = await client.put("/api/admin/reviews/9999/status", json={"status": "hidden"}, headers=admin)
        assert r.status == 404

        r = await client.put(f"/api/admin/reviews/{a}/reply", json={"reply": "Дякуємо!"}, headers=admin)
        assert r.status == 200
        r = await client.put(f"/api/admin/reviews/{a}/reply", json={"reply": "x" * 1001}, headers=admin)
        assert r.status == 400
        r = await client.put("/api/admin/reviews/9999/reply", json={"reply": "x"}, headers=admin)
        assert r.status == 404

        r = await client.get("/api/products/11/reviews")
        pub = await r.json()
        assert pub["total"] == 1 and pub["reviews"][0]["reply"] == "Дякуємо!"

        r = await client.delete(f"/api/admin/reviews/{b}", headers=admin)
        assert r.status == 200 and (await r.json())["counts"]["all"] == 1
        r = await client.delete(f"/api/admin/reviews/{b}", headers=admin)
        assert r.status == 404

    run(scenario)


def test_bulk_actions(env, api):
    orders, reviews = env
    a, _ = _make(orders, reviews, product=11, rating=5)
    b, _ = _make(orders, reviews, user=8, product=99, rating=3, name="Олена")
    run, admin = api

    async def scenario(client):
        r = await client.post("/api/admin/reviews/bulk", json={"action": "x", "ids": [a]}, headers=admin)
        assert r.status == 400
        r = await client.post("/api/admin/reviews/bulk", json={"action": "approve", "ids": []}, headers=admin)
        assert r.status == 400
        r = await client.post("/api/admin/reviews/bulk", json={"action": "approve", "ids": [a, b, 9999]}, headers=admin)
        data = await r.json()
        assert r.status == 200 and data["done"] == 2 and data["counts"]["approved"] == 2
        r = await client.post("/api/admin/reviews/bulk", json={"action": "hide", "ids": [a, b]}, headers=admin)
        assert (await r.json())["done"] == 2
        r = await client.post("/api/admin/reviews/bulk", json={"action": "delete", "ids": [a, b]}, headers=admin)
        data = await r.json()
        assert data["done"] == 2 and data["counts"]["all"] == 0

    run(scenario)
