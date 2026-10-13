"""Čísla v odpovědi – pojistka proti nejhorší chybě chatbota nad předpisy: jiné částce, lhůtě nebo datu.

Co odpověď tvrdí číslicí nebo názvem měsíce, musí být v citovaných úsecích. Na tvaru zápisu nezáleží:
„1 400 Kč“ = „1.400,- Kč“, „22:00“ = „dvacátá druhá hodina“, „31. března“ = „31. 3.“.

Obě strany se čtou jinak, a to záměrně:
  - odpověď přísně: jen číslice a měsíce (model smí napsat „jeden pes“, i když podklad říká „za psa“), skupiny
    číslic oddělené mezerou jako jedno číslo („1 000“);
  - podklad velkoryse: i čísla psaná slovy („patří“ se přečte jako pět) a řada čísel i po částech (tabulka slitá
    z PDF „100 150 200“ vypadá jako jedno číslo, telefon s předvolbou jako jiné číslo než bez ní).
Omyl velkorysým směrem znamená, že kontrola jedno číslo přehlédne; opačný by zahodil správnou odpověď.
"""
from __future__ import annotations

import re
import unicodedata

# značka zdroje má nejvýš tři číslice („[1]“, „[1, 2]“); „[1500] Kč“ je částka v závorkách, ne značka
_MARKER = re.compile(r"\[\d{1,3}(?:\s*[,;]\s*\d{1,3})*\]")
_LINE_NUMBER = re.compile(r"(?m)^[ \t]*(\d{1,2})[.)][ \t]+(?=\D)")    # číslování řádků výčtu: „1. “, „2) “
# hodiny: „22:00“ vždy, „22.00“ a „6,30“ jen před slovem hodin (jinak je to datum, článek nebo desetinné číslo)
_TIME = re.compile(r"(?<![\d.,:])(\d{1,2})(?::(\d{2})(?![\d:])|[.,](\d{2})(?=\s*(?:hod|h\b)))")
# tisíce oddělené mezerou nebo tečkou, případně s desetinnou částí: „1 400“, „2.500,-“, „12 500,50“
_GROUPED = re.compile(r"(?<![\d.,])(\d{1,3}(?:[ .  ]\d{3})+)(?:,(\d+))?(?!\d)")
_DECIMAL = re.compile(r"(?<!\d)(\d+),(\d+)(?!\d)")
_INTEGER = re.compile(r"\d+")
_ZERO = re.compile(r"(?<![\d.,:])0\s*(?:kc|%)")                      # nula je tvrzení jen s jednotkou („0 Kč“)
_WORD = re.compile(r"[a-z]+")
MAX_GROUPS = 8          # delší řada skupin číslic číslo není (tabulka, výpis) – čte se jen po částech

_MONTHS = {form: str(number) for number, forms in enumerate((
    "leden ledna lednu lednem", "unor unora unoru unorem", "brezen brezna breznu breznem", "duben dubna dubnu dubnem",
    "kveten kvetna kvetnu kvetnem", "cerven cervna cervnu cervnem", "cervenec cervence cervenci cervencem",
    "srpen srpna srpnu srpnem", "zari", "rijen rijna rijnu rijnem", "listopad listopadu listopadem",
    "prosinec prosince prosinci prosincem"), start=1) for form in forms.split()}

# začátky číslovek bez diakritiky (základní i řadové: „pět“, „pátého“, „patnáctého“); delší se zkouší dřív
_NUMERALS = sorted({
    "jedenact": 11, "dvanact": 12, "trinact": 13, "ctrnact": 14, "patnact": 15, "sestnact": 16, "sedmnact": 17,
    "osmnact": 18, "devatenact": 19, "dvacet": 20, "dvacat": 20, "tricet": 30, "tricat": 30, "ctyricet": 40,
    "ctyricat": 40, "padesat": 50, "sedesat": 60, "sedmdesat": 70, "osmdesat": 80, "devadesat": 90,
    "jedn": 1, "jeden": 1, "prvn": 1, "dva": 2, "dve": 2, "dvou": 2, "druh": 2, "tri": 3, "trem": 3, "trech": 3,
    "tret": 3, "ctyr": 4, "ctvrt": 4, "pet": 5, "pat": 5, "sest": 6, "sedm": 7, "osm": 8, "devet": 9, "devat": 9,
    "devit": 9, "deset": 10, "desat": 10,
}.items(), key=lambda item: -len(item[0]))
_HUNDRED = frozenset(("sto", "sta", "ste", "set", "stu", "stem"))
_THOUSAND = "tisic"
# slova, která číslovkou jen začínají („patří“, „jednání“, „třídit“, „dveře“)
_NOT_NUMERALS = ("patr", "jednan", "jednot", "trid", "dver", "petic", "osmo")


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _whole(digits: str) -> str:
    """Celé číslo v jednotném tvaru: jen číslice, bez úvodních nul. (Ne přes int() – řada tisíců číslic by ho shodila.)"""
    return re.sub(r"\D", "", digits).lstrip("0") or "0"


