#!/usr/bin/env python3
"""
Фінальний smoke-тест після деплою.

Запуск:
    # Проти продакшну (URL береться з api-config.js автоматично):
    python tests/smoke_test.py

    # Явно вказати URL:
    python tests/smoke_test.py --api-url https://poselyanov3dprint.duckdns.org

    # Проти локального сервера:
    python tests/smoke_test.py --api-url http://localhost:8080

Необхідно: BOT_TOKEN у .env або у змінній середовища.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Примусово UTF-8 на Windows, де stdout може бути cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ── кольори для термінала ───────────────────────────────────────────────────
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"

_USE_COLOR = sys.stdout.isatty()


def _c(color: str, text: str) -> str:
    return f"{color}{text}{RESET}" if _USE_COLOR else text


def ok(msg: str) -> None:
    print(f"  {_c(GREEN, '✓')} {msg}")


def fail(msg: str) -> None:
    print(f"  {_c(RED, '✗')} {msg}")


def skip(msg: str) -> None:
    print(f"  {_c(YELLOW, '⚠')} {msg}")


def section(title: str) -> None:
    print(f"\n{_c(BOLD + CYAN, title)}")
    print(_c(CYAN, "─" * 60))


# ── .env завантажувач (без залежностей) ─────────────────────────────────────
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# ── Telegram initData підпис ─────────────────────────────────────────────────
def _build_init_data(bot_token: str, user_id: int = 9_999_999_999) -> str:
    """Генерує валідну initData для тестового user_id."""
    user_json = json.dumps(
        {"id": user_id, "first_name": "SmokeTest", "username": "smoketest_bot"},
        separators=(",", ":"),
    )
    auth_date = int(time.time())
    params = {
        "auth_date": str(auth_date),
        "user": user_json,
    }
    check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    sig = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    params["hash"] = sig
    return urllib.parse.urlencode(params)


# ── HTTP хелпери ─────────────────────────────────────────────────────────────
def _http(
    method: str,
    url: str,
    *,
    body: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
) -> tuple[int, dict | str]:
    data = json.dumps(body).encode() if body is not None else None
    req_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except urllib.error.URLError as e:
        raise ConnectionError(f"Не вдалося з'єднатися з {url}: {e.reason}") from e


# ── Smoke-тести ───────────────────────────────────────────────────────────────

_RESULTS: list[tuple[str, bool, str]] = []  # (назва, pass, деталь)


def _record(name: str, passed: bool, detail: str = "") -> None:
    _RESULTS.append((name, passed, detail))
    if passed:
        ok(f"{name}" + (f" — {detail}" if detail else ""))
    else:
        fail(f"{name}" + (f" — {detail}" if detail else ""))


def test_health(base: str) -> None:
    section("8.1 ▸ GET /health → 200 OK")
    status, body = _http("GET", f"{base}/health")
    passed = status == 200
    detail = f"HTTP {status}, тіло: {str(body)[:80]}"
    _record("GET /health", passed, detail)


def test_order_invalid_init_data(base: str) -> None:
    section("8.2 ▸ POST /order з невалідним initData → 401/403")
    # Варіант 1: без initData взагалі
    status1, body1 = _http(
        "POST",
        f"{base}/order",
        body={"items": [{"product_id": 1, "quantity": 1}], "total_price": 125},
        headers={"Origin": "https://denisposelyanov.github.io"},
    )
    passed1 = status1 in (401, 403)
    if status1 == 500:
        detail1 = (
            "HTTP 500 — ❌ ПОМИЛКА: сервер повертає 500 замість 403. "
            "Ймовірна причина: ALLOW_LOCAL_NETWORK=true у .env — всі запити через nginx "
            "сприймаються як локальні (request.remote=127.0.0.1) і Auth-bypass спрацьовує. "
            "Перевір: sudo journalctl -u poselyanov3dprint -n 20"
        )
    elif not passed1:
        detail1 = f"HTTP {status1} — очікувався 401/403"
    else:
        detail1 = f"HTTP {status1} ✓"
    _record("POST /order (без initData)", passed1, detail1)

    # Варіант 2: garbage initData
    status2, body2 = _http(
        "POST",
        f"{base}/order",
        body={"items": [{"product_id": 1, "quantity": 1}], "total_price": 125},
        headers={
            "X-Telegram-Init-Data": "hash=deadbeef&auth_date=1&user=%7B%7D",
            "Origin": "https://denisposelyanov.github.io",
        },
    )
    passed2 = status2 in (401, 403)
    _record(
        "POST /order (garbage initData)",
        passed2,
        f"HTTP {status2} {'✓' if passed2 else '— очікувався 401/403'}",
    )


def test_order_valid(base: str, bot_token: str) -> None:
    section("8.3 ▸ POST /order з валідним initData → замовлення прийнято")
    if not bot_token:
        skip("BOT_TOKEN не знайдено — тест пропущено")
        _RESULTS.append(("POST /order (валідний)", None, "пропущено (немає BOT_TOKEN)"))
        return

    import uuid
    init_data = _build_init_data(bot_token)
    status, body = _http(
        "POST",
        f"{base}/order",
        body={
            "items": [{"product_id": 1, "quantity": 1, "product_name": "Fidget-спіраль", "price": 125}],
            "total_price": 125,
            "comment": "[SMOKE TEST — можна видалити]",
            "idempotency_key": f"smoke-{uuid.uuid4().hex[:12]}",
        },
        headers={
            "X-Telegram-Init-Data": init_data,
            "Origin": "https://denisposelyanov.github.io",
        },
    )
    # 200 = нове замовлення; 200 з ok=True теж підходить
    if isinstance(body, dict):
        passed = status == 200 and body.get("ok") is not False
        detail = f"HTTP {status}, order_id={body.get('order_id', '?')}, ok={body.get('ok')}"
    else:
        passed = status == 200
        detail = f"HTTP {status}, тіло: {str(body)[:100]}"
    _record("POST /order (валідний)", passed, detail)
    if passed:
        skip("Перевір ORDERS_CHAT_ID у Telegram — там має з'явитися тестове замовлення з позначкою [SMOKE TEST]")


def test_coupon_exhausted(base: str, bot_token: str) -> None:
    section("8.4 ▸ Купон з перевищеним лімітом → коректна помилка")
    if not bot_token:
        skip("BOT_TOKEN не знайдено — тест пропущено")
        _RESULTS.append(("POST /check_coupon (вичерпаний)", None, "пропущено (немає BOT_TOKEN)"))
        return

    init_data = _build_init_data(bot_token)
    # Спочатку пробуємо з кодом-заглушкою, щоб побачити правильну відповідь "не знайдено"
    status, body = _http(
        "POST",
        f"{base}/check_coupon",
        body={"code": "SMOKE_NONEXISTENT_9999", "cart_total": 500},
        headers={
            "X-Telegram-Init-Data": init_data,
            "Origin": "https://denisposelyanov.github.io",
        },
    )
    if isinstance(body, dict) and body.get("valid") is False:
        msg = body.get("message", "")
        passed = status == 200 and "не знайдено" in msg
        _record(
            "POST /check_coupon (неіснуючий купон)",
            passed,
            f"HTTP {status}, message='{msg}'",
        )
    else:
        _record(
            "POST /check_coupon (неіснуючий купон)",
            False,
            f"HTTP {status}, тіло: {str(body)[:100]}",
        )

    # Тест з кодом, що має uses_max=1, uses_count=1 — якщо такий є в БД
    # (сервер має відповісти valid=false + "вичерпано" або "не знайдено")
    status2, body2 = _http(
        "POST",
        f"{base}/check_coupon",
        body={"code": "EXHAUSTED", "cart_total": 500},
        headers={
            "X-Telegram-Init-Data": init_data,
            "Origin": "https://denisposelyanov.github.io",
        },
    )
    if isinstance(body2, dict) and body2.get("valid") is False:
        msg2 = body2.get("message", "")
        passed2 = status2 == 200 and ("вичерпано" in msg2 or "не знайдено" in msg2 or "закінчився" in msg2 or "активний" in msg2)
        _record(
            "POST /check_coupon (ліміт вичерпано / не існує)",
            passed2,
            f"HTTP {status2}, message='{msg2}'",
        )
    else:
        passed2 = status2 in (401, 403, 200)
        _record(
            "POST /check_coupon (EXHAUSTED код)",
            passed2,
            f"HTTP {status2}, тіло: {str(body2)[:80]}",
        )


def test_admin_panel_blocked(base: str) -> None:
    section("8.6 ▸ Адмін API без авторизації → доступ заблокований")

    # GET /admin/panel повертає HTML (auth перевіряється client-side JS) — це нормальна поведінка.
    # Тест перевіряє захист адмін API-ендпоінтів, які МАЮТЬ повертати 403.
    status_html, _ = _http("GET", f"{base}/admin/panel")
    html_served = status_html == 200
    # HTML-сторінка повертає 200 (JS перевіряє auth) — це очікувано
    _record(
        "GET /admin/panel → HTML (auth client-side)",
        html_served,
        f"HTTP {status_html} — HTML повернуто {'✓' if html_served else '✗'}",
    )

    # Адмін API: GET /api/orders без auth → ОБОВ'ЯЗКОВО 403
    status_api, body_api = _http("GET", f"{base}/api/orders", headers={"Accept": "application/json"})
    passed_api = status_api == 403
    if not passed_api and status_api == 200:
        detail_api = (
            f"HTTP {status_api} — ❌ КРИТИЧНА ПРОБЛЕМА БЕЗПЕКИ: "
            f"замовлення видно без авторизації! "
            f"Ймовірна причина: ALLOW_LOCAL_NETWORK=true у .env на сервері — "
            f"nginx проксіює з 127.0.0.1, всі запити вважаються локальними."
        )
    else:
        detail_api = f"HTTP {status_api} {'✓' if passed_api else '— очікувався 403'}"
    _record("GET /api/orders (без auth) → 403", passed_api, detail_api)

    # Адмін API: GET /api/coupons без auth → ОБОВ'ЯЗКОВО 403
    status_coupons, _ = _http("GET", f"{base}/api/coupons", headers={"Accept": "application/json"})
    passed_coupons = status_coupons == 403
    _record(
        "GET /api/coupons (без auth) → 403",
        passed_coupons,
        f"HTTP {status_coupons} {'✓' if passed_coupons else '— очікувався 403'}",
    )


# ── Підсумок ──────────────────────────────────────────────────────────────────

def _summary() -> int:
    section("ПІДСУМОК SMOKE-ТЕСТУ")
    total = len([r for r in _RESULTS if r[1] is not None])
    passed = sum(1 for r in _RESULTS if r[1] is True)
    skipped = sum(1 for r in _RESULTS if r[1] is None)

    for name, result, detail in _RESULTS:
        if result is True:
            print(f"  {_c(GREEN, '✓ PASS')}  {name}")
        elif result is False:
            print(f"  {_c(RED, '✗ FAIL')}  {name}  ({detail})")
        else:
            print(f"  {_c(YELLOW, '⚠ SKIP')}  {name}  ({detail})")

    print()
    if passed == total:
        print(_c(GREEN + BOLD, f"  Всі {passed}/{total} тестів пройшли успішно!"))
    else:
        failed = total - passed
        print(_c(RED + BOLD, f"  {failed}/{total} тестів НЕ пройшли. Перевір деталі вище."))

    if skipped:
        print(_c(YELLOW, f"  {skipped} тестів пропущено (немає BOT_TOKEN)."))

    # Ручні кроки
    print()
    section("Ручні кроки (виконай на сервері)")
    manual = [
        ("8.5", "Відкрити index.html у Telegram",
         "  → Надішли /start боту, натисни кнопку «🛍️ Відкрити каталог».\n"
         "    Каталог має завантажитися, товари — відображатись."),
        ("8.7", "Перевірити systemd-логи",
         "  $ sudo journalctl -u poselyanov3dprint -n 50 --no-pager\n"
         "    → Немає рядків ERROR або CRITICAL"),
        ("8.8", "Перевірити nginx access-лог",
         "  $ sudo tail -n 100 /var/log/nginx/access.log | grep ' 5[0-9][0-9] '\n"
         "    → Рядків 5xx НЕ має бути"),
    ]
    for num, title, instructions in manual:
        print(f"\n  {_c(BOLD, num + ' ▸ ' + title)}")
        print(_c(YELLOW, instructions))

    # Якщо є проблема з ALLOW_LOCAL_NETWORK — показати інструкцію виправлення
    security_failed = any(
        "api/orders" in name and result is False
        for name, result, _ in _RESULTS
    )
    if security_failed:
        print()
        section("НЕОБХІДНЕ ВИПРАВЛЕННЯ БЕЗПЕКИ")
        print(_c(RED + BOLD, "  ❌ /api/orders доступний без авторизації!"))
        print(_c(YELLOW, """
  Крок 1. Перевір /etc/poselyanov3dprint/.env на сервері:
    $ sudo grep -E 'ALLOW_LOCAL_NETWORK|LOCAL_DEV_MODE|VALIDATE_INIT_DATA' /etc/poselyanov3dprint/.env

  Крок 2. Якщо ALLOW_LOCAL_NETWORK=true — встанови false (або видали цей рядок):
    $ sudo nano /etc/poselyanov3dprint/.env
    # Знайди і виправ:
    ALLOW_LOCAL_NETWORK=false
    LOCAL_DEV_MODE=false
    VALIDATE_INIT_DATA=true

  Крок 3. Перезапусти сервіс:
    $ sudo systemctl restart poselyanov3dprint

  Крок 4. Повтори smoke-тест:
    $ python tests/smoke_test.py
