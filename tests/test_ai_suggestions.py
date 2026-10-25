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
    ai._job.update(running=False, startedAt="", finishedAt="", added=0, error="", kind="")

    async def keep_all(urls):  # без мережі: посилання на фото не перевіряємо
        return list(urls)

    monkeypatch.setattr(ai, "verify_images", keep_all)
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


def test_blocked_themes_are_dropped():
    cats = ["toy"]
    for bad in (
        {"name": "Демонічний череп"},
        {"name": "Брелок Halloween"},
        {"description": "Чаклунський посох для ритуалів"},
        {"source_title": "Evil Wizard Staff by X"},
        {"note": "NSFW варіант"},
    ):
        assert ai.normalize_item(_item(**bad), cats) is None, bad
    for good in ("Магнітний тримач", "Дракон Flexi", "Кумедний кролик", "Hello Kitty", "Spelling board"):
        assert ai.normalize_item(_item(name=good), cats), good


def test_variants_are_normalized_and_set_min_price():
    cats = ["toy"]
    out = ai.normalize_item(_item(variants=[
        {"name": "15 см", "price": 250},
        {"name": "10 см", "price": "180"},
        {"name": "10 СМ", "price": 190},  # дубль назви
        {"name": "", "price": 100},
        {"name": "20 см", "price": 0},
    ]), cats)
    # лишилося дві придатні позиції, ціна товару = найнижча
    assert [v["name"] for v in out["product"]["variants"]] == ["15 см", "10 см"]
    assert out["product"]["price"] == 180 and out["product"]["gift"] is False
    # менше двох позицій — товар без варіантів і зі своєю ціною
    one = ai.normalize_item(_item(price=200, variants=[{"name": "10 см", "price": 150}]), cats)
    assert "variants" not in one["product"] and one["product"]["price"] == 200


def test_reject_blocked_moves_old_suggestions(env):
    ok = ai.normalize_item(_item(name="Милий кролик"), ["toy"])
    old_bad = ai.normalize_item(_item(name="Милий кролик 2"), ["toy"])
    old_bad["product"]["description"] = "Ритуальний свічник"  # потрапив у базу до появи чорного списку
    ai.save_items([ok, old_bad])
    assert ai.reject_blocked() == 1
    status = {i["name"]: i["status"] for i in ai.list_suggestions()}
    assert status == {"Милий кролик": "new", "Милий кролик 2": "rejected"}
    assert ai.reject_blocked() == 0


def test_images_capped_at_fifteen():
    urls = [f"https://example.com/{i}.jpg" for i in range(20)]
    assert len(ai.normalize_item(_item(image_urls=urls), ["toy"])["images"]) == ai.IMAGES_MAX == 15


def test_enrich_photos_merges_and_caps(env):
    ai.save_items([ai.normalize_item(_item(image_urls=["https://example.com/a.jpg"]), ["toy"])])
    sid = ai.list_suggestions()[0]["id"]
    more = [f"https://example.com/{i}.jpg" for i in range(19)] + ["javascript:x"]
    photos = SimpleNamespace(type="tool_use", name="submit_photos", input={"image_urls": more})
    client = FakeClient([_resp([photos], "tool_use")])
    added = asyncio.run(ai.enrich_photos(sid, client))
    images = ai.get_suggestion(sid)["images"]
    assert added == 14 and len(images) == 15
    assert images[0] == "https://example.com/a.jpg" and len(set(images)) == 15
    assert "https://makerworld.com/en/models/1" in client.calls[0]["messages"][0]["content"]
    assert ai.photo_targets() == []  # галерея повна


def test_photo_search_job(env):
    async def run():
        ai.save_items([ai.normalize_item(_item(), ["toy"])])
        photos = SimpleNamespace(type="tool_use", name="submit_photos", input={"image_urls": ["https://example.com/b.jpg"]})
        client = FakeClient([_resp([photos], "tool_use")])
        assert ai.start_photo_search({}, client=client)["count"] == 1
        await asyncio.gather(*list(ai._tasks))

    asyncio.run(run())
    state = ai.job_state()
    assert state["kind"] == "photos" and state["added"] == 1 and not state["error"]
    assert not ai.start_photo_search({"ids": [999]}, client=object())["ok"]  # такої пропозиції немає


