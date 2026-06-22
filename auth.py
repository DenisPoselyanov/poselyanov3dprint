"""Telegram WebApp authentication, CORS, and admin authorization."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlparse

from aiohttp import web

import config

logger = logging.getLogger(__name__)


def normalize_origin(value: str) -> str:
    return (value or "").strip().rstrip("/").lower()


def validate_telegram_init_data(init_data: str) -> dict | None:
    if not init_data or not config.BOT_TOKEN:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        auth_date_raw = parsed.get("auth_date")
        if auth_date_raw:
            try:
                auth_ts = int(auth_date_raw)
                age = datetime.now(timezone.utc).timestamp() - auth_ts
                if age > config.INIT_DATA_MAX_AGE_SEC:
                    logger.warning("initData expired (age=%ss)", int(age))
                    return None
            except (TypeError, ValueError):
                return None

        check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if calc != received_hash:
            return None

        user = json.loads(parsed.get("user", "{}")) if parsed.get("user") else {}
        return {
            "user_id": user.get("id"),
            "username": user.get("username"),
            "first_name": user.get("first_name"),
        }
    except Exception:
        logger.exception("initData validation failed")
        return None


def cors_headers(request: web.Request) -> dict:
    origin = request.headers.get("Origin", "")
    normalized_origin = normalize_origin(origin)
    normalized_allowed = {
        normalize_origin(o): o.rstrip("/") for o in config.CORS_ORIGINS if o.strip()
    }

    allow = "*"
    if normalized_origin and ("*" in config.CORS_ORIGINS or normalized_origin in normalized_allowed):
        allow = origin.rstrip("/")
    elif config.CORS_ORIGINS and config.CORS_ORIGINS[0] != "*":
        allow = config.CORS_ORIGINS[0].rstrip("/")
    return {
        "Access-Control-Allow-Origin": allow,
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data, ngrok-skip-browser-warning",
        "Vary": "Origin",
    }


def resolve_request_user(request: web.Request, data: dict) -> tuple[dict | None, web.Response | None]:
    init_data = request.headers.get("X-Telegram-Init-Data") or data.get("init_data") or ""
    auth = validate_telegram_init_data(init_data) if init_data else None

    if config.VALIDATE_INIT_DATA:
        if not auth or not auth.get("user_id"):
            return None, web.json_response(
                {"ok": False, "error": "invalid_init_data"},
                status=403,
                headers=cors_headers(request),
            )
        return auth, None

    if auth and auth.get("user_id"):
        return auth, None

    uid = data.get("user_id")
    if uid:
        return {
            "user_id": uid,
            "username": (data.get("tg_username") or data.get("username") or "").lstrip("@"),
            "first_name": data.get("first_name") or "",
        }, None

    return {"user_id": 0, "username": "", "first_name": ""}, None


def is_local_dev_origin(request: web.Request) -> bool:
    if not config.LOCAL_DEV_MODE:
        return False
    origin = request.headers.get("Origin", "").strip()
    if not origin:
        return False
    try:
        host = (urlparse(origin).hostname or "").lower()
        return host in {"localhost", "127.0.0.1"}
    except Exception:
        return False


def is_admin_authorized(request: web.Request, auth: dict | None) -> bool:
    if auth and auth.get("user_id") == config.OWNER_ID:
        return True
    if is_local_dev_origin(request):
        return True
    return False


def require_admin(request: web.Request, auth: dict | None) -> web.Response | None:
    if config.VALIDATE_INIT_DATA and not is_admin_authorized(request, auth):
        return web.json_response({"error": "Forbidden"}, status=403, headers=cors_headers(request))
    return None


def extract_init_data(request: web.Request) -> str:
    return (
        request.query.get("initData")
        or request.headers.get("X-Telegram-Init-Data")
        or request.cookies.get("tgInitData", "")
        or ""
    )
