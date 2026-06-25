"""Central configuration from environment variables."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "718746623"))
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
INIT_DATA_MAX_AGE_SEC = int(os.environ.get("INIT_DATA_MAX_AGE_SEC", "86400"))

PROMOTION_ENABLED = os.environ.get("PROMOTION_ENABLED", "true").lower() in ("1", "true", "yes")

CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS",
        "https://denisposelyanov.github.io,http://localhost:8080,http://127.0.0.1:8080,"
        "http://localhost:5500,http://127.0.0.1:5500",
    ).split(",")
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

    if errors:
        raise RuntimeError("Configuration error:\n  - " + "\n  - ".join(errors))
