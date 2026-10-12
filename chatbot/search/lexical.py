"""Hledání podle slov (BM25).

Čeština se neskloňuje podle slovníku: slovo se převede na malá písmena bez diakritiky, ubere se
mu jedna běžná koncovka a zbytek se zkrátí na 6 znaků. „Zaměstnanec“, „zaměstnanci“ i
„zaměstnanců“ tak dají „zamest“ a „odměna“, „odměny“, „odměnou“ dají „odmen“.
Je to hrubé, ale bez knihoven a pro předpisy to stačí; přesnost dodává hledání podle významu.

Proč koncovky: měřeno bez modelu na sadě 25 otázek nad předpisy (správný odstavec mezi prvními
osmi nálezy) – samotné zkrácení na 6 znaků 21 z 25, s ubráním koncovky 24 z 25. Vyřazení běžných
slov („jak“, „kdo“) výsledek nezlepšilo.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

import numpy as np

from chatbot.search.rank import top_k

MIN_WORD = 2        # kratší kusy (a, v, §) nic neříkají
MAX_WORD = 6        # delší slova se zkracují – hrubé sjednocení tvarů
K1, B = 1.5, 0.75   # obvyklé hodnoty BM25: nasycení četnosti a vliv délky úseku
MIN_STEM = 3        # po ubrání koncovky musí zbýt aspoň tolik znaků
_SPLIT = re.compile(r"[^0-9a-z]+")
_REGULATION = re.compile(r"(?<![\d/])\d{1,3}/\d{4}(?![\d/])")      # číslo předpisu „5/2024“
# „čl. 3“, „článek 3“, „odst. 2“, „odstavec 2“ (po odstranění diakritiky)
_REFERENCE = re.compile(r"\b(cl|clanek|clanku|odst|odstavec|odstavce)\b\.?\s*(\d{1,3})\b")
_REFERENCE_KIND = {"cl": "cl", "od": "odst"}
# běžné koncovky skloňování (bez diakritiky), delší dřív; ubírá se nejvýš jedna
_ENDINGS = ("ami", "emi", "ech", "ich", "ych", "ymi", "imi", "ove", "ovi", "eho", "emu", "ymu", "ach",
            "ou", "um", "em", "am", "a", "e", "i", "o", "u", "y")


def _stem(word: str) -> str:
    """Ubere jednu běžnou koncovku („fondu“ → „fond“). Čísla a krátká slova nechává být."""
    if word.isdigit():
        return word
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM:
            return word[: -len(ending)]
    return word


def tokenize(text: str) -> list[str]:
    """Slova pro hledání. Navíc dva druhy odkazů jako jeden kus, protože se na ně lidé ptají přímo:
    číslo předpisu („5/2024“) a článek nebo odstavec s číslem („čl. 3“ → „cl3“, „odst. 2“ → „odst2“).
    Samotné jednociferné číslo slovem není – přidávalo šum."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    references = [f"{_REFERENCE_KIND[kind[:2]]}{number}" for kind, number in _REFERENCE.findall(plain)]
    words = [_stem(word)[:MAX_WORD] for word in _SPLIT.split(plain) if len(word) >= MIN_WORD]
    return _REGULATION.findall(plain) + references + words


class LexicalIndex:
    """Obrácený index nad texty: slovo → ve kterých textech je a kolikrát."""

    def __init__(self, texts: list[str]):
        self.n = len(texts)
        lengths = np.zeros(self.n, dtype=np.float32)
        postings: dict[str, tuple[list[int], list[int]]] = {}
        for i, text in enumerate(texts):
            counts = Counter(tokenize(text))
            lengths[i] = sum(counts.values())
            for term, tf in counts.items():
                docs, tfs = postings.setdefault(term, ([], []))
                docs.append(i)
                tfs.append(tf)
        average = float(lengths.mean()) if self.n else 1.0
        # část vzorce BM25 závislá jen na délce textu – spočítá se jednou
        self.length_norm = K1 * (1 - B + B * lengths / (average or 1.0))
        self.postings = {term: (np.array(docs, dtype=np.int32), np.array(tfs, dtype=np.float32))
                         for term, (docs, tfs) in postings.items()}

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        """Nejlepších `k` textů jako (pořadové číslo, skóre); texty bez jediného slova dotazu se nevracejí."""
        terms = set(tokenize(query))
        if not terms or not self.n:
            return []
        scores = np.zeros(self.n, dtype=np.float32)
        for term in terms:
            posting = self.postings.get(term)
            if posting is None:
                continue
            docs, tfs = posting
            idf = math.log(1 + (self.n - len(docs) + 0.5) / (len(docs) + 0.5))     # vzácné slovo váží víc
            scores[docs] += idf * tfs * (K1 + 1) / (tfs + self.length_norm[docs])
        return top_k(scores, k, positive_only=True)
