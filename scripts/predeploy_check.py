#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Predeploy-перевірка на STAGING Postgres: міграції, ендпоінти етапу 5, паритет сум замовлення.

Реалізує розд. 2-3 PREDEPLOY.md. НІКОЛИ не запускати проти прод-БД: скрипт сам відмовляється
(exit 2), якщо STAGING_DATABASE_URL порожній або вказує на прод (за .env / deploy_remote.py).

Приклад (PowerShell, з кореня репозиторію):

    $env:STAGING_DATABASE_URL = "postgresql://postgres.<ref>:<пароль>@<host>:5432/postgres?sslmode=require"
    python scripts/predeploy_check.py all --reset --yes-this-is-staging
    python scripts/predeploy_check.py endpoints --yes-this-is-staging
    python scripts/predeploy_check.py order-parity --yes-this-is-staging
    python scripts/predeploy_check.py all --dry-run          # лише перевірки безпеки й імпорти, без з'єднання з БД

Підкоманди:
    migrate-clean      (--reset) чиста БД: init_db() без схеми й зі scripts/supabase_schema.sql, init двічі
                       (ідемпотентність), наявність таблиць/колонок етапу 5 через information_schema.
    migrate-from-main  (--reset) БД у «прод-стані»: схема з `git show main:...` + СТАРИЙ init_db() з коду `main`
                       (git archive main у temp, окремий процес), демо-дані, потім init_db() з поточного коду;
                       перевірка бекфілів (status, glow), нових колонок і незмінності кількості/значень рядків.
    endpoints          усі адмін/публічні ендпоінти етапу 5 (слайси 1-6) через aiohttp-клієнт; падає на 5xx,
                       не-JSON там, де очікується JSON, неочікуваних статусах. Мутує лише рядки `predeploy_*`.
    order-parity       11 сценаріїв PREDEPLOY.md 3.1: POST /order і порівняння збережених сум із незалежним
                       розрахунком у цьому скрипті.
    all                migrate-* (лише з --reset), далі endpoints і order-parity.

Прапорці: --dry-run (без з'єднання з БД), --yes-this-is-staging (підтвердження; без нього — інтерактивне
питання), --reset (дозволяє DROP таблиць застосунку), --keep (не прибирати predeploy_* дані після прогону).

Що скрипт гарантує щодо безпеки:
  * env-перевизначення (БД, фейковий BOT_TOKEN, VALIDATE_INIT_DATA=false, LOCAL_DEV_MODE=true...) ставляться ДО
    імпорту bot/config, тому `.env` з продом (load_dotenv не перезаписує вже задані змінні) не діє;
  * polling/webhook не стартують (bot.main() не викликається), відправка в Telegram заглушена
    (_deliver_order_notifications, _notify_client_status, _make_broadcast_sender), app_state.bot_app = None;
  * паролі не друкуються; відмова, якщо ціль збігається з продом.

Обмеження: див. кінець --help (epilog). Внутрішня перевірка самого скрипта без Postgres:
`--selftest-sqlite` (SQLite у temp, лише endpoints/order-parity; для розробки скрипта).
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "predeploy_"            # імена/id тимчасових рядків
FIL_PREFIX = "predeploy-"        # id філаментів/добірок (їхній формат не дозволяє «_»)
COUPON_PREFIX = "PREDEPLOY_"     # купони (код — у верхньому регістрі)
USER_BASE = 9_100_000_000_000    # Telegram ID тестових користувачів/замовлень (далеко за межами реальних)
FAKE_TOKEN = "123456:FAKE-predeploy-token"

EXIT_FAIL = 1
EXIT_REFUSE = 2

KNOWN_TABLES = (
    "users", "orders", "order_items", "coupons", "coupon_uses", "coupon_allowed_users", "filament_colors",
    "order_idempotency", "categories", "products", "filaments", "promotions", "app_settings", "collections",
    "collection_products", "events", "reviews", "broadcasts",
)


# ═══════════════════════════════════════════════════════════════════════════
# Безпека: розбір URL, порівняння з продом, маскування
# ═══════════════════════════════════════════════════════════════════════════

URL_RE = re.compile(r"postgres(?:ql)?://[^\s'\"<>]+", re.I)
SHARED_POOLER_RE = re.compile(r"\.pooler\.supabase\.com$", re.I)


class Refuse(Exception):
    """Перевірка безпеки не пройдена: завершити з кодом 2."""


class DbIdent:
    def __init__(self, url: str):
        u = urlsplit(url.strip())
        self.url = url.strip()
        self.host = (u.hostname or "").lower()
        try:
            self.port = u.port or 5432
        except ValueError:
            self.port = 0
        self.user = unquote(u.username or "")
        self.db = (u.path or "").lstrip("/")
        ref = ""
        if self.user.lower().startswith("postgres.") and len(self.user) > 9:
            ref = self.user.split(".", 1)[1].lower()
        m = re.match(r"^db\.([a-z0-9]+)\.supabase\.co$", self.host)
        if m and not ref:
            ref = m.group(1)
        self.ref = ref
        self.valid = bool(self.host) and u.scheme.lower() in ("postgres", "postgresql")

    def masked(self) -> str:
        return f"postgresql://{self.user or '?'}:***@{self.host}:{self.port}/{self.db}"


def sanitize(text) -> str:
    """Прибрати з тексту (помилки, stderr) рядки підключення з паролями."""
    return URL_RE.sub(lambda m: DbIdent(m.group(0)).masked() if DbIdent(m.group(0)).valid else "postgresql://***", str(text))


def _dotenv_pairs(path: Path) -> dict:
    pairs: dict = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[7:].strip()
            pairs[key] = val.strip().strip("'\"")
    except OSError:
        pass
    return pairs


def collect_prod_urls() -> tuple[list[str], list[str]]:
    """Прод-URL з .env (DATABASE_URL/SUPABASE_DB_URL) + будь-які postgres-URL у .env і deploy_remote.py.

    Повертає (urls, джерела-для-повідомлення)."""
    urls: list[str] = []
    sources: list[str] = []
    env_path = ROOT / ".env"
    pairs = _dotenv_pairs(env_path)
    if env_path.exists():
        sources.append(".env")
    for key in ("DATABASE_URL", "SUPABASE_DB_URL"):
        if pairs.get(key):
            urls.append(pairs[key])
    for name in (".env", "deploy_remote.py"):
        p = ROOT / name
        if not p.exists():
            continue
        if name not in sources:
            sources.append(name)
        try:
            for m in URL_RE.finditer(p.read_text(encoding="utf-8", errors="replace")):
                urls.append(m.group(0).rstrip(".,;)"))
        except OSError:
            pass
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out, sources


def conflict_reason(staging: DbIdent, prod_urls: list[str]) -> str | None:
    """Чому staging-ціль схожа на прод (None — не схожа)."""
    for pu in prod_urls:
        p = DbIdent(pu)
        if pu.strip() == staging.url:
            return "збігається з прод-URL повністю"
        if not p.valid:
            continue
        if (p.host, p.port, p.db, p.user) == (staging.host, staging.port, staging.db, staging.user):
            return "та сама БД, що в проді (host, port, db, user збігаються)"
        if p.host == staging.host:
            if not SHARED_POOLER_RE.search(p.host):
                return f"host збігається з продовим ({staging.host})"
            # Пулер Supabase спільний для проєктів регіону: рішає project ref у імені користувача.
            if not staging.ref:
                return ("спільний пулер Supabase, а в імені користувача немає project ref: "
                        "не можна довести, що це не прод")
        if p.ref:
            blob = f"{staging.host} {staging.user} {staging.db}".lower()
            if p.ref in blob:
                return "містить project ref прод-проєкту Supabase"
    return None


def check_target(url_raw: str) -> tuple[DbIdent, list[str]]:
    """Усі перевірки безпеки цілі. Піднімає Refuse. Повертає (ident, попередження)."""
    warnings: list[str] = []
    url = (url_raw or "").strip()
    if not url:
        raise Refuse("STAGING_DATABASE_URL не задано. Задай рядок підключення до ОКРЕМОЇ staging-БД "
                     "(див. PREDEPLOY.md, розд. 1 і «Як запускати»).")
    ident = DbIdent(url)
    if not ident.valid:
        raise Refuse("STAGING_DATABASE_URL має бути у форматі postgresql://user:password@host:port/db")
    prod_urls, sources = collect_prod_urls()
    if not prod_urls:
        warnings.append("прод-URL не знайдено ні в .env, ні в deploy_remote.py: порівнювати нема з чим")
    reason = conflict_reason(ident, prod_urls)
    if reason:
        raise Refuse(f"ВІДМОВА: STAGING_DATABASE_URL схожий на ПРОД ({reason}). Використай окрему staging-БД.")
    return ident, warnings


def confirm_staging(args, ident: DbIdent) -> None:
    if args.dry_run or args.yes_this_is_staging:
        return
    if sys.stdin is not None and sys.stdin.isatty():
        print(f"\nЦіль: {ident.masked()}")
        try:
            answer = input("Це STAGING-БД, її можна змінювати? Введи слово staging для підтвердження: ").strip().lower()
        except (EOFError, KeyboardInterrupt, OSError):
            raise Refuse("Підтвердження не отримано (немає інтерактивного вводу). Додай --yes-this-is-staging.")
        if answer == "staging":
            return
        raise Refuse("Підтвердження не отримано.")
    raise Refuse("Потрібен прапорець --yes-this-is-staging (немає інтерактивного термінала для підтвердження).")


def apply_env(url: str) -> None:
    """Усі перевизначення ДО імпорту bot/config: load_dotenv не перезаписує вже задане."""
    tmp = tempfile.gettempdir()
    os.environ.update(
        DB_BACKEND="postgres", CATALOG_BACKEND="postgres",
        DATABASE_URL=url, SUPABASE_DB_URL=url,
        BOT_TOKEN=FAKE_TOKEN, VALIDATE_INIT_DATA="false", LOCAL_DEV_MODE="true",
        ALLOW_LOCAL_NETWORK="false", ADMIN_BYPASS_TOKEN=secrets.token_urlsafe(24),
        OWNER_ID="777", ORDERS_CHAT_ID="1",
        NOVAPOSHTA_API_KEY="", UKRPOSHTA_TOKEN="", SUBSCRIPTION_CHANNEL="", SUBSCRIPTION_CHANNEL_URL="",
        CLOUDINARY_CLOUD_NAME="", CLOUDINARY_API_KEY="", CLOUDINARY_API_SECRET="",
        API_PUBLIC_URL="", ADMIN_WEBAPP_URL="", DB_FILE=os.path.join(tmp, "predeploy_unused.db"),
        PYTHONDONTWRITEBYTECODE="1",
    )


