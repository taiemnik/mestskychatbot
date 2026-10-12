"""Soubor → řádky textu. Umí PDF, Word (.docx), Markdown, prostý text a HTML.

Každý formát se čte jinak, ven jde vždy totéž: seznam řádků (`Line`) s nápovědami, které jdou
z formátu zjistit (nadpis, vystředění, strana, tabulka), a údaje o dokumentu, pokud je soubor nese
(záhlaví Markdownu). Členění na články a odstavce z řádků dělá `structure.py`.

Dvě věci, na kterých se čtení předpisů nejčastěji pokazí, řeší tento modul:
  - Word čísluje odstavce automaticky a čísla v textu nejsou – dopočítají se z definic číslování;
  - PDF vrací řádky tak, jak jsou zalomené na stránce – složí se zpět do odstavců.

Knihovny pro PDF, Word a HTML se načítají až při čtení daného formátu. Běžící chatbot (hledání
a odpovědi) je nepotřebuje – čte jen hotový index.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from chatbot.ingest import markers
from chatbot.ingest.model import Line
from chatbot.ingest.structure import join_wrapped

SUPPORTED = (".pdf", ".docx", ".md", ".txt", ".html", ".htm")
MIN_TEXT_CHARS = 40             # méně textu v celém souboru = nejspíš sken bez textové vrstvy
_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_STYLE = re.compile(r"^(?:heading|nadpis)\s*(\d)$", re.IGNORECASE)
_TITLE_STYLE = re.compile(r"^(?:title|název|nazev)$", re.IGNORECASE)
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
_MD_META = re.compile(r"^\*\*([^*:]{2,40}):\*\*\s*(.*)$")        # „**Druh:** Směrnice“
_MD_FRONT = re.compile(r"^([A-Za-z_][\w-]{0,40}):\s*(.*)$")
_MD_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)(?=\S)(.+?)(?<=\S)\1")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_SPACED = re.compile(r"^(?:\S\s+){3,}\S$")                       # „M ě s t o   H o r n í   Á í č k o v“
_AUTONUM = re.compile(r"\bAUTONUM(?:LGL|OUT)?\b")                # pole Wordu, které samo čísluje odstavce
# řídicí znaky kromě tabulátoru a konce řádku – do textu dokumentu nepatří nikdy
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# text vrácený po dvojicích bajtů UTF-16: horní bajt 0x00–0x05 (latinka, řečtina, azbuka) a dolní bajt,
# nebo mezera s řídicím znakem (pomlčky a uvozovky U+2010 až U+201F)
_BYTE_PAIRS = re.compile(r"(?:[\x00-\x05][\x00-\x09\x0b-\xff]|\x20[\x10-\x1f])+")
# číslo strany zapsané tak, že to nic jiného být nemůže: „- 2 -“, „Strana 2“, „str. 2 (celkem 5)“
_PAGE_DECOR = re.compile(r"^\s*[-–]|[-–]\s*$|strana|str\.|stránka|celkem", re.IGNORECASE)
BYTE_PAIRS_MIN = 3              # tolik znaků NUL na stránce už není náhoda
LABELS_MIN_CHARS = 300          # stránka s méně znaky se jako „samé popisky“ neposuzuje
LABELS_LETTER_SHARE = 0.25      # podíl písmen, pod kterým stránku tvoří jen čísla a značky (mapa, výkres)


class NoTextError(Exception):
    """V souboru není čitelný text – typicky naskenované PDF bez textové vrstvy."""


class LabelsOnlyError(NoTextError):
    """V souboru jsou jen čísla a značky bez vět – typicky mapa nebo výkres, ze kterého jdou vytáhnout jen popisky."""


class UnsupportedError(Exception):
    """Formát souboru neumíme číst."""


@dataclass
class Extracted:
    lines: list[Line]
    meta: dict[str, str] = field(default_factory=dict)      # údaje ze záhlaví souboru (jen Markdown)
    # „obrazky“ = v souboru jsou obrázky nebo mapy, které se nečtou; „popisky“ = stránka PDF jen s čísly a značkami
    flags: list[str] = field(default_factory=list)


def read_document(path: Path) -> Extracted:
    """Přečte soubor podle přípony. Bez čitelného textu vyhodí `NoTextError` (u mapy `LabelsOnlyError`)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        lines, labels = _pdf(path)
        result = Extracted(lines, flags=["popisky"] if labels else [])
    elif suffix == ".docx":
        lines, has_images = _docx(path)
        result = Extracted(lines, flags=["obrazky"] if has_images else [])
    elif suffix == ".md":
        result = _markdown(path.read_text(encoding="utf-8-sig", errors="replace"))
    elif suffix == ".txt":
        result = Extracted(join_wrapped(_text_lines(path.read_text(encoding="utf-8-sig", errors="replace"))))
    elif suffix in (".html", ".htm"):
        result = Extracted(_html(path.read_text(encoding="utf-8-sig", errors="replace")))
    else:
        raise UnsupportedError(f"Formát {suffix or '(bez přípony)'} neumím číst.")
    chars = sum(len(line.text) for line in result.lines) + sum(len(cell) for line in result.lines
                                                              for row in (line.rows or []) for cell in row)
    if chars < MIN_TEXT_CHARS:
        raise (LabelsOnlyError if "popisky" in result.flags else NoTextError)(str(path))
    return result


