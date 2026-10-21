"""Довідники Нової Пошти для оформлення: міста й відділення через наш бекенд з кешем.

Ключ API не потрапляє у фронтенд. Укрпошта поки не підключена: довідник її відділень
не перевірений, тому `available_methods` її не віддає (запасний варіант — НП + самовивіз).
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import logging
import re
import time

import aiohttp

import config

logger = logging.getLogger(__name__)

NP_URL = "https://api.novaposhta.ua/v2.0/json/"
CITY_TTL_SEC = 24 * 3600
WAREHOUSE_TTL_SEC = 3 * 3600
WAREHOUSE_PAGE_SIZE = 100  # одна сторінка: перші N відділень, решта — через пошук
WAREHOUSE_SEARCH_TTL_SEC = 30 * 60
WAREHOUSE_NEG_TTL_SEC = 60  # відмова НП (api_error) на місто — не стукаємо повторно
WAREHOUSE_QUERY_MAX = 40
WAREHOUSE_PAGE_DELAY_SEC = 1.0  # пауза перед повтором при «To many requests»
WAREHOUSE_ATTEMPTS = 2
NP_CONCURRENCY = 4
REQUEST_TIMEOUT_SEC = 8
CACHE_MAX = 2000
SEARCH_CACHE_MAX = 500  # окремий кеш пошуку: випадкові запити не витісняють міста й перші сторінки

_REF_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_cache: OrderedDict[tuple, tuple[float, object]] = OrderedDict()
_search_cache: OrderedDict[tuple, tuple[float, object]] = OrderedDict()


class DeliveryUnavailable(Exception):
    """API перевізника недоступне: фронт переходить на текстове поле."""


def available_methods() -> list[str]:
    """Способи доставки для `GET /api/config`.

    НП обовʼязкова (рішення власника) і доступна завжди: без ключа API фронт переходить
    на ручне введення міста й відділення (`np_lookup_enabled` = false). Самовивіз — завжди.
    """
    return ["np", "self"]


def np_lookup_enabled() -> bool:
    """Чи працюють довідники міст/відділень (потрібен NOVAPOSHTA_API_KEY)."""
    return bool(config.NOVAPOSHTA_API_KEY)


def _store(key: tuple) -> OrderedDict:
    # ("np_wh", ref, kind, q) із непорожнім q — окремий обмежений кеш
    if key and key[0] == "np_wh" and len(key) == 4 and key[3]:
        return _search_cache
    return _cache


def _cache_get(key: tuple):
    store = _store(key)
    hit = store.get(key)
    if hit and hit[0] > time.monotonic():
        store.move_to_end(key)
        return hit[1]
    return None


def _cache_put(key: tuple, value, ttl: float) -> None:
    store = _store(key)
    limit = SEARCH_CACHE_MAX if store is _search_cache else CACHE_MAX
    if key not in store and len(store) >= limit:
        now = time.monotonic()
        for k in [k for k, (exp, _) in store.items() if exp <= now]:
            store.pop(k, None)
        while len(store) >= limit:
            store.popitem(last=False)  # LRU: витісняємо найстаріший, а не чистимо все
    store[key] = (time.monotonic() + ttl, value)
    store.move_to_end(key)


def clear_cache() -> None:
    _cache.clear()
    _search_cache.clear()


_np_sem: asyncio.Semaphore | None = None
_np_sem_loop = None


def _get_sem() -> asyncio.Semaphore:
    global _np_sem, _np_sem_loop
    loop = asyncio.get_running_loop()
    if _np_sem is None or _np_sem_loop is not loop:
        _np_sem, _np_sem_loop = asyncio.Semaphore(NP_CONCURRENCY), loop
    return _np_sem


async def _np_call(model: str, method: str, props: dict) -> list:
    """Запит до НП; одночасно не більше NP_CONCURRENCY."""
    if not config.NOVAPOSHTA_API_KEY:
        raise DeliveryUnavailable("no_api_key")
    async with _get_sem():
        return await _np_request(model, method, props)


async def _np_request(model: str, method: str, props: dict) -> list:
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
        errors = (body or {}).get("errors") if isinstance(body, dict) else None
        logger.warning("Нова Пошта %s.%s відхилила запит: %s", model, method, errors)
        if any("many requests" in str(e).lower() for e in (errors or [])):
            raise DeliveryUnavailable("rate_limited")
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


def _clean_query(q) -> str:
    q = "".join(ch if ch.isprintable() else " " for ch in str(q or ""))
    return " ".join(q.split()).lower()[:WAREHOUSE_QUERY_MAX]


async def np_warehouses(city_ref: str, kind: str = "all", q: str = "") -> dict:
    """Відділення міста: пошук за `q` (FindByString) або перша сторінка без нього.

    Повертає {"items": [...], "truncated": bool}; truncated — відповідь НП заповнила сторінку,
    тож є ще відділення, які знаходяться уточненням пошуку.
    """
    city_ref = str(city_ref or "").strip()
    if not _REF_RE.match(city_ref):
        return {"items": [], "truncated": False}
    if kind not in ("all", "branch", "postomat"):
        kind = "all"
    q = _clean_query(q)
    key = ("np_wh", city_ref, kind, q)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    if _cache_get(("np_wh_neg", city_ref, q)) is not None:
        raise DeliveryUnavailable("api_error")
    props = {"CityRef": city_ref, "Limit": str(WAREHOUSE_PAGE_SIZE), "Page": "1", "Language": "UA"}
    if q:
        props["FindByString"] = q
    data = None
    for attempt in range(1, WAREHOUSE_ATTEMPTS + 1):
        try:
            data = await _np_call("Address", "getWarehouses", props)
            break
        except DeliveryUnavailable as exc:
            reason = str(exc)
            if reason == "api_error":
                _cache_put(("np_wh_neg", city_ref, q), True, WAREHOUSE_NEG_TTL_SEC)
            if reason not in ("rate_limited", "request_failed") or attempt == WAREHOUSE_ATTEMPTS:
                raise
            await asyncio.sleep(WAREHOUSE_PAGE_DELAY_SEC)
    result = {
        "items": _normalize_warehouses(data, kind),
        "truncated": len(data) >= WAREHOUSE_PAGE_SIZE,
    }
    _cache_put(key, result, WAREHOUSE_SEARCH_TTL_SEC if q else WAREHOUSE_TTL_SEC)
    return result