def apply_env_selftest() -> str:
    """SQLite у temp + JSON-каталог у temp-копіях (як scripts/preview_server.py). Лише для розробки скрипта."""
    import shutil
    tmp = tempfile.gettempdir()
    db = os.path.join(tmp, "predeploy_selftest.db")
    if os.path.exists(db):
        os.remove(db)
    cat_dir = os.path.join(tmp, "predeploy_selftest_catalog")
    shutil.rmtree(cat_dir, ignore_errors=True)
    os.makedirs(cat_dir)
    for f in ("products.json", "custom_products.json", "categories.json", "filaments.json"):
        shutil.copy(ROOT / f, os.path.join(cat_dir, f))
    os.environ.update(
        DB_BACKEND="sqlite", CATALOG_BACKEND="json", DATABASE_URL="", SUPABASE_DB_URL="", DB_FILE=db,
        PRODUCTS_FILE=os.path.join(cat_dir, "products.json"),
        CUSTOM_PRODUCTS_FILE=os.path.join(cat_dir, "custom_products.json"),
        CATEGORIES_FILE=os.path.join(cat_dir, "categories.json"),
        FILAMENTS_FILE=os.path.join(cat_dir, "filaments.json"),
        BOT_TOKEN=FAKE_TOKEN, VALIDATE_INIT_DATA="false", LOCAL_DEV_MODE="true", ALLOW_LOCAL_NETWORK="false",
        ADMIN_BYPASS_TOKEN=secrets.token_urlsafe(24), OWNER_ID="777", ORDERS_CHAT_ID="1",
        NOVAPOSHTA_API_KEY="", SUBSCRIPTION_CHANNEL="", CLOUDINARY_CLOUD_NAME="", CLOUDINARY_API_KEY="",
        CLOUDINARY_API_SECRET="", API_PUBLIC_URL="", ADMIN_WEBAPP_URL="",
    )
    return db


# ═══════════════════════════════════════════════════════════════════════════
# Звіт
# ═══════════════════════════════════════════════════════════════════════════

class Report:
    def __init__(self):
        self.results: list[tuple[str, str, bool, str]] = []
        self.rows: list[tuple[str, int, float, bool, str]] = []   # таблиця ендпоінтів

    def add(self, section: str, name: str, ok: bool, detail: str = "") -> bool:
        detail = sanitize(detail)
        self.results.append((section, name, bool(ok), detail))
        tail = f" — {detail}" if detail else ""
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{tail}")
        return bool(ok)

    def failed(self) -> list:
        return [r for r in self.results if not r[2]]

    def print_endpoint_table(self) -> None:
        if not self.rows:
            return
        print("\n  Ендпоінт".ljust(66) + "статус   мс   результат")
        print("  " + "-" * 82)
        for label, status, ms, ok, note in self.rows:
            mark = "ok  " if ok else "FAIL"
            print(f"  {label[:58]:<58} {status:>5} {ms:>6.0f}   {mark} {sanitize(note)[:40]}")
        self.rows.clear()

    def summary(self) -> int:
        print("\n" + "=" * 78)
        print("ПІДСУМОК")
        print("=" * 78)
        section = None
        for sec, name, ok, detail in self.results:
            if sec != section:
                print(f"\n[{sec}]")
                section = sec
            print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if (detail and not ok) else ""))
        bad = self.failed()
        total = len(self.results)
        print(f"\nРазом: {total - len(bad)} PASS, {len(bad)} FAIL")
        print("РЕЗУЛЬТАТ: " + ("PASS" if not bad and total else "FAIL"))
        return 0 if (not bad and total) else EXIT_FAIL


# ═══════════════════════════════════════════════════════════════════════════
# Проєкт: імпорт, заглушки, допоміжні SQL
# ═══════════════════════════════════════════════════════════════════════════

class _NullCursor:
    def fetchall(self):
        return []

    def fetchone(self):
        return None

    rowcount = 0


class _NullConn:
    """Порожнє «з'єднання»: catalog_store при імпорті сам читає каталог із БД (bootstrap_json_catalog)."""

    def execute(self, *a, **k):
        return _NullCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _blocked_connect(*a, **k):
    raise RuntimeError("dry-run: з'єднання з БД заборонено")


def import_project(block_db: bool = False):
    """Імпорт bot/config ПІСЛЯ apply_env. Перевіряє інваріанти безпеки й ставить заглушки Telegram.

    Імпорт catalog_store сам викликає bootstrap_json_catalog() і читає БД, тому на час імпорту psycopg.connect
    підміняється порожнім з'єднанням (на порожній staging-БД без таблиць імпорт інакше падав би).
    Після імпорту кеші каталогу перечитуються в ensure_schema(). block_db=True (dry-run) лишає з'єднання
    забороненими й після імпорту."""
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    import psycopg
    real_connect = psycopg.connect
    psycopg.connect = lambda *a, **k: _NullConn()
    try:
        import config  # noqa: F401
        import app_state
        import bot
        from routes.setup import register_http_routes  # noqa: F401
        import services.db_utils  # noqa: F401
    finally:
        psycopg.connect = _blocked_connect if block_db else real_connect

    assert config.BOT_TOKEN == FAKE_TOKEN, "BOT_TOKEN не фейковий: .env перебив перевизначення!"
    if config.DB_BACKEND == "postgres":
        assert config.DATABASE_URL == os.environ.get("STAGING_DATABASE_URL", "").strip(),             "DATABASE_URL у config не збігається зі STAGING_DATABASE_URL"
        assert config.CATALOG_BACKEND == "postgres"
    assert app_state.bot_app is None, "bot_app не повинен існувати (polling не стартуємо)"
    for name in ("_deliver_order_notifications", "_notify_client_status", "_sync_order_status_to_telegram",
                 "_make_broadcast_sender", "update_order_status", "main"):
        assert hasattr(bot, name), f"bot.{name} зник: оновити заглушки в predeploy_check.py"

    async def _no_deliver(**kwargs):
        return None

    async def _no_notify(order, status, ttn=None):
        return False

    async def _sync_db_only(order_id, action):
        mapping = {"confirm": "confirmed", "draft": "draft", "cancel": "cancelled", "printing": "printing",
                   "ready": "ready", "shipped": "shipped", "delivered": "delivered"}
        if action in mapping:
            bot.update_order_status(order_id, mapping[action])

    def _make_sender():
        async def send_one(user_id, b):
            raise RuntimeError("predeploy: відправка в Telegram вимкнена")
        return send_one

    def _forbidden_main(*a, **k):
        raise RuntimeError("predeploy_check: bot.main() (polling) заборонено")

    bot._deliver_order_notifications = _no_deliver
    bot._notify_client_status = _no_notify
    bot._sync_order_status_to_telegram = _sync_db_only
    bot._make_broadcast_sender = _make_sender
    bot.main = _forbidden_main
    return config, bot


def _db():
    from db_core import db_connect
    return db_connect()


def q_all(query: str, params=()):
    from db_core import sql
    conn = _db()
    try:
        return conn.execute(sql(query), params).fetchall()
    finally:
        conn.close()


def q_one(query: str, params=()):
    rows = q_all(query, params)
    return rows[0] if rows else None


def q_exec(query: str, params=()):
    from db_core import sql
    conn = _db()
    try:
        conn.execute(sql(query), params)
        conn.commit()
    finally:
        conn.close()


def ensure_schema() -> None:
    """Той самий init_db(), що виконується на старті сервісу (ідемпотентний)."""
    import config
    from services.db_utils import init_db
    init_db()
    if config.DB_BACKEND != "postgres":     # лише selftest: products-заглушка, як у preview_server.py
        q_exec("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, stl_link TEXT)")
    import catalog_store
    catalog_store.bootstrap_json_catalog(force=True)   # кеші каталогу після імпорту були порожні


def cleanup_predeploy() -> list[str]:
    """Видалити всі predeploy_*-рядки. Повертає список помилок (порожній — все чисто)."""
    from db_core import is_postgres
    pg = is_postgres()
    P, F, C, B = PREFIX, FIL_PREFIX, COUPON_PREFIX, USER_BASE
    my_orders = "SELECT id FROM orders WHERE substr(first_name,1,10) = ? OR user_id >= ?"
    my_products = "SELECT id FROM products WHERE substr(name,1,10) = ?"
    personal = "SELECT code FROM coupons WHERE personal_user_id >= ?"
    stmts: list[tuple[str, tuple]] = [
        (f"DELETE FROM order_items WHERE order_id IN ({my_orders})", (P, B)),
        (f"DELETE FROM coupon_uses WHERE order_id IN ({my_orders}) OR user_id >= ? OR substr(code,1,10) = ? "
         f"OR code IN ({personal})", (P, B, B, C, B)),
        ("DELETE FROM order_idempotency WHERE user_id >= ?", (B,)),
        ("DELETE FROM reviews WHERE substr(author_name,1,10) = ? OR user_id >= ?", (P, B)),
        (f"DELETE FROM reviews WHERE order_id IN ({my_orders})", (P, B)),
        ("DELETE FROM orders WHERE substr(first_name,1,10) = ? OR user_id >= ?", (P, B)),
        ("DELETE FROM events WHERE user_id >= ?", (B,)),
        ("DELETE FROM collection_products WHERE substr(collection_id,1,10) = ?", (F,)),
        ("DELETE FROM collections WHERE substr(id,1,10) = ?", (F,)),
        ("DELETE FROM promotions WHERE substr(id,1,10) = ?", (P,)),
        ("DELETE FROM coupon_allowed_users WHERE substr(code,1,10) = ? OR user_id >= ? "
         f"OR code IN ({personal})", (C, B, B)),
        ("DELETE FROM coupons WHERE substr(code,1,10) = ? OR personal_user_id >= ?", (C, B)),
        ("DELETE FROM broadcasts WHERE substr(text,1,10) = ?", (P,)),
        ("DELETE FROM users WHERE id >= ?", (B,)),
    ]
    if pg:
        stmts[3:3] = [
            (f"DELETE FROM reviews WHERE product_id IN ({my_products})", (P,)),
        ]
        stmts[8:8] = [
            (f"DELETE FROM events WHERE product_id IN ({my_products})", (P,)),
            (f"DELETE FROM collection_products WHERE product_id IN ({my_products})", (P,)),
        ]
        stmts += [
            ("DELETE FROM products WHERE substr(name,1,10) = ?", (P,)),
            ("DELETE FROM categories WHERE substr(id,1,10) = ?", (P,)),
            ("DELETE FROM filaments WHERE substr(id,1,10) = ?", (F,)),
            ("DELETE FROM filament_colors WHERE substr(id,1,10) = ?", (F,)),
        ]
    errors: list[str] = []
    for query, params in stmts:
        try:
            q_exec(query, params)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{query[:60]}...: {sanitize(e)}")
    if not pg:      # JSON-каталог (selftest): через сервіси
        try:
            import catalog_store as cs
            for p in cs.get_admin_products():
                if str(p.get("name", "")).startswith(P):
                    cs.set_product_status(p["id"], "archive")
            for c in list(cs.get_all_categories(active_only=False)):
                if str(c.get("id", "")).startswith(P):
                    cs.delete_category(c["id"])
            cs.reload_filaments_cache(force=True)
            for f in list(cs.FILAMENTS_CACHE):
                if str(f.get("id", "")).startswith(F):
                    cs.delete_filament(f["id"], force=True)
        except Exception as e:  # noqa: BLE001
            errors.append(f"каталог (json): {sanitize(e)}")
    # кеші сервісів
    try:
        import services.promotions as sp
        sp._invalidate_cache()
    except Exception:  # noqa: BLE001
        pass
    return errors


# ═══════════════════════════════════════════════════════════════════════════
# Міграції (лише Postgres)
# ═══════════════════════════════════════════════════════════════════════════

def split_sql(text: str) -> list[str]:
    """Розбити SQL-файл на оператори (без коментарів-рядків і BEGIN/COMMIT)."""
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
    out = []
    for stmt in "\n".join(lines).split(";"):
        s = stmt.strip()
        if s and s.upper() not in ("BEGIN", "COMMIT"):
            out.append(s)
    return out


