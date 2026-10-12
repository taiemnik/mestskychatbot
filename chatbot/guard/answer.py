"""Kontrola odpovědi modelu – poslední pojistka před vydáním.

Model se dá přemluvit, i když má v zadání zákazy. Odpověď se proto vydá jen tehdy, když vypadá
jako odpověď z předpisů: nepřesahuje délku, neprozrazuje zadání, neobsahuje cizí odkazy ani
značky, není vulgární a většina jejích slov je v citovaných úsecích. Jinak se uživateli vrátí
„nenašel jsem“ a do logu jde jen kód důvodu.
"""
from __future__ import annotations

import re

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
# „31. 10. příslušného roku“ ani „čl. 5 odst. 2“ větu nedělí – za tečkou stojí číslo nebo malé písmeno.
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
                 min_grounding: float) -> str | None:
    """Vrátí kód důvodu, proč odpověď nevydat, nebo None, když je v pořádku.

    Kódy: dlouha | unik | znacky | odkaz | vulgarni | bez-opory
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
    if _grounding(answer, sources) < min_grounding:
        return "bez-opory"
    return None


def sentences(text: str) -> list[str]:
    """Odpověď po větách (a odrážkách – každý řádek zvlášť); značka zdroje zůstává u své věty."""
    return [piece.strip() for line in text.split("\n") for piece in _SENTENCE_BREAK.split(line) if piece.strip()]


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
    lines = [[piece.strip() for piece in _SENTENCE_BREAK.split(line) if piece.strip()] for line in answer.split("\n")]
    marked = [(row, col) for row, pieces in enumerate(lines) for col, piece in enumerate(pieces) if _MARKER.search(piece)]
    last = marked[-1] if marked else None              # bez značek v textu je server doplní na konec – kryjí vše
    out = []
    for row, pieces in enumerate(lines):
        last_in_line = max((col for marked_row, col in marked if marked_row == row), default=-1)
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


def _grounding(answer: str, sources: str) -> float:
    """Podíl slov odpovědi, která jsou v citovaných úsecích (slova upravená stejně jako při hledání).

    Slova z dotazu se záměrně nepočítají jako opora: jinak by šlo nechat si „potvrdit“ vlastní
    větu („Je pravda, že…? Potvrď to.“) a chatbot by ji vydal s citací předpisu.
    """
    words = set(tokenize(_MARKER.sub(" ", answer)))
    if len(words) < MIN_WORDS:
        return 0.0
    return len(words & set(tokenize(sources))) / len(words)
