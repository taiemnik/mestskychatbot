"""Složka s dokumenty → rozložené dokumenty pro index.

Co se čte: všechny soubory PDF, Word (.docx), Markdown, text a HTML ve složce `dokumenty/` i v jejích
podsložkách. Co se nečte: složky a soubory začínající podtržítkem nebo tečkou, soubor `README.md`
a `prehled.json`.

Tři pravidla, která stojí za zapamatování:
  - `prehled.json` (nepovinný) nese údaje o dokumentech: název, druh, číslo, účinnost, odkaz na originál,
    poznámku a přílohy. Zapisuje ho stažení ze Sbírky právních předpisů; doplnit ho může i člověk.
  - Přílohy se čtou jako součást svého dokumentu, ne jako samostatné dokumenty. Poznají se z přehledu,
    nebo podle názvu `<dokument>-priloha-1.pdf`.
  - Přepis má přednost: leží-li vedle `vyhlaska.pdf` soubor `vyhlaska.md`, čte se Markdown. Tak se
    opraví sken bez textu nebo PDF, které se přečetlo špatně – originál zůstává na místě.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from chatbot.ingest.extract import SUPPORTED, LabelsOnlyError, NoTextError, UnsupportedError, read_document
from chatbot.ingest.model import Block, DocMeta, ParsedDoc
from chatbot.ingest.structure import build_blocks

OVERVIEW = "prehled.json"
ATTACHMENT_HEADING_CHARS = 200  # delší první odstavec přílohy je její obsah, ne nadpis
_ATTACHMENT = re.compile(r"^(?P<main>.+)-priloha-(?P<n>\d{1,3})$")
_CZ_DATE = re.compile(r"^(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})$")
# jak se údaj může jmenovat v záhlaví Markdownu nebo v přehledu → pole dokumentu
_META_KEYS = {
    "nazev": "nazev", "název": "nazev", "title": "nazev",
    "druh": "druh", "typ": "druh",
    "cislo": "cislo", "číslo": "cislo",
    "ucinnost_od": "ucinnost_od", "účinnost od": "ucinnost_od", "ucinnost": "ucinnost_od", "účinnost": "ucinnost_od",
    "zdroj": "zdroj", "url": "zdroj", "odkaz": "zdroj",
    "poznamka": "poznamka", "poznámka": "poznamka",
}


@dataclass(frozen=True)
class Skipped:
    """Soubor, který se do chatbota nedostal, a proč."""
    path: str
    reason: str


def doc_id(relative_stem: str) -> int:
    """Stálé číslo dokumentu z cesty souboru bez přípony (nejvýš devět číslic). Nezmění se, dokud se soubor
    nepřejmenuje – drží na něm odkazy na odstavce. Přepis v Markdownu má stejné číslo jako originál."""
    return int(hashlib.sha1(relative_stem.encode("utf-8")).hexdigest()[:7], 16)


def load_overview(root: Path) -> dict[str, dict]:
    """Údaje o dokumentech z `prehled.json` (cesta souboru → údaje). Chybí-li soubor, je přehled prázdný."""
    path = Path(root) / OVERVIEW
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        raise ValueError(f"Soubor {OVERVIEW} není platný JSON: {exc}") from None
    documents = data.get("dokumenty") if isinstance(data, dict) else None
    return {str(key).replace("\\", "/"): value for key, value in (documents or {}).items() if isinstance(value, dict)}


def load_documents(root: Path) -> tuple[list[ParsedDoc], list[Skipped]]:
    """Přečte složku a vrátí (dokumenty, přeskočené soubory)."""
    root = Path(root)
    overview = load_overview(root)
    files, skipped = _files(root)
    by_stem: dict[str, list[str]] = {}
    for relative in files:
        by_stem.setdefault(relative.rsplit(".", 1)[0], []).append(relative)

    # přepis v Markdownu má přednost před originálem se stejným názvem
    chosen: dict[str, str] = {}
    transcripts: set[str] = set()
    for stem, candidates in by_stem.items():
        markdown = next((c for c in candidates if c.lower().endswith(".md")), None)
        chosen[stem] = markdown or sorted(candidates)[0]
        if markdown and len(candidates) > 1:
            transcripts.add(stem)

    attachments = _attachments(chosen, overview)
    attached = {stem for stems in attachments.values() for stem in stems}
    documents: list[ParsedDoc] = []
    seen_ids: dict[int, str] = {}
    for stem in sorted(chosen):
        if stem in attached:
            continue
        relative = chosen[stem]
        blocks, flags, file_meta, problem = _read(root / relative)
        if problem:
            skipped.append(Skipped(relative, problem))
            continue
        for order, attachment_stem in enumerate(attachments.get(stem, []), start=1):
            extra, extra_flags, _, problem = _read(root / chosen[attachment_stem])
            if problem:
                skipped.append(Skipped(chosen[attachment_stem], problem))
                continue
            blocks += _as_attachment(extra, order)
            # obrázky, mapy a nečitelné přílohy se hlásí u dokumentu, i když jsou v souboru přílohy
            flags += [flag for flag in extra_flags
                      if flag in ("obrazky", "popisky", "priloha-bez-textu") and flag not in flags]
        for number, block in enumerate(blocks):
            block.anchor = f"b{number}"
        meta = _meta(stem, relative, blocks, file_meta, _overview_entry(overview, by_stem[stem]))
        if meta.id in seen_ids:
            raise ValueError(f"Dva soubory mají stejné číslo dokumentu ({seen_ids[meta.id]} a {relative}) – jeden přejmenujte.")
        seen_ids[meta.id] = relative
        documents.append(ParsedDoc(meta=meta, blocks=blocks, flags=flags + (["prepis"] if stem in transcripts else [])))
    return documents, skipped


def _files(root: Path) -> tuple[list[str], list[Skipped]]:
    """Cesty čitelných souborů od kořene složky (s lomítky) a soubory přeskočené pro neznámý formát."""
    found: list[str] = []
    skipped: list[Skipped] = []
    if not root.is_dir():
        return found, skipped
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        parts = relative.split("/")
        if not path.is_file() or any(part.startswith(("_", ".")) for part in parts):
            continue
        if path.name.lower() in ("readme.md", OVERVIEW):
            continue
        if path.suffix.lower() in SUPPORTED:
            found.append(relative)
        else:
            skipped.append(Skipped(relative, "formát, který neumím číst (umím PDF, DOCX, Markdown, text a HTML)"))
    return found, skipped


def _attachments(chosen: dict[str, str], overview: dict[str, dict]) -> dict[str, list[str]]:
    """Dokument → jeho přílohy (názvy bez přípony, v pořadí). Zdroj: přehled, jinak název `…-priloha-N`."""
    result: dict[str, list[str]] = {}
    for key, entry in overview.items():
        main = key.rsplit(".", 1)[0]
        listed = [str(item).replace("\\", "/").rsplit(".", 1)[0] for item in entry.get("prilohy") or []]
        listed = [stem for stem in listed if stem in chosen]
        if main in chosen and listed:
            result[main] = listed
    known = {stem for stems in result.values() for stem in stems}
    for stem in sorted(chosen):
        match = _ATTACHMENT.match(stem)
        if not match or stem in known or match.group("main") not in chosen:
            continue
        result.setdefault(match.group("main"), []).append(stem)
    for main, stems in result.items():
        stems.sort(key=lambda s: int(_ATTACHMENT.match(s).group("n")) if _ATTACHMENT.match(s) else 0)
    return result


def _read(path: Path) -> tuple[list[Block], list[str], dict[str, str], str | None]:
    """Soubor → (bloky, příznaky, údaje ze záhlaví, důvod přeskočení). Jeden vadný soubor nesmí shodit ostatní."""
    try:
        extracted = read_document(path)
    except LabelsOnlyError:
        return [], [], {}, ("jsou v něm jen čísla a značky bez vět (nejspíš mapa nebo výkres) – nečte se; je-li to seznam "
                            "nebo tabulka, přepište ho do Markdownu a uložte vedle se stejným názvem a příponou .md")
    except NoTextError:
        return [], [], {}, ("není v něm čitelný text (nejspíš sken) – přepište ho do Markdownu a uložte vedle "
                            "se stejným názvem a příponou .md")
    except UnsupportedError as exc:
        return [], [], {}, str(exc)
    except Exception as exc:                        # noqa: BLE001 – poškozený soubor, heslo, neznámá vada
        return [], [], {}, f"soubor se nepodařilo přečíst ({type(exc).__name__})"
    blocks, flags = build_blocks(extracted.lines)
    flags = flags + [flag for flag in extracted.flags if flag not in flags]
    if not blocks:
        return [], [], {}, "po rozboru v něm nezbyl žádný text"
    return blocks, flags, extracted.meta, None


def _as_attachment(blocks: list[Block], order: int) -> list[Block]:
    """Bloky souboru přílohy připravené k připojení za dokument: začínají nadpisem přílohy."""
    blocks = [Block(kind="text", text=b.text.replace("\n", " "), page=b.page) if b.kind == "title" else b for b in blocks]
    if not blocks or blocks[0].kind != "priloha":
        first = blocks[0] if blocks else None
        if (first is not None and first.kind == "text" and first.text.lower().startswith("příloha")
                and len(first.text) <= ATTACHMENT_HEADING_CHARS):
            first.kind = "priloha"                  # nadpis přílohy, který rozbor nepoznal (jiný zápis)
        else:
            blocks.insert(0, Block(kind="priloha", text=f"Příloha č. {order}"))
    return blocks


def _overview_entry(overview: dict[str, dict], candidates: list[str]) -> dict:
    """Záznam přehledu pro dokument: hledá se pod kteroukoli příponou téhož názvu (přepis × originál)."""
    for relative in candidates:
        if relative in overview:
            return overview[relative]
    return {}


def _iso_date(value: str) -> str:
    match = _CZ_DATE.match(value.strip())
    if not match:
        return value.strip()
    day, month, year = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def _meta(stem: str, relative: str, blocks: list[Block], file_meta: dict[str, str], entry: dict) -> DocMeta:
    """Údaje o dokumentu: přehled má přednost před záhlavím souboru, to před odhadem z textu."""
    values: dict[str, str] = {}
    for source in (file_meta, entry):
        for key, value in source.items():
            field = _META_KEYS.get(str(key).strip().lower())
            if field and isinstance(value, (str, int, float)) and str(value).strip():
                values[field] = " ".join(str(value).split())
    title = next((block.text for block in blocks if block.kind == "title"), "")
    name = values.get("nazev") or " ".join(title.split()) or stem.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
    return DocMeta(id=doc_id(stem), soubor=relative, nazev=name[:300], druh=values.get("druh"), cislo=values.get("cislo"),
                   ucinnost_od=_iso_date(values["ucinnost_od"]) if values.get("ucinnost_od") else None,
                   zdroj=values.get("zdroj"), poznamka=values.get("poznamka"))
