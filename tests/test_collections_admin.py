"""Етап 5, слайс 3: керування добірками з адмінки (CRUD, порядок, склад, правила, видимість у магазині)."""

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
import services.collections as col  # noqa: E402


@pytest.fixture
def shop(monkeypatch, tmp_path):
    """JSON-каталог + SQLite у tmp (з сідом 7 типових добірок)."""
    import config

    (tmp_path / "products.json").write_text("[]", encoding="utf-8")
    (tmp_path / "custom_products.json").write_text("[]", encoding="utf-8")
    (tmp_path / "categories.json").write_text(json.dumps([
        {"id": "key", "name": "Брелки", "emoji": "🔑", "order": 1, "active": True},
        {"id": "toy", "name": "Іграшки", "emoji": "🧸", "order": 2, "active": True},
        {"id": "custom", "name": "Індивідуальне", "emoji": "✏️", "order": 3, "active": True},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "CATALOG_BACKEND", "json")
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "shop.db"))
    monkeypatch.setattr(config, "PRODUCTS_FILE", str(tmp_path / "products.json"))
    monkeypatch.setattr(config, "CUSTOM_PRODUCTS_FILE", str(tmp_path / "custom_products.json"))
    monkeypatch.setattr(config, "CATEGORIES_FILE", str(tmp_path / "categories.json"))
    monkeypatch.setattr(cs, "use_catalog_db", lambda: False)
    from services.db_utils import init_db

    init_db()
    col._invalidate_cache()
    cs.reload_categories_cache(force=True)
    cs.reload_products_cache(force=True)
    yield tmp_path
    col._invalidate_cache()
    for cache in (cs.PRODUCTS_CACHE, cs.CUSTOM_PRODUCTS_CACHE, cs.INACTIVE_PRODUCTS_CACHE,
                  cs.INACTIVE_CUSTOM_PRODUCTS_CACHE, cs.CATEGORIES_CACHE):
        cache.clear()


def _add(name="Брелок", price=100, cat="key", **extra):
    res = cs.add_product({"cat": cat, "name": name, "price": price, "mat": "PLA", **extra})
    assert res["ok"], res
    return res["id"]


def _by_id(items):
    return {c["id"]: c for c in items}


# ─── Сід і список для адмінки ────────────────────────────────

def test_admin_list_includes_seed_with_auto_counts(shop):
    cheap = _add("Дешевий", 50)
    mid = _add("Середній", 200, oldPrice=300)
    big = _add("Великий", 400)
    hidden = _add("Прихований дешевий", 10)
    assert cs.set_product_status(hidden, "hidden")["ok"]
    glow = _add("Світний", 120, luminousFilamentChoice=True)
    custom = _add("Індивідуальний", 90, cat="custom", custom_fields="Імʼя")
    contract = _add("Договірний", 0, contractPrice=True)

    items = _by_id(col.list_admin_collections())
    assert len(items) == 7 and all(c["active"] for c in items.values())
    # u100: ціна <= 100 без договірних, лише активні (прихований не рахується): cheap + custom(90)
    assert items["u100"]["count"] == 2
    # gift: до 150 без індивідуальних: cheap + glow(120)
    assert items["gift"]["count"] == 2
    # top: від 300 (договірна ціна 0 не потрапляє)
    assert items["top"]["count"] == 1
    # sale: лише mid зі старою ціною
    assert items["sale"]["count"] == 1
    # glow: тільки з luminousFilamentChoice
    assert items["glow"]["count"] == 1
    # new: усі нові активні (6 з 7: прихований не рахується)
    assert items["new"]["count"] == 6
    assert items["u100"]["label"] == "Авто: до 100 ₴"
    assert {cheap, mid, big, glow, custom, contract}  # усі створені


def test_hits_rule_counts_hot_flag_up_to_limit(shop):
    for i in range(4):
        _add(f"Хіт {i}", 100 + i, hot=True)
    _add("Звичайний", 100)
    items = _by_id(col.list_admin_collections())
    assert items["hits"]["count"] == 4
    assert col.update_collection("hits", {"rule": {"type": "hits", "limit": 2}})["ok"]
    assert _by_id(col.list_admin_collections())["hits"]["count"] == 2


# ─── Створення й перевірка ──────────────────────────────────

def test_create_manual_collection_with_members_and_slug(shop):
    a, b, c = _add("A"), _add("B"), _add("C")
    res = col.create_collection({"name": "Для друзів", "description": "Ідеї", "icon": "heart", "color": "pink",
                                 "mode": "manual", "product_ids": [c, a]})
    assert res["ok"] and res["id"] == "dlia-druziv"
    item = res["collection"]
    assert item["product_ids"] == [c, a] and item["count"] == 2 and item["label"] == "Вручну"
    assert item["active"] is True and item["sort_order"] > 70  # у кінці списку

    again = col.create_collection({"name": "Для друзів", "mode": "manual"})
    assert again["ok"] and again["id"] == "dlia-druziv-2"
    assert again["collection"]["count"] == 0
    assert b  # не входить


def test_create_auto_collection_normalises_rule(shop):
    res = col.create_collection({"name": "Дешево", "mode": "auto",
                                 "rule": {"type": "price_max", "max_price": "80", "junk": 1}})
    assert res["ok"]
    assert res["collection"]["rule"] == {"type": "price_max", "max_price": 80, "exclude_custom": False}


@pytest.mark.parametrize("payload,fragment", [
    ({"name": ""}, "назву"),
    ({"name": "x" * 41}, "довша"),
    ({"name": "A", "icon": "nope"}, "іконка"),
    ({"name": "A", "color": "magenta"}, "колір"),
    ({"name": "A", "mode": "weird"}, "Режим"),
    ({"name": "A", "mode": "auto"}, "правило"),
    ({"name": "A", "mode": "auto", "rule": {"type": "magic"}}, "правила"),
    ({"name": "A", "mode": "auto", "rule": {"type": "price_max"}}, "Ціна до"),
    ({"name": "A", "mode": "auto", "rule": {"type": "price_max", "max_price": -5}}, "Ціна до"),
    ({"name": "A", "mode": "auto", "rule": {"type": "new", "days": 9999}}, "Днів"),
    ({"name": "A", "mode": "manual", "product_ids": [99999]}, "не існує"),
    ({"name": "A", "mode": "manual", "product_ids": "1,2"}, "списком"),
    ({"name": "A", "id": "Bad Id!"}, "ID"),
    ({"name": "A", "id": "order"}, "ID"),
    ({"name": "A", "id": "new"}, "вже є"),
])
def test_create_validation(shop, payload, fragment):
    res = col.create_collection(payload)
    assert not res["ok"] and fragment.lower() in res["error"].lower()
    assert len(col.list_admin_collections()) == 7  # нічого не створено


# ─── Оновлення, режими, видалення ───────────────────────────

def test_update_fields_and_mode_switch(shop):
    a, b = _add("A"), _add("B")
    res = col.update_collection("u100", {"name": "До 50 ₴", "rule": {"type": "price_max", "max_price": 50}, "color": "teal"})
    assert res["ok"] and res["collection"]["name"] == "До 50 ₴" and res["collection"]["color"] == "teal"
    assert res["collection"]["rule"]["max_price"] == 50 and res["collection"]["id"] == "u100"

    # у ручний режим: правило зникає, склад задається
    res = col.update_collection("u100", {"mode": "manual", "product_ids": [b]})
    assert res["ok"] and res["collection"]["mode"] == "manual" and res["collection"]["rule"] is None
    assert res["collection"]["product_ids"] == [b]
    # product_ids в авто-режимі відхиляється
    res = col.update_collection("sale", {"product_ids": [a]})
    assert not res["ok"]
    # назад в авто без правила — помилка, з правилом — ок
    assert not col.update_collection("u100", {"mode": "auto"})["ok"]
    back = col.update_collection("u100", {"mode": "auto", "rule": {"type": "glow"}})
    assert back["ok"] and back["collection"]["rule"] == {"type": "glow"}

    missing = col.update_collection("nope", {"name": "X"})
    assert not missing["ok"] and missing["not_found"]


def test_toggle_active_hides_from_public(shop):
    _add("A", 50)
    assert col.update_collection("u100", {"active": False})["ok"]
    assert "u100" not in {c["id"] for c in col.visible_collections()}
    admin = _by_id(col.list_admin_collections())
    assert admin["u100"]["active"] is False  # в адмінці лишається
    assert col.update_collection("u100", {"active": True})["ok"]
    assert "u100" in {c["id"] for c in col.visible_collections()}


def test_delete_collection_removes_membership(shop):
    a = _add("A")
    cid = col.create_collection({"name": "Тимчасова", "mode": "manual", "product_ids": [a]})["id"]
    assert col.delete_collection(cid)["ok"]
    assert cid not in _by_id(col.list_admin_collections())
    assert col.product_collection_ids(a) == []
    gone = col.delete_collection(cid)
    assert not gone["ok"] and gone["not_found"]
    # рядків членства не лишилось
    from db_core import db_connect

    conn = db_connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM collection_products WHERE collection_id = ?", (cid,)).fetchone()[0] == 0
    finally:
        conn.close()


# ─── Порядок ────────────────────────────────────────────────

def test_reorder_collections(shop):
    ids = [c["id"] for c in col.list_admin_collections()]
    assert ids[:2] == ["new", "u100"]
    res = col.reorder_collections(["top", "new"])
    assert res["ok"] and res["ids"][:2] == ["top", "new"]
    after = [c["id"] for c in col.list_admin_collections()]
    assert after == ["top", "new"] + [i for i in ids if i not in ("top", "new")]
    assert [c["id"] for c in col.visible_collections()][:2] == ["top", "new"]  # публічний порядок теж
    for bad in ([], None, ["new", "new"], ["new", "ghost"]):
        assert not col.reorder_collections(bad)["ok"]


# ─── Склад вручну й чипси в редакторі товару ────────────────

def test_set_collection_products_keeps_order(shop):
    a, b, c = _add("A"), _add("B"), _add("C")
    cid = col.create_collection({"name": "Вибране", "mode": "manual"})["id"]
    res = col.set_collection_products(cid, [b, c, a])
    assert res["ok"] and res["collection"]["product_ids"] == [b, c, a]
    res = col.set_collection_products(cid, [a])
    assert res["collection"]["product_ids"] == [a]
    assert not col.set_collection_products(cid, [999])["ok"]
    assert not col.set_collection_products("sale", [a])["ok"]  # авто-добірка: склад не задається
    assert col.set_collection_products("ghost", [a])["not_found"]


def test_product_collections_add_remove_and_auto_untouched(shop):
    a, b = _add("A"), _add("B")
    one = col.create_collection({"name": "Одна", "mode": "manual", "product_ids": [b]})["id"]
    two = col.create_collection({"name": "Друга", "mode": "manual"})["id"]
    assert col.product_collection_ids(a) == []

    res = col.set_product_collections(a, [two, one])
    assert res["ok"] and set(res["collections"]) == {one, two}
    # новий товар додається в кінець списку добірки
    assert _by_id(col.list_admin_collections())[one]["product_ids"] == [b, a]

    res = col.set_product_collections(a, [one])
    assert res["collections"] == [one] and _by_id(col.list_admin_collections())[two]["product_ids"] == []
    assert _by_id(col.list_admin_collections())[one]["product_ids"] == [b, a]  # B не зачеплений

    assert not col.set_product_collections(a, ["sale"])["ok"]  # авто-добірка недоступна для чипсів
    assert not col.set_product_collections(a, ["ghost"])["ok"]
    assert not col.set_product_collections(a, "one")["ok"]
    assert col.set_product_collections(9999, [one])["not_found"]


# ─── Видимість у магазині ───────────────────────────────────

def test_public_hides_inactive_empty_and_non_active_products(shop):
    a, b, c = _add("A"), _add("B"), _add("C")
    shown = col.create_collection({"name": "Видима", "mode": "manual", "product_ids": [a, b, c]})["id"]
    empty = col.create_collection({"name": "Порожня", "mode": "manual"})["id"]
    only_hidden = col.create_collection({"name": "Лише прихований", "mode": "manual", "product_ids": [b]})["id"]
    off = col.create_collection({"name": "Вимкнена", "mode": "manual", "product_ids": [a], "active": False})["id"]
    assert cs.set_product_status(b, "hidden")["ok"]
    assert cs.set_product_status(c, "archive")["ok"]

    public = _by_id(col.visible_collections())
    assert public[shown]["product_ids"] == [a]
    assert empty not in public and only_hidden not in public and off not in public
    assert {"new", "u100", "hits"} <= set(public)  # авто-добірки віддаються правилом

    # приховані й архівні лишились у ручному списку (адмінка їх бачить)
    admin = _by_id(col.list_admin_collections())[shown]
    assert admin["product_ids"] == [a, b, c] and admin["count"] == 1 and admin["hidden_count"] == 2
    # повернули в активні — знов у магазині, склад збережено
    assert cs.set_product_status(b, "active")["ok"]
    assert _by_id(col.visible_collections())[shown]["product_ids"] == [a, b]


