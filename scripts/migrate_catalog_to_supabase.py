#!/usr/bin/env python3
"""Import catalog JSON files into Supabase/Postgres."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import config  # noqa: E402
from catalog_store import init_catalog_tables, sync_products_id_sequence  # noqa: E402
from db_core import db_connect  # noqa: E402


def load_json(name: str):
    path = ROOT / name
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def migrate(database_url: str, truncate: bool = False) -> None:
    config.DATABASE_URL = database_url
    config.DB_BACKEND = "postgres"

    conn = db_connect()
    init_catalog_tables(conn)

    if truncate:
        conn.execute("TRUNCATE products, categories, filaments RESTART IDENTITY CASCADE")

    categories = load_json("categories.json")
    for cat in categories:
        conn.execute(
            """
            INSERT INTO categories (id, name, emoji, badge_class, sort_order, active, quick_slot)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, emoji = EXCLUDED.emoji, badge_class = EXCLUDED.badge_class,
                sort_order = EXCLUDED.sort_order, active = EXCLUDED.active, quick_slot = EXCLUDED.quick_slot
            """,
            (
                cat.get("id"),
                cat.get("name"),
                cat.get("emoji"),
                cat.get("badgeClass"),
                cat.get("order", 0),
                bool(cat.get("active", True)),
                cat.get("quickSlot"),
            ),
        )

    def insert_product(p: dict, is_custom: bool) -> None:
        conn.execute(
            """
            INSERT INTO products (
                id, category_id, name, emoji, mat, price, old_price, photos, custom_fields,
                hot, gift, filament_choice, luminous_filament_choice, pinned, stl_link, contract_price, is_custom, active
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, true
            )
            ON CONFLICT (id) DO UPDATE SET
                category_id = EXCLUDED.category_id, name = EXCLUDED.name, emoji = EXCLUDED.emoji,
                mat = EXCLUDED.mat, price = EXCLUDED.price, old_price = EXCLUDED.old_price,
                photos = EXCLUDED.photos, custom_fields = EXCLUDED.custom_fields,
                hot = EXCLUDED.hot, gift = EXCLUDED.gift, filament_choice = EXCLUDED.filament_choice,
                luminous_filament_choice = EXCLUDED.luminous_filament_choice,
                pinned = EXCLUDED.pinned, stl_link = EXCLUDED.stl_link,
                contract_price = EXCLUDED.contract_price, is_custom = EXCLUDED.is_custom, active = true
            """,
            (
                p.get("id"),
                p.get("cat"),
                p.get("name"),
                p.get("emoji", "📦"),
                p.get("mat", ""),
                p.get("price", 0),
                p.get("oldPrice"),
                json.dumps(p.get("photos") or []),
                p.get("custom_fields") if isinstance(p.get("custom_fields"), str) else json.dumps(p.get("custom_fields") or ""),
                bool(p.get("hot")),
                bool(p.get("gift")),
                bool(p.get("filamentChoice", True)),
                bool(p.get("luminousFilamentChoice")),
                bool(p.get("pinned")),
                p.get("stlLink", ""),
                bool(p.get("contractPrice")),
                is_custom,
            ),
        )

    for p in load_json("products.json"):
        insert_product(p, False)
    for p in load_json("custom_products.json"):
        insert_product(p, True)

    for i, f in enumerate(load_json("filaments.json")):
        conn.execute(
            """
            INSERT INTO filaments (id, name, hex, available, sort_order)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, hex = EXCLUDED.hex,
                available = EXCLUDED.available, sort_order = EXCLUDED.sort_order
            """,
            (f.get("id"), f.get("name"), f.get("hex"), bool(f.get("available")), i),
        )

    sync_products_id_sequence(conn)
    conn.commit()
    conn.close()
    print("Catalog migration complete.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--truncate-first", action="store_true")
    args = parser.parse_args()
    migrate(args.database_url, truncate=args.truncate_first)


if __name__ == "__main__":
    main()
