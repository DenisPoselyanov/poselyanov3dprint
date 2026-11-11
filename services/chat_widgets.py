"""Віджети чату (Bot API 10.4), пункти 4, 5, 7, 11, 12, 13, 15 з ROADMAP.md.

Тут лише чисті збирачі: HTML віджета, його дані (`chat_data`, до 1024 байт), підпис, а також
кошик чату й правила підбору подарунка. Відправка й обробка запитів — у widget_handlers.py.

Спільні правила (як у services/order_widgets.py):
- HTML після відправки не змінюється, тому все змінне лежить у даних або приходить відповіддю на запит;
- запит від віджета — до 64 байт, відповідь — до 1024 байт;
- у віджеті немає мережі, полів введення й прокрутки; стиль — картки акцій (градієнт, тег-пігулка).
"""

from __future__ import annotations

import html as _html
import json
import re
from datetime import datetime, timezone

import config
from db_core import db_connect, is_postgres, sql as _sql
from services.order_widgets import WIDGET_DATA_MAX, check_widget_size

DENIS_LINK = "https://t.me/denisposelyanov"
CART_MAX_LINES = 6
CART_MAX_QTY = 20
SHOWCASE_MAX = 5
QUOTE_MAX_ROWS = 8
NAME_MAX = 26
ANSWER_MAX = 1024

# ─── Спільна основа ──────────────────────────────────────────────────────────

_BASE_CSS = """
body{margin:0;padding:0}
#w{--i:light-dark(var(--d),var(--l));padding:16px 16px 14px;line-height:1.3;background:linear-gradient(135deg,var(--t),var(--z) 72%)}
.blue{--l:#4BB7E5;--h:#289CDB;--d:#1B7FBA;--t:#289CDB1F;--z:#289CDB00;--e:#289CDB26}
.amber{--l:#FFC93C;--h:#F2A900;--d:#B97C00;--t:#F2A9001F;--z:#F2A90000;--e:#F2A90026}
.violet{--l:#A58BFF;--h:#7C5CFC;--d:#5B3FD6;--t:#7C5CFC1F;--z:#7C5CFC00;--e:#7C5CFC26}
.orange{--l:#FFA957;--h:#F5822A;--d:#C96112;--t:#F5822A1F;--z:#F5822A00;--e:#F5822A26}
.green{--l:#4CC38A;--h:#22A06B;--d:#167C52;--t:#22A06B1F;--z:#22A06B00;--e:#22A06B26}
.red{--l:#FF7A7E;--h:#E5484D;--d:#BC2B30;--t:#E5484D1F;--z:#E5484D00;--e:#E5484D26}
.pink{--l:#FF8FC2;--h:#E64C97;--d:#B92C73;--t:#E64C971F;--z:#E64C9700;--e:#E64C9726}
.teal{--l:#4FD1C5;--h:#14A89B;--d:#0E7F75;--t:#14A89B1F;--z:#14A89B00;--e:#14A89B26}
#w *{box-sizing:border-box}
.eye{display:inline-block;padding:3px 9px;border-radius:99px;background:var(--e);color:var(--i);font-size:10px;font-weight:800;letter-spacing:.11em;text-transform:uppercase}
h3{margin:8px 0 3px;font-size:21px;line-height:1.12;font-weight:800;letter-spacing:-.02em}
p{margin:0;font-size:13px;color:var(--tg-theme-hint-color)}
button{border:0;border-radius:99px;padding:10px 14px;font:inherit;font-size:14px;font-weight:700;background:var(--h);color:#fff}
.g{background:var(--e);color:var(--i)}
.wide{display:block;width:100%;margin-top:12px}
.two{display:flex;gap:8px;margin-top:12px}.two button{flex:1}
.rows{margin-top:10px;font-size:13.5px}
.row{display:flex;align-items:center;gap:10px;padding:4px 0}
.k{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.v{white-space:nowrap;font-variant-numeric:tabular-nums}
.sum{font-weight:800;font-size:15px;margin-top:6px;padding-top:9px;border-top:1px solid var(--tg-theme-section-separator-color)}
s{color:var(--tg-theme-hint-color);font-weight:500;margin-right:5px}
.q{font-size:12.5px;color:var(--tg-theme-hint-color)}
.mini{padding:5px 0;width:30px;height:30px;line-height:20px;font-size:16px}
.b{font-weight:800;color:var(--i)}
[hidden]{display:none!important}
"""

