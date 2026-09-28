import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from catalog_store import _row_to_product, _serialize_custom_fields


def test_empty_custom_fields_not_stored_as_quoted_string():
    assert _serialize_custom_fields(None) == ""
    assert _serialize_custom_fields("") == ""
    assert _serialize_custom_fields("Номер автомобіля") == "Номер автомобіля"
    assert _serialize_custom_fields(["a"]) == '["a"]'


def test_legacy_quoted_custom_fields_read_as_empty():
    product = _row_to_product({"id": 1, "name": "X", "custom_fields": '""'})
    assert "custom_fields" not in product


def test_public_product_hides_stl_link():
    import bot

    product = {"id": 1, "name": "X", "stlLink": "https://makerworld.com/x"}
    public = bot._public_product(product)
    assert "stlLink" not in public
    assert product["stlLink"]  # оригінал у кеші не змінюється
