"""Роль «редактор» (ADMIN_EDITOR_IDS): лише пропозиції ШІ, додавання товару, категорії/добірки на читання."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")

OWNER, EDITOR, STRANGER = 111, 222, 333


@pytest.fixture
def env(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "shop.db"))
    from services.db_utils import init_db

    init_db()
    yield tmp_path


def _http(monkeypatch, steps):
    import auth
    import bot
    import config
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from routes.setup import register_http_routes

    monkeypatch.setattr(config, "VALIDATE_INIT_DATA", True)
    monkeypatch.setattr(config, "ALLOW_LOCAL_NETWORK", False)
    monkeypatch.setattr(config, "OWNER_ID", OWNER)
    monkeypatch.setattr(config, "EDITOR_IDS", frozenset({EDITOR}))
    monkeypatch.setattr(config, "ADMIN_BYPASS_TOKEN", "tok")
    monkeypatch.setattr(auth, "is_local_dev_origin", lambda request: False)
    users = {"owner": OWNER, "editor": EDITOR, "stranger": STRANGER}
    monkeypatch.setattr(
        auth, "validate_telegram_init_data", lambda raw: {"user_id": users[raw]} if raw in users else None
    )

    async def go():
        app = web.Application()
        register_http_routes(app, bot)
        async with TestClient(TestServer(app)) as client:
            return await steps(client)

    return asyncio.run(go())


def _as(who):
    return {"X-Telegram-Init-Data": who}


def test_parse_editor_ids():
    import config

    assert config._parse_id_list("1, 2;3,,x") == frozenset({1, 2, 3})
    assert config._parse_id_list("") == frozenset()


def test_whoami_roles(env, monkeypatch):
    async def steps(client):
        owner = await (await client.get("/api/admin/whoami", headers=_as("owner"))).json()
        editor = await (await client.get("/api/admin/whoami", headers=_as("editor"))).json()
        stranger = await client.get("/api/admin/whoami", headers=_as("stranger"))
        anon = await client.get("/api/admin/whoami")
        return owner["role"], editor["role"], stranger.status, anon.status

    assert _http(monkeypatch, steps) == ("admin", "editor", 403, 403)


def test_editor_allowed_endpoints(env, monkeypatch):
    async def steps(client):
        h = _as("editor")
        return [
            (await client.get("/api/admin/ai-suggestions", headers=h)).status,
            (await client.get("/api/categories?includeInactive=true", headers=h)).status,
        ]

    assert _http(monkeypatch, steps) == [200, 200]


def test_editor_forbidden_endpoints(env, monkeypatch):
    async def steps(client):
        h = _as("editor")
        calls = [
            ("get", "/api/admin/products"), ("get", "/api/orders"), ("get", "/api/admin/overview"),
            ("get", "/api/admin/users"), ("get", "/api/coupons"), ("get", "/api/admin/settings"),
            ("put", "/api/products/1"), ("delete", "/api/products/1"), ("put", "/api/products/1/status"),
            ("post", "/api/categories"), ("post", "/api/admin/collections"), ("get", "/api/admin/collections"),
            ("get", "/api/admin/products/1/collections"), ("put", "/api/admin/products/1/collections"), ("post", "/api/filaments"),
        ]
        return [(m, u, (await getattr(client, m)(u, headers=h)).status) for m, u in calls]

    for method, url, status in _http(monkeypatch, steps):
        assert status == 403, (method, url, status)


def test_stranger_and_anonymous_denied_everywhere(env, monkeypatch):
    async def steps(client):
        out = []
        for headers in ({}, _as("stranger")):
            for m, u in (("get", "/api/admin/ai-suggestions"), ("post", "/api/products"),
                         ("post", "/api/upload-photo"), ("get", "/api/categories?includeInactive=true"),
                         ("get", "/api/admin/collections")):
                out.append((m, u, (await getattr(client, m)(u, headers=headers)).status))
        return out

    for method, url, status in _http(monkeypatch, steps):
        assert status == 403, (method, url, status)


def test_editor_can_create_product(env, monkeypatch):
    async def steps(client):
        resp = await client.post(
            "/api/products", headers=_as("editor"),
            json={"name": "Тест", "price": 100, "cat": "decor", "emoji": "📦", "mat": "PLA", "description": "x"},
        )
        return resp.status

    # 201 — створено, 400 — валідація даних; головне, що не 403
    assert _http(monkeypatch, steps) in (201, 400)
