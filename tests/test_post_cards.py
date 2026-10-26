"""Картки товару 1080×1080: рендерер, нормалізація параметрів, превʼю й публікація картки в канал."""

import io
import os
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")

import services.post_cards as pc  # noqa: E402
from test_channel_posts import FakeBot, _http, env  # noqa: E402,F401


def _jpeg(size=(900, 600), color=(200, 120, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def _open(png: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(png))
    img.load()
    return img


@pytest.mark.parametrize("template", ["new", "sale", "custom"])
@pytest.mark.parametrize("with_photo", [True, False])
def test_render_size_and_format(template, with_photo):
    png = pc.render_card(
        _jpeg() if with_photo else None, template=template, title="Лампа-місяць",
        price="490 ₴", old_price="590 ₴", badge="−17%",
    )
    img = _open(png)
    assert img.format == "PNG" and img.size == (1080, 1080)


def test_render_photo_changes_image():
    a = _open(pc.render_card(None, template="new", title="Т", price="1 ₴"))
    b = _open(pc.render_card(_jpeg(), template="new", title="Т", price="1 ₴"))
    assert a.convert("RGB").getpixel((540, 300)) != b.convert("RGB").getpixel((540, 300))
    assert b.convert("RGB").getpixel((540, 300)) != (255, 255, 255)


def test_render_other_photo_formats_and_exif():
    for fmt in ("PNG", "WEBP", "GIF"):
        buf = io.BytesIO()
        Image.new("RGBA", (300, 200), (10, 200, 30, 128)).save(buf, fmt)
        assert _open(pc.render_card(buf.getvalue(), template="new", title="x")).size == (1080, 1080)
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    Image.new("RGB", (400, 100), (255, 0, 0)).save(buf, "JPEG", exif=exif)
    assert _open(pc.render_card(buf.getvalue(), template="new", title="x")).size == (1080, 1080)


@pytest.mark.parametrize("bad", [b"", b"not an image", b"\xff\xd8\xff garbage", None])
def test_bad_photo_falls_back_to_placeholder(bad):
    png = pc.render_card(bad, template="new", title="Т", price="1 ₴")
    assert _open(png).size == (1080, 1080)


def test_huge_photo_rejected(monkeypatch):
    monkeypatch.setattr(pc, "MAX_PHOTO_PIXELS", 100)
    assert pc._decode_photo(_jpeg((5, 5))) is not None
    assert pc._decode_photo(_jpeg((200, 200))) is None


@pytest.mark.parametrize("title", [
    "", "   ", "Д" * 400, "Слово " * 60, "ДовгеСловоБезПробілів" * 10,
    "Дракон <b>&</b> 🐉 емодзі", "\x00\x01 керуючі ‮ символи",
])
def test_odd_titles_never_raise(title):
    png = pc.render_card(None, template="sale", title=title, price="390 ₴", old_price="490 ₴", badge="−20%")
    assert _open(png).size == (1080, 1080)


@pytest.mark.parametrize("price", ["", "abc", "1 000 000 000 000 000 000 ₴", "₴" * 50, "-5", "0", "12,5", None])
def test_weird_prices_never_raise(price):
    png = pc.render_card(None, template="sale", title="Т", price=price, old_price=price, badge="x" * 30, tag="т" * 80)
    assert _open(png).size == (1080, 1080)


def test_unknown_template_falls_back():
    assert _open(pc.render_card(None, template="zzz", title="Т")).size == (1080, 1080)


def test_title_fit_and_ellipsis():
    lines, size = pc._fit_title("Лампа", 700)
    assert lines == ["Лампа"] and size == 56
    lines, size = pc._fit_title("Дуже довга назва " * 12, 700)
    assert len(lines) <= 2 and size == 36 and lines[-1].endswith("…")


def test_hryvnia_glyph_has_fallback():
    assert not pc._has_glyph("CodecPro-Bold.ttf", "₴")
    assert pc._has_glyph("GolosText-Bold.ttf", "₴")
    assert pc._text_width("₴", "CodecPro-Bold.ttf", "GolosText-Bold.ttf", 50) > 10


def test_card_templates():
    assert pc.card_templates() == ["new", "sale", "custom"]


@pytest.mark.parametrize("price,old,badge", [
    ("390 ₴", "490 ₴", "−20%"),
    ("1 490", "1 990", "−25%"),
    ("100,50", "200,00", "−50%"),
    ("490 ₴", "390 ₴", ""),
    ("490 ₴", "490 ₴", ""),
    ("abc", "490", ""),
    ("390", "", ""),
])
def test_discount_badge(price, old, badge):
    params, err = pc.normalize_card_params({"template": "sale", "title": "Т", "price": price, "old_price": old})
    assert not err and params["badge"] == badge


def test_normalize_params():
    assert pc.normalize_card_params("x")[1]
    assert pc.normalize_card_params({"template": "evil"})[1]
    p, err = pc.normalize_card_params({
        "template": "custom", "title": "A\x00B\n" + "x" * 300, "price": "9" * 100, "old_price": "5", "photo_index": "abc",
    })
    assert not err
    assert "\x00" not in p["title"] and len(p["title"]) <= 120
    assert len(p["price"]) <= 24 and p["old_price"] == "" and p["badge"] == "" and p["photo_index"] == 0
    p, _ = pc.normalize_card_params({"template": "custom", "title": "Т", "price": "5", "photo_index": -3})
    assert p["photo_index"] == 0
    p, _ = pc.normalize_card_params({"title": None, "price": {"a": 1}})
    assert p["template"] == "new" and p["title"] == "" and p["price"] == ""


def test_render_is_fast():
    import time

    pc.render_card(None, template="new", title="Т")  # прогрів кешів
    t = time.time()
    pc.render_card(_jpeg((2000, 1500)), template="sale", title="Лампа-місяць", price="390 ₴", old_price="490 ₴", badge="−20%")
    assert time.time() - t < 2.0


# ─── HTTP ───────────────────────────────────────────────────────

@pytest.fixture
def fake_download(monkeypatch):
    import bot

    calls = []

    async def fake(url):
        calls.append(url)
        return _jpeg()

    monkeypatch.setattr(bot, "_download_card_photo", fake)
    return calls


def test_http_card_preview(env, monkeypatch, fake_download):
    fake = FakeBot()

    async def steps(client, adm):
        url = "/api/admin/products/42/channel-card"
        body = {"template": "sale", "title": "Дракон", "price": "390 ₴", "old_price": "490 ₴", "photo_index": 0}
        assert (await client.post(url, json=body)).status == 403
        r = await client.post(url, json={"template": "bogus"}, headers=adm)
        assert r.status == 400 and not (await r.json())["ok"]
        r = await client.post(url, data="not json", headers=adm)
        assert r.status == 400
        r = await client.post("/api/admin/products/1/channel-card", json=body, headers=adm)
        assert r.status == 404
        r = await client.post(url, json=body, headers=adm)
        assert r.status == 200 and r.headers["Content-Type"] == "image/png"
        assert r.headers["Cache-Control"] == "no-store"
        return await r.read()

    png = _http(env, monkeypatch, steps, fake)
    assert _open(png).size == (1080, 1080)
    assert fake_download == ["https://res.cloudinary.com/x/image/upload/v1/a.gif"]
    assert fake.calls == []


def test_http_card_preview_download_failure_uses_placeholder(env, monkeypatch):
    import bot

    async def fail(url):
        return None

    monkeypatch.setattr(bot, "_download_card_photo", fail)

    async def steps(client, adm):
        r = await client.post("/api/admin/products/42/channel-card", json={"template": "new", "title": ""}, headers=adm)
        assert r.status == 200
        return await r.read()

    assert _open(_http(env, monkeypatch, steps, FakeBot())).size == (1080, 1080)


def test_card_photo_url_and_scheme_guard():
    import asyncio
    import bot

    assert "/upload/f_jpg,w_1400,c_limit/" in bot._card_photo_url("https://res.cloudinary.com/x/image/upload/v1/a.png")
    assert bot._card_photo_url("https://x/a.jpg") == "https://x/a.jpg"
    assert asyncio.run(bot._download_card_photo("file:///etc/passwd")) is None
    assert asyncio.run(bot._download_card_photo("ftp://x/a.jpg")) is None


def test_http_publish_with_card(env, monkeypatch, fake_download):
    fake = FakeBot()
    import bot

    async def steps(client, adm):
        monkeypatch.setattr(bot, "get_product_by_id", lambda pid: {"id": 42, "name": "Д", "photos": ["https://x/a.jpg", "https://x/b.jpg"]})
        url = "/api/admin/products/42/channel-posts"
        r = await client.post(url, json={"channel": "@chan", "text": "x", "card": {"template": "nope"}}, headers=adm)
        assert r.status == 400
        r = await client.post(url, json={
            "channel": "@chan", "text": "🆕 Новинка\nД", "photos_mode": "all",
            "card": {"template": "new", "title": "Д", "price": "10 ₴", "photo_index": 1},
        }, headers=adm)
        return r.status, await r.json()

    status, data = _http(env, monkeypatch, steps, fake)
    assert status == 200 and data["ok"]
    assert [k for k, _ in fake.calls] == ["photo"]  # один знімок, без альбому
    kw = fake.calls[0][1]
    assert kw["photo"].filename == "card.png"
    assert kw["caption"].startswith("<b>🆕 Новинка</b>")
    assert kw["reply_markup"].inline_keyboard[0][0].url.endswith(f"startapp=ch_{data['id']}_product_42")
    assert fake_download == ["https://x/b.jpg"]
    assert _open(kw["photo"].input_file_content).size == (1080, 1080)


def test_http_publish_card_caption_limit(env, monkeypatch, fake_download):
    async def steps(client, adm):
        r = await client.post("/api/admin/products/42/channel-posts", json={
            "channel": "@chan", "text": "x" * 1025, "card": {"template": "new", "title": "Д"},
        }, headers=adm)
        return r.status

    assert _http(env, monkeypatch, steps, FakeBot()) == 400


def test_download_card_photo_rejects_private_hosts():
    """SSRF: приватні/loopback адреси не завантажуються навіть для http(s) URL."""
    import asyncio
    import bot

    for url in ("http://127.0.0.1/x.jpg", "http://localhost/x.jpg", "http://169.254.169.254/latest/meta-data", "ftp://example.com/x.jpg"):
        assert asyncio.run(bot._download_card_photo(url)) is None


def test_new_template_keeps_discount_when_old_price_given():
    """Товар зі знижкою, що публікується як «Новинка»: стара ціна й бейдж лишаються на картинці."""
    params, err = pc.normalize_card_params({"template": "new", "title": "Лампа", "price": "390 ₴", "old_price": "490 ₴"})
    assert not err and params["old_price"] == "490 ₴" and params["badge"] == "−20%"
    png = pc.render_card(None, template="new", title="Лампа", price="390 ₴", old_price="490 ₴", badge=params["badge"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    params, _ = pc.normalize_card_params({"template": "new", "title": "Лампа", "price": "490 ₴", "old_price": ""})
    assert params["badge"] == "" and params["old_price"] == ""
