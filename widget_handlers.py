"""Віджети чату й тимчасові повідомлення (ROADMAP.md, п. 4, 5, 7, 11, 12, 13, 15): відправка, команди, запити.

Збирачі HTML і даних — у services/chat_widgets.py. Тут усе, що говорить із Telegram.
Кожна функція відправки повертає False, якщо віджети вимкнені для користувача або Telegram
відмовив: тоді той, хто викликав, шле звичайне текстове повідомлення, як раніше.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import config
from catalog_store import get_all_categories, get_all_products, get_product_by_id
from db_core import run_db
from rich_messages import BOT_LINK_BASE, _serialize_markup, send_rich_message
from services import chat_widgets as cw
from services import giveaways as giveaways_svc
from services.orders import get_order_with_items
from widget_messages import _delete_quietly, answer_widget_query, edit_widget_caption, edit_widget_data, send_widget

logger = logging.getLogger(__name__)

# Заповнює bot.py: confirm_order(order_id) -> bool, admin_url() -> str | None
hooks: dict = {}

HITS_DAYS = 30
HITS_FALLBACK_DAYS = 90
_KYIV = ZoneInfo("Europe/Kyiv")


# ─── Відправка ───────────────────────────────────────────────────────────────

async def _send(bot, user_id: int, widget: dict, *, reply_markup=None, label: str = "віджет") -> int | None:
    try:
        return await send_widget(
            bot, int(user_id), widget["html"], chat_data=widget.get("data") or "",
            caption=widget.get("caption") or "", reply_markup=reply_markup,
        )
    except Exception as e:
        logger.warning("%s для %s не надіслано, працює текстовий варіант: %s", label, user_id, e)
        return None


async def send_ephemeral(bot, query, text: str, *, replace: bool = False, reply_markup=None) -> dict | None:
    """Тимчасова відповідь на кнопку: бачить лише той, хто натиснув (Bot API 10.3/10.4).

    replace — показати на місці повідомлення з кнопкою. None, якщо Telegram відмовив.
    """
    payload = {
        "chat_id": query.message.chat_id if query.message else query.from_user.id,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
        "ephemeral_message_parameters": {"callback_query_id": str(query.id), "replace_callback_query_message": bool(replace)},
    }
    markup = _serialize_markup(reply_markup)
    if markup:
        payload["reply_markup"] = markup
    try:
        result = await bot.do_api_request("sendMessage", payload)
        return result if isinstance(result, dict) else {"message_id": getattr(result, "message_id", None)}
    except Exception as e:
        logger.info("Тимчасове повідомлення не надіслано: %s", e)
        return None


# ─── 4. Кошторис ─────────────────────────────────────────────────────────────

async def send_quote(bot, order_id: int) -> bool:
    """Після встановлення ціни: розбивка з кнопками «Погоджуюсь» / «Змінити»."""
    order, items = await run_db(get_order_with_items, int(order_id))
    if not order or order.get("price_pending") or order.get("status") not in ("new", "draft"):
        return False
    user_id = int(order.get("user_id") or 0)
    if user_id <= 0 or int(order.get("total_price") or 0) <= 0 or not cw.enabled_for(user_id, "quote"):
        return False
    try:
        widget = cw.build_quote_widget(order, items)
    except ValueError as e:
        logger.warning("Кошторис #%s не зібрано: %s", order_id, e)
        return False
    return bool(await _send(bot, user_id, widget, label="Кошторис"))


async def _quote_agree(bot, user_id: int, chat_id: int, message_id: int, order_id: int) -> str:
    order, _ = await run_db(get_order_with_items, order_id)
    if not order or int(order.get("user_id") or 0) != int(user_id) or not cw.enabled_for(user_id, "quote"):
        return ""
    if order.get("status") in ("new", "draft"):
        # Погодити можна лише вже встановлену ціну: інакше клієнт підтвердить замовлення з нульовою сумою.
        if order.get("price_pending") or int(order.get("total_price") or 0) <= 0:
            return ""
        confirm = hooks.get("confirm_order")
        if not confirm or not await confirm(order_id):
            return ""
    elif order.get("status") == "cancelled":
        return ""
    state = cw.dumps({"a": 1})
    try:
        await edit_widget_data(bot, chat_id, message_id, state)
    except Exception as e:
        logger.info("Кошторис #%s: стан не збережено у віджеті: %s", order_id, e)
    return state


# ─── 5. Розіграш ─────────────────────────────────────────────────────────────

async def _giveaway_state(g: dict, user_id: int) -> dict | None:
    entry = await run_db(giveaways_svc.get_entry, g["id"], user_id)
    if not entry:
        return None
    tickets = await run_db(giveaways_svc.tickets_preview, g, user_id)
    entries = await run_db(giveaways_svc.entries_count, g["id"])
    status = "active" if giveaways_svc.is_open(g) else ("cancelled" if g.get("status") == "cancelled" else "done")
    return cw.giveaway_state(entries, tickets, int(entry["number"]), status)


async def send_giveaway(bot, user_id: int, g: dict) -> bool:
    if not cw.enabled_for(user_id, "giveaway"):
        return False
    state = await _giveaway_state(g, user_id)
    if not state:
        return False
    link = giveaways_svc.join_link(config.BOT_USERNAME, g["id"], user_id) if g.get("referral_bonus") else ""
    try:
        widget = cw.build_giveaway_widget(g, state, link)
    except ValueError as e:
        logger.warning("Віджет розіграшу %s не зібрано: %s", g.get("id"), e)
        return False
    return bool(await _send(bot, user_id, widget, label="Розіграш"))


def my_tickets_text(g: dict, state: dict | None, user_id: int) -> str:
    title = html.escape(g.get("title") or "Розіграш")
    if not state:
        link = giveaways_svc.join_link(config.BOT_USERNAME, g["id"])
        return f"🎁 «{title}»\n\nТи ще не береш участі. Приєднатися: {html.escape(link)}"
    lines = [f"🎁 «{title}»", f"Твій номер: <b>№{state['no']}</b>", f"Шансів: <b>{state['t']}</b> · учасників: {state['n']}"]
    if g.get("referral_bonus"):
        link = giveaways_svc.join_link(config.BOT_USERNAME, g["id"], user_id)
        lines += ["", f"+1 шанс за кожного друга: {html.escape(link)}"]
    return "\n".join(lines)


async def my_tickets_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка «🎟 Мої квитки» під постом розіграшу: відповідь бачить лише той, хто натиснув."""
    query = update.callback_query
    try:
        giveaway_id = int((query.data or "").rsplit("_", 1)[-1])
    except ValueError:
        await query.answer()
        return
    g = await run_db(giveaways_svc.get_giveaway, giveaway_id)
    if not g or not query.from_user:
        await query.answer("Розіграш не знайдено", show_alert=True)
        return
    state = await _giveaway_state(g, query.from_user.id)
    text = my_tickets_text(g, state, query.from_user.id)
    if await send_ephemeral(context.bot, query, text):
        await query.answer()
        return
    # Старі застосунки й чати без підтримки тимчасових повідомлень: спливне вікно
    plain = f"Номер №{state['no']}, шансів: {state['t']}, учасників: {state['n']}" if state else "Ти ще не береш участі"
    await query.answer(plain, show_alert=True)