def _squeeze(text: str) -> str:
    return " ".join(text.split())


# ------------------------------------------------------------------ PDF
def _pdf(path: Path) -> tuple[list[Line], bool]:
    """Text po řádcích v rozvržení stránky a to, zda některou stranu tvoří jen popisky (mapa, výkres).
    Režim „layout“ drží odsazení řádků – z něj se pozná vystředěný nadpis – a správně skládá písmena
    s háčky, která prostý režim u starších PDF trhá."""
    import logging

    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)      # drobné vady v souboru knihovna hlásí, ale text přečte
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")                      # PDF „chráněné“ prázdným heslem (zákaz úprav) jde číst
        except Exception:                           # noqa: BLE001 – cokoli jiného znamená, že text nepřečteme
            raise NoTextError(str(path)) from None
    pages: list[list[str]] = []
    labels = False
    for page in reader.pages:
        raw_lines = clean_page_text(_page_text(page)).split("\n")
        if labels_only(raw_lines):
            # mapa nebo výkres: popisky (čísla parcel, kóty) by jako text jen mátly. Zůstane jen nadpis přílohy,
            # aby bylo vidět, že příloha existuje a její obsah chatbot nezná.
            labels = True
            raw_lines = [raw for raw in raw_lines if markers.appendix(_squeeze(raw))]
        pages.append(raw_lines)
    return lines_from_pages(pages), labels


def _page_text(page) -> str:
    """Text stránky tak, jak ho vrátí knihovna. Když rozvržení selže nebo nic nevrátí, zkusí se prostý režim.
    Prostý režim se bere i u textu vráceného po dvojicích bajtů (znaky NUL): rozvržení v něm některé bajty
    nahrazuje mezerou, prostý režim je zachová a text jde převést zpět."""
    def extract(mode: str) -> str:
        try:
            return page.extract_text(extraction_mode=mode) or ""
        except Exception:                           # noqa: BLE001 – poškozená stránka nesmí shodit celé sestavení
            return ""

    text = extract("layout")
    if not text.strip() or text.count("\x00") >= BYTE_PAIRS_MIN:
        text = extract("plain") or text
    return text


def decode_byte_pairs(text: str) -> str:
    """Některá PDF (typicky mapy vyvedené z GIS) vracejí text po dvojicích bajtů UTF-16: „\\x00P\\x01Y\\x00í“
    místo „Pří“. Úseky takových dvojic převede zpět na znaky; ostatní text nechá být."""
    if text.count("\x00") < BYTE_PAIRS_MIN:
        return text
    return _BYTE_PAIRS.sub(lambda match: match.group(0).encode("latin-1").decode("utf-16-be", errors="replace"), text)


def clean_page_text(text: str) -> str:
    """Text stránky bez řídicích znaků: dvojice bajtů převede na znaky, co zbude, nahradí mezerou.
    Znak NUL ani jiný řídicí znak se do textu dokumentu nesmí dostat – úsek by nešel najít ani přečíst."""
    return _CONTROL.sub(" ", decode_byte_pairs(text))


