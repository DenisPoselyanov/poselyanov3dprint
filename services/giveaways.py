"""Розіграші за підписку на канал: участь через бота, жеребкування з вагами, приз у магазині переможця.

Приз не потребує коду: Mini App бере активні призи з /api/my_prizes, а замовлення передає
prize_winner_id. Приз забрано, коли є не скасоване замовлення з ним (giveaway_winners.order_id) —
скасували замовлення до кінця строку, і приз знову активний.

Механіка — у GIVEAWAYS.md. Тут лише дані й чисті розрахунки; усе, що б'є в Telegram
(перевірка підписки, пости, повідомлення), робить bot.py.

Статуси розіграшу: active → drawing (іде жеребкування) → drawn (чернетка результатів)
→ published; active → cancelled. Переходи атомарні (UPDATE … WHERE status = …), тож фоновий цикл
і кнопка «Завершити зараз» не проведуть жеребкування двічі.
"""

from __future__ import annotations

import logging
import math
import re
import secrets
from datetime import datetime, timedelta, timezone

from db_core import db_connect, is_postgres, sql as _sql

logger = logging.getLogger(__name__)

STATUSES = ("active", "drawing", "drawn", "published", "cancelled")
SHIPPING_PAYERS = ("shop", "winner")
# product — конкретний товар безкоштовно (рядок за 0 ₴); amount — сертифікат на будь-яке замовлення
PRIZE_TYPES = ("product", "amount")
RESERVE_COUNT = 3
CLAIM_HOURS = 48
EXTEND_HOURS = 24
# Нагадування переможцю: (прапорець, за скільки годин до кінця)
REMINDERS = (("reminded_24", 24), ("reminded_3", 3))
CARD_URL_RE = re.compile(r"^https://res\.cloudinary\.com/[^\s]{3,500}$")
MAX_WINNERS = 20
MAX_REFERRAL_LIMIT = 50
DEFAULT_REFERRAL_LIMIT = 5
TITLE_MAX = 100
TEXT_MAX = 900  # підпис фото — 1024, лишаємо запас під рядок про дату завершення
_HTTPS_RE = re.compile(r"^https://[^\s]{3,500}$")
_START_RE = re.compile(r"^gw_(\d{1,9})(?:_r(\d{1,15}))?$")
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def init_giveaways_table(conn) -> None:
    if is_postgres():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaways (
                id              BIGSERIAL PRIMARY KEY,
                title           TEXT NOT NULL DEFAULT '',
                prize_type      TEXT NOT NULL DEFAULT 'product',
                product_id      BIGINT NOT NULL DEFAULT 0,
                product_variant TEXT NOT NULL DEFAULT '',
                prize_value     INTEGER NOT NULL DEFAULT 0,
                winners_count   INTEGER NOT NULL DEFAULT 1,
                ends_at         TIMESTAMPTZ NOT NULL,
                channel         TEXT NOT NULL DEFAULT '',
                shipping_payer  TEXT NOT NULL DEFAULT 'winner',
                referral_bonus  INTEGER NOT NULL DEFAULT 0,
                referral_limit  INTEGER NOT NULL DEFAULT 5,
                text            TEXT NOT NULL DEFAULT '',
                photo           TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'active',
                message_id      BIGINT,
                result_message_id BIGINT,
                eligible_count  INTEGER NOT NULL DEFAULT 0,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                drawn_at        TIMESTAMPTZ,
                published_at    TIMESTAMPTZ
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                id           BIGSERIAL PRIMARY KEY,
                giveaway_id  BIGINT NOT NULL,
                user_id      BIGINT NOT NULL,
                number       INTEGER NOT NULL,
                referred_by  BIGINT,
                is_new       INTEGER NOT NULL DEFAULT 0,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (giveaway_id, user_id),
                UNIQUE (giveaway_id, number)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_winners (
                id             BIGSERIAL PRIMARY KEY,
                giveaway_id    BIGINT NOT NULL,
                user_id        BIGINT NOT NULL,
                role           TEXT NOT NULL DEFAULT 'winner',
                position       INTEGER NOT NULL DEFAULT 0,
                status         TEXT NOT NULL DEFAULT 'pending',
                coupon_code    TEXT,
                claim_deadline TIMESTAMPTZ,
                order_id       BIGINT,
                variant_id     TEXT NOT NULL DEFAULT '',
                card_url       TEXT NOT NULL DEFAULT '',
                reminded_24    INTEGER NOT NULL DEFAULT 0,
                reminded_3     INTEGER NOT NULL DEFAULT 0,
                expired_notified INTEGER NOT NULL DEFAULT 0,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaways (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT NOT NULL DEFAULT '',
                prize_type      TEXT NOT NULL DEFAULT 'product',
                product_id      INTEGER NOT NULL DEFAULT 0,
                product_variant TEXT NOT NULL DEFAULT '',
                prize_value     INTEGER NOT NULL DEFAULT 0,
                winners_count   INTEGER NOT NULL DEFAULT 1,
                ends_at         TIMESTAMP NOT NULL,
                channel         TEXT NOT NULL DEFAULT '',
                shipping_payer  TEXT NOT NULL DEFAULT 'winner',
                referral_bonus  INTEGER NOT NULL DEFAULT 0,
                referral_limit  INTEGER NOT NULL DEFAULT 5,
                text            TEXT NOT NULL DEFAULT '',
                photo           TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'active',
                message_id      INTEGER,
                result_message_id INTEGER,
                eligible_count  INTEGER NOT NULL DEFAULT 0,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                drawn_at        TIMESTAMP,
                published_at    TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                giveaway_id  INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                number       INTEGER NOT NULL,
                referred_by  INTEGER,
                is_new       INTEGER NOT NULL DEFAULT 0,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (giveaway_id, user_id),
                UNIQUE (giveaway_id, number)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_winners (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                giveaway_id    INTEGER NOT NULL,
                user_id        INTEGER NOT NULL,
                role           TEXT NOT NULL DEFAULT 'winner',
                position       INTEGER NOT NULL DEFAULT 0,
                status         TEXT NOT NULL DEFAULT 'pending',
                coupon_code    TEXT,
                claim_deadline TIMESTAMP,
                order_id       INTEGER,
                variant_id     TEXT NOT NULL DEFAULT '',
                card_url       TEXT NOT NULL DEFAULT '',
                reminded_24    INTEGER NOT NULL DEFAULT 0,
                reminded_3     INTEGER NOT NULL DEFAULT 0,
                expired_notified INTEGER NOT NULL DEFAULT 0,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_giveaways_status_ends ON giveaways(status, ends_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_giveaway_entries_ref ON giveaway_entries(giveaway_id, referred_by)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_giveaway_winners_gw ON giveaway_winners(giveaway_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_giveaway_winners_user ON giveaway_winners(user_id, status)")


# ── Допоміжне ───────────────────────────────────────────────────────

def _now_sql() -> str:
    return "NOW()" if is_postgres() else "CURRENT_TIMESTAMP"


def _db_time(dt: datetime):
    """Aware datetime (Postgres) або UTC-рядок (SQLite) — як у broadcasts."""
    dt = dt.astimezone(timezone.utc)
    return dt if is_postgres() else dt.strftime("%Y-%m-%d %H:%M:%S")


def _iso(value) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    s = str(value)
    # SQLite віддає UTC без пояса — позначаємо явно, щоб браузер не читав це як локальний час
    return s.replace(" ", "T") + "Z" if re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$", s) else s


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo("Europe/Kyiv")
except Exception:  # немає tzdata — рахуємо за UTC+3
    _TZ = timezone(timedelta(hours=3))


def format_kyiv(value) -> str:
    """'28.10 о 20:00' за київським часом — для повідомлень у Telegram."""
    try:
        dt = _parse_dt(value)
    except ValueError:
        return ""
    return dt.astimezone(_TZ).strftime("%d.%m о %H:%M") if dt else ""


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _bool(value) -> bool:
    return value in (1, True, "1", "true", "on")


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ── Посилання ───────────────────────────────────────────────────────

def parse_start_param(raw: str) -> tuple[int, int] | None:
    """`gw_<id>` / `gw_<id>_r<user_id>` -> (giveaway_id, referrer_id або 0)."""
    m = _START_RE.match(str(raw or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0)


def join_link(bot_username: str, giveaway_id: int, referrer_id: int = 0) -> str:
    param = f"gw_{int(giveaway_id)}" + (f"_r{int(referrer_id)}" if referrer_id else "")
    return f"https://t.me/{bot_username}?start={param}"


def prize_link(bot_username: str) -> str:
    """Mini App: вікно перемоги показується саме, `startapp=prize` одразу відкриває кошик із призом."""
    return f"https://t.me/{bot_username}?startapp=prize"


def channel_url(channel: str, message_id: int | None = None) -> str:
    """Публічне посилання на канал (або пост) для «Поділитися»; приватний канал — порожньо."""
    name = str(channel or "").strip().lstrip("@")
    if not name or name.lstrip("-").isdigit():
        return ""
    return f"https://t.me/{name}" + (f"/{int(message_id)}" if message_id else "")


# ── Створення ───────────────────────────────────────────────────────

def validate_giveaway(data: dict) -> tuple[dict | None, str | None]:
    """Для prize_type=product вартість призу ставить bot.py з ціни товару (тут не обов'язкова)."""
    prize_type = str(data.get("prize_type") or "product").strip()
    if prize_type not in PRIZE_TYPES:
        return None, "Невідомий тип призу"
    product_id = max(0, _int(data.get("product_id")))
    if prize_type == "product" and product_id <= 0:
        return None, "Обери товар-приз"
    # Зафіксований варіант (розмір/колір); порожньо — переможець обирає сам. Чи існує — перевіряє bot.py
    product_variant = str(data.get("product_variant") or "").strip().lower()[:16] if prize_type == "product" else ""
    title = str(data.get("title") or "").strip()[:TITLE_MAX]
    if prize_type == "amount" and not product_id and not title:
        return None, "Вкажи назву призу"
    prize_value = _int(data.get("prize_value"), -1)
    if prize_type == "product" and prize_value <= 0:
        prize_value = 0
    elif prize_value <= 0 or prize_value > 1_000_000:
        return None, "Вкажи суму сертифіката в гривнях"
    winners = _int(data.get("winners_count"), 0)
    if not 1 <= winners <= MAX_WINNERS:
        return None, f"Переможців — від 1 до {MAX_WINNERS}"
    try:
        ends_at = _parse_dt(data.get("ends_at"))
    except ValueError:
        return None, "Невірна дата завершення"
    now = datetime.now(timezone.utc)
    if not ends_at or ends_at < now + timedelta(minutes=5):
        return None, "Завершення має бути щонайменше через 5 хвилин"
    if ends_at > now + timedelta(days=90):
        return None, "Розіграш може тривати до 90 днів"
    payer = str(data.get("shipping_payer") or "").strip()
    if payer not in SHIPPING_PAYERS:
        return None, "Обери, хто платить за доставку"
    bonus = _bool(data.get("referral_bonus"))
    limit = _int(data.get("referral_limit"), DEFAULT_REFERRAL_LIMIT)
    if bonus and not 1 <= limit <= MAX_REFERRAL_LIMIT:
        return None, f"Ліміт бонусів — від 1 до {MAX_REFERRAL_LIMIT}"
    text = str(data.get("text") or "").strip()
    if not text:
        return None, "Напиши текст поста"
    if len(text) > TEXT_MAX:
        return None, f"Текст задовгий (максимум {TEXT_MAX} символів)"
    photo = str(data.get("photo") or "").strip()
    if photo and not _HTTPS_RE.match(photo):
        return None, "Фото має бути https-посиланням"
    return {
        "prize_type": prize_type, "product_id": product_id, "product_variant": product_variant, "title": title, "prize_value": prize_value, "winners_count": winners,
        "ends_at": ends_at, "shipping_payer": payer, "referral_bonus": 1 if bonus else 0,
        "referral_limit": limit if bonus else DEFAULT_REFERRAL_LIMIT, "text": text, "photo": photo,
    }, None


def create_giveaway(payload: dict, channel: str) -> int:
    conn = db_connect()
    try:
        cols = ("title", "prize_type", "product_id", "product_variant", "prize_value", "winners_count", "ends_at", "channel", "shipping_payer",
                "referral_bonus", "referral_limit", "text", "photo")
        values = tuple(_db_time(payload["ends_at"]) if c == "ends_at" else (channel if c == "channel" else payload[c])
                       for c in cols)
        placeholders = ", ".join("?" for _ in cols)
        if is_postgres():
            cur = conn.execute(
                _sql(f"INSERT INTO giveaways ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id"), values
            )
            gid = int(cur.fetchone()[0])
        else:
            cur = conn.execute(f"INSERT INTO giveaways ({', '.join(cols)}) VALUES ({placeholders})", values)
            gid = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    return gid


def set_message_id(giveaway_id: int, message_id: int) -> None:
    conn = db_connect()
    try:
        conn.execute(_sql("UPDATE giveaways SET message_id = ? WHERE id = ?"), (int(message_id), int(giveaway_id)))
        conn.commit()
    finally:
        conn.close()


def delete_giveaway(giveaway_id: int) -> None:
    """Прибрати розіграш, пост якого не вдалось опублікувати (учасників ще немає)."""
    conn = db_connect()
    try:
        conn.execute(_sql("DELETE FROM giveaways WHERE id = ? AND message_id IS NULL"), (int(giveaway_id),))
        conn.commit()
    finally:
        conn.close()


_GW_COLS = ("id", "title", "prize_type", "product_id", "product_variant", "prize_value", "winners_count", "ends_at", "channel", "shipping_payer",
            "referral_bonus", "referral_limit", "text", "photo", "status", "message_id", "result_message_id",
            "eligible_count", "created_at", "drawn_at", "published_at")


def _gw_row(row: dict) -> dict:
    g = dict(row)
    for k in ("ends_at", "created_at", "drawn_at", "published_at"):
        g[k] = _iso(g.get(k))
    for k in ("id", "product_id", "prize_value", "winners_count", "referral_limit", "eligible_count"):
        g[k] = int(g.get(k) or 0)
    g["referral_bonus"] = bool(g.get("referral_bonus"))
    return g


def get_giveaway(giveaway_id: int) -> dict | None:
    conn = db_connect()
    try:
        cur = conn.execute(_sql(f"SELECT {', '.join(_GW_COLS)} FROM giveaways WHERE id = ?"), (int(giveaway_id),))
        rows = _rows(cur)
    finally:
        conn.close()
    return _gw_row(rows[0]) if rows else None


def is_open(g: dict | None) -> bool:
    if not g or g.get("status") != "active":
        return False
    ends = _parse_dt(g.get("ends_at"))
    return bool(ends and ends > datetime.now(timezone.utc))


def cancel_giveaway(giveaway_id: int) -> bool:
    conn = db_connect()
    try:
        cur = conn.execute(
            _sql("UPDATE giveaways SET status = 'cancelled' WHERE id = ? AND status = 'active'"), (int(giveaway_id),)
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


# ── Участь ──────────────────────────────────────────────────────────

def get_entry(giveaway_id: int, user_id: int) -> dict | None:
    conn = db_connect()
    try:
        cur = conn.execute(
            _sql("SELECT number, referred_by, is_new FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?"),
            (int(giveaway_id), int(user_id)),
        )
        rows = _rows(cur)
    finally:
        conn.close()
    return rows[0] if rows else None


def referral_count(giveaway_id: int, user_id: int) -> int:
    conn = db_connect()
    try:
        row = conn.execute(
            _sql("SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id = ? AND referred_by = ?"),
            (int(giveaway_id), int(user_id)),
        ).fetchone()
    finally:
        conn.close()
    return int(row[0] or 0)


def tickets_preview(g: dict, user_id: int) -> int:
    """Шанси учасника зараз (без перевірки підписки друзів — остаточно рахує жеребкування)."""
    if not g.get("referral_bonus"):
        return 1
    return 1 + min(int(g["referral_limit"]), referral_count(g["id"], user_id))


def _is_new_user(conn, user_id: int, g: dict) -> bool:
    """Новий для бота = запис у users з'явився не раніше старту розіграшу."""
    row = conn.execute(_sql("SELECT joined_at FROM users WHERE id = ?"), (int(user_id),)).fetchone()
    if not row or not row[0]:
        return True
    try:
        joined = _parse_dt(row[0])
        created = _parse_dt(g.get("created_at"))
    except ValueError:
        return False
    return bool(joined and created and joined >= created)


def join(giveaway_id: int, user_id: int, referrer_id: int = 0) -> dict:
    """Записати учасника. {'ok', 'number', 'already', 'error'}."""
    uid = int(user_id or 0)
    g = get_giveaway(giveaway_id)
    if uid <= 0 or not g:
        return {"ok": False, "error": "not_found"}
    existing = get_entry(g["id"], uid)
    if existing:
        return {"ok": True, "already": True, "number": int(existing["number"])}
    if not is_open(g):
        return {"ok": False, "error": "closed"}

    conn = db_connect()
    try:
        is_new = _is_new_user(conn, uid, g)
        referred_by = None
        ref = int(referrer_id or 0)
        # Другом може бути й той, хто вже відкривав бот; запрошення зараховується лише за першу участь —
        # хто вже в розіграші (в т.ч. запрошений кимось), повторно нікому не зараховується.
        if g["referral_bonus"] and ref and ref != uid:
            ok_ref = conn.execute(
                _sql("SELECT 1 FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?"), (g["id"], ref)
            ).fetchone()
            referred_by = ref if ok_ref else None
        # Номер — наступний за максимальним; при гонці UNIQUE(giveaway_id, number) змусить повторити
        for _ in range(8):
            try:
                row = conn.execute(
                    _sql("SELECT COALESCE(MAX(number), 0) + 1 FROM giveaway_entries WHERE giveaway_id = ?"), (g["id"],)
                ).fetchone()
                number = int(row[0])
                conn.execute(
                    _sql("INSERT INTO giveaway_entries (giveaway_id, user_id, number, referred_by, is_new) "
                         "VALUES (?, ?, ?, ?, ?)"),
                    (g["id"], uid, number, referred_by, 1 if is_new else 0),
                )
                conn.commit()
                return {"ok": True, "already": False, "number": number}
            except Exception:
                conn.rollback()
                existing = get_entry(g["id"], uid)
                if existing:
                    return {"ok": True, "already": True, "number": int(existing["number"])}
        logger.warning("giveaway %s: не вдалось видати номер учаснику %s", g["id"], uid)
        return {"ok": False, "error": "busy"}
    finally:
        conn.close()


# ── Жеребкування ────────────────────────────────────────────────────

def due_giveaway_ids() -> list[int]:
    conn = db_connect()
    try:
        rows = conn.execute(
            f"SELECT id FROM giveaways WHERE status = 'active' AND ends_at <= {_now_sql()} ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [int(r[0]) for r in rows]


def claim_for_draw(giveaway_id: int) -> bool:
    """Атомарно active -> drawing. True лише для одного виклику."""
    conn = db_connect()
    try:
        cur = conn.execute(
            _sql("UPDATE giveaways SET status = 'drawing' WHERE id = ? AND status = 'active'"), (int(giveaway_id),)
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def release_draw(giveaway_id: int) -> None:
    """Жеребкування впало — повертаємо в active, цикл спробує ще раз."""
    conn = db_connect()
    try:
        conn.execute(_sql("UPDATE giveaways SET status = 'active' WHERE id = ? AND status = 'drawing'"),
                     (int(giveaway_id),))
        conn.commit()
    finally:
        conn.close()


def recover_interrupted() -> int:
    """Рестарт посеред жеребкування: повертаємо такі розіграші в чергу."""
    conn = db_connect()
    try:
        cur = conn.execute("UPDATE giveaways SET status = 'active' WHERE status = 'drawing'")
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def draw_candidates(giveaway_id: int, exclude_user_ids: tuple[int, ...] = ()) -> list[dict]:
    """Учасники без заблокованих і виключених (власник). Підписку перевіряє bot.py."""
    conn = db_connect()
    try:
        cur = conn.execute(
            _sql("SELECT e.user_id, e.number, e.referred_by, e.is_new FROM giveaway_entries e "
                 "LEFT JOIN users u ON u.id = e.user_id "
                 "WHERE e.giveaway_id = ? AND COALESCE(u.blocked, 0) = 0 ORDER BY e.number"),
            (int(giveaway_id),),
        )
        rows = _rows(cur)
    finally:
        conn.close()
    excluded = {int(x) for x in exclude_user_ids if x}
    return [r for r in rows if int(r["user_id"]) not in excluded]


def compute_weights(entries: list[dict], eligible: set[int], bonus: bool, limit: int) -> dict[int, int]:
    """Шанси: 1 + min(ліміт, друзів, що теж допущені до жеребкування)."""
    weights = {int(e["user_id"]): 1 for e in entries if int(e["user_id"]) in eligible}
    if bonus:
        referrals: dict[int, int] = {}
        for e in entries:
            ref = int(e.get("referred_by") or 0)
            if ref and int(e["user_id"]) in eligible:
                referrals[ref] = referrals.get(ref, 0) + 1
        for uid in weights:
            weights[uid] += min(int(limit), referrals.get(uid, 0))
    return weights


def weighted_sample(weights: dict[int, int], k: int, rng=None) -> list[int]:
    """k різних учасників з урахуванням ваг (Efraimidis–Spirakis): ключ = ln(u) / w, беремо найбільші."""
    rng = rng or secrets.SystemRandom()
    keyed = []
    for uid, w in weights.items():
        if w <= 0:
            continue
        u = 1.0 - rng.random()  # (0, 1]
        keyed.append((math.log(u) / w, uid))
    keyed.sort(reverse=True)
    return [uid for _, uid in keyed[: max(0, int(k))]]


def save_draw(giveaway_id: int, winners: list[int], reserves: list[int], eligible_count: int) -> None:
    conn = db_connect()
    try:
        conn.execute(_sql("DELETE FROM giveaway_winners WHERE giveaway_id = ?"), (int(giveaway_id),))
        for pos, uid in enumerate(winners, 1):
            conn.execute(
                _sql("INSERT INTO giveaway_winners (giveaway_id, user_id, role, position, status) "
                     "VALUES (?, ?, 'winner', ?, 'pending')"),
                (int(giveaway_id), int(uid), pos),
            )
        for pos, uid in enumerate(reserves, 1):
            conn.execute(
                _sql("INSERT INTO giveaway_winners (giveaway_id, user_id, role, position, status) "
                     "VALUES (?, ?, 'reserve', ?, 'reserve')"),
                (int(giveaway_id), int(uid), pos),
            )
        conn.execute(
            _sql(f"UPDATE giveaways SET status = 'drawn', drawn_at = {_now_sql()}, eligible_count = ? "
                 "WHERE id = ? AND status = 'drawing'"),
            (int(eligible_count), int(giveaway_id)),
        )
        conn.commit()
    finally:
        conn.close()


def draw(giveaway_id: int, eligible_user_ids: set[int], entries: list[dict], rng=None) -> dict:
    """Провести жеребкування для вже допущених учасників і зберегти результат."""
    g = get_giveaway(giveaway_id)
    if not g:
        return {"ok": False, "error": "Не знайдено"}
    weights = compute_weights(entries, eligible_user_ids, g["referral_bonus"], g["referral_limit"])
    picked = weighted_sample(weights, g["winners_count"] + RESERVE_COUNT, rng)
    winners, reserves = picked[: g["winners_count"]], picked[g["winners_count"]:]
    save_draw(g["id"], winners, reserves, len(weights))
    return {"ok": True, "winners": winners, "reserves": reserves, "eligible": len(weights)}


# ── Переможці й приз ────────────────────────────────────────────────

# Приз забрано: є замовлення з ним, і його не скасовано. Скасування повертає приз (до кінця строку).
_CLAIMED_SQL = ("({t}.order_id IS NOT NULL AND EXISTS (SELECT 1 FROM orders o "
                "WHERE o.id = {t}.order_id AND o.status <> 'cancelled'))")
CLAIMED_W = _CLAIMED_SQL.format(t="w")

_W_COLS = ("w.id", "w.giveaway_id", "w.user_id", "w.role", "w.position", "w.status", "w.coupon_code",
           "w.claim_deadline", "w.order_id", "w.variant_id", "e.number", "u.name", "u.username")


def list_winners(giveaway_ids: list[int]) -> dict[int, list[dict]]:
    ids = [int(x) for x in giveaway_ids]
    if not ids:
        return {}
    conn = db_connect()
    try:
        cur = conn.execute(
            _sql(f"SELECT {', '.join(_W_COLS)}, CASE WHEN {CLAIMED_W} THEN 1 ELSE 0 END AS claimed "
                 "FROM giveaway_winners w "
                 "LEFT JOIN giveaway_entries e ON e.giveaway_id = w.giveaway_id AND e.user_id = w.user_id "
                 "LEFT JOIN users u ON u.id = w.user_id "
                 f"WHERE w.giveaway_id IN ({', '.join('?' for _ in ids)}) "
                 "ORDER BY w.giveaway_id, CASE w.role WHEN 'winner' THEN 0 ELSE 1 END, w.position, w.id"),
            tuple(ids),
        )
        rows = _rows(cur)
    finally:
        conn.close()
    now = datetime.now(timezone.utc)
    out: dict[int, list[dict]] = {}
    for r in rows:
        claimed = bool(r.get("claimed"))
        deadline = _parse_dt(r.get("claim_deadline")) if r.get("claim_deadline") else None
        w = {
            "id": int(r["id"]), "user_id": int(r["user_id"]), "role": r["role"], "position": int(r["position"]),
            "status": r["status"], "number": int(r.get("number") or 0),
            "name": r.get("name") or "", "username": (r.get("username") or "").replace("—", ""),
            "claim_deadline": _iso(r.get("claim_deadline")), "claimed": claimed,
            "order_id": int(r["order_id"]) if claimed else None,
            "expired": bool(r["status"] == "notified" and not claimed and deadline and deadline < now),
        }
        out.setdefault(int(r["giveaway_id"]), []).append(w)
    return out


def get_winner(winner_id: int) -> dict | None:
    conn = db_connect()
    try:
        cur = conn.execute(
            _sql("SELECT id, giveaway_id, user_id, role, position, status, coupon_code, claim_deadline, order_id, "
                 "card_url FROM giveaway_winners WHERE id = ?"),
            (int(winner_id),),
        )
        rows = _rows(cur)
    finally:
        conn.close()
    return rows[0] if rows else None


def pending_winners(giveaway_id: int) -> list[dict]:
    """Переможці, яким ще не видано приз (для публікації й після «Перекинути»)."""
    conn = db_connect()
    try:
        cur = conn.execute(
            _sql("SELECT id, user_id, position FROM giveaway_winners WHERE giveaway_id = ? AND role = 'winner' "
                 "AND status = 'pending' ORDER BY position"),
            (int(giveaway_id),),
        )
        return _rows(cur)
    finally:
        conn.close()


def claim_publish(giveaway_id: int) -> bool:
    """Атомарно drawn -> published."""
    conn = db_connect()
    try:
        cur = conn.execute(
            _sql(f"UPDATE giveaways SET status = 'published', published_at = {_now_sql()} "
                 "WHERE id = ? AND status = 'drawn'"),
            (int(giveaway_id),),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def unpublish(giveaway_id: int) -> None:
    """Пост з результатами не вийшов — повертаємо чернетку."""
    conn = db_connect()
    try:
        conn.execute(_sql("UPDATE giveaways SET status = 'drawn', published_at = NULL WHERE id = ? "
                          "AND status = 'published'"), (int(giveaway_id),))
        conn.commit()
    finally:
        conn.close()


def set_result_message_id(giveaway_id: int, message_id: int) -> None:
    conn = db_connect()
    try:
        conn.execute(_sql("UPDATE giveaways SET result_message_id = ? WHERE id = ?"),
                     (int(message_id), int(giveaway_id)))
        conn.commit()
    finally:
        conn.close()


def issue_prize(winner_id: int, hours: int = CLAIM_HOURS) -> dict:
    """Приз стає активним у магазині переможця на `hours` годин (pending -> notified)."""
    w = get_winner(winner_id)
    if not w or w["role"] != "winner" or w["status"] != "pending":
        return {"ok": False, "error": "Приз уже видано або переможця замінено"}
    deadline = datetime.now(timezone.utc) + timedelta(hours=hours)
    conn = db_connect()
    try:
        cur = conn.execute(
            _sql("UPDATE giveaway_winners SET status = 'notified', claim_deadline = ?, reminded_24 = 0, "
                 "reminded_3 = 0, expired_notified = 0 WHERE id = ? AND status = 'pending'"),
            (_db_time(deadline), int(winner_id)),
        )
        conn.commit()
        if cur.rowcount != 1:
            return {"ok": False, "error": "Приз уже видано або переможця замінено"}
    finally:
        conn.close()
    return {"ok": True, "deadline": deadline, "user_id": int(w["user_id"])}


def _is_claimed(winner_id: int) -> bool:
    conn = db_connect()
    try:
        row = conn.execute(
            _sql(f"SELECT 1 FROM giveaway_winners w WHERE w.id = ? AND {CLAIMED_W}"), (int(winner_id),)
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def extend_prize(winner_id: int, hours: int = EXTEND_HOURS) -> dict:
    """«Подовжити на 24 год»: від пізнішого з (зараз, старий дедлайн); нагадування — знову."""
    w = get_winner(winner_id)
    if not w or w["role"] != "winner" or w["status"] != "notified":
        return {"ok": False, "error": "Подовжити можна лише виданий приз"}
    if _is_claimed(winner_id):
        return {"ok": False, "error": "Переможець уже оформив приз"}
    old = _parse_dt(w.get("claim_deadline")) or datetime.now(timezone.utc)
    deadline = max(old, datetime.now(timezone.utc)) + timedelta(hours=hours)
    conn = db_connect()
    try:
        conn.execute(
            _sql("UPDATE giveaway_winners SET claim_deadline = ?, reminded_24 = 0, reminded_3 = 0, "
                 "expired_notified = 0 WHERE id = ? AND status = 'notified'"),
            (_db_time(deadline), int(winner_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "deadline": deadline, "user_id": int(w["user_id"]), "giveaway_id": int(w["giveaway_id"])}


def reroll(winner_id: int) -> dict:
    """Замінити переможця першим з резерву (якщо приз ще не оформлено)."""
    from services import coupons as coupons_svc

    w = get_winner(winner_id)
    if not w or w["role"] != "winner" or w["status"] not in ("pending", "notified"):
        return {"ok": False, "error": "Цього переможця вже не можна замінити"}
    g = get_giveaway(w["giveaway_id"])
    if not g or g["status"] not in ("drawn", "published"):
        return {"ok": False, "error": "Розіграш ще не завершено"}
    if _is_claimed(winner_id):
        return {"ok": False, "error": "Переможець уже оформив приз"}
    conn = db_connect()
    try:
        cur = conn.execute(
            _sql("SELECT id, user_id FROM giveaway_winners WHERE giveaway_id = ? AND role = 'reserve' "
                 "AND status = 'reserve' ORDER BY position LIMIT 1"),
            (g["id"],),
        )
        nxt = _rows(cur)
        if not nxt:
            return {"ok": False, "error": "Резерв закінчився"}
        cur = conn.execute(
            _sql("UPDATE giveaway_winners SET status = 'replaced' WHERE id = ? AND status IN ('pending', 'notified')"),
            (int(w["id"]),),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return {"ok": False, "error": "Цього переможця вже замінено"}
        conn.execute(
            _sql("UPDATE giveaway_winners SET role = 'winner', position = ?, status = 'pending' WHERE id = ?"),
            (int(w["position"]), int(nxt[0]["id"])),
        )
        conn.commit()
    finally:
        conn.close()
    if w.get("coupon_code"):  # призи першої версії видавались купоном
        coupons_svc.set_coupon_active(w["coupon_code"], False)
    return {"ok": True, "winner_id": int(nxt[0]["id"]), "user_id": int(nxt[0]["user_id"]),
            "published": g["status"] == "published"}


# ── Приз у магазині переможця ───────────────────────────────────────

_PRIZE_COLS = ("w.id AS winner_id", "w.giveaway_id", "w.user_id", "w.claim_deadline", "w.card_url", "w.variant_id",
               "g.title", "g.prize_type", "g.product_id", "g.product_variant", "g.prize_value",
               "g.shipping_payer", "g.channel", "g.message_id")


def _active_sql(extra: str = "", cols: tuple = ()) -> str:
    return (f"SELECT {', '.join(_PRIZE_COLS + tuple(cols))} FROM giveaway_winners w "
            "JOIN giveaways g ON g.id = w.giveaway_id "
            f"WHERE w.role = 'winner' AND w.status = 'notified' AND w.claim_deadline > {_now_sql()} "
            f"AND NOT {CLAIMED_W} {extra} ORDER BY w.claim_deadline, w.id")


def _prize_row(r: dict) -> dict:
    p = dict(r)
    for k in ("winner_id", "giveaway_id", "user_id", "product_id", "prize_value"):
        p[k] = int(p.get(k) or 0)
    p["claim_deadline"] = _iso(p.get("claim_deadline"))
    p["card_url"] = p.get("card_url") or ""
    p["product_variant"] = p.get("product_variant") or ""
    return p


def active_prizes(user_id: int) -> list[dict]:
    """Призи користувача, які ще можна забрати (видані, строк не минув, не оформлені)."""
    conn = db_connect()
    try:
        cur = conn.execute(_sql(_active_sql("AND w.user_id = ?")), (int(user_id),))
        return [_prize_row(r) for r in _rows(cur)]
    finally:
        conn.close()


def get_active_prize(user_id: int, winner_id: int) -> dict | None:
    conn = db_connect()
    try:
        cur = conn.execute(_sql(_active_sql("AND w.user_id = ? AND w.id = ?")), (int(user_id), int(winner_id)))
        rows = _rows(cur)
    finally:
        conn.close()
    return _prize_row(rows[0]) if rows else None


def claim_in_tx(conn, winner_id: int, user_id: int, order_id: int, variant_id: str = "") -> bool:
    """Прив'язати приз до замовлення в транзакції save_order. False — приз уже не активний."""
    cur = conn.execute(
        _sql("UPDATE giveaway_winners SET order_id = ?, variant_id = ? "
             "WHERE id = ? AND user_id = ? AND role = 'winner' AND status = 'notified' "
             f"AND claim_deadline > {_now_sql()} AND NOT {_CLAIMED_SQL.format(t='giveaway_winners')}"),
        (int(order_id), str(variant_id or "")[:16], int(winner_id), int(user_id)),
    )
    return cur.rowcount == 1


def set_card_url(winner_id: int, user_id: int, url: str) -> bool:
    if not CARD_URL_RE.match(str(url or "")):
        return False
    conn = db_connect()
    try:
        cur = conn.execute(_sql("UPDATE giveaway_winners SET card_url = ? WHERE id = ? AND user_id = ? "
                                "AND role = 'winner'"), (url, int(winner_id), int(user_id)))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def due_reminders() -> list[dict]:
    """Нагадування, час яких настав: [{..., 'flag': 'reminded_3', 'hours': 3}].
    Пропущене раннє нагадування не надсилається, якщо вже настав час пізнішого."""
    conn = db_connect()
    try:
        cur = conn.execute(_sql(_active_sql(cols=("w.reminded_24", "w.reminded_3"))))
        rows = _rows(cur)
    finally:
        conn.close()
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        left = (_parse_dt(r["claim_deadline"]) - now).total_seconds() / 3600
        for flag, hours in sorted(REMINDERS, key=lambda x: x[1]):
            if left <= hours:
                if not r.get(flag):
                    out.append({**_prize_row(r), "flag": flag, "hours": hours})
                break
    return out


def mark_reminded(winner_id: int, flag: str) -> bool:
    """Позначити нагадування (і всі ранніші) — атомарно, щоб не надіслати двічі."""
    hours = dict(REMINDERS)[flag]
    flags = [f for f, h in REMINDERS if h >= hours]
    conn = db_connect()
    try:
        cur = conn.execute(
            _sql(f"UPDATE giveaway_winners SET {', '.join(f + ' = 1' for f in flags)} WHERE id = ? AND {flag} = 0"),
            (int(winner_id),),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def expired_unnotified() -> list[dict]:
    """Строк минув, приз не оформлено, адміну ще не повідомлено."""
    conn = db_connect()
    try:
        cur = conn.execute(_sql(
            "SELECT w.id, w.giveaway_id, w.user_id, w.position, g.title, u.name FROM giveaway_winners w "
            "JOIN giveaways g ON g.id = w.giveaway_id LEFT JOIN users u ON u.id = w.user_id "
            f"WHERE w.role = 'winner' AND w.status = 'notified' AND w.claim_deadline <= {_now_sql()} "
            f"AND w.expired_notified = 0 AND NOT {CLAIMED_W} ORDER BY w.id"
        ))
        return _rows(cur)
    finally:
        conn.close()


def mark_expired_notified(winner_id: int) -> bool:
    conn = db_connect()
    try:
        cur = conn.execute(_sql("UPDATE giveaway_winners SET expired_notified = 1 WHERE id = ? "
                                "AND expired_notified = 0"), (int(winner_id),))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


# ── Список для адмінки ──────────────────────────────────────────────

def _stats(conn, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    ph = ", ".join("?" for _ in ids)
    stats = {gid: {"entries": 0, "new_users": 0, "referrals": 0, "buyers": 0} for gid in ids}
    rows = conn.execute(
        _sql(f"SELECT giveaway_id, COUNT(*), SUM(is_new), SUM(CASE WHEN referred_by IS NOT NULL THEN 1 ELSE 0 END) "
             f"FROM giveaway_entries WHERE giveaway_id IN ({ph}) GROUP BY giveaway_id"),
        tuple(ids),
    ).fetchall()
    for gid, n, new, refs in rows:
        stats[int(gid)].update(entries=int(n or 0), new_users=int(new or 0), referrals=int(refs or 0))
    from services.hits import SOLD_STATUSES

    sph = ", ".join("?" for _ in SOLD_STATUSES)
    try:
        rows = conn.execute(
            _sql(f"SELECT e.giveaway_id, COUNT(DISTINCT e.user_id) FROM giveaway_entries e "
                 f"JOIN orders o ON o.user_id = e.user_id AND o.ordered_at >= e.created_at "
                 f"WHERE e.giveaway_id IN ({ph}) AND o.status IN ({sph}) GROUP BY e.giveaway_id"),
            tuple(ids) + tuple(SOLD_STATUSES),
        ).fetchall()
        for gid, n in rows:
            stats[int(gid)]["buyers"] = int(n or 0)
    except Exception:
        logger.warning("giveaways: підрахунок покупців не вдався", exc_info=True)
    return stats


def list_giveaways(limit: int = 50) -> list[dict]:
    conn = db_connect()
    try:
        cur = conn.execute(_sql(f"SELECT {', '.join(_GW_COLS)} FROM giveaways ORDER BY id DESC LIMIT ?"),
                           (int(limit),))
        items = [_gw_row(r) for r in _rows(cur)]
        stats = _stats(conn, [g["id"] for g in items])
    finally:
        conn.close()
    winners = list_winners([g["id"] for g in items])
    for g in items:
        g["stats"] = stats.get(g["id"], {})
        g["winners"] = winners.get(g["id"], [])
    return items
