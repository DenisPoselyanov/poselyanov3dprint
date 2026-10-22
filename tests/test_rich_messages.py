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


def test_new_rich_builders_share_skeleton():
    import rich_messages as rm

    outputs = [
        rm.build_review_request(12),
        rm.build_contact_card(5, "Іван <b>"),
        rm.build_bot_error("boom", 7),
        rm.build_myid(5),
        rm.build_cache_reloaded(1, 2, 3),
        rm.build_coupon_help(),
        rm.build_broadcast_help(),
        rm.build_error_notice("X", "підказка"),
        rm.build_stats(1, 2, 3, 4, 5, 600, [(1, "A", "@a"), (2, "B", "")], [("P", 2)], [("C", 1, 10)], 10),
        rm.build_coupon_list([{"code": "A", "type": "percent", "value": 10, "active": 1}]),
        rm.build_review_moderation({"id": 1, "order_id": 2, "rating": 4, "text": "ok", "photos": ["http://x"]}, "P"),
    ]
    for html_text in outputs:
        assert html_text.startswith("<h2>") and "<hr/>" in html_text
    assert "Іван &lt;b&gt;" in outputs[1]
    assert "№12" in outputs[0]


def test_manual_client_message_has_signature():
    import rich_messages as rm

    out = rm.build_manual_client_message("Привіт <i>\n\nДругий рядок")
    assert out.count("<p>") == 2 and "&lt;i&gt;" in out and "<footer>" in out
