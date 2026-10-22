"""Відгуки покупців, рейтинг і «N продано» в картці товару (REDESIGN.md, розд. 3.4, 5.3).

Етап 2: таблиця, публічний список схвалених відгуків і агрегати для /api/products.
Залишити відгук (POST /reviews, лише покупець доставленого замовлення) і модерація
кнопками в чаті замовлень — етап 3; сторінка «Відгуки» в адмінці — етап 5.

Поки немає жодного схваленого відгуку, рейтинг товару не віддається — вітрина
не показує ні зірок, ні «0 відгуків».
"""

from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlparse

from db_core import db_connect, is_postgres, sql as _sql
from services.hits import SOLD_STATUSES
from services.settings import sold_count_min

logger = logging.getLogger(__name__)

STATUSES = ("pending", "approved", "hidden")
MAX_PAGE = 50

_STATS_TTL_SEC = 600.0
_stats_cache: dict = {"ts": 0.0, "data": None}


def init_reviews_table(conn) -> None:
    if is_postgres():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id          BIGSERIAL PRIMARY KEY,
                product_id  BIGINT NOT NULL,
                order_id    BIGINT,
                user_id     BIGINT,
                author_name TEXT NOT NULL DEFAULT '',
                rating      INTEGER NOT NULL,
                text        TEXT NOT NULL DEFAULT '',
                photos      TEXT NOT NULL DEFAULT '[]',
                status      TEXT NOT NULL DEFAULT 'pending',
                admin_reply TEXT NOT NULL DEFAULT '',
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id  INTEGER NOT NULL,
                order_id    INTEGER,
                user_id     INTEGER,
                author_name TEXT NOT NULL DEFAULT '',
                rating      INTEGER NOT NULL,
                text        TEXT NOT NULL DEFAULT '',
                photos      TEXT NOT NULL DEFAULT '[]',
                status      TEXT NOT NULL DEFAULT 'pending',
                admin_reply TEXT NOT NULL DEFAULT '',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    # Один відгук на пару (замовлення, товар)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_order_product ON reviews(order_id, product_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_product_status ON reviews(product_id, status)")


# ─── АГРЕГАТИ ДЛЯ КАРТКИ ТОВАРУ ─────────────────────────────

def _fetch_stats() -> dict[int, dict]:
    placeholders = ", ".join("?" for _ in SOLD_STATUSES)
    conn = db_connect()
    try:
        ratings = conn.execute(
            _sql("""
                SELECT product_id, AVG(rating), COUNT(*)
                FROM reviews
                WHERE status = 'approved'
                GROUP BY product_id
            """)
        ).fetchall()
        sold = conn.execute(
            _sql(f"""
                SELECT oi.product_id, SUM(oi.quantity)
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                WHERE o.status IN ({placeholders}) AND oi.product_id IS NOT NULL AND oi.product_id > 0
                GROUP BY oi.product_id
            """),
            SOLD_STATUSES,
        ).fetchall()
    finally:
        conn.close()
    stats: dict[int, dict] = {}
    for pid, avg, count in ratings:
        if pid is not None and count:
            stats.setdefault(int(pid), {}).update(rating_avg=round(float(avg), 1), rating_count=int(count))
    for pid, qty in sold:
        if pid is not None and qty:
            stats.setdefault(int(pid), {})["sold"] = int(qty)
    return stats


def product_stats() -> dict[int, dict]:
    """{product_id: {rating_avg, rating_count, sold}}. Кеш ~10 хв; при помилці БД — порожньо."""
    now = time.monotonic()
    if _stats_cache["data"] is not None and now - _stats_cache["ts"] < _STATS_TTL_SEC:
        return _stats_cache["data"]
    try:
        data = _fetch_stats()
    except Exception:
        logger.warning("reviews: не вдалось порахувати рейтинг і продажі", exc_info=True)
        return {}
    _stats_cache.update(ts=now, data=data)
    return data


def invalidate_stats_cache() -> None:
    _stats_cache.update(ts=0.0, data=None)


def with_stats(products: list[dict]) -> list[dict]:
    """Копії товарів з rating_avg / rating_count (якщо є схвалені відгуки) і sold_count (від порогу).
    Кешовані словники каталогу не змінюються."""
    stats = product_stats()
    threshold = sold_count_min()
    out = []
    for p in products:
        s = stats.get(int(p.get("id") or 0))
        if not s:
            out.append(p)
            continue
        extra = {}
        if s.get("rating_count"):
            extra["rating_avg"] = s["rating_avg"]
            extra["rating_count"] = s["rating_count"]
        if s.get("sold", 0) >= max(1, threshold):
            extra["sold_count"] = s["sold"]
        out.append({**p, **extra} if extra else p)
    return out


