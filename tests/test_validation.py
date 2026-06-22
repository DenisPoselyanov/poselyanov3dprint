import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")

from catalog_store import validate_product_prices, is_contract_product  # noqa: E402
from bot import check_coupon, check_promotion, validate_order_payload  # noqa: E402


def test_validate_product_prices_sale():
    ok, err = validate_product_prices(100, 150)
    assert ok is True
    assert err is None


def test_validate_product_prices_invalid_sale():
    ok, err = validate_product_prices(150, 100)
    assert ok is False


def test_check_promotion_disabled(monkeypatch):
    import bot
    monkeypatch.setattr(bot, "PROMOTION_ENABLED", False)
    assert bot.check_promotion(1000) == 0


def test_check_promotion_at_threshold():
    assert check_promotion(500) == 50


def test_validate_order_payload_empty():
    ok, result = validate_order_payload([], None, 1, 0)
    assert ok is False
    assert result == "Порожній кошик"


def test_is_contract_product():
    assert is_contract_product({"contractPrice": True}) is True
    assert is_contract_product({"price": 10}) is False