def labels_only(raw_lines: list[str]) -> bool:
    """Stránka, na které skoro nejsou slova – jen čísla a značky. Tak se čte mapa nebo výkres: jdou z něj
    vytáhnout jen popisky. Seznam čísel (parcel) vypadá stejně; sestavení na takovou stránku upozorní."""
    chars = [ch for raw in raw_lines for ch in raw if not ch.isspace()]
    return len(chars) >= LABELS_MIN_CHARS and sum(ch.isalpha() for ch in chars) < LABELS_LETTER_SHARE * len(chars)


def lines_from_pages(pages: list[list[str]]) -> list[Line]:
    """Řádky stránek tak, jak je vrátilo PDF → složené odstavce.

    Záhlaví, zápatí a čísla stran se hledají jen na okrajích stránky (první dva a poslední dva řádky).
    Nadpis článku ani číslo uprostřed textu se za ně nepovažuje – jinak by předpis, jehož strany začínají
    „Čl. 3“, „Čl. 5“, „Čl. 10“, přišel o nadpisy článků.
    """
    repeated = _repeated_edges(pages)
    lines: list[Line] = []
    for number, raw_lines in enumerate(pages, start=1):
        filled_at = [i for i, raw in enumerate(raw_lines) if raw.strip()]
        if not filled_at:
            continue
        filled = [raw_lines[i] for i in filled_at]
        edges = set(filled_at[:2] + filled_at[-2:])
        outermost = {filled_at[0], filled_at[-1]}
        left = min(len(raw) - len(raw.lstrip()) for raw in filled)
        right = max(len(raw.rstrip()) for raw in filled)
        for i, raw in enumerate(raw_lines):
            text = _unspace(raw)
            if not text:
                lines.append(Line(blank=True, page=number))
                continue
            if i in edges and _page_furniture(text, number, i in outermost, repeated):
                continue
            lines.append(Line(text=text, page=number, center=_centered(raw, left, right)))
    return join_wrapped(lines)


def _page_furniture(text: str, page: int, outermost: bool, repeated: set[str]) -> bool:
    """Patří řádek z okraje stránky k její výzdobě (číslo strany, opakované záhlaví nebo zápatí), ne k textu?"""
    if markers.is_page_number(text):
        if _PAGE_DECOR.search(text):
            return True                             # „- 2 -“, „Strana 2 (celkem 5)“ – jinak než číslo strany se nečte
        # Samotné číslo nebo „2/5“ může být i obsah („100“ na začátku strany je pokračování tabulky). Číslem
        # strany je, když se takhle opakuje na většině stran, nebo když stojí na krajním řádku a sedí s pořadím strany.
        first = int(re.search(r"\d+", text).group())
        return _edge_key(text) in repeated or (outermost and abs(first - page) <= 2)
    return not markers.starts_element(text) and _edge_key(text) in repeated


def _centered(raw: str, left: int, right: int) -> bool:
    """Je řádek vystředěný? Mezera vlevo i vpravo je znatelná a přibližně stejná."""
    start = len(raw) - len(raw.lstrip())
    gap_left, gap_right = start - left, right - len(raw.rstrip())
    # první řádek odstavce bývá odsazený a o něco kratší – to vystředění není: řádek musí být znatelně užší než text
    narrow = len(raw.strip()) <= 0.8 * (right - left)
    return (narrow and gap_left >= 8 and gap_right >= 8
            and abs(gap_left - gap_right) <= max(6, 0.35 * max(gap_left, gap_right)))


def _unspace(raw: str) -> str:
    """Očistí řádek; prostrkaný text („M ě s t o   H o r n í   Á í č k o v“, „1 5 0   n e b o   1 1 2“) složí zpět do slov.
    Řada samotných čísel („1 2   3 4“) prostrkaný text není – slepením by vznikla jiná čísla."""
    stripped = raw.strip()
    if _SPACED.match(stripped) and re.search(r"\s{2,}", stripped) and any(ch.isalpha() for ch in stripped):
        return " ".join(word.replace(" ", "") for word in re.split(r"\s{2,}", stripped))
    letters = stripped.split()
    if len(letters) >= 5 and all(len(letter) == 1 and letter.isalpha() for letter in letters):
        return "".join(letters)                     # jedno prostrkané slovo: „N a ř í z e n í“
    return _squeeze(stripped)


