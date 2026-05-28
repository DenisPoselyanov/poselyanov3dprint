# SQLite -> Supabase migration

## 1) Apply schema in Supabase SQL Editor

Run file:

- `scripts/supabase_schema.sql`

## 2) Import data from SQLite

```bash
python scripts/migrate_sqlite_to_supabase.py --sqlite-path users.db --database-url "postgresql://...sslmode=require" --truncate-first
```

## 3) Validate row counts

```bash
python scripts/validate_migration.py --sqlite-path users.db --database-url "postgresql://...sslmode=require"
```

## 4) Cutover

Set env vars:

- `DB_BACKEND=postgres`
- `DATABASE_URL=postgresql://...sslmode=require`

Restart bot and verify flows:

- `/start` creates user
- checkout from WebApp (`/order`)
- coupon check (`/check_coupon`)
- admin stats and coupon commands

Rollback (if needed):

- `DB_BACKEND=sqlite`
- `DB_FILE=users.db`
