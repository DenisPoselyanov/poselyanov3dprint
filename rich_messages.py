"""Telegram Rich Messages (Bot API 10.1) — шаблони та обгортки API."""

from __future__ import annotations

import asyncio
import html
import logging
import re
import warnings
from datetime import datetime
from typing import Any, Callable, TypeVar

from telegram.error import RetryAfter, TimedOut
from telegram.warnings import PTBUserWarning

logger = logging.getLogger(__name__)

MAX_TELEGRAM_RETRIES = 3
T = TypeVar("T")
_edit_locks: dict[tuple[int | str, int], asyncio.Lock] = {}


def is_telegram_rate_limited(exc: BaseException) -> bool:
    if isinstance(exc, (RetryAfter, TimedOut)):
        return True
    msg = str(exc).lower()
    return "flood control" in msg or "timed out" in msg or "retry after" in msg


def _retry_after_seconds(exc: BaseException) -> float:
    if isinstance(exc, RetryAfter):
        return float(exc.retry_after) + 0.5
    match = re.search(r"retry in (\d+)", str(exc), re.I)
    if match:
        return float(match.group(1)) + 0.5
    return 3.0


async def _telegram_retry(
    coro_factory: Callable[[], Any],
    *,
    label: str = "telegram api",
) -> T:
    last_exc: BaseException | None = None
    for attempt in range(MAX_TELEGRAM_RETRIES + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            if not is_telegram_rate_limited(e) or attempt >= MAX_TELEGRAM_RETRIES:
                raise
            wait = _retry_after_seconds(e)
            logger.warning(
                "%s rate limited (attempt %s/%s), retry in %.1fs: %s",
                label,
                attempt + 1,
                MAX_TELEGRAM_RETRIES,
                wait,
                e,
            )
            await asyncio.sleep(wait)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{label} failed without exception")


def _edit_lock(chat_id, message_id: int) -> asyncio.Lock:
    key = (chat_id, message_id)
    if key not in _edit_locks:
        _edit_locks[key] = asyncio.Lock()
    return _edit_locks[key]

BOT_LINK_BASE = "https://t.me/poselyanov3dprint_bot?startapp=product_"
CATEGORY_LINK_BASE = "https://t.me/poselyanov3dprint_bot?startapp=category_"
DENIS_LINK = "https://t.me/denisposelyanov"

MONTHS_UA = ["січ", "лют", "бер", "кві", "тра", "чер", "лип", "сер", "вер", "жов", "лис", "гру"]

STATUS_LABELS_FULL = {
    "new": ("🕐", "Очікує підтвердження", "Денис скоро зв'яжеться з тобою"),
    "confirmed": ("✅", "Підтверджено", "Замовлення прийнято у роботу!"),
    "cancelled": ("❌", "Скасовано", "Якщо є питання — напиши Денису"),
    "draft": ("📝", "Під питанням", "Денис уточнює деталі замовлення"),
    "printing": ("🖨️", "Друкується", "Вже беруся за твоє замовлення"),
    "ready": ("📦", "Готове до відправки", "Скоро відправлю"),
    "shipped": ("🚚", "Відправлено", "ТТН — у «Моїх замовленнях»"),
    "delivered": ("🎉", "Отримано", "Дякую, що обрав нас 💙"),
}

STATUS_LABELS_SHORT = {
    "new": ("🕐", "Очікує"),
    "confirmed": ("✅", "Підтверджено"),
    "cancelled": ("❌", "Скасовано"),
    "draft": ("📝", "Під питанням"),
    "printing": ("🖨️", "Друкується"),
    "ready": ("📦", "Готове"),
    "shipped": ("🚚", "Відправлено"),
    "delivered": ("🎉", "Отримано"),
}

# Повідомлення клієнту на кожному кроці (чернетки з REDESIGN.md, розд. 5.2).
NP_TRACKING_URL = "https://novaposhta.ua/tracking/?cargo_number="

# (заголовок, текст) — той самий каркас, що й у підтвердженні замовлення:
# <h2> + <hr/> + <p> + <footer>.
CLIENT_STATUS_TEXT = {
    "draft": ("Є питання по замовленню №{id}", "Денис уточнює деталі й скоро напише тобі."),
    "confirmed": ("Замовлення №{id} підтверджено", "Денис надішле реквізити для оплати окремим повідомленням."),
    "printing": ("Замовлення №{id} друкується", "Почав друкувати твоє замовлення."),
    "ready": ("Замовлення №{id} готове", "Надруковано й запаковано — скоро відправлю."),
    "shipped": (
        "Замовлення №{id} відправлено",
        "ТТН: <code>{ttn}</code>\n<a href=\"" + NP_TRACKING_URL + "{ttn}\">Відстежити на novaposhta.ua</a>",
    ),
    "delivered": (
        "Замовлення №{id} отримано",
        "Дякую, що обрав нас 💙\nЯк тобі? Оціни одним дотиком — за відгук із фото дарую купон на знижку.",
    ),
    "cancelled": ("Замовлення №{id} скасовано", "Якщо є питання — напиши Денису."),
}


def build_client_status_update(order_id: int, status: str, ttn: str | None = None) -> str | None:
    """HTML-повідомлення клієнту про зміну статусу; None — для цього статусу нічого не шлемо."""
    entry = CLIENT_STATUS_TEXT.get(status)
    if not entry:
        return None
    title, text = entry
    if status == "delivered":
        from services.settings import review_bonus_enabled

        if not review_bonus_enabled():
            text = "Дякую, що обрав нас 💙\nЯк тобі? Оціни одним дотиком — це допомагає іншим обрати."
    icon = STATUS_LABELS_SHORT.get(status, ("📦", ""))[0]
    fmt = {"id": int(order_id), "ttn": escape(ttn or "—")}
    blocks = [f"<h2>{icon} {title.format(**fmt)}</h2>", "<hr/>"]
    for line in text.format(**fmt).split("\n"):
        blocks.append(f"<p>{line}</p>")
    if status in ("confirmed", "draft", "cancelled"):
        blocks.append(f'<footer><a href="{DENIS_LINK}">Денис</a></footer>')
    return "\n".join(blocks)


def escape(text: str | None) -> str:
    return html.escape(str(text or ""))


def admin_customer_html_line(
    username: str,
    *,
    user_id: int | None = None,
    first_name: str | None = None,
) -> str:
    from services.orders import _normalize_tg_handle, tg_contact_url

    handle = _normalize_tg_handle(username)
    if handle:
        return f'<a href="https://t.me/{escape(handle)}">@{escape(handle)}</a>'
    display = (first_name or username or "невідомо").strip()
    if display in ("невідомо", "—"):
        display = first_name or "клієнт"
    uid = int(user_id or 0)
    contact_url = tg_contact_url(uid, username)
    if contact_url and uid > 0:
        return (
            f'<a href="{contact_url}">{escape(display)}</a>'
            f' · ID <code>{uid}</code>'
        )
    return f"<b>{escape(display)}</b>"


def format_date(date_raw) -> str:
    if not date_raw:
        return "—"
    try:
        if isinstance(date_raw, datetime):
            dt = date_raw
        else:
            dt = datetime.strptime(str(date_raw)[:10], "%Y-%m-%d")
        return f"{dt.day} {MONTHS_UA[dt.month - 1]} {dt.year}"
    except Exception:
        return escape(str(date_raw)[:10])


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

    async def _send_rich() -> Any:
        return await bot.do_api_request("sendRichMessage", payload)

    try:
        result = await _telegram_retry(_send_rich, label="sendRichMessage")
        return _message_id(result)
    except Exception as e:
        if is_telegram_rate_limited(e):
            raise
        logger.warning("sendRichMessage failed, falling back to HTML: %s", e)
        fallback = rich_to_fallback_html(html_content)

        async def _send_html():
            return await bot.send_message(
                chat_id=chat_id,
                text=fallback,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )

        msg = await _telegram_retry(_send_html, label="sendMessage (html)")
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

    async def _edit_rich() -> None:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=PTBUserWarning, message=".*editMessageText.*")
            await bot.do_api_request("editMessageText", payload)

    lock = _edit_lock(chat_id, message_id)
    async with lock:
        try:
            await _telegram_retry(_edit_rich, label="editMessageText (rich)")
        except Exception as e:
            if is_telegram_rate_limited(e):
                raise
            logger.warning("editMessageText (rich) failed, falling back to HTML: %s", e)
            fallback = rich_to_fallback_html(html_content)

            async def _edit_html() -> None:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=fallback,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )

            await _telegram_retry(_edit_html, label="editMessageText (html)")


