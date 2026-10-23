"""Пропозиції товарів від ШІ: Claude шукає моделі для друку й готує картки в стилі каталогу.

Кнопка «Знайти нові» в адмінці запускає фоновий пошук (`start_generation`). Claude з веб-пошуком
переглядає MakerWorld / Printables / Thingiverse, повертає товари через інструмент `submit_products`,
а ми зберігаємо їх у таблицю `ai_suggestions` зі статусом `new`. У каталог нічого не потрапляє
автоматично: адмін відкриває пропозицію у звичайній формі товару, править і зберігає, після чого
пропозиція стає `published` з номером створеного товару. Ліцензію моделі ШІ не фільтрує, лише описує.

Клієнт Claude ін'єктується (`client=`), тож тести працюють без мережі й ключа.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse

import config
from catalog_store import (
    DESCRIPTION_MAX, NOTE_MAX, VARIANT_NAME_MAX, VARIANTS_MAX, VARIANTS_MIN, get_admin_products, get_all_categories,
)
from db_core import db_connect, is_postgres, run_db, sql as _sql

logger = logging.getLogger(__name__)

STATUSES = ("new", "published", "rejected")
COUNT_MIN, COUNT_MAX, COUNT_DEFAULT = 1, 10, 5
FOCUS_MAX = 300
PRICE_MIN, PRICE_MAX = 20, 20000
NAME_MAX = 80
MAT_MAX = 80
TEXT_MAX = 600
IMAGES_MAX = 10
MAX_CONTINUATIONS = 8
VERIFY_TIMEOUT = 8

# Теми, які магазин не продає: демонічне, Гелловін, магія, окультне, брудне/дорослеє.
# Шукаємо як початок слова (укр./англ. корені) у назві, описі, примітці й назві моделі — збіг відкидає пропозицію.
# Список змінюється тут; перелік і причини — у ADMIN.md, розділ «Пропозиції ШІ».
BLOCKED_STEMS = (
    # демонічне, окультне, магічне
    "демон", "диявол", "дияв", "сатан", "пекл", "пентаграм", "окульт", "ритуал", "некромант", "чаклун",
    "відьм", "відьом", "магі", "заклинан", "бафомет", "люцифер", "вуду", "таро",
    "demon", "devil", "satan", "pentagram", "occult", "ritual", "necromanc", "sorcer", "witch", "wizard",
    "magic", "spells", "voodoo", "tarot", "baphomet", "lucifer", "cursed", "evil", "hellish",
    # Гелловін і похмуре
    "хелоу", "хелов", "хеллов", "геловін", "геллов", "halloween", "spooky", "creepy", "horror", "жах",
    "вампір", "vampire", "зомбі", "zombie", "привид", "ghost", "череп", "skull", "скелет", "skeleton",
    "reaper", "могил", "кров", "blood", "gore",
    # брудне й для дорослих
    "nsfw", "18+", "ерот", "erotic", "секс", "sex", "порно", "porn", "пеніс", "penis", "phallic", "фалос",
    "dick", "boob", "naked", "nude", "голий", "матюк", "fuck", "shit", "лайно", "poop", "turd", "какаш",
)
_BLOCKED_RE = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(s) for s in BLOCKED_STEMS) + ")", re.IGNORECASE)

_job: dict = {"running": False, "startedAt": "", "finishedAt": "", "added": 0, "error": "", "kind": ""}
_tasks: set = set()


def init_ai_suggestions_table(conn) -> None:
    if is_postgres():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_suggestions (
                id            BIGSERIAL PRIMARY KEY,
                status        TEXT NOT NULL DEFAULT 'new',
                name          TEXT NOT NULL DEFAULT '',
                data          TEXT NOT NULL DEFAULT '{}',
                source_url    TEXT NOT NULL DEFAULT '',
                source_title  TEXT NOT NULL DEFAULT '',
                license       TEXT NOT NULL DEFAULT '',
                reason        TEXT NOT NULL DEFAULT '',
                images        TEXT NOT NULL DEFAULT '[]',
                product_id    BIGINT,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_suggestions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                status        TEXT NOT NULL DEFAULT 'new',
                name          TEXT NOT NULL DEFAULT '',
                data          TEXT NOT NULL DEFAULT '{}',
                source_url    TEXT NOT NULL DEFAULT '',
                source_title  TEXT NOT NULL DEFAULT '',
                license       TEXT NOT NULL DEFAULT '',
                reason        TEXT NOT NULL DEFAULT '',
                images        TEXT NOT NULL DEFAULT '[]',
                product_id    INTEGER,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_suggestions_status ON ai_suggestions(status)")


def is_enabled() -> bool:
    return bool(config.ANTHROPIC_API_KEY)


def _iso(value) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    s = str(value)
    # SQLite віддає UTC без пояса — позначаємо явно, щоб браузер не читав це як локальний час
    return s.replace(" ", "T") + "Z" if re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$", s) else s


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── Читання / зміна статусу ─────────────────────────────────────────

_COLUMNS = "id, status, name, data, source_url, source_title, license, reason, images, product_id, created_at"


def _row_to_item(row) -> dict:
    (sid, status, name, data, source_url, source_title, license_, reason, images, product_id, created_at) = row
    try:
        product = json.loads(data or "{}")
    except json.JSONDecodeError:
        product = {}
    try:
        image_list = json.loads(images or "[]")
    except json.JSONDecodeError:
        image_list = []
    return {
        "id": int(sid),
        "status": status,
        "name": name,
        "product": product,
        "sourceUrl": source_url,
        "sourceTitle": source_title,
        "license": license_,
        "reason": reason,
        "images": image_list,
        "productId": int(product_id) if product_id else None,
        "createdAt": _iso(created_at),
    }


def list_suggestions() -> list[dict]:
    conn = db_connect()
    try:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM ai_suggestions ORDER BY id DESC LIMIT 300").fetchall()
    finally:
        conn.close()
    return [_row_to_item(r) for r in rows]


def set_status(suggestion_id: int, status: str, product_id=None) -> dict:
    if status not in STATUSES:
        return {"ok": False, "error": "Невірний статус"}
    pid = None
    if status == "published":
        try:
            pid = int(product_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Потрібен номер створеного товару"}
    conn = db_connect()
    try:
        cur = conn.execute(
            _sql("UPDATE ai_suggestions SET status = ?, product_id = ? WHERE id = ?"),
            (status, pid, int(suggestion_id)),
        )
        conn.commit()
        found = cur.rowcount > 0
    finally:
        conn.close()
    return {"ok": True} if found else {"ok": False, "error": "Не знайдено"}


def delete_suggestion(suggestion_id: int) -> bool:
    conn = db_connect()
    try:
        cur = conn.execute(_sql("DELETE FROM ai_suggestions WHERE id = ?"), (int(suggestion_id),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _known_names() -> list[str]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT name FROM ai_suggestions").fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows if r[0]]


# ── Нормалізація відповіді ШІ ───────────────────────────────────────

def _text(value, max_len: int) -> str:
    return re.sub(r"[ \t]+", " ", str(value or "")).strip()[:max_len]


def _https(value) -> str:
    url = str(value or "").strip()
    return url if re.match(r"^https://[^\s\"'<>]{3,1000}$", url) else ""


def _variants(raw) -> list[dict]:
    """Позиції товару (розміри) від ШІ -> [{name, price}]; менше двох придатних — товар без варіантів."""
    out, seen = [], set()
    for v in raw if isinstance(raw, list) else []:
        if not isinstance(v, dict):
            continue
        name = " ".join(_text(v.get("name"), VARIANT_NAME_MAX).split())
        try:
            price = int(round(float(v.get("price"))))
        except (TypeError, ValueError):
            continue
        if not name or price <= 0 or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        out.append({"name": name, "price": max(PRICE_MIN, min(PRICE_MAX, price))})
    return out[:VARIANTS_MAX] if len(out) >= VARIANTS_MIN else []


def blocked_match(*texts) -> str:
    """Перше слово із чорного списку тем у наданих текстах; порожній рядок — усе гаразд."""
    m = _BLOCKED_RE.search(" ".join(str(t or "") for t in texts))
    return m.group(0) if m else ""


def _clean_images(urls) -> list[str]:
    images = []
    for url in urls or []:
        url = _https(url)
        if url and url not in images:
            images.append(url)
    return images


def normalize_item(raw: dict, category_ids: list[str]) -> dict | None:
    """Пропозиція з відповіді ШІ -> поля для БД; None, якщо без назви чи ціни або тема з чорного списку."""
    if not isinstance(raw, dict):
        return None
    name = _text(raw.get("name"), NAME_MAX)
    if blocked_match(name, raw.get("description"), raw.get("note"), raw.get("source_title"), raw.get("mat")):
        return None
    try:
        price = int(round(float(raw.get("price"))))
    except (TypeError, ValueError):
        price = 0
    if not name or price <= 0:
        return None
    price = max(PRICE_MIN, min(PRICE_MAX, price))
    variants = _variants(raw.get("variants"))
    if variants:
        price = min(v["price"] for v in variants)
    cat = str(raw.get("cat") or "").strip()
    if cat not in category_ids:
        cat = next((c for c in category_ids if c != "custom"), "toy")
    emoji = _text(raw.get("emoji"), 8) or "📦"
    product = {
        "name": name,
        "cat": cat,
        "emoji": emoji,
        "mat": _text(raw.get("mat"), MAT_MAX) or "PLA",
        "price": price,
        "description": str(raw.get("description") or "").strip()[:DESCRIPTION_MAX],
        "note": _text(raw.get("note"), NOTE_MAX),
        "filamentChoice": bool(raw.get("filament_choice", True)),
        "gift": bool(raw.get("gift", False)),
    }
    if variants:
        product["variants"] = variants
        product["gift"] = False
    source_url = _https(raw.get("source_url"))
    # Посилання на STL: пряма сторінка завантаження; якщо ШІ її не дав, то сторінка моделі
    stl_link = _https(raw.get("stl_link")) or source_url
    if stl_link:
        product["stlLink"] = stl_link
    images = _clean_images(raw.get("image_urls"))
    return {
        "name": name,
        "product": product,
        "source_url": source_url,
        "source_title": _text(raw.get("source_title"), 200),
        "license": _text(raw.get("license"), TEXT_MAX),
        "reason": _text(raw.get("reason"), TEXT_MAX),
        "images": images[:IMAGES_MAX],
    }


def save_items(items: list[dict]) -> int:
    if not items:
        return 0
    conn = db_connect()
    try:
        for it in items:
            conn.execute(
                _sql(
                    "INSERT INTO ai_suggestions (status, name, data, source_url, source_title, license, reason, images) "
                    "VALUES ('new', ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    it["name"],
                    json.dumps(it["product"], ensure_ascii=False),
                    it["source_url"],
                    it["source_title"],
                    it["license"],
                    it["reason"],
                    json.dumps(it["images"]),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return len(items)


# ── Промпт і виклик Claude ──────────────────────────────────────────

SUBMIT_TOOL = {
    "name": "submit_products",
    "description": "Зберегти знайдені товари як пропозиції для адміна магазину. Викликати один раз у кінці, з усіма товарами.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "name", "cat", "emoji", "mat", "price", "description", "note",
                        "filament_choice", "gift", "source_url", "source_title", "license",
                        "reason", "image_urls", "stl_link", "variants",
                    ],
                    "properties": {
                        "name": {"type": "string", "description": "Назва товару для вітрини, коротко, як у каталозі"},
                        "cat": {"type": "string", "description": "id категорії з переліку"},
                        "emoji": {"type": "string", "description": "Один emoji"},
                        "mat": {"type": "string", "description": "Короткий рядок під назвою, як у каталозі, напр. «PLA · різні кольори»"},
                        "price": {"type": "integer", "description": "Ціна в гривнях"},
                        "description": {"type": "string", "description": "Опис для картки товару"},
                        "note": {"type": "string", "description": "Коротка примітка (розмір, час друку тощо) або порожній рядок"},
                        "filament_choice": {"type": "boolean", "description": "Чи може покупець вибрати колір"},
                        "gift": {"type": "boolean", "description": "Чи годиться як дрібний подарунок до замовлення"},
                        "source_url": {"type": "string", "description": "Посилання на сторінку моделі"},
                        "stl_link": {"type": "string", "description": "Пряме посилання на сторінку, звідки качається STL/3MF цієї моделі; порожній рядок, якщо не бачив"},
                        "source_title": {"type": "string", "description": "Назва моделі на сайті й автор"},
                        "license": {"type": "string", "description": "Ліцензія моделі і чи дозволено продавати друк"},
                        "reason": {"type": "string", "description": "Чому цей товар підійде магазину"},
                        "image_urls": {"type": "array", "items": {"type": "string"}, "description": "Прямі https-посилання на фото: до 10, найкращі й різноманітні, зокрема фото з відгуків і розділу «Prints/Makes»"},
                        "variants": {
                            "type": "array",
                            "description": "Позиції (розміри) цього товару з цінами, якщо модель має кілька розмірів; інакше порожній список. 2–10 позицій, назва — що саме відрізняє (напр. «10 см»)",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["name", "price"],
                                "properties": {
                                    "name": {"type": "string", "description": "Підпис позиції: розмір чи комплектація, до 40 символів"},
                                    "price": {"type": "integer", "description": "Ціна позиції в гривнях"},
                                },
                            },
                        },
                    },
                },
            }
        },
    },
}

SYSTEM_PROMPT = """Ти допомагаєш власнику маленького українського магазину 3D-друку в Telegram (Mini App) поповнювати каталог.
Магазин друкує на FDM-принтері пластиком PLA (є й світний у темряві), продає по Україні, доставка Новою Поштою.
Покупці — переважно діти, підлітки й молодь: іграшки, антистреси, брелки, дрібні подарунки, корисні речі для дому й гаджетів.

