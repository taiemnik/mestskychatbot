"""Hledání pro odpověď: slova i význam, sloučené do jednoho pořadí.

Slova najdou přesné výrazy (číslo předpisu, „poplatek ze psů“), význam najde odstavec řečený jinak.
Každý způsob vrátí 30 nejlepších a pořadí se sloučí; `similarity` (kosinová podobnost) se u
každého nálezu uvádí zvlášť – podle ní se rozhoduje, jestli má smysl volat model.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from chatbot.index.model import Chunk, embedding_text
from chatbot.index.store import Index
from chatbot.search.lexical import LexicalIndex
from chatbot.search.rank import rrf_merge, top_k
from chatbot.search.vector import cosine

CANDIDATES = 30     # kolik nejlepších z každého způsobu jde do sloučení


@dataclass
class Hit:
    chunk: Chunk
    score: float            # skóre sloučeného pořadí (jen pro řazení)
    similarity: float       # kosinová podobnost úseku s dotazem, i když ho našla jen slova


class Searcher:
    def __init__(self, index: Index):
        self.index = index
        # slovní index nad cestou i textem úseku: úsek se najde i podle názvu dokumentu
        self.lexical = LexicalIndex([embedding_text(chunk) for chunk in index.chunks])

    def search(self, query: str, query_vec: np.ndarray, k: int = 8, semantic: int = 0) -> list[Hit]:
        """Nejlepších `k` úseků sloučeného pořadí; k nim navíc `semantic` nejlepších podle významu.

        Proč navíc: úsek, který na dotaz odpovídá jinými slovy („Do kdy zaplatit za psa?“ × „Poplatek je
        splatný do 31. března.“), bývá podle významu mezi prvními, ale slova s dotazem společná nemá. Ve
        sloučeném pořadí ho pak přeskočí úseky téhož předpisu, které mají jen běžná společná slova
        („poplatek“, „pes“). Model by odpověď nedostal a doplnil by ji z jiného předpisu. Výsledek má
        proto až `k + semantic` úseků.
        """
        if not query.strip() or not self.index.chunks:
            return []
        similarities = cosine(self.index.vectors, query_vec)
        rankings = [self.lexical.search(query, CANDIDATES)]
        # bez modelu je embedding dotazu nulový: pořadí podle významu by bylo náhodné, hledá se jen podle slov
        by_meaning = top_k(similarities, CANDIDATES) if np.any(np.asarray(query_vec)) else []
        if by_meaning:
            rankings.insert(0, by_meaning)
        merged = rrf_merge(rankings)
        scores = dict(merged)
        picked = [i for i, _ in merged[:k]]
        picked += [i for i, _ in by_meaning[:semantic] if i not in picked]
        return [Hit(chunk=self.index.chunks[i], score=scores.get(i, 0.0), similarity=float(similarities[i])) for i in picked]
