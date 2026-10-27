"""Картинки акцій 1080×1080: параметри з налаштувань акції, рендер усіх сцен, превʼю й публікація в канал."""

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

import services.promo_cards as pcs  # noqa: E402
from test_channel_posts import FakeBot, _http, env  # noqa: E402,F401


def _open(png: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(png))
    img.load()
    return img


CATS = {"key": "Брелки", "toy": "Іграшки"}


def _defaults(**promo):
    return pcs.promo_defaults(promo, CATS)


def test_defaults_order_percent_uses_threshold_and_end_date():
    p = _defaults(id="order_10_percent", type="order_percent", title="🔖 −10% на замовлення",
                  subtitle="Знижка на всю суму", emoji="🔖", value=10, min_order=500, ends_at="2026-12-01T10:00:00+00:00")
    assert p["big"] == "−10%" and p["caption"] == "на замовлення"
    assert p["condition"] == "від 500 ₴ · до 01.12"
    assert p["title"] == "−10% на замовлення"  # емодзі прибрано
    assert p["accent"] == "blue" and p["motif"] == "percent"


def test_defaults_order_fixed_and_no_threshold():
    p = _defaults(id="x1", type="order_fixed", title="Знижка", value=50, min_order=0)
    assert p["big"] == "−50 ₴" and p["condition"] == "на всі замовлення"


def test_defaults_category_percent_names_categories():
    p = _defaults(id="x2", type="cat_percent", title="Брелки", value=15, category="key,toy", min_order=0)
    assert p["big"] == "−15%" and p["caption"] == "Брелки, Іграшки" and p["condition"] == "Брелки, Іграшки"


def test_defaults_gift_and_free_ship_and_info():
    g = _defaults(id="gift_3_toys", type="gift_threshold", title="Подарунок", min_items=3, category="key")
    assert g["big"] == "+1" and g["condition"] == "від 3 товарів · Брелки"
    assert g["accent"] == "orange" and g["motif"] == "gift" and g["tag"] == "ПОДАРУНОК"
    f = _defaults(id="free", type="free_ship", title="Доставка", min_order=1000)
    assert f["big"] == "0 ₴" and f["condition"] == "від 1 000 ₴" and f["accent"] == "green" and f["tag"] == "ДОСТАВКА"
    i = _defaults(id="news", type="info", title="Новини", badge="🔥 NEW")
    assert i["big"] == "NEW" and i["condition"] == ""


def test_defaults_accent_from_promo_and_motif_detection():
    assert _defaults(id="a1", type="order_percent", value=5, accent="pink")["accent"] == "pink"
    assert _defaults(id="a2", type="order_percent", value=5, accent="bogus")["accent"] == "blue"
    assert _defaults(id="newyear_20", type="order_percent", value=20)["motif"] == "newyear"
    assert _defaults(id="st_nicholas_gift", type="gift_threshold")["motif"] == "nikolay"
    assert _defaults(id="zz", type="order_percent", value=5, emoji="🌷")["motif"] == "spring"
    assert _defaults(id="zz", type="free_ship")["motif"] == "delivery"


def test_normalize_overrides_and_validation():
    promo = {"id": "order_10_percent", "type": "order_percent", "title": "T", "value": 10, "min_order": 500}
    params, err = pcs.normalize_promo_params({"title": "Мій заголовок", "tag": "нова", "motif": "summer", "accent": "amber",
                                              "condition": "x" * 200}, promo, CATS)
    assert not err
    assert params["title"] == "Мій заголовок" and params["tag"] == "НОВА"
    assert params["motif"] == "summer" and params["accent"] == "amber"
    assert len(params["condition"]) <= pcs.MAX["condition"]
    assert pcs.normalize_promo_params({"motif": "nope"}, promo, CATS)[1]
    assert pcs.normalize_promo_params({"accent": "nope"}, promo, CATS)[1]
    assert pcs.normalize_promo_params("junk", promo, CATS)[1]
    base, err = pcs.normalize_promo_params(None, promo, CATS)
    assert not err and base["big"] == "−10%"