Твоє завдання — знайти в інтернеті моделі для 3D-друку, які добре продаватимуться саме в цьому магазині, і підготувати
для кожної картку товару так, ніби її написав власник. Шукай на MakerWorld, Printables, Thingiverse, Cults3D:
популярні моделі (багато завантажень, лайків, друків), тренди, сезонні речі.

Вимоги до вибору:
- модель реально друкується на звичайному FDM без складного складання і без фарбування;
- собівартість і час друку дозволяють продавати за ціною, близькою до цін магазину;
- товар не повторює те, що вже є в каталозі, і не є вже запропонованим раніше;
- різноманітність: не всі товари з однієї категорії, якщо власник не просив інакше.

Ліцензії не фільтруй: власник за потреби купить комерційну ліцензію. Але для кожного товару чесно напиши в полі license,
яка ліцензія на сторінці моделі і чи дозволено продавати друк. Якщо не дозволено або не зрозуміло — так і напиши,
що для продажу треба купити комерційну ліцензію чи домовитися з автором.

Картка товару:
- пиши українською, у тому ж стилі, тоні й довжині, що й наявні товари магазину (приклади нижче);
- назва коротка й зрозуміла покупцю, без артикулів і англійських хвостів, якщо їх немає у прикладах;
- ціну орієнтуй на ціни схожих товарів у каталозі;
- stl_link — сторінка завантаження файлів цієї самої моделі (лише якщо реально бачив її); не вигадуй посилань;
- image_urls — прямі посилання (https) на фото й анімовані GIF, лише ті, що ти реально бачив; інакше порожній список. Файли .gif і .webp теж підходять.
  Збирай якомога більше різних фото, до 10 на товар, щоб власник мав із чого вибрати: галерея моделі (og:image), а також фото
  з відгуків і розділів «Prints / Makes / Made it», коментарів зі світлинами — реальні відбитки часто виглядають краще за рендери.
  Віддавай перевагу чітким фото готової речі, різним кутам і кольорам; без дублікатів, логотипів, скриншотів слайсера й аватарок.
