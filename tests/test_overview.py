"""Огляд адмінки та події воронки."""

import importlib
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")


@pytest.fixture
def env(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")

    import config

    importlib.reload(config)
    from services.db_utils import init_db

    init_db()
    import services.events as events
    import services.orders as orders
    import services.overview as overview

    importlib.reload(orders)
    importlib.reload(events)
    importlib.reload(overview)
    yield orders, events, overview
    try:
        os.unlink(db_path)
    except OSError:
        pass


def _order(orders, uid, price, qty=1, status="confirmed", name="Брелок"):
    oid, _ = orders.save_order(uid, "@u", "Тест", [{"product_id": 1, "product_name": name, "price": price, "quantity": qty}], price * qty, "")
    orders.update_order_status(oid, status)
    return oid


def test_funnel_events_deduplicate_per_day(env):
    _, events, _ = env
    assert events.record_funnel_event(1, "catalog_open") is True
    assert events.record_funnel_event(1, "catalog_open") is False
    assert events.record_funnel_event(1, "product_view") is False  # потрібен product_id
    assert events.record_funnel_event(1, "product_view", 5) is True
    assert events.record_funnel_event(1, "product_view", 5) is False
    assert events.record_funnel_event(1, "product_view", 6) is True
    assert events.record_funnel_event(0, "catalog_open") is False
    assert events.record_funnel_event(1, "search") is False


def test_overview_kpi_funnel_and_top(env):
    orders, events, overview = env
    _order(orders, 1, 100, qty=2)
    _order(orders, 2, 50, name="Лампа")
    _order(orders, 3, 999, status="cancelled")
    _order(orders, 4, 70, status="new")
    for uid in (1, 2, 3):
        events.record_funnel_event(uid, "catalog_open")
    for uid in (1, 2):
        events.record_funnel_event(uid, "add_to_cart", 1)

    data = overview.get_overview(7)
    assert data["kpi"]["revenue"]["value"] == 250
    assert data["kpi"]["orders"]["value"] == 2
    assert data["kpi"]["avg_check"]["value"] == 125
    assert data["kpi"]["revenue"]["delta"] is None  # у попередньому періоді продажів не було
    funnel = {f["key"]: f["count"] for f in data["funnel"]}
    assert funnel["catalog"] == 3 and funnel["cart"] == 2 and funnel["ordered"] == 3
    assert data["kpi"]["conversion"]["value"] == 100.0
    assert [t["name"] for t in data["top_products"]] == ["Брелок", "Лампа"]
    assert data["top_products"][0]["qty"] == 2
    assert len(data["chart"]) == 7
    assert data["chart"][-1]["revenue"] == 250
    assert data["attention"]["new_orders"]["count"] == 1


def test_overview_previous_period_delta(env):
    orders, _, overview = env
    old = _order(orders, 1, 100)
    from db_core import db_connect

    long_ago = (datetime.now(timezone.utc) - timedelta(days=9)).strftime("%Y-%m-%d %H:%M:%S")
    conn = db_connect()
    conn.execute("UPDATE orders SET ordered_at = ? WHERE id = ?", (long_ago, old))
    conn.commit()
    conn.close()
    _order(orders, 2, 150)
    data = overview.get_overview(7)
    assert data["kpi"]["revenue"]["value"] == 150
    assert data["kpi"]["revenue"]["delta"] == 50.0


def test_overview_unknown_period_falls_back(env):
    _, _, overview = env
    assert overview.get_overview(99)["days"] == 7


def test_overview_all_time_covers_old_orders_without_comparison(env):
    orders, _, overview = env
    old = _order(orders, 1, 100)
    from db_core import db_connect

    long_ago = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
    conn = db_connect()
    conn.execute("UPDATE orders SET ordered_at = ? WHERE id = ?", (long_ago, old))
    conn.commit()
    conn.close()
    _order(orders, 2, 150)

    data = overview.get_overview(0)
    assert data["days"] == 0
    assert data["kpi"]["revenue"]["value"] == 250
    assert data["kpi"]["orders"]["value"] == 2
    assert data["kpi"]["revenue"]["delta"] is None
    assert data["chart_unit"] == "month"
    assert sum(c["revenue"] for c in data["chart"]) == 250
    assert data["chart"][0]["date"].endswith("-01")


def test_overview_all_time_short_history_stays_daily(env):
    orders, _, overview = env
    _order(orders, 1, 100)
    data = overview.get_overview(0)
    assert data["chart_unit"] == "day"
    assert len(data["chart"]) == 7
    assert data["chart"][-1]["revenue"] == 100


def test_overview_all_time_without_orders(env):
    _, _, overview = env
    data = overview.get_overview(0)
    assert data["kpi"]["orders"]["value"] == 0
    assert len(data["chart"]) == 7