# ─── ПУБЛІЧНИЙ СПИСОК ВІДГУКІВ ──────────────────────────────

def _public_photos(raw) -> list[str]:
    try:
        photos = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [u for u in photos if isinstance(u, str) and u.startswith("https://")][:3] if isinstance(photos, list) else []


def _first_name(author: str) -> str:
    """Покупцям показуємо лише імʼя, без прізвища."""
    name = (author or "").strip().split()
    return name[0][:32] if name else "Покупець"


def product_reviews(product_id: int, limit: int = 10, offset: int = 0) -> dict:
    """Схвалені відгуки товару, нові першими: {"reviews": [...], "total": N}."""
    limit = max(1, min(int(limit), MAX_PAGE))
    offset = max(0, int(offset))
    conn = db_connect()
    try:
        total = conn.execute(
            _sql("SELECT COUNT(*) FROM reviews WHERE product_id = ? AND status = 'approved'"),
            (int(product_id),),
        ).fetchone()[0]
        rows = conn.execute(
            _sql("""
                SELECT id, author_name, rating, text, photos, admin_reply, created_at
                FROM reviews
                WHERE product_id = ? AND status = 'approved'
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
            """),
            (int(product_id), limit, offset),
        ).fetchall()
    finally:
        conn.close()
    reviews = []
    for rid, author, rating, text, photos, reply, created in rows:
        reviews.append({
            "id": int(rid),
            "author": _first_name(author),
            "rating": max(1, min(int(rating or 0), 5)),
            "text": text or "",
            "photos": _public_photos(photos),
            "reply": reply or "",
            "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created or ""),
        })
    return {"reviews": reviews, "total": int(total or 0)}


# ─── ЗАЛИШИТИ ВІДГУК (лише покупець доставленого замовлення) ─────

TEXT_MAX = 1000
PHOTOS_MAX = 3
# Фото відгуків завантажує наш ендпоінт: public_id = poselyanov3dprint/review_<user_id>_<ts>.
# Приймаємо лише URL з нашого акаунта Cloudinary і лише власні фото користувача.
PHOTO_HOST = "res.cloudinary.com"
PHOTO_FOLDER = "poselyanov3dprint"
REVIEW_REQUEST_DELAY_DAYS = 1
REVIEW_REMINDER_DELAY_DAYS = 5   # через стільки днів після запиту — одне нагадування
REVIEW_REMINDER_MAX_AGE_DAYS = 30  # старі доставлені замовлення не турбуємо
# Бонус за відгук: персональний одноразовий купон, один на замовлення.
# Не залежить від оцінки — платимо за зміст, а не за позитив.
BONUS_TEXT_MIN_CHARS = 20
BONUS_TEXT_PERCENT = 5
BONUS_PHOTO_PERCENT = 10
BONUS_VALID_DAYS = 30


class ReviewError(Exception):
    """Помилка, текст якої можна показати користувачу."""


def _is_own_photo_url(url, user_id: int) -> bool:
    import config

    cloud = (config.CLOUDINARY_CLOUD_NAME or "").strip()
    if not cloud or not isinstance(url, str) or len(url) > 500:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.hostname != PHOTO_HOST or parsed.port is not None:
        return False
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.params:
        return False
    path = parsed.path
    return path.startswith(f"/{cloud}/image/upload/") and f"/{PHOTO_FOLDER}/review_{int(user_id)}_" in path


def _clean_photos(photos, user_id: int) -> list[str]:
    if not isinstance(photos, list):
        return []
    return [u for u in photos if _is_own_photo_url(u, user_id)][:PHOTOS_MAX]


def pending_reviews(user_id: int) -> list[dict]:
    """Що покупець ще може оцінити: товари доставлених замовлень без відгуку."""
    uid = int(user_id or 0)
    if uid <= 0:
        return []
    conn = db_connect()
    try:
        rows = conn.execute(
            _sql("""
                SELECT o.id, oi.product_id, oi.product_name
                FROM orders o
                JOIN order_items oi ON oi.order_id = o.id
                WHERE o.user_id = ? AND o.status = 'delivered'
                  AND oi.product_id IS NOT NULL AND oi.product_id > 0
                  AND NOT EXISTS (
                      SELECT 1 FROM reviews r WHERE r.order_id = o.id AND r.product_id = oi.product_id
                  )
                ORDER BY o.id DESC, oi.id
            """),
            (uid,),
        ).fetchall()
    finally:
        conn.close()
    seen: set[tuple[int, int]] = set()
    out = []
    for order_id, product_id, name in rows:
        key = (int(order_id), int(product_id))
        if key in seen:
            continue
        seen.add(key)
        out.append({"order_id": key[0], "product_id": key[1], "product_name": name or ""})
    return out


