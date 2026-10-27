"""Фірмові картинки акцій 1080×1080 для постів у Telegram-канал (і для превʼю в адмінці).

Усе береться з налаштувань акції (тип, значення, поріг, категорії, дати, колір); адмін може
підправити тексти й сцену вручну. Сцена (мотив) — мінімалістична сезонна/святкова ілюстрація,
намальована Pillow, без емодзі (шрифт бренду їх не має). Малюємо у 2× і зменшуємо.
"""

from __future__ import annotations

import io
import math
import random
import re

from PIL import Image, ImageDraw, ImageFilter

import config
from services import post_cards as pc

S = 2
W = pc.SIZE * S
NAVY = (0x0C, 0x16, 0x28)
WHITE = (255, 255, 255)
RED = (0xE5, 0x48, 0x4D)
GOLD = (0xF2, 0xA9, 0x00)
CIRCLE = (540, 365, 190)   # cx, cy, r у 1×
BOLD, BOLD_FB = "CodecPro-Bold.ttf", "GolosText-Bold.ttf"
MED, REG = "GolosText-Medium.ttf", "GolosText-Regular.ttf"

# (світлий, основний, темний) — токени акценту ті самі, що знає вітрина
ACCENTS = {
    "blue": ((0x4B, 0xB7, 0xE5), (0x28, 0x9C, 0xDB), (0x1B, 0x7F, 0xBA)),
    "orange": ((0xFF, 0xA9, 0x57), (0xF5, 0x82, 0x2A), (0xC9, 0x61, 0x12)),
    "violet": ((0xA5, 0x8B, 0xFF), (0x7C, 0x5C, 0xFC), (0x5B, 0x3F, 0xD6)),
    "green": ((0x4C, 0xC3, 0x8A), (0x22, 0xA0, 0x6B), (0x16, 0x7C, 0x52)),
    "teal": ((0x3C, 0xC9, 0xC4), (0x14, 0xA3, 0xA3), (0x0D, 0x7F, 0x80)),
    "pink": ((0xFF, 0x86, 0xB8), (0xE8, 0x55, 0x9A), (0xBF, 0x35, 0x78)),
    "red": ((0xFF, 0x7A, 0x7E), (0xE5, 0x48, 0x4D), (0xBC, 0x2B, 0x30)),
    "amber": ((0xFF, 0xC9, 0x3C), (0xF2, 0xA9, 0x00), (0xB9, 0x7C, 0x00)),
}
DEFAULT_ACCENT = {"gift_threshold": "orange", "free_ship": "green", "info": "green"}

# Сцени: ключ → (підпис для адмінки, тег у куті за замовчуванням)
MOTIFS = {
    "none": ("Без ілюстрації", ""),
    "percent": ("Знижка (%)", "АКЦІЯ"),
    "gift": ("Подарунок", "ПОДАРУНОК"),
    "first": ("Знайомство", "ЗНАЙОМСТВО"),
    "cart": ("Великий кошик", "АКЦІЯ"),
    "delivery": ("Доставка", "ДОСТАВКА"),
    "newyear": ("Новий рік", "НОВИЙ РІК"),
    "nikolay": ("Миколай", "ДО МИКОЛАЯ"),
    "valentine": ("14 лютого", "14 ЛЮТОГО"),
    "spring": ("8 березня / весна", "8 БЕРЕЗНЯ"),
    "school": ("До школи", "ДО ШКОЛИ"),
    "summer": ("Літо", "ЛІТО"),
    "party": ("Вечірка / вихідні", "ВИХІДНІ"),
    "review": ("Відгук", "ВІДГУК"),
    "birthday": ("День народження", "ПОДАРУНОК"),
}


# ─── Мотиви ─────────────────────────────────────────────────────

def A(c, a):
    return (*c[:3], a)


class Deco:
    def __init__(self, img, pal):
        self.img = img
        self.light, self.main, self.dark = pal
        self.rng = random.Random(7)

    # ── каркас: малюємо елемент у центрі тимчасового шару, обертаємо, вклеюємо ──
    def stamp(self, fn, x, y, size=200, angle=0.0, **kw):
        box = int(size * S * 1.6)
        tmp = Image.new("RGBA", (box, box), (0, 0, 0, 0))
        d = ImageDraw.Draw(tmp)
        fn(d, box / 2, box / 2, size * S, self, **kw)
        if angle:
            tmp = tmp.rotate(angle, resample=Image.BICUBIC)
        self.img.alpha_composite(tmp, (round(x * S - box / 2), round(y * S - box / 2)))

    def confetti(self, zones, n, colors, size=(8, 16)):
        cx, cy, r = CIRCLE
        placed = 0
        while placed < n:
            zx0, zy0, zx1, zy1 = self.rng.choice(zones)
            x, y = self.rng.uniform(zx0, zx1), self.rng.uniform(zy0, zy1)
            if math.hypot(x - cx, y - cy) < r + 28:
                continue
            col = self.rng.choice(colors)
            kind = self.rng.choice(("rect", "dot", "tri"))
            self.stamp(_confetto, x, y, size=self.rng.uniform(*size) * 2.2,
                       angle=self.rng.uniform(0, 360), col=col, kind=kind)
            placed += 1


