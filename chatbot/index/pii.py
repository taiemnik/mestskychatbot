"""Kontrola osobních údajů při sestavení indexu.

Orientační sken všeho textu, který se o dokumentu ukládá (nadpisy, odstavce, tabulky, údaje
o dokumentu). Nález se vypíše ve výpisu ze sestavení – jen počty, nikdy nalezené hodnoty.
Podle nastavení (`chatbot.toml`, [osobni_udaje] rezim) se dokument s nálezem buď jen ohlásí,
nebo se do chatbota vůbec nedostane.

Je to síto, ne záruka: jméno bez titulu a bez oslovení regulární výraz nenajde. Kontrolu člověkem,
který ví, co smí ven, to nenahrazuje.
"""
from __future__ import annotations

import re

_UPPER = "A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
_LOWER = "a-záčďéěíňóřšťúůýž"

PATTERNS = {
    # osobní tvar adresy jmeno.prijmeni@…; funkční schránky (podatelna@…) se nepočítají
    "email_osobni": re.compile(r"\b[a-z]+\.[a-z]+@[a-z0-9.-]+\.[a-z]{2,}\b", re.IGNORECASE),
    "jmeno_s_titulem": re.compile(
        rf"\b(?:Ing|Mgr|Bc|JUDr|PhDr|MUDr|RNDr|PaedDr|MVDr|DiS)\.(?:\s*(?:et|arch\.))?\s+[{_UPPER}][{_LOWER}]+\s+[{_UPPER}][{_LOWER}]+"),
    "osloveni_prijmeni": re.compile(rf"\b(?:pan|paní|pana|panu|panem)\s+[{_UPPER}][{_LOWER}]{{2,}}"),
    # rodné číslo: RRMMDD/XXXX s platným měsícem (u žen +50) a dnem – ne číslo jednací
    "rodne_cislo": re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2]|5[1-9]|6[0-2])(?:0[1-9]|[12]\d|3[01])/\d{3,4}(?!\d)"),
    # mobilní číslo (začíná 6 nebo 7), s předvolbou i bez; pevné linky a částky v Kč se nepočítají
    "telefon_mobilni": re.compile(r"(?<![\d/])(?:\+420 ?)?[67]\d{2} ?\d{3} ?\d{3}(?!\d)(?! ?(?:Kč|,-|tis|mil))"),
}


def scan_pii(text: str) -> dict[str, int]:
    """Počty nálezů podle druhu. Nevrací nalezené hodnoty – výpis nesmí osobní údaje dál šířit."""
    return {name: len(pattern.findall(text)) for name, pattern in PATTERNS.items()}


def has_findings(counts: dict[str, int]) -> bool:
    return any(counts.values())
