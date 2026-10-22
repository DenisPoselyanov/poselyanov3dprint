# PREDEPLOY.md — перевірка перед деплоєм гілки `redesign` (етапи 1–5)

> Мета: переконатися, що міграції Postgres і оформлення замовлення працюють, **до** того як код потрапить на прод.
> Ключовий ризик: **push у `main` сам деплоїть** (`.github/workflows/ci.yml` → SSH → `deploy/update.sh`, а `deploy_remote.py` теж тягне `main`). Старт сервісу виконує `init_db()` на проді (Supabase), тож міграції йдуть на живу БД. Тому спершу тестуємо на окремій БД, у `main` зливаємо в останню чергу.

## 0. Правила безпеки
- Локальний `.env` = прод Supabase + справжній токен бота. Усі перевірки нижче — з явними перевизначеннями `DATABASE_URL`/`SUPABASE_DB_URL`/`BOT_TOKEN`, ніколи без них.
- Не запускати `bot.main()` (polling) з прод-токеном. Для HTTP-перевірок збирати aiohttp-застосунок через `routes.setup.register_http_routes` + `init_db()` (як `scripts/preview_server.py`), без polling і з фальшивим токеном.
- Перед злиттям у `main` — бекап прод-БД (розд. 4).

## 1. Підготовка: одноразова Postgres-БД
Локально немає ні Postgres, ні Docker. Варіанти (обрати один):
- **A (рекомендовано):** новий безкоштовний Supabase-проєкт «poselyanov-staging» (окрема БД, окремий `DATABASE_URL`). Найближче до прод-середовища.
- **B:** локальний PostgreSQL через `winget install PostgreSQL.PostgreSQL`, БД `staging`.
Потрібно: рядок підключення `STAGING_DATABASE_URL`. Прод-БД не чіпаємо.

## 2. Перевірка міграцій Postgres (автоматично + вручну)
Env для всіх команд: `DB_BACKEND=postgres CATALOG_BACKEND=postgres DATABASE_URL=$STAGING_DATABASE_URL SUPABASE_DB_URL=$STAGING_DATABASE_URL BOT_TOKEN=123:fake VALIDATE_INIT_DATA=false LOCAL_DEV_MODE=true`.

1. **Чиста БД.** Виконати `scripts/supabase_schema.sql` (свіжа схема), потім `init_db()` двічі поспіль → друге виконання без помилок (ідемпотентність).
2. **БД у «прод-стані».** Створити схему зі стану `main` (`git show main:scripts/supabase_schema.sql`), засіяти кілька товарів/замовлень/користувачів/відгуків/купонів/акцій, потім запустити `init_db()` з коду `redesign`. Перевірити, що додалися й заповнились:
   - `products.status` (бекфіл з `active`), `filaments.glow` (бекфіл `luminous%`), `promotions.accent/show_timer`, `order_items.printed`, `orders.*` (доставка, ТТН, нотатка, `promotion_id`…), таблиці `collections`, `collection_products`, `reviews`, `events`, `broadcasts`;
   - існуючі дані не зіпсовані: кількість товарів/замовлень до і після однакова; старі `active=false` → `status='archive'`.
3. **Нові SQL-запити під Postgres** (їх не покривають SQLite-тести) — прогнати кожен ендпоінт на staging-БД, шукаючи помилки синтаксису/типів:
   - Товари: `GET /api/admin/products`, `PUT …/status`, `PATCH`, `POST /api/products/bulk`, duplicate, статистика (`product_stats`).
   - Категорії: `PUT /api/categories/order`, слот; Філаменти: POST/DELETE, `glow`.
   - Добірки: `/api/admin/collections*`, membership, `/api/collections`.
   - Відгуки: `/api/admin/reviews*` + `/api/orders/counts`.
   - Акції/Купони: `/api/admin/promotions` (stats), `/api/coupons` (`GROUP BY UPPER(coupon_code)`, `revenue`, `link`).
   - Клієнти: `/api/admin/users?segment=&counts=1`, `users.csv`, `POST …/coupon`.
   - Розсилки: `broadcasts` CRUD, `RETURNING id`, `INTERVAL`, `MAX(CASE…)`, підрахунок отримувачів (з **заглушкою** відправника).
   - Замовлення/Огляд/Черга друку: `/api/orders*`, `/api/admin/overview`, `/api/print-queue`.
   Зручно оформити як скрипт `scripts/predeploy_check.py`, що піднімає app з env вище й дергає всі ці URL з admin-доступом (`LOCAL_DEV_MODE`), падає на будь-якому 5xx.
