"""Ověření odpovědi druhým voláním modelu – jediná kontrola, která čte smysl, ne slova.

Kontrola slov a čísel (chatbot/guard/answer.py) nepozná odpověď složenou ze slov předpisu, která říká něco
jiného: „noční klid je od šesté do dvacáté druhé hodiny“ má všechna slova i čísla správně. Hotová odpověď se
proto rozdělí na věty a model u každé řekne, jestli to, co tvrdí, v citovaných úsecích opravdu stojí
(zadání: chatbot/answer/prompt.py, `verify_messages`). Tady je jen to, co s jeho verdiktem udělá server.

Zásada: co se neověřilo, se nevydá. Verdikt v jiném tvaru, než jaký byl smluven, je důvod odpověď zastavit.
"""
from __future__ import annotations

import re

from chatbot.guard.answer import split_lines


def read_verdict(raw: object, count: int) -> set[int]:
    """Pořadová čísla vět (od 0), které model označil za nepodložené.

    Smluvený tvar je {"vety": [true nebo false pro každou větu]}. Cokoli jiného – chybějící pole, jiný počet
    položek, „ano“ místo true – je ValueError: z poloviny přečteného verdiktu se nehádá.
    """
    verdicts = raw.get("vety") if isinstance(raw, dict) else None
    if not isinstance(verdicts, list) or len(verdicts) != count or any(type(item) is not bool for item in verdicts):
        raise ValueError("Verdikt ověření nemá smluvený tvar.")
    return {n for n, supported in enumerate(verdicts) if not supported}


def keep_supported(answer: str, unsupported: set[int]) -> str | None:
    """Odpověď bez nepodložených vět (číslovaných stejně jako `sentences`), nebo None, když se nemá vydat vůbec.

    Nevydá se, když neobstála víc než polovina vět: z toho, co zbude, už odpověď nebývá. Jinak se vydá zbytek –
    každá věta v něm je ověřená. Platí to i pro první větu: bývá to jen uvedení („Záleží na tom, kde bydlíte…“),
    které kontrolor odmítne, protože v citovaném úseku doslova není, a fakta jsou až za ním (měřeno 12. 10. 2026).
    Řádky odpovědi (odrážky) zůstávají, jak byly.
    """
    lines = split_lines(answer)
    count = sum(len(pieces) for pieces in lines)
    if len(unsupported) * 2 > count:
        return None
    out, n = [], 0
    for pieces in lines:
        kept = []
        for piece in pieces:
            if n not in unsupported:
                kept.append(piece)
            n += 1
        if kept or not pieces:
            out.append(" ".join(kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