# ─── 7. Акції та купони ──────────────────────────────────────────────────────

COUPONS_BUTTON = InlineKeyboardButton("🎟 Мої купони", callback_data="mycpn")


async def send_promos(bot, user_id: int, promotions: list[dict]) -> bool:
    if not promotions or not cw.enabled_for(user_id, "promo"):
        return False
    try:
        widget = cw.build_promo_widget(promotions)
    except ValueError as e:
        logger.warning("Віджет акцій не зібрано: %s", e)
        return False
    return bool(await _send(bot, user_id, widget, reply_markup=InlineKeyboardMarkup([[COUPONS_BUTTON]]), label="Акції"))


def coupons_text(rows: list) -> str:
    """Короткий список купонів для тимчасової відповіді (повний — /mycoupons)."""
    if not rows:
        return "🎟 Активних купонів поки немає. Слідкуй за акціями в каналі."
    lines = ["🎟 <b>Твої купони</b>"]
    for row in rows[:8]:
        row = dict(row) if isinstance(row, dict) else {"code": row[0], "type": row[1], "value": row[2], "min_order": row[3]}
        value = int(row.get("value") or 0)
        deal = f"−{value}%" if row.get("type") == "percent" else f"−{value} ₴"
        tail = f" від {int(row.get('min_order') or 0)} ₴" if int(row.get("min_order") or 0) else ""
        lines.append(f"<code>{html.escape(str(row.get('code') or ''))}</code> · {deal}{tail}")
    lines.append("\nВведи код при оформленні замовлення.")
    return "\n".join(lines)