# W — API віджета, S() — стан із chat_data, ask() — запит до бота з розбором JSON-відповіді.
_BASE_JS = """
const W=window.TelegramWidget,$=i=>document.getElementById(i),H=(i,h)=>h?$(i).setAttribute('hidden',''):$(i).removeAttribute('hidden'),B='https://t.me/%(bot)s?startapp=',D='%(denis)s';
const TG=u=>W&&W.openTelegramLink(u),E=s=>String(s).replace(/[&<>"]/g,c=>'&#'+c.charCodeAt(0)+';'),M=n=>String(n).replace(/\\B(?=(\\d{3})+$)/g,' ')+' ₴';
let R=null;function S(){if(R)return R;try{return JSON.parse(W&&W.chatData||'{}')||{}}catch(e){return{}}}
function ask(q,f){if(!W)return;W.request(q).then(a=>{try{R=Object.assign({},S(),JSON.parse(a.result||'{}'))}catch(e){}f&&f();draw()},()=>{f&&f();draw()})}
function left(t){let s=Math.max(0,Math.floor((t-Date.now())/1e3)),d=Math.floor(s/86400),h=Math.floor(s%%86400/3600),m=Math.floor(s%%3600/60);return s<=0?'':d?d+' д '+h+' год':h?h+' год '+m+' хв':m+' хв '+s%%60+' с'}
"""
_BASE_END = "if(W){W.onEvent('chatDataChanged',()=>{R=null;draw()});W.ready&&W.ready()}draw();"


def _minify(text: str, sep: str = "") -> str:
    return sep.join(line.strip() for line in text.strip().splitlines())


def _page(body: str, js: str, const: dict | None = None, css: str = "") -> str:
    """Готовий HTML віджета: спільні стилі й JS + власні. Кидає ValueError, якщо не влазить у ліміт."""
    base_js = _BASE_JS % {"bot": config.BOT_USERNAME, "denis": DENIS_LINK}
    const_js = f"const C={json.dumps(const or {}, ensure_ascii=False, separators=(',', ':'))};\n"
    out = (
        f"<style>{_minify(_BASE_CSS + css)}</style>{body}"
        f"<script>{const_js}{_minify(base_js, chr(10))}\n{_minify(js, chr(10))}\n{_BASE_END}</script>"
    )
    check_widget_size(out)
    return out


def dumps(data) -> str:
    """Компактний JSON для chat_data / відповіді на запит; перевіряє ліміт 1024 байти."""
    out = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(out.encode("utf-8")) > WIDGET_DATA_MAX:
        raise ValueError("Дані віджета перевищують 1024 байти")
    return out


def money(value) -> str:
    return f"{int(value or 0):,}".replace(",", " ") + " ₴"


