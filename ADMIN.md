# 📊 ADMIN.md — Адмін панель Poselyanov 3D Print

Коротка технічна документація по адмін-панелі, правах доступу і типовому деплою.

---

## 1) Що вміє адмінка

- Перегляд усіх товарів (звичайні + custom)
- Створення товару
- Редагування товару
- Видалення товару
- Завантаження фото на Cloudinary

UI-файл: `admin-panel.html`  
Backend-обробники: у `bot.py`

---

## 2) Критично важливі ENV змінні

```env
BOT_TOKEN=...
OWNER_ID=718746623
ORDERS_CHAT_ID=-1003739884073
ADMIN_WEBAPP_URL=http://localhost:8080/admin/panel
VALIDATE_INIT_DATA=false
PORT=8080

CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...
```

### Ролі змінних

- `OWNER_ID` — **адмін-користувач** (особистий Telegram ID, позитивний).
- `ORDERS_CHAT_ID` — **чат/канал для замовлень** (`-100...` для каналів).

> Важливо: не використовувати `OWNER_ID` як канал. Це ламає логіку прав для `/admin`.

---

## 3) Запуск локально

```bash
pip install -r requirements.txt
python bot.py
```

Після старту:
- HTTP API слухає `:8080`
- Адмін панель: `http://localhost:8080/admin/panel`

У Telegram:
1. Відкрити бота
2. Виконати `/admin`
3. Натиснути кнопку **«📊 Адмін панель»**

---

## 4) Деплой / Render

1. Задати ENV (мінімум):
   - `BOT_TOKEN`
   - `OWNER_ID`
   - `ORDERS_CHAT_ID`
   - `ADMIN_WEBAPP_URL=https://<your-domain>/admin/panel`
   - `CLOUDINARY_*`
2. Перезапустити сервіс
3. Перевірити `/admin` у Telegram

---

## 5) Доступ і безпека

### Перевірка доступу
- Команда `/admin` перевіряє: `from_user.id == OWNER_ID`
- CRUD API ендпоінти перевіряють адміна при `VALIDATE_INIT_DATA=true`

### CORS
- Джерела беруться з `CORS_ORIGINS`

### Секрети
- `.env` не комітити
- Ключі Cloudinary зберігати тільки в ENV

---

## 6) API адмінки

- `GET /admin/panel`
- `GET /api/products`
- `GET /api/products/{id}`
- `POST /api/products`
- `PUT /api/products/{id}`
- `DELETE /api/products/{id}`
- `POST /api/upload-photo` (multipart, поле `file`)

---

## 7) Формат товарів

### `products.json`
- `id`, `cat`, `emoji`, `photos[]`, `name`, `mat`, `price`
- optional: `oldPrice`, `hot`, `gift`, `filamentChoice`, `stlLink`

### `custom_products.json`
- `id`, `cat="custom"`, `emoji`, `photos[]`, `name`, `mat`, `price`
- optional: `oldPrice`, `hot`, `custom_fields`, `stlLink`

---

## 8) Важливе про замовлення

- Нові замовлення йдуть у `ORDERS_CHAT_ID`.
- Клієнту в особистий бот-чат надсилається підтвердження.
- Формат суми з купоном уніфікований:
  - `💰 Разом: 500 → 450 ₴`

---

## 9) Типові проблеми

### `/admin` не працює
- Перевірити `OWNER_ID` (має бути ваш user id)
- Перезапустити бота після зміни `.env`

### Замовлення не приходять у канал
- Перевірити `ORDERS_CHAT_ID`
- Переконатися, що бот доданий у канал і має право публікації

### Фото не завантажуються
- Перевірити `CLOUDINARY_*`
- Переконатися, що файл — зображення і <= 25MB

---

## 10) Швидкий чекліст після змін

1. `python bot.py` без помилок
2. `/admin` відкриває панель
3. Створення/редагування/видалення товару працює
4. Фото завантажується на Cloudinary
5. Тестове замовлення:
   - приходить клієнту
   - приходить у `ORDERS_CHAT_ID`

---

**Статус:** актуалізовано під поточний код.
