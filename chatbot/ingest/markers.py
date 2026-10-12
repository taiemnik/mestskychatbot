"""Značky, podle kterých se v textu pozná členění: článek, část, příloha, odstavec, písmeno, odrážka, podpis.

Je to jediné místo s pravidly „jak vypadá nadpis článku“. Používá je skládání řádků z PDF (aby se
nový článek nepřilepil k předchozímu odstavci) i rozbor na bloky.
"""
from __future__ import annotations

import re

# „Čl. 3“, „Článek 3.“, „ČLÁNEK IV“ na začátku řádku, za ním může být název. Malé „čl. 4 odst. 2“ je odkaz
# uprostřed věty, který se při zalomení řádku ocitl na začátku – proto se vyžaduje velké Č.
# „ČI.“ (velké I místo malého l) vrací čtení skenů.
_ARTICLE = re.compile(r"^(?:Čl\.|ČL\.|ČI\.|Článek|ČLÁNEK)\s*(\d{1,3}[a-z]?|[IVXLC]{1,7})\b\.?\s*[:–-]?\s*(.*)$")
# „článek 3“ malým písmenem je nadpis jen tehdy, když stojí na řádku sám – jinak je to odkaz ve větě
# („článek 5.“ s tečkou je konec věty zalomený na vlastní řádek)
_ARTICLE_LOWER = re.compile(r"^článek\s+(\d{1,3}[a-z]?)$")
# paragraf jako nadpis: samotný („§ 3“) nebo s názvem („§ 3 Sazba poplatku“); „§ 84 odst. 2 zákona…“ je odkaz
_SECTION = re.compile(r"^§\s*(\d{1,4}[a-z]?)\b\s*(.*)$")
# odkaz, ne nadpis: „Čl. 4 odst. 2“, „Čl. 3 a 4“, „Čl. 5 této vyhlášky“. Jen malými písmeny a celá slova –
# „Odstraňování závad“, „Bodové hodnocení“ nebo „Nařízení odstranění stavby“ jsou názvy článků.
_REFERENCE_TAIL = re.compile(r"^(?:odst(?:avc\w*)?\b|písm(?:en\w*)?\b|bod(?:u|ě|em|y|ů|ech)?\b|a\s|až\s|nebo\s|,|"
                             r"této\b|tohoto\b|vyhlášky\b|nařízení\b|zákona\b)")
# věta o článku, ne jeho název: „Čl. 3 se mění takto:“, „Čl. 5 se zrušuje“, „Čl. 4 zní:“
_SENTENCE_TAIL = re.compile(r"^(?:se\b|zní\b)")
# číslo části: římské, arabské, písmeno nebo řadová číslovka. Jiné slovo za „Část“ je věta nebo místní
# název („Část Horní Lhota“, „Část poplatku lze uhradit…“, „Hlava rodiny“).
_ORDINAL = (r"(?i:první|druh[áýé]|třetí|čtvrt[áýé]|pát[áýé]|šest[áýé]|sedm[áýé]|osm[áýé]|devát[áýé]|desát[áýé]|"
            r"jedenáct[áýé]|dvanáct[áýé]|třináct[áýé]|čtrnáct[áýé]|patnáct[áýé])")
_PART = re.compile(r"^(ČÁST|Část|HLAVA|Hlava|ODDÍL|Oddíl|KAPITOLA|Kapitola)\s+"
                   rf"([IVXLC]{{1,7}}|\d{{1,3}}|[A-H]|{_ORDINAL})\b\.?\s*[:–-]?\s*(.{{0,120}})$")
# „Příloha č. 1“, „Příloha č. 2 – Ceník“, „Příloha č. 1 k vyhlášce…“, „Příloha č. 1, vyhlášky č. 4/2022 o…“
_APPENDIX = re.compile(r"^(?:Příloha|PŘÍLOHA)(?:\s+č\.?\s*(\d{1,3}))?(?:\s*[:–-]\s*.{0,160}|\s*,?\s+(?:k|ke)\s.{0,160}|"
                       r"\s*,?\s+(?:obecně závazné vyhlášky|vyhlášky|nařízení|směrnice|řádu|pravidel|zásad|usnesení)\b.{0,160}|"
                       r"\s+[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ].{0,160})?$")