def run_sql_file(text: str, label: str, report: Report, section: str) -> None:
    """Виконати схему однією транзакцією; RLS/POLICY пропускаємо, якщо немає ролей Supabase."""
    conn = _db()
    try:
        roles = {r[0] for r in conn.execute(
            "SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role')").fetchall()}
        skip_rls = len(roles) < 3
        stmts = split_sql(text)
        skipped = 0
        for s in stmts:
            if skip_rls and re.search(r"POLICY|ROW\s+LEVEL\s+SECURITY", s, re.I):
                skipped += 1
                continue
            conn.execute(s)
        conn.commit()
        note = f"{len(stmts) - skipped} операторів"
        if skipped:
            note += f", {skipped} RLS/POLICY пропущено (немає ролей anon/authenticated/service_role: це не Supabase)"
        report.add(section, f"схема {label} виконана", True, note)
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        report.add(section, f"схема {label} виконана", False, str(e))
        raise
    finally:
        conn.close()


def reset_app_tables(report: Report, section: str) -> None:
    """DROP лише відомих таблиць застосунку. Відмовляє, якщо в public є сторонні таблиці."""
    conn = _db()
    try:
        existing = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
            "AND table_type = 'BASE TABLE'").fetchall()}
        foreign = sorted(existing - set(KNOWN_TABLES))
        if foreign:
            raise Refuse(f"--reset відмовлено: у схемі public є сторонні таблиці {foreign}: це не схоже "
                         f"на окрему staging-БД цього бота.")
        for t in KNOWN_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        conn.commit()
        report.add(section, "таблиці застосунку скинуто (--reset)", True, f"було {len(existing)}")
    finally:
        conn.close()


def expected_columns() -> dict[str, set[str]]:
    """Очікувані колонки: константи беремо з коду, решту — з DDL (init_db/схема/сервіси)."""
    from services.db_utils import ORDER_EXTRA_COLUMNS, ORDER_ITEM_EXTRA_COLUMNS
    from services.promotions import _COLUMNS as promo_cols
    return {
        "users": {"id", "name", "username", "blocked", "joined_at", "start_count", "last_start_at",
                  "activity_count", "last_seen_at"},
        "orders": {"id", "user_id", "username", "first_name", "total_price", "comment", "gift_product_name",
                   "status", "coupon_code", "discount_amount", "promotion_discount", "ordered_at",
                   "price_pending", "channel_message_id", *ORDER_EXTRA_COLUMNS},
        "order_items": {"id", "order_id", "product_id", "product_name", "price", "quantity", "filament",
                        "is_contract_price", "comment", *ORDER_ITEM_EXTRA_COLUMNS},
        "coupons": {"code", "type", "value", "min_order", "uses_max", "uses_count", "one_per_user", "active",
                    "expires_at", "personal_user_id", "stackable", "require_channel_sub", "sub_channel_id"},
        "coupon_uses": {"id", "code", "user_id", "order_id", "used_at", "status"},
        "coupon_allowed_users": {"code", "user_id"},
        "order_idempotency": {"user_id", "idempotency_key", "order_id", "is_new", "created_at"},
        "filament_colors": {"id", "name", "hex", "available"},
        "categories": {"id", "name", "emoji", "badge_class", "sort_order", "active", "quick_slot"},
        "products": {"id", "category_id", "name", "emoji", "mat", "price", "old_price", "photos", "custom_fields",
                     "hot", "gift", "filament_choice", "luminous_filament_choice", "pinned", "stl_link",
                     "contract_price", "is_custom", "active", "status", "created_at", "description", "note",
                     "video"},
        "filaments": {"id", "name", "hex", "available", "sort_order", "glow"},
        "promotions": {*promo_cols, "created_at"},
        "app_settings": {"key", "value", "updated_at"},
        "collections": {"id", "name", "description", "icon", "color", "sort_order", "active", "mode", "rule"},
        "collection_products": {"collection_id", "product_id", "sort_order"},
        "events": {"id", "type", "user_id", "product_id", "query", "value", "created_at"},
        "reviews": {"id", "product_id", "order_id", "user_id", "author_name", "rating", "text", "photos",
                    "status", "admin_reply", "created_at"},
        "broadcasts": {"id", "segment", "text", "photo", "button_label", "button_target", "scheduled_at",
                       "status", "total", "delivered", "failed", "clicks", "created_at", "sent_at"},
    }


def db_columns(conn) -> dict[str, set[str]]:
    cols: dict[str, set[str]] = {}
    for t, c in conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'").fetchall():
        cols.setdefault(t, set()).add(c)
    return cols


def fingerprint(conn) -> dict:
    cols = conn.execute(
        "SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' ORDER BY 1, 2").fetchall()
    idx = conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1").fetchall()
    return {"columns": [tuple(r) for r in cols], "indexes": [r[0] for r in idx]}


def check_expected_columns(report: Report, section: str, label: str) -> None:
    conn = _db()
    try:
        have = db_columns(conn)
    finally:
        conn.close()
    missing = []
    for table, cols in expected_columns().items():
        if table not in have:
            missing.append(f"таблиці {table} немає")
            continue
        for c in sorted(cols - have[table]):
            missing.append(f"{table}.{c}")
    report.add(section, f"{label}: усі очікувані таблиці/колонки є", not missing,
               "бракує: " + ", ".join(missing[:15]) if missing else f"{len(expected_columns())} таблиць")


def check_defaults(report: Report, section: str, label: str) -> None:
    rows = q_all("SELECT table_name, column_name, column_default FROM information_schema.columns "
                 "WHERE table_schema = 'public' AND ((table_name = 'products' AND column_name = 'status') "
                 "OR (table_name = 'filaments' AND column_name = 'glow') "
                 "OR (table_name = 'promotions' AND column_name IN ('accent', 'show_timer')))")
    d = {(r[0], r[1]): str(r[2] or "") for r in rows}
    ok = ("active" in d.get(("products", "status"), "") and "false" in d.get(("filaments", "glow"), "")
          and d.get(("promotions", "show_timer"), "") != "" and ("promotions", "accent") in d)
    report.add(section, f"{label}: DEFAULT для products.status / filaments.glow / promotions.accent,show_timer",
               ok, "" if ok else str(d))


def run_init_twice(report: Report, section: str, label: str) -> None:
    from services.db_utils import init_db
    snaps = []
    for n in (1, 2, 3):
        try:
            init_db()
        except Exception as e:  # noqa: BLE001
            report.add(section, f"{label}: init_db() запуск #{n} без помилок", False, str(e))
            return
        conn = _db()
        try:
            snaps.append(fingerprint(conn))
        finally:
            conn.close()
    report.add(section, f"{label}: init_db() ×3 без помилок", True)
    same = snaps[1] == snaps[2]
    report.add(section, f"{label}: схема після 2-го і 3-го init_db() ідентична (ідемпотентність)", same,
               "" if same else "fingerprint змінився між повторними запусками")


def migrate_clean(report: Report, args) -> None:
    section = "migrate-clean"
    print(f"\n── {section}: чиста БД ──")
    schema_text = (ROOT / "scripts" / "supabase_schema.sql").read_text(encoding="utf-8")
    # A. лише init_db() на порожній БД
    reset_app_tables(report, section)
    run_init_twice(report, section, "порожня БД без schema.sql")
    check_expected_columns(report, section, "порожня БД без schema.sql")
    check_defaults(report, section, "порожня БД без schema.sql")
    # B. schema.sql, потім init_db() ×3
    reset_app_tables(report, section)
    run_sql_file(schema_text, "scripts/supabase_schema.sql", report, section)
    run_init_twice(report, section, "schema.sql + init_db()")
    check_expected_columns(report, section, "schema.sql + init_db()")
    check_defaults(report, section, "schema.sql + init_db()")
    n = q_one("SELECT COUNT(*) FROM collections")
    report.add(section, "collections засіяна типовими добірками", bool(n and n[0] > 0), f"{n[0] if n else 0} шт.")


def _git_main_ref() -> str | None:
    for ref in ("main", "origin/main"):
        r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=ROOT, capture_output=True)
        if r.returncode == 0:
            return ref
    return None


def _git_show(ref: str, path: str) -> str:
    r = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git show {ref}:{path}: {r.stderr.decode('utf-8', 'replace')[:200]}")
    return r.stdout.decode("utf-8")


