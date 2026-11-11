"""Віджет «Живий статус замовлення» (Bot API 10.4): HTML, дані, підпис і облік у БД.

Код віджета (HTML) після відправки не змінюється — змінюються лише дані (`chat_data`, до 1024 байт).
Тому незмінне (склад, сума, оплата, доставка) вшите в HTML, а змінне (статус, оплата, ТТН,
стан посилки) йде в дані. Якщо змінилось вшите — старий віджет видаляємо й шлемо новий
(це вирішує `html_hash`). Обмеження й рішення — у ROADMAP.md, пункт 1.
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import zlib
from datetime import date, datetime, timedelta

import config
from db_core import db_connect, is_postgres, sql as _sql

WIDGET_HTML_MAX = 32768
WIDGET_ZLIB_MAX = 4096
WIDGET_DATA_MAX = 1024
MAX_LIST_ITEMS = 12
NAME_MAX = 44
ACTIVE_STATUSES = ("new", "draft", "confirmed", "printing", "ready", "shipped")
NP_TRACKING_URL = "https://novaposhta.ua/tracking/?cargo_number="
DENIS_LINK = "https://t.me/denisposelyanov"

MONTHS_GEN = ["січня", "лютого", "березня", "квітня", "травня", "червня",
              "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]

# Стани посилки НП у даних віджета: w — в дорозі, b — у відділенні, r — повертається, d — отримана
STATUS_TITLES = {
    "new": "Очікує підтвердження",
    "draft": "Є питання",
    "confirmed": "Підтверджено",
    "printing": "Друкується",
    "ready": "Готове",
    "shipped": "В дорозі",
    "delivered": "Отримано",
    "cancelled": "Скасовано",
}
NOTE_TEXT = {
    "draft": "❓ Є питання по замовленню №{id} — Денис скоро напише",
    "confirmed": "✅ Замовлення №{id} підтверджено",
    "printing": "🖨️ Замовлення №{id} друкується",
    "ready": "📦 Замовлення №{id} готове",
    "ready_self": "📦 Замовлення №{id} можна забирати",
    "shipped": "🚚 Замовлення №{id} відправлено",
    "delivered": "🎉 Замовлення №{id} отримано",
    "cancelled": "❌ Замовлення №{id} скасовано",
    "np_branch": "📦 Посилка №{id} прибула у відділення",
    "np_remind": "⏰ Посилка №{id} чекає у відділенні{until}",
    "np_return": "↩️ Посилка №{id} повертається відправнику",
}


# ─── БД ──────────────────────────────────────────────────────────────────────

def init_order_widget_tables(conn) -> None:
    pg = is_postgres()
    big, ts = ("BIGINT", "TIMESTAMPTZ") if pg else ("INTEGER", "TIMESTAMP")
    now = "NOW()" if pg else "CURRENT_TIMESTAMP"
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS order_widgets (
            order_id {big} PRIMARY KEY,
            user_id {big} NOT NULL,
            message_id {big} NOT NULL,
            html_hash TEXT NOT NULL DEFAULT '',
            data TEXT NOT NULL DEFAULT '',
            caption TEXT NOT NULL DEFAULT '',
            note_message_id {big},
            updated_at {ts} DEFAULT {now}
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_order_widgets_msg ON order_widgets(user_id, message_id)")
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS order_tracking (
            order_id {big} PRIMARY KEY,
            ttn TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT '',
            np_code INTEGER NOT NULL DEFAULT 0,
            np_text TEXT NOT NULL DEFAULT '',
            arrived_on TEXT NOT NULL DEFAULT '',
            free_until TEXT NOT NULL DEFAULT '',
            reminded INTEGER NOT NULL DEFAULT 0,
            checked_at {ts}
        )
    """)


def _one(query: str, params: tuple) -> dict | None:
    conn = db_connect(dict_rows=True)
    try:
        row = conn.execute(_sql(query), params).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _exec(query: str, params: tuple) -> None:
    conn = db_connect()
    try:
        conn.execute(_sql(query), params)
        conn.commit()
    finally:
        conn.close()


def get_widget(order_id: int) -> dict | None:
    return _one("SELECT * FROM order_widgets WHERE order_id = ?", (int(order_id),))


def find_widget_by_message(user_id: int, message_id: int) -> dict | None:
    return _one(
        "SELECT * FROM order_widgets WHERE user_id = ? AND message_id = ?", (int(user_id), int(message_id))
    )