# ─── Мотиви (кожен малює навколо (x, y); s — розмір у SS-пікселях) ───

def _confetto(d, x, y, s, deco, col, kind):
    h = s / 2.2
    if kind == "dot":
        d.ellipse((x - h / 2, y - h / 2, x + h / 2, y + h / 2), fill=col)
    elif kind == "rect":
        d.rounded_rectangle((x - h, y - h / 3, x + h, y + h / 3), radius=2 * S, fill=col)
    else:
        d.polygon([(x, y - h * .7), (x + h * .7, y + h * .6), (x - h * .7, y + h * .6)], fill=col)


def snowflake(d, x, y, s, deco, col, w=3):
    r = s / 2
    for k in range(6):
        a = math.radians(60 * k)
        ex, ey = x + r * math.cos(a), y + r * math.sin(a)
        d.line((x, y, ex, ey), fill=col, width=round(w * S), joint="curve")
        for side in (-1, 1):
            b = a + side * math.radians(55)
            bx, by = x + r * .58 * math.cos(a), y + r * .58 * math.sin(a)
            d.line((bx, by, bx + r * .32 * math.cos(b), by + r * .32 * math.sin(b)), fill=col, width=round(w * S))
    d.ellipse((x - 3 * S, y - 3 * S, x + 3 * S, y + 3 * S), fill=col)


def sparkle(d, x, y, s, deco, col):
    r, q = s / 2, s / 2 * 0.22
    pts = []
    for k in range(8):
        a = math.radians(45 * k - 90)
        rr = r if k % 2 == 0 else q
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
    d.polygon(pts, fill=col)


def star5(d, x, y, s, deco, col):
    r, q = s / 2, s / 2 * 0.42
    pts = []
    for k in range(10):
        a = math.radians(36 * k - 90)
        rr = r if k % 2 == 0 else q
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
    d.polygon(pts, fill=col)


def heart(d, x, y, s, deco, col, outline=0):
    pts = []
    for i in range(120):
        t = math.tau * i / 120
        hx = 16 * math.sin(t) ** 3
        hy = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((x + hx * s / 36, y + hy * s / 36 + s * 0.04))
    if outline:
        d.line(pts + [pts[0]], fill=col, width=round(outline * S), joint="curve")
    else:
        d.polygon(pts, fill=col)


def pct(d, x, y, s, deco, col):
    f = pc._font("CodecPro-Bold.ttf", round(s))
    d.text((x, y), "%", font=f, fill=col, anchor="mm")


def fir(d, x, y, s, deco, col, trunk, balls=()):
    h = s
    w = s * 0.62
    d.rectangle((x - w * .07, y + h * .42, x + w * .07, y + h * .54), fill=trunk)
    for i in range(3):
        top = y - h * .5 + i * h * .27
        bw = w * (.5 + i * .27)
        d.polygon([(x, top), (x + bw / 2, top + h * .36), (x - bw / 2, top + h * .36)], fill=col)
    for (bx, by, bc) in balls:
        d.ellipse((x + bx * w - 5 * S, y + by * h - 5 * S, x + bx * w + 5 * S, y + by * h + 5 * S), fill=bc)
    star5(d, x, y - h * .5, 22 * S, deco, GOLD)


def gift(d, x, y, s, deco, body, ribbon):
    w = s * .72
    top = y - w * .1
    d.rounded_rectangle((x - w / 2, top, x + w / 2, y + w * .55), radius=6 * S, fill=body)
    d.rounded_rectangle((x - w * .56, top - w * .17, x + w * .56, top + w * .05), radius=5 * S, fill=ribbon)
    d.rectangle((x - w * .08, top - w * .17, x + w * .08, y + w * .55), fill=ribbon)
    for sd in (-1, 1):
        d.ellipse((x + sd * w * .3 - w * .2, top - w * .5, x + sd * w * .3 + w * .2, top - w * .12), outline=ribbon, width=round(5 * S))


def lens(d, x, y, length, width, ang, fill, bend=0.0):
    """Гостролистий «листок»: від (x, y) вздовж кута ang (градуси, 0 = вгору)."""
    pts_l, pts_r = [], []
    a = math.radians(ang)
    for i in range(21):
        t = i / 20
        w = width / 2 * math.sin(math.pi * t) ** 0.85
        # центральна лінія з вигином
        off = bend * math.sin(math.pi * t) * length
        cx = off
        cy = -length * t
        for sign, bucket in ((-1, pts_l), (1, pts_r)):
            px, py = cx + sign * w, cy
            bucket.append((x + px * math.cos(a) - py * math.sin(a), y + px * math.sin(a) + py * math.cos(a)))
    d.polygon(pts_l + pts_r[::-1], fill=fill)


