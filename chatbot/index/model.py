"""Úsek – jednotka hledání. Typ je tady zvlášť, aby ho běžící aplikace mohla použít bez čtení dokumentů."""
from __future__ import annotations

import re
from dataclasses import dataclass

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
# věta, kterou do poznámky zapisuje stažení ze Sbírky (chatbot/ingest/sbirka.py, repeal_note) – znění musí sedět
_REPEAL_NOTE = re.compile(r"tento předpis je k (\d{1,2})\. (\d{1,2})\. (\d{4}) zrušen( předpisem č\. [^;]+)?; do té doby platí\.")


# věty, kterými stažení ze Sbírky zapisuje novelizaci (chatbot/ingest/sbirka.py, relation_note) – znění musí sedět
_AMENDED_BY = re.compile(r"byl změněn předpisem č\. (\d{1,3}/\d{4})")
_AMENDS = re.compile(r"mění předpis č\. (\d{1,3}/\d{4})")


def amended_by(meta: dict) -> list[str]:
    """Čísla novel, které tento předpis změnily (z poznámky v přehledu)."""
    return _AMENDED_BY.findall(str(meta.get("poznamka") or ""))


def amends(meta: dict) -> list[str]:
    """Čísla předpisů, které tento předpis jako novela mění."""
    return _AMENDS.findall(str(meta.get("poznamka") or ""))


# Dvě nejběžnější věty, kterými novela ruší celé ustanovení: „Článek 5 až 7 se zrušují.“ a „V čl. 3 se odstavec 2
# zrušuje.“ (i „V čl. 3 se zrušuje odstavec 2.“). U článku musí věta za slovem „zrušuje“ skončit: „V čl. 3 se zrušují
# slova …“ článek neruší – a chatbot by pak platné ustanovení prohlásil za zrušené.
_REPEALED_ARTICLES = re.compile(r"(?:článek|články|čl\.)\s+(\d{1,3}(?:\s*(?:,|a|až|–|-)\s*\d{1,3})*)\s+se\s+zrušuj[eí]\s*(?:[.;]|$)",
                                re.IGNORECASE | re.MULTILINE)
_REPEALED_PARAGRAPH = re.compile(r"v\s+(?:článku|čl\.)\s+(\d{1,3})\s+se\s+(?:odst(?:avec|\.)\s+(\d{1,3})\s+zrušuje"
                                 r"|zrušuje\s+odst(?:avec|\.)\s+(\d{1,3}))", re.IGNORECASE)
_LISTED = re.compile(r"(\d{1,3})(?:\s*(?:až|–|-)\s*(\d{1,3}))?")           # „3 až 5 a 8“ → 3–5 a 8
_PART = re.compile(r"^čl\. (\d{1,3})(?![0-9a-z])(?:.*?\bodst\. (\d{1,3})(?![0-9a-z]))?")     # „čl. 3a“ není článek 3


def repealed_parts(text: str) -> set[tuple[int, int | None]]:
    """Co novela výslovně ruší: dvojice (článek, odstavec), odstavec None = celý článek.

    Změnu znění („slova … se nahrazují slovy …“) tohle nepozná a nesnaží se o to – k takovému předpisu zůstává
    obecné upozornění, že ho změnila novela."""
    parts: set[tuple[int, int | None]] = set()
    for match in _REPEALED_ARTICLES.finditer(text):
        for first, last in _LISTED.findall(match.group(1)):
            parts.update((number, None) for number in range(int(first), int(last or first) + 1))
    parts.update((int(article), int(before or after)) for article, before, after in _REPEALED_PARAGRAPH.findall(text))
    return parts


def chunk_part(label: str) -> tuple[int | None, int | None]:
    """Článek a odstavec úseku z jeho označení („čl. 4 Sazba poplatku, odst. 1“ → (4, 1)); co článek není, (None, None)."""
    match = _PART.match(label)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2)) if match.group(2) else None


@dataclass
class Chunk:
    id: str                 # „<číslo dokumentu>:<kotva>“ – jedinečné v celém indexu
    doc_id: int
    path: list[str]         # celá cesta včetně označení dokumentu
    label: str              # citace uvnitř dokumentu: „čl. 3 Sazba poplatku, odst. 1“
    text: str
    anchor: str             # kotva bloku pro zobrazení (u děleného úseku s příponou -1, -2)


def embedding_text(chunk: Chunk) -> str:
    """Text, ze kterého se počítá embedding: cesta a pod ní text (odstavec se najde i podle názvu dokumentu)."""
    return " › ".join(chunk.path) + "\n" + chunk.text


def not_yet_effective(meta: dict, today: str) -> str:
    """Upozornění u předpisu, který ještě nenabyl účinnosti; jinak prázdný text. `today` je RRRR-MM-DD."""
    value = str(meta.get("ucinnost_od") or "")
    match = _ISO_DATE.match(value)
    if not match or value <= today:
        return ""
    year, month, day = (int(part) for part in match.groups())
    return f"Pozor: tento předpis nabývá účinnosti až {day}. {month}. {year}."


def repeal_passed(note: str, today: str) -> str:
    """Věta „je k D. M. RRRR zrušen…; do té doby platí.“ vzniká při stažení ze Sbírky. Ode dne zrušení už není
    pravda – přepíše se, aby chatbot u zdroje netvrdil opak, dokud správce nestáhne aktuální stav a nesestaví index."""
    def rewrite(match: re.Match) -> str:
        day, month, year = (int(part) for part in match.groups()[:3])
        if f"{year:04d}-{month:02d}-{day:02d}" > today:
            return match.group(0)
        return f"tento předpis byl k {day}. {month}. {year} zrušen{match.group(4) or ''} a už neplatí."
    return _REPEAL_NOTE.sub(rewrite, note)


def document_note(meta: dict, today: str) -> str:
    """Upozornění, které se ukazuje u zdroje a dostává ho model: uložená poznámka (novela, zrušení) a případně
    to, že předpis ještě není účinný. Počítá se při každém dotazu: po dni účinnosti upozornění samo zmizí,
    po dni zrušení se změní na „už neplatí“."""
    stored = repeal_passed(str(meta.get("poznamka") or ""), today)
    return " ".join(part for part in (stored, not_yet_effective(meta, today)) if part)
