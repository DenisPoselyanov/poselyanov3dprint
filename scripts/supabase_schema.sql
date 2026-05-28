BEGIN;

CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    name TEXT,
    username TEXT,
    blocked INTEGER NOT NULL DEFAULT 0,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orders (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    username TEXT,
    first_name TEXT,
    total_price INTEGER,
    comment TEXT,
    gift_product_name TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    coupon_code TEXT,
    discount_amount INTEGER NOT NULL DEFAULT 0,
    ordered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS order_items (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT,
    product_id BIGINT,
    product_name TEXT,
    price INTEGER,
    quantity INTEGER NOT NULL DEFAULT 1,
    filament TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS coupons (
    code TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    value INTEGER NOT NULL,
    min_order INTEGER NOT NULL DEFAULT 0,
    uses_max INTEGER NOT NULL DEFAULT 0,
    uses_count INTEGER NOT NULL DEFAULT 0,
    one_per_user INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    expires_at TIMESTAMPTZ,
    personal_user_id BIGINT
);

CREATE TABLE IF NOT EXISTS coupon_uses (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL,
    user_id BIGINT NOT NULL,
    order_id BIGINT,
    used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS filament_colors (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hex TEXT,
    available INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_coupon_uses_user_id ON coupon_uses(user_id);
CREATE INDEX IF NOT EXISTS idx_users_blocked ON users(blocked);

COMMIT;
