"""Поля оформлення (доставка, контакт) і довідники Нової Пошти."""

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import delivery
from services.validation import (
    normalize_phone,
    split_personalization,
    validate_checkout_fields,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0501234567", "+380501234567"),
        ("+38 (050) 123-45-67", "+380501234567"),
        ("380501234567", "+380501234567"),
        ("501234567", "+380501234567"),
        ("12345", None),
        ("", None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_split_personalization():
    assert split_personalization("Олег · побажання: золотий") == ("Олег", "золотий")
    assert split_personalization("AA 1234 BB") == ("AA 1234 BB", "")


def test_checkout_legacy_without_delivery_is_ok():
    ok, fields = validate_checkout_fields({})
    assert ok and fields == {"source": "miniapp"}


def test_checkout_np_requires_contact_and_branch():
    base = {"delivery_method": "np", "recipient_name": "Іван Петренко", "phone": "0501234567",
            "city": "Київ", "branch": "Відділення №1"}
    ok, fields = validate_checkout_fields(base)
    assert ok
    assert fields["phone"] == "+380501234567"
    assert fields["pay_method"] == "card"
    for missing, msg in (("phone", "телефону"), ("branch", "відділення"), ("city", "місто"),
                         ("recipient_name", "ім'я")):
        bad = {k: v for k, v in base.items() if k != missing}
        ok, err = validate_checkout_fields(bad)
        assert not ok and msg in err


def test_checkout_self_pickup_needs_nothing_more():
    ok, fields = validate_checkout_fields({"delivery_method": "self"})
    assert ok and fields["delivery_method"] == "self"


def test_checkout_rejects_unknown_method():
    ok, err = validate_checkout_fields({"delivery_method": "drone"})
    assert not ok


def test_available_methods_np_always_lookup_needs_key(monkeypatch):
    monkeypatch.setattr(delivery.config, "NOVAPOSHTA_API_KEY", "")
    assert delivery.available_methods() == ["np", "self"]
    assert delivery.np_lookup_enabled() is False
    monkeypatch.setattr(delivery.config, "NOVAPOSHTA_API_KEY", "k")
    assert delivery.available_methods() == ["np", "self"]
    assert delivery.np_lookup_enabled() is True


def test_np_cities_cached_and_filtered(monkeypatch):
    delivery.clear_cache()
    calls = []

    async def fake_call(model, method, props):
        calls.append((model, method))
        return [{"Addresses": [
            {"DeliveryCity": "ref1", "MainDescription": "Київ", "Present": "м. Київ", "Area": "Київська", "Region": ""},
            {"DeliveryCity": "", "MainDescription": "Село без НП", "Present": "с. Х"},
        ]}]

    monkeypatch.setattr(delivery, "_np_call", fake_call)
    first = asyncio.run(delivery.np_cities("  Київ "))
    second = asyncio.run(delivery.np_cities("київ"))
    assert [c["ref"] for c in first] == ["ref1"] and first == second
    assert len(calls) == 1
    assert asyncio.run(delivery.np_cities("к")) == []


def test_np_warehouses_kind_filter(monkeypatch):
    delivery.clear_cache()

    async def fake_call(model, method, props):
        return [
            {"Ref": "a", "Number": "1", "Description": "Відділення №1", "CategoryOfWarehouse": "Branch"},
            {"Ref": "b", "Number": "2", "Description": "Поштомат №2", "CategoryOfWarehouse": "Postomat"},
        ]

    monkeypatch.setattr(delivery, "_np_call", fake_call)
    assert [w["ref"] for w in asyncio.run(delivery.np_warehouses("c", "all"))] == ["a", "b"]
    assert [w["ref"] for w in asyncio.run(delivery.np_warehouses("c", "postomat"))] == ["b"]
    assert [w["ref"] for w in asyncio.run(delivery.np_warehouses("c", "branch"))] == ["a"]
    assert asyncio.run(delivery.np_warehouses("")) == []


def test_np_call_without_key_is_unavailable(monkeypatch):
    monkeypatch.setattr(delivery.config, "NOVAPOSHTA_API_KEY", "")
    with pytest.raises(delivery.DeliveryUnavailable):
        asyncio.run(delivery._np_call("Address", "getWarehouses", {}))


def _wh(n, postomat_every=0):
    out = []
    for i in range(n):
        post = postomat_every and i % postomat_every == 0
        out.append({"Ref": f"r{i}", "Number": str(i), "Description": f"W{i}",
                    "CategoryOfWarehouse": "Postomat" if post else "Branch"})
    return out


def _paged_fake(total, calls, fail_on_page=None, postomat_every=0):
    items = _wh(total, postomat_every)

    async def fake_call(model, method, props):
        page, limit = int(props["Page"]), int(props["Limit"])
        calls.append(page)
        if fail_on_page == page:
            raise delivery.DeliveryUnavailable("request_failed")
        return items[(page - 1) * limit: page * limit]

    return fake_call


def test_np_warehouses_multi_page(monkeypatch):
    delivery.clear_cache()
    calls = []
    monkeypatch.setattr(delivery, "_np_call", _paged_fake(1120, calls))
    res = asyncio.run(delivery.np_warehouses("c"))
    assert len(res) == 1120 and res[-1]["ref"] == "r1119"
    assert calls == [1, 2, 3]


def test_np_warehouses_exact_page_multiple(monkeypatch):
    delivery.clear_cache()
    calls = []
    monkeypatch.setattr(delivery, "_np_call", _paged_fake(1000, calls))
    assert len(asyncio.run(delivery.np_warehouses("c"))) == 1000
    assert calls == [1, 2, 3]  # третя порожня сторінка завершує цикл


def test_np_warehouses_kind_filter_across_pages(monkeypatch):
    delivery.clear_cache()
    calls = []
    monkeypatch.setattr(delivery, "_np_call", _paged_fake(1200, calls, postomat_every=3))
    post = asyncio.run(delivery.np_warehouses("c", "postomat"))
    branch = asyncio.run(delivery.np_warehouses("c", "branch"))
    assert len(post) == 400 and len(branch) == 800
    assert all(w["kind"] == "postomat" for w in post)
    assert any(int(w["number"]) > 1000 for w in branch)


def test_np_warehouses_page_cap(monkeypatch):
    delivery.clear_cache()
    calls = []

    async def endless(model, method, props):
        calls.append(int(props["Page"]))
        return _wh(500)

    monkeypatch.setattr(delivery, "_np_call", endless)
    res = asyncio.run(delivery.np_warehouses("c"))
    assert calls == list(range(1, delivery.WAREHOUSE_MAX_PAGES + 1))
    assert len(res) == 500 * delivery.WAREHOUSE_MAX_PAGES


def test_np_warehouses_partial_failure_short_cache(monkeypatch):
    delivery.clear_cache()
    calls = []
    monkeypatch.setattr(delivery, "_np_call", _paged_fake(1200, calls, fail_on_page=2))
    res = asyncio.run(delivery.np_warehouses("c"))
    assert len(res) == 500
    exp = delivery._cache[("np_wh", "c", "all")][0]
    assert exp - delivery.time.monotonic() <= delivery.WAREHOUSE_PARTIAL_TTL_SEC + 1


def test_np_warehouses_first_page_failure_raises(monkeypatch):
    delivery.clear_cache()
    monkeypatch.setattr(delivery, "_np_call", _paged_fake(1200, [], fail_on_page=1))
    with pytest.raises(delivery.DeliveryUnavailable):
        asyncio.run(delivery.np_warehouses("c"))
    assert ("np_wh", "c", "all") not in delivery._cache


def test_np_warehouses_cache_hit_no_recall(monkeypatch):
    delivery.clear_cache()
    calls = []
    monkeypatch.setattr(delivery, "_np_call", _paged_fake(700, calls))
    asyncio.run(delivery.np_warehouses("c"))
    n = len(calls)
    asyncio.run(delivery.np_warehouses("c"))
    assert len(calls) == n