_PARA_PAREN = re.compile(r"^\((\d{1,3})\)\s*(.*)$", re.S)             # „(1) text“
_MONTHS = "ledna|února|března|dubna|května|června|července|srpna|září|října|listopadu|prosince"
# „1. text“ – ne datum („1. 6. 2026“, „28. října“)
_PARA_DOT = re.compile(rf"^(\d{{1,3}})\.(?!\s*\d{{1,2}}\.)(?!\s+(?:{_MONTHS})\b)\s+(\S.*)$", re.S)
_FOOTNOTE = re.compile(r"^\d{1,2}/\s+\S")
_DATE_START = re.compile(rf"^\d{{1,2}}\.\s*(?:\d{{1,2}}\.|(?:{_MONTHS})\b)")
_PARA_BRACKET = re.compile(r"^(\d{1,3})\)\s+(\S.*)$", re.S)            # „1) text“
_LETTER = re.compile(r"^([a-z]{1,2})\)\s*(.*)$", re.S)                 # „a) text“
# odrážka: spojovník, pomlčky, puntíky, hvězdička a dva znaky písma Symbol, kterými odrážky kreslí Word
_BULLET = re.compile("^([-–—•▪◦\uf0b7\uf0a7*])\\s+(\\S.*)$", re.S)
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}

# podpisové řádky na konci předpisu: „v. r.“, funkce, tečkovaná čára, jméno s titulem
_SIGN_VR = re.compile(r"\bv\.\s?r\.", re.IGNORECASE)
_UPPER = "A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
_ROLE_WORD = (r"(?:\d\.\s*)?(?:(?:místo)?starost(?:a|ka)|zástupce\s+starost(?:y|ky)|primátor(?:ka)?|"
              r"náměst(?:ek|kyně)(?:\s+(?:primátor[ak]|hejtman[ak]))?|hejtman(?:ka)?|tajemn(?:ík|ice)|ředitel(?:ka)?|"
              r"předsed(?:a|kyně))(?:\s+(?:města|obce|městyse|kraje|městského úřadu|statutárního města))?")
# vlastní jméno obce za funkcí („města Horního Áíčkova“, „obce Horní Lhota“, „města Ústí nad Labem“) – tady se velikost písmen
# rozlišuje, aby se za podpis nevzala věta („Starosta města rozhoduje…“)
_PLACE = (rf"(?:\s+[{_UPPER}][^\s,.;:]*(?:\s+(?:nad|pod|u|v|ve|na|při)\s+[{_UPPER}][^\s,.;:]*|"
          rf"\s+[{_UPPER}][^\s,.;:]*){{0,3}})?")
_ROLE = rf"(?i:{_ROLE_WORD}){_PLACE}"
# řádek složený jen z funkcí: „starosta“, „starosta města místostarostka města“ (dva sloupce slité do řádku)
_SIGN_ROLE = re.compile(rf"^(?:{_ROLE}\s*)+$")
# doložka o zveřejnění na úřední desce pod podpisy
_SIGN_POSTED = re.compile(r"^(?:vyvěšeno|sejmuto|sňato)\b", re.IGNORECASE)
_SIGN_TITLE = re.compile(r"\b(?:Ing|Mgr|Bc|JUDr|PhDr|MUDr|RNDr|PaedDr|MVDr|MgA|BcA|doc|prof|DiS)\.")
# tituly před jménem i za ním – při posuzování, zda řádek tvoří jen jména s tituly, se odmyslí
_ANY_TITLE = re.compile(r"\b(?:Ing|Mgr|Bc|JUDr|PhDr|MUDr|RNDr|PaedDr|MVDr|MgA|BcA|doc|prof|DiS|CSc|DrSc|MBA|"
                        r"Ph\.\s?D|LL\.\s?M|arch|et)\b\.?")
_SIGN_DOTS = re.compile(r"^[.…_\-\s]{6,}$")
# samotné jméno (nebo dvě vedle sebe) bez titulu: „Jan Novák“ – podpisem je jen vedle řádku s funkcí.
# Písmena i z jiných abeced, která se v českých příjmeních vyskytují („Müllerová“, „Ľubomír“, „Wójcik“).
_NAME_WORD = (r"[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽÄÖÜËĽĹŔŚŹŻŁĆŃÔ]"
              r"[a-záčďéěíňóřšťúůýžäöüëïľĺŕśźżłćńôàâçèêîőűß]+")