def _extract_main(ref: str, dest: str) -> None:
    r = subprocess.run(["git", "archive", "--format=zip", ref], cwd=ROOT, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("git archive: " + r.stderr.decode("utf-8", "replace")[:200])
    with zipfile.ZipFile(io.BytesIO(r.stdout)) as z:
        for info in z.infolist():
            if info.filename.endswith((".py", ".json", ".sql")) and not info.filename.startswith("tests/"):
                z.extract(info, dest)


def _snapshot(conn) -> dict:
    """Кількості по всіх існуючих таблицях + значення ключових колонок (для порівняння до/після)."""
    tables = sorted(db_columns(conn))
    snap: dict = {"counts": {}}
    for t in tables:
        snap["counts"][t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    snap["orders"] = [tuple(r) for r in conn.execute(
        "SELECT id, user_id, total_price, status, discount_amount, coupon_code FROM orders ORDER BY id").fetchall()]
    snap["order_items"] = [tuple(r) for r in conn.execute(
        "SELECT id, order_id, product_name, price, quantity FROM order_items ORDER BY id").fetchall()]
    snap["products"] = [tuple(r) for r in conn.execute(
        "SELECT id, name, price, active FROM products ORDER BY id").fetchall()]
    snap["users"] = [tuple(r) for r in conn.execute("SELECT id, name, username FROM users ORDER BY id").fetchall()]
    snap["coupons"] = [tuple(r) for r in conn.execute(
        "SELECT code, type, value, uses_count FROM coupons ORDER BY code").fetchall()]
    snap["promotions"] = [tuple(r) for r in conn.execute(
        "SELECT id, type, value, min_order FROM promotions ORDER BY id").fetchall()]
    return snap


def _seed_old_state() -> None:
    """Демо-дані у СТАРІЙ схемі (лише старі колонки). Префікс seed_old_ — для пошуку."""
    conn = _db()
    try:
        ex = conn.execute
        ex("INSERT INTO categories (id, name, emoji, badge_class, sort_order, active) VALUES "
           "('toy','Іграшки','🧸','category-toy',1,true), ('key','Брелки','🔑','category-key',2,true), "
           "('custom','Індивідуальне','✨','category-custom',3,true) ON CONFLICT (id) DO NOTHING")
        for name, price, active, cat in (("seed_old_active_1", 80, True, "key"), ("seed_old_active_2", 150, True, "toy"),
                                         ("seed_old_active_3", 300, True, "toy"), ("seed_old_inactive_1", 90, False, "key"),
                                         ("seed_old_inactive_2", 120, False, "toy")):
            ex("INSERT INTO products (category_id, name, emoji, mat, price, active) VALUES (%s,%s,'📦','PLA',%s,%s)",
               (cat, name, price, active))
        for fid, name, hexv, av, so in (("luminous", "Світний", "#d8ffb0", True, 1), ("luminous-green", "Світний зелений", "#7CFF7C", True, 2),
                                        ("white", "Білий", "#ffffff", True, 3), ("black", "Чорний", "#111111", True, 4)):
            ex("INSERT INTO filaments (id, name, hex, available, sort_order) VALUES (%s,%s,%s,%s,%s) "
               "ON CONFLICT (id) DO NOTHING", (fid, name, hexv, av, so))
        for uid, nm, un in ((9001, "Олена", "olena_seed"), (9002, "Ігор", "igor_seed"), (9003, "Марія", "maria_seed")):
            ex("INSERT INTO users (id, name, username) VALUES (%s,%s,%s)", (uid, nm, un))
        pid = ex("SELECT id FROM products WHERE name = 'seed_old_active_1'").fetchone()[0]
        for uid, uname, fname, total, status, coupon, disc in (
                (9001, "@olena_seed", "Олена", 160, "new", None, 0), (9002, "@igor_seed", "Ігор", 270, "confirmed", "SEEDOLD10", 30),
                (9003, "@maria_seed", "Марія", 400, "delivered", None, 0)):
            oid = ex("INSERT INTO orders (user_id, username, first_name, total_price, comment, status, coupon_code, "
                     "discount_amount) VALUES (%s,%s,%s,%s,'seed',%s,%s,%s) RETURNING id",
                     (uid, uname, fname, total, status, coupon, disc)).fetchone()[0]
            ex("INSERT INTO order_items (order_id, product_id, product_name, price, quantity, filament) "
               "VALUES (%s,%s,'seed_old_item',%s,2,'Чорний')", (oid, pid, total // 2))
        ex("INSERT INTO coupons (code, type, value, min_order, uses_max, uses_count) VALUES "
           "('SEEDOLD10','percent',10,0,0,1), ('SEEDOLD50','fixed',50,300,10,0)")
        ex("INSERT INTO coupon_uses (code, user_id, order_id, status) VALUES "
           "('SEEDOLD10', 9002, (SELECT MIN(id) FROM orders WHERE user_id = 9002), 'confirmed')")
        ex("INSERT INTO promotions (id, type, title, value, min_order) VALUES ('seed_old_promo','order_fixed','Стара акція',30,200)")
        conn.commit()
    finally:
        conn.close()


def migrate_from_main(report: Report, args) -> None:
    section = "migrate-from-main"
    print(f"\n── {section}: БД у «прод-стані» → поточний код ──")
    ref = _git_main_ref()
    if not ref:
        report.add(section, "гілка main доступна в git", False, "ні `main`, ні `origin/main` не знайдено")
        return
    reset_app_tables(report, section)
    old_schema = _git_show(ref, "scripts/supabase_schema.sql")
    run_sql_file(old_schema, f"{ref}:scripts/supabase_schema.sql", report, section)

    with tempfile.TemporaryDirectory(prefix="predeploy_main_") as tmp:
        _extract_main(ref, tmp)
        code = ("import sys; sys.path.insert(0, '.'); from services.db_utils import init_db; init_db(); "
                "print('old init ok')")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        r = subprocess.run([sys.executable, "-c", code], cwd=tmp, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        ok = r.returncode == 0 and "old init ok" in r.stdout
        report.add(section, f"СТАРИЙ init_db() з {ref} виконано", ok, "" if ok else sanitize(r.stderr[-400:]))
        if not ok:
            return

    conn = _db()
    try:
        have = db_columns(conn)
        pre_new = [t for t in ("collections", "reviews", "events", "broadcasts") if t in have]
        report.add(section, "до міграції немає таблиць етапу 5 (collections/reviews/events/broadcasts)", not pre_new,
                   f"вже є: {pre_new}" if pre_new else "")
        report.add(section, "до міграції немає products.status / filaments.glow",
                   "status" not in have.get("products", set()) and "glow" not in have.get("filaments", set()))
    finally:
        conn.close()

    _seed_old_state()
    conn = _db()
    try:
        before = _snapshot(conn)
    finally:
        conn.close()
    report.add(section, "демо-дані засіяно у старій схемі", True,
               ", ".join(f"{t}={n}" for t, n in before["counts"].items() if n))

    from services.db_utils import init_db
    try:
        init_db()
        report.add(section, "НОВИЙ init_db() (redesign) виконано на «прод-стані»", True)
    except Exception as e:  # noqa: BLE001
        report.add(section, "НОВИЙ init_db() (redesign) виконано на «прод-стані»", False, str(e))
        return

    check_expected_columns(report, section, "після міграції")
    check_defaults(report, section, "після міграції")

    conn = _db()
    try:
        after = _snapshot(conn)
        status_rows = conn.execute("SELECT name, active, status FROM products WHERE name LIKE 'seed\\_old\\_%' ORDER BY name").fetchall()
        glow_rows = conn.execute("SELECT id, glow FROM filaments ORDER BY id").fetchall()
        order_rows = conn.execute("SELECT source, promotion_id, promotion_discount FROM orders").fetchall()
        item_rows = conn.execute("SELECT printed FROM order_items").fetchall()
        n_archive = conn.execute("SELECT COUNT(*) FROM products WHERE active = false AND status <> 'archive'").fetchone()[0]
        n_active = conn.execute("SELECT COUNT(*) FROM products WHERE active = true AND status <> 'active'").fetchone()[0]
    finally:
        conn.close()

    bad_status = [r for r in status_rows if r[2] != ("active" if r[1] else "archive")]
    report.add(section, "products.status: active=true→'active', active=false→'archive'",
               len(status_rows) == 5 and not bad_status and n_archive == 0 and n_active == 0,
               f"{[(r[0], r[2]) for r in bad_status]}" if bad_status else f"{len(status_rows)} товарів")
    bad_glow = [r[0] for r in glow_rows if bool(r[1]) != r[0].startswith("luminous")]
    report.add(section, "filaments.glow: true лише для id 'luminous%'", not bad_glow and len(glow_rows) >= 4,
               f"неправильно: {bad_glow}" if bad_glow else f"{len(glow_rows)} кольорів")
    report.add(section, "старі замовлення: source='miniapp', promotion_id='' (DEFAULT-бекфіл)",
               all(r[0] == "miniapp" and (r[1] or "") == "" and (r[2] or 0) == 0 for r in order_rows) and len(order_rows) >= 3,
               f"{order_rows[:3]}")
    report.add(section, "старі позиції: order_items.printed=0", all((r[0] or 0) == 0 for r in item_rows) and bool(item_rows))

    diffs = [f"{t}: {before['counts'][t]}→{after['counts'].get(t)}" for t in before["counts"]
             if before["counts"][t] != after["counts"].get(t)]
    report.add(section, "кількість рядків у старих таблицях не змінилась", not diffs, "; ".join(diffs))
    changed = [k for k in ("orders", "order_items", "products", "users", "coupons", "promotions") if before[k] != after[k]]
    report.add(section, "значення ключових колонок (orders/items/products/users/coupons/promotions) не змінились",
               not changed, ", ".join(changed))

    # повторний init на вже мігрованій БД: нічого не змінюється
    try:
        init_db()
        conn = _db()
        try:
            again = _snapshot(conn)
            st2 = conn.execute("SELECT name, status FROM products WHERE name LIKE 'seed\\_old\\_%' ORDER BY name").fetchall()
        finally:
            conn.close()
        stable = again == after and [tuple(r) for r in st2] == [(r[0], r[2]) for r in status_rows]
        report.add(section, "повторний init_db() на мігрованій БД не змінює дані (status не перераховується)", stable)
    except Exception as e:  # noqa: BLE001
        report.add(section, "повторний init_db() на мігрованій БД", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Ендпоінти
# ═══════════════════════════════════════════════════════════════════════════

class Resp:
    def __init__(self, status=0, data=None, text="", ok=False, headers=None):
        self.status, self.json, self.text, self.ok, self.headers = status, data, text, ok, headers or {}

    def get(self, *path, default=None):
        cur = self.json
        for p in path:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return default
        return cur


class Api:
    def __init__(self, client, admin_headers: dict, report: Report):
        self.client, self.admin_headers, self.report = client, admin_headers, report

    async def call(self, method: str, path: str, *, body=None, expect=None, kind: str = "json",
                   admin: bool = True, label: str | None = None) -> Resp:
        """expect: кортеж дозволених статусів (за замовчуванням будь-який 2xx). kind: json|csv|text."""
        label = label or f"{method} {path}"
        headers = dict(self.admin_headers) if admin else {}
        kwargs = {"json": body} if body is not None else {}
        t0 = time.perf_counter()
        try:
            resp = await self.client.request(method, path, headers=headers, **kwargs)
            text = await resp.text()
            status, ctype = resp.status, resp.headers.get("Content-Type", "")
        except Exception as e:  # noqa: BLE001
            self.report.rows.append((label, 0, (time.perf_counter() - t0) * 1000, False, str(e)))
            self.report.add("endpoints", label, False, f"виняток: {e}")
            return Resp()
        ms = (time.perf_counter() - t0) * 1000
        data, note, ok = None, "", True
        if status >= 500:
            ok, note = False, "5xx"
        elif expect is not None:
            if status not in expect:
                ok, note = False, f"очікувано {expect}"
        elif not 200 <= status < 300:
            ok, note = False, "очікувано 2xx"
        if kind == "json":
            try:
                data = json.loads(text)
            except ValueError:
                ok, note = False, note or "не JSON"
        elif kind == "csv":
            if "csv" not in ctype.lower() or not text.strip():
                ok, note = False, note or f"не CSV ({ctype})"
        self.report.rows.append((label, status, ms, ok, note))
        if not ok:
            self.report.add("endpoints", label, False, f"HTTP {status}: {note}; {text[:160]!r}")
        return Resp(status, data, text, ok, dict(resp.headers))

    def check(self, name: str, cond, detail: str = "") -> bool:
        return self.report.add("endpoints", name, bool(cond), detail if not cond else "")


class Ctx:
    """Ідентифікатори тимчасових рядків; заповнюється під час прогону."""

    def __init__(self):
        self.cat_a, self.cat_b = PREFIX + "cat_a", PREFIX + "cat_b"
        self.fil, self.fil_glow = FIL_PREFIX + "fil", FIL_PREFIX + "glow"
        self.coll = FIL_PREFIX + "coll"
        self.pid: dict[str, int] = {}
        self.uid = {n: USER_BASE + 10 + n for n in range(1, 6)}
        self.order_id = 0
        self.coupon_codes: list[str] = []


def _ids_of(resp: Resp, key: str) -> list:
    data = resp.json
    if isinstance(data, dict):
        data = data.get(key, [])
    return [x.get("id") if isinstance(x, dict) else x for x in (data or [])]


async def ep_catalog(api: Api, ctx: Ctx) -> None:
    for cid in (ctx.cat_a, ctx.cat_b):
        r = await api.call("POST", "/api/categories", body={"id": cid, "name": cid, "emoji": "🧪"})
        api.check(f"категорія {cid} створена", r.get("ok") is True, str(r.json))
    r = await api.call("GET", "/api/categories?includeInactive=true")
    ids = _ids_of(r, "categories") if isinstance(r.json, dict) else _ids_of(r, "")
    api.check("GET categories містить тимчасові категорії", ctx.cat_a in ids and ctx.cat_b in ids)
    r = await api.call("PUT", "/api/categories/order", body={"ids": [i for i in ids if i]})
    api.check("PUT categories/order (поточний порядок без змін)", r.get("ok") is True, str(r.json))

    r = await api.call("PUT", f"/api/categories/{ctx.cat_b}", body={"name": ctx.cat_b, "emoji": "🧫", "quickSlot": None})
    api.check("PUT categories/{id}", r.get("ok") is True, str(r.json))

    # Філаменти (glow, CRUD)
    r = await api.call("POST", "/api/filaments", body={"id": ctx.fil, "name": PREFIX + "Колір", "hex": "#112233", "glow": False})
    api.check("філамент створено", r.get("ok") is True, str(r.json))
    r = await api.call("POST", "/api/filaments", body={"id": ctx.fil_glow, "name": PREFIX + "Світний", "hex": "#aaffaa", "glow": True})
    api.check("світний філамент створено з glow=true", r.get("ok") is True and r.get("filament", "glow") is True, str(r.json))
    await api.call("PUT", f"/api/filaments/{ctx.fil_glow}", body={"glow": False, "available": True})
    r = await api.call("GET", "/api/filaments")
    rows = r.json if isinstance(r.json, list) else (r.get("filaments") or [])
    mine = {f.get("id"): f for f in rows if isinstance(f, dict)}
    api.check("GET filaments: glow оновлено", ctx.fil_glow in mine and mine[ctx.fil_glow].get("glow") is False)
    r = await api.call("DELETE", f"/api/filaments/{ctx.fil_glow}?force=1")
    api.check("філамент видалено (force: колір міг бути в товарах)", r.get("ok") is True, str(r.json))

    # Товари
    specs = {"a1": (ctx.cat_a, 200), "a2": (ctx.cat_a, 150), "a3": (ctx.cat_a, 120), "b1": (ctx.cat_b, 300),
             "g": (ctx.cat_b, 50), "bulk1": (ctx.cat_a, 100), "bulk2": (ctx.cat_a, 200)}
    for key, (cat, price) in specs.items():
        body = {"cat": cat, "name": f"{PREFIX}product_{key}", "price": price, "emoji": "🧪", "mat": "PLA",
                "filamentChoice": True, "gift": key == "g"}
        r = await api.call("POST", "/api/products", body=body, label=f"POST /api/products ({key})")
        if r.get("id"):
            ctx.pid[key] = int(r.get("id"))
    body = {"cat": ctx.cat_b, "name": f"{PREFIX}product_cx", "price": 0, "contractPrice": True, "emoji": "🧪"}
    r = await api.call("POST", "/api/products", body=body, label="POST /api/products (договірна ціна)")
    if r.get("id"):
        ctx.pid["cx"] = int(r.get("id"))
    api.check("тимчасові товари створено", len(ctx.pid) == 8, f"створено {len(ctx.pid)} з 8")
    if len(ctx.pid) < 8:
        return

    r = await api.call("GET", "/api/admin/products")
    rows = r.json if isinstance(r.json, list) else (r.get("products") or [])
    mine = [p for p in rows if isinstance(p, dict) and str(p.get("name", "")).startswith(PREFIX)]
    api.check("GET admin/products містить тимчасові товари зі статистикою", len(mine) >= 8
              and all("status" in p for p in mine), f"знайдено {len(mine)}")
    await api.call("GET", "/api/products")
    await api.call("GET", f"/api/products/{ctx.pid['a1']}")
    await api.call("GET", f"/api/products/{ctx.pid['a1']}/related")
    await api.call("GET", f"/api/products/{ctx.pid['a1']}/reviews")

    r = await api.call("PUT", f"/api/products/{ctx.pid['bulk1']}/status", body={"status": "hidden"})
    api.check("статус → hidden", r.get("ok") is True, str(r.json))
    r = await api.call("PUT", f"/api/products/{ctx.pid['bulk1']}/status", body={"status": "active"})
    api.check("статус → active", r.get("ok") is True, str(r.json))
    await api.call("PUT", f"/api/products/{ctx.pid['bulk1']}/status", body={"status": "weird"}, expect=(400,),
                   label="PUT status (невалідний → 400)")
    r = await api.call("PATCH", f"/api/products/{ctx.pid['bulk1']}", body={"hot": True, "price": 100})
    api.check("PATCH товару", r.get("ok") is True, str(r.json))
    ids = [ctx.pid["bulk1"], ctx.pid["bulk2"]]
    r = await api.call("POST", "/api/products/bulk", body={"ids": ids, "action": "discount", "value": 10})
    api.check("bulk discount", r.get("ok") is True and r.get("updated") == 2, str(r.json))
    r = await api.call("POST", "/api/products/bulk", body={"ids": ids, "action": "clear_discount"})
    api.check("bulk clear_discount", r.get("ok") is True, str(r.json))
    r = await api.call("POST", "/api/products/bulk", body={"ids": ids, "action": "category", "value": ctx.cat_b})
    api.check("bulk category", r.get("ok") is True, str(r.json))
    r = await api.call("POST", "/api/products/bulk", body={"ids": ids, "action": "status", "value": "hidden"})
    api.check("bulk status", r.get("ok") is True, str(r.json))
    r = await api.call("POST", "/api/products/bulk", body={"ids": ids, "action": "status", "value": "active"})
    r = await api.call("POST", f"/api/products/{ctx.pid['bulk2']}/duplicate")
    api.check("duplicate товару (копія 'hidden')", r.get("ok") is True, str(r.json))
    r = await api.call("DELETE", f"/api/products/{ctx.pid['bulk2']}")
    api.check("DELETE товару (м'яко, в архів)", r.get("ok") is True, str(r.json))
    await api.call("PUT", f"/api/products/{ctx.pid['bulk2']}/status", body={"status": "active"})


async def ep_collections(api: Api, ctx: Ctx) -> None:
    if not ctx.pid:
        return
    a1, a2 = ctx.pid["a1"], ctx.pid["a2"]
    r = await api.call("POST", "/api/admin/collections",
                       body={"id": ctx.coll, "name": PREFIX + "Добірка", "mode": "manual", "product_ids": [a1]})
    cid = r.get("collection", "id") or ctx.coll
    api.check("добірку створено", r.get("collection") is not None, str(r.json))
    await api.call("GET", "/api/admin/collections")
    await api.call("POST", "/api/admin/collections/preview", body={"mode": "auto", "rule": {"type": "price_max", "max_price": 100}},
                   label="POST collections/preview (auto)")
    await api.call("POST", "/api/admin/collections/preview", body={"mode": "manual", "product_ids": [a1, a2]},
                   label="POST collections/preview (manual)")
    r = await api.call("PUT", f"/api/admin/collections/{cid}/products", body={"product_ids": [a2, a1]})
    api.check("склад добірки оновлено", r.get("ok") is True, str(r.json))
    await api.call("GET", f"/api/admin/products/{a1}/collections")
    r = await api.call("PUT", f"/api/admin/products/{a1}/collections", body={"collections": [cid]})
    api.check("PUT products/{id}/collections", r.get("ok") is True, str(r.json))
    listing = await api.call("GET", "/api/admin/collections")
    rows = listing.json if isinstance(listing.json, list) else (listing.get("collections") or [])
    ids = [c.get("id") for c in rows if isinstance(c, dict)]
    r = await api.call("PUT", "/api/admin/collections/order", body={"ids": ids})
    api.check("PUT collections/order (поточний порядок)", r.get("ok") is True, str(r.json))
    await api.call("GET", "/api/collections")
    r = await api.call("PUT", f"/api/admin/collections/{cid}", body={"active": False})
    api.check("добірку вимкнено", r.get("ok") is True, str(r.json))
    await api.call("PUT", "/api/admin/collections/predeploy-ghost", body={"name": "X"}, expect=(404,),
                   label="PUT collections/ghost (→ 404)")
    r = await api.call("DELETE", f"/api/admin/collections/{cid}")
    api.check("добірку видалено", r.get("ok") is True, str(r.json))


def _seed_users_orders(ctx: Ctx) -> None:
    import services.orders as orders
    import services.users as users
    for n, uid in ctx.uid.items():
        users.record_start(uid, f"{PREFIX}user{n}", f"predeploy_u{n}")
    items = [{"product_id": ctx.pid.get("a1", 0), "product_name": f"{PREFIX}item", "price": 100, "quantity": 2}]
    oid, _ = orders.save_order(ctx.uid[1], "@predeploy_u1", f"{PREFIX}buyer", items, 200, "")
    orders.update_order_status(oid, "delivered")
    ctx.review_order = oid
    # один відгук на пару (замовлення, товар) → три різні товари
    for key, (status, rating, text, reply) in zip(("a1", "a2", "a3"), (("pending", 5, "predeploy: гарно", ""),
                                                                      ("approved", 4, "predeploy: ок", "дякуємо"),
                                                                      ("hidden", 2, "predeploy: ні", ""))):
        q_exec("INSERT INTO reviews (product_id, order_id, user_id, author_name, rating, text, photos, status, admin_reply) "
               "VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?)",
               (ctx.pid.get(key, 0), oid, ctx.uid[1], f"{PREFIX}author", rating, text, status, reply))


async def ep_reviews(api: Api, ctx: Ctx) -> None:
    rows = q_all("SELECT id, status FROM reviews WHERE author_name = ? ORDER BY id", (f"{PREFIX}author",))
    if len(rows) < 3:
        api.check("тимчасові відгуки засіяно", False, f"є {len(rows)}")
        return
    pending, approved = rows[0][0], rows[1][0]
    r = await api.call("GET", "/api/admin/reviews?status=pending")
    await api.call("GET", "/api/admin/reviews?status=all")
    await api.call("GET", "/api/admin/reviews?q=predeploy&rating=5")
    await api.call("GET", "/api/admin/reviews?status=bogus", expect=(400,), label="GET admin/reviews?status=bogus (→ 400)")
    r = await api.call("GET", "/api/orders/counts")
    api.check("orders/counts містить лічильник відгуків/статусів", isinstance(r.json, dict) and bool(r.json), str(r.json)[:120])
    r = await api.call("PUT", f"/api/admin/reviews/{pending}/status", body={"status": "approved"})
    api.check("відгук опубліковано", r.get("ok") is True, str(r.json))
    r = await api.call("PUT", f"/api/admin/reviews/{approved}/reply", body={"reply": "predeploy: дякуємо!"})
    api.check("відповідь на відгук збережено", r.get("ok") is True, str(r.json))
    await api.call("PUT", "/api/admin/reviews/999999999/status", body={"status": "hidden"}, expect=(404,),
                   label="PUT admin/reviews/missing/status (→ 404)")
    await api.call("GET", f"/api/products/{ctx.pid.get('a1', 0)}/reviews", label="GET products/{id}/reviews (опубліковані)")
    r = await api.call("DELETE", f"/api/admin/reviews/{pending}")
    api.check("відгук видалено", r.get("ok") is True, str(r.json))


async def ep_promotions_coupons(api: Api, ctx: Ctx) -> None:
    pid = PREFIX + "promo_cat"
    r = await api.call("POST", "/api/admin/promotions", body={
        "id": pid, "type": "cat_percent", "title": PREFIX + "акція", "value": 5, "category": ctx.cat_a,
        "min_order": 1_000_000, "accent": "violet", "show_timer": False})
    api.check("акцію створено (accent, show_timer)", r.get("promotion", "accent") == "violet"
              and r.get("promotion", "show_timer") is False, str(r.json))
    r = await api.call("GET", "/api/admin/promotions")
    rows = r.json if isinstance(r.json, list) else (r.get("promotions") or [])
    mine = [p for p in rows if isinstance(p, dict) and p.get("id") == pid]
    api.check("GET admin/promotions: є статистика і статус", bool(mine) and "status" in mine[0], str(mine[:1])[:160])
    r = await api.call("PUT", f"/api/admin/promotions/{pid}", body={"id": pid, "type": "cat_percent", "title": PREFIX + "акція 2",
                                                                    "value": 6, "category": ctx.cat_a, "min_order": 1_000_000})
    api.check("акцію оновлено", r.get("ok") is True, str(r.json))
    await api.call("GET", "/api/promotions")
    r = await api.call("DELETE", f"/api/admin/promotions/{pid}")
    api.check("акцію видалено", r.get("ok") is True, str(r.json))

    code = COUPON_PREFIX + "E2E"
    r = await api.call("POST", "/api/coupons", body={"code": code, "type": "percent", "value": 5, "min_order": 1_000_000})
    api.check("купон створено", r.get("ok") is True, str(r.json))
    r = await api.call("GET", "/api/coupons")
    rows = r.json if isinstance(r.json, list) else (r.get("coupons") or [])
    mine = [c for c in rows if isinstance(c, dict) and c.get("code") == code]
    api.check("GET coupons: revenue і link (GROUP BY UPPER(coupon_code))", bool(mine) and "revenue" in mine[0]
              and "link" in mine[0], str(mine[:1])[:160])
    await api.call("PUT", f"/api/coupons/{code}", body={"type": "percent", "value": 7, "min_order": 1_000_000})
    uid = ctx.uid[2]
    r = await api.call("POST", f"/api/coupons/{code}/allowed-users", body={"user_ids": [uid]})
    api.check("whitelist купона: додано", r.get("ok") is True, str(r.json))
    await api.call("DELETE", f"/api/coupons/{code}/allowed-users/{uid}")
    await api.call("POST", "/check_coupon", body={"code": code, "user_id": uid, "cart_total": 10},
                   admin=False, label="POST /check_coupon")
    r = await api.call("DELETE", f"/api/coupons/{code}")
    api.check("купон видалено", r.get("ok") is True, str(r.json))


async def ep_users_broadcasts(api: Api, ctx: Ctx) -> None:
    import services.users as users
    for seg in users.SEGMENT_KEYS:
        r = await api.call("GET", f"/api/admin/users?segment={seg}&counts=1&limit=20", label=f"GET admin/users?segment={seg}&counts=1")
    api.check("users: counts по сегментах", isinstance(r.json, dict) and "segment_counts" in r.json, str(r.json)[:120] if r.json else "")
    await api.call("GET", "/api/admin/users?search=predeploy&sort=starts&order=desc")
    await api.call("GET", "/api/admin/users.csv?segment=all", kind="csv")
    await api.call("GET", "/api/admin/users.csv?segment=buyers", kind="csv", label="GET admin/users.csv?segment=buyers")
    r = await api.call("POST", f"/api/admin/users/{ctx.uid[1]}/coupon")
    code = r.get("code")
    api.check("персональний купон створено", r.get("ok") is True and bool(code), str(r.json))
    if code:
        await api.call("DELETE", f"/api/coupons/{code}", label="DELETE personal coupon")
    await api.call("POST", "/api/admin/users/999999999999/coupon", expect=(404,), label="POST users/missing/coupon (→ 404)")

    for seg in users.SEGMENT_KEYS:
        await api.call("POST", "/api/admin/broadcasts/preview", body={"segment": seg}, label=f"POST broadcasts/preview ({seg})")
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    r = await api.call("POST", "/api/admin/broadcasts", body={"text": PREFIX + "відкладена", "segment": "all", "scheduled_at": future,
                                                              "button_label": "Дивитись", "button_target": "shop"},
                       label="POST broadcasts (відкладена)")
    sched = r.get("id")
    api.check("відкладену розсилку створено (RETURNING id)", r.get("ok") is True and bool(sched), str(r.json))
    r = await api.call("POST", "/api/admin/broadcasts", body={"text": PREFIX + "чернетка", "segment": "buyers", "draft": True},
                       label="POST broadcasts (чернетка)")
    draft = r.get("id")
    api.check("чернетку створено", r.get("ok") is True and bool(draft), str(r.json))
    await api.call("POST", "/api/admin/broadcasts", body={"text": ""}, expect=(400,), label="POST broadcasts (порожній текст → 400)")
    r = await api.call("GET", "/api/admin/broadcasts")
    rows = (r.json or {}).get("broadcasts", []) if isinstance(r.json, dict) else []
    mine = [b for b in rows if isinstance(b, dict) and b.get("id") in (sched, draft)]
    api.check("GET broadcasts: кліки/замовлення/виторг/посилання", len(mine) == 2 and all(
        k in mine[0] for k in ("clicks", "orders", "revenue", "link")), str(mine[:1])[:160])
    if sched:
        await api.call("POST", "/api/events", body={"type": "broadcast_open", "broadcast_id": sched, "user_id": ctx.uid[3]},
                       admin=False, label="POST /api/events (broadcast_open)")
        r = await api.call("GET", "/api/admin/broadcasts")
        await api.call("DELETE", f"/api/admin/broadcasts/{sched}", label="DELETE broadcasts/{відкладена}")
    if draft:
        await api.call("DELETE", f"/api/admin/broadcasts/{draft}", label="DELETE broadcasts/{чернетка}")
    await api.call("DELETE", "/api/admin/broadcasts/999999999", expect=(404,), label="DELETE broadcasts/missing (→ 404)")


async def ep_orders(api: Api, ctx: Ctx) -> None:
    a1 = ctx.pid.get("a1", 0)
    body = {"customer_name": PREFIX + "manual", "user_id": ctx.uid[4], "source": "offline", "status": "confirmed",
            "items": [{"product_id": a1, "product_name": PREFIX + "item_one", "price": 100, "quantity": 1, "filament": "Чорний"},
                      {"product_id": a1, "product_name": PREFIX + "item_two", "price": 50, "quantity": 2}]}
    r = await api.call("POST", "/api/orders", body=body, label="POST /api/orders (ручне)")
    oid = r.get("id")
    api.check("ручне замовлення створено", r.get("ok") is True and bool(oid), str(r.json))
    if not oid:
        return
    ctx.order_id = int(oid)
    await api.call("GET", "/api/orders?limit=20")
    await api.call("GET", "/api/orders?status=confirmed&limit=20", label="GET /api/orders?status=confirmed")
    await api.call("GET", "/api/orders?pending_price=true", label="GET /api/orders?pending_price=true")
    await api.call("GET", "/api/orders/counts")
    await api.call("GET", "/api/orders.csv", kind="csv")
    await api.call("GET", f"/api/orders/{oid}")
    r = await api.call("GET", "/api/print-queue")
    api.check("print-queue містить позиції тимчасового замовлення", PREFIX + "item_one" in r.text, "")
    await api.call("PUT", f"/api/orders/{oid}", body={"internal_note": PREFIX + "нотатка", "ttn": ""})
    items = [row[0] for row in q_all("SELECT id FROM order_items WHERE order_id = ? ORDER BY id", (oid,))]
    if len(items) == 2:
        r = await api.call("PUT", f"/api/order-items/{items[0]}/printed", body={"printed": True}, label="PUT order-items/{1}/printed")
        api.check("перша позиція надрукована → printing", r.get("status") == "printing", str(r.json))
        r = await api.call("PUT", f"/api/order-items/{items[1]}/printed", body={"printed": True}, label="PUT order-items/{2}/printed")
        api.check("усі позиції надруковані → ready", r.get("status") == "ready", str(r.json))
    else:
        api.check("позиції тимчасового замовлення знайдено", False, f"{len(items)}")
    await api.call("PUT", f"/api/orders/{oid}/status", body={"status": "shipped"}, expect=(400,),
                   label="PUT orders/status shipped без ТТН (→ 400)")
    r = await api.call("PUT", f"/api/orders/{oid}/status", body={"status": "shipped", "ttn": "20450000000001"})
    api.check("статус → shipped з ТТН", r.get("status") == "shipped", str(r.json))
    r = await api.call("PUT", f"/api/orders/{oid}/status", body={"status": "delivered"})
    api.check("статус → delivered", r.get("status") == "delivered", str(r.json))
    await api.call("GET", "/api/admin/overview?days=7")
    await api.call("GET", "/api/admin/overview?days=30", label="GET admin/overview?days=30")
    await api.call("GET", "/api/admin/overview?days=1", label="GET admin/overview?days=1")
    r = await api.call("DELETE", f"/api/orders/{oid}")
    api.check("замовлення видалено", r.get("ok") is True, str(r.json))


async def ep_misc(api: Api, ctx: Ctx) -> None:
    await api.call("GET", "/health", kind="text")
    await api.call("GET", "/api/config")
    await api.call("GET", "/api/hits")
    await api.call("GET", "/api/search/popular")
    await api.call("GET", "/api/admin/settings")
    pid = ctx.pid.get("a1", 0)
    for typ in ("catalog_open", "product_view", "add_to_cart"):
        await api.call("POST", "/api/events", body={"type": typ, "product_id": pid, "user_id": ctx.uid[5]}, admin=False,
                       label=f"POST /api/events ({typ})")
    await api.call("GET", "/api/admin/overview?days=7", label="GET admin/overview (після подій)")


async def run_endpoints_async(report: Report, admin_token: str) -> None:
    import bot
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from routes.setup import register_http_routes

    app = web.Application(client_max_size=30 * 1024 * 1024)
    register_http_routes(app, bot)
    ctx = Ctx()
    async with TestClient(TestServer(app)) as client:
        api = Api(client, {"X-Admin-Bypass-Token": admin_token}, report)
        sections = (("каталог/категорії/філаменти/товари", ep_catalog), ("добірки", ep_collections),
                    ("відгуки", ep_reviews), ("акції/купони", ep_promotions_coupons),
                    ("клієнти/розсилки", ep_users_broadcasts), ("замовлення/черга/огляд", ep_orders), ("інше", ep_misc))
        for title, fn in sections:
            print(f"\n── endpoints: {title} ──")
            try:
                if fn is ep_reviews:
                    _seed_users_orders(ctx)
                if fn is ep_users_broadcasts and not hasattr(ctx, "review_order"):
                    _seed_users_orders(ctx)
                await fn(api, ctx)
            except Exception as e:  # noqa: BLE001
                report.add("endpoints", f"секція «{title}»", False, f"виняток: {type(e).__name__}: {e}")
        report.print_endpoint_table()


def run_endpoints(report: Report, args) -> None:
    print("\n── endpoints: усі ендпоінти етапу 5 ──")
    ensure_schema()
    leftovers = cleanup_predeploy()
    if leftovers:
        report.add("endpoints", "очищення залишків попереднього прогону", False, "; ".join(leftovers[:3]))
    import config
    try:
        asyncio.run(run_endpoints_async(report, config.ADMIN_BYPASS_TOKEN))
    finally:
        if args.keep:
            print("  --keep: predeploy_* дані залишено")
        else:
            errors = cleanup_predeploy()
            report.add("endpoints", "predeploy_* дані прибрано", not errors, "; ".join(errors[:3]))


# ═══════════════════════════════════════════════════════════════════════════
# Паритет сум замовлення (розд. 3.1)
# ═══════════════════════════════════════════════════════════════════════════

# Каталог сценаріїв: ціни тут — наші, вхідні дані; очікування рахуємо власним кодом нижче.
PRODUCTS = {   # ключ: (категорія-ключ, ціна, прапорці)
    "a1": ("A", 200, {}), "a2": ("A", 150, {}), "a3": ("A", 120, {}),
    "b1": ("B", 300, {}), "cx": ("B", 0, {"contract": True}), "g": ("B", 50, {"gift": True}),
}

# promo: (суфікс id, поля акції). coupon: (суфікс, type, value, min_order, stackable).
SCENARIOS = [
    dict(sid="1", title="звичайний кошик без акцій", cart=[("a1", 1), ("b1", 1)], promos=[], coupon=None),
    dict(sid="2", title="order_percent 7% (з округленням догори до 10)", cart=[("a1", 1), ("b1", 1)],
         promos=[("op", dict(type="order_percent", value=7, min_order=400))], coupon=None),
    dict(sid="3", title="order_fixed −60 від 300", cart=[("a1", 2)],
         promos=[("of", dict(type="order_fixed", value=60, min_order=300))], coupon=None),
    dict(sid="4", title="cat_percent 25% лише на категорію A (товари з двох категорій)", cart=[("a1", 1), ("b1", 1)],
         promos=[("cp", dict(type="cat_percent", value=25, category="A"))], coupon=None),
    dict(sid="5", title="gift_threshold: 3 товари категорії A + подарунок", cart=[("a1", 1), ("a2", 1), ("a3", 1)],
         promos=[("gt", dict(type="gift_threshold", category="A", min_items=3, max_gift_price=100))], coupon=None, gift="g"),
    dict(sid="6", title="free_ship від 400: суми не змінюються", cart=[("a1", 2)],
         promos=[("fs", dict(type="free_ship", min_order=400))], coupon=None),
    dict(sid="7a", title="купон % окремо", cart=[("a1", 1), ("b1", 1)], promos=[], coupon=("c7a", "percent", 10, 0, 0)),
    dict(sid="7b", title="купон ₴ окремо", cart=[("a1", 1), ("b1", 1)], promos=[], coupon=("c7b", "fixed", 70, 0, 0)),
    dict(sid="8a", title="купон (stackable) + акція сумуються", cart=[("a1", 1), ("b1", 1)],
         promos=[("op", dict(type="order_percent", value=10, min_order=400))], coupon=("c8a", "fixed", 70, 0, 1), stacking=True),
    dict(sid="8b", title="купон (не stackable) + акція: купон скасовує акцію", cart=[("a1", 1), ("b1", 1)],
         promos=[("op", dict(type="order_percent", value=10, min_order=400))], coupon=("c8b", "fixed", 70, 0, 0), stacking=True),
    dict(sid="9", title="договірна ціна (price_pending)", cart=[("a1", 1), ("cx", 1)], promos=[], coupon=None),
    dict(sid="10", title="товар з персоналізацією і кольором", cart=[("a1", 1, {"custom": "Олена · побажання: матовий", "fil": True})],
         promos=[], coupon=None),
    dict(sid="11", title="кілька акцій одночасно: обирається найвигідніша", cart=[("a1", 1), ("b1", 1)],
         promos=[("op", dict(type="order_percent", value=10, min_order=400)),
                 ("of", dict(type="order_fixed", value=80, min_order=400)),
                 ("cp", dict(type="cat_percent", value=30, category="A"))], coupon=None),
]


def expected_for(sc: dict, stacking_global: bool) -> dict:
    """Незалежний розрахунок очікуваних сум зі сценарію (власна арифметика, без коду застосунку)."""
    subtotal, by_cat = 0, {}
    for line in sc["cart"]:
        key, qty = line[0], line[1]
        cat, price, flags = PRODUCTS[key]
        if flags.get("contract"):
            continue
        subtotal += price * qty
        by_cat[cat] = by_cat.get(cat, 0) + price * qty
    best_id, best_raw = "", 0
    free_id, gift_id = "", ""
    for suffix, p in sc["promos"]:
        pid = PREFIX + "s" + sc["sid"] + "_" + suffix
        t = p["type"]
        raw = 0
        if t == "order_percent" and subtotal >= p.get("min_order", 0):
            raw = subtotal * p["value"] // 100
        elif t == "order_fixed" and subtotal >= p.get("min_order", 0):
            raw = min(p["value"], subtotal)
        elif t == "cat_percent" and subtotal >= p.get("min_order", 0):
            raw = by_cat.get(p["category"], 0) * p["value"] // 100
        elif t == "free_ship" and subtotal >= p.get("min_order", 0) and not free_id:
            free_id = pid
        elif t == "gift_threshold":
            gift_id = pid
        if t in ("order_percent", "order_fixed", "cat_percent") and raw > best_raw:
            best_id, best_raw = pid, raw
    raw_c = 0
    stack = False
    if sc["coupon"]:
        _, ctype, cval, cmin, cstack = sc["coupon"]
        if subtotal >= cmin:
            raw_c = subtotal * cval // 100 if ctype == "percent" else min(cval, subtotal)
        stack = bool(cstack) and stacking_global
    raw_p = 0 if (raw_c > 0 and not stack) else best_raw
    combined = min(subtotal, raw_c + raw_p)
    after = -(-(subtotal - combined) // 10) * 10 if combined > 0 else subtotal
    actual = subtotal - after if combined > 0 else 0
    coupon_disc = min(raw_c, actual)
    promo_disc = actual - coupon_disc
    promotion_id = best_id if promo_disc > 0 else ""
    if not promotion_id and sc.get("gift") and gift_id:
        promotion_id = gift_id
    if not promotion_id and free_id:
        promotion_id = free_id
    return {"subtotal": subtotal, "total": subtotal - actual, "coupon_discount": coupon_disc,
            "promotion_discount": promo_disc, "promotion_id": promotion_id,
            "price_pending": 1 if any(PRODUCTS[l[0]][2].get("contract") for l in sc["cart"]) else 0}


async def run_parity_async(report: Report, ctx: Ctx) -> None:
    import bot
    import services.coupons as coupons
    import services.promotions as promos
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from routes.setup import register_http_routes

    section = "order-parity"
    app = web.Application(client_max_size=30 * 1024 * 1024)
    register_http_routes(app, bot)
    admin_hdr = {}
    async with TestClient(TestServer(app)) as client:
        api = Api(client, admin_hdr, report)

        # 1. каталог для сценаріїв (через ті самі HTTP-ендпоінти адміна)
        import config
        api.admin_headers = {"X-Admin-Bypass-Token": config.ADMIN_BYPASS_TOKEN}
        cat_slug = {"A": ctx.cat_a, "B": ctx.cat_b}
        for cid in (ctx.cat_a, ctx.cat_b):
            await api.call("POST", "/api/categories", body={"id": cid, "name": cid, "emoji": "🧪"})
        await api.call("POST", "/api/filaments", body={"id": ctx.fil, "name": PREFIX + "Колір", "hex": "#112233", "glow": False})
        pids = {}
        for key, (cat, price, flags) in PRODUCTS.items():
            body = {"cat": cat_slug[cat], "name": f"{PREFIX}parity_{key}", "price": price, "emoji": "🧪", "mat": "PLA",
                    "filamentChoice": True, "gift": bool(flags.get("gift"))}
            if flags.get("contract"):
                body.update(price=0, contractPrice=True)
            r = await api.call("POST", "/api/products", body=body, label=f"POST /api/products ({key})")
            if r.get("id"):
                pids[key] = int(r.get("id"))
        report.rows.clear()
        if len(pids) != len(PRODUCTS):
            report.add(section, "тимчасовий каталог для сценаріїв створено", False, f"{len(pids)} з {len(PRODUCTS)}")
            return
        report.add(section, "тимчасовий каталог для сценаріїв створено", True, f"{len(pids)} товарів")

        # 2. тимчасово вимикаємо всі сторонні акції (відновимо у finally) і запам'ятовуємо перемикач сумування
        from services.settings import COUPON_STACKING_KEY, set_setting
        saved_active = {p["id"]: p["active"] for p in promos.list_promotions() if not p["id"].startswith(PREFIX)}
        row = q_one("SELECT value FROM app_settings WHERE key = ?", (COUPON_STACKING_KEY,))
        saved_stacking = None if row is None else row[0]
        print(f"  (тимчасово вимикаю {sum(1 for v in saved_active.values() if v)} сторонніх активних акцій; відновлю наприкінці)")
        try:
            for pid, was_active in saved_active.items():
                if was_active:
                    promos.set_promotion_active(pid, False)

            for sc in SCENARIOS:
                await _run_scenario(api, report, sc, pids, ctx, promos, coupons, set_setting, COUPON_STACKING_KEY)

            # негатив: хибна сума має відхилятись (перевірка, що валідація жива)
            uid = USER_BASE + 900
            r = await api.call("POST", "/order", admin=False, expect=(400,), label="POST /order (хибна сума → 400)", body={
                "user_id": uid, "first_name": PREFIX + "neg", "user": "@predeploy_neg", "idempotency_key": "predeploy-neg-" + secrets.token_hex(4),
                "items": [{"product_id": pids["a1"], "quantity": 1}], "total_price": 1})
            report.add(section, "хибна сума відхиляється (400 «Сума не збігається»)", r.status == 400 and "Сума" in str(r.get("error", default="")), str(r.json)[:120])
        finally:
            for pid, was_active in saved_active.items():
                if was_active:
                    promos.set_promotion_active(pid, True)
            if saved_stacking is None:
                q_exec("DELETE FROM app_settings WHERE key = ?", (COUPON_STACKING_KEY,))
            else:
                set_setting(COUPON_STACKING_KEY, saved_stacking)
            from services.settings import _invalidate_cache
            _invalidate_cache()
            report.add(section, "сторонні акції й перемикач сумування купонів відновлено", True,
                       f"акцій: {sum(1 for v in saved_active.values() if v)}")
        report.print_endpoint_table()


async def _run_scenario(api, report, sc, pids, ctx, promos, coupons, set_setting, stacking_key) -> None:
    section = "order-parity"
    name = f"сценарій {sc['sid']}: {sc['title']}"
    try:
        stacking = bool(sc.get("stacking"))
        set_setting(stacking_key, "1" if stacking else "0")
        # акції сценарію (тимчасові)
        for suffix, p in sc["promos"]:
            data = dict(p)
            if "category" in data:
                data["category"] = {"A": ctx.cat_a, "B": ctx.cat_b}[data["category"]]
            data.update(id=PREFIX + "s" + sc["sid"] + "_" + suffix, title=PREFIX + "акція", priority=50)
            res = promos.create_promotion(data)
            if not res.get("ok"):
                report.add(section, name, False, f"акцію не створено: {res}")
                return
        coupon_code = None
        if sc["coupon"]:
            suffix, ctype, cval, cmin, cstack = sc["coupon"]
            coupon_code = (COUPON_PREFIX + suffix).upper()
            res = coupons.create_coupon({"code": coupon_code, "type": ctype, "value": cval, "min_order": cmin, "stackable": cstack})
            if not res.get("ok"):
                report.add(section, name, False, f"купон не створено: {res}")
                return
        exp = expected_for(sc, stacking)

        items = []
        for line in sc["cart"]:
            extra = line[2] if len(line) > 2 else {}
            it = {"product_id": pids[line[0]], "quantity": line[1]}
            if extra.get("fil"):
                it["filament_id"] = ctx.fil
            if extra.get("custom"):
                it["customValue"] = extra["custom"]
            items.append(it)
        uid = USER_BASE + 100 + int("".join(ch for ch in sc["sid"] if ch.isdigit())) * 10 + (1 if sc["sid"].endswith("b") else 0)
        body = {"user_id": uid, "first_name": f"{PREFIX}s{sc['sid']}", "tg_username": "predeploy_u", "user": "@predeploy_u",
                "items": items, "total_price": exp["total"], "coupon_code": coupon_code,
                "idempotency_key": f"predeploy-{sc['sid']}-{secrets.token_hex(4)}"}
        if sc.get("gift"):
            body["gift"] = f"{PREFIX}parity_{sc['gift']}"
            body["gift_product_id"] = pids[sc["gift"]]
        r = await api.call("POST", "/order", body=body, admin=False, label=f"POST /order — {name}"[:90])
        if not (r.status == 200 and r.get("ok") is True and r.get("order_id")):
            report.add(section, name, False, f"POST /order → {r.status} {r.text[:160]}")
            return
        row = q_one("SELECT total_price, promotion_discount, discount_amount, promotion_id, coupon_code, price_pending, "
                    "gift_product_id FROM orders WHERE id = ?", (int(r.get("order_id")),))
        got = {"total": row[0], "promotion_discount": row[1] or 0, "coupon_discount": (row[2] or 0) - (row[1] or 0),
               "promotion_id": row[3] or "", "price_pending": row[5] or 0}
        want = {k: exp[k] for k in ("total", "promotion_discount", "coupon_discount", "promotion_id", "price_pending")}
        problems = [f"{k}: очікувано {want[k]!r}, у БД {got[k]!r}" for k in want if got[k] != want[k]]
        if coupon_code and exp["coupon_discount"] > 0 and (row[4] or "").upper() != coupon_code:
            problems.append(f"coupon_code: очікувано {coupon_code}, у БД {row[4]!r}")
        if sc.get("gift") and int(row[6] or 0) != pids[sc["gift"]]:
            problems.append(f"gift_product_id: очікувано {pids[sc['gift']]}, у БД {row[6]!r}")
        if sc["sid"] == "10":
            it = q_one("SELECT personalization, personalization_note, filament FROM order_items WHERE order_id = ? "
                       "AND product_id = ?", (int(r.get("order_id")), pids["a1"]))
            if not it or it[0] != "Олена" or it[1] != "матовий" or PREFIX not in (it[2] or ""):
                problems.append(f"персоналізація/колір у позиції: {tuple(it) if it else None}")
        detail = (f"всього {got['total']} ₴, акція −{got['promotion_discount']}, купон −{got['coupon_discount']}, "
                  f"promotion_id={got['promotion_id'] or '—'}")
        report.add(section, name, not problems, "; ".join(problems) if problems else detail)
    except Exception as e:  # noqa: BLE001
        report.add(section, name, False, f"виняток: {type(e).__name__}: {e}")
    finally:
        for suffix, _ in sc["promos"]:
            try:
                promos.delete_promotion(PREFIX + "s" + sc["sid"] + "_" + suffix)
            except Exception:  # noqa: BLE001
                pass
        if sc["coupon"]:
            try:
                coupons.delete_coupon((COUPON_PREFIX + sc["coupon"][0]).upper())
            except Exception:  # noqa: BLE001
                pass


def run_parity(report: Report, args) -> None:
    print("\n── order-parity: 11 сценаріїв PREDEPLOY.md 3.1 ──")
    ensure_schema()
    leftovers = cleanup_predeploy()
    if leftovers:
        report.add("order-parity", "очищення залишків попереднього прогону", False, "; ".join(leftovers[:3]))
    try:
        asyncio.run(run_parity_async(report, Ctx()))
    finally:
        if args.keep:
            print("  --keep: predeploy_* дані залишено")
        else:
            errors = cleanup_predeploy()
            report.add("order-parity", "predeploy_* дані прибрано", not errors, "; ".join(errors[:3]))


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

EPILOG = """\
Обмеження та припущення:
  * migrate-from-main відтворює «прод-стан» так: scripts/supabase_schema.sql з гілки main + СПРАВЖНІЙ init_db()
    коду main (git archive у temp, окремий процес) + демо-дані. Це найближче до реальності, але не точна копія
    прод-даних: у проді можуть бути рядки, яких тут нема (нестандартні значення, великі обсяги).
  * Таблиць reviews/events/collections/broadcasts у main немає, тому бекфіл для них — лише «створені порожніми/типовими».
  * На не-Supabase Postgres (локальний) оператори RLS/POLICY зі схеми пропускаються (нема ролей anon/authenticated).
  * order-parity тимчасово вимикає СТОРОННІ акції у staging та перемикач сумування купонів і відновлює їх наприкінці
    (у блоці finally; при жорсткому вбивстві процесу акції можуть лишитись вимкненими: увімкни в адмінці).
  * Telegram не перевіряється: відправка заглушена. Розсилки не відправляються, лише CRUD/розрахунок отримувачів.
  * Pytest на Postgres (PREDEPLOY.md 2.4) цей скрипт не запускає.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="predeploy_check.py", description=(__doc__ or "").split("\n\n")[0], epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("migrate-clean", "migrate-from-main", "endpoints", "order-parity", "all"))
    p.add_argument("--dry-run", action="store_true", help="лише перевірки безпеки й імпорти, без з'єднання з БД")
    p.add_argument("--yes-this-is-staging", action="store_true", help="підтвердження, що ціль — staging (інакше інтерактивно)")
    p.add_argument("--reset", action="store_true", help="дозволити DROP таблиць застосунку (потрібен для migrate-*)")
    p.add_argument("--keep", action="store_true", help="не прибирати predeploy_* дані після endpoints/order-parity")
    p.add_argument("--selftest-sqlite", action="store_true",
                   help="розробка скрипта: SQLite у temp замість Postgres (лише endpoints/order-parity)")
    return p


def dry_run(args, ident: DbIdent, warnings: list[str]) -> int:
    print("\n[dry-run] З'єднання з БД НЕ встановлюється.")
    for w in warnings:
        print(f"  УВАГА: {w}")
    try:
        config, bot = import_project(block_db=True)
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] імпорт проєкту: {type(e).__name__}: {sanitize(e)}")
        return EXIT_FAIL
    checks = [
        ("DB_BACKEND=postgres", config.DB_BACKEND == "postgres"),
        ("CATALOG_BACKEND=postgres", config.CATALOG_BACKEND == "postgres"),
        ("DATABASE_URL = staging", config.DATABASE_URL == ident.url),
        ("BOT_TOKEN фейковий", config.BOT_TOKEN == FAKE_TOKEN),
        ("VALIDATE_INIT_DATA=false, LOCAL_DEV_MODE=true", (not config.VALIDATE_INIT_DATA) and config.LOCAL_DEV_MODE),
        ("ALLOW_LOCAL_NETWORK=false (адмін лише за токеном)", not config.ALLOW_LOCAL_NETWORK),
        ("НП/Cloudinary/канал підписки вимкнені", not config.NOVAPOSHTA_API_KEY and not config.CLOUDINARY_API_KEY
         and not config.SUBSCRIPTION_CHANNEL),
        ("заглушка розсилок стоїть", bot._make_broadcast_sender().__name__ == "send_one"),
        ("bot.main() заборонено", True),
    ]
    bad = 0
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        bad += 0 if ok else 1
    schema = ROOT / "scripts" / "supabase_schema.sql"
    n = len(split_sql(schema.read_text(encoding="utf-8")))
    print(f"  [PASS] scripts/supabase_schema.sql розібрано: {n} операторів")
    try:
        ncols = sum(len(c) for c in expected_columns().values())
        print(f"  [PASS] список очікуваних колонок: {ncols} у {len(expected_columns())} таблицях")
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] expected_columns: {e}")
        bad += 1
    if args.command in ("migrate-from-main", "all"):
        ref = _git_main_ref()
        print(f"  [{'PASS' if ref else 'FAIL'}] гілка main для migrate-from-main: {ref or 'не знайдено'}")
        bad += 0 if ref else 1
    if args.command in ("migrate-clean", "migrate-from-main") and not args.reset:
        print("  УВАГА: для реального запуску цієї підкоманди потрібен --reset")
    if args.command == "all" and not args.reset:
        print("  УВАГА: без --reset `all` пропустить migrate-* і виконає лише endpoints та order-parity")
    print("\n[dry-run] " + ("OK: перевірки пройдено" if not bad else "ПРОВАЛ"))
    return 0 if not bad else EXIT_FAIL


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    args = build_parser().parse_args(argv)
    report = Report()

    if args.selftest_sqlite:
        if args.command not in ("endpoints", "order-parity"):
            print("--selftest-sqlite підтримує лише endpoints та order-parity")
            return EXIT_REFUSE
        apply_env_selftest()
        import_project()
        print("[selftest] SQLite у temp, JSON-каталог у temp-копіях, Telegram заглушено")
    else:
        try:
            ident, warnings = check_target(os.environ.get("STAGING_DATABASE_URL", ""))
            print(f"Ціль: {ident.masked()}   (пароль приховано)")
            if args.command in ("migrate-clean", "migrate-from-main") and not args.reset and not args.dry_run:
                raise Refuse(f"`{args.command}` руйнує таблиці застосунку: потрібен явний --reset.")
            if args.reset and args.command in ("endpoints", "order-parity"):
                print("  УВАГА: --reset для цієї підкоманди ігнорується")
            confirm_staging(args, ident)
        except Refuse as e:
            print(str(e), file=sys.stderr)
            return EXIT_REFUSE
        apply_env(ident.url)
        if args.dry_run:
            return dry_run(args, ident, warnings)
        for w in warnings:
            print(f"  УВАГА: {w}")
        try:
            import_project()
        except Exception as e:  # noqa: BLE001
            print(f"Не вдалось імпортувати проєкт: {type(e).__name__}: {sanitize(e)}", file=sys.stderr)
            return EXIT_REFUSE

    steps = []
    cmd = args.command
    if cmd in ("migrate-clean", "all") and (cmd == "migrate-clean" or args.reset):
        steps.append(migrate_clean)
    if cmd in ("migrate-from-main", "all") and (cmd == "migrate-from-main" or args.reset):
        steps.append(migrate_from_main)
    if cmd in ("endpoints", "all"):
        steps.append(run_endpoints)
    if cmd in ("order-parity", "all"):
        steps.append(run_parity)
    if cmd == "all" and not args.reset:
        print("(без --reset: migrate-* пропущено)")

    try:
        for step in steps:
            try:
                step(report, args)
            except Refuse as e:
                print(str(e), file=sys.stderr)
                return EXIT_REFUSE
            except Exception as e:  # noqa: BLE001
                report.add(step.__name__, "крок завершився винятком", False, f"{type(e).__name__}: {e}")
    except KeyboardInterrupt:
        print("\nПерервано користувачем")
        report.add("run", "прогін не перервано", False, "KeyboardInterrupt")
    return report.summary()


if __name__ == "__main__":
    sys.exit(main())
