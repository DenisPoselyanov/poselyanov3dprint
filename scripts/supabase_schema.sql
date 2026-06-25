BEGIN;

-- Catalog tables (Phase 2b)
CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    emoji TEXT,
    badge_class TEXT,
    sort_order INTEGER DEFAULT 0,
    active BOOLEAN DEFAULT true,
    quick_slot INTEGER
);

CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    category_id TEXT REFERENCES categories(id),
    name TEXT NOT NULL,
    emoji TEXT,
    mat TEXT,
    price INTEGER DEFAULT 0,
    old_price INTEGER,
    photos JSONB DEFAULT '[]',
    custom_fields TEXT DEFAULT '',
    hot BOOLEAN DEFAULT false,
    gift BOOLEAN DEFAULT false,
    filament_choice BOOLEAN DEFAULT true,
    luminous_filament_choice BOOLEAN DEFAULT false,
    pinned BOOLEAN DEFAULT false,
    stl_link TEXT DEFAULT '',
    contract_price BOOLEAN DEFAULT false,
    is_custom BOOLEAN DEFAULT false,
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS filaments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hex TEXT,
    available BOOLEAN DEFAULT true,
    sort_order INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_active ON products(active);
ALTER TABLE products ADD COLUMN IF NOT EXISTS luminous_filament_choice BOOLEAN DEFAULT false;

-- Transaction tables
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
    ordered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    price_pending INTEGER NOT NULL DEFAULT 0,
    channel_message_id BIGINT
);

CREATE TABLE IF NOT EXISTS order_items (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT,
    product_id BIGINT,
    product_name TEXT,
    price INTEGER,
    quantity INTEGER NOT NULL DEFAULT 1,
    filament TEXT NOT NULL DEFAULT '',
    is_contract_price INTEGER NOT NULL DEFAULT 0
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

-- RLS: service role (bot) bypasses; anon read-only for catalog if needed
ALTER TABLE categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE products ENABLE ROW LEVEL SECURITY;
ALTER TABLE filaments ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE coupons ENABLE ROW LEVEL SECURITY;
ALTER TABLE coupon_uses ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS catalog_categories_read ON categories;
CREATE POLICY catalog_categories_read ON categories FOR SELECT TO anon, authenticated USING (active = true);

DROP POLICY IF EXISTS catalog_products_read ON products;
CREATE POLICY catalog_products_read ON products FOR SELECT TO anon, authenticated USING (active = true);

DROP POLICY IF EXISTS catalog_filaments_read ON filaments;
CREATE POLICY catalog_filaments_read ON filaments FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS catalog_filaments_write ON filaments;
CREATE POLICY catalog_filaments_write ON filaments FOR ALL TO authenticated, service_role USING (true) WITH CHECK (true);

-- Українські назви світних філаментів (раніше були англійською)
UPDATE filaments SET name = 'Світний' WHERE id = 'luminous';
UPDATE filaments SET name = 'Світний зелений' WHERE id = 'luminous-green';
UPDATE filaments SET name = 'Світний синій' WHERE id = 'luminous-blue';
UPDATE filaments SET name = 'Світний бірюзовий' WHERE id = 'luminous-aqua';
UPDATE filaments SET name = 'Світний рожевий' WHERE id = 'luminous-pink';
UPDATE filament_colors SET name = 'Світний' WHERE id = 'luminous';
UPDATE filament_colors SET name = 'Світний зелений' WHERE id = 'luminous-green';
UPDATE filament_colors SET name = 'Світний синій' WHERE id = 'luminous-blue';
UPDATE filament_colors SET name = 'Світний бірюзовий' WHERE id = 'luminous-aqua';
UPDATE filament_colors SET name = 'Світний рожевий' WHERE id = 'luminous-pink';

COMMIT;