# kdo dokument zpracoval nebo schválil – řádek pod textem, který k obsahu nepatří („Zpracovala Ing. Jana Nováková dne…“)
_SIGN_AUTHOR = re.compile(r"^(?:zpracoval|vypracoval|sestavil|připravil|schválil|za správnost)", re.IGNORECASE)
_SIGN_NAME = re.compile(rf"^{_NAME_WORD}(?:[\s-]+{_NAME_WORD}){{1,5}}$")
_SIGN_NAME_IN = re.compile(rf"{_NAME_WORD}\s+{_NAME_WORD}")           # jméno a příjmení kdekoli v řádku
# jméno bez titulu a funkce na jednom řádku: „Jan Novák místostarosta města Horního Áíčkova“
_SIGN_NAME_ROLE = re.compile(rf"^{_NAME_WORD}(?:[\s-]+{_NAME_WORD}){{1,3}}\s+(?:{_ROLE}\s*)+$")
_PAGE_NUMBER = re.compile(r"^(?:[-–]\s*)?(?:(?:strana|str\.|stránka)\s*)?\d{1,3}(?:\s*(?:/|z|ze|\(celkem)\s*\d{1,3}\)?)?(?:\s*[-–])?$",
                          re.IGNORECASE)


def roman_to_int(text: str) -> int | None:
    """„IV“ → 4; neplatný zápis vrací None."""
    total, previous = 0, 0
    for char in reversed(text.upper()):
        value = _ROMAN.get(char)
        if value is None:
            return None
        total += -value if value < previous else value
        previous = max(previous, value)
    return total or None


def article(text: str) -> tuple[str, str] | None:
    """Nadpis článku → (číslo, název na témže řádku). Číslo je arabsky („Článek IV.“ → „4“), u paragrafu „§ 3“."""
    section = _SECTION.match(text)
    if section:
        title = section.group(2).strip()
        # název paragrafu začíná velkým písmenem a není to věta; cokoli jiného je odkaz zalomený na začátek řádku
        if title and (not title[0].isupper() or _REFERENCE_TAIL.match(title) or len(title) > 160
                      or title.endswith((".", ";", ",", ":"))):
            return None
        return f"§ {section.group(1)}", title
    match = _ARTICLE.match(text)
    if not match:
        lower = _ARTICLE_LOWER.match(text)
        return (lower.group(1), "") if lower else None
    number, title = match.group(1), match.group(2).strip()
    if _REFERENCE_TAIL.match(title) or len(title) > 160 or title.endswith((".", ";")):
        return None                                 # odkaz na článek ve větě, ne nadpis
    if _SENTENCE_TAIL.match(title) or (title[:1].islower() and title.endswith(":")):
        return None                                 # věta o článku („Čl. 3 se mění takto:“)
    if not number[0].isdigit():
        value = roman_to_int(number)
        if value is None:
            return None
        number = str(value)
    return number, title


def part(text: str) -> str | None:
    """Nadpis části, hlavy nebo oddílu („ČÁST PRVNÍ“, „Hlava II Společná ustanovení“) → celý nadpis."""
    if text.endswith((".", ";", ",")) or not _PART.match(text):
        return None
    return text


def appendix(text: str) -> str | None:
    """Nadpis přílohy („Příloha č. 1“, „Příloha č. 2 k vyhlášce…“) → celý nadpis. Věta o příloze nadpis není."""
    if len(text) > 200 or text.endswith((".", ";", ",")) or not _APPENDIX.match(text):
        return None
    return text


def paragraph(text: str) -> tuple[str, str, str] | None:
    """Číslovaný odstavec → (druh značky, číslo, text). Druh: „(1)“, „1.“ nebo „1)“."""
    for kind, pattern in (("(1)", _PARA_PAREN), ("1.", _PARA_DOT), ("1)", _PARA_BRACKET)):
        match = pattern.match(text)
        if match:
            return kind, match.group(1), match.group(2).strip()
    return None


def footnote(text: str) -> bool:
    """Poznámka pod čarou zapsaná „1/ text“ – v PDF stojí na konci stránky, tedy i uprostřed výčtu."""
    return bool(_FOOTNOTE.match(text))


