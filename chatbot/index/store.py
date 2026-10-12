"""Soubory indexu: zápis a načtení.

Index tvoří čtyři soubory ve složce (`data/index/`):
  manifest.json    kdy a čím byl index sestaven, počty, rozměr embeddingů
  documents.json   dokumenty: označení, údaje a rozložený text pro zobrazení zdroje
  chunks.json      úseky pro hledání (text, cesta, kotva)
  embeddings.bin   embeddingy úseků ve stejném pořadí jako chunks.json (float16, po řádcích)

Žádná databáze: složka se nasazuje spolu s kódem (z repa nebo zkopírováním na server). Index je
odvozený – jde kdykoli smazat a sestavit znovu příkazem `python tasks.py build`.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from chatbot.index.model import Chunk

FILES = ("manifest.json", "documents.json", "chunks.json", "embeddings.bin")
DISK_DTYPE = "<f2"          # poloviční přesnost, little-endian: index má jednotky megabajtů


class IndexMismatchError(Exception):
    """Index chybí nebo si jeho soubory neodpovídají (jiný počet úseků a embeddingů apod.)."""


@dataclass
class Index:
    manifest: dict
    docs: dict[int, dict]       # číslo dokumentu → záznam z documents.json
    chunks: list[Chunk]
    vectors: np.ndarray         # (počet úseků, rozměr), float32, řádky normované na délku 1


def _write_json(path: Path, data, indent: int | None = None) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"))
    path.write_bytes((text + "\n").encode("utf-8"))      # write_bytes: konce řádků LF i na Windows


def write_index(out_dir: Path, docs: list[dict], chunks: list[Chunk], vectors: np.ndarray, manifest: dict) -> None:
    if len(chunks) != len(vectors):
        raise IndexMismatchError(f"Počet úseků ({len(chunks)}) a embeddingů ({len(vectors)}) se liší.")
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "manifest.json", manifest, indent=2)
    _write_json(out_dir / "documents.json", docs)
    _write_json(out_dir / "chunks.json", [asdict(c) for c in chunks])
    (out_dir / "embeddings.bin").write_bytes(np.ascontiguousarray(vectors, dtype=DISK_DTYPE).tobytes())


def load_index(index_dir: Path) -> Index:
    missing = [name for name in FILES if not (index_dir / name).is_file()]
    if missing:
        raise IndexMismatchError(f"Index chybí nebo není úplný ({index_dir}): chybí {', '.join(missing)}.")
    manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    docs = {int(d["id"]): d for d in json.loads((index_dir / "documents.json").read_text(encoding="utf-8"))}
    chunks = [Chunk(**item) for item in json.loads((index_dir / "chunks.json").read_text(encoding="utf-8"))]
    dim = int(manifest.get("embed_dim", 0))
    raw = np.frombuffer((index_dir / "embeddings.bin").read_bytes(), dtype=DISK_DTYPE)
    if dim <= 0 or raw.size != len(chunks) * dim or manifest.get("chunks") != len(chunks):
        raise IndexMismatchError(
            f"Soubory indexu si neodpovídají: úseků {len(chunks)}, v manifestu {manifest.get('chunks')}, "
            f"rozměr {dim}, hodnot v embeddings.bin {raw.size}.")
    return Index(manifest=manifest, docs=docs, chunks=chunks, vectors=raw.reshape(len(chunks), dim).astype(np.float32))
