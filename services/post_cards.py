"""Фірмові картки товару 1080×1080 для постів у Telegram-канал (і для превʼю в адмінці).

Чиста функція `render_card` без доступу до БД і мережі: фото приходить байтами, на виході PNG.
Шаблони: «new» (Новинка), «sale» (Акція зі знижкою), «custom» (під замовлення, без ціни).
Ресурси (логотип, топо-патерн, шрифти Codec Pro / Golos Text) лежать в assets/post_cards.
Малюємо у 2× і зменшуємо, щоб краї скруглень і текст були гладкими.
"""

from __future__ import annotations

import functools
import io
import logging
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

import config

logger = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "post_cards"
FONTS = ASSETS / "fonts"

SIZE = 1080          # вихідний розмір (px)
SS = 2               # supersampling
MARGIN = 72
MAX_PHOTO_PIXELS = 40_000_000

BLUE = (0x28, 0x9C, 0xDB)
SKY = (0x4B, 0xB7, 0xE5)
ICE = (0xBD, 0xD9, 0xF2)
MIST = (0xEA, 0xF4, 0xFB)
NAVY = (0x0C, 0x16, 0x28)
SLATE = (0x5A, 0x64, 0x74)
STEEL = (0x8F, 0xA3, 0xB8)
WHITE = (255, 255, 255)

TEMPLATES = ("new", "sale", "custom")
DEFAULT_TAGS = {"new": "НОВИНКА", "sale": "АКЦІЯ", "custom": "ЗА ЗАПИТОМ"}
TITLE_MAX = 120
PRICE_MAX = 24
TAG_MAX = 24
BADGE_MAX = 8
SUBTITLE_MAX = 90

# Фото: прямокутник у координатах 1×
PHOTO_BOX = (MARGIN, 160, SIZE - MARGIN, 760)
PHOTO_RADIUS = 32

_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏ -‮⁦-⁩﻿]")
_WS_RE = re.compile(r"\s+")


# ─── Ресурси ────────────────────────────────────────────────────

@functools.lru_cache(maxsize=32)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


@functools.lru_cache(maxsize=1)
def _topo() -> Image.Image | None:
    try:
        return Image.open(ASSETS / "topo.png").convert("RGBA")
    except Exception:
        logger.warning("post_cards: немає topo.png", exc_info=True)
        return None


@functools.lru_cache(maxsize=1)
def _logo() -> Image.Image | None:
    try:
        return Image.open(ASSETS / "logo-horizontal.png").convert("RGBA")
    except Exception:
        logger.warning("post_cards: немає logo-horizontal.png", exc_info=True)
        return None


@functools.lru_cache(maxsize=256)
def _has_glyph(name: str, ch: str) -> bool:
    """Чи є символ у шрифті (відрізняємо від .notdef порівнянням відмальованих масок)."""
    f = _font(name, 48)
    return bytes(f.getmask(ch)) != bytes(f.getmask(""))


def _runs(text: str, primary: str, fallback: str):
    """Розбиває текст на відрізки; символи, яких немає в основному шрифті (₴), беруться з запасного."""
    out: list[tuple[str, str]] = []
    for ch in text:
        name = primary if (ch.isspace() or _has_glyph(primary, ch)) else fallback
        if out and out[-1][1] == name:
            out[-1] = (out[-1][0] + ch, name)
        else:
            out.append((ch, name))
    return out


def _text_width(text: str, primary: str, fallback: str, size: int, spacing: float = 0.0) -> float:
    total = 0.0
    for chunk, name in _runs(text, primary, fallback):
        total += _font(name, size).getlength(chunk) + spacing * len(chunk)
    return total


