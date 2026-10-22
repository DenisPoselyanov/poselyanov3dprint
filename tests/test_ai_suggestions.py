"""Пропозиції ШІ: нормалізація відповіді, збереження, дублікати, pause_turn, статуси — без мережі."""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")

import services.ai_suggestions as ai  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "shop.db"))
    from services.db_utils import init_db

    init_db()
    ai._job.update(running=False, startedAt="", finishedAt="", added=0, error="")
    yield tmp_path


def _item(**over):
    base = {
        "name": "Гнучкий восьминіг",
        "cat": "toy",
        "emoji": "🐙",
        "mat": "PLA · різні кольори",
        "price": 180,
        "description": "Шарнірний восьминіг, друкується одразу рухомим.",
        "note": "",
        "filament_choice": True,
        "gift": False,
        "source_url": "https://makerworld.com/en/models/1",
        "source_title": "Flexi Octopus by X",
        "license": "Standard Digital File License — продаж друку не дозволено, треба комерційна ліцензія",
        "reason": "Популярна модель, схожа на найкращі Flexi-товари магазину",
        "image_urls": ["https://example.com/a.jpg", "javascript:alert(1)", "https://example.com/a.jpg"],
    }
    base.update(over)
    return base


def _resp(content, stop_reason="end_turn"):
    return SimpleNamespace(content=content, stop_reason=stop_reason)


def _submit(items):
    return SimpleNamespace(type="tool_use", name="submit_products", input={"items": items})


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_normalize_item_cleans_fields():
    out = ai.normalize_item(_item(cat="nope", price="99999", emoji=""), ["toy", "key", "custom"])
    assert out["product"]["cat"] == "toy"
    assert out["product"]["price"] == ai.PRICE_MAX
    assert out["product"]["emoji"] == "📦"
    assert out["images"] == ["https://example.com/a.jpg"]
    assert ai.normalize_item(_item(name=""), ["toy"]) is None
    assert ai.normalize_item(_item(price=0), ["toy"]) is None


def test_stl_link_falls_back_to_source_url():
    own = ai.normalize_item(_item(stl_link="https://makerworld.com/en/models/1#download"), ["toy"])
    assert own["product"]["stlLink"] == "https://makerworld.com/en/models/1#download"
    fallback = ai.normalize_item(_item(stl_link="javascript:x"), ["toy"])
    assert fallback["product"]["stlLink"] == "https://makerworld.com/en/models/1"
    assert "stlLink" not in ai.normalize_item(_item(source_url="", stl_link=""), ["toy"])["product"]


def test_generate_saves_and_skips_duplicates(env):
    client = FakeClient([_resp([_submit([
        _item(),
        _item(name="Fidget-спіраль"),  # уже є в каталозі
        _item(name="гнучкий восьминіг"),  # дубль у тій самій відповіді
    ])], "tool_use")])
    added = asyncio.run(ai.generate(3, "для дітей", client))
    assert added == 1
    items = ai.list_suggestions()
    assert [i["name"] for i in items] == ["Гнучкий восьминіг"]
    assert items[0]["status"] == "new"
    assert "комерційна ліцензія" in items[0]["license"]
    request = client.calls[0]
    assert "для дітей" in request["messages"][0]["content"]
    assert any(t.get("name") == "submit_products" for t in request["tools"])

    # Повторний пошук не додає вже запропоноване
    client2 = FakeClient([_resp([_submit([_item()])], "tool_use")])
    assert asyncio.run(ai.generate(1, "", client2)) == 0
    assert "Гнучкий восьминіг" in client2.calls[0]["messages"][0]["content"]


def test_pause_turn_resumes(env):
    paused = [SimpleNamespace(type="server_tool_use", name="web_search", input={})]
    client = FakeClient([_resp(paused, "pause_turn"), _resp([_submit([_item()])], "tool_use")])
    assert asyncio.run(ai.generate(1, "", client)) == 1
    second = client.calls[1]["messages"]
    assert second[1] == {"role": "assistant", "content": paused}


def test_errors_surface(env):
    client = FakeClient([_resp([SimpleNamespace(type="text", text="Ось товари")], "end_turn")])
    with pytest.raises(RuntimeError):
        asyncio.run(ai.generate(1, "", client))
    client = FakeClient([_resp([], "refusal")])
    with pytest.raises(RuntimeError):
        asyncio.run(ai.generate(1, "", client))


def test_job_records_error(env):
    async def run():
        client = FakeClient([_resp([], "refusal")])
        result = ai.start_generation({"count": 99}, client=client)
        assert result["ok"] and ai.job_state()["running"]
        assert ai.start_generation({}, client=client)["error"] == "Пошук уже йде"
        await asyncio.gather(*list(ai._tasks))

    asyncio.run(run())
    state = ai.job_state()
    assert not state["running"] and state["error"]


def test_start_requires_key(env, monkeypatch):
    import config

    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    assert not ai.start_generation({})["ok"]


def test_status_and_delete(env):
    ai.save_items([ai.normalize_item(_item(), ["toy"])])
    sid = ai.list_suggestions()[0]["id"]
    assert not ai.set_status(sid, "published")["ok"]  # без номера товару
    assert ai.set_status(sid, "published", 42)["ok"]
    item = ai.list_suggestions()[0]
    assert item["status"] == "published" and item["productId"] == 42
    assert ai.set_status(sid, "rejected")["ok"]
    assert not ai.set_status(sid, "weird")["ok"]
    assert ai.set_status(999, "new") == {"ok": False, "error": "Не знайдено"}
    assert ai.delete_suggestion(sid)
    assert ai.list_suggestions() == []
