"""Етап 5, слайс 1: статуси товарів (active/hidden/archive), швидкі правки, масові дії, статистика."""

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


@pytest.fixture
def shop(monkeypatch, tmp_path):
    """JSON-каталог і SQLite у tmp: ключові товари + один «індивідуальний»."""
    import config

    (tmp_path / "products.json").write_text("[]", encoding="utf-8")
    (tmp_path / "custom_products.json").write_text("[]", encoding="utf-8")
    (tmp_path / "categories.json").write_text(json.dumps([
        {"id": "key", "name": "Брелки", "emoji": "🔑", "order": 1, "active": True},
        {"id": "toy", "name": "Іграшки", "emoji": "🧸", "order": 2, "active": True},
        {"id": "off", "name": "Вимкнена", "emoji": "x", "order": 3, "active": False},
        {"id": "custom", "name": "Індивідуальне", "emoji": "✏️", "order": 4, "active": True},
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
    cs.reload_categories_cache(force=True)
    cs.reload_products_cache(force=True)
    yield tmp_path
    for cache in (cs.PRODUCTS_CACHE, cs.CUSTOM_PRODUCTS_CACHE, cs.INACTIVE_PRODUCTS_CACHE,
                  cs.INACTIVE_CUSTOM_PRODUCTS_CACHE, cs.CATEGORIES_CACHE):
        cache.clear()


def _add(name="Брелок", price=100, cat="key", **extra):
    res = cs.add_product({"cat": cat, "name": name, "price": price, "mat": "PLA", **extra})
    assert res["ok"], res
    return res["id"]


def _file_statuses(tmp_path):
    rows = json.loads((tmp_path / "products.json").read_text(encoding="utf-8"))
    rows += json.loads((tmp_path / "custom_products.json").read_text(encoding="utf-8"))
    return {r["id"]: r.get("status", "active") for r in rows}


# ─── Статуси: публічний каталог проти адмінського ────────────

def test_public_catalog_shows_only_active(shop):
    a, b, c = _add("A"), _add("B"), _add("C")
    assert cs.set_product_status(b, "hidden")["ok"]
    assert cs.set_product_status(c, "archive")["ok"]

    assert [p["id"] for p in cs.get_all_products()] == [a]
    assert cs.get_product_by_id(a)["name"] == "A"
    assert cs.get_product_by_id(b) is None
    assert cs.get_product_by_id(c) is None
    # публічні кеші (з них беруть замовлення, валідація, хіти) не містять неактивних
    assert {p["id"] for p in cs.PRODUCTS_CACHE + cs.CUSTOM_PRODUCTS_CACHE} == {a}

    admin = {p["id"]: p["status"] for p in cs.get_admin_products()}
    assert admin == {a: "active", b: "hidden", c: "archive"}
    assert cs.get_product_any(c)["name"] == "C"


def test_status_survives_reload_from_files(shop):
    a, b = _add("A"), _add("B")
    cs.set_product_status(b, "hidden")
    assert _file_statuses(shop) == {a: "active", b: "hidden"}

    cs.PRODUCTS_CACHE.clear()
    cs.INACTIVE_PRODUCTS_CACHE.clear()
    cs.reload_products_cache(force=True)
    assert [p["id"] for p in cs.get_all_products()] == [a]
    assert {p["id"]: p["status"] for p in cs.get_admin_products()} == {a: "active", b: "hidden"}


def test_legacy_products_without_status_are_active(shop):
    (shop / "products.json").write_text(json.dumps([
        {"id": 1, "cat": "key", "name": "Старий", "emoji": "🔑", "price": 50, "photos": []},
    ], ensure_ascii=False), encoding="utf-8")
    cs.reload_products_cache(force=True)
    assert [p["id"] for p in cs.get_all_products()] == [1]
    assert "status" not in cs.get_product_by_id(1)  # публічний JSON не міняється
    assert cs.get_admin_products()[0]["status"] == "active"


def test_row_status_from_db_row_and_legacy_active_flag():
    assert cs._row_status({"status": "hidden", "active": True}) == "hidden"
    assert cs._row_status({"status": None, "active": False}) == "archive"
    assert cs._row_status({"active": True}) == "active"
    # рядок, знятий старим кодом (active=false без зміни status), лишається в архіві
    assert cs._row_status({"status": "active", "active": False}) == "archive"
    assert "status" not in cs._row_to_product({"id": 1, "name": "x", "status": "active", "active": True})
    assert cs._row_to_product({"id": 1, "name": "x", "status": "hidden"})["status"] == "hidden"


def test_invalid_status_and_missing_product(shop):
    pid = _add()
    assert not cs.set_product_status(pid, "deleted")["ok"]
    assert not cs.set_product_status(9999, "hidden")["ok"]
    assert cs.get_product_by_id(pid)["name"] == "Брелок"


# ─── Архів і повернення ──────────────────────────────────────

def test_delete_is_archive_and_restore(shop):
    pid = _add()
    assert cs.delete_product(pid)["ok"]
    assert cs.get_product_by_id(pid) is None
    assert cs.get_product_any(pid)["status"] == "archive"
    assert _file_statuses(shop)[pid] == "archive"  # товар лишився у файлі

    assert cs.set_product_status(pid, "active")["ok"]
    assert cs.get_product_by_id(pid)["name"] == "Брелок"
    assert "status" not in cs.get_product_by_id(pid)
    assert _file_statuses(shop)[pid] == "active"
    # повторне видалення архівного не падає
    cs.delete_product(pid)
    assert cs.delete_product(pid)["ok"]


def test_new_id_does_not_reuse_archived_id(shop):
    first, second = _add("1"), _add("2")
    cs.set_product_status(second, "archive")
    third = _add("3")
    assert third > second
    assert len({first, second, third}) == 3


def test_custom_products_keep_status_in_their_file(shop):
    pid = _add("Іменний", cat="custom", custom_fields="Імʼя")
    cs.set_product_status(pid, "hidden")
    assert cs.get_all_products() == []
    rows = json.loads((shop / "custom_products.json").read_text(encoding="utf-8"))
    assert [(r["id"], r.get("status")) for r in rows] == [(pid, "hidden")]
    assert json.loads((shop / "products.json").read_text(encoding="utf-8")) == []


def test_editing_hidden_product_keeps_status(shop):
    pid = _add(price=100)
    cs.set_product_status(pid, "hidden")
    assert cs.update_product(pid, {"name": "Нова назва", "price": 120})["ok"]
    product = cs.get_product_any(pid)
    assert (product["name"], product["price"], product["status"]) == ("Нова назва", 120, "hidden")
    assert cs.get_product_by_id(pid) is None
    # редагування з переносом в «індивідуальне» не губить статус
    assert cs.update_product(pid, {"cat": "custom"})["ok"]
    assert cs.get_product_any(pid)["status"] == "hidden"
    assert _file_statuses(shop)[pid] == "hidden"


# ─── Швидке оновлення ціни й прапорців ───────────────────────

def test_patch_price_and_old_price(shop):
    pid = _add(price=100)
    res = cs.patch_product(pid, {"price": "90", "oldPrice": 120})
    assert res["ok"], res
    assert (res["product"]["price"], res["product"]["oldPrice"]) == (90, 120)
    assert cs.get_product_by_id(pid)["oldPrice"] == 120

    bad = cs.patch_product(pid, {"price": 130})  # не нижча за стару
    assert not bad["ok"] and "нижчою" in bad["error"]
    assert cs.get_product_by_id(pid)["price"] == 90

    assert cs.patch_product(pid, {"oldPrice": None})["ok"]
    assert "oldPrice" not in cs.get_product_by_id(pid)
    for junk in ("abc", -5, 0, "", None, True, 2_000_000):
        assert not cs.patch_product(pid, {"price": junk})["ok"], junk
    assert cs.get_product_by_id(pid)["price"] == 90


def test_patch_flags_and_whitelist(shop):
    pid = _add()
    res = cs.patch_product(pid, {"hot": True, "pinned": True, "gift": True})
    assert res["ok"]
    product = cs.get_product_by_id(pid)
    assert (product["hot"], product["pinned"], product["gift"]) == (True, True, True)
    assert cs.patch_product(pid, {"hot": False})["ok"]
    assert cs.get_product_by_id(pid)["hot"] is False
    assert cs.get_product_by_id(pid)["pinned"] is True

    assert not cs.patch_product(pid, {"name": "Хакнуто"})["ok"]
    assert not cs.patch_product(pid, {"hot": "yes"})["ok"]
    assert not cs.patch_product(pid, {})["ok"]
    assert not cs.patch_product(pid, {"status": "archive"})["ok"]  # статус — лише окремим ендпоінтом
    assert cs.get_product_by_id(pid)["name"] == "Брелок"
    assert not cs.patch_product(9999, {"hot": True})["ok"]


def test_patch_blocks_contract_price_and_custom_gift(shop):
    contract = _add("Договірний", contractPrice=True)
    assert not cs.patch_product(contract, {"price": 50})["ok"]
    assert not cs.patch_product(contract, {"gift": True})["ok"]
    assert cs.patch_product(contract, {"hot": True})["ok"]
    assert cs.get_product_by_id(contract).get("contractPrice") is True  # прапорці не ламають договірну ціну

    custom = _add("Іменний", cat="custom", custom_fields="Імʼя")
    assert not cs.patch_product(custom, {"gift": True})["ok"]
    assert cs.patch_product(custom, {"pinned": True})["ok"]


def test_patch_works_on_hidden_and_archived(shop):
    pid = _add(price=100)
    cs.set_product_status(pid, "archive")
    assert cs.patch_product(pid, {"price": 80})["ok"]
    assert cs.get_product_any(pid)["price"] == 80
    assert cs.get_product_any(pid)["status"] == "archive"


# ─── Масові дії ──────────────────────────────────────────────

def test_bulk_discount_and_clear(shop):
    a, b = _add("A", 200), _add("B", 99)
    res = cs.bulk_update_products([a, b], "discount", 20)
    assert res["ok"] and res["updated"] == 2 and res["skipped"] == []
    pa, pb = cs.get_product_by_id(a), cs.get_product_by_id(b)
    assert (pa["price"], pa["oldPrice"]) == (160, 200)
    assert (pb["price"], pb["oldPrice"]) == (79, 99)

    # повторна знижка рахується від старої ціни, а не знижується каскадом
    cs.bulk_update_products([a], "discount", 50)
    assert (cs.get_product_by_id(a)["price"], cs.get_product_by_id(a)["oldPrice"]) == (100, 200)

    res = cs.bulk_update_products([a, b], "clear_discount")
    assert res["updated"] == 2
    pa = cs.get_product_by_id(a)
    assert pa["price"] == 200 and "oldPrice" not in pa
    assert cs.bulk_update_products([a], "clear_discount")["updated"] == 0


def test_bulk_discount_skips_contract_and_validates(shop):
    plain = _add("A", 100)
    contract = _add("Договірний", contractPrice=True)
    res = cs.bulk_update_products([plain, contract, 9999], "discount", 10)
    assert res["ok"] and res["updated"] == 1
    assert res["skipped"] == [{"id": contract, "reason": "договірна ціна"}]
    assert res["missing"] == [9999]
    for bad in (0, 91, -1, "x", None, "", True):
        assert not cs.bulk_update_products([plain], "discount", bad)["ok"], bad
    # дуже мала ціна: знижка округлилась би до 0
    tiny = _add("Копійка", 1)
    assert cs.bulk_update_products([tiny], "discount", 90)["skipped"][0]["reason"] == "ціна занадто мала"


def test_bulk_category(shop):
    a, b = _add("A", cat="key"), _add("B", cat="key")
    custom = _add("Іменний", cat="custom", custom_fields="Імʼя")
    res = cs.bulk_update_products([a, b, custom], "category", "toy")
    assert res["updated"] == 2 and res["skipped"] == [{"id": custom, "reason": "індивідуальне"}]
    assert cs.get_product_by_id(a)["cat"] == "toy"
    assert cs.get_product_by_id(custom)["cat"] == "custom"
    assert not cs.bulk_update_products([a], "category", "off")["ok"]       # вимкнена категорія
    assert not cs.bulk_update_products([a], "category", "nope")["ok"]
    assert not cs.bulk_update_products([a], "category", "custom")["ok"]


def test_bulk_status_and_input_validation(shop):
    a, b, c = _add("A"), _add("B"), _add("C")
    res = cs.bulk_update_products([a, b], "status", "hidden")
    assert res["updated"] == 2
    assert [p["id"] for p in cs.get_all_products()] == [c]
    assert cs.bulk_update_products([a, b, c], "status", "hidden")["updated"] == 1  # a, b вже приховані
    cs.bulk_update_products([a, b], "status", "active")
    assert {p["id"] for p in cs.get_all_products()} == {a, b}  # c лишився прихованим

    assert not cs.bulk_update_products([a], "status", "bogus")["ok"]
    assert not cs.bulk_update_products([a], "explode", 1)["ok"]
    assert not cs.bulk_update_products([], "status", "hidden")["ok"]
    assert not cs.bulk_update_products("1,2", "status", "hidden")["ok"]
    assert not cs.bulk_update_products(["x"], "status", "hidden")["ok"]
    assert not cs.bulk_update_products(list(range(1, 600)), "status", "hidden")["ok"]
    assert {p["id"] for p in cs.get_all_products()} == {a, b}


# ─── Дублювання ──────────────────────────────────────────────

def test_duplicate_creates_hidden_copy(shop):
    pid = _add("Брелок", 150, oldPrice=200, hot=True, photos=["https://x/1.jpg"], description="Опис")
    res = cs.duplicate_product(pid)
    assert res["ok"] and res["id"] != pid
    copy = cs.get_product_any(res["id"])
    assert copy["name"] == "Брелок (копія)"
    assert copy["status"] == "hidden"
    assert (copy["price"], copy["oldPrice"], copy["hot"], copy["description"]) == (150, 200, True, "Опис")
    assert copy["photos"] == ["https://x/1.jpg"] and copy["photos"] is not cs.get_product_by_id(pid)["photos"]
    assert [p["id"] for p in cs.get_all_products()] == [pid]
    assert not cs.duplicate_product(9999)["ok"]


# ─── Статистика ──────────────────────────────────────────────

def test_admin_stats_views_cart_sold(shop, monkeypatch):
    import services.product_stats as ps
    from db_core import db_connect
    from services.events import record_funnel_event

    a, b = _add("A"), _add("B")
    record_funnel_event(1, "product_view", a)
    record_funnel_event(2, "product_view", a)
    record_funnel_event(1, "product_view", a)  # повтор за добу — не рахується
    record_funnel_event(1, "add_to_cart", a)
    record_funnel_event(1, "catalog_open")
    conn = db_connect()
    conn.executemany(
        "INSERT INTO orders (id, user_id, total_price, status) VALUES (?, 1, 100, ?)",
        [(1, "confirmed"), (2, "delivered"), (3, "new"), (4, "cancelled")],
    )
    conn.executemany(
        "INSERT INTO order_items (order_id, product_id, product_name, price, quantity) VALUES (?, ?, 'x', 50, ?)",
        [(1, a, 2), (2, a, 3), (3, a, 40), (4, a, 40), (2, b, 1)],
    )
    conn.commit()
    conn.close()

    calls = []
    real_connect = ps.db_connect
    monkeypatch.setattr(ps, "db_connect", lambda *args, **kw: calls.append(1) or real_connect(*args, **kw))
    stats = ps.product_admin_stats()
    assert len(calls) == 1  # одне підключення, два агрегати — без запитів на кожен товар
    assert stats[a] == {"views": 2, "cart": 1, "sold": 5}
    assert stats[b] == {"views": 0, "cart": 0, "sold": 1}

    cs.set_product_status(b, "archive")
    rows = {r["id"]: r for r in ps.admin_products_with_stats(cs.get_admin_products())}
    assert (rows[a]["views"], rows[a]["cart_adds"], rows[a]["sold"]) == (2, 1, 5)
    assert rows[b]["status"] == "archive" and rows[b]["sold"] == 1
    third = _add("Без подій")
    rows = {r["id"]: r for r in ps.admin_products_with_stats(cs.get_admin_products())}
    assert (rows[third]["views"], rows[third]["cart_adds"], rows[third]["sold"]) == (0, 0, 0)


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


def test_admin_routes_require_admin(api):
    run, _ = api
    pid = _add()

    async def scenario(client):
        calls = [
            ("get", "/api/admin/products", None),
            ("put", f"/api/products/{pid}/status", {"status": "hidden"}),
            ("patch", f"/api/products/{pid}", {"hot": True}),
            ("post", "/api/products/bulk", {"ids": [pid], "action": "status", "value": "hidden"}),
            ("post", f"/api/products/{pid}/duplicate", None),
            ("delete", f"/api/products/{pid}", None),
        ]
        out = []
        for method, url, body in calls:
            resp = await getattr(client, method)(url, json=body) if body is not None else await getattr(client, method)(url)
            out.append(resp.status)
        return out

    assert run(scenario) == [403] * 6
    assert cs.get_product_by_id(pid) is not None  # нічого не змінилось


def test_http_flow_status_patch_bulk_duplicate(api):
    run, admin = api
    a, b = _add("A", 200), _add("B", 100)

    async def scenario(client):
        r = await client.put(f"/api/products/{a}/status", json={"status": "hidden"}, headers=admin)
        assert r.status == 200 and (await r.json())["status"] == "hidden"
        r = await client.put(f"/api/products/{a}/status", json={"status": "weird"}, headers=admin)
        assert r.status == 400

        # публічний список: лише активні, без status/stlLink
        pub = await (await client.get("/api/products")).json()
        assert [p["id"] for p in pub] == [b]
        assert "status" not in pub[0]
        r = await client.get(f"/api/products/{a}")
        assert r.status == 404  # прихований покупцям не віддається

        # адмінський список: усі, зі статусом і статистикою
        rows = await (await client.get("/api/admin/products", headers=admin)).json()
        assert {x["id"]: x["status"] for x in rows} == {a: "hidden", b: "active"}
        assert all({"views", "cart_adds", "sold"} <= x.keys() for x in rows)
        # адмін бачить прихований товар у редакторі
        r = await client.get(f"/api/products/{a}", headers=admin)
        assert r.status == 200 and (await r.json())["status"] == "hidden"

        r = await client.patch(f"/api/products/{b}", json={"price": 90, "hot": True}, headers=admin)
        data = await r.json()
        assert r.status == 200 and data["product"]["price"] == 90 and data["product"]["hot"] is True
        r = await client.patch(f"/api/products/{b}", json={"price": 500, "oldPrice": 100}, headers=admin)
        assert r.status == 400 and not (await r.json())["ok"]
        r = await client.patch(f"/api/products/{b}", data="не json", headers=admin)
        assert r.status == 400

        r = await client.post("/api/products/bulk", json={"ids": [a, b], "action": "discount", "value": 10}, headers=admin)
        data = await r.json()
        assert r.status == 200 and data["updated"] == 2
        r = await client.post("/api/products/bulk", json={"ids": [a], "action": "discount", "value": 99}, headers=admin)
        assert r.status == 400

        r = await client.post(f"/api/products/{b}/duplicate", headers=admin)
        data = await r.json()
        assert r.status == 201 and data["ok"]
        copy_id = data["id"]

        # DELETE лишається «м'яким»: товар у архіві, а не зникає
        r = await client.delete(f"/api/products/{b}", headers=admin)
        assert r.status == 200
        rows = await (await client.get("/api/admin/products", headers=admin)).json()
        assert {x["id"]: x["status"] for x in rows} == {a: "hidden", b: "archive", copy_id: "hidden"}
        r = await client.put(f"/api/products/{b}/status", json={"status": "active"}, headers=admin)
        assert r.status == 200
        pub = await (await client.get("/api/products")).json()
        assert [p["id"] for p in pub] == [b]

    run(scenario)