def starts_with_date(text: str) -> bool:
    """Začíná řádek datem („28. října“, „1. 6. 2026“)? Takový řádek bývá pokračováním věty, ne novým bodem."""
    return bool(_DATE_START.match(text))


def letter(text: str) -> tuple[str, str] | None:
    """Písmeno „a) text“ → (značka, text)."""
    match = _LETTER.match(text)
    return (f"{match.group(1)})", match.group(2).strip()) if match else None


def bullet(text: str) -> tuple[str, str] | None:
    """Odrážka → (značka, text). Značka se sjednocuje na pomlčku."""
    match = _BULLET.match(text)
    return ("–", match.group(2).strip()) if match else None


def starts_element(text: str) -> bool:
    """Začíná řádek nový prvek (článek, část, přílohu, odstavec, písmeno, odrážku)? Podle toho se v PDF
    a v prostém textu pozná, že řádek není pokračováním předchozího odstavce."""
    return any(check(text) for check in (article, part, appendix, paragraph, letter, bullet))


def is_signature(text: str) -> bool:
    """Vypadá řádek jako součást podpisů na konci předpisu? (jména s tituly, funkce i s názvem obce, „v. r.“,
    tečkovaná čára, doložka o vyvěšení na úřední desce)"""
    stripped = " ".join(text.split())
    if not stripped:
        return True
    if (_SIGN_DOTS.match(stripped) or _SIGN_VR.search(stripped) or _SIGN_ROLE.match(stripped)
            or _SIGN_NAME_ROLE.match(stripped)):
        return True
    if len(stripped) < 120 and _SIGN_POSTED.match(stripped):
        return True
    if len(stripped) < 120 and _SIGN_AUTHOR.match(stripped) and (_SIGN_TITLE.search(stripped) or _SIGN_NAME_IN.search(stripped)):
        return True
    return _titled_names(stripped)


def _titled_names(stripped: str) -> bool:
    """Řádek složený jen ze jmen s tituly, případně s funkcí („Mgr. Jana Nováková“, „Ing. Petr Dvořák, starosta“,
    dvě jména vedle sebe). Údaj, ve kterém jméno s titulem jen je („Velitel jednotky: Ing. Petr Dvořák,
    tel. 555 123 456“), podpis není – patří k obsahu."""
    if len(stripped) >= 90 or not _SIGN_TITLE.search(stripped):
        return False
    rest = re.sub(_ROLE, " ", _ANY_TITLE.sub(" ", stripped))
    rest = re.sub(r"[.…_]{2,}", " ", rest)          # tečkované čáry pro podpis slité z PDF do téhož řádku
    rest = " ".join(rest.replace(",", " ").split())
    # prázdný zbytek: za funkcí stálo jméno a vzalo se jako název obce („starosta Ing. Jan Novák“)
    return not rest or bool(re.fullmatch(rf"{_NAME_WORD}(?:[\s-]+{_NAME_WORD}){{1,7}}", rest))


def is_role_only(text: str) -> bool:
    """Řádek jen s funkcí („starosta“, „místostarostka města Horního Áíčkova“). Sám podpisem být nemusí – stejně vypadá
    výčet členů komise nebo název článku „Tajemník“; podpisem je až vedle jména nebo jiného podpisového řádku."""
    return bool(_SIGN_ROLE.match(" ".join(text.split())))


def is_signed(text: str) -> bool:
    """Řádek s „v. r.“ (vlastní rukou) – jistý znak podpisu, ať stojí v dokumentu kdekoli."""
    return bool(_SIGN_VR.search(text))


def is_bare_name(text: str) -> bool:
    """Samotné jméno a příjmení bez titulu a bez dalšího textu („Jan Novák“). Samo o sobě nic
    neznamená – podpisem je jen tehdy, když vedle něj stojí řádek s funkcí."""
    stripped = " ".join(text.split())
    return len(stripped) < 60 and bool(_SIGN_NAME.match(stripped))


def is_page_number(text: str) -> bool:
    """Samotné číslo strany („3“, „- 3 -“, „Strana 2 (celkem 5)“, „2/5“)."""
    return bool(_PAGE_NUMBER.match(text.strip()))
