"""Re-download the Material Symbols subset with every icon the UI uses.

    uv run python scripts/update_icon_font.py

Scans the static HTML/JS and categories.py for icon names, intersects them with
the official Material Symbols name list and fetches a subset font from Google
Fonts into src/synthsift/static/vendor/fonts/ (so the UI works offline).
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "src/synthsift/static/vendor/fonts"
NAMES_URL = (
    "https://raw.githubusercontent.com/google/material-design-icons/master/variablefont/"
    "MaterialSymbolsOutlined%5BFILL%2CGRAD%2Copsz%2Cwght%5D.codepoints"
)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def main() -> None:
    official = {line.split()[0] for line in fetch(NAMES_URL).decode().splitlines() if line.strip()}
    sources = [*ROOT.glob("src/synthsift/static/**/*.html"), *ROOT.glob("src/synthsift/static/js/*.js"),
               ROOT / "src/synthsift/categories.py"]
    used: set[str] = set()
    for path in sources:
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'["\'>]([a-z][a-z0-9_]{1,40})["\'<]', text)) & official
    names = ",".join(sorted(used))
    css = fetch(
        "https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,400,0..1,0"
        f"&icon_names={names}&display=block"
    ).decode()
    url = re.search(r"url\((https://[^)]+)\)", css).group(1)
    (FONT_DIR / "material-symbols-outlined.woff2").write_bytes(fetch(url))
    print(f"{len(used)} icons -> {FONT_DIR / 'material-symbols-outlined.woff2'}")


if __name__ == "__main__":
    main()