def submit_review(
    user_id: int,
    author_name: str,
    order_id: int,
    product_id: int,
    rating,
    text: str = "",
    photos=None,
) -> dict:
    """Зберігає відгук. Повертає {"id", "status"}; ReviewError — з текстом для покупця."""
    from services.settings import REVIEWS_AUTO_PUBLISH_KEY, get_bool

    uid = int(user_id or 0)
    try:
        order_id, product_id, rating = int(order_id), int(product_id), int(rating)
    except (TypeError, ValueError):
        raise ReviewError("Некоректні дані відгуку")
    if uid <= 0:
        raise ReviewError("Потрібна авторизація")
    if not 1 <= rating <= 5:
        raise ReviewError("Оцінка має бути від 1 до 5")
    text = str(text or "").strip()[:TEXT_MAX]
    photos = _clean_photos(photos, uid)
    status = "approved" if get_bool(REVIEWS_AUTO_PUBLISH_KEY) else "pending"

    conn = db_connect()
    try:
        order = conn.execute(
            _sql("SELECT user_id, status FROM orders WHERE id = ?"), (order_id,)
        ).fetchone()
        if not order or int(order[0] or 0) != uid:
            raise ReviewError("Замовлення не знайдено")
        if order[1] != "delivered":
            raise ReviewError("Відгук можна залишити після отримання замовлення")
        has_item = conn.execute(
            _sql("SELECT 1 FROM order_items WHERE order_id = ? AND product_id = ?"),
            (order_id, product_id),
        ).fetchone()
        if not has_item or product_id <= 0:
            raise ReviewError("Цього товару немає в замовленні")
        if conn.execute(
            _sql("SELECT 1 FROM reviews WHERE order_id = ? AND product_id = ?"), (order_id, product_id)
        ).fetchone():
            raise ReviewError("Ти вже залишив відгук на цей товар")
        params = (product_id, order_id, uid, (author_name or "").strip()[:64], rating, text,
                  json.dumps(photos), status)
        insert = """
            INSERT INTO reviews (product_id, order_id, user_id, author_name, rating, text, photos, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        if is_postgres():
            review_id = conn.execute(_sql(insert + " RETURNING id"), params).fetchone()[0]
        else:
            review_id = conn.execute(insert, params).lastrowid
        conn.commit()
    finally:
        conn.close()
    invalidate_stats_cache()
    return {"id": int(review_id), "status": status}


def get_review(review_id: int) -> dict | None:
    conn = db_connect()
    try:
        row = conn.execute(
            _sql("""
                SELECT r.id, r.product_id, r.order_id, r.user_id, r.author_name, r.rating, r.text,
                       r.photos, r.status, r.admin_reply
                FROM reviews r WHERE r.id = ?
            """),
            (int(review_id),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    keys = ("id", "product_id", "order_id", "user_id", "author_name", "rating", "text",
            "photos", "status", "admin_reply")
    review = dict(zip(keys, row))
    review["photos"] = _public_photos(review["photos"])
    return review


def set_review_status(review_id: int, status: str) -> bool:
    if status not in STATUSES:
        return False
    conn = db_connect()
    try:
        cur = conn.execute(_sql("UPDATE reviews SET status = ? WHERE id = ?"), (status, int(review_id)))
        conn.commit()
        changed = cur.rowcount > 0
    finally:
        conn.close()
    invalidate_stats_cache()
    return changed


# ─── АДМІНКА: МОДЕРАЦІЯ, ВІДПОВІДІ, ВИДАЛЕННЯ (етап 5, слайс 4) ───

REPLY_MAX = 1000
ADMIN_LIST_SCAN = 1000  # скільки найновіших рядків дивимось перед текстовим пошуком


def _iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def admin_list_reviews(
    status: str = "all",
    q: str = "",
    rating=None,
    limit: int = 100,
    product_names: dict | None = None,
) -> list[dict]:
    """Відгуки для сторінки «Відгуки», нові першими.

    `product_names` — {product_id: назва} з каталогу (товари не лежать у цій БД), потрібне для пошуку
    за назвою. Пошук (товар, імʼя/нік клієнта, текст, №замовлення) — у Python, бо LIKE у SQLite не
    знає регістру кирилиці. Дивимось ADMIN_LIST_SCAN найновіших рядків.
    """
    limit = max(1, min(int(limit or 100), 500))
    where, params = [], []
    if status in STATUSES:
        where.append("r.status = ?")
        params.append(status)
    try:
        rating = int(rating) if rating not in (None, "", "all") else None
    except (TypeError, ValueError):
        rating = None
    if rating is not None and 1 <= rating <= 5:
        where.append("r.rating = ?")
        params.append(rating)
    sql_text = """
        SELECT r.id, r.product_id, r.order_id, r.user_id, r.author_name, r.rating, r.text, r.photos,
               r.status, r.admin_reply, r.created_at, u.name, u.username
        FROM reviews r LEFT JOIN users u ON u.id = r.user_id
    """
    if where:
        sql_text += " WHERE " + " AND ".join(where)
    sql_text += " ORDER BY r.created_at DESC, r.id DESC LIMIT ?"
    conn = db_connect()
    try:
        rows = conn.execute(_sql(sql_text), (*params, ADMIN_LIST_SCAN)).fetchall()
    finally:
        conn.close()
    needle = str(q or "").strip().lower().lstrip("#")
    names = product_names or {}
    out = []
    for rid, pid, oid, uid, author, rate, text, photos, st, reply, created, uname, username in rows:
        client = (uname or author or "").strip()
        item = {
            "id": int(rid),
            "product_id": int(pid) if pid is not None else None,
            "order_id": int(oid) if oid is not None else None,
            "user_id": int(uid) if uid is not None else None,
            "client_name": client or "Покупець",
            "client_username": (username or "").lstrip("@"),
            "rating": max(1, min(int(rate or 0), 5)),
            "text": text or "",
            "photos": _public_photos(photos),
            "status": st,
            "reply": reply or "",
            "created_at": _iso(created),
        }
        if needle:
            hay = " ".join((
                str(names.get(item["product_id"], "")), item["client_name"], author or "",
                item["client_username"], item["text"], str(item["order_id"] or ""),
            )).lower()
            if needle not in hay:
                continue
        out.append(item)
        if len(out) >= limit:
            break
    return out


def admin_review_counts() -> dict:
    """{pending, approved, hidden, all, avg_rating, rated_count}: вкладки, шапка сторінки й лічильник меню.
    Середня й кількість — лише за опублікованими (так само, як бачить покупець)."""
    conn = db_connect()
    try:
        rows = conn.execute(
            _sql("SELECT status, COUNT(*), AVG(rating) FROM reviews GROUP BY status")
        ).fetchall()
    finally:
        conn.close()
    counts = {s: 0 for s in STATUSES}
    avg = None
    for st, n, a in rows:
        if st in counts:
            counts[st] = int(n or 0)
        if st == "approved" and n:
            avg = round(float(a), 1)
    return {**counts, "all": sum(counts.values()), "avg_rating": avg, "rated_count": counts["approved"]}


def pending_review_count() -> int:
    conn = db_connect()
    try:
        return int(conn.execute(_sql("SELECT COUNT(*) FROM reviews WHERE status = 'pending'")).fetchone()[0] or 0)
    finally:
        conn.close()


def set_admin_reply(review_id: int, reply) -> bool:
    """Відповідь магазину під відгуком (≤1000 символів, порожня — прибрати). False, якщо відгуку немає."""
    reply = str(reply or "").strip()
    if len(reply) > REPLY_MAX:
        raise ReviewError(f"Відповідь задовга (максимум {REPLY_MAX} символів)")
    conn = db_connect()
    try:
        cur = conn.execute(_sql("UPDATE reviews SET admin_reply = ? WHERE id = ?"), (reply, int(review_id)))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_review(review_id: int) -> bool:
    """Остаточно видаляє відгук і скидає кеш рейтингу. Покупець зможе залишити відгук на цей товар знову."""
    conn = db_connect()
    try:
        cur = conn.execute(_sql("DELETE FROM reviews WHERE id = ?"), (int(review_id),))
        conn.commit()
        deleted = cur.rowcount > 0
    finally:
        conn.close()
    invalidate_stats_cache()
    return deleted


# ─── ЗАПИТ ВІДГУКУ ПІСЛЯ ДОСТАВКИ ──────────────────────────────

def _days_ago(days: int) -> str:
    return f"NOW() - INTERVAL '{int(days)} days'" if is_postgres() else f"datetime('now', '-{int(days)} days')"


_UNREVIEWED_ITEM_EXISTS = """
    EXISTS (
        SELECT 1 FROM order_items oi
        WHERE oi.order_id = o.id AND oi.product_id > 0
          AND NOT EXISTS (
              SELECT 1 FROM reviews r WHERE r.order_id = o.id AND r.product_id = oi.product_id
          )
    )