async def my_coupons_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка «🎟 Мої купони»: тимчасова відповідь, щоб не засмічувати чат."""
    from services.coupons import get_my_coupons

    query = update.callback_query
    rows = await run_db(get_my_coupons, query.from_user.id)
    text = coupons_text(list(rows or []))
    if not await send_ephemeral(context.bot, query, text):
        await context.bot.send_message(chat_id=query.from_user.id, text=text, parse_mode="HTML")
    await query.answer()


# ─── 11. Кошик у чаті ────────────────────────────────────────────────────────

def _cart_discount(subtotal: int, cat_totals: dict) -> int:
    from services.promotions import compute_order_discount

    try:
        return compute_order_discount(subtotal, cat_totals)
    except Exception:
        logger.warning("Кошик чату: знижку акції не пораховано", exc_info=True)
        return 0


def _cart_state(items: dict[int, int]) -> dict:
    return cw.cart_state(items, get_product_by_id, _cart_discount)


async def show_cart(bot, user_id: int, *, resend: bool = False, items: dict | None = None,
                    message_id=None) -> dict | None:
    """Показати кошик: оновити наявний віджет або надіслати новий. Повертає стан; None — віджет недоступний."""
    if items is None:
        cart = await run_db(cw.get_cart, user_id)
        items, message_id = cart["items"], cart["message_id"]
    state = await run_db(_cart_state, items)
    data, caption = cw.dumps(state), cw.cart_caption(state)
    if message_id and not resend:
        try:
            await edit_widget_data(bot, user_id, int(message_id), data)
            try:
                await edit_widget_caption(bot, user_id, int(message_id), caption)
            except Exception:
                pass  # підпис не змінився
            return state
        except Exception as e:
            logger.info("Кошик %s: віджет недоступний, надсилаю новий: %s", user_id, e)
    new_id = await _send(bot, user_id, {"html": cw.build_cart_widget(), "data": data, "caption": caption}, label="Кошик")
    if not new_id:
        return None
    if message_id:
        await _delete_quietly(bot, user_id, message_id)
    await run_db(cw.save_cart, user_id, items, new_id)
    return state


async def cart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if cw.enabled_for(user_id, "cart") and await show_cart(context.bot, user_id, resend=True) is not None:
        return
    await send_rich_message(
        context.bot, update.message.chat_id,
        "<h2>🛒 Кошик</h2><p>Кошик зберігається в каталозі — відкрий його кнопкою меню й оформи замовлення там.</p>",
    )


async def _cart_query(bot, user_id: int, message_id: int, parts: list[str]) -> str:
    op = parts[1] if len(parts) > 1 else ""
    cart = await run_db(cw.get_cart, user_id)
    items, cart_message = cart["items"], cart["message_id"]
    if op == "s":
        state = await show_cart(bot, user_id, resend=True, items=items, message_id=cart_message)
        return cw.dumps({"n": cw.cart_count(state or {})})
    try:
        product_id = int(parts[2])
    except (IndexError, ValueError):
        return ""
    product = await run_db(get_product_by_id, product_id)
    items, error = cw.change_cart(items, op, product_id, product)
    await run_db(cw.save_cart, user_id, items)
    state = await run_db(_cart_state, items)
    state["m"] = error
    from_cart = bool(cart_message) and int(cart_message) == int(message_id)
    if from_cart:
        # Натиснули в самому кошику: він намалює відповідь, а дані збережемо для наступного відкриття
        try:
            await edit_widget_data(bot, user_id, message_id, cw.dumps({**state, "m": ""}))
            await edit_widget_caption(bot, user_id, message_id, cw.cart_caption(state))
        except Exception as e:
            logger.info("Кошик %s: дані у віджеті не оновлено: %s", user_id, e)
        return cw.dumps(state)
    # Додали з вітрини: кошик оновлюємо (або шлемо вперше) окремим повідомленням
    if not error:
        await show_cart(bot, user_id, items=items, message_id=cart_message)
    return cw.dumps({"n": cw.cart_count(state), "m": error})


# ─── 13. Вітрини: хіти й добірки ─────────────────────────────────────────────

def _hit_products(limit: int = cw.SHOWCASE_MAX) -> list[dict]:
    from services.hits import top_selling_products

    active = {int(p["id"]): p for p in get_all_products() if p.get("id") is not None}
    for days in (HITS_DAYS, HITS_FALLBACK_DAYS):
        found = [active[h["product_id"]] for h in top_selling_products(days, 24) if h["product_id"] in active]
        if len(found) >= 3:
            return found[:limit]
    hot = [p for p in active.values() if p.get("hot")]
    return (found + [p for p in hot if p not in found])[:limit]


def _collection_products(collection_id: str, limit: int = cw.SHOWCASE_MAX) -> tuple[str, list[dict]]:
    from services.collections import matching_products, visible_collections
    from services.hits import top_selling_products

    for item in visible_collections():
        if item["id"] != collection_id:
            continue
        pool = [p for p in get_all_products() if p.get("id") is not None]
        if item.get("mode") == "manual":
            by_id = {int(p["id"]): p for p in pool}
            found = [by_id[pid] for pid in item.get("product_ids", []) if pid in by_id]
        else:
            hit_ids = [int(h["product_id"]) for h in top_selling_products(90, 24)]
            found = matching_products(item.get("rule") or {}, pool, hit_ids)
        return item.get("name") or "Добірка", found[:limit]
    return "", []


def showcase_tabs() -> list[list[str]]:
    from services.collections import visible_collections

    tabs = [["hits", "Хіти"]]
    try:
        for item in visible_collections():
            if (item.get("rule") or {}).get("type") == "hits":
                continue  # дублює першу вкладку
            tabs.append([item["id"], item.get("name") or item["id"]])
    except Exception:
        logger.warning("Вітрина: добірки недоступні", exc_info=True)
    return tabs[:4]


def showcase_content(kind: str) -> tuple[str, list[list]]:
    """(заголовок, рядки товарів) для вкладки: hits або id добірки."""
    if kind and kind != "hits":
        title, products = _collection_products(kind)
        if title:
            return title, cw.showcase_rows(products)
    return "Хіти продажів", cw.showcase_rows(_hit_products())


def _rows_for(user_id: int, rows: list[list]) -> list[list]:
    """Без віджета кошика «+» у вітринах не показуємо: товар відкривається в застосунку."""
    if cw.enabled_for(user_id, "cart"):
        return rows
    return [[*row[:4], 0] for row in rows]


def products_list_html(title: str, rows: list[list], footer: str = "") -> str:
    """Текстовий варіант вітрини — для тих, кому віджети вимкнено або не підтримуються."""
    parts = [f"<h2>{html.escape(title)}</h2>", "<ul>"]
    for pid, name, price, old, _ in rows:
        was = f" <s>{old} ₴</s>" if old else ""
        parts.append(f'<li><a href="{BOT_LINK_BASE}{pid}">{html.escape(name)}</a> —{was} {price} ₴</li>')
    parts.append("</ul>")
    if footer:
        parts.append(f"<footer>{html.escape(footer)}</footer>")
    return "\n".join(parts)


async def hits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    title, rows = await run_db(showcase_content, "hits")
    if not rows:
        await send_rich_message(context.bot, update.message.chat_id, "<p>Поки що хітів немає — зазирни в каталог 🛍</p>")
        return
    if cw.enabled_for(user_id, "hits"):
        rows = _rows_for(user_id, rows)
        tabs = await run_db(showcase_tabs)
        widget = cw.build_showcase_widget(title, rows, kind="hits", tabs=tabs if len(tabs) > 1 else [],
                                          subtitle="Найчастіше замовляють. Ціни оновлюються самі")
        if await _send(context.bot, user_id, widget, label="Вітрина"):
            return
    await send_rich_message(context.bot, update.message.chat_id,
                            products_list_html(f"🔥 {title}", rows, "Тисни на назву — відкриється товар у каталозі"))


# ─── 12. Підбір подарунка ────────────────────────────────────────────────────

def _gift_keyboard(options: dict, prefix: str) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(label[0], callback_data=f"{prefix}_{key}") for key, label in options.items()]
    return InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)])


GIFT_STEP_1 = "🎁 <b>Підберу подарунок</b>\n\nКрок 1 із 3 · Для кого?"


async def gift_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.message.chat_id, text=GIFT_STEP_1, parse_mode="HTML",
        reply_markup=_gift_keyboard(cw.GIFT_WHO, "gf"),
    )


def gift_products(who: str, budget: str, occasion: str) -> list[dict]:
    from services.hits import top_selling_products

    products = [p for p in get_all_products() if p.get("id") is not None]
    categories = {str(c.get("id")): str(c.get("name") or "") for c in get_all_categories()}
    hit_ids = [int(h["product_id"]) for h in top_selling_products(90, 24)]
    return cw.pick_gifts(who, budget, occasion, products, categories=categories, hit_ids=hit_ids)


async def gift_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кроки підбору: gf_<кому> → gf_<кому>_<бюджет> → gf_<кому>_<бюджет>_<привід>."""
    query = update.callback_query
    await query.answer()
    picked = (query.data or "").split("_")[1:]
    if not picked or picked[0] not in cw.GIFT_WHO:
        return
    who = picked[0]
    if len(picked) == 1:
        await query.edit_message_text(
            f"🎁 <b>Підберу подарунок</b>\n{cw.GIFT_WHO[who][0]}\n\nКрок 2 із 3 · Який бюджет?",
            parse_mode="HTML", reply_markup=_gift_keyboard(cw.GIFT_BUDGET, f"gf_{who}"),
        )
        return
    budget = picked[1]
    if budget not in cw.GIFT_BUDGET:
        return
    if len(picked) == 2:
        await query.edit_message_text(
            f"🎁 <b>Підберу подарунок</b>\n{cw.GIFT_WHO[who][0]} · {cw.GIFT_BUDGET[budget][0]}\n\nКрок 3 із 3 · Який привід?",
            parse_mode="HTML", reply_markup=_gift_keyboard(cw.GIFT_OCCASION, f"gf_{who}_{budget}"),
        )
        return
    occasion = picked[2] if picked[2] in cw.GIFT_OCCASION else "none"
    summary = cw.gift_summary(who, budget, occasion)
    rows = cw.showcase_rows(await run_db(gift_products, who, budget, occasion))
    user_id, chat_id = query.from_user.id, query.message.chat_id
    again = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Підібрати ще раз", callback_data="gf_again")]])
    if not rows:
        await query.edit_message_text(f"🎁 {summary}\n\nНічого не знайшов у цих межах — спробуй інший бюджет.",
                                      reply_markup=again)
        return
    if chat_id == user_id and cw.enabled_for(user_id, "gift"):
        widget = cw.build_showcase_widget("Ось що раджу", _rows_for(user_id, rows), kind="gift", subtitle=summary, accent="pink")
        if await _send(context.bot, user_id, widget, label="Підбір подарунка"):
            await query.edit_message_text(f"🎁 Підібрав: {summary}", reply_markup=again)
            return
    await query.edit_message_text(f"🎁 Підібрав: {summary}", reply_markup=again)
    await send_rich_message(context.bot, chat_id, products_list_html("🎁 Ось що раджу", rows, summary))


