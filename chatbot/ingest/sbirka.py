"""Sbírka právních předpisů územních samosprávných celků (sbirkapp.gov.cz).

Každá obec, město i kraj má ve Sbírce stránku se seznamem svých obecně závazných vyhlášek a nařízení:
https://sbirkapp.gov.cz/vydavatel/<ID datové schránky>. Tento modul seznam přečte, u každého platného
předpisu otevře detail, stáhne text (PDF nebo DOCX) i přílohy a zapíše přehled `prehled.json`
s údaji, které chatbot ukazuje u zdroje (druh, číslo, účinnost, odkaz na originál, návaznosti).

Zrušené a vyřazené předpisy se nestahují – chatbot má odpovídat jen z toho, co platí. Co platit
přestalo od minulého stažení, se ze složky odloží stranou (`sbirka/_neplatne/`), nemaže se.

Chyba nesmí vypadat jako „předpis přestal platit“: odkládá se jen soubor předpisu, o kterém seznam
výslovně říká, že neplatí. Když se nenačte detail předpisu nebo předpis v seznamu úplně chybí (výpadek,
neúplné stránkování), jeho dřív stažené soubory i záznam v přehledu zůstávají a stažení to ohlásí.
Předpis, jehož soubor člověk sám odložil do složky začínající podtržítkem, se znovu nestahuje.

Stránky Sbírky se čtou tak, jak je vidí prohlížeč (veřejné rozhraní pro stroje Sbírka nemá).
Mezi požadavky je krátká pauza, aby stahování server nezatěžovalo.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import quote

BASE = "https://sbirkapp.gov.cz"
USER_AGENT = "mestskychatbot (+https://github.com/taiemnik/mestskychatbot)"
SUBDIR = "sbirka"               # podsložka ve složce dokumentů, kam se předpisy ukládají
STALE_DIR = "_neplatne"         # kam se odloží, co platit přestalo (složky s podtržítkem chatbot nečte)
MAX_PAGES = 200                 # pojistka proti nekonečnému stránkování
MAX_FILE_BYTES = 60_000_000     # větší soubor se neuloží (text předpisu má desítky kB až jednotky MB)
SLUG_CHARS = 60
# soubor, který ve složce už je, se znovu nestahuje: originál, nebo jeho ruční přepis (v tomto pořadí přednosti)
KEPT_SUFFIXES = (".pdf", ".docx", ".odt", ".md")
# co se ve složce `sbirka/` bere jako soubor předpisu (jiné soubory – desktop.ini, .DS_Store – se nechávají být)
DOCUMENT_SUFFIXES = KEPT_SUFFIXES + (".doc", ".rtf", ".txt", ".html", ".htm")
_ATTACHMENT_STEM = re.compile(r"-priloha-\d{1,3}$")
_DS_ID = re.compile(r"[a-z0-9]{7}")
_URL_ID = re.compile(r"^https?://sbirkapp\.gov\.cz/vydavatel/([a-z0-9]{7})(?:[/?#].*)?$", re.IGNORECASE)
_REPEALED = re.compile(r"zrušeno k\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", re.IGNORECASE)
_NUMBER = re.compile(r"^(\d{1,4})/(\d{4})$")
# začátek názvu, který jen opakuje druh a vydavatele: „Obecně závazná vyhláška města Horní Áíčkov …“
_NAME_PREFIX = re.compile(r"^\s*(?:obecně závazná vyhláška|nařízení)\b.*?[\s,]+(?=(?:o|k|ke|kterou|kterým|na|pro)\s)",
                          re.IGNORECASE)
KIND_SHORT = {"obecně závazná vyhláška": "ozv", "nařízení": "narizeni"}

Fetch = Callable[[str], bytes]


class SbirkaError(Exception):
    """Sbírka neodpověděla nebo odpověděla něčím, čemu modul nerozumí."""


class NotFound(SbirkaError):
    """Stránka neexistuje (404) – u seznamu to znamená konec stránkování."""


@dataclass(frozen=True)
class Entry:
    """Jeden řádek seznamu předpisů vydavatele."""
    detail_id: str          # „SPPVZOR0000000A1“
    cislo: str              # „2/2026“
    druh: str               # „Obecně závazná vyhláška“ | „Nařízení“
    nazev: str
    vyhlaseno: str          # RRRR-MM-DD (když jde datum přečíst)
    ucinnost_od: str        # RRRR-MM-DD
    platnost: str           # „Platné“ | „Zrušeno k …“ | „Vyřazeno“

    @property
    def platny(self) -> bool:
        """Sbírka předpis vede jako platný. Jestli platí k dnešku, říká `is_valid` (bere i budoucí zrušení)."""
        return self.platnost.strip().lower().startswith("platn")


@dataclass(frozen=True)
class Relation:
    """Návaznost na jiný předpis. `smer`: „na-jiny“ = tento předpis mění nebo ruší jiný,
    „na-tento“ = jiný předpis mění nebo ruší tento."""
    smer: str
    druh: str               # „Novelizace“, „Zrušení“…
    cislo: str
    nazev: str


@dataclass(frozen=True)
class Detail:
    nazev: str
    udaje: dict[str, str]                       # „Druh právního předpisu“ → hodnota
    soubory: list[tuple[str, str]]              # (název souboru, adresa); první je text předpisu, další přílohy
    navaznosti: list[Relation] = field(default_factory=list)


@dataclass
class SbirkaReport:
    vydavatel: str = ""
    nazev_vydavatele: str = ""                  # jak se vydavatel jmenuje na stránce Sbírky (kontrola, že je to správná obec)
    nalezeno: int = 0                           # všech předpisů v seznamu
    platnych: int = 0
    stazeno: list[str] = field(default_factory=list)
    ponechano: list[str] = field(default_factory=list)      # soubor už ve složce byl
    odlozeno: list[str] = field(default_factory=list)       # přestalo platit → sbirka/_neplatne/
    problemy: list[str] = field(default_factory=list)

    def format(self) -> str:
        who = f"{self.vydavatel} ({self.nazev_vydavatele})" if self.nazev_vydavatele else self.vydavatel
        lines = [f"Sbírka právních předpisů – vydavatel {who}",
                 f"Předpisů v seznamu: {self.nalezeno} · platných: {self.platnych} · staženo souborů: {len(self.stazeno)} "
                 f"· ponecháno: {len(self.ponechano)}"]
        for title, items in (("Staženo", self.stazeno), ("Odloženo (už neplatí)", self.odlozeno),
                             ("K ruční kontrole", self.problemy)):
            if items:
                lines += [f"{title} ({len(items)}):"] + [f"  - {item}" for item in items]
        return "\n".join(lines)


# ------------------------------------------------------------------ čtení stránek
def _soup(html: str):
    from bs4 import BeautifulSoup       # jen při stahování; běžící chatbot tuhle knihovnu nepotřebuje

    return BeautifulSoup(html, "html.parser")


def _clean(text: str) -> str:
    return " ".join(text.split())


def _iso_date(text: str) -> str:
    """„14.04.2026“ → „2026-04-14“; jiný text vrátí očištěný beze změny."""
    match = re.fullmatch(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", _clean(text))
    if not match:
        return _clean(text)
    day, month, year = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_list(html: str) -> list[Entry]:
    """Řádky seznamu předpisů na jedné straně stránky vydavatele."""
    entries = []
    for row in _soup(html).select("tr.gov-journal__link"):
        cells = {cell.get("data-title", ""): cell for cell in row.find_all("td")}
        number = cells.get("Číslo právního předpisu")
        link = number.find("a", href=re.compile(r"^/detail/[A-Z0-9]+$")) if number else None
        if link is None:
            continue
        kind = cells.get("Druh právního předpisu")
        # v buňce druhu bývá i štítek („VYŘAZENO“) – druh je první řádek textu
        kind_text = next((_clean(part) for part in kind.stripped_strings), "") if kind else ""

        def text(title: str) -> str:
            return _clean(cells[title].get_text(" ")) if title in cells else ""

        entries.append(Entry(detail_id=link["href"].rsplit("/", 1)[1], cislo=_clean(link.get_text()), druh=kind_text,
                             nazev=text("Název právního předpisu"), vyhlaseno=_iso_date(text("Datum vyhlášení")),
                             ucinnost_od=_iso_date(text("Datum nabytí účinnosti")), platnost=text("Platnost")))
    return entries


def parse_publisher(html: str) -> str:
    """Název vydavatele, jak ho uvádí první řádek seznamu („Město Horní Áíčkov IČO: 00000001“); prázdné, když tam není."""
    row = _soup(html).select_one("tr.gov-journal__link")
    cell = row.find("td", attrs={"data-title": "Publikující"}) if row else None
    return _clean(cell.get_text(" ")) if cell else ""


def repeal_date(entry: Entry) -> str | None:
    """Datum z údaje „Zrušeno k 1.1.2031“ jako RRRR-MM-DD; None, když předpis takový údaj nemá."""
    match = _REPEALED.search(entry.platnost)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def is_valid(entry: Entry, today: str | None = None) -> bool:
    """Platí předpis k dnešku? „Platné“ ano; „Zrušeno k <datum>“ jen do dne před tím datem; „Vyřazeno“ ne."""
    if entry.platny:
        return True
    until = repeal_date(entry)
    return until is not None and (today or date.today().isoformat()) < until


def repeal_note(entry: Entry, relations: list["Relation"] | tuple = ()) -> str:
    """Upozornění u předpisu, který ještě platí, ale Sbírka už uvádí, k jakému dni je zrušen (a čím)."""
    until = repeal_date(entry)
    if until is None:
        return ""
    year, month, day = (int(part) for part in until.split("-"))
    by = next((rel.cislo for rel in relations if rel.smer == "na-tento" and "zruš" in rel.druh.lower()), "")
    return f"Pozor: tento předpis je k {day}. {month}. {year} zrušen" + (f" předpisem č. {by}" if by else "") + "; do té doby platí."


def parse_detail(html: str, detail_id: str) -> Detail:
    """Detail předpisu: údaje z postranního sloupce, soubory ke stažení a návaznosti na jiné předpisy."""
    soup = _soup(html)
    title = soup.find("h1")
    data: dict[str, str] = {}
    aside = soup.find("aside")
    for heading in (aside.find_all("h4") if aside else []):
        value = heading.find_next_sibling("p")
        if value is not None:
            data[_clean(heading.get_text())] = _clean(value.get_text(" "))
    # Soubory: text předpisu má adresu …/text, přílohy …/prilohy/<číslo>. Jiné odkazy na stránce
    # (například …/verze s historií oprav) soubory nejsou.
    wanted = re.compile(rf"^/detail/{re.escape(detail_id)}/(text|prilohy/\d+)$")
    links = [(_clean(a.get_text()) or "soubor", a["href"]) for a in soup.find_all("a", href=True) if wanted.match(a["href"])]
    files = [link for link in links if link[1].endswith("/text")] + [link for link in links if not link[1].endswith("/text")]
    relations = []
    for heading in soup.find_all("h3"):
        table = heading.find_next_sibling("table")
        if table is None:
            continue
        direction = "na-tento" if "navazují na tento" in _clean(heading.get_text()).lower() else "na-jiny"
        for row in table.select("tbody tr"):
            cells = {cell.get("data-title", ""): _clean(cell.get_text(" ")) for cell in row.find_all("td")}
            if cells.get("Číslo právního předpisu"):
                relations.append(Relation(smer=direction, druh=cells.get("Druh návaznosti", ""),
                                          cislo=cells["Číslo právního předpisu"], nazev=cells.get("Název podání", "")))
    return Detail(nazev=_clean(title.get_text(" ")) if title else "", udaje=data, soubory=files, navaznosti=relations)


# ------------------------------------------------------------------ názvy a poznámky
def slug(text: str, limit: int = SLUG_CHARS) -> str:
    """Název souboru z textu: malá písmena bez diakritiky, slova spojená pomlčkou, zkráceno na celé slovo."""
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    words = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")
    if len(words) > limit:
        words = words[:limit].rsplit("-", 1)[0]
    return words or "dokument"


def short_name(name: str) -> str:
    """Název bez úvodního opakování druhu a vydavatele: „Obecně závazná vyhláška města Horní Áíčkov o nočním klidu“
    → „o nočním klidu“. Název, který tak nezačíná („Tržní řád“), zůstane celý."""
    shortened = _NAME_PREFIX.sub("", name, count=1).strip()
    return shortened or name.strip()


def file_stem(entry: Entry) -> str:
    """Název souboru bez přípony: „ozv-2026-02-o-nocnim-klidu“."""
    kind = KIND_SHORT.get(entry.druh.strip().lower()) or slug(entry.druh, 20)
    number = _NUMBER.match(entry.cislo.strip())
    numbering = f"{number.group(2)}-{int(number.group(1)):02d}" if number else slug(entry.cislo, 20)
    return f"{kind}-{numbering}-{slug(short_name(entry.nazev))}"


def relation_note(relations: list[Relation]) -> str:
    """Upozornění, které chatbot ukáže u zdroje. Jen to, co mění, jak se má předpis číst:
    předpis byl změněn novelou, předpis sám je novelou jiného, nebo ho jiný předpis ruší.
    Že předpis při svém vydání zrušil starší úpravu, čtenáře odpovědi nezajímá – to upozornění není."""
    notes = []
    for rel in relations:
        kind = rel.druh.lower()
        if rel.smer == "na-tento" and "noveliz" in kind:
            notes.append(f"Pozor: tento předpis byl změněn předpisem č. {rel.cislo} – ověřte znění i tam.")
        elif rel.smer == "na-tento" and "zruš" in kind:
            notes.append(f"Pozor: předpis č. {rel.cislo} tento předpis ruší.")
        elif rel.smer == "na-jiny" and "noveliz" in kind:
            notes.append(f"Tento předpis mění předpis č. {rel.cislo} – čtěte ho spolu s ním.")
    return " ".join(dict.fromkeys(notes))


def _note(entry: Entry, relations: list[Relation]) -> str:
    """Poznámka do přehledu. Když Sbírka uvádí den zrušení, řekne ho jedna věta i s tím, čím je předpis zrušen –
    obecná věta o zrušení z návazností se pak už neopakuje."""
    repeal = repeal_note(entry, relations)
    others = [rel for rel in relations if not (repeal and rel.smer == "na-tento" and "zruš" in rel.druh.lower())]
    return " ".join(part for part in (relation_note(others), repeal) if part)


def relation_list(relations: list[Relation]) -> list[str]:
    """Všechny návaznosti slovy („ruší č. 2/2025“, „změněn č. 2/2022“) – do přehledu pro člověka."""
    words = {("na-jiny", "noveliz"): "mění č.", ("na-jiny", "zruš"): "ruší č.",
             ("na-tento", "noveliz"): "změněn č.", ("na-tento", "zruš"): "zrušen č."}
    out = []
    for rel in relations:
        for (direction, stem_), word in words.items():
            if rel.smer == direction and stem_ in rel.druh.lower():
                out.append(f"{word} {rel.cislo}")
    return list(dict.fromkeys(out))


def _extension(data: bytes, link_name: str) -> str | None:
    """Přípona podle obsahu souboru (ne podle názvu – ten u Sbírky někdy příponu nemá). None = neumíme číst."""
    if data.startswith(b"%PDF"):
        return ".pdf"
    if data.startswith(b"PK\x03\x04"):            # balík ZIP: Word (.docx) i OpenDocument (.odt)
        return ".odt" if link_name.lower().endswith(".odt") else ".docx"
    return None


def _unsupported_reason(data: bytes) -> str:
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        return "starý formát Wordu (.doc) – otevřete ho ve Wordu a uložte jako .docx nebo PDF"
    if data.lstrip().startswith(b"{\\rtf"):
        return "formát RTF – uložte ho jako .docx nebo PDF"
    return "neznámý formát souboru"


# ------------------------------------------------------------------ stažení
def http_fetch(url: str) -> bytes:
    """Výchozí stahování přes httpx. 404 je `NotFound`, jiná chyba `SbirkaError`."""
    import httpx

    try:
        response = httpx.get(url, follow_redirects=True, timeout=60.0, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        raise SbirkaError(f"Sbírka neodpovídá ({type(exc).__name__}): {url}") from None
    if response.status_code == 404:
        raise NotFound(url)
    if response.status_code >= 400:
        raise SbirkaError(f"Sbírka vrátila chybu {response.status_code}: {url}")
    return response.content


def publisher_id(value: str) -> str:
    """ID datové schránky vydavatele z ID nebo z adresy stránky ve Sbírce. Cokoli jiného je chyba –
    hodnota jde do adresy a do názvu složky, nesmí v ní být nic navíc."""
    text = value.strip()
    match = _URL_ID.match(text)
    if match:
        return match.group(1).lower()
    if _DS_ID.fullmatch(text.lower()):
        return text.lower()                         # ID datové schránky velikost písmen nerozlišuje
    raise ValueError("Zadejte ID datové schránky obce (7 znaků, například abc1234) nebo adresu její stránky "
                     "ve Sbírce: https://sbirkapp.gov.cz/vydavatel/<ID>.")


def search(query: str, fetch: Fetch | None = None) -> list[tuple[str, str]]:
    """Vydavatelé podle názvu: seznam (ID datové schránky, název s krajem a IČO). Používá našeptávač Sbírky –
    stejný, jaký nabízí její vyhledávací pole. Obcí téhož jména bývá víc; rozliší je kraj, PSČ a IČO."""
    text = " ".join(query.split())
    if not text:
        return []
    unavailable = SbirkaError("Sbírka na hledání vydavatele neodpověděla seznamem – zkuste to později nebo hledejte "
                              "na https://sbirkapp.gov.cz (ID je na konci adresy stránky obce).")
    try:
        raw = (fetch or http_fetch)(f"{BASE}/xhr/naseptavac/ovm?q={quote(text)}")
    except NotFound:
        raise unavailable from None                 # našeptávač na staré adrese není – samotná adresa by nic neřekla
    try:
        items = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        raise unavailable from None
    found = []
    for item in items if isinstance(items, list) else []:
        code, name = (item.get("kod"), item.get("name")) if isinstance(item, dict) else (None, None)
        if isinstance(code, str) and isinstance(name, str) and _DS_ID.fullmatch(code.lower()):
            found.append((code.lower(), _clean(name)))
    return found


def list_entries(ds_id: str, fetch: Fetch, pause: float) -> tuple[list[Entry], str]:
    """Všechny předpisy vydavatele přes všechny strany seznamu a název vydavatele z první strany."""
    entries: list[Entry] = []
    seen: set[str] = set()
    publisher = ""
    for page in range(1, MAX_PAGES + 1):
        try:
            html = fetch(f"{BASE}/vydavatel/{ds_id}?page={page}").decode("utf-8", errors="replace")
        except NotFound:
            if page == 1:
                raise SbirkaError(f"Vydavatel s ID datové schránky „{ds_id}“ ve Sbírce není.") from None
            break
        except SbirkaError as exc:
            if page == 1:
                # na neznámé ID Sbírka odpovídá chybou serveru, ne „nenalezeno“ – nejčastěji je to překlep
                raise SbirkaError(f"Stránku vydavatele „{ds_id}“ se nepodařilo načíst ({exc}). Zkontrolujte ID – "
                                  "správné najdete příkazem: python tasks.py sbirka --hledej \"název obce\"") from None
            raise
        fresh = [entry for entry in parse_list(html) if entry.detail_id not in seen]
        if not fresh:
            break
        publisher = publisher or parse_publisher(html)
        seen.update(entry.detail_id for entry in fresh)
        entries.extend(fresh)
        time.sleep(pause)
    return entries, publisher


def download(vydavatel: str, out_dir: Path, fetch: Fetch | None = None, pause: float = 0.6,
             log: Callable[[str], None] = lambda line: None, today: str | None = None) -> SbirkaReport:
    """Stáhne platné předpisy vydavatele do `out_dir/sbirka/` a zapíše `out_dir/prehled.json`.

    `vydavatel` je ID datové schránky nebo adresa stránky ve Sbírce. Soubor, který ve složce už je,
    se znovu nestahuje (opravenou verzi téhož předpisu stáhnete tak, že starý soubor přesunete mimo složku
    dokumentů). Předpis, jehož soubor leží ve složce začínající podtržítkem (`sbirka/_neplatne/`, `_stranou/`),
    odložil člověk – nestahuje se a stažení to ohlásí. Údaje k vlastním dokumentům (mimo `sbirka/`)
    v přehledu zůstávají. `today` (RRRR-MM-DD) jen pro testy.
    """
    ds_id = publisher_id(vydavatel)
    fetch = fetch or http_fetch
    today = today or date.today().isoformat()
    out_dir = Path(out_dir)
    previous = _read_overview(out_dir / "prehled.json")     # nečitelný přehled je chyba dřív, než se cokoli změní
    target = out_dir / SUBDIR
    target.mkdir(parents=True, exist_ok=True)
    report = SbirkaReport(vydavatel=ds_id)

    entries, report.nazev_vydavatele = list_entries(ds_id, fetch, pause)
    if not entries:
        # stránka existuje, ale řádky předpisů v ní nejsou: jiné ID, nebo Sbírka změnila podobu stránky
        raise SbirkaError(f"Na stránce vydavatele „{ds_id}“ nejsou žádné předpisy – buď žádné nevydal, nebo Sbírka "
                          "změnila podobu stránky. Ve složce se nic nezměnilo.")
    valid = [entry for entry in entries if is_valid(entry, today)]
    report.nalezeno, report.platnych = len(entries), len(valid)
    _note_oddities(valid, today, report)
    documents: dict[str, dict] = {}
    expected: set[str] = set()                      # názvy souborů bez přípony, které do složky patří
    unverified: set[str] = set()                    # platné předpisy, u kterých dnes nevíme, jaké mají přílohy
    for entry in valid:
        stem = file_stem(entry)
        what = f"{entry.druh} č. {entry.cislo}"
        expected.add(stem)                          # platný předpis do složky patří, i kdyby se dnes nic nestáhlo
        log(f"{what} – {short_name(entry.nazev)}")
        aside = _set_aside_by_hand(out_dir, stem)
        if aside:
            report.problemy.append(f"{what}: soubor je odložen ve složce {aside}/ – nestahuji ho znovu. "
                                   "Má-li v chatbotovi být, přesuňte ho zpět.")
            unverified.add(stem)
            continue
        try:
            detail = parse_detail(fetch(f"{BASE}/detail/{entry.detail_id}").decode("utf-8", errors="replace"),
                                  entry.detail_id)
        except SbirkaError as exc:
            report.problemy.append(f"{what}: detail se nepodařilo načíst ({exc}) – "
                                   "dřív stažené soubory zůstávají, zkuste stažení zopakovat")
            unverified.add(stem)
            continue
        time.sleep(pause)
        # text předpisu má adresu …/text, ostatní soubory jsou přílohy; týž odkaz bývá na stránce dvakrát
        links = list(dict((href, (name, href)) for name, href in detail.soubory).values())
        texts = [link for link in links if link[1].endswith("/text")]
        if not texts:
            # bez textu by se první příloha uložila pod názvem předpisu a četla jako jeho znění
            report.problemy.append(f"{what}: ve Sbírce není soubor s textem – nestahuji ani přílohy")
            unverified.add(stem)
            continue
        main = _save(fetch, BASE + texts[0][1], target, stem, texts[0][0], report, what=what)
        time.sleep(pause)
        attachments: list[str] = []
        for order, (link_name, href) in enumerate((link for link in links if not link[1].endswith("/text")), start=1):
            expected.add(f"{stem}-priloha-{order}")
            name = _save(fetch, BASE + href, target, f"{stem}-priloha-{order}", link_name, report,
                         what=f"{what}, příloha {order}")
            if name:
                attachments.append(f"{SUBDIR}/{name}")
            time.sleep(pause)
        if not main:
            continue                                # bez textu předpisu nemá záznam smysl
        documents[f"{SUBDIR}/{main}"] = {
            "nazev": short_name(entry.nazev), "druh": entry.druh, "cislo": entry.cislo,
            "vyhlaseno": entry.vyhlaseno, "ucinnost_od": entry.ucinnost_od,
            "zdroj": f"{BASE}/detail/{entry.detail_id}",
            "poznamka": _note(entry, detail.navaznosti),
            "navaznosti": relation_list(detail.navaznosti), "prilohy": attachments,
        }

    invalid = {file_stem(entry) for entry in entries if not is_valid(entry, today)}
    _set_aside_stale(target, expected, invalid - expected, unverified, report, today)
    _write_overview(out_dir, f"{BASE}/vydavatel/{ds_id}", previous, documents)
    return report


def _note_oddities(valid: list[Entry], today: str, report: SbirkaReport) -> None:
    """Co stažení neumí rozhodnout a má vidět člověk: víc platných předpisů stejného názvu (chatbot by odpovídal
    z obou) a předpis, který nabude účinnosti až za čas (chatbot z něj odpovídá hned, jen s upozorněním)."""
    by_name: dict[str, list[Entry]] = {}
    for entry in valid:
        # celý název, ne zkrácený jako u souboru – dva různé dlouhé názvy se stejným začátkem nejsou tentýž předpis
        by_name.setdefault(slug(short_name(entry.nazev), limit=10_000), []).append(entry)
    for same in by_name.values():
        if len(same) > 1:
            numbers = ", ".join(f"č. {entry.cislo}" for entry in same)
            title = short_name(same[0].nazev)
            title = title if len(title) <= 90 else title[:90].rstrip() + "…"
            report.problemy.append(f"Více platných předpisů má stejný název ({numbers}: {title}) – "
                                   "chatbot bude odpovídat ze všech; ověřte, že starší opravdu platí")
    for entry in valid:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry.ucinnost_od) and entry.ucinnost_od > today:
            year, month, day = (int(part) for part in entry.ucinnost_od.split("-"))
            report.problemy.append(f"{entry.druh} č. {entry.cislo}: nabývá účinnosti až {day}. {month}. {year} – "
                                   "chatbot z něj odpovídá už teď a u zdroje na to upozorní")


def _save(fetch: Fetch, url: str, target: Path, stem: str, link_name: str, report: SbirkaReport, what: str) -> str | None:
    """Stáhne jeden soubor a vrátí jeho název ve složce; None, když se nepovedlo nebo ho neumíme číst."""
    existing = sorted((p.name for p in target.glob(stem + ".*") if p.is_file() and p.suffix.lower() in KEPT_SUFFIXES),
                      key=lambda name: KEPT_SUFFIXES.index(Path(name).suffix.lower()))
    if existing:
        report.ponechano.append(existing[0])
        return existing[0]
    try:
        data = fetch(url)
    except SbirkaError as exc:
        report.problemy.append(f"{what}: soubor se nepodařilo stáhnout ({exc})")
        return None
    if len(data) > MAX_FILE_BYTES:
        report.problemy.append(f"{what}: soubor je příliš velký ({len(data) // 1_000_000} MB) – stáhněte ho ručně: {url}")
        return None
    extension = _extension(data, link_name)
    if extension is None:
        # říct i kam a pod jakým názvem převedený soubor patří – pod jiným názvem by ho další stažení nepoznalo
        report.problemy.append(f"{what}: {_unsupported_reason(data)}. Převedený soubor uložte jako "
                               f"{SUBDIR}/{stem}.docx (nebo .pdf) – originál: {url}")
        return None
    name = stem + extension
    (target / name).write_bytes(data)
    report.stazeno.append(name)
    return name


def _set_aside_by_hand(out_dir: Path, stem: str) -> str | None:
    """Leží soubor předpisu ve složce začínající podtržítkem (`sbirka/_neplatne/`, `_stranou/`)? Tam ho odložil
    člověk – souběžně platný starší předpis, dokument s osobními údaji. Vrací cestu složky od složky dokumentů."""
    for path in sorted(out_dir.rglob(stem + ".*")):
        relative = path.relative_to(out_dir)
        if path.is_file() and any(part.startswith("_") for part in relative.parts[:-1]):
            return relative.parent.as_posix()
    return None


def _set_aside_stale(target: Path, expected: set[str], invalid: set[str], unverified: set[str], report: SbirkaReport,
                     today: str) -> None:
    """Soubory předpisů, které Sbírka vede jako zrušené nebo vyřazené, přesune do `_neplatne/` (nemaže je).

    Odkládá se jen to, o čem seznam výslovně říká, že neplatí (`invalid`), a příloha, kterou platný předpis
    ve Sbírce už nemá. Soubor, který v seznamu chybí úplně, zůstává a ohlásí se: častěji než zrušení to znamená
    neúplný seznam (výpadek, změna stránkování). U předpisů z `unverified` (detail se nenačetl) zůstávají i přílohy.
    Porovnává se název bez přípony: ruční přepis platného předpisu do Markdownu (`….md` vedle PDF) zůstává.
    """
    unknown: list[str] = []
    for path in sorted(target.iterdir()):
        if not path.is_file() or path.suffix.lower() not in DOCUMENT_SUFFIXES or path.stem in expected:
            continue
        base = _ATTACHMENT_STEM.sub("", path.stem)  # „…-priloha-2“ patří ke svému předpisu
        if base in invalid or (base in expected and base not in unverified):
            _move_aside(path, target / STALE_DIR, today)
            report.odlozeno.append(path.name)
        elif base not in expected:
            unknown.append(path.name)
    if unknown:
        shown = ", ".join(unknown[:5]) + (f" a {len(unknown) - 5} dalších" if len(unknown) > 5 else "")
        report.problemy.append(
            f"Ve složce {SUBDIR}/ jsou soubory, které seznam ve Sbírce nezná ({shown}) – nechávám je být. Seznam mohl "
            f"přijít neúplný: zkuste stažení zopakovat. Pokud předpis opravdu neplatí, přesuňte soubor do "
            f"{SUBDIR}/{STALE_DIR}/ ručně.")


def _move_aside(path: Path, stale_dir: Path, today: str) -> None:
    """Přesune soubor do složky odložených. Soubor téhož jména, který tam už leží, nepřepíše – nový dostane datum."""
    stale_dir.mkdir(exist_ok=True)
    destination = stale_dir / path.name
    counter = 1
    while destination.exists():
        suffix = f"-{today}" + (f"-{counter}" if counter > 1 else "")
        destination = stale_dir / f"{path.stem}{suffix}{path.suffix}"
        counter += 1
    path.replace(destination)


def _read_overview(path: Path) -> dict:
    """Dosavadní přehled (i se značkou BOM, kterou přidávají některé editory). Chybí-li, je prázdný. Neplatný JSON
    je chyba: přepsáním by zmizely údaje, které k vlastním dokumentům doplnil člověk."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        raise SbirkaError(f"Soubor {path.name} není platný JSON ({exc}). Opravte ho, nebo ho přesuňte stranou – stažení by "
                          "ho přepsalo a ručně doplněné údaje by zmizely. Ve složce se nic nezměnilo.") from None
    return data if isinstance(data, dict) else {}


def _write_overview(out_dir: Path, source: str, previous: dict, documents: dict[str, dict]) -> None:
    """Zapíše přehled: nové záznamy předpisů ze Sbírky, k tomu dřívější záznamy všeho, co ve složce zůstalo
    (vlastní dokumenty; předpisy, jejichž detail se dnes nenačetl nebo které v seznamu chyběly). Záznam souboru,
    který ve složce už není (odložený předpis), se nepřenáší."""
    earlier = previous.get("dokumenty") if isinstance(previous.get("dokumenty"), dict) else {}
    kept = {key: value for key, value in earlier.items()
            if not str(key).startswith(SUBDIR + "/") or (out_dir / str(key)).is_file()}
    overview = {"zdroj": source, "stazeno": date.today().isoformat(), "dokumenty": {**kept, **documents}}
    text = json.dumps(overview, ensure_ascii=False, indent=2)
    (out_dir / "prehled.json").write_bytes((text + "\n").encode("utf-8"))   # write_bytes: konce řádků LF i na Windows
