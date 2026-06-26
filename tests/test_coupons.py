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


def test_check_coupon_fixed_discount(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="FIX50", type="fixed", value=50)
    conn.close()

    result = coupons.check_coupon("FIX50", 1, 200)
    assert result["valid"] is True
    assert result["discount"] == 50


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


def test_check_coupon_one_per_user_already_used(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="ONCE", one_per_user=1)
    conn.execute(
        "INSERT INTO coupon_uses (code, user_id, order_id) VALUES (?, ?, ?)",
        ("ONCE", 7, 1),
    )
    conn.commit()
    conn.close()

    result = coupons.check_coupon("ONCE", 7, 500)
    assert result["valid"] is False
    assert "вже використовував" in result["message"]


def test_consume_coupon_increments_uses_count(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="USEME", uses_max=5, uses_count=0)
    coupons.consume_coupon(conn, "USEME", 1, 10)
    row = conn.execute("SELECT uses_count FROM coupons WHERE code = ?", ("USEME",)).fetchone()
    conn.close()

    assert row[0] == 1


def test_consume_coupon_raises_when_exhausted(coupon_db):
    coupons, _ = coupon_db
    from db_core import db_connect

    conn = db_connect()
    _insert_coupon(conn, code="GONE", uses_max=1, uses_count=1)

    with pytest.raises(coupons.CouponConsumptionError, match="вичерпано"):
        coupons.consume_coupon(conn, "GONE", 1, 10)
    conn.close()


def test_check_promotion_disabled(monkeypatch, coupon_db):
    coupons, _ = coupon_db
    import config

    monkeypatch.setattr(config, "PROMOTION_ENABLED", False)
    assert coupons.check_promotion(1000) == 0


def test_check_promotion_at_threshold(coupon_db):
    coupons, _ = coupon_db
    assert coupons.check_promotion(500) == 50


def test_check_promotion_below_threshold(coupon_db):
    coupons, _ = coupon_db
    assert coupons.check_promotion(499) == 0
