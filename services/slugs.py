"""Транслітерація назв у ID (категорії, кольори): «Брелки для ключів» -> «brelky-dlia-kliuchiv»."""

from __future__ import annotations

import re

# Українська національна транслітерація (КМУ 2010), спрощено: без розрізнення початку слова
_UA = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ж": "zh", "з": "z",
    "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ь": "", "ю": "iu", "я": "ia", "'": "", "’": "", "ʼ": "",
    # російські літери — на випадок назв, написаних російською
    "ы": "y", "э": "e", "ё": "io", "ъ": "",
}

SLUG_MAX = 32


def slugify_ua(text: str, fallback: str = "item", max_len: int = SLUG_MAX) -> str:
    """Назва -> `[a-z0-9-]`, без подвійних дефісів. Порожній результат -> `fallback`."""
    out: list[str] = []
    for ch in str(text or "").strip().lower():
        if ch in _UA:
            out.append(_UA[ch])
        elif "a" <= ch <= "z" or "0" <= ch <= "9":
            out.append(ch)
        else:
            out.append("-")
    slug = re.sub(r"-{2,}", "-", "".join(out)).strip("-")[:max_len].strip("-")
    return slug or fallback


def unique_slug(text: str, existing, fallback: str = "item") -> str:
    """slugify_ua + числовий суфікс (-2, -3…), якщо такий ID уже зайнятий."""
    taken = {str(x) for x in existing}
    base = slugify_ua(text, fallback=fallback)
    if base not in taken:
        return base
    n = 2
    while f"{base[: SLUG_MAX - 4]}-{n}" in taken:
        n += 1
    return f"{base[: SLUG_MAX - 4]}-{n}"