def tulip(d, x, y, s, deco, petal, stem):
    h = s
    base_y = y + h * .22
    # стебло і листя
    d.line((x, base_y, x, y + h * .78), fill=stem, width=round(6 * S))
    lens(d, x, y + h * .78, h * .62, h * .2, -24, stem, bend=-.05)
    lens(d, x, y + h * .78, h * .5, h * .17, 26, stem, bend=.05)
    # бутон: два задніх пелюстки темніші, передній світліший
    dark = (max(petal[0] - 40, 0), max(petal[1] - 40, 0), max(petal[2] - 30, 0), 255)
    light = (min(petal[0] + 35, 255), min(petal[1] + 35, 255), min(petal[2] + 35, 255), 255)
    lens(d, x, base_y, h * .62, h * .3, -16, dark)
    lens(d, x, base_y, h * .62, h * .3, 16, dark)
    lens(d, x, base_y, h * .66, h * .34, 0, petal)
    lens(d, x - h * .03, base_y - h * .06, h * .44, h * .09, 0, light)


def cake(d, x, y, s, deco, base, icing, flame):
    w = s
    cream = (255, 240, 214, 255)
    pink = icing
    # тарілка
    d.rounded_rectangle((x - w * .62, y + w * .4, x + w * .62, y + w * .48), radius=6 * S, fill=(0xD9, 0xE3, 0xEE, 255))
    d.rounded_rectangle((x - w * .1, y + w * .46, x + w * .1, y + w * .52), radius=3 * S, fill=(0xC4, 0xD1, 0xE0, 255))
    # нижній ярус
    d.rounded_rectangle((x - w * .5, y + w * .06, x + w * .5, y + w * .42), radius=int(w * .06), fill=cream)
    # верхній ярус
    d.rounded_rectangle((x - w * .32, y - w * .22, x + w * .32, y + w * .08), radius=int(w * .05), fill=cream)

    def drips(x0, x1, y0, depth):
        d.rounded_rectangle((x0, y0 - w * .03, x1, y0 + w * .06), radius=int(w * .04), fill=pink)
        n = 6
        step = (x1 - x0) / n
        for i in range(n):
            cx = x0 + step * (i + .5)
            ln = depth * (1.0 if i % 2 == 0 else .55)
            d.rounded_rectangle((cx - step * .3, y0, cx + step * .3, y0 + ln), radius=int(step * .3), fill=pink)
    drips(x - w * .5, x + w * .5, y + w * .06, w * .16)
    drips(x - w * .32, x + w * .32, y - w * .22, w * .13)
    # посипка
    accents = [deco.main, deco.dark, GOLD, WHITE]
    rng = random.Random(3)
    for i in range(11):
        sx = x - w * .42 + rng.random() * w * .84
        sy = y + w * .26 + rng.random() * w * .12
        a = rng.random() * math.pi
        c = accents[i % 4]
        d.line((sx, sy, sx + math.cos(a) * 9 * S, sy + math.sin(a) * 9 * S), fill=(*c[:3], 255), width=round(4 * S))
    # свічки
    for k, c in zip((-1, 0, 1), (deco.main, deco.light, deco.dark)):
        cx = x + k * w * .16
        top = y - w * .22 - w * .02
        d.rounded_rectangle((cx - 4 * S, top - w * .2, cx + 4 * S, top), radius=2 * S, fill=(*c[:3], 255))
        d.line((cx - 3 * S, top - w * .13, cx + 3 * S, top - w * .17), fill=WHITE, width=round(3 * S))
        fy = top - w * .2
        d.polygon([(cx, fy - w * .13), (cx + 7 * S, fy - w * .03), (cx, fy), (cx - 7 * S, fy - w * .03)], fill=flame)
        d.ellipse((cx - 3 * S, fy - w * .06, cx + 3 * S, fy - w * .01), fill=(255, 244, 200, 255))


def daisy(d, x, y, s, deco, petal, center):
    r = s / 2
    for k in range(8):
        a = math.radians(45 * k)
        px, py = x + r * .55 * math.cos(a), y + r * .55 * math.sin(a)
        d.ellipse((px - r * .3, py - r * .3, px + r * .3, py + r * .3), fill=petal)
    d.ellipse((x - r * .28, y - r * .28, x + r * .28, y + r * .28), fill=center)


def pencil(d, x, y, s, deco, body, tip, band):
    L, hh = s, s * .13
    x0 = x - L / 2
    d.rectangle((x0 + L * .12, y - hh, x0 + L * .86, y + hh), fill=body)
    d.rectangle((x0 + L * .12, y - hh, x0 + L * .2, y + hh), fill=band)
    d.polygon([(x0 + L * .86, y - hh), (x0 + L, y), (x0 + L * .86, y + hh)], fill=(0xF6, 0xDC, 0xB4))
    d.polygon([(x0 + L * .95, y - hh * .3), (x0 + L, y), (x0 + L * .95, y + hh * .3)], fill=NAVY)
    d.rounded_rectangle((x0, y - hh, x0 + L * .12, y + hh), radius=4 * S, fill=tip)


