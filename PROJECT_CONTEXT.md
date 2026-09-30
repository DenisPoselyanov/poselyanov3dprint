# PROJECT_CONTEXT.md — Poselyanov 3D Print Mini App

> Актуальний контекст проєкту станом на поточний код `main`.
> Цей файл використовувати як стартовий бриф у нових чатах.

> 🎨 **Йде редизайн магазину й адмінки за прототипом.** План, специфікація екранів, вибрані варіанти
> і потрібні зміни бекенду — у [REDESIGN.md](REDESIGN.md); код прототипу — у [docs/prototype/](docs/prototype/).
> Цей файл описує **поточний** стан коду; нове з'являтиметься тут після кожного етапу.

---

## 🔗 Репозиторій

- **GitHub:** https://github.com/DenisPoselyanov/poselyanov3dprint
- **Mini App (GitHub Pages):** https://denisposelyanov.github.io/poselyanov3dprint/
- **Основні файли:**
  - `index.html` — фронтенд Mini App
  - `bot.py` — Telegram-бот + HTTP API (`aiohttp`)
  - `routes/setup.py` — реєстрація HTTP-маршрутів
  - `services/` — бізнес-логіка: `orders`, `pricing`, `promotions`, `coupons`, `users`, `settings`, `notifications`, `stats`, `validation`, `db_utils` (схема й міграції),
    `collections` (добірки на головній), `hits` (топ продажів), `events` (події вітрини, популярні пошуки)
  - `handlers/` — реєстрація команд бота, rate limit, обробка помилок
  - `admin-panel.html` — адмінка
  - `config.py` — централізована конфігурація з ENV
  - `auth.py` — автентифікація (initData), CORS, авторизація адміна
  - `db_core.py` — підключення до БД (SQLite / PostgreSQL)
  - `catalog_store.py` — каталог товарів (JSON-файли або DB)
  - `rich_messages.py` — шаблони повідомлень бота
  - `security_utils.py` — валідація URL, STL-посилань
  - `products.json`, `custom_products.json`, `categories.json`, `filaments.json` — каталоги/довідники

---

## 📱 Що це за проєкт

Telegram Mini App для продажу 3D-друкованих товарів.

- Бот: `@poselyanov3dprint_bot`
- Замовлення оформлюються з веб-каталогу та потрапляють у БД і в Telegram-чат замовлень.
- Адмін керує товарами, категоріями, філаментами і замовленнями через WebApp-панель (`/admin`).

---

## 🧱 Поточна структура та стек

- **Frontend:** один файл `index.html` (HTML/CSS/JS без фреймворків). Після етапу 1 редизайну:
  - дизайн-система: токени (`--bg`, `--surface`, `--ink`, `--blue`…) + аліаси старих змінних для ще не перероблених картки товару й кошика;
    тема — `data-theme` на `<html>` за `Telegram.WebApp.colorScheme`, поза Telegram — за системою;
  - модуль «ВІТРИНА» в JS: SVG-іконки (`ICON_PATHS`, `ic()`), картки (`cardHtml`, `controlHtml` — «+»/степпер оновлюються на місці через `data-ctl`),
    головна (`renderHome`), список категорії/добірки (`renderList`, сортування), пошук (`searchProducts` — синоніми + транслітерація),
    Обране (`FAVS`, Telegram CloudStorage `fav_v1` + localStorage), тости (`showToast` замість `alert`), BackButton (`backAction`), HapticFeedback (`haptic`);
  - нижнє меню: Каталог · Акції · Обране · Кошик (`switchPage(page)`); «Замовлення» з'явиться на етапі 3;
  - localStorage: `cart_v1`, `fav_v1`, `recent_v1` (нещодавно переглянуті), `search_recent_v1`
- **Backend bot:** `python-telegram-bot` (async)
- **HTTP API:** `aiohttp` на порту `8080`
- **DB:** SQLite (`users.db`) локально **або** PostgreSQL/Supabase у продакшені
- **Каталог:** JSON-файли (за замовчуванням) **або** таблиці в БД (`CATALOG_BACKEND=postgres`)
- **Медіа:** Cloudinary (з локальною оптимізацією: GIF/WebP анімація, JPEG/WebP стиск)

---

## ⚙️ Важливі ENV змінні