def _draw_text(d: ImageDraw.ImageDraw, x: float, baseline: float, text: str, primary: str, fallback: str,
               size: int, fill, spacing: float = 0.0) -> float:
    """Малює текст від baseline; повертає x після останнього символу."""
    for chunk, name in _runs(text, primary, fallback):
        f = _font(name, size)
        if spacing:
            for ch in chunk:
                d.text((x, baseline), ch, font=f, fill=fill, anchor="ls")
                x += f.getlength(ch) + spacing
        else:
            d.text((x, baseline), chunk, font=f, fill=fill, anchor="ls")
            x += f.getlength(chunk)
    return x


# ─── Параметри ──────────────────────────────────────────────────

def card_templates() -> list[str]:
    return list(TEMPLATES)


def _clean(value, limit: int) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    text = _CTRL_RE.sub(" ", str(value))
    return _WS_RE.sub(" ", text).strip()[:limit].strip()


def _parse_amount(value: str) -> float | None:
    """«1 490,50 ₴» → 1490.5; None, якщо числа в рядку немає."""
    m = re.search(r"\d[\d\s .,]*", value or "")
    if not m:
        return None
    s = m.group(0).replace(" ", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    s = s.rstrip(".")
    try:
        return float(s)
    except ValueError:
        return None


def discount_badge(price: str, old_price: str) -> str:
    """«−N%», якщо обидві ціни — числа і стара більша за нову; інакше порожньо."""
    new, old = _parse_amount(price), _parse_amount(old_price)
    if new is None or old is None or new < 0 or old <= new:
        return ""
    pct = int(round((old - new) / old * 100))
    return f"−{pct}%" if 0 < pct < 100 else ""


def normalize_card_params(raw) -> tuple[dict, str]:
    """Перевіряє й обрізає JSON від клієнта. Повертає (параметри, помилка); помилка порожня, якщо все гаразд."""
    if not isinstance(raw, dict):
        return {}, "Некоректні параметри картки"
    template = str(raw.get("template") or "new").strip().lower()
    if template not in TEMPLATES:
        return {}, "Невідомий шаблон картки"
    try:
        photo_index = max(0, min(int(raw.get("photo_index") or 0), 99))
    except (TypeError, ValueError):
        photo_index = 0
    params = {
        "template": template,
        "title": _clean(raw.get("title"), TITLE_MAX),
        "price": _clean(raw.get("price"), PRICE_MAX),
        "old_price": _clean(raw.get("old_price"), PRICE_MAX),
        "tag": _clean(raw.get("tag"), TAG_MAX),
        "subtitle": _clean(raw.get("subtitle"), SUBTITLE_MAX),
        "badge": "",
        "photo_index": photo_index,
    }
    if template in ("new", "sale"):
        # Знижку показуємо і на «Новинці», якщо товар уже зі старою ціною
        params["badge"] = discount_badge(params["price"], params["old_price"])
    else:
        params["old_price"] = ""
    return params, ""


# ─── Малювання ──────────────────────────────────────────────────

def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return m


def _decode_photo(photo: bytes | None) -> Image.Image | None:
    if not photo:
        return None
    try:
        img = Image.open(io.BytesIO(photo))
        w, h = img.size
        if w < 1 or h < 1 or w * h > MAX_PHOTO_PIXELS:
            return None
        img.seek(0)
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, WHITE)
            bg.paste(rgba, mask=rgba.getchannel("A"))
            return bg
        return img.convert("RGB")
    except Exception:
        return None