def _edge_key(text: str) -> str:
    return re.sub(r"\d+", "#", text.strip().lower())


def _repeated_edges(pages: list[list[str]]) -> set[str]:
    """Záhlaví a zápatí: řádky, které se nahoře nebo dole opakují na většině stran.

    Řádek s číslem se za opakovaný bere jen tehdy, když je na všech stranách stejný („Vyhláška č. 5/2025“),
    nebo když se číslo mění spolu se stranou („Strana 2 z 5“). Řádky, které se liší jinými čísly
    („p. č. 123“, „p. č. 456“), jsou obsah – seznam, který náhodou vyšel na okraje stran.
    """
    if len(pages) < 3:
        return set()
    seen: dict[str, list[tuple[int, str]]] = {}
    for number, raw_lines in enumerate(pages, start=1):
        filled = [_squeeze(raw) for raw in raw_lines if raw.strip()]
        on_page: dict[str, str] = {}
        for text in filled[:2] + filled[-2:]:
            # značka článku nebo odstavce záhlavím není, i kdyby jí začínala každá strana; číslo strany „- 2 -“ ano
            if markers.starts_element(text) and not markers.is_page_number(text):
                continue
            on_page.setdefault(_edge_key(text), text)
        for key, text in on_page.items():
            seen.setdefault(key, []).append((number, text))
    needed = 0.6 * len(pages)
    repeated: set[str] = set()
    for key, found in seen.items():
        if len(found) < needed or len(key) >= 120:
            continue
        if "#" not in key or len({text for _, text in found}) == 1 or _counts_pages(found, needed):
            repeated.add(key)
    return repeated


def _counts_pages(found: list[tuple[int, str]], needed: float) -> bool:
    """Mění se některé číslo v řádku spolu se stranou (2, 3, 4 na stranách 2, 3, 4 – i s posunem o titulní list)?"""
    numbers = [(page, [int(digits) for digits in re.findall(r"\d+", text)]) for page, text in found]
    for position in range(min(len(values) for _, values in numbers)):
        offsets = Counter(values[position] - page for page, values in numbers)
        if offsets.most_common(1)[0][1] >= needed:
            return True
    return False


