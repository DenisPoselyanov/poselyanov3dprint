"""Безпечний локальний перегляд адмінки: SQLite у temp, JSON-каталог, фейковий токен бота, без polling.

Запуск: python scripts/preview_server.py, далі http://admin.localhost:8081/admin/panel
(не localhost: для localhost адмінка стукає на :8080). Нічого не пише в прод: .env не діє,
бот-повідомлення йдуть з фейковим токеном і завершуються помилкою (client_notified=false).
"""
import os, shutil, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT); sys.path.insert(0, ROOT)
db = os.path.join(tempfile.gettempdir(), "preview_admin.db")
if os.path.exists(db): os.remove(db)
# Каталог теж у temp-копіях: правки товарів у перегляді не чіпають products.json у репозиторії.
cat_dir = os.path.join(tempfile.gettempdir(), "preview_catalog")
shutil.rmtree(cat_dir, ignore_errors=True); os.makedirs(cat_dir)
for _f in ("products.json", "custom_products.json", "categories.json", "filaments.json"):
    shutil.copy(os.path.join(ROOT, _f), os.path.join(cat_dir, _f))
os.environ.update(DB_BACKEND="sqlite", CATALOG_BACKEND="json", DATABASE_URL="", SUPABASE_DB_URL="",
                  VALIDATE_INIT_DATA="false", LOCAL_DEV_MODE="true", DB_FILE=db, ALLOW_LOCAL_NETWORK="true",
                  PRODUCTS_FILE=os.path.join(cat_dir, "products.json"), CUSTOM_PRODUCTS_FILE=os.path.join(cat_dir, "custom_products.json"),
                  CATEGORIES_FILE=os.path.join(cat_dir, "categories.json"), FILAMENTS_FILE=os.path.join(cat_dir, "filaments.json"),
                  BOT_TOKEN="123456:FAKE-preview-token", NOVAPOSHTA_API_KEY="", ORDERS_CHAT_ID="1", OWNER_ID="777")
import bot
from aiohttp import web
from routes.setup import register_http_routes
bot.init_db()
import services.orders as orders
from db_core import db_connect
_c = db_connect(); _c.execute('CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, stl_link TEXT)'); _c.commit(); _c.close()  # у проді products є в Postgres

def mk(uid, name, tag, items, total, status, **extra):
    oid, _ = orders.save_order(uid, tag, name, items, total, extra.pop("comment", ""), extra=extra or None)
    if status != "new":
        orders.update_order_status(oid, status)
    return oid

it = lambda n, p=80, q=1, **k: {"product_id": 1, "product_name": n, "price": p, "quantity": q, "filament": k.get("fl", "Чорний"), **k}
mk(101, "Олена", "@olena", [it("Брелок Kuromi", 80, 2, personalization="Олена")], 160, "new", delivery_method="np", city="Київ", branch="№12", recipient_name="Олена К.", phone="+380501112233", pay_method="card")
mk(102, "Ігор", "@igor", [it("Підставка під телефон", 150)], 150, "draft", delivery_method="self")
mk(103, "Марія", "@maria", [it("Органайзер"), it("Брелок", 80, 3)], 400, "confirmed", delivery_method="np", city="Львів", branch="№3", recipient_name="Марія", phone="+380671112233", pay_method="cod")
mk(104, "Тарас", "@taras", [it("Дракон", 300, 1, fl="Червоний")], 300, "printing", internal_note="клієнт просив матовий")
mk(105, "Софія", "@sofia", [it("Квітка", 120)], 120, "ready", delivery_method="np", city="Одеса", branch="№7")
mk(106, "Данило", "@danylo", [it("Фігурка", 220)], 220, "shipped", ttn="20450000000001")
mk(107, "Ліля", "@lilya", [it("Лампа", 500)], 500, "delivered")
mk(108, "Богдан", "@bohdan", [it("Брелок", 80)], 80, "cancelled")
_c = db_connect()
for _pid, _t in ((1, "product_view"), (1, "product_view"), (1, "add_to_cart"), (2, "product_view")):
    _c.execute("INSERT INTO events (type, user_id, product_id) VALUES (?, ?, ?)", (_t, 900 + _pid, _pid))
_c.commit(); _c.close()
# Відгуки в усіх статусах (з фото й відповіддю) для сторінки «Відгуки»
import json as _json
_c = db_connect()
_c.execute("INSERT OR IGNORE INTO users (id, name, username) VALUES (101, 'Олена К.', '@olena')")
_c.execute("INSERT OR IGNORE INTO users (id, name, username) VALUES (103, 'Марія', '@maria')")
_c.commit(); _c.close()
_ph = lambda n: "https://picsum.photos/seed/rv%d/600/450" % n
for _uid, _pid, _rate, _txt, _photos, _st, _reply in (
    (101, 1, 5, "Дуже гарний брелок, друк чистий, прийшов швидко!", [_ph(1), _ph(2)], "pending", ""),
    (102, 2, 2, "Трохи кривувато надрукувало, очікував краще.", [], "pending", ""),
    (103, 3, 4, "", [_ph(3)], "pending", ""),
    (104, 1, 5, "Дарував подарунок - всім сподобалось. Рекомендую!", [_ph(4), _ph(5), _ph(6)], "approved", "Дякуємо за відгук! Раді, що сподобалось."),
    (105, 2, 4, "Нормально, але чекав довше ніж обіцяли.", [], "approved", ""),
    (106, 4, 3, "Колір трохи інший ніж на фото.", [], "hidden", ""),
):
    _oid, _ = orders.save_order(_uid, "@u", "Тест", [it("Товар %d" % _pid, 100, 1, product_id=_pid)], 100, "")
    orders.update_order_status(_oid, "delivered")
    _c = db_connect()
    _c.execute("INSERT INTO reviews (product_id, order_id, user_id, author_name, rating, text, photos, status, admin_reply) VALUES (?,?,?,?,?,?,?,?,?)",
               (_pid, _oid, _uid, "Клієнт %d" % _uid, _rate, _txt, _json.dumps(_photos), _st, _reply))
    _c.commit(); _c.close()
app = web.Application(client_max_size=30 * 1024 * 1024)
register_http_routes(app, bot)
web.run_app(app, host="127.0.0.1", port=8081, print=None)