def _items_have_comments(items: list[dict]) -> bool:
    return any(
        (i.get("comment") or "").strip()
        for i in items
        if not str(i.get("product_name", "")).startswith("🎁")
    )


def _render_admin_items_with_comments(items: list[dict], *, linked: bool = True) -> list[str]:
    parts: list[str] = []
    filtered = [i for i in items if not str(i.get("product_name", "")).startswith("🎁")]
    if not filtered:
        return parts
    idx = 0
    while idx < len(filtered):
        group_comment = (filtered[idx].get("comment") or "").strip()
        end = idx
        group_items: list[str] = []
        while end < len(filtered) and (filtered[end].get("comment") or "").strip() == group_comment:
            group_items.append(_admin_item_line(filtered[end], linked=linked))
            end += 1
        parts.append("<ul>")
        parts.extend(group_items)
        parts.append("</ul>")
        if group_comment:
            parts.append(f"<blockquote><p>📝 {escape(group_comment)}</p></blockquote>")
        idx = end
    return parts


def _render_client_items_with_comments(items: list[dict]) -> list[str]:
    parts: list[str] = []
    filtered = [i for i in items if not str(i.get("product_name", "")).startswith("🎁")]
    if not filtered:
        return parts
    idx = 0
    while idx < len(filtered):
        group_comment = (filtered[idx].get("comment") or "").strip()
        end = idx
        group_items: list[str] = []
        while end < len(filtered) and (filtered[end].get("comment") or "").strip() == group_comment:
            group_items.append(_client_item_line(filtered[end]))
            end += 1
        parts.append("<ul>")
        parts.extend(group_items)
        parts.append("</ul>")
        if group_comment:
            parts.append(f"<blockquote><p>📝 {escape(group_comment)}</p></blockquote>")
        idx = end
    return parts