def _placeholder(size: tuple[int, int], label: str = "ФОТО ТОВАРУ") -> Image.Image:
    """Світлий градієнт із топо-патерном і підписом «ФОТО ТОВАРУ»."""
    w, h = size
    gy = Image.linear_gradient("L").resize((w, h), Image.BILINEAR)
    gx = Image.linear_gradient("L").rotate(90).transpose(Image.FLIP_LEFT_RIGHT).resize((w, h), Image.BILINEAR)
    base = ImageChops.add(gx, gy, scale=2)  # діагональний градієнт зліва-зверху вправо-вниз
    top, bottom = (0xF4, 0xF8, 0xFC), (0xE3, 0xEC, 0xF5)
    img = Image.merge("RGB", [base.point(lambda v, a=top[i], b=bottom[i]: int(a + (b - a) * v / 255)) for i in range(3)])
    topo = _topo()
    if topo is not None:
        scale = max(w, h) / topo.width
        t = topo.resize((round(topo.width * scale), round(topo.height * scale)), Image.LANCZOS)
        t = t.crop(((t.width - w) // 2, (t.height - h) // 2, (t.width - w) // 2 + w, (t.height - h) // 2 + h))
        alpha = t.getchannel("A").point(lambda v: int(v * 0.6))
        img.paste(Image.new("RGB", (w, h), ICE), mask=alpha)
    d = ImageDraw.Draw(img)
    sp = 3.0 * SS
    size_px = 20 * SS
    width = _text_width(label, "CodecPro-Bold.ttf", "GolosText-Bold.ttf", size_px, sp) - sp
    _draw_text(d, (w - width) / 2, h / 2 + 7 * SS, label, "CodecPro-Bold.ttf", "GolosText-Bold.ttf", size_px, STEEL, sp)
    return img


def _cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(img, size, method=Image.LANCZOS, centering=(0.5, 0.5))


def _ellipsize(text: str, fits) -> str:
    if fits(text):
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(text[:mid].rstrip() + "…"):
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip() + "…") if lo else "…"


def _wrap(text: str, font_fn, max_w: float, max_lines: int) -> tuple[list[str], bool]:
    """Жадібний перенос по словах. Повертає (рядки, чи влізло повністю)."""
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for word in words:
        cand = f"{cur} {word}".strip()
        if font_fn(cand) <= max_w:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = word
        # одне слово ширше за рядок — рубаємо по символах
        while font_fn(cur) > max_w and len(cur) > 1:
            n = len(cur)
            while n > 1 and font_fn(cur[:n]) > max_w:
                n -= 1
            lines.append(cur[:n])
            cur = cur[n:]
    if cur:
        lines.append(cur)
    return lines, len(lines) <= max_lines


def _fit_title(title: str, max_w: int, max_size: int = 56) -> tuple[list[str], int]:
    """Підбирає розмір max_size→36 так, щоб влізло у 2 рядки; інакше обрізає з «…»."""
    name, fb = "CodecPro-Bold.ttf", "GolosText-Bold.ttf"
    for size in range(max_size, 35, -2):
        px = size * SS
        lines, ok = _wrap(title, lambda s: _text_width(s, name, fb, px), max_w * SS, 2)
        if ok:
            return lines, size
    px = 36 * SS
    width = lambda s: _text_width(s, name, fb, px)  # noqa: E731
    lines, _ = _wrap(title, width, max_w * SS, 2)
    lines = lines[:2]
    # Останній рядок = решта тексту після першого, скорочена з «…»
    rest = title
    for line in lines[:-1]:
        rest = rest[len(line):].lstrip()
    lines[-1] = _ellipsize(rest, lambda s: width(s) <= max_w * SS)
    return lines, 36


def _paste_shadowed_circle(img: Image.Image, cx: int, cy: int, r: int) -> None:
    """Блакитний круг із градієнтом і м'якою тінню (координати вже у SS)."""
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((cx - r, cy - r + 10 * SS, cx + r, cy + r + 10 * SS), fill=(*BLUE, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14 * SS))
    img.alpha_composite(shadow)
    d = 2 * r
    grad = Image.linear_gradient("L").resize((d, d)).rotate(-45, resample=Image.BILINEAR)
    lut = [tuple(int(SKY[i] + (BLUE[i] - SKY[i]) * v / 255) for i in range(3)) for v in range(256)]
    chans = [grad.point([c[i] for c in lut]) for i in range(3)]
    circle = Image.merge("RGB", chans).convert("RGBA")
    mask = Image.new("L", (d * 2, d * 2), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d * 2 - 1, d * 2 - 1), fill=255)
    circle.putalpha(mask.resize((d, d), Image.LANCZOS))
    img.alpha_composite(circle, (cx - r, cy - r))