def save_widget(order_id: int, user_id: int, message_id: int, html_hash: str, data: str, caption: str) -> None:
    """Новий віджет замовлення (старий запис, якщо був, замінюється; рядок-сповіщення зберігається)."""
    old = get_widget(order_id)
    conn = db_connect()
    try:
        conn.execute(_sql("DELETE FROM order_widgets WHERE order_id = ?"), (int(order_id),))
        conn.execute(
            _sql("INSERT INTO order_widgets (order_id, user_id, message_id, html_hash, data, caption, note_message_id) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)"),
            (int(order_id), int(user_id), int(message_id), html_hash, data, caption,
             (old or {}).get("note_message_id")),
        )
        conn.commit()
    finally:
        conn.close()


def update_widget_state(order_id: int, data: str, caption: str) -> None:
    _exec("UPDATE order_widgets SET data = ?, caption = ? WHERE order_id = ?", (data, caption, int(order_id)))


def set_widget_note(order_id: int, note_message_id: int | None) -> None:
    _exec("UPDATE order_widgets SET note_message_id = ? WHERE order_id = ?", (note_message_id, int(order_id)))


def delete_widget(order_id: int) -> None:
    _exec("DELETE FROM order_widgets WHERE order_id = ?", (int(order_id),))


def get_tracking(order_id: int) -> dict | None:
    return _one("SELECT * FROM order_tracking WHERE order_id = ?", (int(order_id),))


