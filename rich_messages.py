"""Telegram Rich Messages (Bot API 10.1) — шаблони та обгортки API."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

BOT_LINK_BASE = "https://t.me/poselyanov3dprint_bot?startapp=product_"
DENIS_LINK = "https://t.me/denisposelyanov"

MONTHS_UA = ["січ", "лют", "бер", "кві", "тра", "чер", "лип", "сер", "вер", "жов", "лис", "гру"]

STATUS_LABELS_FULL = {
    "new": ("🕐", "Очікує підтвердження", "Денис скоро зв'яжеться з тобою"),
    "confirmed": ("✅", "Підтверджено", "Замовлення прийнято у роботу!"),
    "cancelled": ("❌", "Скасовано", "Якщо є питання — напиши Денису"),
    "draft": ("📝", "Під питанням", "Денис уточнює деталі замовлення"),
}

STATUS_LABELS_SHORT = {
    "new": ("🕐", "Очікує"),
    "confirmed": ("✅", "Підтверджено"),
    "cancelled": ("❌", "Скасовано"),
    "draft": ("📝", "Під питанням"),
}


def escape(text: str | None) -> str:
    return html.escape(str(text or ""))


def format_date(date_raw: str | None) -> str:
    if not date_raw:
        return "—"
    try:
        dt = datetime.strptime(date_raw[:10], "%Y-%m-%d")
        return f"{dt.day} {MONTHS_UA[dt.month - 1]} {dt.year}"
    except Exception:
        return date_raw[:10]


def product_link(name: str, product_id: int | None, *, linked: bool = True) -> str:
    safe = escape(name or "—")
    if linked and product_id:
        return f'<a href="{BOT_LINK_BASE}{product_id}">{safe}</a>'
    return safe


def _serialize_markup(markup) -> dict | None:
    if markup is None:
        return None
    return markup.to_dict()


def _message_id(result: Any) -> int | None:
    if result is None:
        return None
    if isinstance(result, dict):
        return result.get("message_id")
    return getattr(result, "message_id", None)


def rich_to_fallback_html(rich_html: str) -> str:
    """Спрощений HTML для fallback, якщо sendRichMessage недоступний."""
    text = rich_html
    for tag in ("h2", "h3", "p", "li", "footer", "blockquote", "hr"):
        text = re.sub(rf"</?{tag}[^>]*>", "\n", text)
    text = re.sub(r"<ul[^>]*>|</ul>", "\n", text)
    text = re.sub(r"<table[^>]*>|</table>|<tr[^>]*>|</tr>", "\n", text)
    text = re.sub(r"<t[hd][^>]*>|</t[hd]>", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def send_rich_message(bot, chat_id, html_content: str, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "rich_message": {"html": html_content},
    }
    markup_dict = _serialize_markup(reply_markup)
    if markup_dict:
        payload["reply_markup"] = markup_dict
    try:
        result = await bot.do_api_request("sendRichMessage", payload)
        return _message_id(result)
    except Exception as e:
        logger.warning("sendRichMessage failed, falling back to HTML: %s", e)
        fallback = rich_to_fallback_html(html_content)
        msg = await bot.send_message(
            chat_id=chat_id,
            text=fallback,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        return msg.message_id


async def edit_rich_message(bot, chat_id, message_id: int, html_content: str, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "rich_message": {"html": html_content},
    }
    markup_dict = _serialize_markup(reply_markup)
    if markup_dict is not None:
        payload["reply_markup"] = markup_dict
    try:
        await bot.do_api_request("editMessageText", payload)
    except Exception as e:
        logger.warning("editMessageText (rich) failed, falling back to HTML: %s", e)
        fallback = rich_to_fallback_html(html_content)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=fallback,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )


def _client_item_line(item: dict) -> str:
    name = escape(item.get("product_name", "—"))
    qty = item.get("quantity", 1)
    line = f"{name} × {qty}"
    if item.get("filament_name"):
        line += f" · 🎨 {escape(item['filament_name'])}"
    return f"<li>{line}</li>"


def _client_order_block(order: dict) -> str:
    items = order.get("items") or []
    total_price = int(order.get("total_price") or 0)
    coupon_discount = int(order.get("coupon_discount") or 0)
    promotion_discount = int(order.get("promotion_discount") or 0)
    total_discount = coupon_discount + promotion_discount
    coupon_code = order.get("coupon_code")
    gift = order.get("gift")
    comment = (order.get("comment") or "").strip()

    parts = ['<p><b>Товари:</b></p>', "<ul>"]
    parts.extend(_client_item_line(i) for i in items)
    parts.append("</ul>")

    if total_discount > 0:
        original_price = total_price + total_discount
        if coupon_discount > 0 and coupon_code:
            parts.append(f"<p>🏷️ <b>Купон {escape(coupon_code)}:</b> −{coupon_discount} ₴</p>")
        if promotion_discount > 0:
            parts.append(f"<p>🔥 <b>Акція -10%:</b> −{promotion_discount} ₴</p>")
        parts.append(
            f"<p>💰 <b>Разом:</b> <s>{original_price} ₴</s> → <b>{total_price} ₴</b></p>"
        )
    else:
        parts.append(f"<p>💰 <b>Разом: {total_price} ₴</b></p>")

    if gift:
        parts.append(f"<p>🎁 Подарунок: {escape(gift)} — <b>безкоштовно</b></p>")
    if comment:
        parts.append(
            f'<blockquote expandable="true"><p>📝 {escape(comment)}</p></blockquote>'
        )
    return "\n".join(parts)


def build_client_order_confirmation(orders_batch: list[dict]) -> str:
    """HTML для підтвердження клієнту (одне або кілька замовлень)."""
    blocks = ['<h2>✅ Замовлення прийнято!</h2>', "<hr/>"]
    for i, order in enumerate(orders_batch):
        if i > 0:
            blocks.append("<hr/>")
        blocks.append(_client_order_block(order))
    blocks.append(
        f'<footer><a href="{DENIS_LINK}">Денис</a> '
        "зв'яжеться з тобою найближчим часом 🙌</footer>"
    )
    return "\n".join(blocks)


def _admin_item_line(item: dict, *, linked: bool = True) -> str:
    name = item.get("product_name", "—")
    product_id = int(item.get("product_id") or 0) or None
    qty = item.get("quantity", 1)
    price = int(item.get("price") or 0)
    subtotal = price * qty
    linked_name = product_link(name, product_id, linked=linked)
    line = f"{linked_name} × {qty} — {subtotal} ₴"
    filament = (item.get("filament_name") or item.get("filament") or "").strip()
    if filament:
        line += f" · 🎨 {escape(filament)}"
    custom = item.get("customValue")
    if custom:
        line += f" <i>{escape(custom)}</i>"
    return f"<li>{line}</li>"


def _admin_pricing_table(
    total_price: int,
    discount_amount: int,
    coupon_code: str | None = None,
    *,
    coupon_discount: int | None = None,
    promotion_discount: int | None = None,
) -> str:
    if discount_amount <= 0:
        return f"<p>💰 <b>Разом: {total_price} ₴</b></p>"

    original_price = total_price + discount_amount
    discount_parts = []
    if coupon_discount is not None or promotion_discount is not None:
        cd = coupon_discount or 0
        pd = promotion_discount or 0
        if cd > 0 and coupon_code:
            discount_parts.append(f"🏷️ {escape(coupon_code)} −{cd} ₴")
        if pd > 0:
            discount_parts.append(f"🔥 Акція −{pd} ₴")
    elif coupon_code:
        discount_parts.append(f"🏷️ {escape(coupon_code)} −{discount_amount} ₴")
    else:
        discount_parts.append(f"Знижка −{discount_amount} ₴")

    lines = [f"<p>{' · '.join(discount_parts)}</p>"] if discount_parts else []
    lines.append(
        "<table>"
        "<tr><th>Сума</th><th>Знижка</th><th>До сплати</th></tr>"
        f"<tr><td>{original_price} ₴</td><td>−{discount_amount} ₴</td>"
        f"<td><b>{total_price} ₴</b></td></tr>"
        "</table>"
    )
    return "\n".join(lines)


def build_admin_order_notification(
    order_id: int,
    username: str,
    items: list[dict],
    total_price: int,
    *,
    coupon_code: str | None = None,
    discount_amount: int = 0,
    coupon_discount: int | None = None,
    promotion_discount: int | None = None,
    gift: str | None = None,
    gift_product_id: int | None = None,
    comment: str | None = None,
    status_label: str | None = None,
    linked: bool = True,
) -> str:
    parts = [
        f"<h2>🔔 НОВЕ ЗАМОВЛЕННЯ #{order_id}</h2>",
        f"<p>👤 Від: <b>{escape(username)}</b></p>",
        "<hr/>",
        "<p><b>Товари:</b></p>",
        "<ul>",
    ]
    for item in items:
        if str(item.get("product_name", "")).startswith("🎁"):
            continue
        parts.append(_admin_item_line(item, linked=linked))
    parts.append("</ul>")

    parts.append(
        _admin_pricing_table(
            total_price,
            discount_amount,
            coupon_code,
            coupon_discount=coupon_discount,
            promotion_discount=promotion_discount,
        )
    )

    if gift:
        gift_name = str(gift).strip()
        if linked and gift_product_id:
            gift_line = product_link(gift_name, gift_product_id, linked=True)
        else:
            gift_line = escape(gift_name)
        parts.append(f"<p>🎁 Подарунок: {gift_line} — безкоштовно</p>")

    if comment:
        parts.append(f"<blockquote><p>📝 {escape(comment)}</p></blockquote>")

    if status_label:
        parts.extend(["<hr/>", f"<p><b>Статус: {escape(status_label)}</b></p>"])

    return "\n".join(parts)


def build_admin_order_with_status(
    order: dict,
    items: list[dict],
    status_label: str,
    *,
    linked: bool = True,
    gift_product_id: int | None = None,
) -> str:
    return build_admin_order_notification(
        order_id=int(order["id"]),
        username=order.get("username") or "невідомо",
        items=items,
        total_price=int(order.get("total_price") or 0),
        coupon_code=order.get("coupon_code"),
        discount_amount=int(order.get("discount_amount") or 0),
        gift=order.get("gift_product_name"),
        gift_product_id=gift_product_id,
        comment=order.get("comment"),
        status_label=status_label,
        linked=linked,
    )


def build_order_status(order: dict) -> str:
    icon, label, hint = STATUS_LABELS_FULL.get(
        order.get("status", ""), ("❔", order.get("status", ""), "")
    )
    date_fmt = format_date(order.get("ordered_at"))
    return (
        f"<h2>📦 Останнє замовлення #{order['id']}</h2>"
        f"<p>📅 {date_fmt} · 💰 {order['total_price']} ₴</p>"
        "<hr/>"
        f"<p>{icon} <b>{escape(label)}</b></p>"
        f"<p><i>{escape(hint)}</i></p>"
    )


def build_order_history(orders: list[dict], items_by_order: dict[int, list], first_name: str = "") -> str:
    parts = [f"<h2>📦 Замовлення {escape(first_name)}</h2>", "<hr/>"]

    for o in orders:
        icon, label = STATUS_LABELS_SHORT.get(o.get("status", ""), ("❔", o.get("status", "")))
        date_fmt = format_date(o.get("ordered_at"))
        parts.append(f"<p>{icon} <b>Замовлення #{o['id']}</b> · {date_fmt}</p>")
        parts.append("<ul>")

        for item in items_by_order.get(o["id"], []):
            name = item.get("product_name", "—")
            qty = item.get("quantity", 1)
            price = int(item.get("price") or 0)
            fl = (item.get("filament") or "").strip()
            if price == 0:
                parts.append(f"<li>{escape(name)}</li>")
            else:
                subtotal = price * qty
                qty_str = f" × {qty}" if qty > 1 else ""
                fl_str = f" · 🎨 {escape(fl)}" if fl else ""
                parts.append(f"<li>{escape(name)}{qty_str} — {subtotal} ₴{fl_str}</li>")

        parts.append("</ul>")

        if o.get("coupon_code") and o.get("discount_amount"):
            original = (o.get("total_price") or 0) + (o.get("discount_amount") or 0)
            parts.append(
                f"<p>🏷️ Купон <code>{escape(o['coupon_code'])}</code>: "
                f"−{o['discount_amount']} ₴</p>"
            )
            parts.append(f"<p>💰 <b>Разом: {original} → {o['total_price']} ₴</b></p>")
        else:
            parts.append(f"<p>💰 <b>Разом: {o['total_price']} ₴</b></p>")

        parts.append(f"<p>{icon} {escape(label)}</p>")
        if o.get("comment"):
            parts.append(f"<p>📝 <i>{escape(o['comment'])}</i></p>")
        parts.append("<hr/>")

    parts.append(f"<footer>Показано останніх замовлень: <b>{len(orders)}</b></footer>")
    return "\n".join(parts)


def build_broadcast_report(sent: int, failed: int, audience: str) -> str:
    return (
        "<h2>📨 Розсилка завершена</h2>"
        f"<p>Аудиторія: <b>{escape(audience)}</b></p>"
        "<hr/>"
        f"<p>✅ Надіслано: <b>{sent}</b></p>"
        f"<p>❌ Помилок/блокувань: <b>{failed}</b></p>"
    )
