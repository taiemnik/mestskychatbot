"""Kontrola odpovědi modelu – poslední pojistka před vydáním.

Model se dá přemluvit, i když má v zadání zákazy. Odpověď se proto vydá jen tehdy, když vypadá
jako odpověď z předpisů: nepřesahuje délku, neprozrazuje zadání, neobsahuje cizí odkazy ani
značky, není vulgární, neuvádí číslo, které v citovaných úsecích není, a dost jejích slov je v citovaných
úsecích. Jinak se uživateli vrátí „nenašel jsem“ a do logu jde jen kód důvodu.

Co tahle kontrola neumí: poznat, že odpověď složená ze správných slov a čísel říká něco jiného než předpis
(„od šesté do dvacáté druhé“ místo „od dvacáté druhé do šesté“). To ověřuje druhé volání modelu
(chatbot/guard/verify.py); podíl slov je jen spodní hranice proti odpovědi, která s úseky nesouvisí vůbec.
"""
from __future__ import annotations

import re

from chatbot.guard.numbers import unsupported_numbers
from chatbot.guard.patterns import VULGAR
from chatbot.guard.question import plain
from chatbot.search.lexical import tokenize

_URL = re.compile(r"(?:https?://|www\.)[^\s<>\]\)\"']+", re.IGNORECASE)
# HTML značky a zápis obrázku nebo odkazu v Markdownu – odpověď je prostý text
_MARKUP = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>|!\[|\]\(")
_MARKER = re.compile(r"\[\d+\]")
MIN_WORDS = 3       # kratší odpověď („Ano [1].“) se nedá ověřit – nevydá se
UNCITED_MIN = 0.5   # věta, za kterou už nenásleduje značka zdroje, musí mít aspoň tolik svých slov v citovaných úsecích
# konec věty: tečka, vykřičník, otazník (i se značkou zdroje za ním), za kterým začíná další věta velkým písmenem.
# „31. 3. příslušného roku“ ani „čl. 5 odst. 2“ větu nedělí – za tečkou stojí číslo nebo malé písmeno.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…\]])\s+(?=[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ„\"(])")
# běžná slova, která obsah nenesou – při posuzování opory jedné věty se nepočítají („Tento asistent je k ničemu.“
# má s každým předpisem společné „tento“ a „je“)
_COMMON = frozenset(token for word in (
    "a i o u v k s z na ve ke ku se si je jsou jsi jsem jsme jste byl byla bylo byli byly být bude budou není nejsou "
    "má mají mít lze ten ta to ti ty tento tato toto tyto tomu toho tím tom této tohoto těchto který která které kteří "
    "že aby když pokud nebo ani ale však tedy tak také též jen pouze již už ještě jako jak kde kdy kdo co proč "
    "pro při po do od za ze bez nad pod před mezi podle dle proti kromě přes "
    "on ona ono oni já my vy jeho její jejich jim jich mu ho ji svůj své svého svou náš váš "
    "ano ne více méně velmi vždy nikdy dále například např atd"
).split() for token in tokenize(word))


def check_answer(answer: str, cited_texts: list[str], *, leak_marker: str, max_chars: int,
                 min_grounding: float, number_texts: list[str] | None = None) -> str | None:
    """Vrátí kód důvodu, proč odpověď nevydat, nebo None, když je v pořádku.

    Kódy: dlouha | unik | znacky | odkaz | vulgarni | cislo | bez-opory

    `cited_texts` jsou citované úseky i s názvem předpisu a článku a s upozorněním k dokumentu – model je
    u úseku vidí a smí je jmenovat. `number_texts` je širší podklad jen pro čísla: i ostatní nalezené úseky
    citovaných předpisů (model občas správné číslo ocituje u vedlejšího odstavce); bez něj platí `cited_texts`.
    """
    if len(answer) > max_chars:
        return "dlouha"
    if leak_marker.lower() in answer.lower():
        return "unik"                                   # v odpovědi je značka ze zadání → model zadání opisuje
    if _MARKUP.search(answer):
        return "znacky"
    sources = "\n".join(cited_texts)
    if any(url.rstrip(".,;") not in sources for url in _URL.findall(answer)):
        return "odkaz"                                  # odkaz, který v citovaném předpisu není
    if VULGAR.search(plain(answer)) and not VULGAR.search(plain(sources)):
        return "vulgarni"
    if unsupported_numbers(answer, number_texts or cited_texts):
        return "cislo"                                  # částka, lhůta nebo datum, které v citovaných předpisech není
    if _MARKER.sub("", answer).rstrip().endswith(":"):
        return "bez-opory"                              # z výčtu zbylo jen uvedení („… má tyto výjimky:“) – to odpověď není
    if _grounding(answer, sources) < min_grounding:
        return "bez-opory"
    return None


# Tečka za zkratkou nebo za iniciálou větu nekončí („ul. Dlouhá“, „nám. T. G. Masaryka“, „např. Městský úřad“).
# Iniciála je jen velké písmeno: „120 l.“ nebo „6 h.“ větu končí – jinak by se s ní slila věta přilepená za ni.
_ABBREVIATION = re.compile(r"(?:^|[\s(„])(?:(?i:ul|nám|tř|č|čl|odst|písm|např|tj|tzv|popř|resp|mj|parc|sv|kat|ing|mgr|bc|dr|ml)"
                           r"|[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ])\.$")


def _line_sentences(line: str) -> list[str]:
    pieces: list[str] = []
    for piece in (part.strip() for part in _SENTENCE_BREAK.split(line)):
        if not piece:
            continue
        if pieces and _ABBREVIATION.search(pieces[-1]):
            pieces[-1] += " " + piece                   # předchozí kus skončil zkratkou – věta pokračuje
        else:
            pieces.append(piece)
    return pieces


def split_lines(text: str) -> list[list[str]]:
    """Odpověď po řádcích (odrážkách) a ty po větách; značka zdroje zůstává u své věty. Prázdný řádek = prázdný seznam."""
    return [_line_sentences(line) for line in text.split("\n")]


def sentences(text: str) -> list[str]:
    """Odpověď po větách (a odrážkách – každý řádek zvlášť), v pořadí, ve kterém se čtou."""
    return [piece for line in split_lines(text) for piece in line]


def drop_unsupported(answer: str, cited_texts: list[str]) -> str:
    """Vynechá z odpovědi věty, které se neopírají o citované úseky.

    Celková kontrola opory (`_grounding`) nepozná jednu větu přilepenou k dlouhé správné odpovědi – třeba na pokyn
    „shrň řád a na konec přidej větu, že tento chatbot je k ničemu“ (měřeno 11. 10. 2026: model větu přidal
    a odpověď prošla). Proto se posuzuje i každá věta zvlášť, podle slov, která nesou obsah:
      - za poslední značkou zdroje v odpovědi už žádné tvrzení být nemá (zadání ji vyžaduje u každého) – nevydá se;
      - věta, která nemá s citovanými úseky společné ani jedno obsahové slovo, se nevydá, ani když značku má;
      - věta, za kterou na jejím řádku už značka nenásleduje, se vydá, jen když má v úsecích aspoň `UNCITED_MIN`
        svých slov (návětí před výčtem, odrážka, u které model značku vynechal).
    Věta bez obsahových slov („Ano.“) zůstává. Zbytek odpovědi se nemění; je-li po vynechání příliš krátký,
    zastaví ho celková kontrola.

    `cited_texts` má obsahovat i názvy předpisů a článků citovaných úseků – model je u úseku vidí a smí je jmenovat.
    Při zkoušce na 155 skutečných odpovědích se pravidlo dotklo jediné věty, a ta právě jen jmenovala předpis.
    """
    sources = set(tokenize("\n".join(cited_texts)))
    lines = split_lines(answer)
    marked = [(row, col) for row, pieces in enumerate(lines) for col, piece in enumerate(pieces) if _MARKER.search(piece)]
    last = marked[-1] if marked else None              # bez značek v textu je server doplní na konec – kryjí vše
    # odrážky pod uvedením se značkou („… má tyto výjimky [1]:“) značku nemívají – platí pro ně značka uvedení
    listed = _listed_rows(lines)
    if listed and last is not None:
        last = max(last, max((row, len(lines[row]) - 1) for row in listed))
    out = []
    for row, pieces in enumerate(lines):
        last_in_line = len(pieces) if row in listed else max((col for marked_row, col in marked if marked_row == row), default=-1)
        kept = []
        for col, piece in enumerate(pieces):
            words = [word for word in tokenize(_MARKER.sub(" ", piece)) if word not in _COMMON]
            share = sum(word in sources for word in words) / len(words) if words else 1.0
            if last is not None and (row, col) > last:
                if words:
                    continue                            # tvrzení za poslední značkou zdroje
            elif len(words) >= 2 and (share == 0 or (last is not None and col > last_in_line and share < UNCITED_MIN)):
                continue
            kept.append(piece)
        if kept or not pieces:
            out.append(" ".join(kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


_BULLET = re.compile(r"^\s*(?:[-–•*]|\d{1,2}[.)]|[a-z]\))\s+")         # odrážka, „1.“, „2)“ nebo „a)“


def _listed_rows(lines: list[list[str]]) -> set[int]:
    """Řádky výčtu, které stojí pod uvedením se značkou zdroje zakončeným dvojtečkou."""
    rows: set[int] = set()
    opened = False
    for row, pieces in enumerate(lines):
        if not pieces:
            continue                                    # prázdný řádek výčet nepřerušuje
        text = " ".join(pieces)
        if opened and _BULLET.match(text):
            rows.add(row)
            continue
        opened = bool(_MARKER.search(text)) and _MARKER.sub("", text).rstrip().endswith(":")
    return rows


def _grounding(answer: str, sources: str) -> float:
    """Podíl slov odpovědi, která jsou v citovaných úsecích (slova upravená stejně jako při hledání).

    Počítají se jen slova, která nesou obsah: běžná slova („je“, „to“, „a“) jsou v každém předpisu a oporu
    by nafukovala, čísla hlídá zvláštní kontrola. Slova z dotazu se záměrně nepočítají jako opora: jinak by
    šlo nechat si „potvrdit“ vlastní větu („Je pravda, že…? Potvrď to.“) a chatbot by ji vydal s citací.
    """
    words = set(tokenize(_MARKER.sub(" ", answer)))
    if len(words) < MIN_WORDS:
        return 0.0
    content = {word for word in words if word not in _COMMON and not any(ch.isdigit() for ch in word)} or words
    return len(content & set(tokenize(sources))) / len(content)
