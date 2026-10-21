"""Замовлення вручну та CSV-експорт."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.manual_orders import orders_to_csv, parse_manual_order


def _data(**over):
    base = {
        "customer_name": "Олена",
        "username": "@olena_k",
        "phone": "0501234567",
        "source": "instagram",
        "items": [{"product_name": "Брелок", "quantity": 2, "price": 80, "filament": "Чорний"}],
    }
    base.update(over)
    return base


def test_parse_ok_computes_total_and_normalizes():
    ok, p = parse_manual_order(_data())
    assert ok
    assert p["total_price"] == 160
    assert p["username"] == "@olena_k"
    assert p["extra"]["phone"] == "+380501234567"
    assert p["extra"]["source"] == "instagram"
    assert p["status"] == "confirmed"
    assert p["items"][0]["filament_name"] == "Чорний"


def test_parse_rejects_bad_input():
    assert parse_manual_order(_data(customer_name=""))[0] is False
    assert parse_manual_order(_data(items=[]))[0] is False
    assert parse_manual_order(_data(items=[{"product_name": "X", "quantity": 0, "price": 1}]))[0] is False
    assert parse_manual_order(_data(items=[{"product_name": "X", "quantity": 1, "price": -5}]))[0] is False
    assert parse_manual_order(_data(source="miniapp"))[0] is False
    assert parse_manual_order(_data(phone="123"))[0] is False
    assert parse_manual_order(_data(username="a b"))[0] is False
    assert parse_manual_order(_data(status="bogus"))[0] is False


def test_csv_escapes_formulas_and_has_rows():
    rows = [
        {"id": 7, "ordered_at": "2026-10-21 10:00:00", "status": "shipped", "first_name": "=HYPERLINK(1)",
         "username": "@u", "total_price": 160, "ttn": "20450000000001",
         "items": [{"product_name": "Брелок", "quantity": 2, "filament": "Чорний"}]},
    ]
    text = orders_to_csv(rows)
    lines = text.strip().split("\r\n")
    assert lines[0].startswith("№;Дата;Статус")
    assert "Відправлено" in lines[1]
    assert "Брелок ×2 (Чорний)" in lines[1]
    assert "'=HYPERLINK(1)" in lines[1]