4. **Pytest на Postgres.** Спробувати `DB_BACKEND=postgres … python -m pytest -q` на staging. Якщо тести жорстко прив'язані до SQLite — зафіксувати, які саме, і покрити їх скриптом з п. 3. Тести створюють/чистять дані: **тільки на staging**.

Критерій: жодного 5xx, міграція ідемпотентна, дані після міграції збігаються.

## 3. Пробне замовлення (змінено `validate_order_payload`, `handle_order`)
Спочатку на staging-застосунку (фальшивий токен → повідомлення «не доставлено» — це очікувано), потім, якщо потрібно, один реальний тест після деплою.

**3.1 Паритет сум (автоматично, без Telegram).** Для кожного сценарію порівняти `getCartPricing()` у `index.html` (браузер) з сервером `validate_order_payload`:
1. звичайний кошик без акцій;
2. `order_percent`; 3. `order_fixed`; 4. `cat_percent` (товари з двох категорій);
5. `gift_threshold` + подарунок; 6. `free_ship` (суми не змінюються, у кошику «безкоштовно · акція»);
7. купон (%/₴) окремо; 8. купон + акція (сумується / не сумується);
9. договірна ціна (`price_pending`); 10. товар з персоналізацією;
11. кілька акцій одночасно — обирається найвигідніша, однаково на клієнті й сервері.
Очікування: `POST /order` повертає 200, а не «Сума не збігається»; збережені `orders.promotion_id`, `coupon_discount`, `promotion_discount`, `total_price` відповідають розрахунку.

**3.2 Наскрізний сценарій у staging-адмінці.** Створити замовлення → у списку з'явилося → «Підтвердити» → черга друку → «надруковано» → `ready` → ТТН + `shipped` → `delivered` → (відгук) → відгук у «Відгуки» → публікація. На кожному кроці відсутність помилок у логах.

**3.3 Реальний пробний заказ після деплою (на проді, 15 хв).** З власного акаунта:
1. відкрити Mini App з Telegram (перевірка WebView: шрифти, нижнє меню, BackButton, прокрутка);
2. додати товар із акцією, оформити замовлення з НП (вручну введені місто/відділення, якщо `NOVAPOSHTA_API_KEY` не задано);
3. у чаті замовлень прийшло повідомлення; у адмінці — нове замовлення, лічильники;
4. провести статуси до `delivered`, переконатися, що клієнт отримує повідомлення в бот;
5. скасувати/видалити пробне замовлення.

## 4. Перед злиттям у `main`
- [ ] Бекап прод-БД Supabase (дамп `pg_dump` або Supabase backup / `deploy/backup.sh`); зберегти.
- [ ] `pytest` зелений (SQLite) і п. 2–3 пройдені на staging.
- [ ] У `ENV_CONTENT` (`deploy_remote.py`) та на сервері: задати/перевірити `BOT_USERNAME`, `NOVAPOSHTA_API_KEY` (за наявності), `UKRPOSHTA_TOKEN` (опційно). Без ключа НП працює текстовим введенням.
- [ ] Затвердити тексти статусних повідомлень (REDESIGN.md §5.2) — власник.
- [ ] План відкату: `git revert`/reset `main` на попередній коміт + `deploy/update.sh`; міграції лише додають колонки/таблиці (`IF NOT EXISTS`), старий код їх ігнорує. Єдине зміниме значення — бекфіл `status`/`glow`; відкат даних не потрібен.
- [ ] Вікно деплою: коли немає активних замовлень; після рестарту `GET /health` → `ok`.

