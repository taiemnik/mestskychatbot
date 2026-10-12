"""Úsek – jednotka hledání. Typ je tady zvlášť, aby ho běžící aplikace mohla použít bez čtení dokumentů."""
from __future__ import annotations

import re
from dataclasses import dataclass

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
# věta, kterou do poznámky zapisuje stažení ze Sbírky (chatbot/ingest/sbirka.py, repeal_note) – znění musí sedět
_REPEAL_NOTE = re.compile(r"tento předpis je k (\d{1,2})\. (\d{1,2})\. (\d{4}) zrušen( předpisem č\. [^;]+)?; do té doby platí\.")


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