def clip(text, limit: int = NAME_MAX) -> str:
    text = " ".join(str(text or "—").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _ts_ms(value) -> int:
    """ISO-рядок або datetime → мілісекунди Unix; 0, якщо дати немає."""
    if not value:
        return 0
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# ─── Запуск ──────────────────────────────────────────────────────────────────

def enabled_for(user_id, kind: str) -> bool:
    """Режим віджета `kind` з налаштувань: off — нікому, owner — лише власнику, all — усім."""
    from services.settings import widget_mode

    mode = widget_mode(kind)
    if mode == "all":
        return True
    uid = int(user_id or 0)
    return mode == "owner" and uid > 0 and uid == int(config.OWNER_ID or 0)


# ─── 4. Кошторис ─────────────────────────────────────────────────────────────

def build_quote_widget(order: dict, items: list[dict]) -> dict:
    """Розбивка ціни замовлення з кнопками «Погоджуюсь» / «Змінити». Стан: {"a": 1} — клієнт погодив."""
    oid = int(order["id"])
    esc = _html.escape
    goods = [i for i in items if not (i.get("is_gift") or str(i.get("product_name") or "").startswith("🎁"))]
    rows = []
    for item in goods[:QUOTE_MAX_ROWS]:
        qty = int(item.get("quantity") or 1)
        q = f' <span class="q">× {qty}</span>' if qty > 1 else ""
        rows.append(f'<div class="row"><span class="k">{esc(clip(item.get("product_name"), 40))}{q}</span>'
                    f'<span class="v">{money(int(item.get("price") or 0) * qty)}</span></div>')
    if len(goods) > QUOTE_MAX_ROWS:
        rows.append(f'<div class="row q">і ще {len(goods) - QUOTE_MAX_ROWS} — у застосунку</div>')
    gift = str(order.get("gift_product_name") or "").strip()
    if gift:
        rows.append(f'<div class="row"><span class="k">🎁 {esc(clip(gift, 40))}</span><span class="v">0 ₴</span></div>')
    discount = int(order.get("discount_amount") or 0)
    if discount > 0:
        rows.append(f'<div class="row"><span class="k">Знижка</span><span class="v">−{money(discount)}</span></div>')
    total = int(order.get("total_price") or 0)
    body = (
        '<div id="w" class="amber">'
        f'<span class="eye">Кошторис · №{oid}</span><h3 id="h"></h3><p id="p"></p>'
        f'<div class="rows">{"".join(rows)}'
        f'<div class="row sum"><span class="k">До сплати</span><span class="v">{money(total)}</span></div></div>'
        '<div class="two" id="act"><button id="ok">Погоджуюсь</button><button class="g" id="no">Змінити</button></div>'
        "</div>"
    )
    js = """
function draw(){const d=S();$('w').className=d.a?'green':'amber';
$('h').textContent=d.a?'Ціну погоджено':'Узгодимо ціну?';
$('p').textContent=d.a?'Дякую! Денис напише щодо оплати':d.x?'Не вдалося — спробуй ще раз':'Перевір розбивку й підтвердь';H('act',d.a)}
$('ok').onclick=()=>{$('p').textContent='Зберігаю…';ask('q:a:'+C.id,()=>{if(!S().a)R={x:1}})};
$('no').onclick=()=>TG(D);
"""
    return {
        "html": _page(body, js, {"id": oid}),
        "data": dumps({"a": 0}),
        "caption": f"Кошторис замовлення №{oid} · {money(total)}",
    }


# ─── 5. Розіграш ─────────────────────────────────────────────────────────────

def giveaway_state(entries: int, tickets: int, number: int, status: str = "active") -> dict:
    return {"n": int(entries), "t": int(tickets), "no": int(number), "st": status}


def build_giveaway_widget(g: dict, state: dict, link: str = "") -> dict:
    """Участь у розіграші: номер, шанси, лічильник учасників і таймер до підсумків."""
    title = clip(g.get("title") or "Розіграш", 60)
    body = (
        '<div id="w" class="pink"><span class="eye">Розіграш</span>'
        f'<h3>{_html.escape(title)}</h3><p id="p"></p>'
        '<div class="rows"><div class="row"><span class="k">Твій номер</span><span class="v b" id="no"></span></div>'
        '<div class="row" id="tr"><span class="k">Твої шанси</span><span class="v b" id="t"></span></div>'
        '<div class="row"><span class="k">Учасників</span><span class="v" id="n"></span></div></div>'
        '<div class="two" id="ref"><button id="inv">Запросити друзів</button><button class="g" id="cp">Копіювати</button></div>'
        "</div>"
    )
    js = """
function draw(){const d=S(),l=left(C.e),on=d.st=='active'&&l;
$('p').textContent=on?'До підсумків: '+l:d.st=='cancelled'?'Розіграш скасовано':'Розіграш завершено — підсумки в каналі';
$('no').textContent='№'+(d.no||'—');$('t').textContent=d.t||1;$('n').textContent=d.n||1;H('tr',!C.r);H('ref',!C.r||!on)}
$('inv').onclick=()=>TG('https://t.me/share/url?url='+encodeURIComponent(C.l)+'&text='+encodeURIComponent('Беру участь у розіграші — приєднуйся 🎁'));
$('cp').onclick=()=>W&&W.copyText(C.l);
setInterval(draw,1e3);ask('g:'+C.id);W&&W.onEvent('activated',()=>ask('g:'+C.id));
"""
    const = {"id": int(g["id"]), "e": _ts_ms(g.get("ends_at")), "r": 1 if g.get("referral_bonus") and link else 0, "l": link}
    return {
        "html": _page(body, js, const),
        "data": dumps(state),
        "caption": f"🎁 Ти в розіграші «{title}» · №{int(state.get('no') or 0)}",
    }


# ─── 7. Акції ────────────────────────────────────────────────────────────────

_PROMO_ACCENTS = ("orange", "violet", "green", "blue", "pink", "teal", "red", "amber")


def promo_line(promo: dict) -> str:
    """Один рядок суті акції — те саме, що в /sales текстом."""
    ptype = promo.get("type")
    value, min_order = int(promo.get("value") or 0), int(promo.get("min_order") or 0)
    if ptype == "order_percent":
        return f"−{value}% на замовлення" + (f" від {money(min_order)}" if min_order else "")
    if ptype == "order_fixed":
        return f"−{money(value)} на замовлення" + (f" від {money(min_order)}" if min_order else "")
    if ptype == "gift_threshold":
        return f"{int(promo.get('min_items') or 0)}+ товари в кошику — подарунок до {money(promo.get('max_gift_price'))}"
    if ptype == "free_shipping":
        return "Безкоштовна доставка" + (f" від {money(min_order)}" if min_order else "")
    return str(promo.get("subtitle") or "").strip()


def build_promo_widget(promotions: list[dict]) -> dict:
    """До трьох активних акцій: назва, суть і таймер до кінця. Перемикання — локально, без запиту."""
    cards = []
    for idx, promo in enumerate(promotions[:3]):
        accent = promo.get("accent") if promo.get("accent") in _PROMO_ACCENTS else _PROMO_ACCENTS[idx % 3]
        cards.append([
            accent,
            clip(f"{promo.get('emoji') or '🔥'} {promo.get('title') or 'Акція'}", 44),
            clip(promo_line(promo), 70),
            _ts_ms(promo.get("ends_at")) if promo.get("show_timer", True) else 0,
            clip(promo.get("badge") or "Акція", 16),
        ])
    body = (
        '<div id="w"><span class="eye" id="e"></span><h3 id="h"></h3><p id="p"></p>'
        '<p id="tm" class="b" style="margin-top:8px"></p>'
        '<div class="two"><button class="g" id="nx" hidden>Наступна ›</button><button id="go">До каталогу</button></div></div>'
    )
    js = """
let n=0;function draw(){const c=C.c[n],l=c[3]&&left(c[3]);$('w').className=c[0];$('e').textContent=c[4]+(C.c.length>1?' · '+(n+1)+'/'+C.c.length:'');
$('h').textContent=c[1];$('p').textContent=c[2];$('tm').textContent=l?'⏳ Ще '+l:'';H('tm',!l);H('nx',C.c.length<2)}
$('nx').onclick=()=>{n=(n+1)%C.c.length;draw()};$('go').onclick=()=>TG(B+'sales');setInterval(draw,1e3);
"""
    return {
        "html": _page(body, js, {"c": cards}),
        "data": "",
        "caption": "",  # назва акції вже є у віджеті — підпис би дублював її
    }


# ─── Товари для вітрин і кошика ──────────────────────────────────────────────

def product_price(product: dict) -> int:
    variants = product.get("variants") or []
    prices = [int(v.get("price") or 0) for v in variants if int(v.get("price") or 0) > 0]
    return min(prices) if prices else int(product.get("price") or 0)


def can_quick_add(product: dict | None) -> bool:
    """Чи можна покласти товар у кошик одним дотиком — дзеркало `opensCard` в index.html."""
    if not product or product.get("contractPrice") or product.get("variants") or product.get("custom_fields"):
        return False
    return product.get("status", "active") == "active" and int(product.get("price") or 0) > 0


def showcase_rows(products: list[dict]) -> list[list]:
    """[id, назва, ціна, стара ціна, чи можна в кошик] — рядки вітрини в даних віджета."""
    rows = []
    for p in products[:SHOWCASE_MAX]:
        price = product_price(p)
        old = int(p.get("oldPrice") or 0)
        rows.append([int(p["id"]), clip(p.get("name")), price, old if old > price else 0, 1 if can_quick_add(p) else 0])
    return rows


def build_showcase_widget(title: str, rows: list[list], *, kind: str = "hits", tabs: list[list] | None = None,
                          subtitle: str = "", accent: str = "violet") -> dict:
    """Список товарів: дотик по назві відкриває товар у застосунку, «+» кладе в кошик чату.

    kind — ключ для оновлення (`h:<kind>`); tabs — [[ключ, назва], …] перемикачі добірок.
    Для підбору подарунка (kind="gift") список не оновлюється.
    """
    tabs = [[str(k), clip(n, 14)] for k, n in (tabs or [])][:4]
    body = (
        f'<div id="w" class="{accent}"><span class="eye" id="e"></span>'
        f'<h3 id="h"></h3><p id="p"></p><div class="tabs" id="tabs"></div><div class="rows" id="l"></div>'
        '<button class="g wide" id="cart" hidden></button></div>'
    )
    css = ".tabs{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap}.tabs button{padding:5px 11px;font-size:12.5px}.row .k{font-weight:600}"
    js = """
let K=C.k,T=C.t,N=0;
function draw(){const d=S(),l=d.l||[];$('e').textContent=C.y;$('h').textContent=d.h||T;$('p').textContent=l.length?C.s:'Поки порожньо — зазирни в каталог';
$('tabs').innerHTML=C.tabs.map(t=>'<button data-k="'+t[0]+'"'+(t[0]==K?'':' class=g')+'>'+E(t[1])+'</button>').join('');H('tabs',!C.tabs.length);
$('l').innerHTML=l.map(r=>'<div class="row"><span class="k" data-p="'+r[0]+'">'+E(r[1])+'</span><span class="v">'+(r[3]?'<s>'+M(r[3])+'</s>':'')+M(r[2])+'</span>'+(r[4]?'<button class="mini g" data-a="'+r[0]+'">+</button>':'<button class="mini g" data-p="'+r[0]+'">›</button>')+'</div>').join('');
N=d.n||N;H('cart',!N);$('cart').textContent='Кошик · '+N+' ›'}
$('w').onclick=e=>{const t=e.target,a=t.dataset;if(a.p)TG(B+'product_'+a.p);else if(a.a){t.textContent='✓';ask('c:a:'+a.a)}else if(a.k){K=a.k;ask('h:'+K)}};
$('cart').onclick=()=>ask('c:s');
if(C.k!='gift'){ask('h:'+K);W&&W.onEvent('activated',()=>ask('h:'+K))}
"""
    const = {"k": kind, "t": clip(title, 40), "s": clip(subtitle, 80), "tabs": tabs,
             "y": "Підбір подарунка" if kind == "gift" else "Вітрина"}
    return {
        "html": _page(body, js, const, css),
        "data": dumps({"l": rows}),
        "caption": f"{title}: " + ", ".join(r[1] for r in rows[:3]) if rows else title,
    }


# ─── 11. Кошик у чаті ────────────────────────────────────────────────────────

def init_chat_widget_tables(conn) -> None:
    big, ts = ("BIGINT", "TIMESTAMPTZ") if is_postgres() else ("INTEGER", "TIMESTAMP")
    now = "NOW()" if is_postgres() else "CURRENT_TIMESTAMP"
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS chat_carts (
            user_id {big} PRIMARY KEY,
            items TEXT NOT NULL DEFAULT '{{}}',
            message_id {big},
            updated_at {ts} DEFAULT {now}
        )
    """)


def get_cart(user_id: int) -> dict:
    """{"items": {product_id: qty}, "message_id": int | None}"""
    conn = db_connect(dict_rows=True)
    try:
        row = conn.execute(_sql("SELECT items, message_id FROM chat_carts WHERE user_id = ?"), (int(user_id),)).fetchone()
    finally:
        conn.close()
    if not row:
        return {"items": {}, "message_id": None}
    row = dict(row)
    try:
        raw = json.loads(row.get("items") or "{}")
        items = {int(k): int(v) for k, v in raw.items() if int(v) > 0}
    except (ValueError, TypeError, AttributeError):
        items = {}
    return {"items": items, "message_id": row.get("message_id")}


def save_cart(user_id: int, items: dict[int, int], message_id=...) -> None:
    payload = json.dumps({str(k): int(v) for k, v in items.items() if int(v) > 0})
    now = "NOW()" if is_postgres() else "CURRENT_TIMESTAMP"
    conn = db_connect()
    try:
        exists = conn.execute(_sql("SELECT 1 FROM chat_carts WHERE user_id = ?"), (int(user_id),)).fetchone()
        if not exists:
            conn.execute(_sql("INSERT INTO chat_carts (user_id, items) VALUES (?, ?)"), (int(user_id), payload))
        else:
            conn.execute(_sql(f"UPDATE chat_carts SET items = ?, updated_at = {now} WHERE user_id = ?"),
                         (payload, int(user_id)))
        if message_id is not ...:
            conn.execute(_sql("UPDATE chat_carts SET message_id = ? WHERE user_id = ?"), (message_id, int(user_id)))
        conn.commit()
    finally:
        conn.close()


def change_cart(items: dict[int, int], op: str, product_id: int, product: dict | None) -> tuple[dict[int, int], str]:
    """Застосувати дію до кошика: a/+ — додати, - — прибрати одну, x — видалити. Повертає (кошик, помилка)."""
    items = dict(items)
    pid = int(product_id)
    if op in ("a", "+"):
        if not can_quick_add(product):
            return items, "Цей товар обирається в застосунку"
        if pid not in items and len(items) >= CART_MAX_LINES:
            return items, f"У кошику чату до {CART_MAX_LINES} позицій — решту додай у застосунку"
        items[pid] = min(CART_MAX_QTY, items.get(pid, 0) + 1)
    elif op == "-":
        if items.get(pid, 0) <= 1:
            items.pop(pid, None)
        else:
            items[pid] -= 1
    elif op == "x":
        items.pop(pid, None)
    return items, ""


def cart_state(items: dict[int, int], lookup, discount_fn=None) -> dict:
    """Стан кошика для віджета. Ціни й знижка рахуються тут, на сервері, з каталогу.

    lookup(product_id) → товар або None; discount_fn(subtotal, cat_totals) → знижка акції.
    Товари, яких уже немає в продажу, з кошика випадають.
    """
    lines, subtotal, cat_totals = [], 0, {}
    for pid, qty in items.items():
        product = lookup(pid)
        if not can_quick_add(product):
            continue
        price = int(product.get("price") or 0)
        lines.append([int(pid), clip(product.get("name"), 24), price, int(qty)])
        subtotal += price * qty
        cat = str(product.get("cat") or "")
        cat_totals[cat] = cat_totals.get(cat, 0) + price * qty
    discount = int(discount_fn(subtotal, cat_totals) or 0) if discount_fn and subtotal else 0
    return {"i": lines, "s": subtotal, "d": discount, "t": max(0, subtotal - discount), "m": ""}


def cart_count(state: dict) -> int:
    return sum(int(line[3]) for line in state.get("i") or [])


def cart_start_param(state: dict) -> str:
    """`cart_12x2-15x1` — Mini App додає ці товари в кошик (startapp, до 64 символів)."""
    return "cart_" + "-".join(f"{line[0]}x{line[3]}" for line in state.get("i") or [])


def cart_caption(state: dict) -> str:
    n = cart_count(state)
    return f"🛒 Кошик: {n} шт. · {money(state.get('t'))}" if n else "🛒 Кошик порожній"


def build_cart_widget() -> str:
    """HTML кошика однаковий для всіх: увесь вміст приходить у даних (`cart_state`)."""
    body = (
        '<div id="w" class="blue"><span class="eye">Кошик</span><h3 id="h"></h3><p id="p"></p>'
        '<div class="rows" id="l"></div>'
        '<div class="rows" id="tot"><div class="row" id="dr"><span class="k">Знижка за акцією</span><span class="v" id="d"></span></div>'
        '<div class="row sum"><span class="k">Разом</span><span class="v" id="t"></span></div></div>'
        '<button class="wide" id="go"></button></div>'
    )
    js = """
function draw(){const d=S(),l=d.i||[],n=l.reduce((a,r)=>a+r[3],0);
$('h').textContent=n?n+' шт. · '+M(d.t):'Кошик порожній';$('p').textContent=d.m||(n?'Купон і доставку вкажеш при оформленні':'Додай товари з вітрини або каталогу');
$('l').innerHTML=l.map(r=>'<div class="row"><span class="k">'+E(r[1])+'</span><button class="mini g" data-q="c:-:'+r[0]+'">−</button><span class="v">'+r[3]+'</span><button class="mini g" data-q="c:+:'+r[0]+'">+</button><span class="v">'+M(r[2]*r[3])+'</span></div>').join('');
H('tot',!n);H('dr',!d.d);$('d').textContent='−'+M(d.d||0);$('t').textContent=M(d.t||0);$('go').textContent=n?'Оформити замовлення':'До каталогу'}
$('l').onclick=e=>{const q=e.target.dataset.q;q&&ask(q)};
$('go').onclick=()=>{const l=S().i||[];TG(B+(l.length?'cart_'+l.map(r=>r[0]+'x'+r[3]).join('-'):'shop'))};
"""
    return _page(body, js)


# ─── 12. Підбір подарунка ────────────────────────────────────────────────────

GIFT_WHO = {
    "kid": ("Дитині", ("іграш", "флексі", "fidget", "дракон", "динозавр", "тварин", "антистрес", "машин", "робот")),
    "her": ("Їй", ("ваз", "прикрас", "органайзер", "світ", "квіт", "шкатул", "декор", "свіч", "серц")),
    "him": ("Йому", ("тримач", "підставк", "органайзер", "авто", "інструмент", "кабел", "геймер", "навушник")),
    "work": ("Колезі", ("органайзер", "підставк", "ручк", "стіл", "тримач", "брелок", "кабел", "візит")),
    "any": ("Будь-кому", ()),
}
GIFT_BUDGET = {
    "b1": ("до 200 ₴", 0, 200),
    "b2": ("200–500 ₴", 200, 500),
    "b3": ("500–1000 ₴", 500, 1000),
    "b4": ("від 1000 ₴", 1000, 10 ** 9),
}
GIFT_OCCASION = {
    "bday": ("День народження", ()),
    "ny": ("Новий рік", ("новоріч", "різдв", "ялин", "сніж", "зим", "олен", "санта")),
    "home": ("Для дому", ("декор", "ваз", "світ", "горщик", "кухн", "дім", "полиц", "гачк")),
    "none": ("Без приводу", ()),
}
_WORD_RE = re.compile(r"\s+")


def pick_gifts(who: str, budget: str, occasion: str, products: list[dict], *, categories: dict[str, str] | None = None,
               hit_ids: list[int] | None = None, limit: int = SHOWCASE_MAX) -> list[dict]:
    """Підбір за правилами: бюджет відсікає, збіги за ключовими словами й продажі впорядковують.

    Якщо в бюджеті замало товарів, межі розширюються; договірні й неактивні не пропонуються.
    """
    who_words = GIFT_WHO.get(who, GIFT_WHO["any"])[1]
    occ_words = GIFT_OCCASION.get(occasion, GIFT_OCCASION["none"])[1]
    _, lo, hi = GIFT_BUDGET.get(budget, GIFT_BUDGET["b2"])
    rank = {pid: i for i, pid in enumerate(hit_ids or [])}
    categories = categories or {}

    scored = []
    for p in products:
        price = product_price(p)
        if p.get("contractPrice") or price <= 0 or p.get("status", "active") != "active" or p.get("gift"):
            continue
        text = _WORD_RE.sub(" ", " ".join(str(x or "") for x in (
            p.get("name"), p.get("description"), categories.get(str(p.get("cat") or ""), ""))).lower())
        score = 3 * sum(w in text for w in who_words) + 2 * sum(w in text for w in occ_words)
        pid = int(p["id"])
        if pid in rank:
            score += max(0.0, 2 - rank[pid] * 0.2)
        if p.get("hot"):
            score += 0.5
        scored.append((score, price, pid, p))

    for widen in (1.0, 1.5, 3.0, 1e9):
        fit = [row for row in scored if lo / widen <= row[1] <= hi * widen]
        if len(fit) >= min(3, limit) or widen >= 1e9:
            break
    fit.sort(key=lambda row: (-row[0], abs(row[1] - (lo + min(hi, 2000)) / 2), row[2]))
    return [row[3] for row in fit[:limit]]


def gift_summary(who: str, budget: str, occasion: str) -> str:
    parts = [GIFT_WHO.get(who, GIFT_WHO["any"])[0], GIFT_BUDGET.get(budget, GIFT_BUDGET["b2"])[0]]
    if occasion in GIFT_OCCASION and occasion != "none":
        parts.append(GIFT_OCCASION[occasion][0])
    return " · ".join(parts)


# ─── 15. Адмінський дашборд ──────────────────────────────────────────────────

def dash_state(today: dict, week: dict, *, queue: int = 0, pending_price: int = 0,
               searches: list[str] | None = None, stamp: str = "") -> dict:
    """today / week — {"orders", "revenue", "started"} з services.overview._period_stats."""
    def cut(period: dict) -> list[int]:
        return [int(period.get("orders") or 0), int(period.get("revenue") or 0), int(period.get("started") or 0)]

    return {"t": cut(today), "w": cut(week), "q": int(queue), "pp": int(pending_price),
            "s": [clip(q, 18) for q in (searches or [])[:4]], "u": stamp}


def build_dash_widget(admin_url: str = "") -> str:
    body = (
        '<div id="w" class="teal"><span class="eye">Магазин сьогодні</span><h3 id="h"></h3><p id="p"></p>'
        '<div class="rows"><div class="row q"><span class="k"></span><span class="v c">Сьогодні</span><span class="v c">7 днів</span></div>'
        '<div class="row"><span class="k">Замовлення</span><span class="v c b" id="o0"></span><span class="v c" id="o1"></span></div>'
        '<div class="row"><span class="k">Виручка</span><span class="v c b" id="r0"></span><span class="v c" id="r1"></span></div>'
        '<div class="row"><span class="k">Нові клієнти</span><span class="v c b" id="u0"></span><span class="v c" id="u1"></span></div></div>'
        '<p id="s" style="margin-top:8px"></p>'
        '<button class="g wide" id="go">Відкрити адмінку</button></div>'
    )
    css = ".c{width:76px;text-align:right}"
    js = """
function draw(){const d=S(),t=d.t||[0,0,0],w=d.w||[0,0,0];
$('h').textContent=d.q?d.q+' нових у черзі':'Черга порожня';$('p').textContent=(d.pp?'Без ціни: '+d.pp+' · ':'')+'Оновлено '+(d.u||'—');
$('o0').textContent=t[0];$('o1').textContent=w[0];$('r0').textContent=M(t[1]);$('r1').textContent=M(w[1]);$('u0').textContent=t[2];$('u1').textContent=w[2];
$('s').textContent=(d.s||[]).length?'Шукають: '+d.s.join(', '):''}
H('go',!C.a);$('go').onclick=()=>W&&W.openLink(C.a);
ask('d');W&&W.onEvent('activated',()=>ask('d'));setInterval(()=>ask('d'),6e4);
"""
    return _page(body, js, {"a": admin_url if str(admin_url).lower().startswith("https://") else ""}, css)