```env
BOT_TOKEN=...
OWNER_ID=718746623
ORDERS_CHAT_ID=-1003739884073
VALIDATE_INIT_DATA=true          # false для локальної розробки
PORT=8080
WEBAPP_URL=https://denisposelyanov.github.io/poselyanov3dprint/
API_PUBLIC_URL=                  # публічна URL бекенду (для ngrok/prod)
ADMIN_WEBAPP_URL=https://...     # URL адмінки (має бути https://)
CORS_ORIGINS=https://denisposelyanov.github.io,http://localhost:8080,...

# БД
DB_BACKEND=sqlite                # або postgres
DB_FILE=users.db                 # тільки для sqlite
DATABASE_URL=                    # для postgres (або SUPABASE_DB_URL)

# Каталог
CATALOG_BACKEND=sqlite           # або postgres/supabase
PRODUCTS_FILE=products.json
CUSTOM_PRODUCTS_FILE=custom_products.json
FILAMENTS_FILE=filaments.json
CATEGORIES_FILE=categories.json

# Cloudinary
CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...

# Misc
LOCAL_DEV_MODE=false
INIT_DATA_MAX_AGE_SEC=86400
BROADCAST_DELAY_SEC=0.05
PROMOTION_ENABLED=true
MAX_UPLOAD_BYTES=26214400        # 25 MB
```

### Пояснення
- `OWNER_ID` — **особистий Telegram user id адміна** (позитивний).
- `ORDERS_CHAT_ID` — **чат/канал для замовлень** (часто `-100...`).
  - Розділено навмисно, щоб адмін-доступ і канал замовлень не конфліктували.
- `VALIDATE_INIT_DATA=false` — дозволяє тестування без реального Telegram initData.
- `CATALOG_BACKEND=postgres` — каталог зберігається в БД; при `sqlite` — в JSON-файлах.

---

## 🗄️ Актуальна БД

Схема створюється і мігрується на старті (`services/db_utils.init_db`, `ALTER TABLE … IF NOT EXISTS`).

### `users`
- `id`, `name`, `username`, `blocked`, `joined_at`,
  `start_count`, `last_start_at`, `activity_count`, `last_seen_at`

### `orders`
- `id`, `user_id`, `username`, `first_name`, `total_price`, `comment`,
  `gift_product_name`, `status`, `coupon_code`, `discount_amount`, `promotion_discount`,
  `price_pending`, `ordered_at`
- `price_pending` = 1 → є товари з договірною ціною, сума буде уточнена

### `order_items`
- `id`, `order_id`, `product_id`, `product_name`, `price`, `quantity`,
  `filament`, `is_contract_price`
- `is_contract_price` = 1 → позиція з договірною ціною (price=0 до уточнення)

### `order_idempotency`
- `user_id`, `idempotency_key`, `order_id`, `is_new`, `created_at` — захист від подвійного оформлення

### `coupons`
- `code`, `type` (`percent`/`fixed`), `value`, `min_order`, `uses_max`, `uses_count`,
  `one_per_user`, `active`, `expires_at`, `personal_user_id`,
  `stackable` (сумується з акцією), `require_channel_sub`, `sub_channel_id` (канал для перевірки підписки)

### `coupon_uses`
- `id`, `code`, `user_id`, `order_id`, `used_at`, `status`

### `coupon_allowed_users`
- `code`, `user_id` — купон доступний лише цим Telegram ID

### `collections` / `collection_products`
- `collections`: `id`, `name`, `description`, `icon`, `color`, `sort_order`, `active`, `mode` (`auto`/`manual`), `rule` (JSON для `auto`)
- `collection_products`: `collection_id`, `product_id`, `sort_order` — склад ручних добірок
- Сід — 7 авто-добірок (Новинки, До 100 ₴, Світяться вночі, Подарунок до 150 ₴, Зі знижкою, Хіти, Від 300 ₴). Керування з адмінки — етап 5; до того лише в БД

### `events`
- `id`, `type` (зараз лише `search`), `user_id`, `product_id`, `query`, `value` (для пошуку — кількість результатів), `created_at`

### `promotions`
- `id`, `type` (`order_percent`/`order_fixed`/`gift_threshold`/`info`), `title`, `subtitle`, `emoji`, `badge`,
  `active`, `priority`, `value`, `min_order`, `category`, `min_items`, `max_gift_price`, `starts_at`, `ends_at`

### `app_settings`
- `key`, `value`, `updated_at` — налаштування з адмінки

### `filament_colors`
- `id`, `name`, `hex`, `available`

