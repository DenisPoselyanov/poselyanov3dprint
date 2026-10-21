"""Довідники Нової Пошти для оформлення: міста й відділення через наш бекенд з кешем.

Ключ API не потрапляє у фронтенд. Укрпошта поки не підключена: довідник її відділень
не перевірений, тому `available_methods` її не віддає (запасний варіант — НП + самовивіз).
"""

from __future__ import annotations

import logging
import time

import aiohttp

import config

logger = logging.getLogger(__name__)

NP_URL = "https://api.novaposhta.ua/v2.0/json/"
CITY_TTL_SEC = 24 * 3600
WAREHOUSE_TTL_SEC = 3 * 3600
REQUEST_TIMEOUT_SEC = 8
CACHE_MAX = 2000

_cache: dict[tuple, tuple[float, object]] = {}


class DeliveryUnavailable(Exception):
    """API перевізника недоступне: фронт переходить на текстове поле."""


def available_methods() -> list[str]:
    """Способи доставки для `GET /api/config`: НП лише з ключем, самовивіз завжди."""
    methods = []
    if config.NOVAPOSHTA_API_KEY:
        methods.append("np")
    methods.append("self")
    return methods


def _cache_get(key: tuple):
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    return None


def _cache_put(key: tuple, value, ttl: float) -> None:
    if len(_cache) >= CACHE_MAX:
        now = time.monotonic()
        for k in [k for k, (exp, _) in _cache.items() if exp <= now]:
            _cache.pop(k, None)
        if len(_cache) >= CACHE_MAX:
            _cache.clear()
    _cache[key] = (time.monotonic() + ttl, value)


def clear_cache() -> None:
    _cache.clear()


async def _np_call(model: str, method: str, props: dict) -> list:
    if not config.NOVAPOSHTA_API_KEY:
        raise DeliveryUnavailable("no_api_key")
    payload = {
        "apiKey": config.NOVAPOSHTA_API_KEY,
        "modelName": model,
        "calledMethod": method,
        "methodProperties": props,
    }
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SEC)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(NP_URL, json=payload) as resp:
                body = await resp.json(content_type=None)
    except Exception as exc:
        logger.warning("Нова Пошта %s.%s недоступна: %s", model, method, exc)
        raise DeliveryUnavailable("request_failed") from exc
    if not isinstance(body, dict) or not body.get("success"):
        logger.warning("Нова Пошта %s.%s відхилила запит: %s", model, method, (body or {}).get("errors"))
        raise DeliveryUnavailable("api_error")
    return body.get("data") or []


def _normalize_cities(data: list) -> list[dict]:
    out = []
    for block in data:
        for addr in block.get("Addresses") or []:
            ref = addr.get("DeliveryCity")
            if not ref:  # населений пункт без відділень НП
                continue
            out.append(
                {
                    "ref": ref,
                    "name": addr.get("MainDescription") or addr.get("Present") or "",
                    "present": addr.get("Present") or "",
                    "area": addr.get("Area") or "",
                    "region": addr.get("Region") or "",
                }
            )
    return out


async def np_cities(query: str, limit: int = 10) -> list[dict]:
    q = " ".join(str(query or "").lower().split())
    if len(q) < 2:
        return []
    key = ("np_cities", q, limit)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    data = await _np_call("Address", "searchSettlements", {"CityName": q, "Limit": str(limit)})
    result = _normalize_cities(data)
    _cache_put(key, result, CITY_TTL_SEC)
    return result


def _normalize_warehouses(data: list, kind: str) -> list[dict]:
    out = []
    for w in data:
        is_postomat = (w.get("CategoryOfWarehouse") or "") == "Postomat"
        if kind == "postomat" and not is_postomat:
            continue
        if kind == "branch" and is_postomat:
            continue
        out.append(
            {
                "ref": w.get("Ref") or "",
                "number": w.get("Number") or "",
                "name": w.get("Description") or "",
                "kind": "postomat" if is_postomat else "branch",
            }
        )
    return out


async def np_warehouses(city_ref: str, kind: str = "all", limit: int = 500) -> list[dict]:
    city_ref = str(city_ref or "").strip()
    if not city_ref:
        return []
    if kind not in ("all", "branch", "postomat"):
        kind = "all"
    key = ("np_wh", city_ref, kind)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    data = await _np_call(
        "Address", "getWarehouses", {"CityRef": city_ref, "Limit": str(limit), "Language": "UA"}
    )
    result = _normalize_warehouses(data, kind)
    _cache_put(key, result, WAREHOUSE_TTL_SEC)
    return result
