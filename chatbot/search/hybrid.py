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
from chatbot.search.lexical import LexicalIndex, references
from chatbot.search.rank import rrf_merge, top_k
from chatbot.search.vector import cosine

CANDIDATES = 30     # kolik nejlepších z každého způsobu jde do sloučení
# Kolikrát víc platí místo v pořadí podle významu než v pořadí podle slov. Lidé se ptají jinými slovy, než jaká
# jsou v předpisu („popelnice“ × „poplatek za obecní systém odpadového hospodářství“): slovní hledání je na takové
# otázky slabší a při stejné váze stahovalo správný úsek dolů. Slova zůstávají kvůli přesným výrazům (číslo
# předpisu, článek). Hodnota je změřená sadou otázek (python tasks.py check --hledani), ne odhadnutá: vyšší váha
# zlepšila první místa (první správný úsek u 67 % otázek místo 57 %), ale model dostal správný úsek méně často
# (93 % místo 98 %) – s vahou se sloučené pořadí kryje s osmi úseky, které se podle významu přidávají tak jako tak,
# a to, co našla jen slova, vypadne. Proto zůstává 1.
MEANING_WEIGHT = 1.0
# Dotaz, který jmenuje článek nebo odstavec („co říká čl. 4 vyhlášky 5/2024“): úseky, na které odkaz sedí celý, jdou
# do výsledku všechny – ale jen když jich je pár. „Čl. 4“ bez čísla předpisu sedí na každý předpis a nepřidá nic.
EXACT_MAX = 6


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
        by_words = self.lexical.search(query, CANDIDATES)
        # bez modelu je embedding dotazu nulový: pořadí podle významu by bylo náhodné, hledá se jen podle slov
        by_meaning = top_k(similarities, CANDIDATES) if np.any(np.asarray(query_vec)) else []
        merged = rrf_merge([by_meaning, by_words], [MEANING_WEIGHT, 1.0]) if by_meaning else rrf_merge([by_words])
        scores = dict(merged)
        picked = [i for i, _ in merged[:k]]
        picked += [i for i, _ in by_meaning[:semantic] if i not in picked]
        exact = self.lexical.with_all(references(query))
        if len(exact) <= EXACT_MAX:
            picked += [i for i in exact if i not in picked]
        return [Hit(chunk=self.index.chunks[i], score=scores.get(i, 0.0), similarity=float(similarities[i])) for i in picked]
