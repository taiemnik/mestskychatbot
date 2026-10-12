"""Pomocné funkce pořadí: výběr nejlepších a sloučení dvou pořadí."""
from __future__ import annotations

import numpy as np

RRF_K = 60      # konstanta sloučení převrácenými pořadími (běžná hodnota; tlumí rozdíly na špici)


def top_k(scores: np.ndarray, k: int, positive_only: bool = False) -> list[tuple[int, float]]:
    """Nejlepších `k` položek jako (pořadové číslo, skóre), od nejlepší. Shodu rozhoduje nižší číslo."""
    if k <= 0 or scores.size == 0:
        return []
    k = min(k, scores.size)
    candidates = np.argpartition(-scores, k - 1)[:k]
    ordered = sorted(candidates.tolist(), key=lambda i: (-float(scores[i]), i))
    return [(i, float(scores[i])) for i in ordered if not positive_only or scores[i] > 0]


def rrf_merge(rankings: list[list[tuple[int, float]]]) -> list[tuple[int, float]]:
    """Sloučí několik pořadí: položka dostane za každé pořadí 1 / (RRF_K + místo). Skóre způsobů se nemíchají."""
    merged: dict[int, float] = {}
    for ranking in rankings:
        for place, (item, _) in enumerate(ranking, start=1):
            merged[item] = merged.get(item, 0.0) + 1.0 / (RRF_K + place)
    return sorted(merged.items(), key=lambda pair: (-pair[1], pair[0]))