def book(d, x, y, s, deco, cover, page):
    w, h = s, s * .75
    d.rounded_rectangle((x - w / 2, y - h / 2, x + w / 2, y + h / 2), radius=8 * S, fill=cover)
    d.rectangle((x - w / 2 + w * .1, y - h / 2 + h * .08, x + w / 2 - w * .06, y + h / 2 - h * .08), fill=page)
    for i in range(3):
        yy = y - h * .22 + i * h * .2
        d.line((x - w * .28, yy, x + w * .3, yy), fill=A(cover, 140), width=round(4 * S))


def sun(d, x, y, s, deco, col, ray):
    r = s * .3
    for k in range(14):
        a = math.radians(360 / 14 * k)
        d.line((x + r * 1.3 * math.cos(a), y + r * 1.3 * math.sin(a), x + r * 1.9 * math.cos(a), y + r * 1.9 * math.sin(a)),
               fill=ray, width=round(9 * S), joint="curve")
    d.ellipse((x - r, y - r, x + r, y + r), fill=col)


def waves(d, x, y, s, deco, col):
    for j in range(3):
        pts = [(x - s / 2 + i * s / 60, y + j * 26 * S + math.sin(i / 60 * math.tau * 2.5) * 9 * S) for i in range(61)]
        d.line(pts, fill=col, width=round(6 * S), joint="curve")


def balloon(d, x, y, s, deco, col):
    w, h = s * .62, s * .78
    d.line([(x + math.sin(i / 8 * 3) * 6 * S, y + h * .5 + i / 8 * s * .5) for i in range(9)], fill=A(NAVY, 110), width=round(2.5 * S))
    d.polygon([(x, y + h * .5 - 2 * S), (x - 7 * S, y + h * .5 + 9 * S), (x + 7 * S, y + h * .5 + 9 * S)], fill=col)
    d.ellipse((x - w / 2, y - h / 2, x + w / 2, y + h / 2), fill=col)
    d.ellipse((x - w * .27, y - h * .3, x - w * .1, y - h * .08), fill=(255, 255, 255, 110))


def streamer(d, x, y, s, deco, col):
    pts = [(x + math.sin(i / 40 * math.tau * 3) * s * .12, y - s / 2 + i / 40 * s) for i in range(41)]
    d.line(pts, fill=col, width=round(7 * S), joint="curve")


def cart(d, x, y, s, deco, col):
    w = s
    d.line([(x - w * .55, y - w * .38), (x - w * .35, y - w * .38), (x - w * .2, y + w * .18), (x + w * .35, y + w * .18)],
           fill=col, width=round(9 * S), joint="curve")
    d.polygon([(x - w * .3, y - w * .26), (x + w * .5, y - w * .26), (x + w * .36, y + w * .1), (x - w * .2, y + w * .1)], fill=col)
    for cx in (x - w * .12, x + w * .28):
        d.ellipse((cx - 9 * S, y + w * .26, cx + 9 * S, y + w * .26 + 18 * S), fill=col)


def truck(d, x, y, s, deco, body, cab, wheel):
    w = s
    d.rounded_rectangle((x - w * .5, y - w * .26, x + w * .14, y + w * .16), radius=8 * S, fill=body)
    d.polygon([(x + w * .14, y - w * .12), (x + w * .34, y - w * .12), (x + w * .5, y + w * .04), (x + w * .5, y + w * .16), (x + w * .14, y + w * .16)], fill=cab)
    d.polygon([(x + w * .2, y - w * .06), (x + w * .31, y - w * .06), (x + w * .41, y + w * .05), (x + w * .2, y + w * .05)], fill=(255, 255, 255, 230))
    for cx in (x - w * .3, x + w * .3):
        d.ellipse((cx - 14 * S, y + w * .1, cx + 14 * S, y + w * .1 + 28 * S), fill=wheel)
        d.ellipse((cx - 6 * S, y + w * .1 + 8 * S, cx + 6 * S, y + w * .1 + 20 * S), fill=WHITE)
    for k, ln in enumerate((.32, .22, .28)):
        yy = y - w * .12 + k * w * .12
        d.line((x - w * .62 - w * ln, yy, x - w * .6, yy), fill=A(body, 120), width=round(5 * S))


def pin(d, x, y, s, deco, col):
    r = s * .3
    d.polygon([(x - r * .9, y + r * .3), (x + r * .9, y + r * .3), (x, y + s * .62)], fill=col)
    d.ellipse((x - r, y - r, x + r, y + r), fill=col)
    d.ellipse((x - r * .4, y - r * .4, x + r * .4, y + r * .4), fill=WHITE)


def bubble(d, x, y, s, deco, col):
    w, h = s, s * .7
    d.rounded_rectangle((x - w / 2, y - h / 2, x + w / 2, y + h / 2), radius=22 * S, fill=col)
    d.polygon([(x - w * .2, y + h / 2 - 2 * S), (x - w * .34, y + h / 2 + 22 * S), (x - w * .02, y + h / 2 - 2 * S)], fill=col)
    for i in range(3):
        cx = x + (i - 1) * w * .22
        d.ellipse((cx - 6 * S, y - 6 * S, cx + 6 * S, y + 6 * S), fill=WHITE)


