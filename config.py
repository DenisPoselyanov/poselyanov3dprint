"""Central configuration from environment variables."""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

BOT_TOKEN = os.environ.get("BOT_TOKEN")
_owner_id_raw = os.environ.get("OWNER_ID", "").strip()
OWNER_ID = int(_owner_id_raw) if _owner_id_raw else 0
ORDERS_CHAT_ID = int(os.environ.get("ORDERS_CHAT_ID", str(OWNER_ID)))
WEBAPP_URL = os.environ.get(
    "WEBAPP_URL", "https://denisposelyanov.github.io/poselyanov3dprint/"
).strip()
API_PUBLIC_URL = os.environ.get("API_PUBLIC_URL", "").strip().rstrip("/")

DB_FILE = os.environ.get("DB_FILE", "users.db")
DB_BACKEND = os.environ.get("DB_BACKEND", "sqlite").strip().lower()
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")

PRODUCTS_FILE = os.environ.get("PRODUCTS_FILE", "products.json")
CUSTOM_PRODUCTS_FILE = os.environ.get("CUSTOM_PRODUCTS_FILE", "custom_products.json")
FILAMENTS_FILE = os.environ.get("FILAMENTS_FILE", "filaments.json")
CATEGORIES_FILE = os.environ.get("CATEGORIES_FILE", "categories.json")

VALIDATE_INIT_DATA = os.environ.get("VALIDATE_INIT_DATA", "true").lower() in ("1", "true", "yes")
LOCAL_DEV_MODE = os.environ.get("LOCAL_DEV_MODE", "false").lower() in ("1", "true", "yes")
# Дозволити адмін-доступ з будь-якого пристрою локальної мережі (192.168.x.x, 10.x.x.x тощо).
ALLOW_LOCAL_NETWORK = os.environ.get("ALLOW_LOCAL_NETWORK", "false").lower() in ("1", "true", "yes")
INIT_DATA_MAX_AGE_SEC = int(os.environ.get("INIT_DATA_MAX_AGE_SEC", "86400"))

# Secure token for direct browser admin access (e.g. via ngrok without Telegram context).
# Loaded from env if set, otherwise generated fresh each process start.
ADMIN_BYPASS_TOKEN: str = os.environ.get("ADMIN_BYPASS_TOKEN") or secrets.token_urlsafe(32)

PROMOTION_ENABLED = os.environ.get("PROMOTION_ENABLED", "true").lower() in ("1", "true", "yes")

_default_cors = (
    "https://denisposelyanov.github.io,http://localhost:8080,http://127.0.0.1:8080,"
    "http://localhost:5500,http://127.0.0.1:5500"
    if LOCAL_DEV_MODE
    else "https://denisposelyanov.github.io"
)
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", _default_cors).split(",")
    if o.strip()
]

CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "")

CATALOG_BACKEND = os.environ.get("CATALOG_BACKEND", DB_BACKEND).strip().lower()

STATIC_ALLOWED_FILES = frozenset(
    f.strip()
    for f in os.environ.get(
        "STATIC_ALLOWED_FILES",
        "index.html,admin-panel.html,logo.svg,logo-dark.svg,shared.js,api-config.js,"
        "products.json,custom_products.json,categories.json,filaments.json",
    ).split(",")
    if f.strip()
)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
BROADCAST_DELAY_SEC = float(os.environ.get("BROADCAST_DELAY_SEC", "0.05"))
# False у продакшні — не втрачати updates під час рестарту. True лише для debug/першого деплою.
DROP_PENDING_UPDATES = os.environ.get("DROP_PENDING_UPDATES", "false").lower() in (
    "1",
    "true",
    "yes",
)
# Мінімальний інтервал між повторними /start від одного користувача (секунди).
COMMAND_COOLDOWN_SEC = float(os.environ.get("COMMAND_COOLDOWN_SEC", "2.0"))


def validate_startup_config() -> None:
    """Fail fast on insecure or incomplete configuration."""
    errors: list[str] = []

    if not BOT_TOKEN:
        errors.append("BOT_TOKEN is required")

    if DB_BACKEND == "postgres" and not DATABASE_URL:
        errors.append("DATABASE_URL (or SUPABASE_DB_URL) is required when DB_BACKEND=postgres")

    if not VALIDATE_INIT_DATA and not LOCAL_DEV_MODE:
        errors.append(
            "VALIDATE_INIT_DATA=false is only allowed with LOCAL_DEV_MODE=true "
            "(localhost development). Set VALIDATE_INIT_DATA=true for production."
        )

    if not os.environ.get("OWNER_ID", "").strip():
        errors.append("OWNER_ID must be set explicitly in environment")

    if ALLOW_LOCAL_NETWORK and not LOCAL_DEV_MODE:
        errors.append(
            "ALLOW_LOCAL_NETWORK=true is only safe for local development "
            "(set LOCAL_DEV_MODE=true). Disable for production."
        )

    if not LOCAL_DEV_MODE and not API_PUBLIC_URL:
        errors.append(
            "API_PUBLIC_URL is required in production (GitHub Pages storefront needs a public API URL). "
            "Must match window.__API_BASE__ in api-config.js on GitHub Pages."
        )

    if errors:
        raise RuntimeError("Configuration error:\n  - " + "\n  - ".join(errors))
