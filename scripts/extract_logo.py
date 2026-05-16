"""Винести inline SVG з index.html у logo.svg (один раз).

Запуск з кореня репозиторію:
  python scripts/extract_logo.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
LOGO = ROOT / "logo.svg"

START = '      <svg id="Layer_2"'
END = '      </svg>'


def main():
    text = INDEX.read_text(encoding="utf-8")
    start = text.find(START)
    end = text.find(END, start)
    if start < 0 or end < 0:
        raise SystemExit("SVG block not found in index.html")

    end += len(END)
    svg = text[start:end].strip() + "\n"
    LOGO.write_text(svg, encoding="utf-8")

    replacement = (
        '      <img src="logo.svg" alt="Poselyanov 3D Print" width="200" height="48" decoding="async">'
    )
    new_text = text[:start] + replacement + text[end:]
    INDEX.write_text(new_text, encoding="utf-8")
    print(f"OK: {LOGO.name} ({len(svg)} bytes), index.html оновлено")


if __name__ == "__main__":
    main()
