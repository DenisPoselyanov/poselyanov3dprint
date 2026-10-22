"""Етап 5, слайс 2: порядок категорій, ID з назви, кольори філаменту (створення, видалення, glow, використання)."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")

import catalog_store as cs  # noqa: E402
from services.slugs import slugify_ua, unique_slug  # noqa: E402


@pytest.fixture
def shop(monkeypatch, tmp_path):
    import config

    (tmp_path / "products.json").write_text("[]", encoding="utf-8")
    (tmp_path / "custom_products.json").write_text("[]", encoding="utf-8")
    (tmp_path / "categories.json").write_text(json.dumps([
        {"id": "key", "name": "Брелки", "emoji": "🔑", "order": 1, "active": True, "quickSlot": 1},
        {"id": "toy", "name": "Іграшки", "emoji": "🧸", "order": 2, "active": True, "quickSlot": 2},
        {"id": "off", "name": "Вимкнена", "emoji": "x", "order": 3, "active": False},
        {"id": "custom", "name": "Індивідуальне", "emoji": "✏️", "order": 4, "active": True},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "filaments.json").write_text(json.dumps([
        {"id": "white", "name": "Білий", "hex": "#f0f0f0", "available": True},
        {"id": "luminous", "name": "Світний", "hex": "#c8f7c5", "available": True},
        {"id": "lilac", "name": "Бузковий", "hex": "#b39ddb", "available": False, "glow": False},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "CATALOG_BACKEND", "json")
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "shop.db"))
    monkeypatch.setattr(config, "PRODUCTS_FILE", str(tmp_path / "products.json"))
    monkeypatch.setattr(config, "CUSTOM_PRODUCTS_FILE", str(tmp_path / "custom_products.json"))
    monkeypatch.setattr(config, "CATEGORIES_FILE", str(tmp_path / "categories.json"))
    monkeypatch.setattr(config, "FILAMENTS_FILE", str(tmp_path / "filaments.json"))
    monkeypatch.setattr(cs, "use_catalog_db", lambda: False)
    from services.db_utils import init_db

    init_db()
    cs.reload_categories_cache(force=True)
    cs.reload_filaments_cache(force=True)
    cs.reload_products_cache(force=True)
    yield tmp_path
    for cache in (cs.PRODUCTS_CACHE, cs.CUSTOM_PRODUCTS_CACHE, cs.INACTIVE_PRODUCTS_CACHE,
                  cs.INACTIVE_CUSTOM_PRODUCTS_CACHE, cs.CATEGORIES_CACHE, cs.FILAMENTS_CACHE):
        cache.clear()


def _order_on_disk(tmp_path):
    rows = json.loads((tmp_path / "categories.json").read_text(encoding="utf-8"))
    return [r["id"] for r in sorted(rows, key=lambda r: r["order"])]


def _filaments_on_disk(tmp_path):
    return {f["id"]: f for f in json.loads((tmp_path / "filaments.json").read_text(encoding="utf-8"))}


# ─── Транслітерація ──────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Брелки", "brelky"),
    ("Іграшки для дітей", "ihrashky-dlia-ditei"),
    ("Щука й ґанок", "shchuka-i-ganok"),
    ("Мʼякі ІГРАШКИ", "miaki-ihrashky"),
    ("  3D-принт!  ", "3d-prynt"),
    ("Ялинка, Є", "ialynka-ie"),
])
def test_slugify_ua(text, expected):
    assert slugify_ua(text) == expected


def test_slugify_ua_fallback_and_limit():
    assert slugify_ua("???", fallback="category") == "category"
    assert slugify_ua("") == "item"
    assert len(slugify_ua("а" * 100)) <= 32
    assert unique_slug("Брелки", ["brelky", "brelky-2"]) == "brelky-3"
    assert unique_slug("Брелки", []) == "brelky"


# ─── Категорії: id з назви, слоти, порядок ───────────────────

def test_add_category_generates_unique_id(shop):
    first = cs.add_category({"name": "Брелки для ключів", "emoji": "🗝️"})
    assert first["ok"] and first["category"]["id"] == "brelky-dlia-kliuchiv"
    again = cs.add_category({"name": "Брелки для ключів", "emoji": "🗝️"})
    assert again["ok"] and again["category"]["id"] == "brelky-dlia-kliuchiv-2"
    # явний ID, як і раніше, працює; без назви — відмова
    assert cs.add_category({"id": "own", "name": "Своя"})["ok"]
    assert not cs.add_category({"id": "x", "name": ""})["ok"]


def test_quick_slot_is_exclusive_and_validated(shop):
    res = cs.add_category({"name": "Нова", "quickSlot": 1})
    assert res["ok"]
    slots = {c["id"]: c.get("quickSlot") for c in cs.get_all_categories(active_only=False)}
    assert slots["nova"] == 1 and slots["key"] is None and slots["toy"] == 2
    assert cs.update_category("toy", {"quickSlot": 1})["ok"]
    slots = {c["id"]: c.get("quickSlot") for c in cs.get_all_categories(active_only=False)}
    assert slots["toy"] == 1 and slots["nova"] is None
    assert not cs.update_category("toy", {"quickSlot": 9})["ok"]
    assert cs.update_category("toy", {"quickSlot": None})["ok"]


def test_reorder_categories_persists_and_keeps_rest(shop):
    res = cs.reorder_categories(["toy", "custom", "key"])
    assert res["ok"] and res["order"] == ["toy", "custom", "key", "off"]
    assert _order_on_disk(shop) == ["toy", "custom", "key", "off"]
    assert [c["id"] for c in cs.get_all_categories(active_only=False)] == ["toy", "custom", "key", "off"]
    # публічний список: лише активні, але в новому порядку
    assert [c["id"] for c in cs.get_all_categories()] == ["toy", "custom", "key"]


@pytest.mark.parametrize("bad", [None, [], "key", ["key", "key"], ["key", "nope"], [1, {}]])
def test_reorder_categories_rejects_bad_input(shop, bad):
    before = _order_on_disk(shop)
    assert not cs.reorder_categories(bad)["ok"]
    assert _order_on_disk(shop) == before


# ─── Філаменти ───────────────────────────────────────────────

def test_add_filament_validates_and_generates_id(shop):
    res = cs.add_filament({"name": "Темно-зелений", "hex": "#1b5e20", "glow": False})
    assert res["ok"] and res["filament"]["id"] == "temno-zelenyi"
    assert res["filament"]["available"] is True and res["filament"]["glow"] is False
    assert _filaments_on_disk(shop)["temno-zelenyi"]["hex"] == "#1b5e20"
    # синхронізовано з таблицею, на яку посилаються замовлення
    from db_core import db_connect
    conn = db_connect()
    row = conn.execute("SELECT name FROM filament_colors WHERE id = ?", ("temno-zelenyi",)).fetchone()
    conn.close()
    assert row is not None

    assert not cs.add_filament({"name": "", "hex": "#fff"})["ok"]
    assert not cs.add_filament({"name": "Кривий", "hex": "red"})["ok"]
    assert not cs.add_filament({"name": "білий", "hex": "#ffffff"})["ok"]  # така назва вже є
    assert not cs.add_filament({"id": "white", "name": "Інший", "hex": "#fff"})["ok"]
    assert not cs.add_filament({"id": "Bad Id!", "name": "Інший", "hex": "#fff"})["ok"]


def test_glow_flag_roundtrip_and_legacy_default(shop):
    by_id = {f["id"]: f for f in cs.FILAMENTS_CACHE}
    # старі записи без glow: світним лишається luminous*
    assert cs.is_glow_filament(by_id["luminous"]) and not cs.is_glow_filament(by_id["white"])
    assert cs.update_filament("white", {"glow": True})["filament"]["glow"] is True
    assert _filaments_on_disk(shop)["white"]["glow"] is True
    assert cs.update_filament("white", {"glow": False})["filament"]["glow"] is False
    # явне glow=false перебиває правило за id
    assert cs.update_filament("luminous", {"glow": False})["filament"]["glow"] is False
    assert not cs.is_glow_filament(cs.get_filament_by_id("luminous"))
    # звичайне оновлення не губить glow старого запису
    assert cs.update_filament("lilac", {"available": True})["filament"]["glow"] is False
    assert not cs.update_filament("white", {"hex": "zzz"})["ok"]


def _add_product(**extra):
    res = cs.add_product({"cat": "key", "name": "Брелок", "price": 100, "mat": "PLA", **extra})
    assert res["ok"], res
    return res["id"]


def test_filament_usage_counts(shop):
    assert cs.filament_usage_counts() == {"white": 0, "luminous": 0, "lilac": 0}
    a = _add_product()
    _add_product(luminousFilamentChoice=True)
    _add_product(filamentChoice=False)
    c = _add_product()
    cs.set_product_status(c, "hidden")  # приховані рахуються
    d = _add_product()
    cs.set_product_status(d, "archive")  # архів — ні
    _add_product(cat="custom", name="Імʼя", mat="x", custom_fields="Імʼя")  # індивідуальні кольору не мають
    usage = cs.filament_usage_counts()
    assert usage["white"] == 3 and usage["lilac"] == 3  # a, світний товар, прихований
    assert usage["luminous"] == 1
    assert a


def test_delete_filament_refused_when_used_unless_forced(shop):
    _add_product()
    res = cs.delete_filament("white")
    assert not res["ok"] and res["used_by"] == 1
    assert "white" in _filaments_on_disk(shop)

    unused = cs.add_filament({"name": "Рожевий", "hex": "#f48fb1"})["filament"]["id"]
    assert cs.filament_usage_counts()[unused] == 1  # звичайний колір — у всіх товарах з вибором кольору
    cs.set_product_status(next(p["id"] for p in cs.get_admin_products()), "archive")
    res = cs.delete_filament(unused)
    assert res["ok"] and res["used_by"] == 0
    assert unused not in _filaments_on_disk(shop)

    forced = cs.delete_filament("white", force=True)
    assert forced["ok"] and "white" not in _filaments_on_disk(shop)
    assert not cs.delete_filament("white")["ok"]  # уже немає


def test_glow_filament_validation_in_orders(shop):
    """Колір із glow=true недоступний товару без luminousFilamentChoice (як раніше для luminous*)."""
    from services.validation import is_glow_filament

    assert cs.update_filament("lilac", {"glow": True, "available": True})["ok"]
    assert is_glow_filament(cs.get_filament_by_id("lilac"))
    assert not is_glow_filament(cs.get_filament_by_id("white"))


# ─── HTTP ────────────────────────────────────────────────────

@pytest.fixture
def api(shop, monkeypatch):
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

    def run(coro_fn):
        async def go():
            app = web.Application()
            register_http_routes(app, bot)
            async with TestClient(TestServer(app)) as client:
                return await coro_fn(client)
        return asyncio.run(go())

    return run, {"X-Admin-Bypass-Token": "test-admin-token"}


def test_new_admin_routes_require_admin(api, shop):
    run, _ = api

    async def scenario(client):
        calls = [
            ("put", "/api/categories/order", {"ids": ["toy", "key"]}),
            ("post", "/api/filaments", {"name": "Х", "hex": "#fff"}),
            ("delete", "/api/filaments/white", None),
        ]
        out = []
        for method, url, body in calls:
            resp = await (getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url))
            out.append(resp.status)
        return out

    assert run(scenario) == [403, 403, 403]
    assert _order_on_disk(shop)[:2] == ["key", "toy"]
    assert "white" in _filaments_on_disk(shop)


def test_http_categories_order_and_create_without_id(api, shop):
    run, admin = api

    async def scenario(client):
        # «order» не потрапляє в PUT /api/categories/{id}
        r = await client.put("/api/categories/order", json={"ids": ["custom", "toy", "key"]}, headers=admin)
        assert r.status == 200 and (await r.json())["order"] == ["custom", "toy", "key", "off"]
        r = await client.put("/api/categories/order", json={"ids": ["nope"]}, headers=admin)
        assert r.status == 400
        r = await client.put("/api/categories/order", data="не json", headers=admin)
        assert r.status == 400
        r = await client.post("/api/categories", json={"name": "Підставки", "emoji": "📱"}, headers=admin)
        data = await r.json()
        assert r.status == 201 and data["category"]["id"] == "pidstavky"
        # адмін бачить і вимкнені, публіка — ні
        admin_list = await (await client.get("/api/categories?includeInactive=true", headers=admin)).json()
        assert "off" in [c["id"] for c in admin_list]
        public = await (await client.get("/api/categories")).json()
        assert "off" not in [c["id"] for c in public]
        assert [c["id"] for c in public][:3] == ["custom", "toy", "key"]

    run(scenario)


def test_http_filaments_flow(api, shop):
    run, admin = api
    _add_product()
    _add_product(luminousFilamentChoice=True)

    async def scenario(client):
        pub = await (await client.get("/api/filaments")).json()
        assert all("used_by" not in f for f in pub)  # покупцям — без службових лічильників
        assert {f["id"]: f["glow"] for f in pub} == {"white": False, "luminous": True, "lilac": False}

        rows = await (await client.get("/api/filaments", headers=admin)).json()
        assert {f["id"]: f["used_by"] for f in rows} == {"white": 2, "luminous": 1, "lilac": 2}

        r = await client.post("/api/filaments", json={"name": "Бірюзовий", "hex": "#26c6da", "glow": True, "available": False}, headers=admin)
        data = await r.json()
        assert r.status == 201 and data["filament"]["id"] == "biriuzovyi"
        assert data["filament"]["glow"] is True and data["filament"]["available"] is False
        r = await client.post("/api/filaments", json={"name": "Бірюзовий", "hex": "#26c6da"}, headers=admin)
        assert r.status == 400
        r = await client.post("/api/filaments", json={"name": "Х", "hex": "nope"}, headers=admin)
        assert r.status == 400
        r = await client.post("/api/filaments", json=["не обʼєкт"], headers=admin)
        assert r.status == 400

        r = await client.put("/api/filaments/white", json={"glow": True}, headers=admin)
        assert r.status == 200 and (await r.json())["filament"]["glow"] is True

        r = await client.delete("/api/filaments/white", headers=admin)
        data = await r.json()
        assert r.status == 409 and data["used_by"] == 1 and not data["ok"]  # став світним: лише товар зі світними
        r = await client.delete("/api/filaments/nope", headers=admin)
        assert r.status == 404
        r = await client.delete("/api/filaments/white?force=1", headers=admin)
        assert r.status == 200
        ids = [f["id"] for f in await (await client.get("/api/filaments")).json()]
        assert "white" not in ids and "biriuzovyi" in ids

    run(scenario)