"""


def _due_orders(where: str) -> list[dict]:
    conn = db_connect()
    try:
        rows = conn.execute(
            _sql(f"""
                SELECT o.id, o.user_id
                FROM orders o
                WHERE o.status = 'delivered' AND o.delivered_at IS NOT NULL
                  AND o.user_id IS NOT NULL AND o.user_id > 0
                  AND {where}
                  AND {_UNREVIEWED_ITEM_EXISTS}
                ORDER BY o.id
                LIMIT 50
            """)
        ).fetchall()
    finally:
        conn.close()
    return [{"order_id": int(r[0]), "user_id": int(r[1])} for r in rows]


def orders_due_review_request(days: int = REVIEW_REQUEST_DELAY_DAYS) -> list[dict]:
    """Доставлені `days` днів тому замовлення, яким ще не слали запит і де є що оцінити."""
    return _due_orders(f"o.review_requested_at IS NULL AND o.delivered_at <= {_days_ago(days)}")


def orders_due_review_reminder(days: int = REVIEW_REMINDER_DELAY_DAYS) -> list[dict]:
    """Одне нагадування: запит був `days` днів тому, відгуку досі немає."""
    return _due_orders(
        f"o.review_requested_at IS NOT NULL AND o.review_requested_at <= {_days_ago(days)} "
        f"AND o.review_reminded_at IS NULL AND o.delivered_at >= {_days_ago(REVIEW_REMINDER_MAX_AGE_DAYS)}"
    )


def _mark_order_time(column: str, order_id: int) -> None:
    now_expr = "NOW()" if is_postgres() else "CURRENT_TIMESTAMP"
    conn = db_connect()
    try:
        conn.execute(_sql(f"UPDATE orders SET {column} = {now_expr} WHERE id = ?"), (int(order_id),))
        conn.commit()
    finally:
        conn.close()


def mark_review_requested(order_id: int) -> None:
    _mark_order_time("review_requested_at", order_id)


def mark_review_reminded(order_id: int) -> None:
    _mark_order_time("review_reminded_at", order_id)


# ─── БОНУС ЗА ВІДГУК ─────────────────────────────────────────────

def bonus_config() -> dict:
    """Умови бонусу для форми відгуку й повідомлень."""
    return {
        "text_percent": BONUS_TEXT_PERCENT,
        "photo_percent": BONUS_PHOTO_PERCENT,
        "text_min": BONUS_TEXT_MIN_CHARS,
        "valid_days": BONUS_VALID_DAYS,
    }


def review_bonus_percent(review: dict) -> int:
    """Знижка за відгук: з фото — більша, зі змістовним текстом — менша, лише зірки — без бонусу."""
    if review.get("photos"):
        return BONUS_PHOTO_PERCENT
    if len(str(review.get("text") or "").strip()) >= BONUS_TEXT_MIN_CHARS:
        return BONUS_TEXT_PERCENT
    return 0


def grant_review_bonus(review_id: int) -> dict | None:
    """Видає персональний купон за опублікований відгук. Один бонус на замовлення, ідемпотентно:
    замовлення «захоплюється» одним UPDATE, тож повторна публікація чи гонка двох запитів купона не задвоять.
    Повертає {"code","percent","expires_at","link","user_id","order_id"} або None."""
    from services.coupons import create_personal_coupon

    review = get_review(review_id)
    if not review or review["status"] != "approved":
        return None
    user_id, order_id = int(review.get("user_id") or 0), int(review.get("order_id") or 0)
    percent = review_bonus_percent(review)
    if user_id <= 0 or order_id <= 0 or percent <= 0:
        return None

    conn = db_connect()
    try:
        cur = conn.execute(
            _sql("UPDATE orders SET review_bonus_code = 'pending' WHERE id = ? AND COALESCE(review_bonus_code, '') = ''"),
            (order_id,),
        )
        conn.commit()
        if cur.rowcount != 1:
            return None
    finally:
        conn.close()

    coupon = create_personal_coupon(user_id, percent=percent, days=BONUS_VALID_DAYS)
    conn = db_connect()
    try:
        conn.execute(
            _sql("UPDATE orders SET review_bonus_code = ? WHERE id = ?"),
            (coupon.get("code", "") if coupon.get("ok") else "", order_id),
        )
        conn.commit()
    finally:
        conn.close()
    if not coupon.get("ok"):
        logger.warning("Бонус за відгук #%s: не вдалось створити купон: %s", review_id, coupon.get("error"))
        return None
    return {**coupon, "order_id": order_id}
