import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rich_messages import build_admin_orders_batch


@pytest.mark.parametrize(
    "status_label",
    [
        "🕐 Очікує",
        "✅ Підтверджено",
        "❌ Відмінено",
    ],
)
def test_admin_batch_order_status_is_bold_at_bottom(status_label):
    html = build_admin_orders_batch(
        "@denisposelyanov",
        [
            {
                "order_id": 60,
                "status_label": status_label,
                "items": [
                    {
                        "product_id": 11,
                        "product_name": "PentaClick - Клікер з ризинкою",
                        "price": 100,
                        "quantity": 1,
                        "filament_name": "Зелений",
                    }
                ],
                "total_price": 100,
            }
        ],
    )

    header = "<p><b>Замовлення #60</b></p>"
    status = f"<p><b>Статус: {status_label}</b></p>"

    assert header in html
    assert f"Замовлення #60</b> — {status_label}" not in html
    assert status in html
    assert html.index(status) > html.index("Разом: 100 ₴")