def hat(d, x, y, s, deco, col):
    w = s
    d.polygon([(x - w * .44, y), (x - w * .34, y - w * .3), (x - w * .1, y - w * .5), (x + w * .2, y - w * .52),
               (x + w * .4, y - w * .42), (x + w * .44, y - w * .3), (x + w * .44, y)], fill=col)
    d.ellipse((x + w * .31, y - w * .56, x + w * .53, y - w * .34), fill=WHITE)
    d.rounded_rectangle((x - w * .5, y - w * .05, x + w * .5, y + w * .17), radius=int(w * .1), fill=WHITE)


# ─── Композиції по акціях ────────────────────────────────────────


ZONES = [(70, 140, 340, 600), (740, 140, 1010, 600)]


# ─── Композиції по сценах ────────────────────────────────────────

def draw_back(img, key, pal):
    D = Deco(img, pal)
    L, M, Dk = pal
    conf = [A(L, 230), A(M, 230), (255, 255, 255, 255)]
    TREE = A((0x8B, 0x5E, 0x3C), 255)
    GREEN = A((0x3F, 0xA9, 0x6B), 255)

    if key == "percent":
        D.stamp(pct, 170, 330, 170, -14, col=A(M, 60))
        D.stamp(pct, 905, 420, 120, 12, col=A(M, 50))
        D.stamp(sparkle, 925, 215, 52, col=A(M, 190))
    elif key == "gift":
        D.stamp(gift, 165, 430, 180, -8, body=A(M, 255), ribbon=A(Dk, 255))
        D.stamp(gift, 910, 290, 110, 10, body=A(L, 255), ribbon=A(Dk, 255))
        D.confetti(ZONES, 5, conf)
    elif key == "first":
        D.stamp(sparkle, 165, 300, 80, col=A(M, 200)); D.stamp(sparkle, 915, 420, 64, col=A(L, 255))
        D.stamp(star5, 905, 230, 48, 12, col=A(M, 170))
    elif key == "cart":
        D.stamp(cart, 170, 430, 180, -6, col=A(M, 230))
        D.stamp(sparkle, 915, 260, 56, col=A(L, 255))
    elif key == "delivery":
        D.stamp(truck, 175, 410, 240, 0, body=A(M, 255), cab=A(Dk, 255), wheel=A(NAVY, 255))
        D.stamp(pin, 905, 250, 120, 0, col=A(Dk, 255))
        pts = [(280 + i * 6.2, 560 - math.sin(i / 100 * math.pi) * 90) for i in range(101)]
        d = ImageDraw.Draw(img)
        for i in range(0, 100, 5):
            (x0, y0), (x1, y1) = pts[i], pts[i + 2]
            d.line((x0 * S, y0 * S, x1 * S, y1 * S), fill=A(M, 150), width=7 * S)
    elif key == "newyear":
        for (x, y, s) in [(160, 210, 84), (905, 230, 64), (860, 360, 46)]:
            D.stamp(snowflake, x, y, s, 0, col=A(M, 170), w=3)
        D.stamp(fir, 150, 470, 190, 0, col=A(M, 255), trunk=TREE, balls=[(-.1, -.15, RED), (.12, .05, GOLD)])
        D.stamp(fir, 915, 500, 130, 0, col=A(Dk, 255), trunk=TREE, balls=[(.1, -.1, RED)])
    elif key == "nikolay":
        D.stamp(gift, 165, 450, 160, -8, body=A(M, 255), ribbon=A(Dk, 255))
        D.stamp(snowflake, 905, 240, 80, 0, col=A(M, 150), w=3)
        D.stamp(snowflake, 850, 420, 48, 0, col=A(L, 255), w=3)
        D.stamp(star5, 150, 250, 48, 10, col=GOLD)
    elif key == "valentine":
        D.stamp(heart, 160, 260, 160, -14, col=A(M, 255), outline=4)
        D.stamp(heart, 240, 450, 66, 14, col=A(L, 255))
        D.stamp(heart, 895, 440, 120, -10, col=A(M, 235))
        D.stamp(heart, 940, 260, 56, 12, col=A(L, 255))
    elif key == "spring":
        D.stamp(tulip, 150, 410, 230, -5, petal=A(M, 255), stem=GREEN)
        D.stamp(tulip, 250, 470, 160, 7, petal=A(L, 255), stem=GREEN)
        D.stamp(tulip, 915, 420, 200, 6, petal=A(M, 255), stem=GREEN)
    elif key == "school":
        D.stamp(pencil, 190, 420, 250, -58, body=A(M, 255), tip=A(Dk, 255), band=A(Dk, 255))
        D.stamp(book, 905, 380, 140, 8, cover=A(Dk, 255), page=(255, 255, 255, 255))
        D.stamp(star5, 905, 230, 44, -12, col=GOLD)
    elif key == "summer":
        D.stamp(sun, 945, 305, 300, 0, col=A(L, 255), ray=A(M, 200))
        D.stamp(waves, 170, 450, 250, 0, col=A(M, 150))
    elif key == "party":
        D.stamp(balloon, 190, 320, 150, -8, col=A(M, 255)); D.stamp(balloon, 255, 440, 100, 8, col=A(L, 255))
        D.stamp(balloon, 900, 350, 140, 8, col=A(Dk, 255))
        D.confetti(ZONES, 8, conf + [GOLD])
    elif key == "review":
        D.stamp(star5, 160, 300, 110, -10, col=GOLD)
        D.stamp(star5, 250, 450, 64, 12, col=GOLD)
        D.stamp(bubble, 905, 350, 130, 6, col=A(M, 255))
    elif key == "birthday":
        D.stamp(balloon, 160, 300, 150, -8, col=A(M, 255)); D.stamp(balloon, 245, 430, 110, 8, col=A(L, 255))
        D.stamp(cake, 905, 400, 200, 0, base=A(Dk, 255), icing=(255, 134, 184, 255), flame=GOLD)
        D.confetti([(70, 140, 340, 600), (760, 140, 1010, 250)], 5, conf + [GOLD])


