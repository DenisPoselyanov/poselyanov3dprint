"""Етап 5, слайс 6: «Розсилки» — створення, відкладення, відправка через підміну, без дубля, блокування."""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")

import services.broadcasts as bc  # noqa: E402
import services.orders as orders  # noqa: E402
import services.users as users  # noqa: E402
from db_core import db_connect  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "shop.db"))
    from services.db_utils import init_db

    init_db()
    for uid in (1, 2, 3):
        users.record_start(uid, f"U{uid}", f"u{uid}")
    yield tmp_path


class FakeSender:
    def __init__(self, fail=None):
        self.sent = []
        self.fail = fail or {}

    async def __call__(self, user_id, b):
        if user_id in self.fail:
            raise Exception(self.fail[user_id])
        self.sent.append(user_id)


async def _nosleep(_):
    return None


def _run(coro):
    return asyncio.run(coro)


def _send(bid, sender):
    return _run(bc.send_broadcast(bid, sender, delay=0, sleep=_nosleep))


def test_table_migration_is_idempotent(env):
    from services.db_utils import init_db

    init_db()
    init_db()
    conn = db_connect()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(broadcasts)").fetchall()]
    conn.close()
    for c in ("segment", "text", "photo", "button_label", "button_target", "scheduled_at", "status",
              "total", "delivered", "failed", "clicks", "created_at", "sent_at"):
        assert c in cols


def test_validation(env):
    bad = [
        {"text": ""},
        {"text": "x", "segment": "nope"},
        {"text": "x", "photo": "http://a/b.jpg"},
        {"text": "x" * 1025, "photo": "https://a.b/c.jpg"},
        {"text": "x", "button_label": "Go", "button_target": "product:abc"},
        {"text": "x", "button_target": "product:1"},
        {"text": "x", "button_label": "L" * 40, "button_target": "shop"},
        {"text": "x", "scheduled_at": "не дата"},
    ]
    for data in bad:
        assert not bc.create_broadcast(data)["ok"], data
    assert bc.create_broadcast({"text": "ok", "button_label": "Відкрити", "button_target": "collection:new"})["ok"]


def test_preview_and_send_now_marks_sent(env):
    assert bc.preview_recipients("all")["count"] == 3
    res = bc.create_broadcast({"text": "Привіт", "segment": "all"})
    assert res["ok"] and res["send_now"] and res["total"] == 3
    sender = FakeSender()
    out = _send(res["id"], sender)
    assert out["ok"] and sorted(sender.sent) == [1, 2, 3]
    b = bc.get_broadcast(res["id"])
    assert b["status"] == "sent" and b["delivered"] == 3 and b["failed"] == 0 and b["sent_at"]


def test_no_double_send(env):
    res = bc.create_broadcast({"text": "Раз"})
    sender = FakeSender()

    async def both():
        return await asyncio.gather(
            bc.send_broadcast(res["id"], sender, delay=0, sleep=_nosleep),
            bc.send_broadcast(res["id"], sender, delay=0, sleep=_nosleep),
        )

    r1, r2 = _run(both())
    assert sorted([bool(r1.get("skipped")), bool(r2.get("skipped"))]) == [False, True]
    assert len(sender.sent) == 3
    assert _send(res["id"], sender).get("skipped")  # уже sent
    assert len(sender.sent) == 3


def test_blocked_users_are_marked_and_skipped_next_time(env):
    res = bc.create_broadcast({"text": "Hi"})
    sender = FakeSender(fail={2: "Forbidden: bot was blocked by the user", 3: "network glitch"})
    _send(res["id"], sender)
    b = bc.get_broadcast(res["id"])
    assert b["delivered"] == 1 and b["failed"] == 2 and b["status"] == "sent"
    assert users.is_user_blocked(2) and not users.is_user_blocked(3)  # лише справжнє блокування
    res2 = bc.create_broadcast({"text": "Again"})
    assert res2["total"] == 2
    s2 = FakeSender()
    _send(res2["id"], s2)
    assert sorted(s2.sent) == [1, 3]


def test_all_failed_status(env):
    res = bc.create_broadcast({"text": "Hi"})
    _send(res["id"], FakeSender(fail={1: "x", 2: "x", 3: "x"}))
    assert bc.get_broadcast(res["id"])["status"] == "failed"


def test_segment_targeting(env):
    oid, _ = orders.save_order(1, "@u", "U1", [{"product_id": 1, "product_name": "X", "price": 100, "quantity": 1}], 100, "")
    orders.update_order_status(oid, "confirmed")
    res = bc.create_broadcast({"text": "Для покупців", "segment": "buyers"})
    sender = FakeSender()
    _send(res["id"], sender)
    assert sender.sent == [1]


def test_schedule_due_only_after_time_and_once(env):
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    res = bc.create_broadcast({"text": "Потім", "scheduled_at": future})
    assert res["status"] == "scheduled" and not res["send_now"]
    assert bc.due_broadcast_ids() == []
    conn = db_connect()  # "час настав" (напр. після рестарту)
    conn.execute("UPDATE broadcasts SET scheduled_at = datetime('now','-1 minutes') WHERE id = ?", (res["id"],))
    conn.commit()
    conn.close()
    assert bc.due_broadcast_ids() == [res["id"]]
    sender = FakeSender()
    assert _run(bc.run_due_broadcasts(sender, delay=0, sleep=_nosleep)) == 1
    assert _run(bc.run_due_broadcasts(sender, delay=0, sleep=_nosleep)) == 0
    assert len(sender.sent) == 3 and bc.get_broadcast(res["id"])["status"] == "sent"


