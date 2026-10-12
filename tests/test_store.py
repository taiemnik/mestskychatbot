"""Soubory indexu – zápis, načtení a kontrola, že si odpovídají."""
import json

import numpy as np
import pytest

from chatbot.index.chunks import make_chunks
from chatbot.index.store import IndexMismatchError, load_index, write_index
from chatbot.ingest.folder import load_documents
from chatbot.llm.provider import FakeProvider
from conftest import AICKOV

DOC_ID = 123456


def small_index(tmp_path):
    doc = next(d for d in load_documents(AICKOV)[0] if d.meta.cislo == "2/2024")
    doc.meta.id = DOC_ID
    chunks = make_chunks(doc)
    vectors = FakeProvider(dim=32).embed([c.text for c in chunks])
    manifest = {"built": "2026-10-10", "embed_model": "fake-bag-of-words", "embed_dim": 32,
                "docs": 1, "chunks": len(chunks), "content_hash": "abc"}
    write_index(tmp_path, [{"id": doc.meta.id, "label": doc.meta.label, "meta": {}, "hash": "h", "flags": [],
                            "blocks": []}], chunks, vectors, manifest)
    return chunks, vectors


def test_roundtrip_preserves_order_and_values(tmp_path):
    chunks, vectors = small_index(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["chunks.json", "documents.json", "embeddings.bin", "manifest.json"]
    index = load_index(tmp_path)
    assert [c.id for c in index.chunks] == [c.id for c in chunks]
    assert index.chunks[0] == chunks[0]                       # celý úsek včetně cesty a kotvy
    assert index.vectors.dtype == np.float32
    assert index.vectors.shape == vectors.shape
    assert np.abs(index.vectors - vectors).max() < 1e-3       # poloviční přesnost na disku
    assert index.manifest["embed_dim"] == 32
    assert list(index.docs) == [DOC_ID]                       # klíč je číslo dokumentu (int), ne text


def test_embeddings_stored_as_float16(tmp_path):
    chunks, _ = small_index(tmp_path)
    assert (tmp_path / "embeddings.bin").stat().st_size == len(chunks) * 32 * 2


def test_mismatch_raises_when_embeddings_truncated(tmp_path):
    small_index(tmp_path)
    path = tmp_path / "embeddings.bin"
    path.write_bytes(path.read_bytes()[:-10])
    with pytest.raises(IndexMismatchError):
        load_index(tmp_path)


def test_mismatch_raises_when_chunk_count_differs(tmp_path):
    small_index(tmp_path)
    path = tmp_path / "chunks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(data[:-1], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(IndexMismatchError):
        load_index(tmp_path)


def test_missing_index_raises_mismatch(tmp_path):
    with pytest.raises(IndexMismatchError, match="chybí"):
        load_index(tmp_path / "neexistuje")