def _client_item_line(item: dict) -> str:
    name = escape(item.get("product_name", "—"))
    qty = item.get("quantity", 1)
    line = f"{name} × {qty}"
    if item.get("filament_name"):
        line += f" · 🎨 {escape(item['filament_name'])}"
    if item.get("is_contract_price"):
        line += " · договірна ціна"
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
    price_pending = bool(order.get("price_pending"))
    has_contract = any(i.get("is_contract_price") for i in items)

    parts = ['<p><b>Товари:</b></p>']
    parts.extend(_render_client_items_with_comments(items))

    if total_discount > 0:
        original_price = total_price + total_discount
        if coupon_discount > 0 and coupon_code:
            parts.append(f"<p>🏷️ <b>Купон {escape(coupon_code)}:</b> −{coupon_discount} ₴</p>")
        if promotion_discount > 0:
            parts.append(f"<p>🔥 <b>Акція:</b> −{promotion_discount} ₴</p>")
        if has_contract and price_pending:
            parts.append(
                f"<p>💰 <b>Разом:</b> <s>{original_price} ₴</s> → <b>{total_price} ₴</b> + договірна</p>"
            )
        else:
            parts.append(
                f"<p>💰 <b>Разом:</b> <s>{original_price} ₴</s> → <b>{total_price} ₴</b></p>"
            )
    elif price_pending or (has_contract and total_price == 0):
        if total_price > 0 and has_contract:
            parts.append(f"<p>💰 <b>Разом: {total_price} ₴</b> + договірна позиція</p>")
        else:
            parts.append("<p>💰 <b>Договірна ціна</b> — суму узгодимо після замовлення</p>")
    else:
        parts.append(f"<p>💰 <b>Разом: {total_price} ₴</b></p>")

    if gift:
        parts.append(f"<p>🎁 Подарунок: {escape(gift)} — <b>безкоштовно</b></p>")
    if comment and not _items_have_comments(items):
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
    if item.get("is_contract_price") and price == 0:
        line = f"{linked_name} × {qty} — договірна"
    else:
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
    price_pending: bool = False,
) -> str:
    if price_pending and total_price == 0 and discount_amount <= 0:
        return "<p>⚠️ <b>Є договірні позиції</b> — вкажи ціну в адмін-панелі</p>"

    if discount_amount <= 0:
        suffix = " + договірна" if price_pending else ""
        return f"<p>💰 <b>Разом: {total_price} ₴{suffix}</b></p>"

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
    user_id: int | None = None,
    first_name: str | None = None,
    coupon_code: str | None = None,
    discount_amount: int = 0,
    coupon_discount: int | None = None,
    promotion_discount: int | None = None,
    gift: str | None = None,
    gift_product_id: int | None = None,
    comment: str | None = None,
    status_label: str | None = None,
    linked: bool = True,
    price_pending: bool = False,
) -> str:
    customer = admin_customer_html_line(
        username, user_id=user_id, first_name=first_name,
    )
    parts = [
        f"<h2>🔔 НОВЕ ЗАМОВЛЕННЯ #{order_id}</h2>",
        f"<p>👤 Від: {customer}</p>",
        "<hr/>",
        "<p><b>Товари:</b></p>",
    ]
    parts.extend(_render_admin_items_with_comments(items, linked=linked))

    parts.append(
        _admin_pricing_table(
            total_price,
            discount_amount,
            coupon_code,
            coupon_discount=coupon_discount,
            promotion_discount=promotion_discount,
            price_pending=price_pending,
        )
    )

    if gift:
        gift_name = str(gift).strip()
        if linked and gift_product_id:
            gift_line = product_link(gift_name, gift_product_id, linked=True)
        else:
            gift_line = escape(gift_name)
        parts.append(f"<p>🎁 Подарунок: {gift_line} — безкоштовно</p>")

    if comment and not _items_have_comments(items):
        parts.append(f"<blockquote><p>📝 {escape(comment)}</p></blockquote>")

    if status_label:
        parts.extend(["<hr/>", f"<p><b>Статус: {escape(status_label)}</b></p>"])

    return "\n".join(parts)


