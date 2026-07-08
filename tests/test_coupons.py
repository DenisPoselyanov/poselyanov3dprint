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
def coupon_db(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    monkeypatch.setenv("DB_FILE", db_path)
    monkeypatch.setenv("DB_BACKEND", "sqlite")

    import config

    importlib.reload(config)

    from services.db_utils import init_db

    init_db()

    import services.coupons as coupons

    importlib.reload(coupons)

    yield coupons, db_path

    try:
        os.unlink(db_path)
    except OSError:
        pass


def _insert_coupon(conn, **kwargs):
    defaults = {
        "code": "TEST10",
        "type": "percent",
        "value": 10,
        "min_order": 0,
        "uses_max": 0,
        "uses_count": 0,
        "one_per_user": 0,
        "active": 1,
        "expires_at": None,
        "personal_user_id": None,
    }
    defaults.update(kwargs)
    conn.execute(
        """
        INSERT INTO coupons
        (code, type, value, min_order, uses_max, uses_count, one_per_user, active, expires_at, personal_user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            defaults["code"],
            defaults["type"],
            defaults["value"],
            defaults["min_order"],
            defaults["uses_max"],
            defaults["uses_count"],
            defaults["one_per_user"],
            defaults["active"],
            defaults["expires_at"],
            defaults["personal_user_id"],
        ),
    )
    conn.commit()


def test_check_coupon_not_found(coupon_db):
    coupons, _ = coupon_db
    result = coupons.check_coupon("MISSING", 1, 500)
    assert result["valid"] is False
    assert "не знайдено" in result["message"]


def test_check_coupon_percent_discount(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="SAVE10", type="percent", value=10)
    conn.close()

    result = coupons.check_coupon("SAVE10", 1, 200)
    assert result["valid"] is True
    assert result["discount"] == 20


def test_check_coupon_percent_discount_rounds_final_price_up(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="SAVE10", type="percent", value=10)
    conn.close()

    result = coupons.check_coupon("SAVE10", 1, 170)
    assert result["valid"] is True
    assert result["discount"] == 10


def test_check_coupon_fixed_discount(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="FIX50", type="fixed", value=50)
    conn.close()

    result = coupons.check_coupon("FIX50", 1, 200)
    assert result["valid"] is True
    assert result["discount"] == 50


def test_check_coupon_fixed_discount_rounds_final_price_up(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="FIX50", type="fixed", value=50)
    conn.close()

    result = coupons.check_coupon("FIX50", 1, 153)
    assert result["valid"] is True
    assert result["discount"] == 43


def test_check_coupon_min_order_not_met(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="MIN300", min_order=300)
    conn.close()

    result = coupons.check_coupon("MIN300", 1, 100)
    assert result["valid"] is False
    assert "Мінімальна сума" in result["message"]


def test_check_coupon_expired(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = db_connect()
    _insert_coupon(conn, code="OLD", expires_at=expired)
    conn.close()

    result = coupons.check_coupon("OLD", 1, 500)
    assert result["valid"] is False
    assert "закінчився" in result["message"]


def test_check_coupon_personal_user_mismatch(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="PERSONAL", personal_user_id=42)
    conn.close()

    result = coupons.check_coupon("PERSONAL", 99, 500)
    assert result["valid"] is False
    assert "іншому користувачу" in result["message"]


def _insert_order(conn, order_id=1, user_id=1, status="new", coupon_code=None):
    conn.execute(
        """
        INSERT INTO orders (id, user_id, username, first_name, total_price, status, coupon_code)
        VALUES (?, ?, 'user', 'Test', 100, ?, ?)
        """,
        (order_id, user_id, status, coupon_code),
    )
    conn.commit()


def test_check_coupon_one_per_user_already_used(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="ONCE", one_per_user=1)
    conn.execute(
        "INSERT INTO coupon_uses (code, user_id, order_id, status) VALUES (?, ?, ?, ?)",
        ("ONCE", 7, 1, "confirmed"),
    )
    conn.commit()
    conn.close()

    result = coupons.check_coupon("ONCE", 7, 500)
    assert result["valid"] is False
    assert "вже використовував" in result["message"]


def test_reserve_coupon_does_not_increment_uses_count(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="USEME", uses_max=5, uses_count=0)
    _insert_order(conn, order_id=10)
    coupons.reserve_coupon(conn, "USEME", 1, 10)
    row = conn.execute("SELECT uses_count FROM coupons WHERE code = ?", ("USEME",)).fetchone()
    pending = conn.execute(
        "SELECT status FROM coupon_uses WHERE order_id = ?", (10,)
    ).fetchone()
    conn.close()

    assert row[0] == 0
    assert pending[0] == "pending"


def test_confirm_coupon_for_order_increments_uses_count(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="USEME", uses_max=5, uses_count=0)
    _insert_order(conn, order_id=10)
    coupons.reserve_coupon(conn, "USEME", 1, 10)
    conn.commit()

    coupons.confirm_coupon_for_order(conn, 10)
    conn.commit()

    row = conn.execute("SELECT uses_count FROM coupons WHERE code = ?", ("USEME",)).fetchone()
    status = conn.execute(
        "SELECT status FROM coupon_uses WHERE order_id = ?", (10,)
    ).fetchone()
    conn.close()

    assert row[0] == 1
    assert status[0] == "confirmed"


def test_release_coupon_for_order_removes_pending(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="USEME", uses_max=5, uses_count=0)
    _insert_order(conn, order_id=10)
    coupons.reserve_coupon(conn, "USEME", 1, 10)
    conn.commit()

    coupons.release_coupon_for_order(conn, 10)
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM coupon_uses WHERE order_id = ?", (10,)).fetchone()
    uses_count = conn.execute("SELECT uses_count FROM coupons WHERE code = ?", ("USEME",)).fetchone()
    conn.close()

    assert count[0] == 0
    assert uses_count[0] == 0


def test_reserve_coupon_raises_when_exhausted(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="GONE", uses_max=1, uses_count=0)
    _insert_order(conn, order_id=10)
    coupons.reserve_coupon(conn, "GONE", 1, 10)
    conn.commit()
    conn.close()

    conn = db_connect()
    _insert_order(conn, order_id=11, user_id=2)
    with pytest.raises(coupons.CouponConsumptionError, match="вичерпано"):
        coupons.reserve_coupon(conn, "GONE", 2, 11)
    conn.close()


def test_check_coupon_blocks_pending_hold_for_one_per_user(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="ONCE", one_per_user=1)
    _insert_order(conn, order_id=10, user_id=7)
    coupons.reserve_coupon(conn, "ONCE", 7, 10)
    conn.commit()
    conn.close()

    result = coupons.check_coupon("ONCE", 7, 500)
    assert result["valid"] is False
    assert "вже використовував" in result["message"]


def test_get_my_coupons_pending_not_marked_used(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="PENDING", one_per_user=1)
    _insert_order(conn, order_id=10, user_id=42)
    coupons.reserve_coupon(conn, "PENDING", 42, 10)
    conn.commit()
    conn.close()

    rows = coupons.get_my_coupons(42)
    row = next(r for r in rows if (r[0] if not isinstance(r, dict) else r["code"]) == "PENDING")
    used = row[-1] if not isinstance(row, dict) else row["used_by_user"]
    assert not used


def test_reserve_then_release_allows_reuse(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="REUSE", one_per_user=1)
    _insert_order(conn, order_id=10, user_id=7)
    coupons.reserve_coupon(conn, "REUSE", 7, 10)
    conn.commit()
    coupons.release_coupon_for_order(conn, 10)
    conn.commit()
    conn.close()

    result = coupons.check_coupon("REUSE", 7, 500)
    assert result["valid"] is True


def test_confirm_coupon_is_idempotent(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="IDEM", uses_max=5)
    _insert_order(conn, order_id=10)
    coupons.reserve_coupon(conn, "IDEM", 1, 10)
    conn.commit()
    coupons.confirm_coupon_for_order(conn, 10)
    coupons.confirm_coupon_for_order(conn, 10)
    conn.commit()

    row = conn.execute("SELECT uses_count FROM coupons WHERE code = ?", ("IDEM",)).fetchone()
    conn.close()
    assert row[0] == 1


def test_check_promotion_disabled(monkeypatch, coupon_db):
    coupons, _ = coupon_db
    import config

    monkeypatch.setattr(config, "PROMOTION_ENABLED", False)
    assert coupons.check_promotion(1000) == 0


def test_check_promotion_at_threshold(coupon_db):
    coupons, _ = coupon_db
    assert coupons.check_promotion(500) == 50


def test_check_promotion_rounds_final_price_up(coupon_db):
    coupons, _ = coupon_db
    assert coupons.check_promotion(510) == 50


def _insert_allowed_users(conn, code, user_ids):
    for uid in user_ids:
        conn.execute(
            "INSERT OR IGNORE INTO coupon_allowed_users (code, user_id) VALUES (?, ?)",
            (code, uid),
        )
    conn.commit()


def test_check_coupon_whitelist_allowed(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="STUDENTS")
    _insert_allowed_users(conn, "STUDENTS", [42, 43])
    conn.close()

    result = coupons.check_coupon("STUDENTS", 42, 500)
    assert result["valid"] is True


def test_check_coupon_whitelist_denied(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="STUDENTS")
    _insert_allowed_users(conn, "STUDENTS", [42, 43])
    conn.close()

    result = coupons.check_coupon("STUDENTS", 99, 500)
    assert result["valid"] is False
    assert "іншому користувачу" in result["message"]


def test_create_coupon_with_allowed_user_ids(coupon_db):
    coupons, _ = coupon_db

    result = coupons.create_coupon(
        {
            "code": "GROUP",
            "type": "percent",
            "value": 15,
            "allowed_user_ids": [111, 222],
        }
    )
    assert result["ok"] is True
    coupon = result["coupon"]
    assert sorted(coupon["allowed_user_ids"]) == [111, 222]
    assert coupon["personal_user_id"] is None


def test_add_coupon_users_incremental(coupon_db):
    coupons, _ = coupon_db

    coupons.create_coupon({"code": "CLASS", "type": "fixed", "value": 50, "allowed_user_ids": [1]})
    result = coupons.add_coupon_users("CLASS", [2, 3])
    assert result["ok"] is True
    assert result["added_user_ids"] == [2, 3]
    assert sorted(result["coupon"]["allowed_user_ids"]) == [1, 2, 3]

    again = coupons.add_coupon_users("CLASS", [2])
    assert again["ok"] is True
    assert again["added_user_ids"] == []


def test_get_my_coupons_whitelist_visibility(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="PUBLIC")
    _insert_coupon(conn, code="PRIVATE")
    _insert_allowed_users(conn, "PRIVATE", [42])
    conn.close()

    rows = coupons.get_my_coupons(42)
    codes = {row[0] if not isinstance(row, dict) else row["code"] for row in rows}
    assert "PUBLIC" in codes
    assert "PRIVATE" in codes

    rows_other = coupons.get_my_coupons(99)
    codes_other = {row[0] if not isinstance(row, dict) else row["code"] for row in rows_other}
    assert "PUBLIC" in codes_other
    assert "PRIVATE" not in codes_other


def test_delete_coupon_removes_whitelist(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    coupons.create_coupon(
        {"code": "TEMP", "type": "percent", "value": 10, "allowed_user_ids": [5, 6]}
    )
    assert coupons.delete_coupon("TEMP")["ok"] is True

    conn = db_connect()
    count = conn.execute(
        "SELECT COUNT(*) FROM coupon_allowed_users WHERE code = ?", ("TEMP",)
    ).fetchone()[0]
    conn.close()
    assert count == 0


def test_parse_allowed_user_ids_from_string(coupon_db):
    coupons, _ = coupon_db
    assert coupons._parse_allowed_user_ids("111, 222\n333") == [111, 222, 333]


def test_check_promotion_below_threshold(coupon_db):
    coupons, _ = coupon_db
    assert coupons.check_promotion(499) == 0