def save_tracking(order_id: int, **fields) -> dict:
    """Створює або оновлює рядок трекінгу; `checked_at` ставиться завжди."""
    allowed = {"ttn", "state", "np_code", "np_text", "arrived_on", "free_until", "reminded"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    now = "NOW()" if is_postgres() else "CURRENT_TIMESTAMP"
    conn = db_connect()
    try:
        exists = conn.execute(_sql("SELECT 1 FROM order_tracking WHERE order_id = ?"), (int(order_id),)).fetchone()
        if not exists:
            conn.execute(_sql("INSERT INTO order_tracking (order_id) VALUES (?)"), (int(order_id),))
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            _sql(f"UPDATE order_tracking SET {sets + ', ' if sets else ''}checked_at = {now} WHERE order_id = ?"),
            (*fields.values(), int(order_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return get_tracking(order_id) or {}


def list_trackable_orders(limit: int = 300) -> list[dict]:
    """Відправлені Новою Поштою замовлення з ТТН — їх перевіряє фоновий трекінг."""
    conn = db_connect(dict_rows=True)
    try:
        rows = conn.execute(
            _sql("""
                SELECT o.id, o.user_id, o.ttn, o.phone, o.status, o.is_test, o.ordered_at,
                       t.state, t.np_code, t.arrived_on, t.free_until, t.reminded, t.ttn AS tracked_ttn
                FROM orders o
                LEFT JOIN order_tracking t ON t.order_id = o.id
                WHERE o.status = 'shipped' AND COALESCE(o.ttn, '') <> ''
                ORDER BY o.id DESC
                LIMIT ?
            """),
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def list_active_order_ids(user_id: int, limit: int = 5) -> list[int]:
    """Незавершені замовлення покупця — для /status."""
    marks = ", ".join("?" for _ in ACTIVE_STATUSES)
    conn = db_connect(dict_rows=True)
    try:
        rows = conn.execute(
            _sql(f"SELECT id FROM orders WHERE user_id = ? AND status IN ({marks}) ORDER BY id DESC LIMIT ?"),
            (int(user_id), *ACTIVE_STATUSES, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [int(dict(r)["id"]) for r in rows][::-1]


# ─── Запуск ──────────────────────────────────────────────────────────────────

def widget_enabled_for(user_id) -> bool:
    """Режим із налаштувань: off — нікому, owner — лише власнику, all — усім."""
    from services.settings import order_widget_mode

    mode = order_widget_mode()
    if mode == "all":
        return True
    return mode == "owner" and int(user_id or 0) == int(config.OWNER_ID or 0) and int(user_id or 0) > 0


# ─── Тексти ──────────────────────────────────────────────────────────────────

def _money(value) -> str:
    return f"{int(value or 0):,}".replace(",", " ") + " ₴"


def _plural(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "товар"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "товари"
    return "товарів"


def format_day(iso: str) -> str:
    """«2026-11-19» → «19 листопада»; порожній рядок, якщо дата нечитабельна."""
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return ""
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


def _is_gift(item: dict) -> bool:
    return bool(item.get("is_gift")) or str(item.get("product_name") or "").strip().startswith("🎁")


def _clip(text: str, limit: int = NAME_MAX) -> str:
    text = " ".join(str(text or "—").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def total_label(order: dict, items: list[dict]) -> str:
    total = int(order.get("total_price") or 0)
    if order.get("price_pending") or any(i.get("is_contract_price") and not int(i.get("price") or 0) for i in items):
        return f"{_money(total)} + договірна" if total > 0 else "Договірна ціна"
    return _money(total)


def status_title(order: dict, tracking: dict | None = None) -> str:
    status = str(order.get("status") or "new")
    if status == "ready" and order.get("delivery_method") == "self":
        return "Можна забирати"
    if status == "shipped":
        state = (tracking or {}).get("state") or ""
        return {"b": "Чекає у відділенні", "r": "Посилка повертається"}.get(state, "В дорозі")
    return STATUS_TITLES.get(status, status)


def build_caption(order: dict, tracking: dict | None = None) -> str:
    """Підпис під віджетом порожній: статус уже є у самому віджеті, а сповіщення — окремим рядком (status_note)."""
    return ""


def status_note(order: dict, event: str | None = None, tracking: dict | None = None) -> str | None:
    """Короткий рядок-сповіщення про подію; None — про цю подію не сповіщаємо."""
    key = event or str(order.get("status") or "")
    if key == "ready" and order.get("delivery_method") == "self":
        key = "ready_self"
    template = NOTE_TEXT.get(key)
    if not template:
        return None
    until = format_day((tracking or {}).get("free_until") or "")
    return template.format(id=int(order["id"]), until=f", безкоштовно до {until}" if until else "")


def build_widget_data(order: dict, tracking: dict | None = None, *, review_bonus: bool = True) -> str:
    """Змінний стан віджета — компактний JSON (ліміт 1024 байти)."""
    data: dict = {"s": str(order.get("status") or "new")}
    if order.get("is_paid"):
        data["p"] = 1
    eta = str(order.get("eta_text") or "").strip()
    if eta and data["s"] in ("confirmed", "printing"):
        data["e"] = eta[:40]
    ttn = str(order.get("ttn") or "").strip()
    if ttn:
        data["n"] = ttn
    if tracking and data["s"] == "shipped":
        if tracking.get("state"):
            data["c"] = tracking["state"]
        until = format_day(tracking.get("free_until") or "")
        if until and tracking.get("state") == "b":
            data["f"] = until
    if review_bonus:
        data["rb"] = 1
    out = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(out.encode("utf-8")) > WIDGET_DATA_MAX:
        raise ValueError("Дані віджета перевищують 1024 байти")
    return out


# ─── HTML ────────────────────────────────────────────────────────────────────

# Акценти — ті самі, що в картках акцій (services/promo_cards.ACCENTS): світлий, основний, темний.
# color-mix() у віджетах заборонений, тому відтінки — готовими hex-значеннями з прозорістю.
_CSS = """
body{margin:0;padding:0}
#w{--i:light-dark(var(--d),var(--l));padding:16px 16px 14px;line-height:1.3;background:linear-gradient(135deg,var(--t),var(--z) 72%)}
.blue{--l:#4BB7E5;--h:#289CDB;--d:#1B7FBA;--t:#289CDB1F;--z:#289CDB00;--e:#289CDB26}
.amber{--l:#FFC93C;--h:#F2A900;--d:#B97C00;--t:#F2A9001F;--z:#F2A90000;--e:#F2A90026}
.violet{--l:#A58BFF;--h:#7C5CFC;--d:#5B3FD6;--t:#7C5CFC1F;--z:#7C5CFC00;--e:#7C5CFC26}
.orange{--l:#FFA957;--h:#F5822A;--d:#C96112;--t:#F5822A1F;--z:#F5822A00;--e:#F5822A26}
.green{--l:#4CC38A;--h:#22A06B;--d:#167C52;--t:#22A06B1F;--z:#22A06B00;--e:#22A06B26}
.red{--l:#FF7A7E;--h:#E5484D;--d:#BC2B30;--t:#E5484D1F;--z:#E5484D00;--e:#E5484D26}
#w *{box-sizing:border-box}
.hero{display:flex;align-items:center;gap:14px}
.txt{flex:1;min-width:0}
.eye{display:inline-block;padding:3px 9px;border-radius:99px;background:var(--e);color:var(--i);font-size:10px;font-weight:800;letter-spacing:.11em;text-transform:uppercase}
h3{margin:8px 0 3px;font-size:21px;line-height:1.12;font-weight:800;letter-spacing:-.02em}
p{margin:0;font-size:13px;color:var(--tg-theme-hint-color)}
.orb{flex:none;width:64px;height:64px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,var(--l),var(--h));box-shadow:0 0 26px var(--e)}
svg{fill:none;stroke-linecap:round;stroke-linejoin:round}
.orb svg{width:30px;height:30px;stroke:#fff;stroke-width:1.5}
.bar{display:flex;gap:4px;margin-top:16px}
.bar i{flex:1;height:5px;border-radius:9px;background:var(--e)}
.bar .on{background:var(--h)}
.ttn{margin-top:9px;font-size:13px;color:var(--tg-theme-hint-color)}
.ttn b{color:var(--tg-theme-text-color);font-variant-numeric:tabular-nums;letter-spacing:.02em;margin:0 5px 0 4px}
.ttn svg{width:14px;height:14px;vertical-align:-2px;stroke:var(--i);stroke-width:1.6}
.foot{display:flex;align-items:center;gap:10px;margin-top:12px;font-size:13.5px}
.foot b{flex:1}
.foot span,.back,.more{color:var(--i);font-weight:700}
small,.meta,.q{font-size:12.5px;color:var(--tg-theme-hint-color)}
button{display:block;width:100%;margin-top:13px;border:0;border-radius:99px;padding:10px 14px;font:inherit;font-size:14px;font-weight:700;background:var(--h);color:#fff}
.g{background:var(--e);color:var(--i)}
.back{font-size:13px}
.rows{margin-top:10px;font-size:13.5px}
.row{display:flex;gap:10px;padding:3.5px 0}
.k{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.v{white-space:nowrap;font-variant-numeric:tabular-nums}
.sum{font-weight:800;font-size:15px;margin-top:6px;padding-top:9px;border-top:1px solid var(--tg-theme-section-separator-color)}
s{color:var(--tg-theme-hint-color);font-weight:500;margin-right:5px}
.meta{margin-top:8px}
[hidden]{display:none!important}
"""

_ICONS = {
    "clock": '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
    "print": '<path d="M7 9V4h10v5M7 17H4.5V9.5h15V17H17M7 14h10v6H7z"/>',
    "box": '<path d="M4 8l8-4 8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8"/>',
    "truck": '<path d="M2.5 6.5h11V16h-11zM13.5 10h4l3 3.2V16h-7"/><circle cx="7" cy="17.5" r="1.8"/><circle cx="17" cy="17.5" r="1.8"/>',
    "pin": '<path d="M12 21s6.5-5.6 6.5-10.5a6.5 6.5 0 10-13 0C5.5 15.4 12 21 12 21z"/><circle cx="12" cy="10.5" r="2.3"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "x": '<path d="M7 7l10 10M17 7L7 17"/>',
}

# C — вшиті константи замовлення; стан приходить у chatData (див. build_widget_data).
_JS = """
const W=window.TelegramWidget,$=i=>document.getElementById(i),H=(i,h)=>h?$(i).setAttribute('hidden',''):$(i).removeAttribute('hidden'),D='%(denis)s',B='https://t.me/%(bot)s?startapp=';
const TG=u=>W&&W.openTelegramLink(u),den=()=>TG(D);let act=null;
function st(){try{return JSON.parse(W&&W.chatData||C.d)||{}}catch(e){return{}}}
function view(d){const s=d.s,card=C.pm=='card',track=()=>W&&W.openLink('%(np)s'+d.n);
let v={new:['amber','clock',0,'Очікує підтвердження','Денис скоро напише тобі'],
draft:['amber','clock',0,'Є питання','Денис уточнює деталі й напише тобі'],
confirmed:['blue','check',0,'Підтверджено',card&&!d.p?'Денис надішле реквізити для оплати':'Скоро почну друкувати'],
printing:['blue','print',1,'Друкується','Почав друкувати твоє замовлення'],
ready:C.self?['violet','box',2,'Можна забирати','Домовмося про зручний час','Написати Денису',den]:['violet','box',2,'Готове','Надруковано й запаковано — скоро відправлю'],
shipped:['orange','truck',3,'В дорозі','Прямує до твого відділення','Відстежити на Новій Пошті',track],
delivered:['green','check',C.of-1,'Отримано','Дякую, що обрав нас 💙',d.rb?'Оцінити й отримати купон':'Оцінити',()=>TG(B+'review_'+C.id)],
cancelled:['red','x',-1,'Скасовано','Є питання — напиши Денису','Написати Денису',den]}[s]||['blue','clock',0,s,''];
if(s=='shipped'&&d.c=='b')v=['orange','pin',3,'Чекає у відділенні',d.f?'Безкоштовно до '+d.f:'Можна забирати',v[5],v[6]];
if(s=='shipped'&&d.c=='r')v=['red','x',3,'Посилка повертається','Напиши Денису, якщо це помилка','Написати Денису',den];
if(d.e&&(s=='printing'||s=='confirmed'&&!(card&&!d.p)))v[4]='Орієнтовний термін: '+d.e;
return v}
function render(){const d=st(),v=view(d),card=C.pm=='card';
$('w').className=v[0];$('orb').innerHTML='<svg viewBox="0 0 24 24">'+I[v[1]]+'</svg>';
$('h').textContent=v[3];$('p').textContent=v[4];
let b='';for(let i=0;i<C.of;i++)b+='<i'+(i<=v[2]?' class=on':'')+'></i>';
$('bar').innerHTML=b;H('bar',v[2]<0);
H('ttn',!d.n||d.s!='shipped');$('n').textContent=(d.n||'').replace(/(.{4})(?=.)/g,'$1 ');
$('pay').textContent=d.s=='cancelled'?'':card?d.p?'оплачено':'new,draft'.includes(d.s)?'карткою':'очікує оплати':C.pm=='cod'?'при отриманні':'';
H('cta',!v[5]);$('cta').textContent=v[5]||'';act=v[6]||null;
if(d.s=='shipped'&&d.c!='d'&&W&&!render.q){render.q=1;W.request('r').catch(()=>{})}}
function tab(o){H('main',o);H('det',!o)}
const on=(i,f)=>$(i).addEventListener('click',f);
on('cta',()=>act&&act());on('ttn',()=>{const n=st().n;n&&W&&W.copyText(n)});
on('items',()=>tab(1));on('back',()=>tab(0));on('app',()=>TG(B+'order_'+C.id));
if(W){W.onEvent('chatDataChanged',render);W.ready&&W.ready()}render();
"""


def build_widget_html(order: dict, items: list[dict], *, initial_data: str = "", max_items: int = MAX_LIST_ITEMS) -> str:
    """HTML віджета одного замовлення. Незмінне вшите сюди; розмір перевіряє `check_widget_size`."""
    oid = int(order["id"])
    esc = _html.escape
    goods = [i for i in items if not _is_gift(i)]
    rows = []
    for item in goods[:max_items]:
        qty = int(item.get("quantity") or 1)
        price = int(item.get("price") or 0)
        value = "договірна" if item.get("is_contract_price") and not price else _money(price * qty)
        q = f' <span class="q">× {qty}</span>' if qty > 1 else ""
        rows.append(f'<div class="row"><span class="k">{esc(_clip(item.get("product_name")))}{q}</span>'
                    f'<span class="v">{value}</span></div>')
    rest = len(goods) - max_items
    if rest > 0:
        rows.append(f'<div class="row more">і ще {rest} — у застосунку</div>')
    gift = str(order.get("gift_product_name") or "").strip()
    if gift:
        rows.append(f'<div class="row"><span class="k">🎁 {esc(_clip(gift))}</span><span class="v">0 ₴</span></div>')

    total = total_label(order, goods)
    discount = int(order.get("discount_amount") or 0)
    old = f"<s>{_money(int(order.get('total_price') or 0) + discount)}</s>" if discount > 0 else ""
    pay_method = str(order.get("pay_method") or "")
    is_self = order.get("delivery_method") == "self"
    pay = {"card": "Оплата карткою", "cod": "Оплата при отриманні"}.get(pay_method, "")
    if is_self:
        ship = "Самовивіз"
    else:
        ship = ", ".join(x for x in (_clip(order.get("city") or "", 30), _clip(order.get("branch") or "", 40)) if x and x != "—")
        name = _clip(order.get("recipient_name") or "", 30)
        if name and name != "—":
            ship = f"{ship} · {name}" if ship else name
    meta = " · ".join(x for x in (pay, ship) if x)
    count = len(goods)

    const = json.dumps(
        {"id": oid, "of": 4 if is_self else 5, "self": 1 if is_self else 0, "pm": pay_method, "d": initial_data or "{}"},
        ensure_ascii=False, separators=(",", ":"),
    )
    icons = json.dumps(_ICONS, separators=(",", ":"))
    js = _JS % {"denis": DENIS_LINK, "bot": config.BOT_USERNAME, "np": NP_TRACKING_URL}
    css = "".join(line.strip() for line in _CSS.strip().splitlines())
    script = "".join(line.strip() + "\n" for line in js.strip().splitlines())
    return (
        f"<style>{css}</style>"
        '<div id="w"><div id="main">'
        f'<div class="hero"><div class="txt"><span class="eye">Замовлення №{oid}</span>'
        '<h3 id="h"></h3><p id="p"></p></div><div class="orb" id="orb"></div></div>'
        '<div class="bar" id="bar"></div>'
        '<div class="ttn" id="ttn" hidden>ТТН<b id="n"></b>'
        '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M5 15V6a1 1 0 011-1h9"/></svg></div>'
        f'<div class="foot"><b id="items">{count} {_plural(count)} · {esc(total)} <span>›</span></b>'
        '<small id="pay"></small></div>'
        '<button id="cta" hidden></button>'
        '</div><div id="det" hidden>'
        f'<div class="back" id="back">‹ Замовлення №{oid}</div>'
        f'<div class="rows">{"".join(rows)}'
        f'<div class="row sum"><span class="k">Разом</span><span class="v">{old}{esc(total)}</span></div></div>'
        + (f'<div class="meta">{esc(meta)}</div>' if meta else "")
        + '<button class="g" id="app">Відкрити в застосунку</button>'
        "</div></div>"
        f"<script>const C={const},I={icons};\n{script}</script>"
    )


def widget_zlib_size(html_text: str) -> int:
    """Розмір після zlib-9 — саме так рахує сервер Telegram."""
    return len(zlib.compress(html_text.encode("utf-8"), 9))


def check_widget_size(html_text: str) -> None:
    raw = len(html_text.encode("utf-8"))
    if raw > WIDGET_HTML_MAX or widget_zlib_size(html_text) > WIDGET_ZLIB_MAX:
        raise ValueError(f"Віджет завеликий: {raw} байт, {widget_zlib_size(html_text)} після стиснення")


def widget_html_hash(html_text: str) -> str:
    return hashlib.sha1(html_text.encode("utf-8")).hexdigest()[:16]


def build_widget(order: dict, items: list[dict], tracking: dict | None = None, *, review_bonus: bool = True) -> dict:
    """Усе для відправки: html (без стану — щоб хеш не залежав від статусу), дані, підпис, хеш."""
    # Довгі унікальні назви стискаються гірше: якщо не влазимо в 4 КБ — показуємо менше позицій.
    for max_items in range(MAX_LIST_ITEMS, -1, -2):
        html_text = build_widget_html(order, items, max_items=max_items)
        if widget_zlib_size(html_text) <= WIDGET_ZLIB_MAX:
            break
    check_widget_size(html_text)
    return {
        "html": html_text,
        "hash": widget_html_hash(html_text),
        "data": build_widget_data(order, tracking, review_bonus=review_bonus),
        "caption": build_caption(order, tracking),
    }


# ─── Нова Пошта ──────────────────────────────────────────────────────────────

NP_RECEIVED = {9, 10, 11, 106}
NP_AT_BRANCH = {7, 8}
NP_RETURNING = {102, 103, 105, 108}
NP_ON_WAY = {4, 5, 6, 41, 101, 104, 111, 112}
REMIND_DAYS = (3, 5)
OWNER_ALERT_DAY = 5


def np_state(code: int) -> str:
    """Код статусу НП → стан у віджеті: w / b / r / d; '' — ще не рухається або невідомо."""
    if code in NP_RECEIVED:
        return "d"
    if code in NP_AT_BRANCH:
        return "b"
    if code in NP_RETURNING:
        return "r"
    if code in NP_ON_WAY:
        return "w"
    return ""


def free_until_from_np(paid_keeping_from: str) -> str:
    """Останній безкоштовний день зберігання (ISO). НП віддає дату початку платного зберігання."""
    text = str(paid_keeping_from or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return (datetime.strptime(text, fmt).date() - timedelta(days=1)).isoformat()
        except ValueError:
            continue
    return ""


def due_reminder(arrived_on: str, reminded: int, today: date | None = None) -> int:
    """Який день нагадування настав (3 або 5) і ще не надісланий; 0 — нічого."""
    try:
        arrived = date.fromisoformat(str(arrived_on)[:10])
    except ValueError:
        return 0
    days = ((today or date.today()) - arrived).days
    due = [d for d in REMIND_DAYS if days >= d and int(reminded or 0) < d]
    return max(due) if due else 0