def build_admin_orders_batch(
    username: str,
    sections: list[dict],
    *,
    user_id: int | None = None,
    first_name: str | None = None,
) -> str:
    """HTML для адмін-каналу: кілька замовлень одного клієнта в одному повідомленні.

    Кожен елемент sections містить ті самі ключі, що й build_admin_order_notification,
    плюс опційний 'status_label' і 'status'.
    """
    customer = admin_customer_html_line(
        username, user_id=user_id, first_name=first_name,
    )
    parts = [
        f"<h2>🔔 ЗАМОВЛЕННЯ від {customer}</h2>",
        "<hr/>",
    ]
    for i, s in enumerate(sections):
        if i > 0:
            parts.append("<hr/>")
        oid = s["order_id"]
        sl = s.get("status_label")
        hdr = f"<b>Замовлення #{oid}</b>"
        parts.append(f"<p>{hdr}</p>")
        linked = s.get("linked", True)
        parts.extend(_render_admin_items_with_comments(s.get("items", []), linked=linked))
        parts.append(_admin_pricing_table(
            int(s.get("total_price") or 0),
            int(s.get("discount_amount") or 0),
            s.get("coupon_code"),
            coupon_discount=s.get("coupon_discount"),
            promotion_discount=s.get("promotion_discount"),
            price_pending=bool(s.get("price_pending")),
        ))
        gift = s.get("gift")
        if gift:
            gift_name = str(gift).strip()
            gid = s.get("gift_product_id")
            gift_line = product_link(gift_name, gid, linked=linked) if (linked and gid) else escape(gift_name)
            parts.append(f"<p>🎁 {gift_line} — безкоштовно</p>")
        if s.get("comment") and not _items_have_comments(s.get("items", [])):
            parts.append(f"<blockquote><p>📝 {escape(s['comment'])}</p></blockquote>")
        if sl:
            parts.append(f"<p><b>Статус: {escape(sl)}</b></p>")
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
        user_id=order.get("user_id"),
        first_name=order.get("first_name"),
        items=items,
        total_price=int(order.get("total_price") or 0),
        coupon_code=order.get("coupon_code"),
        discount_amount=int(order.get("discount_amount") or 0),
        gift=order.get("gift_product_name"),
        gift_product_id=gift_product_id,
        comment=order.get("comment"),
        status_label=status_label,
        linked=linked,
        price_pending=bool(order.get("price_pending")),
    )


def build_client_price_quote(order_id: int, total_price: int, items: list[dict]) -> str:
    """HTML-повідомлення клієнту з фінальною сумою після узгодження."""
    parts = [
        f"<h2>💰 Замовлення №{order_id}</h2>",
        "<p>Фінальна сума узгоджена:</p>",
        "<ul>",
    ]
    for item in items:
        if str(item.get("product_name", "")).startswith("🎁"):
            continue
        name = escape(item.get("product_name", "—"))
        qty = int(item.get("quantity") or 1)
        price = int(item.get("price") or 0)
        line = f"{name} × {qty}"
        fl = (item.get("filament") or "").strip()
        if fl:
            line += f" · 🎨 {escape(fl)}"
        if price > 0:
            line += f" — {price * qty} ₴"
        parts.append(f"<li>{line}</li>")
    parts.append("</ul>")
    parts.append(f"<p><b>До сплати: {total_price} ₴</b></p>")
    parts.append(
        f'<footer><a href="{DENIS_LINK}">Денис</a> '
        "зв'яжеться з тобою для оплати 🙌</footer>"
    )
    return "\n".join(parts)