def draw_front(img, key, pal):
    D = Deco(img, pal)
    if key == "nikolay":
        cx, cy, r = CIRCLE
        D.stamp(hat, cx - 40, cy - r + 12, 170, -16, col=RED + (255,))


# ─── Параметри з налаштувань акції ───────────────────────────────

MAX = {"tag": 24, "big": 8, "caption": 30, "title": 70, "subtitle": 120, "condition": 44}
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF←-⇿⌀-⏿①-➿⤀-⥿⬀-⯿️‍]")
# Підстрічки id акції → сцена (раніше за емодзі: id стабільніший)
_ID_MOTIFS = (
    ("newyear", "newyear"), ("new_year", "newyear"), ("nicholas", "nikolay"), ("nikolay", "nikolay"),
    ("valentine", "valentine"), ("march", "spring"), ("spring", "spring"), ("school", "school"),
    ("summer", "summer"), ("weekend", "party"), ("review", "review"), ("birthday", "birthday"),
    ("first_order", "first"), ("big_cart", "cart"), ("delivery", "delivery"), ("gift", "gift"),
)
_EMOJI_MOTIFS = {
    "🎄": "newyear", "❄": "newyear", "🎅": "nikolay", "💝": "valentine", "❤": "valentine", "💘": "valentine",
    "🌷": "spring", "🌸": "spring", "🎒": "school", "☀": "summer", "🏖": "summer", "🎉": "party", "🥳": "party",
    "⭐": "review", "🎂": "birthday", "🎁": "gift", "🚚": "delivery", "📦": "cart", "🛒": "cart", "👋": "first",
}
_TYPE_MOTIFS = {"gift_threshold": "gift", "free_ship": "delivery"}


def _strip_emoji(text) -> str:
    return pc._WS_RE.sub(" ", _EMOJI_RE.sub("", str(text or ""))).strip()


