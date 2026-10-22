"""Публікація товару в Telegram-канал з адмінки: пост з фото і кнопками «Замовити» / «Усі товари».

Кнопки — звичайні url-кнопки на `t.me/<bot>?startapp=ch_<id>[_product_N]` (web_app-кнопки в каналах
не працюють). Мітка `ch_<id>` рахує переходи з конкретного поста (подія channel_open, value = id поста),
як `bc_<id>` у розсилок. Канал зберігається в app_settings, щоб міняти його без редеплою.
"""

from __future__ import annotations

import html
import logging
import re

import config
from db_core import db_connect, is_postgres, sql as _sql
from services import settings as settings_svc

logger = logging.getLogger(__name__)

CHANNEL_KEY = "channel_id"
DEFAULT_CHANNEL = "@poselyanov3dprint"
MAX_ALBUM_PHOTOS = 10
CAPTION_MAX = 1024
TEXT_MAX = 4096
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_CHAT_ID_RE = re.compile(r"^-100\d{5,}$")


def init_channel_posts_table(conn) -> None:
    if is_postgres():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_posts (
                id          BIGSERIAL PRIMARY KEY,
                product_id  BIGINT NOT NULL,
                chat_id     TEXT NOT NULL DEFAULT '',
                message_id  BIGINT,
                text        TEXT NOT NULL DEFAULT '',
                clicks      INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id  INTEGER NOT NULL,
                chat_id     TEXT NOT NULL DEFAULT '',
                message_id  INTEGER,
                text        TEXT NOT NULL DEFAULT '',
                clicks      INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_posts_product ON channel_posts(product_id)")


# ── Канал ───────────────────────────────────────────────────────────

def normalize_channel(raw) -> str | None:
    """`@name`, `name`, `t.me/name`, `https://t.me/name` -> `@name`; `-100…` лишається як є.
    Порожній рядок — канал не задано; None — невалідне значення."""
    value = str(raw or "").strip()
    if not value:
        return ""
    value = re.sub(r"^(https?://)?(www\.)?(t|telegram)\.me/", "", value, flags=re.I).strip("/")
    if _CHAT_ID_RE.match(value):
        return value
    value = value.lstrip("@")
    return f"@{value}" if _USERNAME_RE.match(value) else None


def get_channel() -> str:
    return settings_svc.get_setting(CHANNEL_KEY, "") or DEFAULT_CHANNEL


def set_channel(channel: str) -> None:
    settings_svc.set_setting(CHANNEL_KEY, channel)


# ── Текст і кнопки ──────────────────────────────────────────────────

def validate_text(raw, has_photo: bool) -> tuple[str, str]:
    """(текст, помилка). Ліміт — підпис до фото 1024 символи, звичайне повідомлення 4096."""
    text = str(raw or "").replace("\r\n", "\n").strip()
    if not text:
        return "", "Текст поста порожній"
    limit = CAPTION_MAX if has_photo else TEXT_MAX
    # Запас на <b></b> першого рядка: Telegram рахує ліміт уже після розбору HTML, тож теги не важать
    if len(text) > limit:
        return "", f"Задовгий текст: {len(text)} із {limit} символів"
    return text, ""


def caption_html(text: str) -> str:
    """Екранований текст, перший рядок — жирним (заголовок/рубрика)."""
    first, _, rest = text.partition("\n")
    out = f"<b>{html.escape(first)}</b>"
    if rest:
        out += "\n" + html.escape(rest)
    return out


def product_link(post_id: int, product_id: int) -> str:
    return f"https://t.me/{config.BOT_USERNAME}?startapp=ch_{int(post_id)}_product_{int(product_id)}"


def shop_link(post_id: int) -> str:
    return f"https://t.me/{config.BOT_USERNAME}?startapp=ch_{int(post_id)}"


# ── Записи постів ───────────────────────────────────────────────────

def create_post(product_id: int, chat_id: str, text: str) -> int:
    """Резервуємо id до відправки — він потрібен для мітки в посиланнях кнопок."""
    conn = db_connect()
    try:
        params = (int(product_id), str(chat_id), text)
        if is_postgres():
            row = conn.execute(
                _sql("INSERT INTO channel_posts (product_id, chat_id, text) VALUES (?, ?, ?) RETURNING id"), params
            ).fetchone()
            post_id = int(row[0])
        else:
            cur = conn.execute(_sql("INSERT INTO channel_posts (product_id, chat_id, text) VALUES (?, ?, ?)"), params)
            post_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    return post_id


def set_message_id(post_id: int, message_id: int) -> None:
    conn = db_connect()
    try:
        conn.execute(_sql("UPDATE channel_posts SET message_id = ? WHERE id = ?"), (int(message_id), int(post_id)))
        conn.commit()
    finally:
        conn.close()


def delete_post(post_id: int) -> None:
    """Прибрати резерв, якщо Telegram відмовив."""
    conn = db_connect()
    try:
        conn.execute(_sql("DELETE FROM channel_posts WHERE id = ?"), (int(post_id),))
        conn.commit()
    finally:
        conn.close()


def _iso(value) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def post_url(chat_id: str, message_id) -> str:
    """Посилання на пост: для публічного каналу t.me/name/N, для приватного t.me/c/<id>/N."""
    if not message_id:
        return ""
    chat_id = str(chat_id or "")
    if chat_id.startswith("@"):
        return f"https://t.me/{chat_id[1:]}/{int(message_id)}"
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{int(message_id)}"
    return ""


def list_product_posts(product_id: int, limit: int = 5) -> list[dict]:
    conn = db_connect()
    try:
        rows = conn.execute(
            _sql("""
                SELECT id, chat_id, message_id, clicks, created_at FROM channel_posts
                WHERE product_id = ? AND message_id IS NOT NULL
                ORDER BY id DESC LIMIT ?
            """),
            (int(product_id), int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": int(r[0]),
            "url": post_url(r[1], r[2]),
            "clicks": int(r[3] or 0),
            "created_at": _iso(r[4]),
        }
        for r in rows
    ]


# ── Переходи (start_param ch_<id>) ──────────────────────────────────

def record_click(user_id: int, post_id: int) -> bool:
    """Один перехід на клієнта на пост (подія channel_open, value = id поста)."""
    if not user_id or not post_id:
        return False
    conn = db_connect()
    try:
        if not conn.execute(
            _sql("SELECT 1 FROM channel_posts WHERE id = ? AND message_id IS NOT NULL"), (int(post_id),)
        ).fetchone():
            return False
        if conn.execute(
            _sql("SELECT 1 FROM events WHERE type = 'channel_open' AND user_id = ? AND value = ? LIMIT 1"),
            (int(user_id), int(post_id)),
        ).fetchone():
            return False
        conn.execute(
            _sql("INSERT INTO events (type, user_id, value) VALUES ('channel_open', ?, ?)"), (int(user_id), int(post_id))
        )
        conn.execute(_sql("UPDATE channel_posts SET clicks = clicks + 1 WHERE id = ?"), (int(post_id),))
        conn.commit()
    finally:
        conn.close()
    return True
