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


@pytest.fixture(autouse=True)
def _no_np_delays(monkeypatch):
    monkeypatch.setattr(delivery, "WAREHOUSE_PAGE_DELAY_SEC", 0)


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


def test_np_call_without_key_is_unavailable(monkeypatch):
    monkeypatch.setattr(delivery.config, "NOVAPOSHTA_API_KEY", "")
    with pytest.raises(delivery.DeliveryUnavailable):
        asyncio.run(delivery._np_call("Address", "getWarehouses", {}))


REF = "8d5a980d-391c-11dd-90d9-001a92567626"


def _wh(n, postomat_every=0):
    out = []
    for i in range(n):
        post = postomat_every and i % postomat_every == 0
        out.append({"Ref": f"r{i}", "Number": str(i), "Description": f"W{i}",
                    "CategoryOfWarehouse": "Postomat" if post else "Branch"})
    return out


def _fake(data, calls, raises=None):
    async def fake_call(model, method, props):
        calls.append(props)
        if raises:
            exc = raises.pop(0) if isinstance(raises, list) else raises
            if exc:
                raise exc
        return data

    return fake_call


def _wh_items(res):
    return [w["ref"] for w in res["items"]]


def test_np_warehouses_search_single_call(monkeypatch):
    delivery.clear_cache()
    calls = []
    monkeypatch.setattr(delivery, "_np_call", _fake(_wh(3), calls))
    res = asyncio.run(delivery.np_warehouses(REF, "all", "  15\x00 Хрещатик "))
    assert len(calls) == 1
    assert calls[0]["FindByString"] == "15 хрещатик" and calls[0]["Limit"] == "100"
    assert res == {"items": res["items"], "truncated": False} and len(res["items"]) == 3
    # кеш за нормалізованим q
    asyncio.run(delivery.np_warehouses(REF, "all", "15 ХРЕЩАТИК"))
    assert len(calls) == 1


def test_np_warehouses_query_clamped(monkeypatch):
    delivery.clear_cache()
    calls = []
    monkeypatch.setattr(delivery, "_np_call", _fake([], calls))
    asyncio.run(delivery.np_warehouses(REF, "all", "x" * 100))
    assert len(calls[0]["FindByString"]) == delivery.WAREHOUSE_QUERY_MAX


def test_np_warehouses_no_q_first_page_truncated(monkeypatch):
    delivery.clear_cache()
    calls = []
    monkeypatch.setattr(delivery, "_np_call", _fake(_wh(100), calls))
    res = asyncio.run(delivery.np_warehouses(REF))
    assert len(calls) == 1 and "FindByString" not in calls[0]
    assert res["truncated"] is True and len(res["items"]) == 100
    exp = delivery._cache[("np_wh", REF, "all", "")][0]
    assert exp - delivery.time.monotonic() > delivery.WAREHOUSE_SEARCH_TTL_SEC


def test_np_warehouses_kind_filter(monkeypatch):
    delivery.clear_cache()
    calls = []
    monkeypatch.setattr(delivery, "_np_call", _fake(_wh(6, postomat_every=3), calls))
    assert len(asyncio.run(delivery.np_warehouses(REF, "all"))["items"]) == 6
    post = asyncio.run(delivery.np_warehouses(REF, "postomat"))
    branch = asyncio.run(delivery.np_warehouses(REF, "branch"))
    assert _wh_items(post) == ["r0", "r3"] and len(branch["items"]) == 4
    assert post["truncated"] is False


def test_np_warehouses_invalid_ref_no_upstream(monkeypatch):
    delivery.clear_cache()
    calls = []
    monkeypatch.setattr(delivery, "_np_call", _fake(_wh(2), calls))
    for bad in ("", "c", "../x", REF + "x"):
        assert asyncio.run(delivery.np_warehouses(bad)) == {"items": [], "truncated": False}
    assert calls == []


def test_np_warehouses_api_error_negative_cache(monkeypatch):
    delivery.clear_cache()
    calls = []
    monkeypatch.setattr(delivery, "_np_call", _fake([], calls, raises=delivery.DeliveryUnavailable("api_error")))
    for q in ("", "5", "6"):
        with pytest.raises(delivery.DeliveryUnavailable):
            asyncio.run(delivery.np_warehouses(REF, "all", q))
    assert len(calls) == 1  # без повторів і без нових звернень


def test_np_warehouses_rate_limited_retry_then_success(monkeypatch):
    delivery.clear_cache()
    calls = []
    err = [delivery.DeliveryUnavailable("rate_limited"), None]
    monkeypatch.setattr(delivery, "_np_call", _fake(_wh(2), calls, raises=err))
    assert len(asyncio.run(delivery.np_warehouses(REF, "all", "1"))["items"]) == 2
    assert len(calls) == 2


def test_np_warehouses_gives_up_after_two_attempts(monkeypatch):
    delivery.clear_cache()
    calls = []
    errs = [delivery.DeliveryUnavailable("request_failed")] * 5
    monkeypatch.setattr(delivery, "_np_call", _fake([], calls, raises=errs))
    with pytest.raises(delivery.DeliveryUnavailable):
        asyncio.run(delivery.np_warehouses(REF))
    assert len(calls) == delivery.WAREHOUSE_ATTEMPTS
    assert ("np_wh", REF, "all", "") not in delivery._cache


def test_np_call_maps_many_requests_to_rate_limited(monkeypatch):
    class Resp:
        async def json(self, content_type=None):
            return {"success": False, "errors": ["To many requests"]}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class Sess(Resp):
        def __init__(self, *a, **k):
            pass

        def post(self, *a, **k):
            return Resp()

    monkeypatch.setattr(delivery.config, "NOVAPOSHTA_API_KEY", "k")
    monkeypatch.setattr(delivery.aiohttp, "ClientSession", Sess)
    with pytest.raises(delivery.DeliveryUnavailable, match="rate_limited"):
        asyncio.run(delivery._np_call("Address", "getWarehouses", {}))


def test_np_call_concurrency_bounded(monkeypatch):
    monkeypatch.setattr(delivery.config, "NOVAPOSHTA_API_KEY", "k")
    state = {"now": 0, "max": 0}

    async def fake_request(model, method, props):
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return []

    monkeypatch.setattr(delivery, "_np_request", fake_request)

    async def run():
        await asyncio.gather(*[delivery._np_call("A", "b", {}) for _ in range(12)])

    asyncio.run(run())
    assert state["max"] == delivery.NP_CONCURRENCY


def test_handler_passes_q_and_returns_items_truncated(monkeypatch):
    import json

    import bot
    from aiohttp.test_utils import make_mocked_request

    seen = []

    async def fake_wh(city, kind, q):
        seen.append((city, kind, q))
        return {"items": [{"ref": "a"}], "truncated": True}

    monkeypatch.setattr(bot, "resolve_request_user", lambda req, data: ({"user_id": 7}, None))
    monkeypatch.setattr(bot, "allow_delivery_lookup", lambda uid: True)
    monkeypatch.setattr(bot.delivery, "np_warehouses", fake_wh)
    req = make_mocked_request("GET", f"/delivery/np/warehouses?city={REF}&kind=branch&q=15")
    body = json.loads(asyncio.run(bot.handle_np_warehouses(req)).text)
    assert seen == [(REF, "branch", "15")]
    assert body == {"ok": True, "items": [{"ref": "a"}], "truncated": True}

    monkeypatch.setattr(bot, "allow_delivery_lookup", lambda uid: False)
    assert asyncio.run(bot.handle_np_warehouses(req)).status == 429