def _decimal(whole: str, fraction: str | None) -> str:
    """Desetinná část bez nul na konci („650,00“ → „650“)."""
    fraction = (fraction or "").rstrip("0")
    return f"{_whole(whole)},{fraction}" if fraction else _whole(whole)


def _grouped(match: re.Match, parts: bool) -> list[str]:
    """Skupiny číslic oddělené mezerou nebo tečkou. V odpovědi jedno číslo; v podkladu (`parts`) navíc každá
    souvislá část řady – co vypadá jako „2 100 150 200“, bývá sloupec tabulky, a telefon se píše i bez předvolby."""
    groups = re.split(r"\D", match.group(1))
    if not parts:
        return [_decimal(match.group(1), match.group(2))]
    found = [_decimal(match.group(1), match.group(2))] if len(groups) <= MAX_GROUPS else []
    for start in range(len(groups)):
        for stop in range(start + 1, min(len(groups), start + MAX_GROUPS) + 1):
            found.append(_whole("".join(groups[start:stop])))
    return found


def _digits(text: str, parts: bool = False) -> set[str]:
    """Čísla zapsaná číslicemi a měsíce zapsané slovem. `text` je bez diakritiky a malými písmeny."""
    found: set[str] = set(["0"] if _ZERO.search(text) else [])

    def take(values: list[str]) -> str:
        found.update(value for value in values if value != "0")
        return " "                                      # co je přečtené, se z textu vyjme, aby se nečetlo podruhé

    text = _TIME.sub(lambda m: take([_whole(part) for part in m.groups() if part]), text)      # minuty „00“ tvrzení nejsou
    text = _GROUPED.sub(lambda m: take(_grouped(m, parts)), text)
    text = _DECIMAL.sub(lambda m: take([_decimal(m.group(1), m.group(2))]), text)
    take([_whole(number) for number in _INTEGER.findall(text)])
    found.update(_MONTHS[word] for word in _WORD.findall(text) if word in _MONTHS)
    return found


def _numeral(word: str) -> int | None:
    if word.startswith(_NOT_NUMERALS):
        return None
    return next((value for start, value in _NUMERALS if word.startswith(start)), None)


def _words(text: str) -> set[str]:
    """Čísla psaná slovy. Vedle každé číslovky zvlášť i složená čísla: „dvacáté druhé“ → 22, „pět set“ → 500,
    „dva tisíce pět set“ → 2 500 (a cestou 2 000 a 500)."""
    found: set[int] = set()
    total = current = 0
    for word in _WORD.findall(text):
        value = _numeral(word)
        if word in _HUNDRED:
            current = (current or 1) * 100
        elif word.startswith(_THOUSAND):
            total, current = total + (current or 1) * 1000, 0
            found.add(total)
        elif value:
            current += value
            found.add(value)
        else:
            total = current = 0                         # jiné slovo složené číslo ukončuje
            continue
        found.add(total + current)
        found.add(current)
    return {str(value) for value in found if value}


def _unnumbered(answer: str) -> str:
    """Odpověď bez číslování řádků výčtu („1. “, „2) “) – ale jen když řádky tvoří řadu 1, 2, 3…
    Osamělé číslo na začátku řádku bývá den („30. dubna je poplatek splatný“), a to tvrzení je."""
    numbers = [int(number) for number in _LINE_NUMBER.findall(answer)]
    if len(numbers) < 2 or numbers != list(range(1, len(numbers) + 1)):
        return answer
    return _LINE_NUMBER.sub("", answer)


def answer_numbers(answer: str) -> set[str]:
    """Co odpověď tvrdí číslicí nebo měsícem. Značky zdrojů a číslování řádků tvrzení nejsou."""
    return _digits(_plain(_unnumbered(_MARKER.sub(" ", answer))))


def source_numbers(texts: list[str]) -> set[str]:
    """Všechna čísla, o která se odpověď smí opřít: číslice (řady i po částech), měsíce a čísla psaná slovy."""
    text = _plain("\n".join(texts))
    return _digits(text, parts=True) | _words(text)


def unsupported_numbers(answer: str, sources: list[str]) -> set[str]:
    """Čísla z odpovědi, která v podkladu nejsou."""
    return answer_numbers(answer) - source_numbers(sources)
