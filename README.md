# Poselyanov 3D Print

Telegram Mini App for a 3D print shop: catalog, cart, coupons, admin panel.

## Stack

- **Frontend:** `index.html` (GitHub Pages), `admin-panel.html` (VPS)
- **Backend:** `bot.py` — python-telegram-bot + aiohttp
- **Catalog:** JSON files or Supabase Postgres (`catalog_store.py`)
- **Orders:** SQLite or Supabase

## Quick start (local)

```bash
cp .env.example .env
# Fill BOT_TOKEN, Cloudinary, etc.
pip install -r requirements.txt
python bot.py
```

Set `LOCAL_DEV_MODE=true` and `VALIDATE_INIT_DATA=false` for local browser testing.

## Modules

| File | Role |
|------|------|
| `config.py` | Environment configuration |
| `auth.py` | Telegram initData, CORS, admin auth |
| `catalog_store.py` | Product/category/filament CRUD |
| `db_core.py` | SQLite/Postgres connection |
| `security_utils.py` | SSRF protection, static allowlist |
| `rich_messages.py` | Telegram rich HTML messages |

## Production (VPS)

See [deploy/README.md](deploy/README.md).

## Tests

```bash
python -m pytest tests/
```
