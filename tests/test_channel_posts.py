"""Публікація товару в канал з адмінки: канал, текст, кнопки з міткою ch_<id>, переходи, admin-only."""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")

import services.channel_posts as cp  # noqa: E402
from db_core import db_connect  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "shop.db"))
    from services.db_utils import init_db

    init_db()
    yield tmp_path


@pytest.mark.parametrize("raw,expected", [
    ("@poselyanov3d", "@poselyanov3d"),
    ("poselyanov3d", "@poselyanov3d"),
    ("https://t.me/poselyanov3d", "@poselyanov3d"),
    ("t.me/poselyanov3d/", "@poselyanov3d"),
    ("-1001234567890", "-1001234567890"),
    ("", ""),
    ("@ab", None),
    ("hello world", None),
    ("-12345", None),
])
def test_normalize_channel(raw, expected):
    assert cp.normalize_channel(raw) == expected


def test_text_limits_and_html():
    assert cp.validate_text("  ", True)[1]
    assert cp.validate_text("x" * 1025, True)[1]
    assert cp.validate_text("x" * 1025, False) == ("x" * 1025, "")
    assert cp.caption_html("🆕 <Новинка>\nТекст & ціна") == "<b>🆕 &lt;Новинка&gt;</b>\nТекст &amp; ціна"


def test_links_carry_post_mark():
    assert cp.product_link(7, 42).endswith("?startapp=ch_7_product_42")
    assert cp.shop_link(7).endswith("?startapp=ch_7")
    assert cp.post_url("@chan", 15) == "https://t.me/chan/15"
    assert cp.post_url("-1001234567890", 15) == "https://t.me/c/1234567890/15"


def test_clicks_counted_once_per_user(env):
    pid = cp.create_post(42, "@chan", "Текст")
    assert not cp.record_click(1, pid)  # ще не відправлено
    cp.set_message_id(pid, 100)
    assert cp.record_click(1, pid)
    assert not cp.record_click(1, pid)
    assert cp.record_click(2, pid)
    assert not cp.record_click(1, 9999)
    posts = cp.list_product_posts(42)
    assert posts[0]["clicks"] == 2 and posts[0]["url"] == "https://t.me/chan/100"


class FakeBot:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def send_photo(self, **kw):
        if self.fail:
            raise Exception(self.fail)
        self.calls.append(("photo", kw))
        return SimpleNamespace(message_id=55)

    async def send_message(self, **kw):
        self.calls.append(("message", kw))
        return SimpleNamespace(message_id=56)

    async def send_media_group(self, **kw):
        self.calls.append(("album", kw))
        return [SimpleNamespace(message_id=70), SimpleNamespace(message_id=71)]

    async def pin_chat_message(self, **kw):
        self.calls.append(("pin", kw))


def _http(env, monkeypatch, steps, fake_bot):
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
    monkeypatch.setattr(bot, "bot_app", SimpleNamespace(bot=fake_bot))
    products = {42: {"id": 42, "name": "Дракон", "photos": ["https://res.cloudinary.com/x/image/upload/v1/a.gif"]}}
    monkeypatch.setattr(bot, "get_product_by_id", lambda pid: products.get(pid))

    async def go():
        app = web.Application()
        register_http_routes(app, bot)
        async with TestClient(TestServer(app)) as client:
            return await steps(client, {"X-Admin-Bypass-Token": "tok"})

    return asyncio.run(go())


