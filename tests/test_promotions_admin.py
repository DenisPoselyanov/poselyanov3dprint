"""Етап 5, слайс 5: нові типи акцій (cat_percent, free_ship), accent/show_timer, статистика, сумісність зі старими акціями."""

import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")

import catalog_store as cs  # noqa: E402
import services.promotions as promos  # noqa: E402
from services.pricing import split_discounts  # noqa: E402


@pytest.fixture
def shop(monkeypatch, tmp_path):
    """JSON-каталог з товарами двох категорій + SQLite у tmp (акції — лише дефолтні)."""
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
    promos._invalidate_cache()
    cs.reload_categories_cache(force=True)
    cs.reload_products_cache(force=True)
    yield tmp_path
    promos._invalidate_cache()
    for cache in (cs.PRODUCTS_CACHE, cs.CUSTOM_PRODUCTS_CACHE, cs.INACTIVE_PRODUCTS_CACHE,
                  cs.INACTIVE_CUSTOM_PRODUCTS_CACHE, cs.CATEGORIES_CACHE):
        cache.clear()


def _add(name, price, cat):
    res = cs.add_product({"cat": cat, "name": name, "price": price, "mat": "PLA", "filamentChoice": False})
    assert res["ok"], res
    return res["id"]


def _clear_defaults():
    """Без базових акцій — щоб рахувати лише те, що створено в тесті."""
    for pid in ("gift_3_toys", "order_10_percent"):
        assert promos.delete_promotion(pid)["ok"]


def _promo(**kw):
    data = {"id": "p1", "type": "order_percent", "title": "Тест", "value": 10}
    data.update(kw)
    res = promos.create_promotion(data)
    assert res["ok"], res
    return res["promotion"]


def _validate(items, total, coupon=None):
    from services.validation import validate_order_payload

    return validate_order_payload([{"product_id": pid, "quantity": q} for pid, q in items], coupon, 0, total)


# ─── Валідація нових типів і полів ───────────────────────────

def test_new_types_are_validated(shop):
    base = {"id": "x1", "title": "T"}
    # cat_percent: потрібні відсоток і хоча б одна категорія
    assert not promos.create_promotion({**base, "type": "cat_percent", "value": 10})["ok"]
    assert not promos.create_promotion({**base, "type": "cat_percent", "value": 0, "category": "key"})["ok"]
    assert not promos.create_promotion({**base, "type": "cat_percent", "value": 101, "category": "key"})["ok"]
    ok = promos.create_promotion({**base, "type": "cat_percent", "value": 15, "category": " toy , key,key "})
    assert ok["ok"] and ok["promotion"]["category"] == "key,toy"  # нормалізовано: унікальні, відсортовані
    # free_ship: без обовʼязкових числових полів, поріг необовʼязковий
    free = promos.create_promotion({**base, "id": "x2", "type": "free_ship"})
    assert free["ok"] and free["promotion"]["min_order"] == 0
    assert not promos.create_promotion({**base, "id": "x3", "type": "teleport"})["ok"]


def test_accent_and_show_timer_roundtrip(shop):
    p = _promo(id="acc", accent="violet", show_timer=False)
    assert p["accent"] == "violet" and p["show_timer"] is False
    assert not promos.create_promotion({"id": "bad", "type": "info", "title": "T", "accent": "#ff00ff"})["ok"]
    # оновлення без цих полів не скидає їх
    upd = promos.update_promotion("acc", {"title": "Нова назва"})
    assert upd["ok"] and upd["promotion"]["accent"] == "violet" and upd["promotion"]["show_timer"] is False
    upd = promos.update_promotion("acc", {"accent": "", "show_timer": True})
    assert upd["promotion"]["accent"] == "" and upd["promotion"]["show_timer"] is True
    # вітрина бачить поля
    pub = {x["id"]: x for x in promos.public_promotions()["promotions"]}
    assert pub["acc"]["accent"] == "" and pub["acc"]["show_timer"] is True