"""
        ))

    return 0 if passed == total else 1


# ── main ──────────────────────────────────────────────────────────────────────

def _detect_api_url() -> str:
    """Спробувати прочитати window.__API_BASE__ з api-config.js (пропускає закоментовані рядки)."""
    cfg = ROOT / "api-config.js"
    if cfg.exists():
        for line in cfg.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*"):
                continue
            m = re.search(r"window\.__API_BASE__\s*=\s*['\"]([^'\"]+)['\"]", stripped)
            if m:
                return m.group(1).rstrip("/")
    return "http://localhost:8080"


def main() -> int:
    _load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description="Smoke-тест після деплою")
    parser.add_argument(
        "--api-url",
        default=_detect_api_url(),
        help="Базовий URL API (default: з api-config.js або http://localhost:8080)",
    )
    args = parser.parse_args()
    base = args.api_url.rstrip("/")
    bot_token = os.environ.get("BOT_TOKEN", "")

    print(_c(BOLD, "\n══════════════════════════════════════════════════════════"))
    print(_c(BOLD, "  SMOKE-ТЕСТ: Poselyanov 3D Print Bot + API"))
    print(_c(BOLD, "══════════════════════════════════════════════════════════"))
    print(f"  API URL : {_c(CYAN, base)}")
    print(f"  BOT_TOKEN: {_c(GREEN, 'знайдено') if bot_token else _c(YELLOW, 'НЕ знайдено — деякі тести пропустяться')}")
    print()

    try:
        test_health(base)
        test_order_invalid_init_data(base)
        test_order_valid(base, bot_token)
        test_coupon_exhausted(base, bot_token)
        test_admin_panel_blocked(base)
    except ConnectionError as e:
        fail(f"Сервер недоступний: {e}")
        print(_c(RED, "\n  ❌ Не вдалося з'єднатися з сервером. Перевір, чи запущений бот і чи правильний --api-url."))
        return 2

    return _summary()


if __name__ == "__main__":
    sys.exit(main())