async def gift_again_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(GIFT_STEP_1, parse_mode="HTML", reply_markup=_gift_keyboard(cw.GIFT_WHO, "gf"))


# ─── 15. Адмінський дашборд ──────────────────────────────────────────────────

def dash_state() -> dict:
    from services.events import popular_searches
    from services.overview import get_overview

    def cut(overview: dict) -> dict:
        started = next((f["count"] for f in overview.get("funnel", []) if f.get("key") == "started"), 0)
        kpi = overview.get("kpi", {})
        return {"orders": kpi.get("orders", {}).get("value"), "revenue": kpi.get("revenue", {}).get("value"),
                "started": started}

    today, week = get_overview(1), get_overview(7)
    attention = today.get("attention", {})
    try:
        searches = popular_searches(7, 4)
    except Exception:
        searches = []
    return cw.dash_state(
        cut(today), cut(week),
        queue=int(attention.get("new_orders", {}).get("count") or 0),
        pending_price=len(attention.get("pending_price", {}).get("items") or []),
        searches=searches, stamp=datetime.now(_KYIV).strftime("%H:%M"),
    )


def dash_text(state: dict) -> str:
    t, w = state["t"], state["w"]
    lines = [
        "📊 <b>Магазин сьогодні</b>",
        f"Замовлень: <b>{t[0]}</b> (за 7 днів — {w[0]})",
        f"Виручка: <b>{t[1]} ₴</b> (за 7 днів — {w[1]} ₴)",
        f"Нові клієнти: <b>{t[2]}</b> (за 7 днів — {w[2]})",
        f"Нових у черзі: <b>{state['q']}</b>" + (f" · без ціни: {state['pp']}" if state["pp"] else ""),
    ]
    if state["s"]:
        lines.append("Шукають: " + html.escape(", ".join(state["s"])))
    return "\n".join(lines)


