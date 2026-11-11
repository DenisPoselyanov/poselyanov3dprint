"""Зразки віджетів для сторінки «Віджети» в адмінці.

HTML збирають ті самі функції, що й бот (services/order_widgets.py, services/chat_widgets.py),
тож прев'ю завжди показує справжній вигляд. Дані — вигадані приклади, з базою не пов'язані.
"""
from datetime import datetime, timedelta, timezone

from services import chat_widgets as cw
from services import order_widgets as ow

_ORDER = {
    "id": 131, "status": "new", "total_price": 730, "discount_amount": 50, "gift_product_name": "Брелок",
    "delivery_method": "np", "city": "Київ", "branch": "Відділення №12", "recipient_name": "Олена К.",
    "pay_method": "card", "is_paid": 1,
}
_ITEMS = [
    {"product_name": "Фігурка за твоїм ескізом", "price": 520, "quantity": 1},
    {"product_name": "Органайзер для кабелів", "price": 130, "quantity": 2},
]
_ROWS = [
    [1, "Флексі-дракон", 250, 0, 1], [2, "Органайзер для кабелів", 120, 150, 1], [3, "Ваза «Хвиля»", 480, 0, 1],
    [4, "Брелок із написом", 90, 0, 0], [5, "Підставка для навушників", 260, 0, 1],
]


def _state(label: str, data: str, caption: str) -> dict:
    return {"label": label, "data": data, "caption": caption}


def _status() -> dict:
    widget = ow.build_widget(_ORDER, _ITEMS)
    steps = [
        ("Нове", {"status": "new"}, None),
        ("Прийнято", {"status": "confirmed", "eta_text": "2–3 дні"}, None),
        ("Друкується", {"status": "printing", "eta_text": "2–3 дні"}, None),
        ("У дорозі", {"status": "shipped", "ttn": "20450718331264"}, {"state": "w"}),
        ("У відділенні", {"status": "shipped", "ttn": "20450718331264"}, {"state": "b", "free_until": _day(5)}),
        ("Отримано", {"status": "delivered", "ttn": "20450718331264"}, None),
    ]
    states = []
    for label, patch, tracking in steps:
        order = {**_ORDER, **patch}
        states.append(_state(label, ow.build_widget_data(order, tracking), ow.build_caption(order, tracking)))
    return {"html": widget["html"], "states": states}


def _day(offset: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=offset)).date().isoformat()


def _soon(days: int, hours: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours)).isoformat()


def _quote() -> dict:
    widget = cw.build_quote_widget(_ORDER, _ITEMS)
    return {"html": widget["html"], "states": [
        _state("Чекає відповіді", '{"a":0}', widget["caption"]),
        _state("Погоджено", '{"a":1}', widget["caption"]),
    ]}


def _giveaway() -> dict:
    g = {"id": 3, "title": "Флексі-дракон 40 см", "ends_at": _soon(3, 5), "referral_bonus": 1}
    widget = cw.build_giveaway_widget(g, cw.giveaway_state(214, 3, 57), "https://t.me/")
    return {"html": widget["html"], "states": [_state("Триває", widget["data"], widget["caption"])]}


def _promo() -> dict:
    widget = cw.build_promo_widget([
        {"type": "order_percent", "value": 10, "min_order": 500, "title": "Знижка на все", "emoji": "🔥",
         "badge": "−10%", "ends_at": _soon(2, 7), "accent": "orange"},
        {"type": "gift_threshold", "min_items": 3, "max_gift_price": 100, "title": "Безкоштовний подарунок",
         "emoji": "🎁"},
    ])
    return {"html": widget["html"], "states": [_state("Дві акції", widget.get("data") or "", widget["caption"])]}


def _cart() -> dict:
    filled = {"i": [[1, "Флексі-дракон", 250, 2], [2, "Органайзер для кабелів", 120, 1]],
              "s": 620, "d": 60, "t": 560, "m": ""}
    return {"html": cw.build_cart_widget(), "states": [
        _state("З товарами", cw.dumps(filled), cw.cart_caption(filled)),
        _state("Порожній", "{}", cw.cart_caption({})),
    ]}


def _hits() -> dict:
    widget = cw.build_showcase_widget(
        "Хіти продажів", _ROWS, tabs=[["hits", "Хіти"], ["new", "Новинки"], ["sale", "Знижки"]],
        subtitle="Найчастіше замовляють. Ціни оновлюються самі")
    return {"html": widget["html"], "states": [
        _state("Хіти", cw.dumps({"l": _ROWS, "n": 3}), widget["caption"])]}


def _gift() -> dict:
    widget = cw.build_showcase_widget("Ось що раджу", _ROWS[:4], kind="gift",
                                      subtitle="Дитині · 200–500 ₴ · Новий рік", accent="pink")
    return {"html": widget["html"], "states": [_state("Результат підбору", widget["data"], widget["caption"])]}


def _dash() -> dict:
    state = cw.dash_state({"orders": 3, "revenue": 1540, "started": 5}, {"orders": 21, "revenue": 9870, "started": 34},
                          queue=2, pending_price=1, searches=["дракон", "органайзер", "ваза"], stamp="14:02")
    return {"html": cw.build_dash_widget(""), "states": [
        _state("Сьогодні", cw.dumps(state), "📊 Сьогодні: 3 замовл. · 1540 ₴ · у черзі 2")]}


_BUILDERS = {
    "status": _status, "quote": _quote, "giveaway": _giveaway, "promo": _promo,
    "cart": _cart, "hits": _hits, "gift": _gift, "dash": _dash,
}


def previews() -> dict[str, dict]:
    """{ключ віджета: {html, states: [{label, data, caption}]}} — порядок як у services.settings.WIDGET_KINDS."""
    return {key: build() for key, build in _BUILDERS.items()}