def test_legacy_rows_get_defaults_and_migration_is_idempotent(tmp_path):
    """Таблиця акцій зі старої схеми (без accent/show_timer) мігрується двічі без помилок."""
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("""CREATE TABLE promotions (
        id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', subtitle TEXT NOT NULL DEFAULT '',
        emoji TEXT NOT NULL DEFAULT '🔥', badge TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
        priority INTEGER NOT NULL DEFAULT 100, value INTEGER NOT NULL DEFAULT 0, min_order INTEGER NOT NULL DEFAULT 0,
        category TEXT NOT NULL DEFAULT '', min_items INTEGER NOT NULL DEFAULT 0, max_gift_price INTEGER NOT NULL DEFAULT 0,
        starts_at TIMESTAMP, ends_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("INSERT INTO promotions (id, type, title, value, min_order) VALUES ('old_one', 'order_percent', 'Стара', 5, 100)")
    conn.commit()
    promos.init_promotions_table(conn)
    promos.init_promotions_table(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(promotions)").fetchall()}
    assert {"accent", "show_timer"} <= cols
    row = conn.execute("SELECT accent, show_timer FROM promotions WHERE id = 'old_one'").fetchone()
    assert row == ("", 1)
    norm = promos._normalize_row({"id": "old_one", "type": "order_percent", "active": 1, "accent": "", "show_timer": 1})
    assert norm["accent"] == "" and norm["show_timer"] is True
    # рядок без самих колонок (наприклад, дефолтний набір) теж нормалізується
    legacy = promos._normalize_row({"id": "z", "type": "info", "active": 1})
    assert legacy["accent"] == "" and legacy["show_timer"] is True
    conn.close()


# ─── Розрахунок: cat_percent ─────────────────────────────────

def test_cat_percent_discounts_only_chosen_category(shop):
    _clear_defaults()
    key, toy = _add("Брелок", 100, "key"), _add("Іграшка", 200, "toy")
    _promo(id="keys15", type="cat_percent", value=20, category="key")
    # 2 брелки (200) + 1 іграшка (200) = 400; знижка 20% лише від 200 = 40
    assert promos.compute_order_raw_discount(400, {"key": 200, "toy": 200}) == 40
    assert promos.compute_order_raw_discount(400) == 0  # без складу кошика cat_percent не діє
    ok, res = _validate([(key, 2), (toy, 1)], 360)
    assert ok, res
    assert res["subtotal"] == 400 and res["promotion_discount"] == 40 and res["total_price"] == 360
    assert res["promotion_id"] == "keys15"
    ok, err = _validate([(key, 2), (toy, 1)], 400)  # клієнт не врахував знижку
    assert not ok and "Сума не збігається" in err
    ok, res = _validate([(toy, 1)], 200)  # лише іграшка — знижки немає
    assert ok and res["promotion_discount"] == 0 and res["promotion_id"] == ""


def test_cat_percent_respects_min_order_and_multiple_categories(shop):
    _clear_defaults()
    key, toy = _add("Брелок", 100, "key"), _add("Іграшка", 200, "toy")
    _promo(id="both", type="cat_percent", value=10, category="key,toy", min_order=500)
    ok, res = _validate([(key, 1), (toy, 1)], 300)  # 300 < 500 — поріг не виконано
    assert ok and res["promotion_discount"] == 0
    ok, res = _validate([(key, 2), (toy, 2)], 540)  # 600, знижка 10% від усього = 60
    assert ok, res
    assert res["promotion_discount"] == 60 and res["promotion_id"] == "both"


def test_best_promotion_wins_and_is_recorded(shop):
    _clear_defaults()
    key, toy = _add("Брелок", 100, "key"), _add("Іграшка", 100, "toy")
    _promo(id="order10", type="order_percent", value=10, min_order=100)
    _promo(id="keys30", type="cat_percent", value=30, category="key")
    # 5 брелків + 1 іграшка = 600: order10 = 60, keys30 = 150 → виграє категорійна
    ok, res = _validate([(key, 5), (toy, 1)], 450)
    assert ok, res
    assert res["promotion_discount"] == 150 and res["promotion_id"] == "keys30"
    # лише іграшки: категорійна дає 0, діє order10
    ok, res = _validate([(toy, 3)], 270)
    assert ok and res["promotion_id"] == "order10"


def test_coupon_without_stacking_replaces_promotion_attribution(shop):
    from services.coupons import create_coupon

    _clear_defaults()
    key = _add("Брелок", 100, "key")
    _promo(id="keys20", type="cat_percent", value=20, category="key")
    assert create_coupon({"code": "TEN", "type": "percent", "value": 10})["ok"]
    ok, res = _validate([(key, 4)], 360, coupon="TEN")  # купон замінює акцію
    assert ok, res
    assert res["coupon_discount"] == 40 and res["promotion_discount"] == 0 and res["promotion_id"] == ""
    assert split_discounts(400, 40, 80, stack=False) == (40, 0)


# ─── Розрахунок: free_ship ───────────────────────────────────

def test_free_ship_is_a_flag_not_a_price(shop):
    _clear_defaults()
    key = _add("Брелок", 100, "key")
    _promo(id="ship700", type="free_ship", min_order=700)
    ok, res = _validate([(key, 3)], 300)
    assert ok and res["free_ship_promotion_id"] == "" and res["total_price"] == 300
    ok, res = _validate([(key, 8)], 800)
    assert ok and res["free_ship_promotion_id"] == "ship700"
    assert res["total_price"] == 800 and res["promotion_discount"] == 0  # сума не змінюється
    # без порогу — безумовно
    _promo(id="always", type="free_ship", min_order=0, title="Усім")
    ok, res = _validate([(key, 1)], 100)
    assert ok and res["free_ship_promotion_id"] == "always"
    assert promos.free_shipping_promotion(50)["id"] == "always"


def test_disabled_and_scheduled_new_types_do_nothing(shop):
    _clear_defaults()
    key = _add("Брелок", 100, "key")
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    _promo(id="later", type="cat_percent", value=50, category="key", starts_at=future)
    _promo(id="off", type="free_ship", title="Вимк", active=0)
    ok, res = _validate([(key, 2)], 200)
    assert ok and res["promotion_discount"] == 0 and res["free_ship_promotion_id"] == ""


# ─── Статус і статистика ─────────────────────────────────────

def test_status_and_stats_from_orders(shop):
    import services.orders as orders

    _clear_defaults()
    now = datetime.now(timezone.utc)
    _promo(id="run", value=10)
    _promo(id="soon", starts_at=(now + timedelta(days=2)).isoformat(), title="Скоро")
    _promo(id="past", ends_at=(now - timedelta(days=1)).isoformat(), starts_at=(now - timedelta(days=5)).isoformat(), title="Було")
    _promo(id="off", active=0, title="Вимк")
    statuses = {p["id"]: p["status"] for p in promos.list_promotions_admin()}
    assert statuses == {"run": "active", "soon": "scheduled", "past": "ended", "off": "off"}

    def order(uid, total, status, promo_id="run", disc=0, gift=None):
        oid, _ = orders.save_order(
            uid, "@u", "Тест", [{"product_id": 1, "product_name": "X", "price": total, "quantity": 1}], total, "",
            promotion_discount=disc, extra={"promotion_id": promo_id, **({"gift_product_id": gift} if gift else {})},
        )
        orders.update_order_status(oid, status)

    order(1, 450, "confirmed", disc=50)
    order(2, 900, "delivered", disc=100, gift=7)
    order(3, 300, "new", disc=30)  # ще не прийнято — у виторг не йде
    order(4, 200, "cancelled", disc=20)
    order(5, 100, "shipped", promo_id="soon")
    by_id = {p["id"]: p["stats"] for p in promos.list_promotions_admin()}
    assert by_id["run"] == {"orders": 2, "revenue": 1350, "discount": 150, "gifts": 1}
    assert by_id["soon"]["orders"] == 1 and by_id["soon"]["revenue"] == 100
    assert by_id["off"] == {"orders": 0, "revenue": 0, "discount": 0, "gifts": 0}


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


def test_admin_promotion_routes(api):
    run, admin = api

    async def scenario(client):
        r = await client.get("/api/admin/promotions")
        assert r.status == 403
        r = await client.post("/api/admin/promotions", json={"id": "nope", "type": "info", "title": "X"})
        assert r.status == 403

        r = await client.post("/api/admin/promotions", headers=admin, json={
            "id": "keys_http", "type": "cat_percent", "title": "Брелки −10%", "value": 10, "category": "key",
            "accent": "teal", "show_timer": False})
        assert r.status == 201
        created = (await r.json())["promotion"]
        assert created["accent"] == "teal" and created["show_timer"] is False

        r = await client.post("/api/admin/promotions", headers=admin, json={"id": "bad_http", "type": "cat_percent", "title": "X", "value": 10})
        assert r.status == 400

        listing = await (await client.get("/api/admin/promotions", headers=admin)).json()
        row = next(p for p in listing if p["id"] == "keys_http")
        assert row["status"] == "active" and row["stats"] == {"orders": 0, "revenue": 0, "discount": 0, "gifts": 0}

        pub = await (await client.get("/api/promotions")).json()
        item = next(p for p in pub["promotions"] if p["id"] == "keys_http")
        assert item["type"] == "cat_percent" and item["accent"] == "teal" and item["show_timer"] is False

        r = await client.put("/api/admin/promotions/keys_http", headers=admin, json={"active": False})
        assert r.status == 200 and (await r.json())["promotion"]["active"] is False

    run(scenario)


def test_order_handler_records_promotion_id(api, monkeypatch):
    """/order сам визначає акцію для статистики (клієнтському promotion_id не довіряє)."""
    import bot

    run, admin = api
    _clear_defaults()
    key = _add("Брелок", 100, "key")
    _promo(id="keys20", type="cat_percent", value=20, category="key")
    _promo(id="ship_h", type="free_ship", min_order=0, title="Доставка")

    async def noop(**kwargs):
        return None

    monkeypatch.setattr(bot, "_deliver_order_notifications", noop)

    async def scenario(client):
        payload = {
            "idempotency_key": "k-1", "items": [{"product_id": key, "quantity": 3}], "total_price": 240,
            "promotion_id": "forged_by_client", "user_id": 1,
        }
        r = await client.post("/order", headers=admin, json=payload)
        body = await r.json()
        assert r.status == 200 and body["ok"], body
        return body

    body = run(scenario)
    from db_core import db_connect, sql

    conn = db_connect(dict_rows=True)
    row = conn.execute(sql("SELECT promotion_id, promotion_discount, total_price FROM orders WHERE id = ?"), (int(body["order_id"]),)).fetchone()
    conn.close()
    assert row["promotion_id"] == "keys20"
    assert int(row["promotion_discount"]) == 60 and int(row["total_price"]) == 240


def _post_order(api, monkeypatch, payload):
    import bot
    from db_core import db_connect, sql

    run, admin = api

    async def noop(**kwargs):
        return None

    monkeypatch.setattr(bot, "_deliver_order_notifications", noop)

    async def scenario(client):
        r = await client.post("/order", headers=admin, json=payload)
        body = await r.json()
        assert r.status == 200 and body["ok"], body
        return body

    body = run(scenario)
    conn = db_connect(dict_rows=True)
    row = conn.execute(sql("SELECT promotion_id, promotion_discount, total_price FROM orders WHERE id = ?"), (int(body["order_id"]),)).fetchone()
    conn.close()
    return row


def test_order_handler_attributes_free_ship_when_no_discount(api, monkeypatch):
    _clear_defaults()
    key = _add("Брелок", 100, "key")
    _promo(id="ship_only", type="free_ship", min_order=200, title="Доставка")
    row = _post_order(api, monkeypatch, {"idempotency_key": "k-2", "items": [{"product_id": key, "quantity": 3}], "total_price": 300, "user_id": 1})
    assert row["promotion_id"] == "ship_only" and int(row["promotion_discount"]) == 0 and int(row["total_price"]) == 300


def test_order_handler_attributes_gift_and_ignores_forged_id(api, monkeypatch):
    keys = [_add(f"Брелок {i}", 100, "key") for i in range(3)]
    gift = cs.add_product({"cat": "key", "name": "Подарунок", "price": 50, "mat": "PLA",
                           "filamentChoice": False, "gift": True})["id"]
    assert promos.delete_promotion("order_10_percent")["ok"]  # лишається лише подарункова акція за замовчуванням
    row = _post_order(api, monkeypatch, {
        "idempotency_key": "k-3", "items": [{"product_id": k, "quantity": 1} for k in keys], "total_price": 300,
        "gift_product_id": gift, "promotion_id": "forged", "user_id": 1,
    })
    assert row["promotion_id"] == "gift_3_toys"


def test_order_handler_ignores_gift_below_threshold(api, monkeypatch):
    """Поріг акції не досягнуто — подарунок, який просить клієнт, не додається й акція не зараховується."""
    key = _add("Брелок", 100, "key")
    gift = cs.add_product({"cat": "key", "name": "Подарунок", "price": 50, "mat": "PLA",
                           "filamentChoice": False, "gift": True})["id"]
    assert promos.delete_promotion("order_10_percent")["ok"]
    row = _post_order(api, monkeypatch, {
        "idempotency_key": "k-3b", "items": [{"product_id": key, "quantity": 1}], "total_price": 100,
        "gift_product_id": gift, "gift": "Дорогий товар", "user_id": 1,
    })
    assert not row["promotion_id"]


def test_order_handler_clears_forged_promotion_id(api, monkeypatch):
    key = _add("Брелок", 100, "key")
    assert promos.delete_promotion("order_10_percent")["ok"]
    # без подарунка й без вигоди — promotion_id порожній, навіть якщо клієнт щось надіслав
    row = _post_order(api, monkeypatch, {
        "idempotency_key": "k-4", "items": [{"product_id": key, "quantity": 1}], "total_price": 100,
        "promotion_id": "forged", "user_id": 1,
    })
    assert not row["promotion_id"]
