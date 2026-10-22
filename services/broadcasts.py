"""Розсилки з адмінки: таблиця broadcasts, сегменти, відправка у фоні, облік переходів.

Безпека: сама відправка в Telegram — це ін'єктована функція `send_one(user_id, broadcast)`
(див. bot._make_broadcast_sender), тож тести й перегляд підміняють її заглушкою.
Один раз: перехід scheduled/draft -> sending робиться атомарним UPDATE з перевіркою статусу
(`claim_broadcast`), тому ні фоновий цикл, ні кнопка «Надіслати» не відправлять розсилку двічі.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import config
from db_core import db_connect, is_postgres, sql as _sql
from services import users as users_svc

logger = logging.getLogger(__name__)

STATUSES = ("draft", "scheduled", "sending", "sent", "failed")
TEXT_MAX = 4096
CAPTION_MAX = 1024
LABEL_MAX = 32
ATTRIBUTION_DAYS = 7
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_PROGRESS_EVERY = 20


def init_broadcasts_table(conn) -> None:
    if is_postgres():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id            BIGSERIAL PRIMARY KEY,
                segment       TEXT NOT NULL DEFAULT 'all',
                text          TEXT NOT NULL DEFAULT '',
                photo         TEXT NOT NULL DEFAULT '',
                button_label  TEXT NOT NULL DEFAULT '',
                button_target TEXT NOT NULL DEFAULT '',
                scheduled_at  TIMESTAMPTZ,
                status        TEXT NOT NULL DEFAULT 'draft',
                total         INTEGER NOT NULL DEFAULT 0,
                delivered     INTEGER NOT NULL DEFAULT 0,
                failed        INTEGER NOT NULL DEFAULT 0,
                clicks        INTEGER NOT NULL DEFAULT 0,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                sent_at       TIMESTAMPTZ
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                segment       TEXT NOT NULL DEFAULT 'all',
                text          TEXT NOT NULL DEFAULT '',
                photo         TEXT NOT NULL DEFAULT '',
                button_label  TEXT NOT NULL DEFAULT '',
                button_target TEXT NOT NULL DEFAULT '',
                scheduled_at  TIMESTAMP,
                status        TEXT NOT NULL DEFAULT 'draft',
                total         INTEGER NOT NULL DEFAULT 0,
                delivered     INTEGER NOT NULL DEFAULT 0,
                failed        INTEGER NOT NULL DEFAULT 0,
                clicks        INTEGER NOT NULL DEFAULT 0,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_at       TIMESTAMP
            )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_status_sched ON broadcasts(status, scheduled_at)")


def _now_sql() -> str:
    return "NOW()" if is_postgres() else "CURRENT_TIMESTAMP"


def _iso(value) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    s = str(value)
    # SQLite віддає UTC без пояса — позначаємо явно, щоб браузер не читав це як локальний час
    return s.replace(" ", "T") + "Z" if re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$", s) else s


def _db_time(dt: datetime):
    """Значення для запису в колонку часу: aware datetime (Postgres) або UTC-рядок (SQLite)."""
    dt = dt.astimezone(timezone.utc)
    return dt if is_postgres() else dt.strftime("%Y-%m-%d %H:%M:%S")


def is_undeliverable_error(exc: BaseException) -> bool:
    """Користувач заблокував бота / видалив акаунт — позначаємо blocked, не повторюємо."""
    msg = str(exc).lower()
    return any(m in msg for m in ("bot was blocked", "user is deactivated", "chat not found", "forbidden"))


# ── Кнопка / посилання ──────────────────────────────────────────────

def parse_target(raw: str) -> tuple[str, str] | None:
    """'shop' | 'product:12' | 'collection:slug' | 'url:https://…' -> (kind, value)."""
    raw = str(raw or "").strip()
    if raw == "shop":
        return "shop", ""
    kind, _, value = raw.partition(":")
    value = value.strip()
    if kind == "product" and value.isdigit() and int(value) > 0:
        return "product", str(int(value))
    if kind == "collection" and _SLUG_RE.match(value):
        return "collection", value
    if kind == "url" and re.match(r"^https://[^\s]{3,500}$", value):
        return "url", value
    return None


def button_url(broadcast_id: int, target: str) -> str:
    """Посилання кнопки: Mini App з start_param `bc_<id>[_product_N|_collection_slug]` або зовнішній https."""
    parsed = parse_target(target)
    if not parsed:
        return ""
    kind, value = parsed
    if kind == "url":
        return value
    param = f"bc_{int(broadcast_id)}"
    if kind in ("product", "collection"):
        param += f"_{kind}_{value}"
    return f"https://t.me/{config.BOT_USERNAME}?startapp={param}"


# ── Створення / читання ─────────────────────────────────────────────

def _parse_when(value) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def validate_broadcast(data: dict) -> tuple[dict | None, str | None]:
    segment = users_svc.normalize_segment(data.get("segment"))
    if str(data.get("segment") or "all").strip().lower() not in users_svc.SEGMENT_KEYS:
        return None, "Невідомий сегмент"
    text = str(data.get("text") or "").strip()
    photo = str(data.get("photo") or "").strip()
    if not text:
        return None, "Текст розсилки обов'язковий"
    if photo and not re.match(r"^https://[^\s]{3,500}$", photo):
        return None, "Фото має бути https-посиланням"
    if len(text) > (CAPTION_MAX if photo else TEXT_MAX):
        return None, f"Текст задовгий (максимум {CAPTION_MAX if photo else TEXT_MAX} символів{' з фото' if photo else ''})"
    label = str(data.get("button_label") or "").strip()
    target = str(data.get("button_target") or "").strip()
    if label or target:
        if not label:
            return None, "Вкажи текст кнопки"
        if len(label) > LABEL_MAX:
            return None, f"Текст кнопки до {LABEL_MAX} символів"
        if not parse_target(target):
            return None, "Невірна ціль кнопки"
        if len(button_url(999999999, target)) > 400:
            return None, "Ціль кнопки задовга"
    try:
        when = _parse_when(data.get("scheduled_at"))
    except ValueError:
        return None, "Невірна дата розсилки"
    now = datetime.now(timezone.utc)
    if when and when > now + timedelta(days=365):
        return None, "Дата надто далеко"
    return {"segment": segment, "text": text, "photo": photo, "button_label": label,
            "button_target": target if label else "", "when": when, "draft": bool(data.get("draft"))}, None


def create_broadcast(data: dict) -> dict:
    """Створює розсилку. Без `scheduled_at` (або дата вже настала) — статус scheduled з часом «зараз»,
    тобто піде на відправку негайно; із майбутньою датою — відкладена; `draft` — чернетка."""
    payload, err = validate_broadcast(data)
    if err:
        return {"ok": False, "error": err}
    now = datetime.now(timezone.utc)
    when = payload["when"]
    if when is None or when < now:
        when = now
    status = "draft" if payload["draft"] else "scheduled"
    total = len(users_svc.segment_user_ids(payload["segment"]))
    conn = db_connect()
    try:
        if is_postgres():
            cur = conn.execute(
                "INSERT INTO broadcasts (segment, text, photo, button_label, button_target, scheduled_at, status, total) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (payload["segment"], payload["text"], payload["photo"], payload["button_label"],
                 payload["button_target"], _db_time(when), status, total),
            )
            bid = int(cur.fetchone()[0])
        else:
            cur = conn.execute(
                "INSERT INTO broadcasts (segment, text, photo, button_label, button_target, scheduled_at, status, total) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (payload["segment"], payload["text"], payload["photo"], payload["button_label"],
                 payload["button_target"], _db_time(when), status, total),
            )
            bid = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": bid, "status": status, "total": total, "send_now": status == "scheduled" and when <= now}


_COLS = ("id", "segment", "text", "photo", "button_label", "button_target", "scheduled_at", "status",
         "total", "delivered", "failed", "clicks", "created_at", "sent_at")


def _row(r) -> dict:
    d = dict(zip(_COLS, r))
    for k in ("scheduled_at", "created_at", "sent_at"):
        d[k] = _iso(d[k])
    for k in ("id", "total", "delivered", "failed", "clicks"):
        d[k] = int(d[k] or 0)
    return d


def get_broadcast(bid: int) -> dict | None:
    conn = db_connect()
    try:
        r = conn.execute(_sql(f"SELECT {', '.join(_COLS)} FROM broadcasts WHERE id = ?"), (int(bid),)).fetchone()
    finally:
        conn.close()
    return _row(r) if r else None


def _attribution() -> dict[int, tuple[int, int]]:
    """bid -> (замовлень, виторг): замовлення клієнта в статусах confirmed…delivered, зроблені
    протягом ATTRIBUTION_DAYS після його переходу з розсилки."""
    from services.hits import SOLD_STATUSES

    marks = ", ".join("?" * len(SOLD_STATUSES))
    horizon = (
        f"e.created_at + INTERVAL '{ATTRIBUTION_DAYS} days'" if is_postgres()
        else f"datetime(e.created_at, '+{ATTRIBUTION_DAYS} days')"
    )
    conn = db_connect()
    try:
        rows = conn.execute(
            _sql(
                f"""
                SELECT e.value, COUNT(DISTINCT o.id), COALESCE(SUM(o.total_price), 0)
                FROM events e
                JOIN orders o ON o.user_id = e.user_id AND o.ordered_at >= e.created_at
                             AND o.ordered_at <= {horizon} AND o.status IN ({marks}) AND COALESCE(o.is_test, 0) = 0
                WHERE e.type = 'broadcast_open'
                GROUP BY e.value
                """
            ),
            tuple(SOLD_STATUSES),
        ).fetchall()
    finally:
        conn.close()
    return {int(r[0]): (int(r[1] or 0), int(r[2] or 0)) for r in rows if r[0] is not None}


def list_broadcasts(limit: int = 100) -> list[dict]:
    conn = db_connect()
    try:
        rows = conn.execute(
            _sql(f"SELECT {', '.join(_COLS)} FROM broadcasts ORDER BY id DESC LIMIT ?"), (int(limit),)
        ).fetchall()
    finally:
        conn.close()
    items = [_row(r) for r in rows]
    try:
        attr = _attribution()
    except Exception:
        logger.warning("broadcasts: атрибуція замовлень не вдалась", exc_info=True)
        attr = {}
    for b in items:
        b["segment_label"] = users_svc.SEGMENT_LABELS.get(b["segment"], b["segment"])
        b["clicks_pct"] = round(100 * b["clicks"] / b["delivered"], 1) if b["delivered"] else 0
        b["orders"], b["revenue"] = attr.get(b["id"], (0, 0))
        b["link"] = button_url(b["id"], b["button_target"]) if b["button_label"] else ""
    return items


def preview_recipients(segment: str) -> dict:
    seg = users_svc.normalize_segment(segment)
    return {"segment": seg, "count": len(users_svc.segment_user_ids(seg))}


def delete_broadcast(bid: int) -> str:
    """'deleted' | 'not_found' | 'locked' (видаляти можна лише draft/scheduled)."""
    conn = db_connect()
    try:
        cur = conn.execute(_sql("DELETE FROM broadcasts WHERE id = ? AND status IN ('draft','scheduled')"), (int(bid),))
        conn.commit()
        if cur.rowcount > 0:
            return "deleted"
        exists = conn.execute(_sql("SELECT 1 FROM broadcasts WHERE id = ?"), (int(bid),)).fetchone()
    finally:
        conn.close()
    return "locked" if exists else "not_found"


# ── Відправка ───────────────────────────────────────────────────────

def due_broadcast_ids() -> list[int]:
    conn = db_connect()
    try:
        rows = conn.execute(
            f"SELECT id FROM broadcasts WHERE status = 'scheduled' AND scheduled_at <= {_now_sql()} ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [int(r[0]) for r in rows]


def claim_broadcast(bid: int) -> bool:
    """Атомарно scheduled -> sending. True лише для одного виклику — захист від подвійної відправки."""
    conn = db_connect()
    try:
        cur = conn.execute(_sql("UPDATE broadcasts SET status = 'sending' WHERE id = ? AND status = 'scheduled'"), (int(bid),))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def recover_interrupted() -> int:
    """Після рестарту: розсилки у статусі sending (процес впав посеред відправки) позначаємо failed
    і НЕ відправляємо повторно — невідомо, кому вже дійшло."""
    conn = db_connect()
    try:
        cur = conn.execute(f"UPDATE broadcasts SET status = 'failed', sent_at = {_now_sql()} WHERE status = 'sending'")
        conn.commit()
        return max(0, cur.rowcount)
    finally:
        conn.close()


def _update_counters(bid: int, delivered: int, failed: int) -> None:
    conn = db_connect()
    try:
        conn.execute(_sql("UPDATE broadcasts SET delivered = ?, failed = ? WHERE id = ?"), (delivered, failed, int(bid)))
        conn.commit()
    finally:
        conn.close()


def _finish(bid: int, status: str, delivered: int, failed: int) -> None:
    conn = db_connect()
    try:
        conn.execute(
            _sql(f"UPDATE broadcasts SET status = ?, delivered = ?, failed = ?, sent_at = {_now_sql()} WHERE id = ?"),
            (status, delivered, failed, int(bid)),
        )
        conn.commit()
    finally:
        conn.close()


async def send_broadcast(bid: int, send_one, delay: float | None = None, sleep=asyncio.sleep) -> dict:
    """Відправити розсилку. `send_one(user_id, broadcast)` — async, кидає виняток при невдачі.
    Повертає {'ok', 'skipped'?, 'delivered', 'failed'}."""
    if not await asyncio.to_thread(claim_broadcast, bid):
        return {"ok": False, "skipped": True}
    b = await asyncio.to_thread(get_broadcast, bid)
    delay = config.BROADCAST_DELAY_SEC if delay is None else delay
    delivered = failed = 0
    try:
        recipients = await asyncio.to_thread(users_svc.segment_user_ids, b["segment"])
        conn_total = len(recipients)
        await asyncio.to_thread(_set_total, bid, conn_total)
        for i, uid in enumerate(recipients, 1):
            try:
                await send_one(uid, b)
                delivered += 1
            except Exception as e:  # noqa: BLE001 — будь-яка помилка Telegram рахується як недоставлено
                failed += 1
                if is_undeliverable_error(e):
                    await asyncio.to_thread(users_svc.set_blocked, uid, True)
                else:
                    logger.warning("Broadcast #%s: не надіслано %s: %s", bid, uid, e)
            if i % _PROGRESS_EVERY == 0:
                await asyncio.to_thread(_update_counters, bid, delivered, failed)
            if delay > 0:
                await sleep(delay)
    except asyncio.CancelledError:
        await asyncio.to_thread(_finish, bid, "failed", delivered, failed)
        raise
    except Exception:
        logger.exception("Broadcast #%s перервано", bid)
        await asyncio.to_thread(_finish, bid, "failed", delivered, failed)
        return {"ok": False, "delivered": delivered, "failed": failed}
    status = "failed" if (delivered == 0 and failed > 0) else "sent"
    await asyncio.to_thread(_finish, bid, status, delivered, failed)
    return {"ok": True, "delivered": delivered, "failed": failed}


def _set_total(bid: int, total: int) -> None:
    conn = db_connect()
    try:
        conn.execute(_sql("UPDATE broadcasts SET total = ? WHERE id = ?"), (total, int(bid)))
        conn.commit()
    finally:
        conn.close()


async def run_due_broadcasts(send_one, **kw) -> int:
    n = 0
    for bid in await asyncio.to_thread(due_broadcast_ids):
        res = await send_broadcast(bid, send_one, **kw)
        if not res.get("skipped"):
            n += 1
    return n


# ── Переходи (start_param bc_<id>) ──────────────────────────────────

def record_click(user_id: int, bid: int) -> bool:
    """Один перехід на клієнта на розсилку (подія broadcast_open, value = id розсилки)."""
    if not user_id or not bid:
        return False
    conn = db_connect()
    try:
        b = conn.execute(_sql("SELECT status FROM broadcasts WHERE id = ?"), (int(bid),)).fetchone()
        if not b or b[0] not in ("sending", "sent", "failed"):
            return False
        if conn.execute(
            _sql("SELECT 1 FROM events WHERE type = 'broadcast_open' AND user_id = ? AND value = ? LIMIT 1"),
            (int(user_id), int(bid)),
        ).fetchone():
            return False
        conn.execute(
            _sql("INSERT INTO events (type, user_id, value) VALUES ('broadcast_open', ?, ?)"), (int(user_id), int(bid))
        )
        conn.execute(_sql("UPDATE broadcasts SET clicks = clicks + 1 WHERE id = ?"), (int(bid),))
        conn.commit()
    finally:
        conn.close()
    return True