def _fmt_money(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    return f"{n:,}".replace(",", " ") + " ₴"


def _fmt_date(value) -> str:
    from services.promotions import _parse_dt

    dt = _parse_dt(value)
    if dt is None:
        return ""
    try:
        from zoneinfo import ZoneInfo

        dt = dt.astimezone(ZoneInfo("Europe/Kyiv"))
    except Exception:
        pass
    return dt.strftime("%d.%m")


def detect_motif(promo: dict) -> str:
    pid = str(promo.get("id") or "").lower()
    for needle, motif in _ID_MOTIFS:
        if needle in pid:
            return motif
    for ch in str(promo.get("emoji") or ""):
        if ch in _EMOJI_MOTIFS:
            return _EMOJI_MOTIFS[ch]
    return _TYPE_MOTIFS.get(promo.get("type"), "percent")


def promo_defaults(promo: dict, cat_names: dict | None = None) -> dict:
    """Параметри картинки з реальних налаштувань акції (тип, значення, поріг, категорії, дати, колір)."""
    cat_names = cat_names or {}
    ptype = promo.get("type")
    try:
        value = int(promo.get("value") or 0)
        min_order = int(promo.get("min_order") or 0)
    except (TypeError, ValueError):
        value = min_order = 0
    cats = [cat_names.get(c.strip(), c.strip()) for c in str(promo.get("category") or "").split(",") if c.strip()]
    motif = detect_motif(promo)
    tag = MOTIFS[motif][1] or "АКЦІЯ"
    cond = f"від {_fmt_money(min_order)}" if min_order else ""
    if ptype == "order_percent":
        big, caption = f"−{value}%", "на замовлення"
        cond = cond or "на всі замовлення"
    elif ptype == "order_fixed":
        big, caption = f"−{_fmt_money(value)}", "на замовлення"
        cond = cond or "на всі замовлення"
    elif ptype == "cat_percent":
        big, caption = f"−{value}%", ", ".join(cats[:2]) if cats else "на категорії"
        cond = cond or (", ".join(cats) if cats else "")
    elif ptype == "gift_threshold":
        try:
            n = int(promo.get("min_items") or 0)
        except (TypeError, ValueError):
            n = 0
        big, caption = "+1", "подарунок"
        cond = f"від {n} товарів" if n else ""
        if cats:
            cond = f"{cond} · {cats[0]}" if cond else cats[0]
        tag = "ПОДАРУНОК"
    elif ptype == "free_ship":
        big, caption = "0 ₴", "за доставку"
        cond = cond or "для всіх замовлень"
        tag = "ДОСТАВКА"
    else:
        big, caption = _strip_emoji(promo.get("badge")) or "НОВИНА", ""
        cond = ""
        tag = MOTIFS[motif][1] or "ІНФО"
    end = _fmt_date(promo.get("ends_at"))
    start = _fmt_date(promo.get("starts_at"))
    if end:
        cond = f"{cond} · до {end}" if cond else f"до {end}"
    elif start:
        cond = f"{cond} · з {start}" if cond else f"з {start}"
    accent = promo.get("accent")
    if accent not in ACCENTS:
        accent = DEFAULT_ACCENT.get(ptype, "blue")
    return {
        "tag": tag, "big": big, "caption": caption,
        "title": _strip_emoji(promo.get("title")) or str(promo.get("id") or ""),
        "subtitle": _strip_emoji(promo.get("subtitle")),
        "condition": cond, "accent": accent, "motif": motif,
    }


def normalize_promo_params(raw, promo: dict, cat_names: dict | None = None) -> tuple[dict, str]:
    """Типові значення з акції + правки адміна. Повертає (params, error)."""
    base = promo_defaults(promo, cat_names)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return base, "Некоректні параметри картинки"
    out = dict(base)
    for key, limit in MAX.items():
        if isinstance(raw.get(key), str):
            out[key] = pc._clean(raw[key], limit)
    out["tag"] = out["tag"].upper()
    if raw.get("accent") is not None:
        if raw["accent"] not in ACCENTS:
            return base, "Невідомий колір"
        out["accent"] = raw["accent"]
    if raw.get("motif") is not None:
        if raw["motif"] not in MOTIFS:
            return base, "Невідома ілюстрація"
        out["motif"] = raw["motif"]
    return out, ""


def shop_link() -> str:
    """Кнопка під постом акції: відкриває Mini App (без привʼязки до товару)."""
    return f"https://t.me/{config.BOT_USERNAME}?startapp=promo"


def default_post_text(promo: dict, params: dict) -> str:
    """Підпис до поста: перший рядок (жирним) — заголовок; далі опис і умови."""
    head = (str(promo.get("emoji") or "").strip() + " " + params["title"]).strip()
    lines = [head]
    if params["subtitle"]:
        lines += ["", params["subtitle"]]
    if params["condition"]:
        lines += ["", f"📌 {params['condition']}"]
    lines += ["", "Відкривай магазин і користуйся 👇"]
    return "\n".join(lines)


# ─── Малювання ───────────────────────────────────────────────────

def _tw(text, font, size, sp=0.0, fb=BOLD_FB):
    return pc._text_width(text, font, fb, size, sp) - (sp if sp else 0)


def _center(d, cx, base, text, font, size, fill, sp=0.0, fb=BOLD_FB):
    pc._draw_text(d, cx - _tw(text, font, size, sp, fb) / 2, base, text, font, fb, size, fill, sp)


def _wrap(text, font, size, max_w, fb=REG):
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if _tw(t, font, size, 0, fb) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _gradient_circle(img, cx, cy, r, c1, c2):
    shadow = Image.new("RGBA", img.size, (*c2, 0))
    ImageDraw.Draw(shadow).ellipse((cx - r, cy - r + 14 * S, cx + r, cy + r + 14 * S), fill=(*c2, 95))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(20 * S)))
    dd = 2 * r
    grad = Image.linear_gradient("L").resize((dd, dd)).rotate(-45, resample=Image.BILINEAR)
    lut = [tuple(int(c1[i] + (c2[i] - c1[i]) * v / 255) for i in range(3)) for v in range(256)]
    circle = Image.merge("RGB", [grad.point([c[i] for c in lut]) for i in range(3)]).convert("RGBA")
    mask = Image.new("L", (dd * 2, dd * 2), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, dd * 2 - 1, dd * 2 - 1), fill=255)
    circle.putalpha(mask.resize((dd, dd), Image.LANCZOS))
    img.alpha_composite(circle, (cx - r, cy - r))