def test_delete_only_scheduled_or_draft(env):
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    a = bc.create_broadcast({"text": "a", "scheduled_at": future})["id"]
    d = bc.create_broadcast({"text": "d", "draft": True})["id"]
    s = bc.create_broadcast({"text": "s"})["id"]
    _send(s, FakeSender())
    assert bc.delete_broadcast(a) == "deleted" and bc.delete_broadcast(d) == "deleted"
    assert bc.delete_broadcast(s) == "locked" and bc.delete_broadcast(9999) == "not_found"
    assert bc.due_broadcast_ids() == []  # чернетка не відправляється


def test_recover_interrupted_never_resends(env):
    res = bc.create_broadcast({"text": "x"})
    assert bc.claim_broadcast(res["id"])
    assert bc.recover_interrupted() == 1
    assert bc.get_broadcast(res["id"])["status"] == "failed"
    assert bc.due_broadcast_ids() == []


def test_button_url_and_clicks_attribution(env):
    res = bc.create_broadcast({"text": "x", "button_label": "Глянути", "button_target": "product:12"})
    bid = res["id"]
    assert bc.button_url(bid, "product:12").endswith(f"startapp=bc_{bid}_product_12")
    assert bc.button_url(bid, "shop").endswith(f"startapp=bc_{bid}")
    assert bc.button_url(bid, "url:https://example.com/a") == "https://example.com/a"
    assert not bc.record_click(1, bid)  # ще не відправлена
    _send(bid, FakeSender())
    assert bc.record_click(1, bid) and not bc.record_click(1, bid)  # один раз на клієнта
    assert bc.record_click(2, bid)
    oid, _ = orders.save_order(1, "@u", "U1", [{"product_id": 1, "product_name": "X", "price": 250, "quantity": 1}], 250, "")
    orders.update_order_status(oid, "confirmed")
    item = bc.list_broadcasts()[0]
    assert item["clicks"] == 2 and item["delivered"] == 3 and item["clicks_pct"] == 66.7
    assert item["orders"] == 1 and item["revenue"] == 250
    assert item["link"].endswith(f"bc_{bid}_product_12")


def _http(monkeypatch, steps):
    import auth
    import bot
    import config
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from routes.setup import register_http_routes

    monkeypatch.setattr(config, "VALIDATE_INIT_DATA", True)
    monkeypatch.setattr(config, "ALLOW_LOCAL_NETWORK", False)
    monkeypatch.setattr(config, "ADMIN_BYPASS_TOKEN", "tok")
    monkeypatch.setattr(auth, "is_local_dev_origin", lambda request: False)

    async def go():
        app = web.Application()
        register_http_routes(app, bot)
        async with TestClient(TestServer(app)) as client:
            return await steps(client, {"X-Admin-Bypass-Token": "tok"})

    return asyncio.run(go())


def test_http_admin_only_create_send_with_mock_sender_and_delete(env, monkeypatch):
    import bot

    sender = FakeSender()
    monkeypatch.setattr(bot, "_make_broadcast_sender", lambda: sender)

    async def steps(client, adm):
        for method, url in (("get", "/api/admin/broadcasts"), ("post", "/api/admin/broadcasts"),
                            ("post", "/api/admin/broadcasts/preview"), ("delete", "/api/admin/broadcasts/1")):
            assert (await getattr(client, method)(url)).status == 403
        p = await (await client.post("/api/admin/broadcasts/preview", json={"segment": "all"}, headers=adm)).json()
        assert p["count"] == 3
        r = await client.post("/api/admin/broadcasts", json={"text": ""}, headers=adm)
        assert r.status == 400
        r = await client.post("/api/admin/broadcasts", json={"text": "Привіт", "segment": "all"}, headers=adm)
        data = await r.json()
        assert r.status == 200 and data["ok"]
        for _ in range(50):
            await asyncio.sleep(0.05)
            lst = await (await client.get("/api/admin/broadcasts", headers=adm)).json()
            if lst["broadcasts"][0]["status"] == "sent":
                break
        assert lst["broadcasts"][0]["delivered"] == 3 and sorted(sender.sent) == [1, 2, 3]
        assert (await client.delete(f"/api/admin/broadcasts/{data['id']}", headers=adm)).status == 409
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        r2 = await (await client.post("/api/admin/broadcasts", json={"text": "Потім", "scheduled_at": future}, headers=adm)).json()
        assert (await client.delete(f"/api/admin/broadcasts/{r2['id']}", headers=adm)).status == 200
        assert (await client.delete("/api/admin/broadcasts/9999", headers=adm)).status == 404
        return True

    assert _http(monkeypatch, steps)


def test_http_broadcast_open_event_counts_click(env, monkeypatch):
    res = bc.create_broadcast({"text": "x"})
    _send(res["id"], FakeSender())

    async def steps(client, adm):
        r = await client.post("/api/events", json={"type": "broadcast_open", "broadcast_id": res["id"]},
                              headers={**adm, "X-Telegram-User-Id": "1"})
        assert r.status == 200
        return await r.json()

    out = _http(monkeypatch, steps)
    assert out["ok"]
