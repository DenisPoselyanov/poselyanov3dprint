# PROJECT_CONTEXT.md — Poselyanov 3D Print Mini App

> Актуальний контекст проєкту станом на поточний код `main`.
> Цей файл використовувати як стартовий бриф у нових чатах.

---

## 🔗 Репозиторій

- **GitHub:** https://github.com/DenisPoselyanov/poselyanov3dprint
- **Mini App (GitHub Pages):** https://denisposelyanov.github.io/poselyanov3dprint/
- **Основні файли:**
  - `index.html` — фронтенд Mini App
  - `bot.py` — Telegram-бот + HTTP API (`aiohttp`)
  - `products.json`, `custom_products.json`, `filaments.json` — каталоги/довідники
  - `admin-panel.html` — адмінка

---

## 📱 Що це за проєкт

Telegram Mini App для продажу 3D-друкованих товарів.

- Бот: `@poselyanov3dprint_bot`
- Замовлення оформлюються з веб-каталогу та потрапляють у БД і в Telegram-чат замовлень.
- Адмін керує товарами через WebApp-панель (`/admin`).

---

## 🧱 Поточна структура та стек

- **Frontend:** один файл `index.html` (HTML/CSS/JS без фреймворків)
- **Backend bot:** `python-telegram-bot` (async)
- **HTTP API:** `aiohttp` на порту `8080`
- **DB:** SQLite (`users.db`)
- **Медіа:** Cloudinary

---

## ⚙️ Важливі ENV змінні

```env
BOT_TOKEN=...
OWNER_ID=718746623
ORDERS_CHAT_ID=-1003739884073
VALIDATE_INIT_DATA=false
PORT=8080
ADMIN_WEBAPP_URL=http://localhost:8080/admin/panel
CORS_ORIGINS=https://denisposelyanov.github.io,http://localhost:8080,...

CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...
```

### Пояснення
- `OWNER_ID` — **особистий Telegram user id адміна** (позитивний).
- `ORDERS_CHAT_ID` — **чат/канал для замовлень** (часто `-100...`).
  - Розділено навмисно, щоб адмін-доступ і канал замовлень не конфліктували.

---

## 🗄️ Актуальна БД (реально в коді)

### `users`
- `id`, `name`, `username`, `blocked`, `joined_at`

### `orders`
- `id`, `user_id`, `username`, `first_name`, `total_price`, `comment`,
  `gift_product_name`, `status`, `coupon_code`, `discount_amount`, `ordered_at`

### `order_items`
- `id`, `order_id`, `product_id`, `product_name`, `price`, `quantity`, `filament`

### `coupons`
- `code`, `type`, `value`, `min_order`, `uses_max`, `uses_count`,
  `one_per_user`, `active`, `expires_at`, `personal_user_id`

### `coupon_uses`
- `id`, `code`, `user_id`, `order_id`, `used_at`

### `filament_colors`
- `id`, `name`, `hex`, `available`

---

## 🛍️ Потік замовлення

1. Frontend формує кошик (`items`, `coupon_code`, `total_price`, `comment`, `gift`).
2. `POST /order` у `bot.py`:
   - валідація користувача (`initData` або fallback при `VALIDATE_INIT_DATA=false`),
   - серверний перерахунок суми,
   - перевірка купона,
   - запис у БД (`orders` + `order_items`).
3. Клієнту в бот приходить підтвердження.
4. У чат замовлень (`ORDERS_CHAT_ID`) летить адмін-повідомлення з кнопками статусу.

---

## 🧾 Формат повідомлень (актуально)

- Для адміна/каналу сума з купоном у форматі:
  - `💰 Разом: 500 → 450 ₴`
- Для клієнтського підтвердження в боті також використовується формат зі стрілкою.

---

## 🔐 Доступ та адмінка

- Команда `/admin` доступна тільки коли `from_user.id == OWNER_ID`.
- Admin API (`/api/products`, `/api/upload-photo`...) орієнтується на `OWNER_ID` при ввімкненій перевірці `VALIDATE_INIT_DATA=true`.
- `GET /admin/panel` віддає `admin-panel.html`.

---

## 🌐 HTTP маршрути (основні)

- `POST /order` — створити замовлення
- `POST /check_coupon` — валідація купона
- `GET /admin/panel` — UI адмінки
- `GET /api/products`
- `GET /api/products/{id}`
- `POST /api/products`
- `PUT /api/products/{id}`
- `DELETE /api/products/{id}`
- `POST /api/upload-photo`

---

## 🤖 Команди бота

### Адмін
- `/admin`, `/stats`, `/broadcast`, `/coupon`, `/reload_products`, `/myid`

### Користувач
- `/history`, `/mycoupons`, `/sales`, `/status`, `/contact`

---

## 📌 Нотатки для наступних змін

1. При зміні логіки повідомлень синхронізувати формат:
   - клієнтське підтвердження
   - повідомлення в канал/чат замовлень
2. Не змішувати `OWNER_ID` і `ORDERS_CHAT_ID`.
3. Перед деплоєм перевіряти права бота в каналі (`Post Messages`).
4. Після зміни `.env` завжди перезапускати процес бота.

---

## ✅ Поточний стан

Проєкт робочий: замовлення з Mini App зберігаються в БД, купони застосовуються, повідомлення відправляються в окремий чат замовлень і клієнту в бот, адмінка керує товарами через WebApp.
