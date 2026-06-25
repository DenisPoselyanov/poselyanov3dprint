BEGIN;

-- Відкат колонок варіантів товару (sizes/designs), якщо вони вже були додані
ALTER TABLE order_items DROP COLUMN IF EXISTS size;
ALTER TABLE order_items DROP COLUMN IF EXISTS design;
ALTER TABLE products DROP COLUMN IF EXISTS sizes;
ALTER TABLE products DROP COLUMN IF EXISTS designs;

COMMIT;