def build_order_status(order: dict) -> str:
    icon, label, hint = STATUS_LABELS_FULL.get(
        order.get("status", ""), ("❔", order.get("status", ""), "")
    )
    date_fmt = format_date(order.get("ordered_at"))
    return (
        f"<h2>📦 Останнє замовлення №{order['id']}</h2>"
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
        parts.append(f"<p>{icon} <b>Замовлення №{o['id']}</b> · {date_fmt}</p>")
        parts.append("<ul>")

        for item in items_by_order.get(o["id"], []):
            name = item.get("product_name", "—")
            qty = item.get("quantity", 1)
            price = int(item.get("price") or 0)
            product_id = int(item.get("product_id") or 0) or None
            fl = (item.get("filament") or "").strip()
            if price == 0:
                parts.append(f"<li>{product_link(name, product_id)}</li>")
            else:
                subtotal = price * qty
                qty_str = f" × {qty}" if qty > 1 else ""
                fl_str = f" · 🎨 {escape(fl)}" if fl else ""
                parts.append(
                    f"<li>{product_link(name, product_id)}{qty_str} — {subtotal} ₴{fl_str}</li>"
                )

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


def build_personal_coupon_notification(
    code: str,
    ctype: str,
    value: int,
    *,
    min_order: int = 0,
    one_per_user: int = 0,
    expires_at: str | None = None,
) -> str:
    """Rich HTML — сповіщення користувачу про персональний купон."""
    label = f"{value}%" if ctype == "percent" else f"{value} ₴"
    parts = [
        "<h2>🎟️ Для тебе є купон!</h2>",
        "<hr/>",
        "<p>Денис створив для тебе персональний промокод:</p>",
        f"<p>🏷️ <b><code>{escape(code)}</code></b> — знижка {label}</p>",
    ]

    conditions: list[str] = []
    if min_order:
        conditions.append(f"<li>Від суми: {min_order} ₴</li>")
    if expires_at:
        conditions.append(f"<li>Діє до: {format_date(expires_at)}</li>")
    if one_per_user:
        conditions.append("<li>Одноразовий ⚡</li>")
    if conditions:
        parts.append("<ul>")
        parts.extend(conditions)
        parts.append("</ul>")

    parts.extend([
        "<p><b>Як скористатися:</b></p>",
        "<ul>",
        "<li>Відкрий каталог і додай товари в кошик</li>",
        "<li>Натисни «Промокод» у кошику та введи код</li>",
        "<li>Оформи замовлення — знижка застосується автоматично</li>",
        "</ul>",
        "<footer>Усі твої купони також доступні через /mycoupons</footer>",
    ])
    return "\n".join(parts)


def build_admin_coupon_created_notification(
    code: str,
    ctype: str,
    value: int,
    *,
    min_order: int = 0,
    uses_max: int = 0,
    one_per_user: int = 0,
    expires_at: str | None = None,
    personal_user_id: int | None = None,
    allowed_user_ids: list[int] | None = None,
    source: str = "admin_panel",
) -> str:
    """Rich HTML — підтвердження адміну про створення купона."""
    label = f"{value}%" if ctype == "percent" else f"{value} ₴"
    source_label = "адмін-панель" if source == "admin_panel" else "/coupon"
    parts = [
        "<h2>🎟️ Купон створено</h2>",
        "<hr/>",
        f"<p>Джерело: <b>{escape(source_label)}</b></p>",
        f"<p>🏷️ <b><code>{escape(code)}</code></b> — знижка {label}</p>",
    ]

    conditions: list[str] = []
    if min_order:
        conditions.append(f"<li>Мін. сума замовлення: {min_order} ₴</li>")
    if uses_max:
        conditions.append(f"<li>Макс. використань: {uses_max}</li>")
    else:
        conditions.append("<li>Макс. використань: безліміт</li>")
    if one_per_user:
        conditions.append("<li>Одноразовий для кожного користувача ⚡</li>")
    if expires_at:
        conditions.append(f"<li>Діє до: {format_date(expires_at)}</li>")
    if allowed_user_ids:
        ids_preview = ", ".join(f"<code>{uid}</code>" for uid in allowed_user_ids[:10])
        if len(allowed_user_ids) > 10:
            ids_preview += f" … (+{len(allowed_user_ids) - 10})"
        if len(allowed_user_ids) == 1:
            access_label = "Доступ лише для 1 користувача"
        else:
            access_label = f"Доступ лише для {len(allowed_user_ids)} користувачів"
        conditions.append(f"<li>{access_label}: {ids_preview}</li>")
    elif personal_user_id:
        conditions.append(f"<li>Доступ лише для user_id: <code>{personal_user_id}</code></li>")
    if conditions:
        parts.append("<ul>")
        parts.extend(conditions)
        parts.append("</ul>")

    return "\n".join(parts)


def build_broadcast_report(sent: int, failed: int, audience: str) -> str:
    return (
        "<h2>📨 Розсилка завершена</h2>"
        f"<p>Аудиторія: <b>{escape(audience)}</b></p>"
        "<hr/>"
        f"<p>✅ Надіслано: <b>{sent}</b></p>"
        f"<p>❌ Помилок/блокувань: <b>{failed}</b></p>"
    )


def build_start_welcome() -> str:
    return (
        "<h2>👋 Привіт! Я — Poselyanov 3D Print</h2>"
        "<p>Роблю 3D-принти на замовлення.</p>"
        "<p>Натисни кнопку нижче, щоб переглянути каталог 👇</p>"
    )


def build_catalog_hint() -> str:
    return "<h2>🛍️ Каталог Poselyanov 3D Print</h2><p>Натисни кнопку нижче 👇</p>"


def build_no_orders() -> str:
    return (
        "<h2>📭 Замовлень поки немає</h2>"
        "<p>Відкрий каталог і зроби перше замовлення! 🛍️</p>"
    )


def build_sales_empty() -> str:
    return (
        "<h2>😔 Зараз акцій немає</h2>"
        "<p>Слідкуй за оновленнями — знижки з'являться незабаром! 🔔</p>"
    )


GIFT_CATEGORY_LABELS = {
    "toy": "Іграшки",
    "anti": "Антистрес",
    "key": "Брелки",
    "gifts": "Подарунки та декор",
    "home": "Дім та побут",
    "gadget": "Для гаджетів",
    "tool": "Інструменти та приладдя",
    "custom": "Індивідуальне",
    "individual": "Індивідуальне",
}


def build_sales_list(promotions: list[dict]) -> str:
    """Реальні акції магазину (з /api/promotions), а не поштучні знижки товарів."""
    parts = ["<h2>🔥 Акції магазину</h2>", "<hr/>"]
    for promo in promotions:
        emoji = promo.get("emoji") or "🔥"
        title = escape(promo.get("title") or "Акція")
        subtitle = escape(promo.get("subtitle") or "")
        badge = escape(promo.get("badge") or "")
        ptype = promo.get("type")

        header = f"<p>{emoji} <b>{title}</b>"
        if badge:
            header += f" · <code>{badge}</code>"
        header += "</p>"
        parts.append(header)

        if ptype in ("order_percent", "order_fixed"):
            min_order = int(promo.get("min_order") or 0)
            if promo.get("type") == "order_percent":
                value_line = f"🏷️ −{int(promo.get('value') or 0)}% на всю суму замовлення"
            else:
                value_line = f"🏷️ −{int(promo.get('value') or 0)} ₴ на всю суму замовлення"
            parts.append(f"<blockquote><p>{value_line}<br/>📦 Від {min_order} ₴ у кошику</p></blockquote>")
        elif ptype == "gift_threshold":
            min_items = int(promo.get("min_items") or 0)
            max_gift_price = int(promo.get("max_gift_price") or 0)
            category = str(promo.get("category") or "").strip()
            category_label = GIFT_CATEGORY_LABELS.get(category)
            if category_label:
                category_link = f'<a href="{CATEGORY_LINK_BASE}{category}">{escape(category_label)}</a>'
                cart_line = f"🎁 Додай {min_items}+ товари з категорії «{category_link}» у кошик"
            else:
                cart_line = f"🎁 Додай {min_items}+ товари у кошик"
            parts.append(
                "<blockquote><p>"
                f"{cart_line}"
                f"<br/>✨ Обери подарунок вартістю до {max_gift_price} ₴"
                "</p></blockquote>"
            )
        elif subtitle:
            parts.append(f"<blockquote><p>{subtitle}</p></blockquote>")

        parts.append("<hr/>")

    parts.append("<footer>Акції діють автоматично при оформленні замовлення в каталозі 👇</footer>")
    return "\n".join(parts)


def build_mycoupons_empty() -> str:
    return (
        "<h2>🎟️ Персональних купонів немає</h2>"
        "<p>Слідкуй за новинами — іноді ми даруємо знижки! 🎁</p>"
    )


def build_mycoupons_list(rows: list[tuple], *, stacking_enabled: bool = False) -> str:
    parts = ["<h2>🎟️ Твої купони</h2>", "<hr/>", "<ul>"]
    for row in rows:
        code, ctype, value, min_order, uses_max, uses_count, one_per_user, expires_at = row[:8]
        stackable = bool(row[8]) and stacking_enabled
        used_by_user = row[9]
        label = f"{value}%" if ctype == "percent" else f"{value} ₴"
        lines = [f"🏷️ <b><code>{escape(code)}</code></b> — знижка {label}"]
        if stackable:
            lines.append("🔥 Сумується з акційною знижкою")
        if min_order:
            lines.append(f"Від суми: {min_order} ₴")
        if one_per_user:
            lines.append("⛔ Вже використано" if used_by_user else "⚡ Одноразовий")
        elif uses_max:
            left = max(0, uses_max - uses_count)
            lines.append(f"Залишилось використань: {left}")
        if expires_at:
            lines.append(f"Діє до: {format_date(expires_at)}")
        else:
            lines.append("Безстроковий ♾️")
        parts.append(f"<li>{'<br/>'.join(lines)}</li>")
    parts.extend([
        "</ul>",
        "<hr/>",
        "<footer>Введи код у кошику при оформленні замовлення 🛍️</footer>",
    ])
    return "\n".join(parts)


def build_contact() -> str:
    return (
        "<h2>📬 Контакти</h2>"
        "<hr/>"
        "<p>👤 <b>Денис Поселянов</b></p>"
        f'<p>💬 Написати особисто: <a href="{DENIS_LINK}">@denisposelyanov</a></p>'
        "<p>🤖 Бот магазину: @poselyanov3dprint_bot</p>"
        "<hr/>"
        "<footer>Відповідає зазвичай протягом кількох годин ⏰</footer>"
    )


def build_admin_panel_hint() -> str:
    return (
        "<h2>🔐 Адмін панель</h2>"
        "<ul>"
        "<li>➕ Додавати нові товари</li>"
        "<li>✏️ Редагувати існуючі товари</li>"
        "<li>🗑️ Видаляти товари</li>"
        "<li>📸 Завантажувати фото на Cloudinary</li>"
        "</ul>"
        "<footer>Натисни кнопку нижче, щоб відкрити панель</footer>"
    )


def build_notice(
    icon: str,
    title: str,
    *lines: str,
    items: list[str] | None = None,
    footer: str | None = None,
) -> str:
    """Універсальний rich-каркас: <h2> + <hr/> + <p>… + <ul> + <footer>.

    Рядки й пункти вставляються як є (HTML) — екрануй користувацькі дані через escape().
    """
    parts = [f"<h2>{icon} {title}</h2>", "<hr/>"]
    parts.extend(f"<p>{line}</p>" for line in lines if line)
    if items:
        parts.append("<ul>")
        parts.extend(f"<li>{item}</li>" for item in items)
        parts.append("</ul>")
    if footer:
        parts.append(f"<footer>{footer}</footer>")
    return "\n".join(parts)


def build_error_notice(reason: str, *hints: str, title: str = "Не вдалось") -> str:
    return build_notice("❌", title, escape(reason), items=[escape(h) for h in hints] or None)


def _bonus_line(bonus: dict | None) -> str:
    if not bonus:
        return ""
    return (
        f"🎁 Подяка: купон −{int(bonus['photo_percent'])}% за відгук із фото "
        f"або −{int(bonus['text_percent'])}% за змістовний текст."
    )


def build_review_request(order_id: int, bonus: dict | None = None) -> str:
    return build_notice(
        "⭐",
        f"Як тобі замовлення №{int(order_id)}?",
        "Постав оцінку одним дотиком — відгук допомагає іншим обрати.",
        _bonus_line(bonus),
        footer=f'<a href="{DENIS_LINK}">Денис</a>',
    )


def build_review_reminder(order_id: int, bonus: dict | None = None) -> str:
    return build_notice(
        "⭐",
        f"Замовлення №{int(order_id)}: поділишся враженням?",
        "Це займе хвилину, а мені — справжній орієнтир, що покращувати.",
        _bonus_line(bonus),
        footer=f'<a href="{DENIS_LINK}">Денис</a>',
    )


def build_review_bonus(percent: int, code: str, expires_at: str = "") -> str:
    lines = [
        f"Дякую за відгук! Тримай персональний купон <b>−{int(percent)}%</b> на наступне замовлення.",
        f"Код: <code>{escape(code)}</code>",
    ]
    if expires_at:
        lines.append(f"Діє до {escape(expires_at)}, одноразовий — лише для тебе.")
    lines.append("Натисни «Застосувати купон» — магазин відкриється, а знижка підставиться сама, щойно додаси товар у кошик.")
    return build_notice("🎁", "Купон за відгук", *lines, footer=f'<a href="{DENIS_LINK}">Денис</a>')


def build_manual_client_message(text: str) -> str:
    """Довільний текст від Дениса клієнту — абзаци + підпис."""
    paragraphs = "".join(f"<p>{escape(line)}</p>" for line in text.splitlines() if line.strip())
    return paragraphs + f'\n<footer><a href="{DENIS_LINK}">Денис</a></footer>'


def build_review_moderation(review: dict, product_name: str) -> str:
    stars = "★" * int(review["rating"]) + "☆" * (5 - int(review["rating"]))
    links = [
        f'<a href="{escape(url)}">фото {n}</a>' for n, url in enumerate(review.get("photos") or [], 1)
    ]
    parts = [
        f"<h2>⭐ Новий відгук #{review['id']}</h2>",
        "<hr/>",
        f"<p><b>{escape(product_name)}</b> · замовлення #{review['order_id']}</p>",
        f"<p>{stars} · {escape(review.get('author_name') or 'Покупець')}</p>",
        f"<blockquote><p>{escape(review.get('text') or '—')}</p></blockquote>",
    ]
    if links:
        parts.append(f"<p>📷 {' · '.join(links)}</p>")
    parts.append("<footer>Модерація — кнопки нижче</footer>")
    return "\n".join(parts)


def build_contact_card(uid: int, name: str) -> str:
    return build_notice(
        "👤",
        "Контакт клієнта",
        f'<a href="tg://user?id={int(uid)}">{escape(name)}</a>',
        f"ID: <code>{int(uid)}</code>",
        footer="Натисни ім'я, щоб відкрити чат у Telegram",
    )


def build_bot_error(err_text: str, update_id: int | None = None) -> str:
    lines = [f"<code>{escape(err_text)}</code>"]
    if update_id is not None:
        lines.append(f"update_id: <code>{int(update_id)}</code>")
    return build_notice("⚠️", "Помилка бота", *lines)


def build_myid(user_id: int) -> str:
    return build_notice("🆔", "Твій Telegram ID", f"<code>{int(user_id)}</code>")


def build_cache_reloaded(products: int, custom: int, filaments: int) -> str:
    return build_notice(
        "✅",
        "Кеш оновлено",
        items=[f"Товари: <b>{products}</b>", f"Індивідуальні: <b>{custom}</b>", f"Філаменти: <b>{filaments}</b>"],
    )


def build_stats(
    user_count, order_count, order_confirmed, order_draft, order_cancelled, earned,
    recent, top_products, coupon_stats, total_discount,
) -> str:
    parts = [
        "<h2>📊 Статистика</h2>",
        "<hr/>",
        "<ul>",
        f"<li>👥 Користувачів: <b>{user_count}</b></li>",
        f"<li>📦 Замовлень всього: <b>{order_count}</b></li>",
        f"<li>✅ Підтверджених: <b>{order_confirmed}</b></li>",
        f"<li>❓ Під питанням: <b>{order_draft}</b></li>",
        f"<li>❌ Відмінених: <b>{order_cancelled}</b></li>",
        f"<li>💰 Зароблено: <b>{earned} ₴</b></li>",
        "</ul>",
    ]
    if recent:
        parts.extend(["<hr/>", "<p><b>🕐 Останні користувачі</b></p>", "<ul>"])
        for uid, name, username in recent:
            raw = str(username or "").strip()
            if raw.startswith("@") and raw[1:].strip() not in ("", "невідомо", "—"):
                parts.append(f"<li>{escape(name)} {escape(raw)}</li>")
            else:
                parts.append(f"<li>{escape(name)} — ID <code>{escape(uid)}</code></li>")
        parts.append("</ul>")
    if top_products:
        parts.extend(["<hr/>", "<p><b>🏆 Топ товари</b></p>", "<ul>"])
        parts.extend(f"<li>{escape(name)} — {cnt} шт</li>" for name, cnt in top_products)
        parts.append("</ul>")
    if coupon_stats:
        parts.extend(["<hr/>", "<p><b>🎟️ Купони (топ-3)</b></p>", "<ul>"])
        parts.extend(
            f"<li><code>{escape(code)}</code> — {uses} раз, −{disc} ₴</li>"
            for code, uses, disc in coupon_stats
        )
        parts.append("</ul>")
        parts.append(f"<footer>💸 Всього знижок: <b>{total_discount} ₴</b></footer>")
    return "\n".join(parts)


def _code(text: str) -> str:
    return f"<code>{escape(text)}</code>"


def build_coupon_help() -> str:
    c = _code
    return "\n".join([
        "<h2>🎟️ Управління купонами</h2>",
        "<hr/>",
        "<p><b>Створити купон</b></p>",
        "<ul>",
        f"<li>{c('/coupon add КОД percent 20')} — знижка 20%</li>",
        f"<li>{c('/coupon add КОД fixed 50')} — знижка 50 ₴</li>",
        "</ul>",
        "<p><b>Додаткові опції</b></p>",
        "<ul>",
        f"<li>{c('min=200')} — мінімальна сума замовлення</li>",
        f"<li>{c('max=10')} — максимум використань</li>",
        f"<li>{c('once')} — одноразовий (один юзер — один раз)</li>",
        f"<li>{c('expires=2025-12-31')} — термін дії</li>",
        f"<li>{c('user=123456789')} — один користувач (legacy)</li>",
        f"<li>{c('users=111,222,333')} — доступ лише для цих Telegram ID</li>",
        "</ul>",
        "<p><b>Приклади</b></p>",
        "<ul>",
        f"<li>{c('/coupon add ЛІТО percent 15 min=300 expires=2026-08-31')}</li>",
        f"<li>{c('/coupon add УЧНІ percent 10 expires=2027-05-31 users=111,222')}</li>",
        f"<li>{c('/coupon add VIP fixed 100 once user=718746623')}</li>",
        "</ul>",
        "<p><b>Інші команди</b></p>",
        "<ul>",
        f"<li>{c('/coupon list')} — всі купони</li>",
        f"<li>{c('/coupon adduser КОД 123456789')} — додати ID до купона</li>",
        f"<li>{c('/coupon disable КОД')} — вимкнути</li>",
        f"<li>{c('/coupon enable КОД')} — увімкнути</li>",
        f"<li>{c('/coupon delete КОД')} — видалити</li>",
        "</ul>",
    ])


def build_coupon_list(rows: list[dict]) -> str:
    items = []
    for c in rows:
        label = f"{c['value']}%" if c["type"] == "percent" else f"{c['value']} ₴"
        status = "✅" if c.get("active") else "🚫"
        uses_count = int(c.get("uses_count") or 0)
        uses_max = int(c.get("uses_max") or 0)
        uses_str = f"{uses_count}/{uses_max}" if uses_max else f"{uses_count}/∞"
        line = f"{status} {_code(c['code'])} — {label} · {uses_str}"
        if c.get("expires_at"):
            line += f" · до {format_date(c['expires_at'])}"
        allowed = c.get("allowed_user_ids") or []
        if len(allowed) == 1:
            line += " · лише для 1 користувача"
        elif allowed:
            line += f" · лише для {len(allowed)} користувачів"
        elif c.get("personal_user_id"):
            line += f" · лише для ID {_code(c['personal_user_id'])}"
        items.append(line)
    return build_notice("🎟️", "Всі купони", items=items, footer=f"Всього: <b>{len(rows)}</b>")


def build_broadcast_help() -> str:
    c = _code
    return build_notice(
        "📨",
        "Розсилка",
        "Відповідай (reply) на повідомлення командою:",
        items=[
            f"{c('/broadcast')} — усім",
            f"{c('/broadcast <product_id>')} — усім + кнопка товару",
            f"{c('/broadcast u:<user_id>')} — конкретному юзеру",
            f"{c('/broadcast u:<user_id> <product_id>')} — юзеру + кнопка товару",
        ],
        footer="💡 Rich-форматування (заголовки, списки, цитати) збережеться при розсилці",
    )