def test_http_publish_post(env, monkeypatch):
    fake = FakeBot()

    async def steps(client, adm):
        url = "/api/admin/products/42/channel-posts"
        assert (await client.get(url)).status == 403
        assert (await client.post(url, json={})).status == 403
        r = await client.post(url, json={"channel": "bad channel", "text": "x"}, headers=adm)
        assert r.status == 400
        r = await client.post("/api/admin/products/1/channel-posts", json={"channel": "@chan", "text": "x"}, headers=adm)
        assert r.status == 404
        r = await client.post(url, json={"channel": "t.me/mychan", "text": "🆕 Новинка\nДракон", "silent": True, "pin": True}, headers=adm)
        data = await r.json()
        assert r.status == 200 and data["ok"] and data["pinned"] and data["url"] == "https://t.me/mychan/55"
        info = await (await client.get(url, headers=adm)).json()
        assert info["channel"] == "@mychan" and len(info["posts"]) == 1
        return data["id"]

    post_id = _http(env, monkeypatch, steps, fake)
    kind, kw = fake.calls[0]
    assert kind == "photo" and kw["chat_id"] == "@mychan" and kw["disable_notification"] is True
    assert "/upload/f_jpg," in kw["photo"]  # з GIF береться перший кадр
    assert kw["caption"].startswith("<b>🆕 Новинка</b>")
    buttons = kw["reply_markup"].inline_keyboard[0]
    assert buttons[0].url.endswith(f"startapp=ch_{post_id}_product_42")
    assert buttons[1].url.endswith(f"startapp=ch_{post_id}")
    assert fake.calls[1][0] == "pin"


def test_http_publish_album(env, monkeypatch):
    fake = FakeBot()
    import bot

    async def steps(client, adm):
        monkeypatch.setattr(bot, "get_product_by_id", lambda pid: {"id": 42, "name": "Д", "photos": ["https://x/a.jpg", "https://x/b.jpg"]})
        r = await client.post("/api/admin/products/42/channel-posts", json={"channel": "@chan", "text": "Т\nх"}, headers=adm)
        return await r.json()

    data = _http(env, monkeypatch, steps, fake)
    assert data["url"] == "https://t.me/chan/70"
    assert [c[0] for c in fake.calls] == ["album", "message"]
    assert len(fake.calls[0][1]["media"]) == 2 and fake.calls[0][1]["media"][0].caption.startswith("<b>Т</b>")
    assert fake.calls[1][1]["reply_markup"].inline_keyboard[0][0].url.endswith(f"_product_42")


def test_http_publish_one_photo_of_many(env, monkeypatch):
    fake = FakeBot()
    import bot

    async def steps(client, adm):
        monkeypatch.setattr(bot, "get_product_by_id", lambda pid: {"id": 42, "name": "Д", "photos": ["https://x/a.jpg", "https://x/b.jpg"]})
        r = await client.post("/api/admin/products/42/channel-posts", json={"channel": "@chan", "text": "Т", "photos_mode": "one", "photo_index": 1}, headers=adm)
        return await r.json()

    assert _http(env, monkeypatch, steps, fake)["ok"]
    assert [c[0] for c in fake.calls] == ["photo"] and fake.calls[0][1]["photo"] == "https://x/b.jpg"


def test_default_channel(env):
    assert cp.get_channel() == "@poselyanov3dprint"
    cp.set_channel("@other")
    assert cp.get_channel() == "@other"


def test_http_publish_failure_leaves_no_post(env, monkeypatch):
    fake = FakeBot(fail="Bad Request: chat not found")

    async def steps(client, adm):
        r = await client.post("/api/admin/products/42/channel-posts", json={"channel": "@nochan", "text": "x"}, headers=adm)
        data = await r.json()
        assert r.status == 502 and "Канал не знайдено" in data["error"]
        return True

    assert _http(env, monkeypatch, steps, fake)
    conn = db_connect()
    assert conn.execute("SELECT COUNT(*) FROM channel_posts").fetchone()[0] == 0
    conn.close()
    assert cp.get_channel() == cp.DEFAULT_CHANNEL  # невдалий канал не запам'ятовується


def test_http_publish_error_log_hides_token(env, monkeypatch, caplog):
    import bot

    monkeypatch.setattr(bot, "BOT_TOKEN", "123:SECRET")
    fake = FakeBot(fail="Network error: https://api.telegram.org/bot123:SECRET/sendPhoto")

    async def steps(client, adm):
        r = await client.post("/api/admin/products/42/channel-posts", json={"channel": "@chan", "text": "x"}, headers=adm)
        return r.status

    with caplog.at_level("WARNING"):
        assert _http(env, monkeypatch, steps, fake) == 502
    assert "SECRET" not in caplog.text and "bot***" in caplog.text
