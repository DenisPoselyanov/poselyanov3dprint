"""Tests for order contact URL helpers."""

from services.orders import tg_contact_url, tg_contact_keyboard_url, tg_contact_url_from_order


def test_tg_contact_url_with_username():
    assert tg_contact_url(123, "@MihoDmitriev") == "https://t.me/MihoDmitriev"


def test_tg_contact_url_with_handle_without_at():
    assert tg_contact_url(123, "MihoDmitriev") == "tg://user?id=123"


def test_tg_contact_url_first_name_not_handle():
    assert tg_contact_url(987654321, "Саня") == "tg://user?id=987654321"
    assert tg_contact_url(987654321, "Николай") == "tg://user?id=987654321"


def test_tg_contact_url_fallback_to_user_id():
    assert tg_contact_url(987654321, "невідомо") == "tg://user?id=987654321"
    assert tg_contact_url(987654321, "—") == "tg://user?id=987654321"
    assert tg_contact_url(987654321, None) == "tg://user?id=987654321"


def test_tg_contact_url_no_data():
    assert tg_contact_url(None, None) is None
    assert tg_contact_url(0, "невідомо") is None


def test_tg_contact_keyboard_url_with_username():
    assert tg_contact_keyboard_url(123, "@MihoDmitriev") == "https://t.me/MihoDmitriev"


def test_tg_contact_keyboard_url_skips_user_id_links():
    assert tg_contact_keyboard_url(987654321, "невідомо") == "tg://user?id=987654321"
    assert tg_contact_keyboard_url(987654321, "Саня") == "tg://user?id=987654321"
    assert tg_contact_keyboard_url(987654321, None) == "tg://user?id=987654321"
    assert tg_contact_keyboard_url(None, None) is None


def test_tg_contact_url_from_order():
    order = {"user_id": 555, "username": "@client"}
    assert tg_contact_url_from_order(order) == "https://t.me/client"

    order_no_handle = {"user_id": 555, "username": "невідомо"}
    assert tg_contact_url_from_order(order_no_handle) == "tg://user?id=555"