async def dash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dash — лише власнику, у приватному чаті: закріплений віджет зі зведенням дня."""
    user = update.effective_user
    if not user or int(user.id) != int(config.OWNER_ID or 0):
        return
    state = await run_db(dash_state)
    chat_id = update.message.chat_id
    if chat_id == user.id and cw.enabled_for(user.id, "dash"):
        admin_url = (hooks.get("admin_url") or (lambda: ""))() or ""
        try:
            widget = {"html": cw.build_dash_widget(admin_url), "data": cw.dumps(state),
                      "caption": f"📊 Сьогодні: {state['t'][0]} замовл. · {state['t'][1]} ₴ · у черзі {state['q']}"}
            message_id = await _send(context.bot, user.id, widget, label="Дашборд")
        except ValueError:
            message_id = None
        if message_id:
            try:
                await context.bot.pin_chat_message(chat_id=chat_id, message_id=message_id, disable_notification=True)
            except Exception as e:
                logger.info("Дашборд не закріплено: %s", e)
            return
    await context.bot.send_message(chat_id=chat_id, text=dash_text(state), parse_mode="HTML")


# ─── Запити від віджетів ─────────────────────────────────────────────────────

async def handle_widget_query(bot, query: dict) -> bool:
    """Розбір `widget_query` для віджетів чату. False — запит не наш (його обробить віджет статусу)."""
    data = str(query.get("data") or "")
    head = data.split(":", 1)[0]
    if head not in ("q", "g", "c", "h", "d"):
        return False
    user_id = int((query.get("from") or {}).get("id") or 0)
    message_id = int(query.get("message_id") or 0)
    chat_id = int((query.get("chat") or {}).get("id") or user_id)
    parts = data.split(":")
    answer = ""
    try:
        if head == "q" and len(parts) == 3 and parts[1] == "a":
            answer = await _quote_agree(bot, user_id, chat_id, message_id, int(parts[2]))
        elif head == "g" and len(parts) == 2:
            g = await run_db(giveaways_svc.get_giveaway, int(parts[1]))
            state = await _giveaway_state(g, user_id) if g else None
            answer = cw.dumps(state) if state else ""
        elif head == "c":
            answer = await _cart_query(bot, user_id, message_id, parts) if cw.enabled_for(user_id, "cart") else ""
        elif head == "h":
            title, rows = await run_db(showcase_content, parts[1] if len(parts) > 1 else "hits")
            cart = await run_db(cw.get_cart, user_id)
            answer = cw.dumps({"l": _rows_for(user_id, rows), "h": title, "n": sum(cart["items"].values())})
        elif head == "d" and user_id == int(config.OWNER_ID or 0):
            answer = cw.dumps(await run_db(dash_state))
    except Exception:
        logger.warning("widget_query %r: обробка не вдалась", data, exc_info=True)
    try:
        await answer_widget_query(bot, str(query.get("id") or ""), answer[: cw.ANSWER_MAX])
    except Exception as e:
        logger.info("answerWidgetQuery: %s", e)
    return True


def parse_json(text: str) -> dict:
    try:
        return json.loads(text or "{}")
    except ValueError:
        return {}
