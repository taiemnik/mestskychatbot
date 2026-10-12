"""Z nastavení (`chatbot.toml`) vyrobí soubory, které potřebuje stránka.

  public/nastaveni.js   název, organizace a texty (`window.NASTAVENI`) + slova, která nejsou jména
  public/vzhled.css     barvy odvozené z jedné hlavní barvy (světlý i tmavý režim)
  public/logo.svg       ikona stránky v hlavní barvě
  public/otazky.json    návrhy otázek na úvodní stránce

Stránka je statická a bez sestavování – proto se nastavení propisuje do souborů, ne do šablony.
Soubory se přepíšou při každém `python tasks.py build` a `serve`; ručně se neupravují.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from chatbot.config import Instance

DARK_BG = "#0f1b2a"                     # pozadí tmavého režimu (s ním se míchají tmavé odstíny)
STEM_CHARS = 5                          # stejné zkrácení používá public/osobni-udaje.js
MAX_KNOWN_WORDS = 6000
_CAPITALIZED = re.compile(r"\b[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýž]{2,}")


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def mix(color: str, other: str, share: float) -> str:
    """Smíchá dvě barvy: `share` = podíl druhé barvy (0 = první, 1 = druhá)."""
    a, b = _rgb(color), _rgb(other)
    return "#" + "".join(f"{round(x + (y - x) * share):02x}" for x, y in zip(a, b))


def luminance(color: str) -> float:
    """Relativní jas barvy podle WCAG (0 = černá, 1 = bílá)."""
    def channel(value: int) -> float:
        share = value / 255
        return share / 12.92 if share <= 0.03928 else ((share + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(v) for v in _rgb(color))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def theme_css(color: str) -> str:
    """Proměnné barev pro světlý a tmavý režim odvozené z hlavní barvy. Přepisují výchozí hodnoty ve styles.css."""
    light = mix(color, "#ffffff", 0.45)
    # text na tlačítku v hlavní barvě: bílý, u světlé barvy tmavý (kontrast)
    on_accent = "#ffffff" if luminance(color) < 0.35 else "#10202f"
    r, g, b = _rgb(light)
    dark_accent = mix(color, "#ffffff", 0.6)
    dr, dg, db = _rgb(dark_accent)
    dark = (f"--accent: {dark_accent}; --accent-hover: {mix(color, '#ffffff', 0.72)}; --accent-text: #0b1826; "
            f"--bubble: {mix(color, DARK_BG, 0.5)}; --sky-soft: {mix(color, DARK_BG, 0.75)}; "
            f"--mark-bg: {mix(color, DARK_BG, 0.55)}; --focus: rgba({dr}, {dg}, {db}, .35);")
    return f"""/* Vzniká z chatbot.toml příkazem `python tasks.py build` – neupravujte ručně. */
:root {{
  --c-navy: {color};
  --c-blue: {light};
  --c-sky: {mix(color, "#ffffff", 0.85)};
  --accent-hover: {mix(color, "#ffffff", 0.15)};
  --accent-text: {on_accent};
  --bubble: {mix(color, "#ffffff", 0.86)};
  --bubble-text: {mix(color, "#000000", 0.4)};
  --sky-soft: {mix(color, "#ffffff", 0.92)};
  --mark-bg: {mix(color, "#ffffff", 0.88)};
  --focus: rgba({r}, {g}, {b}, .35);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-rezim="svetly"]) {{ {dark} }}
}}
:root[data-rezim="tmavy"] {{ {dark} }}
"""


def logo_svg(color: str) -> str:
    """Ikona stránky: dlaždice v hlavní barvě s bublinou. Stejná kresba je symbol #i-logo v index.html."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect x="3" y="3" width="58" height="58" rx="15" fill="{color}"/>
  <path d="M18 18h28a6 6 0 0 1 6 6v14a6 6 0 0 1-6 6H32l-9 8v-8h-5a6 6 0 0 1-6-6V24a6 6 0 0 1 6-6Z" fill="#ffffff"/>
  <path d="M22 28h20M22 35h13" fill="none" stroke="{color}" stroke-width="3.2" stroke-linecap="round"/>
</svg>
"""


def stem(word: str) -> str:
    """Zkrácený tvar slova pro porovnání (malá písmena bez diakritiky, prvních pět znaků): „Lhotce“ → „lhotc“."""
    plain = unicodedata.normalize("NFKD", word.lower())
    return "".join(ch for ch in plain if not unicodedata.combining(ch))[:STEM_CHARS]


def known_words(texts: list[str]) -> list[str]:
    """Slova s velkým písmenem, která se vyskytují v dokumentech (názvy ulic, částí obce, institucí).

    Stránka před odesláním dotazu varuje, když dotaz vypadá na jméno a příjmení (dvě slova s velkým
    písmenem za sebou). Slova z dokumentů za jméno nepovažuje – jinak by varovala u každé otázky na ulici.
    """
    found: dict[str, int] = {}
    for text in texts:
        for word in _CAPITALIZED.findall(text):
            key = stem(word)
            found[key] = found.get(key, 0) + 1
    frequent = sorted(found, key=lambda key: (-found[key], key))[:MAX_KNOWN_WORDS]
    return sorted(frequent)


def _corpus(index_dir: Path) -> list[str]:
    """Texty úseků hotového indexu (když existuje) – zdroj známých slov."""
    path = Path(index_dir) / "chunks.json"
    if not path.is_file():
        return []
    try:
        return [" ".join(item.get("path", [])) + " " + item.get("text", "") for item in json.loads(path.read_text(encoding="utf-8"))]
    except (ValueError, AttributeError, TypeError):
        return []


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))          # write_bytes: konce řádků LF i na Windows


def write_public(instance: Instance, public_dir: Path, index_dir: Path) -> None:
    """Zapíše do složky stránky soubory odvozené z nastavení a z hotového indexu."""
    public_dir = Path(public_dir)
    public_dir.mkdir(parents=True, exist_ok=True)
    settings = {
        "nazev": instance.nazev, "organizace": instance.organizace, "popis": instance.popis,
        "upozorneni": instance.upozorneni, "kontakt": instance.kontakt, "odkaz": instance.odkaz, "navod": instance.navod,
        "barva": instance.barva, "barva_tmava": DARK_BG,
        "znama_slova": known_words(_corpus(index_dir) + [instance.nazev, instance.organizace]),
    }
    payload = json.dumps(settings, ensure_ascii=False, indent=1).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    _write(public_dir / "nastaveni.js",
           "/* Vzniká z chatbot.toml příkazem `python tasks.py build` – neupravujte ručně. */\n"
           f"window.NASTAVENI = {payload};\n")
    _write(public_dir / "vzhled.css", theme_css(instance.barva))
    _write(public_dir / "logo.svg", logo_svg(instance.barva))
    _write(public_dir / "otazky.json", json.dumps(list(instance.navrhy), ensure_ascii=False, indent=0) + "\n")