### Каталогові таблиці (тільки при `CATALOG_BACKEND=postgres`)
- `categories` — `id`, `name`, `emoji`, `badge_class`, `sort_order`, `active`, `quick_slot`
- `products` — повна структура товару з усіма полями
- `filaments` — `id`, `name`, `hex`, `available`, `sort_order`

---

## 📦 Структура товару (продукт)

```json
{
  "id": 1,
  "cat": "toy",
  "name": "Назва",
  "emoji": "🎲",
  "mat": "PLA",
  "price": 250,
  "oldPrice": 300,
  "photos": ["https://..."],
  "hot": false,
  "pinned": false,
  "gift": false,
  "contractPrice": false,
  "filamentChoice": true,
  "luminousFilamentChoice": false,
  "stlLink": "",
  "custom_fields": [],
  "createdAt": "2026-09-30T12:00:00+00:00"
}
```

- `contractPrice: true` → ціна договірна (price=0 в БД, в замовленні `is_contract_price=1`)
- `gift: true` → товар-подарунок (може бути вибраний як гіфт у кошику)
- `filamentChoice` — чи показувати вибір кольору філаменту
- `luminousFilamentChoice` — чи показувати вибір люмінесцентного філаменту (на вітрині — бейдж «Світиться» і добірка «Світяться вночі»)
- `createdAt` — коли товар додано (БД: `products.created_at`); бейдж «Новинка» і добірка «Новинки». У старих товарів дати немає

---

## 🏷️ Статуси замовлень

| Код | Емодзі | Назва |
|-----|--------|-------|
| `new` | 🕐 | Очікує підтвердження |
| `confirmed` | ✅ | Підтверджено |
| `cancelled` | ❌ | Скасовано |
| `draft` | 📝 | Під питанням |