def test_default_post_text():
    promo = {"id": "a", "type": "order_percent", "title": "T", "emoji": "🔖", "value": 10, "min_order": 500, "subtitle": "Опис"}
    text = pcs.default_post_text(promo, pcs.promo_defaults(promo))
    assert text.splitlines()[0] == "🔖 T"
    assert "Опис" in text and "📌 від 500 ₴" in text


@pytest.mark.parametrize("motif", list(pcs.MOTIFS))
def test_render_every_motif(motif):
    png = pcs.render_promo_card(tag="АКЦІЯ", big="−10%", caption="на замовлення", title="Заголовок акції",
                                subtitle="Опис акції у два-три рядки, щоб перевірити перенесення тексту", condition="від 500 ₴",
                                accent="violet", motif=motif)
    assert _open(png).size == (1080, 1080)


def test_render_survives_odd_text():
    png = pcs.render_promo_card(tag="", big="", caption="", title="Дуже довга назва акції " * 6,
                                subtitle="дуже довгий підзаголовок " * 12, condition="умова " * 20, accent="nope", motif="nope")
    assert _open(png).size == (1080, 1080)


# ─── HTTP ───────────────────────────────────────────────────────

def test_http_promo_channel_info_and_preview(env, monkeypatch):
    async def steps(client, adm):
        info_url = "/api/admin/promotions/order_10_percent/channel-post"
        card_url = "/api/admin/promotions/order_10_percent/channel-card"
        assert (await client.get(info_url)).status == 403
        assert (await client.post(card_url, json={})).status == 403
        assert (await client.get("/api/admin/promotions/nope/channel-post", headers=adm)).status == 404
        r = await client.get(info_url, headers=adm)
        info = await r.json()
        assert r.status == 200 and info["ok"] and info["params"]["big"] == "−10%"
        assert info["params"]["condition"].startswith("від 500 ₴")
        assert info["text"] and {m["key"] for m in info["motifs"]} == set(pcs.MOTIFS)
        assert (await client.post(card_url, data="junk", headers=adm)).status == 400
        assert (await client.post(card_url, json={"motif": "bogus"}, headers=adm)).status == 400
        assert (await client.post("/api/admin/promotions/nope/channel-card", json={}, headers=adm)).status == 404
        r = await client.post(card_url, json={"title": "Свій заголовок", "motif": "summer"}, headers=adm)
        assert r.status == 200 and r.headers["Content-Type"] == "image/png" and r.headers["Cache-Control"] == "no-store"
        return await r.read()

    assert _open(_http(env, monkeypatch, steps, FakeBot())).size == (1080, 1080)


def test_http_publish_promo_post(env, monkeypatch):
    fake = FakeBot()

    async def steps(client, adm):
        url = "/api/admin/promotions/order_10_percent/channel-post"
        body = {"channel": "@chan", "text": "🔖 −10% на замовлення\n\nУмови", "card": {"motif": "summer"}, "pin": True}
        assert (await client.post(url, json=body)).status == 403
        assert (await client.post(url, json={**body, "channel": ""}, headers=adm)).status == 400
        assert (await client.post(url, json={**body, "card": {"motif": "bogus"}}, headers=adm)).status == 400
        assert (await client.post(url, json={**body, "text": "x" * 1025}, headers=adm)).status == 400
        assert (await client.post("/api/admin/promotions/nope/channel-post", json=body, headers=adm)).status == 404
        r = await client.post(url, json=body, headers=adm)
        return r.status, await r.json()

    status, data = _http(env, monkeypatch, steps, fake)
    assert status == 200 and data["ok"] and data["channel"] == "@chan"
    assert [k for k, _ in fake.calls][0] == "photo"
    kw = fake.calls[0][1]
    assert kw["photo"].filename == "promo.png"
    assert kw["caption"].startswith("<b>🔖 −10% на замовлення</b>")
    assert kw["reply_markup"].inline_keyboard[0][0].url.endswith("startapp=promo")
    assert _open(kw["photo"].input_file_content).size == (1080, 1080)