def render_card(photo: bytes | None, *, template: str, title: str, price: str = "", old_price: str = "",
                badge: str = "", tag: str = "", subtitle: str = "") -> bytes:
    """Малює картку товару й повертає PNG рівно 1080×1080. Ніколи не падає через текстові параметри."""
    template = template if template in TEMPLATES else "new"
    title = _clean(title, TITLE_MAX) or "Без назви"
    price = _clean(price, PRICE_MAX)
    old_price = _clean(old_price, PRICE_MAX) if template in ("new", "sale") else ""
    badge = _clean(badge, BADGE_MAX) if template in ("new", "sale") else ""
    tag = _clean(tag, TAG_MAX).upper() or DEFAULT_TAGS[template]
    subtitle = _clean(subtitle, SUBTITLE_MAX)
    if template == "custom":
        price, old_price = "", ""

    S = SS
    W = SIZE * S
    img = Image.new("RGBA", (W, W), (*WHITE, 255))
    d = ImageDraw.Draw(img)
    bold, bold_fb = "CodecPro-Bold.ttf", "GolosText-Bold.ttf"
    body, body_fb = "GolosText-Regular.ttf", "GolosText-Medium.ttf"

    # Шапка: логотип зліва
    logo = _logo()
    if logo is not None:
        lh = 66 * S
        lw = round(logo.width * lh / logo.height)
        img.alpha_composite(logo.resize((lw, lh), Image.LANCZOS), (MARGIN * S, 60 * S))

    # Шапка: тег-піл справа
    tag_px, tag_sp = 18 * S, 2.6 * S
    tag_w = _text_width(tag, bold, bold_fb, tag_px, tag_sp) - tag_sp
    pill_w, pill_h = round(tag_w + 44 * S), 38 * S
    max_pill = (SIZE - 2 * MARGIN - 300) * S
    if pill_w > max_pill:  # дуже довгий тег: ужимаємо літерні проміжки не будемо, просто обрізаємо
        tag = _ellipsize(tag, lambda s: _text_width(s, bold, bold_fb, tag_px, tag_sp) + 44 * S <= max_pill)
        tag_w = _text_width(tag, bold, bold_fb, tag_px, tag_sp) - tag_sp
        pill_w = round(tag_w + 44 * S)
    px1, py0 = (SIZE - MARGIN) * S, 74 * S
    d.rounded_rectangle((px1 - pill_w, py0, px1, py0 + pill_h), radius=pill_h // 2, fill=MIST)
    _draw_text(d, px1 - pill_w + 22 * S, py0 + pill_h / 2 + 6.5 * S, tag, bold, bold_fb, tag_px, BLUE, tag_sp)

    # Фото (або заглушка) зі скругленням і тонкою внутрішньою рамкою
    x0, y0, x1, y1 = (v * S for v in PHOTO_BOX)
    box = (x1 - x0, y1 - y0)
    src = _decode_photo(photo)
    tile = _cover(src, box) if src is not None else _placeholder(box)
    tile = tile.convert("RGBA")
    mask = _rounded_mask(box, PHOTO_RADIUS * S)
    img.paste(tile, (x0, y0), mask)
    border = Image.new("RGBA", box, (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle(
        (0, 0, box[0] - 1, box[1] - 1), radius=PHOTO_RADIUS * S, outline=(*NAVY, 22), width=S
    )
    img.alpha_composite(border, (x0, y0))

    # Бейдж знижки: на лівому верхньому куті фото
    if template in ("new", "sale") and badge:
        r = 84 * S
        cx, cy = x0 + 36 * S + r, y0 + 36 * S + r
        _paste_shadowed_circle(img, cx, cy, r)
        d = ImageDraw.Draw(img)
        bsize = 50 * S
        while bsize > 24 * S and _text_width(badge, bold, bold_fb, bsize) > 2 * r - 36 * S:
            bsize -= S
        bw = _text_width(badge, bold, bold_fb, bsize)
        _draw_text(d, cx - bw / 2, cy + bsize * 0.36, badge, bold, bold_fb, bsize, WHITE)

    # Ціна справа (спершу, щоб знати, скільки місця лишилось заголовку)
    right = (SIZE - MARGIN) * S
    price_w = 0
    price_px = 56 * S
    if price:
        while price_px > 36 * S and _text_width(price, bold, bold_fb, price_px) > 330 * S:
            price_px -= S
        price_w = _text_width(price, bold, bold_fb, price_px)

    title_max_w = SIZE - 2 * MARGIN - (round(price_w / S) + 36 if price else 0)
    lines, tsize = _fit_title(title, title_max_w)
    tpx = tsize * S
    # Перший рядок стоїть на тій самій базовій лінії, що й ціна
    base1 = 828 * S if len(lines) == 1 else 822 * S
    for i, line in enumerate(lines):
        _draw_text(d, MARGIN * S, base1 + i * round(tsize * 1.12) * S, line, bold, bold_fb, tpx, NAVY)
    if subtitle:
        # Підзаголовок (характеристики) під заголовком, як на макеті; біля ціни не заходить під неї
        sub_w = (title_max_w if price else SIZE - 2 * MARGIN) * S
        sub_px = 28 * S
        sub_fn = lambda s: _text_width(s, body, body_fb, sub_px)
        subtitle = _ellipsize(subtitle, lambda s: sub_fn(s) <= sub_w)
        sub_base = base1 + (len(lines) - 1) * round(tsize * 1.12) * S + 58 * S
        _draw_text(d, MARGIN * S, sub_base, subtitle, body, body_fb, sub_px, SLATE)
    if price:
        _draw_text(d, right - price_w, base1, price, bold, bold_fb, price_px, BLUE)
        if old_price:
            old_px = 28 * S
            ow = _text_width(old_price, "GolosText-Medium.ttf", "GolosText-Medium.ttf", old_px)
            ob = base1 + 50 * S
            ox = right - ow
            _draw_text(d, ox, ob, old_price, "GolosText-Medium.ttf", "GolosText-Medium.ttf", old_px, STEEL)
            ly = ob - old_px * 0.30
            d.line((ox - 2 * S, ly, right + 2 * S, ly), fill=STEEL, width=round(2.2 * S))

    # Підвал: лінія й підписи
    d.line((MARGIN * S, 972 * S, right, 972 * S), fill=(0xE3, 0xE8, 0xEF), width=S)
    fb_y = 1013 * S
    left_text = f"Магазин у Telegram → @{config.BOT_USERNAME}"
    _draw_text(d, MARGIN * S, fb_y, left_text, "GolosText-Regular.ttf", "GolosText-Medium.ttf", 21 * S, (0x6E, 0x7A, 0x8C))
    brand = "POSELYANOV 3D PRINT"
    bsp = 2.6 * S
    bw = _text_width(brand, bold, bold_fb, 15 * S, bsp) - bsp
    _draw_text(d, right - bw, fb_y, brand, bold, bold_fb, 15 * S, STEEL, bsp)

    out = img.convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=False, compress_level=3)
    return buf.getvalue()


# ─── Картка розіграшу ───────────────────────────────────────────

GIVEAWAY_TITLE_MAX = 100
GIVEAWAY_ENDS_MAX = 24


def render_giveaway_card(photo: bytes | None, *, title: str, ends: str = "") -> bytes:
    """Макет «Пост — Розіграш» 1080×1080: фото призу, «Розігрую …», три кроки участі. Не падає через текст."""
    title = _clean(title, GIVEAWAY_TITLE_MAX) or "приз"
    ends = _clean(ends, GIVEAWAY_ENDS_MAX)

    S = SS
    W = SIZE * S
    img = Image.new("RGBA", (W, W), (*WHITE, 255))
    d = ImageDraw.Draw(img)
    bold, bold_fb = "CodecPro-Bold.ttf", "GolosText-Bold.ttf"
    med, med_fb = "GolosText-Medium.ttf", "GolosText-Medium.ttf"
    right = (SIZE - MARGIN) * S

    logo = _logo()
    if logo is not None:
        lh = 66 * S
        lw = round(logo.width * lh / logo.height)
        img.alpha_composite(logo.resize((lw, lh), Image.LANCZOS), (MARGIN * S, 60 * S))

    def pill(text: str, x: int, y: int, h: int, fill, color, size: int, spacing: float, pad: int, align_right: bool = False) -> None:
        px, sp = size * S, spacing * S
        tw = _text_width(text, bold, bold_fb, px, sp) - sp
        pw = round(tw + 2 * pad * S)
        x0 = x - pw if align_right else x
        d.rounded_rectangle((x0, y, x0 + pw, y + h), radius=h // 2, fill=fill)
        _draw_text(d, x0 + pad * S, y + h / 2 + size * 0.36 * S, text, bold, bold_fb, px, color, sp)

    pill("РОЗІГРАШ", right, 74 * S, 38 * S, MIST, BLUE, 18, 2.6, 22, align_right=True)

    # Фото призу (або заглушка) зі скругленням і тонкою рамкою
    x0, y0, x1, y1 = MARGIN * S, 160 * S, right, 670 * S
    box = (x1 - x0, y1 - y0)
    src = _decode_photo(photo)
    tile = (_cover(src, box) if src is not None else _placeholder(box, "ФОТО ПРИЗУ")).convert("RGBA")
    img.paste(tile, (x0, y0), _rounded_mask(box, PHOTO_RADIUS * S))
    border = Image.new("RGBA", box, (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle((0, 0, box[0] - 1, box[1] - 1), radius=PHOTO_RADIUS * S, outline=(*NAVY, 22), width=S)
    img.alpha_composite(border, (x0, y0))
    pill("БЕЗКОШТОВНО", x0 + 36 * S, y0 + 36 * S, 42 * S, NAVY, WHITE, 18, 2.6, 22)

    # «Розігрую …» — один або два рядки
    lines, tsize = _fit_title(f"Розігрую {title}", SIZE - 2 * MARGIN, max_size=48)
    base1 = 742 if len(lines) == 1 else 736
    step = round(tsize * 1.12)
    for i, line in enumerate(lines):
        _draw_text(d, MARGIN * S, (base1 + i * step) * S, line, bold, bold_fb, tsize * S, NAVY)

    # Три кроки участі
    cy = (826 if len(lines) == 1 else 856) * S
    steps = (
        ("Підпишись", "на канал"),
        ("Натисни «Взяти", "участь» під постом"),
        ("Результати —", ends or "у вказаний день"),
    )
    r = 26 * S
    for i, (l1, l2) in enumerate(steps):
        cx = (MARGIN + 26 + i * 312) * S
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=BLUE)
        num = str(i + 1)
        nw = _text_width(num, bold, bold_fb, 26 * S)
        _draw_text(d, cx - nw / 2, cy + 9 * S, num, bold, bold_fb, 26 * S, WHITE)
        tx = cx + r + 16 * S
        for j, t in enumerate((l1, l2)):
            px = 24 * S
            while px > 18 * S and _text_width(t, med, med_fb, px) > 226 * S:
                px -= S
            _draw_text(d, tx, cy - 1 * S + j * 30 * S, t, med, med_fb, px, NAVY)

    # Підвал
    d.line((MARGIN * S, 972 * S, right, 972 * S), fill=(0xE3, 0xE8, 0xEF), width=S)
    fb_y = 1013 * S
    _draw_text(d, MARGIN * S, fb_y, f"Магазин у Telegram → @{config.BOT_USERNAME}", "GolosText-Regular.ttf", "GolosText-Medium.ttf",
               21 * S, (0x6E, 0x7A, 0x8C))
    brand, bsp = "POSELYANOV 3D PRINT", 2.6 * S
    bw = _text_width(brand, bold, bold_fb, 15 * S, bsp) - bsp
    _draw_text(d, right - bw, fb_y, brand, bold, bold_fb, 15 * S, STEEL, bsp)

    out = img.convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=False, compress_level=3)
    return buf.getvalue()


# ─── Картка підсумків розіграшу ─────────────────────────────────

RESULTS_MAX_PILLS = 4
RESULTS_NAME_MAX = 22


def _drawable(text: str, *fonts: str) -> str:
    """Лишає лише символи, які є хоча б в одному зі шрифтів (емодзі в іменах дали б «квадратики»)."""
    return "".join(ch for ch in text if ch.isspace() or any(_has_glyph(f, ch) for f in fonts))


def render_results_card(names: list[str], *, prize: str = "") -> bytes:
    """Макет «Пост — Підсумки розіграшу» 1080×1080: «Вітаю переможця!» і плашки з іменами. Не падає через текст."""
    bold, bold_fb = "CodecPro-Bold.ttf", "GolosText-Bold.ttf"
    reg, reg_fb = "GolosText-Regular.ttf", "GolosText-Medium.ttf"
    clean = []
    for n in names:
        n = _clean(_drawable(str(n or ""), bold, bold_fb), 60) or "Учасник"
        clean.append(n if len(n) <= RESULTS_NAME_MAX else n[: RESULTS_NAME_MAX - 1].rstrip() + "…")
    clean = clean or ["Учасник"]
    total = len(clean)
    shown = clean[:RESULTS_MAX_PILLS]
    prize = _clean(prize, GIVEAWAY_TITLE_MAX)

    S = SS
    W = SIZE * S
    cx = W // 2
    right = (SIZE - MARGIN) * S

    # Фон: діагональний градієнт і топо-патерн
    gy = Image.linear_gradient("L").resize((W, W), Image.BILINEAR)
    gx = Image.linear_gradient("L").rotate(90).transpose(Image.FLIP_LEFT_RIGHT).resize((W, W), Image.BILINEAR)
    base = ImageChops.add(gx, gy, scale=2)
    top, bottom = (0xF7, 0xFB, 0xFE), (0xE2, 0xEF, 0xF9)
    img = Image.merge("RGB", [base.point(lambda v, a=top[i], b=bottom[i]: int(a + (b - a) * v / 255)) for i in range(3)]).convert("RGBA")
    topo = _topo()
    if topo is not None:
        t = topo.resize((W, W), Image.LANCZOS)
        img.paste(Image.new("RGB", (W, W), ICE), mask=t.getchannel("A").point(lambda v: int(v * 0.55)))
    d = ImageDraw.Draw(img)

    logo = _logo()
    if logo is not None:
        lh = 66 * S
        img.alpha_composite(logo.resize((round(logo.width * lh / logo.height), lh), Image.LANCZOS), (MARGIN * S, 60 * S))
    tag, tag_px, tag_sp = "ПІДСУМКИ", 18 * S, 2.6 * S
    tw = _text_width(tag, bold, bold_fb, tag_px, tag_sp) - tag_sp
    pw, ph = round(tw + 44 * S), 38 * S
    d.rounded_rectangle((right - pw, 74 * S, right, 74 * S + ph), radius=ph // 2, fill=WHITE)
    _draw_text(d, right - pw + 22 * S, 74 * S + ph / 2 + 6.5 * S, tag, bold, bold_fb, tag_px, BLUE, tag_sp)

    # Куб із м'яким світлом
    try:
        cube = Image.open(ASSETS / "cube.png").convert("RGBA")
    except Exception:
        cube = None
    if cube is not None:
        ch = 240 * S
        cube = cube.resize((round(cube.width * ch / cube.height), ch), Image.LANCZOS)
        cy0 = 190 * S
        glow = Image.new("RGBA", img.size, (*WHITE, 0))
        ImageDraw.Draw(glow).ellipse((cx - 190 * S, cy0 - 20 * S, cx + 190 * S, cy0 + ch + 20 * S), fill=(*WHITE, 230))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(40 * S)))
        img.alpha_composite(cube, (cx - cube.width // 2, cy0))
        d = ImageDraw.Draw(img)

    def centered(text: str, baseline: float, primary: str, fb: str, size: int, fill, spacing: float = 0.0) -> None:
        w = _text_width(text, primary, fb, size, spacing) - spacing
        _draw_text(d, cx - w / 2, baseline, text, primary, fb, size, fill, spacing)

    centered("РОЗІГРАШ ЗАВЕРШЕНО", 505 * S, bold, bold_fb, 20 * S, BLUE, 4.0 * S)
    title = "Вітаю переможця!" if total == 1 else "Вітаю переможців!"
    centered(title, 590 * S, bold, bold_fb, 72 * S, NAVY)

    # Плашки з іменами: одна велика, 2–3 стовпчиком, 4+ у два стовпці
    def pill(text: str, x0: int, y0: int, w: int, h: int, size: int) -> None:
        d.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=h // 2, fill=WHITE, outline=BLUE, width=3 * S)
        px = size
        while px > 22 * S and _text_width(text, bold, bold_fb, px) > w - 48 * S:
            px -= S
        tw_ = _text_width(text, bold, bold_fb, px)
        _draw_text(d, x0 + (w - tw_) / 2, y0 + h / 2 + px * 0.36, text, bold, bold_fb, px, BLUE)

    y = 650 * S
    if len(shown) == 1:
        w, h = 520 * S, 92 * S
        pill(shown[0], cx - w // 2, y, w, h, 46 * S)
        y += h
    elif len(shown) <= 3:
        w, h, gap = 520 * S, 64 * S, 12 * S
        for n in shown:
            pill(n, cx - w // 2, y, w, h, 34 * S)
            y += h + gap
        y -= gap
    else:
        w, h, gap = 400 * S, 64 * S, 12 * S
        for i, n in enumerate(shown):
            pill(n, cx - w - gap // 2 + (i % 2) * (w + gap), y + (i // 2) * (h + gap), w, h, 32 * S)
        y += 2 * h + gap

    more = total - len(shown)
    note = f"Приз — {prize}. " if prize else ""
    note += (f"Ще переможців: {more}. " if more > 0 else "") + ("Переможцю" if total == 1 else "Переможцям") + " ми написали в боті. Дякуємо всім за участь!"
    px = 27 * S
    font_fn = lambda s_: _text_width(s_, reg, reg_fb, px)  # noqa: E731
    lines, ok = _wrap(note, font_fn, 780 * S, 2)
    lines = lines[:2]
    if not ok:
        lines[-1] = _ellipsize(lines[-1], lambda s_: font_fn(s_) <= 780 * S)
    by = max(y + 58 * S, 880 * S) if len(lines) == 2 else max(y + 58 * S, 900 * S)
    by = min(by, 905 * S)
    for i, line in enumerate(lines):
        centered(line, by + i * 40 * S, reg, reg_fb, px, SLATE)

    d.line((MARGIN * S, 972 * S, right, 972 * S), fill=(0xD9, 0xE3, 0xEE), width=S)
    fb_y = 1013 * S
    _draw_text(d, MARGIN * S, fb_y, f"Магазин у Telegram → @{config.BOT_USERNAME}", reg, reg_fb, 21 * S, (0x6E, 0x7A, 0x8C))
    brand, bsp = "POSELYANOV 3D PRINT", 2.6 * S
    bw = _text_width(brand, bold, bold_fb, 15 * S, bsp) - bsp
    _draw_text(d, right - bw, fb_y, brand, bold, bold_fb, 15 * S, STEEL, bsp)

    out = img.convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=False, compress_level=3)
    return buf.getvalue()
