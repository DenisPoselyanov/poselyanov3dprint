#!/usr/bin/env python3
"""Перенести фото товарів зі сторонніх хостингів (i.ibb.co тощо) у Cloudinary.

Сторонні фото віддаються в оригінальному розмірі (1,5–3,5 МБ PNG) — Cloudinary стискає їх
до 1000px і віддає WebP/AVIF через f_auto, як решту каталогу.

    python scripts/rehost_external_photos.py           # лише показати, що буде змінено
    python scripts/rehost_external_photos.py --apply   # завантажити й оновити товари

Працює з тим каталогом, який налаштований у .env (CATALOG_BACKEND: postgres або JSON-файли).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import cloudinary  # noqa: E402
import cloudinary.uploader  # noqa: E402

import config  # noqa: E402
from catalog_store import load_products_file, use_catalog_db  # noqa: E402
from db_core import db_connect  # noqa: E402

CLOUDINARY_HOST = "res.cloudinary.com"


def is_external(url: str) -> bool:
    return bool(url) and url.startswith("http") and CLOUDINARY_HOST not in url


def rehost(url: str, product_id: int) -> str:
    result = cloudinary.uploader.upload(
        url,
        folder="poselyanov3dprint",
        public_id=f"rehosted_{product_id}_{abs(hash(url)) % 10**8}",
        resource_type="image",
        width=1000,
        crop="limit",
        quality="auto:good",
        fetch_format="auto",
    )
    return result["secure_url"]


def load_products() -> list[dict]:
    if use_catalog_db():
        conn = db_connect(dict_rows=True)
        rows = conn.execute("SELECT id, name, photos FROM products ORDER BY id").fetchall()
        conn.close()
        out = []
        for r in rows:
            photos = r["photos"] or []
            if isinstance(photos, str):
                photos = json.loads(photos)
            out.append({"id": r["id"], "name": r["name"], "photos": photos})
        return out
    return [
        {**p, "_file": path}
        for path in (config.PRODUCTS_FILE, config.CUSTOM_PRODUCTS_FILE)
        for p in load_products_file(path)
    ]


def save_photos(product: dict, photos: list[str]) -> None:
    if use_catalog_db():
        conn = db_connect()
        conn.execute(
            "UPDATE products SET photos = %s::jsonb WHERE id = %s",
            (json.dumps(photos), product["id"]),
        )
        conn.commit()
        conn.close()
        return
    path = Path(product["_file"])
    data = json.loads(path.read_text(encoding="utf-8"))
    for p in data:
        if p.get("id") == product["id"]:
            p["photos"] = photos
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="завантажити в Cloudinary й оновити товари")
    args = parser.parse_args()

    if args.apply and not (config.CLOUDINARY_CLOUD_NAME and config.CLOUDINARY_API_KEY and config.CLOUDINARY_API_SECRET):
        print("Немає ключів Cloudinary у .env")
        return 1
    cloudinary.config(
        cloud_name=config.CLOUDINARY_CLOUD_NAME,
        api_key=config.CLOUDINARY_API_KEY,
        api_secret=config.CLOUDINARY_API_SECRET,
        secure=True,
    )

    changed = 0
    for product in load_products():
        photos = product.get("photos") or []
        external = [u for u in photos if is_external(u)]
        if not external:
            continue
        print(f"#{product['id']} {product.get('name', '')}: {len(external)} стороннє фото")
        if not args.apply:
            for u in external:
                print("   ", u)
            continue
        new_photos = []
        for u in photos:
            if is_external(u):
                new_url = rehost(u, product["id"])
                print(f"    {u}\n      -> {new_url}")
                new_photos.append(new_url)
            else:
                new_photos.append(u)
        save_photos(product, new_photos)
        changed += 1

    if args.apply:
        print(f"Оновлено товарів: {changed}. У проді: /reload у боті або перезапуск сервісу.")
    else:
        print("Перегляд без змін. Запусти з --apply, щоб перенести.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
