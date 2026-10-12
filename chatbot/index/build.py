"""Sestavení indexu: složka dokumentů → bloky → úseky → embeddingy → soubory indexu + výpis změn.

Volá se příkazem `python tasks.py build`. Embeddingy se počítají jen pro úseky, jejichž text se
změnil (mezipaměť podle otisku textu), takže běžná aktualizace stojí jen zlomek prvního sestavení.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path

import numpy as np

from chatbot.index.model import document_note
from chatbot.index.chunks import Chunk, embedding_text, make_chunks
from chatbot.index.pii import has_findings, scan_pii
from chatbot.index.report import BuildReport, format_report  # noqa: F401  (format_report se odsud importuje)
from chatbot.index.store import FILES, write_index
from chatbot.ingest.folder import Skipped, load_documents
from chatbot.ingest.model import ParsedDoc
from chatbot.llm.provider import LLMProvider


class EmbeddingCache:
    """Mezipaměť embeddingů na disku (jeden soubor pro každý model a rozměr)."""

    def __init__(self, path: Path, model: str, dim: int):
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{model}-{dim}")
        self.path = path.with_name(f"{path.stem}-{safe}.npz")
        self.items: dict[str, np.ndarray] = {}
        if self.path.is_file():
            data = np.load(self.path, allow_pickle=False)
            self.items = dict(zip(data["keys"].tolist(), data["vectors"]))

    def get(self, keys: list[str]) -> dict[str, np.ndarray]:
        return {k: self.items[k] for k in keys if k in self.items}

    def put(self, items: dict[str, np.ndarray]) -> None:
        self.items.update(items)

    def save(self) -> None:
        if not self.items:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        keys = list(self.items)
        np.savez(self.path, keys=np.array(keys), vectors=np.stack([self.items[k] for k in keys]).astype(np.float32))


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _previous(out_dir: Path) -> dict[int, tuple[str, str]]:
    """Dokumenty z minulého indexu: číslo → (otisk, označení). Slouží k výpisu změn."""
    path = out_dir / "documents.json"
    if not path.is_file():
        return {}
    try:
        return {int(d["id"]): (d.get("hash", ""), d.get("label", "")) for d in json.loads(path.read_text(encoding="utf-8"))}
    except (ValueError, KeyError, TypeError):
        return {}                                   # poškozený starý index: výpis změn prostě řekne „vše nové“


def build_index(documents_dir: Path, llm: LLMProvider, out_dir: Path, cache_path: Path, today: str | None = None,
                pii_mode: str = "hlasit") -> BuildReport:
    """Sestaví index ze složky dokumentů. `pii_mode`: „hlasit“ nálezy osobních údajů jen vypíše,
    „vynechat“ dokument s nálezem do indexu nepustí."""
    report = BuildReport()
    today = today or date.today().isoformat()
    documents, skipped = load_documents(documents_dir)
    report.skipped = list(skipped)
    previous = _previous(out_dir)
    records: list[dict] = []
    chunks: list[Chunk] = []
    for doc in documents:
        record, doc_chunks = _document(doc, pii_mode, report, today)
        if record:
            records.append(record)
            chunks.extend(doc_chunks)
    _diff(records, previous, report)
    report.docs, report.chunks = len(records), len(chunks)
    if not chunks:
        report.errors.append(f"Ve složce {documents_dir} není žádný čitelný dokument. Nahrajte do ní soubory "
                             "(PDF, DOCX, Markdown, text) nebo stáhněte předpisy obce: python tasks.py sbirka <ID datové schránky>")
        return report                               # prázdný index nezapisujeme

    vectors = _embed(chunks, llm, cache_path, report)
    manifest = {
        "built": today,
        "embed_model": llm.embed_model,
        "embed_dim": int(vectors.shape[1]),
        "docs": len(records),
        "chunks": len(chunks),
        "content_hash": _sha1("\n".join(sorted(r["hash"] for r in records))),
    }
    write_index(out_dir, records, chunks, vectors, manifest)
    report.index_bytes = sum((out_dir / name).stat().st_size for name in FILES)
    return report


def _document(doc: ParsedDoc, pii_mode: str, report: BuildReport, today: str) -> tuple[dict | None, list[Chunk]]:
    """Jeden dokument → záznam pro `documents.json` a jeho úseky. Dokument bez textu nebo (v režimu
    „vynechat“) s nálezem osobních údajů vrací None."""
    label = doc.meta.label
    doc_chunks = make_chunks(doc)
    if not doc_chunks:
        report.skipped.append(Skipped(doc.meta.soubor, "po rozboru v něm nezbyl žádný text"))
        return None, []
    counts = scan_pii(full_text(doc))
    if has_findings(counts):
        report.pii.append((label, counts))
        # kde v dokumentu nález je (označení úseku, nikdy hodnota) – ať člověk ví, kam se podívat
        places = list(dict.fromkeys(_pii_place(chunk, order) for order, chunk in enumerate(doc_chunks, start=1)
                                    if has_findings(scan_pii(f"{chunk.label}\n{chunk.text}"))))
        if places:
            report.pii_places[label] = places
        if pii_mode == "vynechat":
            report.skipped.append(Skipped(doc.meta.soubor, "osobní údaje (režim „vynechat“ v chatbot.toml)"))
            return None, []
    if doc.flags:
        report.flagged.append((label, list(doc.flags)))
    note = document_note(asdict(doc.meta), today)       # novela, zrušení k datu, předpis ještě není účinný
    if note:
        report.noted.append(f"{label} – {note}")
    blocks = [asdict(block) for block in doc.blocks]
    record = {"id": doc.meta.id, "label": label, "meta": asdict(doc.meta), "flags": list(doc.flags), "blocks": blocks,
              "hash": _sha1(json.dumps([asdict(doc.meta), blocks], ensure_ascii=False, sort_keys=True))}
    return record, doc_chunks


def _pii_place(chunk: Chunk, order: int) -> str:
    """Místo nálezu do výpisu. Označení úseku je text nadpisu z dokumentu – stojí-li osobní údaj přímo v nadpisu,
    vypsal by se s ním. Pak se místo označení uvede jen pořadí úseku."""
    if has_findings(scan_pii(chunk.label)):
        return f"{order}. úsek dokumentu (údaj je v nadpisu)"
    return chunk.label


def full_text(doc: ParsedDoc) -> str:
    """Všechen text, který se o dokumentu uloží a může ukázat: nadpisy, odstavce s pododstavci, tabulky
    i údaje o dokumentu. Kontrola osobních údajů musí vidět totéž, co uvidí uživatel."""
    def items(entries) -> list[str]:
        return [part for entry in entries for part in (entry.text, *items(entry.children))]

    parts = [str(value) for value in asdict(doc.meta).values() if isinstance(value, str)]
    for block in doc.blocks:
        parts += [block.text, *items(block.items), *(cell for row in block.rows for cell in row)]
    return "\n".join(parts)


def _diff(records: list[dict], previous: dict[int, tuple[str, str]], report: BuildReport) -> None:
    """Co se změnilo proti minulému indexu."""
    current = {r["id"]: r for r in records}
    report.new = sorted(r["label"] for i, r in current.items() if i not in previous)
    report.changed = sorted(r["label"] for i, r in current.items() if i in previous and previous[i][0] != r["hash"])
    report.removed = sorted(label for i, (_, label) in previous.items() if i not in current)


def _embed(chunks: list[Chunk], llm: LLMProvider, cache_path: Path, report: BuildReport) -> np.ndarray:
    """Embeddingy úseků: z mezipaměti, co jde; zbytek spočítá model."""
    texts = [embedding_text(c) for c in chunks]
    keys = [_sha1(f"{llm.embed_model}|{llm.embed_dim}|{t}") for t in texts]
    cache = EmbeddingCache(cache_path, llm.embed_model, llm.embed_dim)
    known = cache.get(keys)
    todo: dict[str, str] = {}
    for key, text in zip(keys, texts):
        if key not in known:
            todo.setdefault(key, text)
    if todo:
        vectors_new = llm.embed(list(todo.values()))
        known.update(dict(zip(todo.keys(), vectors_new)))
        cache.put(dict(zip(todo.keys(), vectors_new)))
        cache.save()
    report.embedded_now = len(todo)
    return np.stack([known[k] for k in keys]).astype(np.float32)
