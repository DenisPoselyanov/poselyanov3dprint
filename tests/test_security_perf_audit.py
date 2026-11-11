"""Аудит безпеки й продуктивності: підміна імені в замовленні, ліміти, статика з ETag, індекси, «бомба» у фото."""

import asyncio
import hashlib
import hmac
import io
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")

from db_core import db_connect  # noqa: E402

TOKEN = "123:test"


@pytest.fixture
def env(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "shop.db"))
    from services.db_utils import init_db

    init_db()
    yield tmp_path


def _init_data(user: dict) -> str:
    fields = {"user": json.dumps(user), "auth_date": str(int(time.time()))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _run(routes, steps):
    async def go():
        app = web.Application()
        for method, path, handler in routes:
            app.router.add_route(method, path, handler)
        async with TestClient(TestServer(app)) as client:
            return await steps(client)

    return asyncio.run(go())


# ─── Замовлення: ім'я та нік лише з підписаного initData ────

@pytest.fixture
def order_env(env, monkeypatch):
    import bot
    import config

    monkeypatch.setattr(config, "VALIDATE_INIT_DATA", True)
    monkeypatch.setattr(config, "LOCAL_DEV_MODE", False)
    monkeypatch.setattr(config, "ALLOW_LOCAL_NETWORK", False)  # інакше запит з 127.0.0.1 — це «адмін з локальної мережі»
    monkeypatch.setattr(config, "BOT_TOKEN", TOKEN)

    def fake_validate(items, coupon_code, user_id, client_total, prize=None):
        return True, {
            "items": [{"product_id": 1, "product_name": "Товар", "price": 100, "quantity": 1}],
            "total_price": 100,
        }

    async def no_notifications(**kwargs):
        return None

    monkeypatch.setattr(bot, "validate_order_payload", fake_validate)
    monkeypatch.setattr(bot, "resolve_order_gift", lambda items, gift_id: None)
    monkeypatch.setattr(bot, "_deliver_order_notifications", no_notifications)
    return bot


def _order_row(order_id):
    conn = db_connect()
    row = conn.execute("SELECT username, first_name, comment FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    return row


def _post_order(bot, user: dict, body: dict):
    async def steps(client):
        r = await client.post("/order", json=body, headers={"X-Telegram-Init-Data": _init_data(user)})
        return r.status, await r.json()

    return _run([("POST", "/order", bot.handle_order)], steps)


def test_order_ignores_username_from_request_body(order_env):
    evil = "@x');alert(document.cookie);//"
    status, data = _post_order(
        order_env,
        {"id": 501, "first_name": "Оля"},  # у користувача немає @username
        {"items": [{}], "total_price": 100, "idempotency_key": "k1", "user": evil, "tg_username": "admin_fake",
         "first_name": "<b>Чужий</b>"},
    )
    assert status == 200 and data["ok"]
    username, first_name, _ = _order_row(data["order_id"])
    assert username == "Оля" and first_name == "Оля"


def test_order_uses_signed_username_and_caps_comment(order_env):
    status, data = _post_order(
        order_env,
        {"id": 502, "first_name": "Ігор", "username": "igor_3d"},
        {"items": [{}], "total_price": 100, "idempotency_key": "k2", "user": "@someone_else", "comment": "я" * 5000},
    )
    assert status == 200
    username, _, comment = _order_row(data["order_id"])
    assert username == "@igor_3d"
    assert len(comment) == order_env.ORDER_COMMENT_MAX


def test_order_rejects_non_object_body(order_env):
    async def steps(client):
        r = await client.post("/order", json=[1, 2, 3])
        return r.status

    assert _run([("POST", "/order", order_env.handle_order)], steps) == 400


@pytest.mark.parametrize("raw, expected", [
    ("igor_3d", "igor_3d"),
    ("@igor_3d", "igor_3d"),
    ("x');alert(1);//", ""),
    ("<img src=x>", ""),
    ("a" * 33, ""),
    (None, ""),
    (12345, "12345"),
])
def test_clean_tg_username(raw, expected):
    import bot

    assert bot._clean_tg_username(raw) == expected


# ─── Межі числових параметрів ───────────────────────────────

@pytest.mark.parametrize("query, expected", [
    ("", 10),
    ("limit=5", 5),
    ("limit=-7", 0),
    ("limit=99999999999999999999999", 100_000),
    ("limit=abc", 10),
])
def test_query_int_is_clamped(query, expected):
    import bot

    request = make_mocked_request("GET", "/api/x?" + query)
    assert bot._query_int(request, "limit", 10) == expected


def test_my_orders_limit_is_clamped(env):
    from services.orders import list_user_orders, save_order

    for n in range(3):
        save_order(9, "@u", "U", [{"product_id": 1, "product_name": "Т", "price": 10, "quantity": 1}], 10, "",
                   coupon_code=None)
        conn = db_connect()
        conn.execute("UPDATE orders SET status = 'delivered'")
        conn.commit()
        conn.close()
    # Від'ємний LIMIT у SQLite означає «без ліміту» — раніше так можна було витягнути всю історію одним запитом
    assert len(list_user_orders(9, -1)) == 1
    assert len(list_user_orders(9, 10**9)) == 3


# ─── Індекси ────────────────────────────────────────────────

def test_order_indexes_exist(env):
    conn = db_connect()
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()}
    conn.close()
    assert {
        "idx_orders_user_id", "idx_orders_status", "idx_orders_ordered_at",
        "idx_order_items_order_id", "idx_order_items_product_id", "idx_events_user_type_created",
    } <= names


# ─── Статика: кеш у пам'яті, gzip один раз, ETag → 304 ──────

def test_static_etag_gzip_and_revalidation(monkeypatch, tmp_path):
    import bot

    monkeypatch.chdir(tmp_path)
    bot._static_cache.clear()
    page = "<html>" + "каталог " * 400 + "</html>"
    (tmp_path / "index.html").write_text(page, encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG" + b"0" * 2000)

    async def steps(client):
        r = await client.get("/", headers={"Accept-Encoding": "gzip"})
        assert r.status == 200 and r.headers["Content-Encoding"] == "gzip"
        assert r.headers["Cache-Control"] == "no-cache" and "Accept-Encoding" in r.headers["Vary"]
        assert await r.text() == page
        etag = r.headers["ETag"]

        r = await client.get("/", headers={"If-None-Match": etag})
        assert r.status == 304 and not await r.read()

        r = await client.get("/", headers={"Accept-Encoding": "identity"})
        assert r.status == 200 and "Content-Encoding" not in r.headers and await r.text() == page

        (tmp_path / "index.html").write_text(page + "<!-- v2 -->", encoding="utf-8")
        r = await client.get("/", headers={"If-None-Match": etag})
        assert r.status == 200 and r.headers["ETag"] != etag and (await r.text()).endswith("<!-- v2 -->")

        r = await client.get("/logo.png")
        assert r.status == 200 and r.headers["Cache-Control"] == "public, max-age=86400"
        assert "Content-Encoding" not in r.headers

        assert (await client.get("/missing.png")).status == 404
        assert (await client.get("/bot.py")).status == 403

    _run([("GET", "/", bot.handle_index), ("GET", "/{path:.*}", bot.handle_static)], steps)


# ─── Фото: «бомба» не розпаковується, великий JPEG стискається ──

def test_decompression_bomb_is_not_decoded(monkeypatch):
    import bot
    from PIL import Image

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    out = io.BytesIO()
    Image.new("RGB", (200, 200), "white").save(out, format="PNG")
    data = out.getvalue()
    # Понад ліміт Pillow кидає виняток на відкритті; файл повертається без локальної обробки
    assert bot.prepare_static_for_upload(data, "image/png") == (data, "image/png")
    assert bot.is_animated_image(data, "image/webp") is False


def test_large_jpeg_is_downscaled():
    import bot
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (4000, 3000), (10, 120, 200)).save(out, format="JPEG", quality=90)
    optimized, content_type = bot.prepare_static_for_upload(out.getvalue(), "image/jpeg")
    assert content_type == "image/jpeg"
    with Image.open(io.BytesIO(optimized)) as img:
        assert img.size == (bot.STATIC_MAX_WIDTH, 900)


def test_cloudinary_timeout_does_not_wait_for_upload(monkeypatch):
    import concurrent.futures

    import bot

    monkeypatch.setattr(bot, "CLOUDINARY_UPLOAD_TIMEOUT", 0.05)
    monkeypatch.setattr(bot.cloudinary.uploader, "upload", lambda *a, **kw: time.sleep(1.5))
    started = time.monotonic()
    with pytest.raises(concurrent.futures.TimeoutError):
        bot._cloudinary_upload_with_timeout(b"x", {})
    assert time.monotonic() - started < 1.0