- variants — якщо в моделі є кілька розмірів чи комплектацій, дай 2–10 позицій і підпиши кожну (напр. «10 см», «15 см», «З кільцем»)
  з окремою ціною; якщо розмір один — порожній список і одна ціна в price (тоді price — найнижча ціна позицій).

Чого НЕ пропонувати ніколи (магазин це не продає): усе демонічне, окультне й магічне (демони, дияволи, чаклуни, відьми, ритуали,
пентаграми, таро), тематику Гелловіну й похмуре (черепи, скелети, вампіри, зомбі, привиди, кров, жахи), брудне, грубе чи для
дорослих. Такі моделі пропускай, навіть якщо вони дуже популярні. Обирай світле, миле, кумедне, корисне й подарункове.

Мова: ЗАВЖДИ українська. Усі текстові поля (name, mat, description, note, reason, license, source_title-пояснення) пиши українською,
навіть якщо сторінка моделі англійською: описи, ліцензії й причини перекладай. Назви-бренди й імена авторів можна лишати латиницею.

Коли закінчиш пошук, виклич інструмент submit_products один раз з усіма товарами. Не пиши окремого звіту текстом."""


def _catalog_brief() -> str:
    categories = [c for c in get_all_categories() if c.get("id")]
    cat_lines = "\n".join(f"- {c['id']}: {c.get('emoji', '')} {c.get('name', '')}" for c in categories)
    products = []
    for p in get_admin_products():
        if p.get("status") == "archive":
            continue
        item = {k: p[k] for k in ("name", "cat", "mat", "price", "oldPrice", "description", "note") if p.get(k)}
        products.append(item)
    return (
        "Категорії магазину (у полі cat використовуй id):\n"
        f"{cat_lines}\n\n"
        "Наявні товари магазину (стиль і ціни для орієнтиру; їх не пропонуй):\n"
        f"{json.dumps(products, ensure_ascii=False, indent=1)}"
    )


def build_request_text(count: int, focus: str, known: list[str]) -> str:
    parts = [f"Знайди {count} нових товарів для магазину."]
    if focus:
        parts.append(f"Побажання власника: {focus}")
    if known:
        parts.append("Ці назви вже пропонувалися раніше, їх і дуже схожі не повторюй:\n" + "\n".join(f"- {n}" for n in known[-150:]))
    return "\n\n".join(parts)


def _make_client():
    import anthropic

    return anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=600.0)


PHOTOS_TOOL = {
    "name": "submit_photos",
    "description": "Зберегти знайдені фото моделі. Викликати один раз у кінці.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["image_urls"],
        "properties": {
            "image_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Прямі https-посилання на фото (jpg/png/webp/gif), до 10, без дублікатів",
            }
        },
    },
}

PHOTOS_PROMPT = """Ти шукаєш фото для картки товару в магазині 3D-друку. Дана конкретна модель (посилання на сторінку).
Відкрий її сторінку й зберіть якомога більше різних якісних фото готової речі, до 10 штук:
- галерея моделі (основні фото, різні кути й кольори);
- фото з відгуків, коментарів і розділів «Prints / Makes / Made it» — реальні відбитки людей часто кращі за рендери;
- за потреби пошукай ту саму модель на інших сайтах (MakerWorld, Printables, Thingiverse, Cults3D).
Потрібні прямі https-посилання на файли зображень (jpg, png, webp, gif), які ти реально бачив у коді сторінки. Нічого не вигадуй.
Без логотипів, аватарок, скриншотів слайсера, дублікатів і фото іншої моделі. Не бери нічого демонічного, магічного, про Гелловін чи брудного.
Коли закінчиш, виклич submit_photos один раз. Не пиши окремого звіту текстом."""


def _web_tools(searches: int, fetches: int) -> list[dict]:
    return [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": searches},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": fetches},
    ]


async def _converse(client, system: list[dict], request_text: str, tools: list[dict], submit_name: str) -> dict:
    """Діалог із Claude, доки він не викличе submit-інструмент; повертає його вхідні дані."""
    messages = [{"role": "user", "content": request_text}]
    for _ in range(MAX_CONTINUATIONS):
        response = await client.beta.messages.create(
            model=config.AI_SUGGESTIONS_MODEL,
            max_tokens=32000,
            system=system,
            messages=messages,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == submit_name:
                return block.input or {}
        if response.stop_reason == "pause_turn":
            # Серверний цикл пошуку дійшов до ліміту кроків — продовжуємо з того ж місця
            messages = [messages[0], {"role": "assistant", "content": response.content}]
            continue
        if response.stop_reason == "refusal":
            raise RuntimeError("ШІ відмовився виконати запит")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Відповідь ШІ обрізалася; спробуй менше товарів за раз")
        raise RuntimeError("ШІ не повернув жодного результату")
    raise RuntimeError("Пошук тривав занадто довго; спробуй ще раз")


async def ask_claude(client, request_text: str, catalog_brief: str) -> list[dict]:
    """Пошук моделей і картки товарів: список сирих товарів з інструмента submit_products."""
    system = [
        {"type": "text", "text": SYSTEM_PROMPT},
        {"type": "text", "text": catalog_brief, "cache_control": {"type": "ephemeral"}},
    ]
    result = await _converse(client, system, request_text, _web_tools(15, 25) + [SUBMIT_TOOL], "submit_products")
    items = result.get("items")
    return items if isinstance(items, list) else []


async def ask_photos(client, request_text: str) -> list[str]:
    """Пошук додаткових фото вже знайденої моделі."""
    system = [{"type": "text", "text": PHOTOS_PROMPT}]
    result = await _converse(client, system, request_text, _web_tools(8, 12) + [PHOTOS_TOOL], "submit_photos")
    urls = result.get("image_urls")
    return urls if isinstance(urls, list) else []


async def _is_public_host(url: str) -> bool:
    """SSRF-захист: URL приходять із веб-контенту, тож хост має резолвитися лише в публічні адреси."""
    host = urlparse(url).hostname
    if not host:
        return False
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addrs = {info[4][0].split("%")[0] for info in infos}
    return bool(addrs) and all(ipaddress.ip_address(a).is_global for a in addrs)


async def _url_ok(session, url: str) -> bool:
    """Чи віддає посилання зображення. Відмову 403/429 (захист від хотлінку) не вважаємо поломкою.

    Редіректи не відстежуємо (кожен крок довелося б перевіряти на SSRF): 3xx вважаємо живим посиланням,
    а недоступне в браузері фото адмінка однаково прибере сама (onerror).
    """
    if not await _is_public_host(url):
        return False
    try:
        async with session.get(url, headers={"Range": "bytes=0-0", "User-Agent": "Mozilla/5.0"}, allow_redirects=False) as r:
            if r.status in (403, 429) or 300 <= r.status < 400:
                return True
            if r.status >= 400:
                return False
            ctype = (r.headers.get("Content-Type") or "").lower()
            return ctype.startswith("image/") or ctype.startswith("application/octet-stream")
    except Exception:
        return False


async def verify_images(urls: list[str]) -> list[str]:
    """Відкидає мертві посилання на фото (ШІ інколи вигадує URL); порядок зберігається."""
    if not urls:
        return []
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=VERIFY_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        flags = await asyncio.gather(*(_url_ok(session, u) for u in urls))
    return [u for u, ok in zip(urls, flags) if ok]


async def generate(count: int, focus: str, client=None) -> int:
    """Один пошук: питає Claude, нормалізує й зберігає пропозиції. Повертає кількість доданих."""
    await run_db(reject_blocked)
    brief = await run_db(_catalog_brief)
    known = await run_db(_known_names)
    existing = {p.get("name", "").strip().lower() for p in await run_db(get_admin_products)}
    category_ids = [c["id"] for c in await run_db(get_all_categories)]
    raw_items = await ask_claude(client or _make_client(), build_request_text(count, focus, known), brief)
    seen = existing | {n.strip().lower() for n in known}
    items = []
    for raw in raw_items:
        item = normalize_item(raw, category_ids)
        if item and item["name"].lower() not in seen:
            seen.add(item["name"].lower())
            item["images"] = await verify_images(item["images"])
            items.append(item)
    return await run_db(save_items, items)


# ── Додаткові фото для наявних пропозицій ───────────────────────────

def get_suggestion(suggestion_id: int) -> dict | None:
    conn = db_connect()
    try:
        row = conn.execute(_sql(f"SELECT {_COLUMNS} FROM ai_suggestions WHERE id = ?"), (int(suggestion_id),)).fetchone()
    finally:
        conn.close()
    return _row_to_item(row) if row else None


def set_images(suggestion_id: int, images: list[str]) -> None:
    conn = db_connect()
    try:
        conn.execute(
            _sql("UPDATE ai_suggestions SET images = ? WHERE id = ?"),
            (json.dumps(images[:IMAGES_MAX]), int(suggestion_id)),
        )
        conn.commit()
    finally:
        conn.close()


def reject_blocked() -> int:
    """Нові пропозиції із чорного списку тем (з минулих пошуків) переносить у «Відхилені»."""
    bad = [
        it["id"]
        for it in list_suggestions()
        if it["status"] == "new"
        and blocked_match(
            it["name"], it["product"].get("description"), it["product"].get("note"),
            it["sourceTitle"], it["product"].get("mat"),
        )
    ]
    if not bad:
        return 0
    conn = db_connect()
    try:
        for sid in bad:
            conn.execute(_sql("UPDATE ai_suggestions SET status = 'rejected' WHERE id = ?"), (sid,))
        conn.commit()
    finally:
        conn.close()
    return len(bad)


def photo_targets(ids=None) -> list[int]:
    """Пропозиції, яким бракує фото: вказані id або всі нові з менш ніж IMAGES_MAX фото."""
    wanted = {int(i) for i in ids or []}
    return [
        it["id"]
        for it in list_suggestions()
        if (it["id"] in wanted if wanted else it["status"] == "new") and len(it["images"]) < IMAGES_MAX
    ]


async def enrich_photos(suggestion_id: int, client=None) -> int:
    """Шукає додаткові фото (галерея, відгуки) для однієї пропозиції. Повертає, скільки нових додано."""
    item = await run_db(get_suggestion, suggestion_id)
    if not item:
        return 0
    have = list(item["images"])
    lines = [
        f"Товар: {item['name']}",
        f"Сторінка моделі: {item['sourceUrl'] or 'невідома, знайди модель за назвою'}",
        f"Назва моделі на сайті: {item['sourceTitle']}" if item["sourceTitle"] else "",
        f"Уже є {len(have)} фото (їх не повторюй), потрібно разом до {IMAGES_MAX}.",
    ]
    found = await ask_photos(client or _make_client(), "\n".join(line for line in lines if line))
    fresh = [u for u in _clean_images(found) if u not in have][: IMAGES_MAX - len(have)]
    fresh = await verify_images(fresh)
    if fresh:
        await run_db(set_images, suggestion_id, have + fresh)
    return len(fresh)


def job_state() -> dict:
    return dict(_job)


def _finish_job() -> None:
    _job["running"] = False
    _job["finishedAt"] = _now_iso()


async def _run_job(count: int, focus: str, client) -> None:
    try:
        _job["added"] = await generate(count, focus, client)
    except Exception as e:  # помилку показуємо в адмінці, а не губимо у фоні
        logger.exception("AI suggestions failed")
        _job["error"] = str(e) or e.__class__.__name__
    finally:
        _finish_job()


async def _run_photo_job(ids: list[int], client) -> None:
    failures = 0
    try:
        client = client or _make_client()
        for sid in ids:
            try:
                _job["added"] += await enrich_photos(sid, client)
            except Exception as e:  # одна невдача не зупиняє решту
                failures += 1
                logger.exception("AI photo search failed for %s", sid)
                _job["error"] = str(e) or e.__class__.__name__
        if failures < len(ids):
            _job["error"] = ""
    except Exception as e:
        _job["error"] = str(e) or e.__class__.__name__
    finally:
        _finish_job()


def _begin_job(kind: str) -> None:
    _job.update(running=True, startedAt=_now_iso(), finishedAt="", added=0, error="", kind=kind)


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def start_generation(data: dict, client=None) -> dict:
    """Запускає фоновий пошук (лише один одночасно). Викликати з event loop."""
    if client is None and not is_enabled():
        return {"ok": False, "error": "Не задано ANTHROPIC_API_KEY на сервері"}
    if _job["running"]:
        return {"ok": False, "error": "Пошук уже йде"}
    try:
        count = int(data.get("count") or COUNT_DEFAULT)
    except (TypeError, ValueError):
        count = COUNT_DEFAULT
    count = max(COUNT_MIN, min(COUNT_MAX, count))
    focus = _text(data.get("focus"), FOCUS_MAX)
    _begin_job("products")
    _spawn(_run_job(count, focus, client))
    return {"ok": True, "job": job_state()}


def start_photo_search(data: dict, client=None) -> dict:
    """Фоновий пошук додаткових фото: для `ids` або для всіх нових пропозицій з неповною галереєю."""
    if client is None and not is_enabled():
        return {"ok": False, "error": "Не задано ANTHROPIC_API_KEY на сервері"}
    if _job["running"]:
        return {"ok": False, "error": "Пошук уже йде"}
    reject_blocked()
    ids = data.get("ids")
    targets = photo_targets(ids if isinstance(ids, list) else None)
    if not targets:
        return {"ok": False, "error": "Усі пропозиції вже мають повну галерею фото"}
    _begin_job("photos")
    _spawn(_run_photo_job(targets, client))
    return {"ok": True, "job": job_state(), "count": len(targets)}