## 5. Деплой і післядеплойна перевірка
1. Злити `redesign` → `main` (PR), CI запустить тести і SSH-деплой. (Або `python deploy_remote.py`, якщо деплоїмо вручну — не обидва підряд.)
2. Стежити за логами сервісу під час першого старту (`journalctl -u poselyanov3dprint -f`): міграції без помилок.
3. `GET /health`, відкрити адмінку, пройти по всіх сторінках (Огляд, Замовлення, Черга, Товари, Категорії, Добірки, Філаменти, Акції, Купони, Клієнти, Розсилки, Відгуки) — консоль без помилок.
4. Пробне замовлення (п. 3.3).
5. **Розсилки не тестувати на реальних клієнтах**: перший тест — сегмент з одним Telegram ID (власний) через «Написати»/розсилку з фільтром, лише після п. 1–4.

## 6. Що лишається поза цією перевіркою
Реальна відправка розсилок у масштабі, flood control, фото через Cloudinary, `tg://` посилання на пристроях, світла тема нових блоків, drag-and-drop на дотику.

## 7. Як запускати `scripts/predeploy_check.py`
Скрипт автоматизує п. 2.1–2.3 і 3.1: міграції (чиста БД і «прод-стан» з `main`), усі ендпоінти етапу 5 та паритет сум 11 сценаріїв. **Тільки на staging-БД**: скрипт сам відмовляється (код виходу 2), якщо `STAGING_DATABASE_URL` порожній або збігається з продом із `.env`/`deploy_remote.py` (URL повністю, host, або project ref Supabase). Паролі в виводі маскуються, Telegram заглушений (фейковий токен), polling не стартує.

PowerShell, з кореня репозиторію (гілка `redesign`):
```powershell
# 1) Рядок підключення ОКРЕМОЇ staging-БД (НЕ прод!). Для Supabase: Project Settings -> Database -> Connection string
$env:STAGING_DATABASE_URL = "postgresql://postgres.<STAGING-REF>:<ПАРОЛЬ>@aws-0-<регіон>.pooler.supabase.com:5432/postgres?sslmode=require"

# 2) Сухий прогін: лише перевірки безпеки й імпорти, до БД не підключається
python scripts/predeploy_check.py all --dry-run

# 3) Повний прогін: скидає таблиці застосунку у staging (--reset), міграції, ендпоінти, паритет
python scripts/predeploy_check.py all --reset --yes-this-is-staging

# Окремо (без --reset БД не руйнується; migrate-* без --reset відмовляються):
python scripts/predeploy_check.py migrate-clean --reset --yes-this-is-staging
python scripts/predeploy_check.py migrate-from-main --reset --yes-this-is-staging
python scripts/predeploy_check.py endpoints --yes-this-is-staging
python scripts/predeploy_check.py order-parity --yes-this-is-staging

# Прибрати змінну після роботи
Remove-Item Env:STAGING_DATABASE_URL
```
- `--reset` робить `DROP TABLE` лише відомих таблиць застосунку й відмовляє, якщо в схемі `public` є сторонні таблиці. Без `--yes-this-is-staging` скрипт у терміналі спитає підтвердження (слово `staging`).
- `endpoints` і `order-parity` пишуть лише рядки з префіксом `predeploy_` і прибирають їх наприкінці (`--keep` лишає для огляду). `order-parity` на час прогону вимикає сторонні акції staging і відновлює їх у `finally`.
- Підсумок у кінці: список PASS/FAIL, код виходу `0` (усе добре), `1` (є FAIL), `2` (відмова за правилами безпеки). Таблиця ендпоінтів показує статус і час у мс.
- Обмеження скрипта: див. `python scripts/predeploy_check.py --help` (внизу). Pytest на Postgres (п. 2.4) він не запускає.