def test_preview_counts_without_saving(shop):
    _add("Дешевий", 50)
    _add("Дорогий", 500)
    res = col.preview_collection({"mode": "auto", "rule": {"type": "price_max", "max_price": 100}})
    assert res["ok"] and res["count"] == 1 and len(res["product_ids"]) == 1
    res = col.preview_collection({"mode": "auto", "rule": {"type": "price_min", "min_price": "10"}})
    assert res["count"] == 2
    assert not col.preview_collection({"mode": "auto", "rule": {"type": "price_max", "max_price": "abc"}})["ok"]
    assert col.preview_collection({"mode": "manual", "product_ids": []})["count"] == 0
    assert len(col.list_admin_collections()) == 7


def test_init_is_idempotent_after_changes(shop):
    from db_core import db_connect

    assert col.update_collection("new", {"name": "Мої новинки"})["ok"]
    conn = db_connect()
    try:
        col.init_collections_table(conn)
        conn.commit()
    finally:
        conn.close()
    items = _by_id(col.list_admin_collections())
    assert len(items) == 7 and items["new"]["name"] == "Мої новинки"


# ─── HTTP: доступ і форма відповідей ─────────────────────────

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


def test_admin_collection_routes_require_admin(api):
    run, _ = api
    pid = _add()

    async def scenario(client):
        calls = [
            ("get", "/api/admin/collections", None),
            ("post", "/api/admin/collections", {"name": "X", "mode": "manual"}),
            ("put", "/api/admin/collections/new", {"name": "X"}),
            ("delete", "/api/admin/collections/new", None),
            ("put", "/api/admin/collections/order", {"ids": ["top"]}),
            ("post", "/api/admin/collections/preview", {"mode": "manual"}),
            ("put", "/api/admin/collections/new/products", {"product_ids": []}),
            ("get", f"/api/admin/products/{pid}/collections", None),
            ("put", f"/api/admin/products/{pid}/collections", {"collections": []}),
        ]
        out = []
        for method, url, body in calls:
            resp = await getattr(client, method)(url, json=body) if body is not None else await getattr(client, method)(url)
            out.append(resp.status)
        return out

    assert run(scenario) == [403] * 9
    assert len(col.list_admin_collections()) == 7 and _by_id(col.list_admin_collections())["new"]["name"] == "Новинки"


def test_http_flow(api):
    run, admin = api
    a, b = _add("A", 50), _add("B", 60)

    async def scenario(client):
        r = await client.post("/api/admin/collections", json={"name": "Мої", "mode": "manual", "product_ids": [a]}, headers=admin)
        assert r.status == 201
        created = (await r.json())["collection"]
        cid = created["id"]
        assert created["count"] == 1

        r = await client.post("/api/admin/collections", json={"name": ""}, headers=admin)
        assert r.status == 400 and (await r.json())["ok"] is False

        r = await client.put(f"/api/admin/collections/{cid}/products", json={"product_ids": [b, a]}, headers=admin)
        assert r.status == 200 and (await r.json())["collection"]["product_ids"] == [b, a]

        r = await client.put(f"/api/admin/products/{a}/collections", json={"collections": []}, headers=admin)
        assert r.status == 200
        r = await client.get(f"/api/admin/products/{b}/collections", headers=admin)
        assert (await r.json())["collections"] == [cid]

        r = await client.put("/api/admin/collections/order", json={"ids": [cid]}, headers=admin)
        assert r.status == 200
        listing = await (await client.get("/api/admin/collections", headers=admin)).json()
        assert listing["collections"][0]["id"] == cid and len(listing["collections"]) == 8

        # публічний ендпоінт без авторизації: порядок і склад
        pub = await (await client.get("/api/collections")).json()
        assert pub["collections"][0]["id"] == cid and pub["collections"][0]["product_ids"] == [b]

        r = await client.post("/api/admin/collections/preview", json={"mode": "auto", "rule": {"type": "price_max", "max_price": 55}}, headers=admin)
        assert (await r.json())["count"] == 1

        r = await client.put(f"/api/admin/collections/{cid}", json={"active": False}, headers=admin)
        assert r.status == 200
        pub = await (await client.get("/api/collections")).json()
        assert cid not in {c["id"] for c in pub["collections"]}

        r = await client.put("/api/admin/collections/ghost", json={"name": "X"}, headers=admin)
        assert r.status == 404
        r = await client.delete(f"/api/admin/collections/{cid}", headers=admin)
        assert r.status == 200
        r = await client.delete(f"/api/admin/collections/{cid}", headers=admin)
        assert r.status == 404

    run(scenario)
