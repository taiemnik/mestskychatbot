"""Úseky pro hledání.

Úsek = odstavec i se svými písmeny a body (ty samostatně nedávají smysl), dále každá tabulka
a každý odstavec volného textu. Každý úsek nese cestu (dokument → příloha → část → článek → odstavec),
ze které vzniká citace u odpovědi. Číslo článku se uvádí jen tehdy, když ho má nadpis v dokumentu.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from chatbot.index.model import Chunk, embedding_text  # noqa: F401  (embedding_text se odsud importuje)
from chatbot.ingest.model import Block, ParsedDoc, SubItem

MAX_CHUNK_CHARS = 6000      # delší úsek se dělí po pododstavcích
MIN_TEXT_CHARS = 15         # kratší volný text (osamocené číslo poznámky apod.) úsekem není – viz _too_short
_WORD = re.compile(r"[^\W\d_]{3,}")


@dataclass
class _Position:
    """Kde v dokumentu právě jsme: příloha → část → článek."""
    priloha: str | None = None
    part: str | None = None
    article: str | None = None

    def enter(self, block: Block) -> bool:
        """Nadpis změní polohu. Vrací True, když blok byl nadpis."""
        if block.kind == "title":
            self.priloha, self.part, self.article = None, None, None
        elif block.kind == "priloha":
            self.priloha, self.part, self.article = block.text, None, None
        elif block.kind == "part":
            self.part, self.article = block.text, None
        elif block.kind == "article":
            self.article = _article_label(block)
        else:
            return False
        return True


def make_chunks(doc: ParsedDoc) -> list[Chunk]:
    doc_label = doc.meta.label
    structured = any(block.kind == "article" for block in doc.blocks)
    where = _Position()
    chunks: list[Chunk] = []
    last_place: tuple | None = None                 # oddíl (příloha, část, článek) naposledy přidaného úseku
    for block in doc.blocks:
        if where.enter(block):
            continue
        place = (where.priloha, where.part, where.article)
        tail, texts = _block_texts(block)
        if block.kind == "text" and _fragment(block.text):
            # Krátký text bez vlastního údaje („Vstup volný.“, „1 ks 20 Kč“) sám úsekem být nemá, ale je to obsah:
            # připojí se k předchozímu úseku téhož oddílu. Když v oddíle žádný není, je úsekem sám – článek
            # s jedinou krátkou větou nesmí zůstat bez úseku. Před prvním nadpisem jde o zbytek záhlaví a zahodí se.
            if chunks and last_place == place:
                chunks[-1].text = f"{chunks[-1].text}\n{block.text}"
                continue
            texts = [block.text] if any(place) else []
        path = [doc_label] + [p for p in place if p] + ([tail] if tail else [])
        label = _label(where, tail, structured, block.page)
        for i, text in enumerate(texts):
            anchor = block.anchor if len(texts) == 1 else f"{block.anchor}-{i + 1}"
            chunks.append(Chunk(id=f"{doc.meta.id}:{anchor}", doc_id=doc.meta.id,
                                path=path, label=label, text=text, anchor=anchor))
        if texts:
            last_place = place
    return chunks


def _block_texts(block: Block) -> tuple[str | None, list[str]]:
    """Konec citace („odst. N“, „tabulka“) a texty úseků jednoho bloku; prázdný seznam = blok úsekem není."""
    if block.kind == "para":
        # odstavec bez čísla (jen s písmeny) se cituje článkem
        return (f"odst. {block.number}" if block.number else None), _split_paragraph(block)
    if block.kind == "table":
        return "tabulka", _pack_lines("", _render_rows(block.rows))
    # upozornění není text dokumentu; příliš krátký volný text (osamocené číslo) také ne
    if block.kind == "note" or _too_short(block.text):
        return None, []
    return None, _pack_lines("", _hard_split(block.text))


def _too_short(text: str) -> bool:
    """Krátký volný text úsekem není (osamocené číslo poznámky, „viz výše“) – ledaže nese údaj:
    číslo i slovo („150 nebo 112“)."""
    if len(text) >= MIN_TEXT_CHARS:
        return False
    return not (any(ch.isdigit() for ch in text) and _WORD.search(text))


def _fragment(text: str) -> bool:
    """Krátký text, který nese slova, ale ne samostatný údaj („Vstup volný.“, „Zdarma.“, „1 ks 20 Kč“).
    Samotné číslo bez písmen (značka poznámky, zbytek čísla strany) sem nepatří – to se zahazuje."""
    return _too_short(text) and any(ch.isalpha() for ch in text)


def _article_label(block: Block) -> str:
    if block.number and block.number.startswith("§"):
        return f"{block.number} {block.text}".strip()
    if block.number:
        return f"čl. {block.number} {block.text}".strip()
    return f"„{block.text}“"                        # nadpis oddílu bez čísla


def _label(where: _Position, tail: str | None, structured: bool, page: int | None) -> str:
    """Citace uvnitř dokumentu. Část se uvádí jen tehdy, když chybí článek; bez nadpisů strana originálu."""
    if where.article:
        place = where.article
    elif where.part:
        place = f"„{where.part}“"
    elif where.priloha:
        place = None
    elif structured:
        place = "úvodní ustanovení"
    else:
        place = f"strana {page}" if page else "text dokumentu"
    return ", ".join(p for p in (where.priloha, place, tail) if p)


def _render_items(items: list[SubItem], level: int = 0) -> list[list[str]]:
    """Pododstavce jako skupiny řádků se značkou z originálu. Skupina = položka s celou podstrukturou."""
    groups = []
    for item in items:
        lines = ["   " * level + f"{item.marker} {item.text}".strip()]
        for sub_group in _render_items(item.children, level + 1):
            lines.extend(sub_group)
        groups.append(lines)
    return groups


def _render_rows(rows: list[list[str]]) -> list[str]:
    """Tabulka po řádcích: „záhlaví: hodnota; záhlaví: hodnota“. Tabulka o jednom řádku se jen spojí."""
    if len(rows) < 2:
        return [" | ".join(c for c in row if c) for row in rows]
    header = rows[0]
    lines = []
    for row in rows[1:]:
        cells = [f"{header[i]}: {value}" if i < len(header) and header[i] else value
                 for i, value in enumerate(row) if value]
        if cells:
            lines.append("; ".join(cells))
    return lines


def _split_paragraph(block: Block) -> list[str]:
    """Odstavec s pododstavci jako jeden text; nad limit se dělí po pododstavcích a každý díl nese návětí."""
    groups = ["\n".join(group) for group in _render_items(block.items)] + _render_rows(block.rows)
    return _pack_lines(block.text, groups)


def _pack_lines(head: str, units: list[str]) -> list[str]:
    """Skládá jednotky (řádky, skupiny) do dílů nepřesahujících limit; každý díl začíná návětím `head`."""
    pieces: list[str] = []
    current = [head] if head else []
    size = len(head)
    for unit in units:
        for part in _hard_split(unit, MAX_CHUNK_CHARS - len(head) - 1):
            if current and size + 1 + len(part) > MAX_CHUNK_CHARS and (len(current) > 1 or not head):
                pieces.append("\n".join(current))
                current, size = ([head] if head else []), len(head)
            current.append(part)
            size += 1 + len(part)
    if current and (len(current) > 1 or not pieces):
        pieces.append("\n".join(current))
    return [p for p in pieces if p.strip()]


def _hard_split(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Text delší než limit rozdělí na mezerách (nouzové dělení jedné příliš dlouhé jednotky)."""
    limit = max(limit, 500)
    parts = []
    while len(text) > limit:
        cut = text.rfind(" ", 0, limit)
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts
