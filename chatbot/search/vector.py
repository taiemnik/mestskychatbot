"""Hledání podle významu: kosinová podobnost embeddingu dotazu a embeddingů úseků."""
from __future__ import annotations

import numpy as np

from chatbot.search.rank import top_k


def cosine(vectors: np.ndarray, query_vec: np.ndarray) -> np.ndarray:
    """Podobnost dotazu se všemi řádky. Řádky `vectors` jsou normované; dotaz se normuje tady."""
    query = np.asarray(query_vec, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(query))
    if len(vectors) == 0 or norm == 0.0:
        return np.zeros(len(vectors), dtype=np.float32)
    return vectors @ (query / norm)


def vector_search(vectors: np.ndarray, query_vec: np.ndarray, k: int) -> list[tuple[int, float]]:
    return top_k(cosine(vectors, query_vec), k)