# ------------------------------------------------------------------ Word
def _docx(path: Path) -> tuple[list[Line], bool]:
    """Odstavce a tabulky v pořadí dokumentu (a zda jsou v dokumentu obrázky). Odstavci s automatickým
    číslováním se číslo doplní do textu."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    numbering = _Numbering(document)
    autonum = [0]                                   # počítadlo polí AUTONUM (číslo článku psané polem Wordu)
    lines: list[Line] = []
    for child in document.element.body.iterchildren():
        if child.tag == _WORD_NS + "p":
            paragraph = Paragraph(child, document)
            label = numbering.label(paragraph)
            style = _heading_style(paragraph)
            center = _docx_centered(paragraph)
            pieces = [_unspace(piece) for piece in _docx_text(paragraph, autonum).split("\n")]
            pieces = [piece for piece in pieces if piece]
            if label:
                pieces = [f"{label} {pieces[0]}".strip()] + pieces[1:] if pieces else [label]
            lines += [Line(text=piece, style=style, center=center) for piece in pieces]
        elif child.tag == _WORD_NS + "tbl":
            rows = _docx_rows(Table(child, document))
            if rows:
                lines.append(Line(rows=rows))
    # obrázek nebo mapa vložené do textu (novější i starší zápis Wordu) – obsah se nečte, jen se to ohlásí
    has_images = any(True for tag in ("drawing", "pict") for _ in document.element.body.iter(_WORD_NS + tag))
    return lines, has_images


def _docx_text(paragraph, autonum: list[int]) -> str:
    """Text odstavce. Pole AUTONUM („Čl. {AUTONUM}“) Word čísluje sám a číslo v souboru není –
    doplní se pořadím pole v dokumentu."""
    if not any(_AUTONUM.search(node.text or "") for node in paragraph._p.iter(_WORD_NS + "instrText")):
        return paragraph.text
    parts: list[str] = []
    for node in paragraph._p.iter():
        if node.tag == _WORD_NS + "t":
            parts.append(node.text or "")
        elif node.tag == _WORD_NS + "tab":
            parts.append("\t")
        elif node.tag in (_WORD_NS + "br", _WORD_NS + "cr"):
            parts.append("\n")
        elif node.tag == _WORD_NS + "instrText" and _AUTONUM.search(node.text or ""):
            autonum[0] += 1
            parts.append(str(autonum[0]))
    return "".join(parts)


def _docx_rows(table) -> list[list[str]]:
    rows = []
    for row in table.rows:
        cells: list[str] = []
        previous = None
        for cell in row.cells:
            if cell._tc is previous:                # sloučená buňka se v řádku opakuje – stačí jednou
                continue
            previous = cell._tc
            cells.append(_squeeze(cell.text))
        if any(cells):
            rows.append(cells)
    return rows


def _style_chain(paragraph):
    """Styl odstavce a styly, ze kterých dědí (nejvýš deset úrovní – pojistka proti zacyklení)."""
    style = paragraph.style
    for _ in range(10):
        if style is None:
            return
        yield style
        style = style.base_style


def _heading_style(paragraph) -> str | None:
    for style in _style_chain(paragraph):
        name = (style.name or "").strip()
        match = _HEADING_STYLE.match(name)
        if match:
            return f"h{match.group(1)}"
        if _TITLE_STYLE.match(name):
            return "h1"
    return None


def _docx_centered(paragraph) -> bool:
    if paragraph.alignment is not None:
        return int(paragraph.alignment) == 1
    for style in _style_chain(paragraph):
        alignment = style.paragraph_format.alignment
        if alignment is not None:
            return int(alignment) == 1
    return False


def _attr(element, name: str) -> str | None:
    return element.get(_WORD_NS + name) if element is not None else None


class _Numbering:
    """Automatické číslování Wordu: z definic v dokumentu dopočítá, jaké číslo odstavec nese.

    Word čísla do textu nezapisuje – u odstavce je jen odkaz na seznam (numId) a úroveň (ilvl).
    Seznam říká, čím úroveň začíná, jak se čísluje (1, a, I, odrážka) a jak se značka zapíše
    („(%1)“, „%1.“, „Čl. %1“). Počítadla se vedou pro každou definici zvlášť; hlubší úrovně se
    vynulují, když přijde položka vyšší úrovně.
    """

    def __init__(self, document):
        self.levels: dict[str, dict[int, tuple[int, str, str]]] = {}    # definice → úroveň → (start, formát, zápis)
        self.style_level: dict[tuple[str, str], int] = {}               # (definice, styl) → úroveň
        self.nums: dict[str, tuple[str, dict[int, int]]] = {}           # seznam → (definice, nové starty úrovní)
        self.counters: dict[str, dict[int, int]] = {}                   # definice → úroveň → poslední číslo
        self.started: set[tuple[str, int]] = set()                      # (seznam, úroveň), kde už se uplatnil nový start
        try:
            root = document.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            return                                                      # dokument žádné číslování nemá
        for abstract in root.findall(_WORD_NS + "abstractNum"):
            abstract_id = _attr(abstract, "abstractNumId") or ""
            levels: dict[int, tuple[int, str, str]] = {}
            for level in abstract.findall(_WORD_NS + "lvl"):
                index = int(_attr(level, "ilvl") or 0)
                start = int(_attr(level.find(_WORD_NS + "start"), "val") or 1)
                fmt = _attr(level.find(_WORD_NS + "numFmt"), "val") or "decimal"
                text = _attr(level.find(_WORD_NS + "lvlText"), "val") or ""
                levels[index] = (start, fmt, text)
                style = _attr(level.find(_WORD_NS + "pStyle"), "val")
                if style:
                    self.style_level[(abstract_id, style)] = index
            self.levels[abstract_id] = levels
        for num in root.findall(_WORD_NS + "num"):
            abstract_id = _attr(num.find(_WORD_NS + "abstractNumId"), "val") or ""
            overrides: dict[int, int] = {}
            for override in num.findall(_WORD_NS + "lvlOverride"):
                start = _attr(override.find(_WORD_NS + "startOverride"), "val")
                if start is not None:
                    overrides[int(_attr(override, "ilvl") or 0)] = int(start)
            self.nums[_attr(num, "numId") or ""] = (abstract_id, overrides)

    def _reference(self, paragraph) -> tuple[str, int] | None:
        """(seznam, úroveň) odstavce: z jeho vlastního nastavení, jinak ze stylu."""
        properties = paragraph._p.pPr
        direct = properties.numPr if properties is not None else None
        if direct is not None and direct.numId is not None:
            num_id = str(direct.numId.val)
            return None if num_id == "0" else (num_id, int(direct.ilvl.val) if direct.ilvl is not None else 0)
        for style in _style_chain(paragraph):
            style_properties = style.element.pPr
            via_style = style_properties.numPr if style_properties is not None else None
            if via_style is None or via_style.numId is None:
                continue
            num_id = str(via_style.numId.val)
            if num_id == "0":
                return None
            abstract_id = self.nums.get(num_id, ("", {}))[0]
            level = (int(via_style.ilvl.val) if via_style.ilvl is not None
                     else self.style_level.get((abstract_id, style.style_id), 0))
            return num_id, level
        return None

    def label(self, paragraph) -> str:
        """Značka, kterou Word u odstavce vykreslí („(2)“, „b)“, „Čl. 3“, „–“); prázdná, když číslovaný není."""
        reference = self._reference(paragraph)
        if reference is None:
            return ""
        num_id, level = reference
        abstract_id, overrides = self.nums.get(num_id, ("", {}))
        levels = self.levels.get(abstract_id)
        if not levels or level not in levels:
            return ""
        counters = self.counters.setdefault(abstract_id, {})
        # Seznam s vlastním začátkem („číslovat znovu od 1“): nový start platí pro úroveň, na které se seznam
        # poprvé použije. Word ho do souboru zapisuje pro všechny úrovně najednou – kdyby se uplatnil na všechny,
        # písmena pod odstavcem by vynulovala i čítač odstavců (odstavce 1, 2, 1 místo 1, 2, 3).
        if (num_id, level) not in self.started:
            self.started.add((num_id, level))
            if level in overrides:
                counters[level] = overrides[level] - 1
        counters[level] = counters.get(level, levels[level][0] - 1) + 1
        for deeper in [index for index in counters if index > level]:
            del counters[deeper]
        start, fmt, text = levels[level]
        if fmt == "bullet":
            return "–"
        if fmt == "none":
            return ""

        def render(match: re.Match) -> str:
            index = int(match.group(1)) - 1
            if index not in levels:
                return ""
            return _format_number(counters.get(index, levels[index][0]), levels[index][1])

        return re.sub(r"%(\d)", render, text).strip()


def _format_number(value: int, fmt: str) -> str:
    if fmt in ("lowerLetter", "upperLetter"):
        letters = ""
        number = max(value, 1)
        while number:
            number, rest = divmod(number - 1, 26)
            letters = chr(97 + rest) + letters
        return letters if fmt == "lowerLetter" else letters.upper()
    if fmt in ("lowerRoman", "upperRoman"):
        roman = ""
        number = max(value, 1)
        for amount, sign in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"),
                             (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
            while number >= amount:
                roman += sign
                number -= amount
        return roman if fmt == "lowerRoman" else roman.upper()
    return str(value)


# ------------------------------------------------------------------ Markdown a prostý text
def _text_lines(text: str) -> list[Line]:
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw.strip()
        lines.append(Line(text=_squeeze(stripped)) if stripped else Line(blank=True))
    return lines


def _plain_markdown(text: str) -> str:
    """Odstraní značky zvýraznění a z odkazu nechá jen jeho text."""
    text = _MD_LINK.sub(r"\1", text)
    for _ in range(3):                              # vnořené značky (**_text_**)
        text = _MD_EMPHASIS.sub(r"\2", text)
    return _squeeze(text.replace("\\", ""))


def _markdown(text: str) -> Extracted:
    """Markdown: nadpisy (#), seznamy, tabulky; údaje o dokumentu ze záhlaví mezi „---“ nebo z řádků
    „**Druh:** Směrnice“ na začátku. Komentáře <!-- … --> se nečtou."""
    text = re.sub(r"<!--.*?-->", "", text.replace("\r\n", "\n").replace("\r", "\n"), flags=re.S)
    raw_lines = text.split("\n")
    meta: dict[str, str] = {}
    start = 0
    if raw_lines and raw_lines[0].strip() == "---":
        for index in range(1, len(raw_lines)):
            if raw_lines[index].strip() == "---":
                start = index + 1
                break
            pair = _MD_FRONT.match(raw_lines[index].strip())
            if pair:
                meta[pair.group(1).strip().lower()] = pair.group(2).strip().strip("\"'")
    lines: list[Line] = []
    table: list[list[str]] = []
    heading_seen = False

    def flush_table() -> None:
        if table:
            lines.append(Line(rows=[row for row in table]))
            table.clear()

    for raw in raw_lines[start:]:
        stripped = raw.strip()
        if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
            cells = [_plain_markdown(cell) for cell in stripped.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")) for cell in cells if cell) or not any(cells):
                table.append(cells)
            continue
        flush_table()
        if not stripped or re.fullmatch(r"[-*_]{3,}", stripped):
            lines.append(Line(blank=True))
            continue
        pair = _MD_META.match(stripped) if not heading_seen else None
        if pair:                                    # „**Druh:** Směrnice“ nad prvním nadpisem = údaj o dokumentu
            meta[_squeeze(pair.group(1)).lower()] = _plain_markdown(pair.group(2))
            continue
        heading = _MD_HEADING.match(stripped)
        if heading:
            heading_seen = True
            lines.append(Line(text=_plain_markdown(heading.group(2)), style=f"h{len(heading.group(1))}"))
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        indent += 3 * raw[:indent].count("\t")      # tabulátor = čtyři mezery
        body = _plain_markdown(stripped)
        level = indent // 4 if markers.starts_element(body) else 0
        lines.append(Line(text=body, level=level))
    flush_table()
    return Extracted(join_wrapped(lines), meta)


# ------------------------------------------------------------------ HTML
def _html(text: str) -> list[Line]:
    """Nadpisy, odstavce, položky seznamů a tabulky z HTML stránky (skripty a styly se přeskočí)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(text, "html.parser")
    for junk in soup(["script", "style", "nav", "noscript", "template"]):
        junk.decompose()
    lines: list[Line] = []
    for node in (soup.body or soup).find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table"]):
        if node.name != "table" and node.find_parent("table") is not None:
            continue
        if node.name == "table":
            if node.find_parent("table") is not None:
                continue
            rows = [[_squeeze(cell.get_text(" ")) for cell in row.find_all(["th", "td"])] for row in node.find_all("tr")]
            rows = [row for row in rows if any(row)]
            if rows:
                lines.append(Line(rows=rows))
            continue
        if node.name == "li":
            own = _squeeze(" ".join(part for part in node.find_all(string=True, recursive=False))
                           + " " + " ".join(child.get_text(" ") for child in node.find_all(recursive=False)
                                            if child.name not in ("ul", "ol")))
            if not own:
                continue
            depth = len(node.find_parents(["ul", "ol"]))
            parent = node.find_parent(["ul", "ol"])
            if parent is not None and parent.name == "ol":
                position = len(node.find_previous_siblings("li")) + 1
                marker = f"{position}." if depth == 1 else f"{_format_number(position, 'lowerLetter')})"
            else:
                marker = "–"
            lines.append(Line(text=own if markers.starts_element(own) else f"{marker} {own}", level=max(0, depth - 1)))
            continue
        if node.name == "p" and node.find_parent("li") is not None:
            continue                                # text položky už vzal její <li>
        content = _squeeze(node.get_text(" "))
        if content:
            lines.append(Line(text=content, style=node.name if node.name.startswith("h") else None))
    return lines
