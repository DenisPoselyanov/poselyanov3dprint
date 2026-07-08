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

from catalog_store import is_contract_product, validate_product_prices  # noqa: E402
from rich_messages import (  # noqa: E402
    BOT_LINK_BASE,
    build_personal_coupon_notification,
    build_sales_list,
    product_link,
)
from services.coupons import check_promotion  # noqa: E402
from services.validation import validate_order_payload  # noqa: E402


@pytest.fixture
def catalog(monkeypatch):
    products = [
        {"id": 3, "name": "Rounded 170", "price": 170, "filamentChoice": False},
        {"id": 4, "name": "Rounded 510", "price": 510, "filamentChoice": False},
        {"id": 1, "name": "Кубик", "price": 100, "filamentChoice": False},
        {"id": 2, "name": "Фігурка", "price": 200, "filamentChoice": True},
    ]
    filaments = [
        {"id": "red", "name": "Червоний", "available": True},
    ]

    import services.validation as validation

    monkeypatch.setattr(validation, "PRODUCTS_CACHE", products)
    monkeypatch.setattr(validation, "CUSTOM_PRODUCTS_CACHE", [])
    monkeypatch.setattr(validation, "FILAMENTS_CACHE", filaments)
    monkeypatch.setattr(validation, "reload_products_cache", lambda force=False: None)
    monkeypatch.setattr(validation, "reload_filaments_cache", lambda force=False: None)
    return products, filaments


def test_validate_product_prices_sale():
    ok, err = validate_product_prices(100, 150)
    assert ok is True
    assert err is None


def test_validate_product_prices_invalid_sale():
    ok, err = validate_product_prices(150, 100)
    assert ok is False


def test_check_promotion_disabled(monkeypatch):
    import config

    monkeypatch.setattr(config, "PROMOTION_ENABLED", False)
    assert check_promotion(1000) == 0


def test_check_promotion_at_threshold():
    assert check_promotion(500) == 50


def test_check_promotion_rounds_final_price_up():
    assert check_promotion(510) == 50


def test_validate_order_payload_empty():
    ok, result = validate_order_payload([], None, 1, 0)
    assert ok is False
    assert result == "Порожній кошик"


def test_validate_order_payload_unknown_product(catalog):
    ok, result = validate_order_payload([{"product_id": 999, "quantity": 1}], None, 1, 0)
    assert ok is False
    assert "Невідомий товар" in result


def test_validate_order_payload_invalid_quantity(catalog):
    ok, result = validate_order_payload([{"product_id": 1, "quantity": 0}], None, 1, 0)
    assert ok is False
    assert "Кількість" in result


def test_validate_order_payload_duplicate_lines(catalog):
    items = [{"product_id": 1, "quantity": 1}, {"product_id": 1, "quantity": 1}]
    ok, result = validate_order_payload(items, None, 1, 180)
    assert ok is False
    assert "Дублікат" in result


def test_validate_order_payload_total_mismatch(catalog):
    items = [{"product_id": 1, "quantity": 1}]
    ok, result = validate_order_payload(items, None, 1, 999)
    assert ok is False
    assert "Сума не збігається" in result


def test_validate_order_payload_success_without_promotion(catalog, monkeypatch):
    monkeypatch.setattr("services.validation.check_promotion", lambda total: 0)
    items = [{"product_id": 1, "quantity": 2}]
    ok, result = validate_order_payload(items, None, 1, 200)
    assert ok is True
    assert result["subtotal"] == 200
    assert result["total_price"] == 200
    assert result["promotion_discount"] == 0


def test_validate_order_payload_success_with_promotion(catalog, monkeypatch):
    monkeypatch.setattr("services.validation.check_promotion", lambda total: 50 if total >= 500 else 0)
    items = [{"product_id": 1, "quantity": 5}]
    ok, result = validate_order_payload(items, None, 1, 450)
    assert ok is True
    assert result["subtotal"] == 500
    assert result["promotion_discount"] == 50
    assert result["total_price"] == 450


def test_validate_order_payload_with_coupon(catalog, monkeypatch):
    monkeypatch.setattr(
        "services.validation.check_coupon",
        lambda code, user_id, subtotal: {
            "valid": True,
            "discount": 20,
            "message": "ok",
        },
    )
    items = [{"product_id": 1, "quantity": 1}]
    ok, result = validate_order_payload(items, "SAVE20", 1, 80)
    assert ok is True
    assert result["coupon_discount"] == 20
    assert result["total_price"] == 80
    assert result["promotion_discount"] == 0


def test_validate_order_payload_coupon_rounds_final_price_up(catalog, monkeypatch):
    monkeypatch.setattr(
        "services.validation.check_coupon",
        lambda code, user_id, subtotal: {
            "valid": True,
            "discount": 17,
            "message": "ok",
        },
    )
    items = [{"product_id": 3, "quantity": 1}]
    ok, result = validate_order_payload(items, "SAVE10", 1, 160)
    assert ok is True
    assert result["subtotal"] == 170
    assert result["coupon_discount"] == 10
    assert result["total_price"] == 160
    assert result["promotion_discount"] == 0


def test_validate_order_payload_promotion_rounds_final_price_up(catalog, monkeypatch):
    monkeypatch.setattr("services.validation.check_promotion", lambda total: 51 if total >= 500 else 0)
    items = [{"product_id": 4, "quantity": 1}]
    ok, result = validate_order_payload(items, None, 1, 460)
    assert ok is True
    assert result["subtotal"] == 510
    assert result["promotion_discount"] == 50
    assert result["total_price"] == 460


def test_validate_order_payload_rejects_unrounded_client_total(catalog, monkeypatch):
    monkeypatch.setattr("services.validation.check_promotion", lambda total: 51 if total >= 500 else 0)
    items = [{"product_id": 4, "quantity": 1}]
    ok, result = validate_order_payload(items, None, 1, 459)
    assert ok is False
    assert "Сума не збігається" in result


def test_validate_order_payload_invalid_coupon(catalog, monkeypatch):
    monkeypatch.setattr(
        "services.validation.check_coupon",
        lambda code, user_id, subtotal: {"valid": False, "message": "Купон не знайдено ❌"},
    )
    items = [{"product_id": 1, "quantity": 1}]
    ok, result = validate_order_payload(items, "BAD", 1, 100)
    assert ok is False
    assert "Купон" in result


def test_validate_order_payload_unknown_filament(catalog):
    items = [{"product_id": 2, "quantity": 1, "filament_id": "missing"}]
    ok, result = validate_order_payload(items, None, 1, 180)
    assert ok is False
    assert "філаменту" in result


def test_is_contract_product():
    assert is_contract_product({"contractPrice": True}) is True
    assert is_contract_product({"price": 10}) is False


def test_build_personal_coupon_notification():
    html = build_personal_coupon_notification(
        "VIP2026",
        "percent",
        15,
        min_order=300,
        one_per_user=1,
        expires_at="2026-08-31",
    )
    assert "VIP2026" in html
    assert "15%" in html
    assert "Від суми: 300 ₴" in html
    assert "Діє до:" in html
    assert "Одноразовий" in html
    assert "Промокод" in html
    assert "/mycoupons" in html


def test_build_sales_list_product_links():
    items = [
        {"id": 42, "emoji": "🎁", "name": "Тестовий товар", "price": 80, "oldPrice": 100},
    ]
    html = build_sales_list(items)
    assert f'href="{BOT_LINK_BASE}42"' in html
    assert product_link("🎁 Тестовий товар", 42) in html
    assert "80 ₴" in html
    assert "Економія: 20 ₴" in html