> Редизайн (етап 3) додає `printing`, `ready`, `shipped`, `delivered` — див. [REDESIGN.md](REDESIGN.md#52-замовлення).

---

## 🛍️ Потік замовлення

1. Frontend формує кошик (`items`, `coupon_code`, `total_price`, `comment`, `gift`).
2. `POST /order` у `bot.py`:
   - валідація initData (або fallback при `VALIDATE_INIT_DATA=false`),
   - серверний перерахунок суми (включаючи contract-price товари),
   - перевірка купона,
   - запис у БД (`orders` + `order_items`).
3. Клієнту в бот приходить підтвердження (`rich_messages.build_client_order_confirmation`).
4. У чат замовлень (`ORDERS_CHAT_ID`) летить адмін-повідомлення з кнопками статусу.

---

## 🧾 Формат повідомлень

- Сума з купоном: `💰 Разом: 500 → 450 ₴`
- Якщо є contract-price товари: додається рядок `💬 Є позиції з договірною ціною`
- Всі шаблони в `rich_messages.py`:
  - `build_admin_order_notification` — повідомлення в канал замовлень
  - `build_client_order_confirmation` — підтвердження клієнту
  - `build_client_price_quote` — цінова пропозиція для договірних товарів
  - `build_order_history` — список замовлень (`/history`)
  - `build_order_status` — статус одного замовлення (`/status`)

---

## 🔐 Доступ та адмінка

- Команда `/admin` доступна тільки коли `from_user.id == OWNER_ID`.
- Admin API орієнтується на `OWNER_ID` при ввімкненій перевірці `VALIDATE_INIT_DATA=true`.
- `GET /admin/panel` віддає `admin-panel.html`.
- `ADMIN_WEBAPP_URL` має починатися з `https://` — інакше кнопка не відображається.
- ngrok URL автоматично отримує `?bypass=admin`.

---

## 🌐 HTTP маршрути (повний список)

Реєстрація — у `routes/setup.py`.

### Публічні
- `POST /order` — створити замовлення
- `POST /check_coupon` — валідація купона
- `GET /api/promotions` — активні акції для магазину
- `GET /api/hits?days=90&limit=12` — топ продажів з `order_items` (статуси `confirmed…delivered`), кеш 10 хв
- `GET /api/collections` — активні добірки (`product_ids` для ручних, `rule` для авто — склад рахує фронт)
- `GET /api/search/popular?days=30&limit=5` — «Шукають найчастіше» (лише запити з результатами; при малій статистиці — заглушка)
- `POST /api/events` — подія з вітрини `{type: "search", query, results}`; потрібен initData, ліміт 30/хв на користувача
- `GET /health` — статус сервісу + `validate_init_data`, `catalog_backend`
- `GET /` — фронтенд Mini App
- `GET /{path:.*}` — статичні файли (whitelist з `STATIC_ALLOWED_FILES`)

### Адмін API (`OWNER_ID`-only при `VALIDATE_INIT_DATA=true`)
- `GET /admin/panel` — UI адмінки
- `GET /api/products` — список товарів
- `GET /api/products/{id}` — один товар
- `POST /api/products` — створити товар
- `PUT /api/products/{id}` — оновити товар
- `DELETE /api/products/{id}` — видалити товар
- `GET /api/categories` — список категорій
- `POST /api/categories` — створити категорію
- `PUT /api/categories/{id}` — оновити категорію
- `DELETE /api/categories/{id}` — видалити категорію
- `GET /api/filaments` — список філаментів
- `PUT /api/filaments/{id}` — оновити філамент
- `GET /api/orders` — список замовлень
- `GET /api/orders/{id}` — одне замовлення
- `PUT /api/orders/{id}/pricing` — оновити ціну (для contract-price)
- `PUT /api/orders/{id}/status` — змінити статус
- `DELETE /api/orders/{id}` — видалити замовлення
- `GET/POST /api/coupons`, `PUT/DELETE /api/coupons/{code}` — купони
- `POST /api/coupons/{code}/allowed-users`, `DELETE /api/coupons/{code}/allowed-users/{user_id}` — доступ за Telegram ID
- `GET/POST /api/admin/promotions`, `PUT/DELETE /api/admin/promotions/{id}` — акції
- `GET /api/admin/users` — користувачі бота з лічильниками
- `GET/PUT /api/admin/settings` — налаштування
- `POST /api/upload-photo` — завантажити фото (multipart або base64)
- `POST /api/upload-photo-url` — завантажити фото з URL

---

## 🤖 Команди бота

### Адмін
- `/admin` — відкрити адмінку (WebApp)
- `/stats` — статистика (користувачі, замовлення, виручка)
- `/broadcast` — масова розсилка
- `/coupon` — керування купонами
- `/reload_products` — перезавантажити кеш каталогу
- `/myid` — показати свій Telegram ID

### Користувач
- `/start` — привітання + кнопка відкрити магазин
- `/catalog` — відкрити каталог (Mini App)
- `/history` — список своїх замовлень
- `/mycoupons` — активні купони
- `/sales` — поточні акції
- `/status` — статус останнього замовлення
- `/contact` — зв'язатися з адміном

---

## 🖼️ Оптимізація зображень

При завантаженні через `/api/upload-photo` або `/api/upload-photo-url`:
- **Анімовані GIF/WebP:** зменшення якщо > 45 MP total, обрізання кадрів (макс. 80)
- **Статичні:** стиск до JPEG (85%) або WebP (з alpha), resize до 1200px ширини якщо > 300 KB

---

## 📌 Нотатки для наступних змін

1. При зміні логіки повідомлень синхронізувати формати в `rich_messages.py`:
   - клієнтське підтвердження
   - повідомлення в канал/чат замовлень
2. Не змішувати `OWNER_ID` і `ORDERS_CHAT_ID`.
3. Перед деплоєм перевіряти права бота в каналі (`Post Messages`).
4. Після зміни `.env` завжди перезапускати процес бота.
5. При `CATALOG_BACKEND=postgres` — товари/категорії читаються з БД, а не з JSON.
6. `ADMIN_WEBAPP_URL` **обов'язково** має бути `https://` — без цього кнопка в `/admin` не з'являється.
7. При додаванні нового поля до товару — оновити `_row_to_product()` в `catalog_store.py`.

---

## ✅ Поточний стан

Проєкт робочий: замовлення з Mini App зберігаються в БД, купони й акції застосовуються, повідомлення відправляються в окремий чат замовлень і клієнту в бот, адмінка керує товарами/категоріями/філаментами/купонами/акціями через WebApp. Підтримуються два бекенди БД (SQLite / PostgreSQL) та два бекенди каталогу (JSON / DB).

**Далі:** редизайн у 5 етапів за [REDESIGN.md](REDESIGN.md). ✅ Етап 1 (дизайн-система, головна, список, пошук, Обране) — готовий; наступний — етап 2 (картка товару, вкладка «Акції», відгуки). Потрібен ключ API Нової Пошти до етапу 3.