def render_promo_card(*, tag: str = "", big: str = "", caption: str = "", title: str = "", subtitle: str = "",
                      condition: str = "", accent: str = "blue", motif: str = "percent") -> bytes:
    """PNG 1080×1080. Не падає через текстові параметри."""
    pal = ACCENTS.get(accent, ACCENTS["blue"])
    light, main, dark = pal
    motif = motif if motif in MOTIFS else "percent"
    img = Image.new("RGBA", (W, W), (255, 255, 255, 255))

    topo = pc._topo()
    if topo is not None:
        t = topo.resize((1200 * S, 1200 * S), Image.LANCZOS)
        t.putalpha(t.getchannel("A").point(lambda v: int(v * 0.16)))
        img.alpha_composite(t, (-60 * S, -60 * S))
    glow = Image.new("RGBA", img.size, (255, 255, 255, 0))
    ImageDraw.Draw(glow).ellipse((60 * S, 300 * S, 1020 * S, 960 * S), fill=(255, 255, 255, 235))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(70 * S)))
    tint = Image.new("RGBA", img.size, (*light, 0))
    ImageDraw.Draw(tint).ellipse((300 * S, 130 * S, 780 * S, 610 * S), fill=(*light, 70))
    img.alpha_composite(tint.filter(ImageFilter.GaussianBlur(60 * S)))

    logo = pc._logo()
    if logo is not None:
        lh = 66 * S
        lw = round(logo.width * lh / logo.height)
        img.alpha_composite(logo.resize((lw, lh), Image.LANCZOS), (72 * S, 60 * S))
    tag = (tag or "").strip()
    if tag:
        px, sp = 18 * S, 2.6 * S
        pw, ph = round(_tw(tag, BOLD, px, sp) + 44 * S), 38 * S
        x1, y0 = 1008 * S, 60 * S + (66 * S - ph) // 2
        pill = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(pill).rounded_rectangle((x1 - pw, y0, x1, y0 + ph), radius=ph // 2, fill=(*main, 36))
        img.alpha_composite(pill)
        pc._draw_text(ImageDraw.Draw(img), x1 - pw + 22 * S, y0 + ph / 2 + 6.5 * S, tag, BOLD, BOLD_FB, px, dark, sp)

    draw_back(img, motif, pal)
    cx, cy, r = CIRCLE[0] * S, CIRCLE[1] * S, CIRCLE[2] * S
    _gradient_circle(img, cx, cy, r, light, main)
    draw_front(img, motif, pal)
    d = ImageDraw.Draw(img)

    big = (big or "").strip() or "АКЦІЯ"
    size = 150 * S
    while size > 30 * S and _tw(big, BOLD, size) > 2 * r - 70 * S:
        size -= 4 * S
    base = cy + size * 0.20 if caption else cy + size * 0.36
    _center(d, cx, base, big, BOLD, size, WHITE)
    if caption:
        csize = 30 * S
        while csize > 16 * S and _tw(caption, MED, csize, 0, BOLD_FB) > 2 * r - 90 * S:
            csize -= 2 * S
        _center(d, cx, base + 52 * S, caption, MED, csize, (255, 255, 255, 235), 0, BOLD_FB)

    title = (title or "").strip() or "Акція"
    tsize = 64 * S
    lines = _wrap(title, BOLD, tsize, 900 * S, BOLD_FB)
    while tsize > 30 * S and (len(lines) > 2 or any(_tw(ln, BOLD, tsize) > 900 * S for ln in lines)):
        tsize -= 2 * S
        lines = _wrap(title, BOLD, tsize, 900 * S, BOLD_FB)
    tbase = 668 * S
    for i, ln in enumerate(lines[:2]):
        _center(d, 540 * S, tbase + i * 76 * S, ln, BOLD, tsize, pc.NAVY)
    nl = min(len(lines), 2)

    sub = (subtitle or "").strip()
    ssize = 28 * S
    sl = _wrap(sub, MED, ssize, 800 * S, MED)[:3] if sub else []
    if sl:
        best = sl
        for mw in range(780, 380, -20):
            t2 = _wrap(sub, MED, ssize, mw * S, MED)
            if len(t2) > len(sl):
                break
            best = t2
        sl = best[:3]
    sbase = tbase + (nl - 1) * 76 * S + 62 * S
    for i, ln in enumerate(sl):
        _center(d, 540 * S, sbase + i * 40 * S, ln, MED, ssize, pc.SLATE, 0, MED)

    condition = (condition or "").strip()
    if condition:
        csz = 26 * S
        pw2, ph2 = round(_tw(condition, BOLD, csz, 0) + 64 * S), 56 * S
        py = (sbase + (len(sl) - 1) * 40 * S + 34 * S) if sl else (tbase + (nl - 1) * 76 * S + 44 * S)
        py = min(py, 972 * S - ph2 - 20 * S)
        pill2 = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(pill2).rounded_rectangle(
            (540 * S - pw2 / 2, py, 540 * S + pw2 / 2, py + ph2), radius=ph2 // 2, fill=(*main, 255))
        img.alpha_composite(pill2)
        d = ImageDraw.Draw(img)
        _center(d, 540 * S, py + ph2 / 2 + 9 * S, condition, BOLD, csz, WHITE)

    d.line((72 * S, 972 * S, 1008 * S, 972 * S), fill=(0xE3, 0xE8, 0xEF), width=S)
    pc._draw_text(d, 72 * S, 1012 * S, f"Магазин у Telegram → @{config.BOT_USERNAME}", MED, MED, 20 * S, pc.SLATE)
    brand, bsp = "POSELYANOV 3D PRINT", 2.4 * S
    bw = _tw(brand, REG, 16 * S, bsp, REG)
    pc._draw_text(d, 1008 * S - bw, 1011 * S, brand, REG, REG, 16 * S, pc.STEEL, bsp)

    out = io.BytesIO()
    img.convert("RGB").resize((pc.SIZE, pc.SIZE), Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()
