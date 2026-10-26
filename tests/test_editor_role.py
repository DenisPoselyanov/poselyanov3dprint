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


def test_editor_cannot_create_product_directly(env, monkeypatch):
    async def steps(client):
        resp = await client.post(
            "/api/products", headers=_as("editor"),
            json={"name": "Тест", "price": 100, "cat": "decor", "emoji": "📦", "mat": "PLA", "description": "x"},
        )
        return resp.status

    assert _http(monkeypatch, steps) == 403


def _insert_suggestion(name="Тест"):
    from db_core import db_connect

    conn = db_connect()
    cur = conn.execute("INSERT INTO ai_suggestions (status, name, data) VALUES ('new', ?, '{}')", (name,))
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid


def _suggestion_row(sid):
    from db_core import db_connect

    conn = db_connect()
    row = conn.execute("SELECT status, name FROM ai_suggestions WHERE id = ?", (sid,)).fetchone()
    conn.close()
    return tuple(row)


def test_editor_draft_goes_to_review_slot_not_catalog(env, monkeypatch):
    sid = _insert_suggestion("Кролик")
    draft = {"name": "Кролик v2", "price": 150, "cat": "decor", "emoji": "🐰", "mat": "PLA", "photos": ["https://x.test/a.jpg"]}

    async def steps(client):
        resp = await client.put(f"/api/admin/ai-suggestions/{sid}", headers=_as("editor"), json={"product": draft})
        listing = await (await client.get("/api/admin/ai-suggestions", headers=_as("editor"))).json()
        products = await (await client.get("/api/admin/products", headers=_as("owner"))).json()
        return resp.status, listing["items"], products

    status, items, products = _http(monkeypatch, steps)
    assert status == 200
    assert _suggestion_row(sid) == ("review", "Кролик v2")
    item = next(i for i in items if i["id"] == sid)
    assert item["status"] == "review" and item["product"]["photos"] == ["https://x.test/a.jpg"]
    assert "Кролик v2" not in [p.get("name") for p in (products if isinstance(products, list) else products.get("products", []))]


def test_editor_cannot_change_status_or_publish(env, monkeypatch):
    sid = _insert_suggestion()

    async def steps(client):
        h = _as("editor")
        return [
            (await client.put(f"/api/admin/ai-suggestions/{sid}", headers=h, json={"status": "published", "productId": 1})).status,
            (await client.put(f"/api/admin/ai-suggestions/{sid}", headers=h, json={"status": "new"})).status,
            (await client.put(f"/api/admin/ai-suggestions/{sid}", headers=h, json={"status": "rejected"})).status,
            (await client.delete(f"/api/admin/ai-suggestions/{sid}", headers=h)).status,
        ]

    assert _http(monkeypatch, steps) == [403, 403, 200, 403]
    assert _suggestion_row(sid)[0] == "rejected"  # редактор може відхилити відкриту пропозицію


def test_owner_can_set_status_after_review(env, monkeypatch):
    sid = _insert_suggestion()

    async def steps(client):
        await client.put(f"/api/admin/ai-suggestions/{sid}", headers=_as("editor"), json={"product": {"name": "Тест"}})
        return (await client.put(f"/api/admin/ai-suggestions/{sid}", headers=_as("owner"), json={"status": "rejected"})).status

    assert _http(monkeypatch, steps) == 200
    assert _suggestion_row(sid)[0] == "rejected"


def test_editor_cannot_touch_published_or_rejected(env, monkeypatch):
    from db_core import db_connect

    conn = db_connect()
    pub = conn.execute("INSERT INTO ai_suggestions (status, name, data, product_id) VALUES ('published', 'Опублікований', '{}', 7)").lastrowid
    rej = conn.execute("INSERT INTO ai_suggestions (status, name, data) VALUES ('rejected', 'Відхилений', '{}')").lastrowid
    conn.commit()
    conn.close()

    async def steps(client):
        h = _as("editor")
        return [
            (await client.put(f"/api/admin/ai-suggestions/{pub}", headers=h, json={"product": {"name": "Зміна"}})).status,
            (await client.put(f"/api/admin/ai-suggestions/{rej}", headers=h, json={"product": {"name": "Зміна"}})).status,
        ]

    assert _http(monkeypatch, steps) == [400, 400]
    assert _suggestion_row(pub) == ("published", "Опублікований")
    assert _suggestion_row(rej) == ("rejected", "Відхилений")


def test_editor_draft_ignores_admin_only_fields(env, monkeypatch):
    import json
    from db_core import db_connect

    sid = _insert_suggestion("Кролик")

    async def steps(client):
        draft = {"name": "Кролик", "price": 150, "sourceStats": {"downloads": 999}, "productId": 7, "_admin": True}
        return (await client.put(f"/api/admin/ai-suggestions/{sid}", headers=_as("editor"), json={"product": draft})).status

    assert _http(monkeypatch, steps) == 200
    conn = db_connect()
    data = json.loads(conn.execute("SELECT data FROM ai_suggestions WHERE id = ?", (sid,)).fetchone()[0])
    conn.close()
    assert data["price"] == 150
    assert "productId" not in data and "_admin" not in data and "sourceStats" not in data


def test_editor_draft_values_are_validated(env, monkeypatch):
    import json
    from db_core import db_connect

    sid = _insert_suggestion("Кролик")
    draft = {
        "name": "<img src=x onerror=alert(1)>",
        "stlLink": "javascript:alert(1)",
        "video": "http://insecure.test/v",
        "photos": ["javascript:alert(1)", "https://ok.test/a.jpg", "data:image/png;base64,AAAA"],
        "price": "abc",
        "hot": "yes",
        "status": "published",
    }

    async def steps(client):
        return (await client.put(f"/api/admin/ai-suggestions/{sid}", headers=_as("editor"), json={"product": draft})).status

    assert _http(monkeypatch, steps) == 200
    conn = db_connect()
    data = json.loads(conn.execute("SELECT data FROM ai_suggestions WHERE id = ?", (sid,)).fetchone()[0])
    conn.close()
    assert data["stlLink"] == "" and data["video"] == ""
    assert data["photos"] == ["https://ok.test/a.jpg"]
    assert "price" not in data and "hot" not in data and "status" not in data
    assert data["name"] == "<img src=x onerror=alert(1)>"  # зберігається як текст; у UI екранується


def test_editor_can_reject_open_suggestion_only(env, monkeypatch):
    from db_core import db_connect

    conn = db_connect()
    pub = conn.execute("INSERT INTO ai_suggestions (status, name, data, product_id) VALUES ('published', 'Опублікований', '{}', 7)").lastrowid
    conn.commit()
    conn.close()
    sid = _insert_suggestion("Відкритий")
    review = _insert_suggestion("На перевірці")

    async def steps(client):
        h = _as("editor")
        await client.put(f"/api/admin/ai-suggestions/{review}", headers=h, json={"product": {"name": "Чернетка"}})
        return [
            (await client.put(f"/api/admin/ai-suggestions/{sid}", headers=h, json={"status": "rejected"})).status,
            (await client.put(f"/api/admin/ai-suggestions/{review}", headers=h, json={"status": "rejected"})).status,
            (await client.put(f"/api/admin/ai-suggestions/{pub}", headers=h, json={"status": "rejected"})).status,
            (await client.put(f"/api/admin/ai-suggestions/{sid}", headers=h, json={"status": "rejected", "productId": 1})).status,
        ]

    assert _http(monkeypatch, steps) == [200, 200, 400, 403]
    assert _suggestion_row(sid)[0] == "rejected"
    assert _suggestion_row(review)[0] == "rejected"
    assert _suggestion_row(pub)[0] == "published"