def test_url_check_rejects_non_public_hosts():
    async def run():
        for url in ("https://127.0.0.1/a.jpg", "https://169.254.169.254/latest", "https://10.0.0.5/x.png", "https://localhost/x.png"):
            assert not await ai._is_public_host(url), url
        assert not await ai._url_ok(object(), "https://192.168.1.1/a.jpg")  # до мережі навіть не доходить
        assert await ai._is_public_host("https://8.8.8.8/a.jpg")

    asyncio.run(run())


def test_resolver_drops_private_addresses():
    async def run():
        resolver = ai._PublicOnlyResolver()

        async def fake(host, port=0, family=0):
            return [{"host": "10.0.0.1", "port": port}] if host == "evil.test" else [
                {"host": "10.0.0.1", "port": port}, {"host": "8.8.8.8", "port": port}]

        resolver._inner.resolve = fake
        with pytest.raises(OSError):
            await resolver.resolve("evil.test", 443)
        assert [a["host"] for a in await resolver.resolve("mixed.test", 443)] == ["8.8.8.8"]
        await resolver.close()

    asyncio.run(run())


def test_add_manual_follows_same_rules(env):
    async def run():
        ok = await ai.add_manual(_item(name="Ручний кролик"))
        assert ok["ok"] and ok["name"] == "Ручний кролик"
        assert not (await ai.add_manual(_item(name="ручний кролик")))["ok"]  # дубль
        assert not (await ai.add_manual(_item(name="Демонічний кролик")))["ok"]  # чорний список
        assert not (await ai.add_manual(_item(name="Fidget-спіраль")))["ok"]  # уже є в каталозі
        assert not (await ai.add_manual({"name": "Без ціни"}))["ok"]

    asyncio.run(run())
    names = [i["name"] for i in ai.list_suggestions()]
    assert names == ["Ручний кролик"] and ai.list_suggestions()[0]["status"] == "new"


def test_update_images_replaces_gallery(env):
    async def run():
        ai.save_items([ai.normalize_item(_item(image_urls=["https://example.com/old.jpg"]), ["toy"])])
        sid = ai.list_suggestions()[0]["id"]
        assert not (await ai.update_images(999, ["https://example.com/x.jpg"]))["ok"]
        result = await ai.update_images(sid, ["https://example.com/new1.jpg", "https://example.com/new2.jpg"])
        assert result == {"ok": True, "images": 2}
        assert ai.get_suggestion(sid)["images"] == ["https://example.com/new1.jpg", "https://example.com/new2.jpg"]

    asyncio.run(run())


def test_source_stats_are_clean(env):
    out = ai.normalize_item(_item(source_stats={"downloads": "73092", "likes": -1, "prints": None, "rating": 4.97}), ["toy"])
    assert out["product"]["sourceStats"] == {"downloads": 73092, "rating": 4.97}
    assert "sourceStats" not in ai.normalize_item(_item(source_stats={"rating": 9}), ["toy"])["product"]


def test_update_suggestion_sets_license_and_stats(env):
    async def run():
        ai.save_items([ai.normalize_item(_item(), ["toy"])])
        sid = ai.list_suggestions()[0]["id"]
        assert await ai.update_suggestion(sid, {"license": "Standard Digital File License", "sourceStats": {"downloads": 362, "likes": 269}}) == {"ok": True}
        item = ai.get_suggestion(sid)
        assert item["license"] == "Standard Digital File License"
        assert item["product"]["sourceStats"] == {"downloads": 362, "likes": 269}
        assert not (await ai.update_suggestion(999, {"license": "x"}))["ok"]

    asyncio.run(run())
