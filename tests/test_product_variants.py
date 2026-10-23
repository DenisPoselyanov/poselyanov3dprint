"""Варіанти товару (розміри) з власними цінами: збереження в каталозі й перевірка ціни в замовленні."""

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

SIZES = [
    {"name": "10 см", "price": 250},
    {"name": "15 см", "price": 400},
    {"name": "Великий — під замовлення", "contract": True},
]


@pytest.fixture
def shop(monkeypatch, tmp_path):
    import config

    (tmp_path / "products.json").write_text("[]", encoding="utf-8")
    (tmp_path / "custom_products.json").write_text("[]", encoding="utf-8")
    (tmp_path / "categories.json").write_text(json.dumps([
        {"id": "home", "name": "Дім", "emoji": "🏠", "order": 1, "active": True},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "filaments.json").write_text(json.dumps([
        {"id": "white", "name": "Білий", "hex": "#f0f0f0", "available": True},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "CATALOG_BACKEND", "json")
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "shop.db"))
    monkeypatch.setattr(config, "PRODUCTS_FILE", str(tmp_path / "products.json"))
    monkeypatch.setattr(config, "CUSTOM_PRODUCTS_FILE", str(tmp_path / "custom_products.json"))
    monkeypatch.setattr(config, "CATEGORIES_FILE", str(tmp_path / "categories.json"))
    monkeypatch.setattr(config, "FILAMENTS_FILE", str(tmp_path / "filaments.json"))
    monkeypatch.setattr(cs, "use_catalog_db", lambda: False)
    # Типові акції (−10% від суми) тут не потрібні: перевіряємо саме ціни варіантів
    import services.validation as validation

    monkeypatch.setattr(validation, "compute_order_raw_discount", lambda subtotal, cat_totals: 0)
    from services.db_utils import init_db

    init_db()
    cs.reload_categories_cache(force=True)
    cs.reload_filaments_cache(force=True)
    cs.reload_products_cache(force=True)
    yield tmp_path
    for cache in (cs.PRODUCTS_CACHE, cs.CUSTOM_PRODUCTS_CACHE, cs.INACTIVE_PRODUCTS_CACHE,
                  cs.INACTIVE_CUSTOM_PRODUCTS_CACHE, cs.CATEGORIES_CACHE, cs.FILAMENTS_CACHE):
        cache.clear()


def _add(**extra):
    data = {"cat": "home", "name": "Ваза", "emoji": "🏺", "mat": "PLA", "price": 999, "oldPrice": 1500,
            "gift": True, "variants": SIZES, **extra}
    res = cs.add_product(data)
    assert res["ok"], res
    return cs.get_product_any(res["id"])


def test_normalize_variants_ids_and_errors():
    out = cs.normalize_variants(SIZES)
    assert [v["id"] for v in out] == ["v1", "v2", "v3"]
    assert out[2] == {"id": "v3", "name": "Великий — під замовлення", "contract": True}
    # Наявні id зберігаються, нові не збігаються з ними
    kept = cs.normalize_variants([{"id": "v2", "name": "A", "price": 1}, {"name": "B", "price": 2}])
    assert [v["id"] for v in kept] == ["v2", "v1"]
    assert cs.normalize_variants(None) == []
    for bad in (
        [{"name": "A", "price": 1}],                                   # один варіант
        [{"name": "A", "price": 1}, {"name": "a", "price": 2}],        # дубль назви
        [{"name": "A", "price": 1}, {"name": "", "price": 2}],         # без назви
        [{"name": "A", "price": 1}, {"name": "B", "price": 0}],        # нульова ціна
        [{"name": "A", "contract": True}, {"name": "B", "contract": True}],  # жодної ціни
    ):
        with pytest.raises(ValueError):
            cs.normalize_variants(bad)


def test_add_product_with_variants_uses_min_price(shop):
    p = _add()
    assert p["price"] == 250
    assert "oldPrice" not in p
    assert p["gift"] is False
    assert [v["name"] for v in p["variants"]] == ["10 см", "15 см", "Великий — під замовлення"]


def test_update_keeps_variant_ids_and_can_drop_variants(shop):
    p = _add()
    variants = [dict(v) for v in p["variants"]]
    variants[0]["price"] = 300
    variants.pop(1)
    variants.append({"name": "20 см", "price": 600})
    assert cs.update_product(p["id"], {"variants": variants})["ok"]
    p = cs.get_product_any(p["id"])
    assert [(v["id"], v.get("price")) for v in p["variants"]] == [("v1", 300), ("v3", None), ("v2", 600)]
    assert p["price"] == 300

    # Часткове оновлення без `variants` не чіпає варіанти й ціну
    assert cs.update_product(p["id"], {"name": "Ваза 2", "price": 1})["ok"]
    p = cs.get_product_any(p["id"])
    assert p["price"] == 300 and len(p["variants"]) == 3

    # Договірна ціна прибирає варіанти; порожній список — звичайна ціна
    assert cs.update_product(p["id"], {"contractPrice": True})["ok"]
    assert "variants" not in cs.get_product_any(p["id"])
    assert cs.update_product(p["id"], {"variants": SIZES})["ok"]
    assert cs.update_product(p["id"], {"variants": [], "price": 120})["ok"]
    p = cs.get_product_any(p["id"])
    assert "variants" not in p and p["price"] == 120


def test_quick_price_edit_refused_for_variants(shop):
    p = _add()
    assert not cs.patch_product(p["id"], {"price": 100})["ok"]
    assert not cs.patch_product(p["id"], {"gift": True})["ok"]
    assert cs.patch_product(p["id"], {"hot": True})["ok"]


def test_order_uses_variant_price(shop):
    from services.validation import validate_order_payload

    p = _add()
    items = [
        {"product_id": p["id"], "variant_id": "v2", "quantity": 1, "filament_id": "white"},
        {"product_id": p["id"], "variant_id": "v1", "quantity": 1, "filament_id": "white"},
    ]
    ok, res = validate_order_payload(items, None, 1, 650)
    assert ok, res
    assert res["subtotal"] == 650
    assert [i["product_name"] for i in res["items"]] == ["Ваза · 15 см", "Ваза · 10 см"]
    assert [i["variant_id"] for i in res["items"]] == ["v2", "v1"]
    assert res["price_pending"] == 0

    # Клієнт підсунув ціну дешевшого варіанта — сума не збігається
    ok, _ = validate_order_payload(items[:1], None, 1, 250)
    assert not ok


def test_order_contract_variant_and_missing_variant(shop):
    from services.validation import validate_order_payload

    p = _add()
    ok, res = validate_order_payload([{"product_id": p["id"], "variant_id": "v3", "quantity": 1}], None, 1, 0)
    assert ok, res
    assert res["items"][0]["is_contract_price"] is True
    assert res["price_pending"] == 1

    for vid in ("", "nope"):
        ok, err = validate_order_payload([{"product_id": p["id"], "variant_id": vid, "quantity": 1}], None, 1, 250)
        assert not ok and "варіант" in err


def test_order_item_stores_variant_id(shop):
    from services.orders import list_user_orders, save_order
    from services.validation import validate_order_payload

    p = _add()
    ok, res = validate_order_payload([{"product_id": p["id"], "variant_id": "v2", "quantity": 1}], None, 7, 400)
    assert ok, res
    save_order(7, "tester", "Tester", res["items"], res["total_price"], "")
    item = list_user_orders(7)[0]["items"][0]
    assert item["variant_id"] == "v2"
    assert item["product_name"] == "Ваза · 15 см"
