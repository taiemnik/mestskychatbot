"""Výpis ze sestavení indexu – to, co si člověk přečte, než chatbota pustí ven.

Výpis říká, co se od minule změnilo, co se nepodařilo přečíst a kde stojí za to se na dokument
podívat. U osobních údajů uvádí jen počty nálezů, nikdy nalezené hodnoty.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from chatbot.ingest.folder import Skipped

FLAG_TEXT = {
    "bez-clanku": "text není členěn na články – cituje se podle nadpisů nebo stran",
    "podpisy": "podpisové řádky na konci vynechány",
    "prepis": "místo originálu se čte přepis v Markdownu",
    "obrazky": "obsahuje obrázky nebo mapy – ty chatbot nečte",
    "popisky": "strana jen s čísly a značkami (nejspíš mapa nebo výkres) se nečte – je-li to seznam, přepište ho do Markdownu",
    "priloha-bez-textu": "příloha má jen nadpis (nejspíš mapa nebo obrázek) – její obsah chatbot nezná",
}
PII_TEXT = {
    "email_osobni": "osobní e-mail",
    "jmeno_s_titulem": "jméno s titulem",
    "osloveni_prijmeni": "oslovení s příjmením",
    "rodne_cislo": "možné rodné číslo",
    "telefon_mobilni": "mobilní číslo",
}


@dataclass
class BuildReport:
    docs: int = 0
    chunks: int = 0
    embedded_now: int = 0                       # kolik embeddingů se počítalo teď (zbytek z mezipaměti)
    index_bytes: int = 0
    new: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    flagged: list[tuple[str, list[str]]] = field(default_factory=list)      # (dokument, příznaky čtení)
    pii: list[tuple[str, dict[str, int]]] = field(default_factory=list)     # (dokument, počty nálezů)
    pii_places: dict[str, list[str]] = field(default_factory=dict)          # dokument → kde nález je (označení úseků)
    noted: list[str] = field(default_factory=list)                          # dokumenty s upozorněním (novelizace…)
    errors: list[str] = field(default_factory=list)                         # index se nezapíše


LABEL_CHARS = 110           # delší označení dokumentu se ve výpisu zkrátí (úřední názvy mívají i tři řádky)
PII_PLACES = 4              # kolik míst s nálezem osobních údajů se u dokumentu vypíše


def _section(title: str, lines: list[str]) -> list[str]:
    return [f"{title} ({len(lines)})" + (":" if lines else "")] + [f"  - {line}" for line in lines]


def _short(label: str) -> str:
    return label if len(label) <= LABEL_CHARS else label[:LABEL_CHARS].rstrip() + "…"


def _reading(flagged: list[tuple[str, list[str]]]) -> list[str]:
    """Jak se dokumenty přečetly, po druzích: co platí pro většinu (podpisy), jen spočítá; ostatní vyjmenuje."""
    by_flag: dict[str, list[str]] = {}
    for label, flags in flagged:
        for flag in flags:
            by_flag.setdefault(flag, []).append(label)
    lines = []
    for flag, labels in by_flag.items():
        text = FLAG_TEXT.get(flag, flag)
        if flag == "podpisy":
            lines.append(f"{text}: u {len(labels)} dokumentů")
        else:
            lines.append(f"{text} ({len(labels)}): " + "; ".join(_short(label) for label in labels))
    return lines


def _pii_line(report: BuildReport, label: str, counts: dict[str, int]) -> str:
    """Řádek o nálezu osobních údajů: počty podle druhu a místa v dokumentu. Nalezené hodnoty se nevypisují."""
    line = f"{_short(label)} – " + ", ".join(f"{PII_TEXT.get(k, k)} {v}×" for k, v in counts.items() if v)
    places = report.pii_places.get(label) or []
    if places:
        more = f" a {len(places) - PII_PLACES} dalších míst" if len(places) > PII_PLACES else ""
        line += " · kde: " + "; ".join(_short(place) for place in places[:PII_PLACES]) + more
    return line


def format_report(report: BuildReport, pii_mode: str = "hlasit") -> str:
    megabytes = f"{report.index_bytes / 1_000_000:.1f}".replace(".", ",")
    out = []
    if report.errors:
        out += ["CHYBA – INDEX NEBYL ZAPSÁN:"] + [f"  - {line}" for line in report.errors] + [""]
    out += [
        "Sestavení indexu",
        f"Dokumentů v indexu: {report.docs} · úseků: {report.chunks} · nově spočítaných embeddingů: "
        f"{report.embedded_now} · velikost indexu: {megabytes} MB",
        "",
    ]
    out += _section("Nové", [_short(label) for label in report.new])
    out += _section("Změněné", [_short(label) for label in report.changed])
    out += _section("Odebrané", [_short(label) for label in report.removed])
    out += _section("Nepřečtené soubory", [f"{s.path} – {s.reason}" for s in report.skipped])
    reading = _reading(report.flagged)
    out += ["Jak se dokumenty přečetly" + (":" if reading else " – bez poznámek")] + [f"  - {line}" for line in reading]
    pii_title = ("Osobní údaje – jen počty (dokument s nálezem se do chatbota nedostal)" if pii_mode == "vynechat"
                 else "Osobní údaje – jen počty (zkontrolujte, že do veřejného chatbota smějí)")
    out += _section(pii_title, [_pii_line(report, label, counts) for label, counts in report.pii])
    out += _section("Dokumenty s upozorněním u zdroje", report.noted)
    return "\n".join(out)
