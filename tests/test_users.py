import importlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VALIDATE_INIT_DATA", "false")
os.environ.setdefault("LOCAL_DEV_MODE", "true")
os.environ.setdefault("DB_BACKEND", "sqlite")


@pytest.fixture
def users_db(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")

    import config

    importlib.reload(config)

    from services.db_utils import init_db

    init_db()

    import services.users as users

    importlib.reload(users)
    yield users, db_path

    try:
        os.unlink(db_path)
    except OSError:
        pass


def test_record_start_increments_counter(users_db):
    users, _ = users_db
    users.record_start(111, "Денис", "denys")
    users.record_start(111, "Денис", "denys")
    users.record_start(222, "Оля", "")

    result = users.list_users()
    by_id = {u["id"]: u for u in result["users"]}

    assert result["total"] == 2
    assert by_id[111]["start_count"] == 2
    assert by_id[111]["username"] == "@denys"
    assert by_id[111]["last_start_at"]
    assert by_id[222]["start_count"] == 1


def test_record_activity_counts_separately(users_db):
    users, _ = users_db
    users.record_start(333, "Іван", "ivan")
    users.record_activity(333)
    users.record_activity(333)

    row = users.list_users()["users"][0]
    assert row["start_count"] == 1
    assert row["activity_count"] == 2
    assert row["last_seen_at"]


def test_record_activity_ignores_unknown_and_invalid_ids(users_db):
    users, _ = users_db
    users.record_activity(0)
    users.record_activity(999)
    assert users.list_users()["total"] == 0


def test_list_users_sorting_and_pagination(users_db):
    users, _ = users_db
    for uid, starts in ((1, 1), (2, 5), (3, 3)):
        for _ in range(starts):
            users.record_start(uid, f"user{uid}", "")

    desc = users.list_users(sort="starts", order="desc")
    assert [u["id"] for u in desc["users"]] == [2, 3, 1]

    asc = users.list_users(sort="starts", order="asc")
    assert [u["id"] for u in asc["users"]] == [1, 3, 2]

    page = users.list_users(sort="starts", order="desc", limit=1, offset=1)
    assert page["total"] == 3
    assert [u["id"] for u in page["users"]] == [3]


def test_list_users_search_by_username_and_id(users_db):
    users, _ = users_db
    users.record_start(555, "Денис", "denys3d")
    users.record_start(666, "Оля", "olya")

    assert [u["id"] for u in users.list_users(search="denys")["users"]] == [555]
    assert [u["id"] for u in users.list_users(search="@olya")["users"]] == [666]
    assert [u["id"] for u in users.list_users(search="666")["users"]] == [666]
    assert users.list_users(search="нікого")["total"] == 0


def test_list_users_blocked_filter(users_db):
    users, _ = users_db
    users.record_start(777, "A", "")
    users.record_start(888, "B", "")
    users.set_blocked(888, True)

    assert [u["id"] for u in users.list_users(blocked="active")["users"]] == [777]
    only = users.list_users(blocked="only")["users"]
    assert [u["id"] for u in only] == [888]
    assert only[0]["blocked"] is True


def test_list_users_includes_confirmed_order_totals(users_db):
    users, db_path = users_db
    users.record_start(999, "Клієнт", "")

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO orders (user_id, total_price, status) VALUES (?, ?, ?)", (999, 500, "confirmed")
    )
    conn.execute(
        "INSERT INTO orders (user_id, total_price, status) VALUES (?, ?, ?)", (999, 300, "confirmed")
    )
    conn.execute(
        "INSERT INTO orders (user_id, total_price, status) VALUES (?, ?, ?)", (999, 900, "cancelled")
    )
    conn.commit()
    conn.close()

    row = users.list_users()["users"][0]
    assert row["orders_count"] == 3
    assert row["orders_total"] == 800


def test_migration_backfills_existing_users_with_one_start(monkeypatch):
    """Стара база без лічильника: наявним користувачам ставимо 1 запуск."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE users (
            id        INTEGER PRIMARY KEY,
            name      TEXT,
            username  TEXT,
            blocked   INTEGER DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("INSERT INTO users (id, name, username) VALUES (42, 'Старий', '@old')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")

    import config

    importlib.reload(config)

    from services.db_utils import init_db

    init_db()

    import services.users as users

    importlib.reload(users)

    row = users.list_users()["users"][0]
    assert row["id"] == 42
    assert row["start_count"] == 1
    assert row["last_start_at"]

    try:
        os.unlink(db_path)
    except OSError:
        pass
