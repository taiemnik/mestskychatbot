"""Datové typy vstupu: řádek vytažený ze souboru, údaje o dokumentu a jeho rozložený text."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Line:
    """Jeden kus textu tak, jak ho vrátí čtení souboru (odstavec Wordu, řádek PDF, řádek textu).

    style:  „h1“ až „h6“ u nadpisu (Word, Markdown, HTML), jinak None
    center: řádek je v originálu vystředěný (napovídá, že jde o nadpis článku)
    level:  odsazení položky seznamu (0 = bez odsazení); pozná se jen v Markdownu a HTML
    rows:   řádky tabulky (pak je `text` prázdný)
    blank:  prázdný řádek – v PDF a textu odděluje odstavce
    """
    text: str = ""
    page: int | None = None
    style: str | None = None
    center: bool = False
    level: int = 0
    rows: list[list[str]] | None = None
    blank: bool = False


@dataclass
class DocMeta:
    """Údaje o dokumentu. Co není známo, je None – chatbot to pak prostě neukáže."""
    id: int                         # stálé číslo dokumentu (otisk cesty souboru), je v odkazech na odstavce
    soubor: str                     # cesta ve složce dokumentů
    nazev: str
    druh: str | None = None         # „Obecně závazná vyhláška“, „Nařízení“, „Směrnice“…
    cislo: str | None = None        # „5/2024“
    ucinnost_od: str | None = None  # RRRR-MM-DD
    zdroj: str | None = None        # adresa originálu (Sbírka právních předpisů, web obce)
    poznamka: str | None = None     # upozornění u zdroje, např. že předpis změnila novela

    @property
    def label(self) -> str:
        """Označení dokumentu u citace: „Obecně závazná vyhláška č. 2/2026 o nočním klidu“."""
        if self.druh and self.cislo:
            return f"{self.druh} č. {self.cislo} {self.nazev}"
        if self.cislo:
            return f"{self.nazev} č. {self.cislo}"
        return self.nazev


@dataclass
class SubItem:
    """Pododstavec (písmeno, bod, odrážka) uvnitř odstavce; může mít další úroveň.
    `marker` je označení z originálu („a)“, „1.“, „–“); prázdné = značku doplní zobrazení (a, b, c…)."""
    text: str
    children: list["SubItem"] = field(default_factory=list)
    marker: str = ""


@dataclass
class Block:
    """Jeden kus textu dokumentu v pořadí, v jakém jde za sebou.

    kind:   title (název dokumentu) | priloha | part (část, hlava, větší nadpis) | article (článek, menší nadpis) |
            para (odstavec s číslem nebo s pododstavci) | text (odstavec bez čísla) | table | note (upozornění –
            není text dokumentu)
    anchor: kotva pro zobrazení a zvýraznění (b0, b1, … v pořadí dokumentu)
    number: u článku číslo z nadpisu („3“, u paragrafu „§ 3“), u odstavce jeho číslo
    page:   strana originálu, když ji známe (PDF)
    """
    kind: str
    text: str
    anchor: str = ""
    number: str | None = None
    items: list[SubItem] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    page: int | None = None


@dataclass
class ParsedDoc:
    """Rozložený dokument.

    flags: podpisy (podpisové řádky vynechány) | bez-clanku (text není členěn na články) | prepis (místo originálu
           se čte jeho přepis v Markdownu) | obrazky (v souboru jsou obrázky nebo mapy, které se nečtou) |
           popisky (strana PDF jen s čísly a značkami – mapa nebo výkres – se nečte) |
           priloha-bez-textu (příloha má jen nadpis – její obsah se nepřečetl)
    """
    meta: DocMeta
    blocks: list[Block]
    flags: list[str] = field(default_factory=list)
