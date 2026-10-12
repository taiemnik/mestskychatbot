"""Omezovače provozu: počet dotazů z jedné adresy za minutu a za den, pauza po opakovaných nevhodných dotazech,
hodinový strop.

Všechno je jen v paměti běžícího procesu – adresy se nikam nezapisují. Kde běží víc instancí
(víc procesů, hosting, který aplikaci spouští podle provozu), má každá vlastní počítadla, a když
instance skončí, počítadla se vynulují. Jde tam tedy o orientační brzdu; tvrdou pojistkou útraty
je měsíční strop nastavený u poskytovatele modelu.
"""
from __future__ import annotations

from collections import deque

MAX_KEYS = 5000         # nad tento počet sledovaných adres se nejstarší záznamy zahazují (ochrana paměti)


def _prune(table: dict, limit: int = MAX_KEYS) -> None:
    """Když tabulka přeroste, zahodí nejstarší polovinu klíčů (slovník drží pořadí vložení)."""
    if len(table) > limit:
        for key in list(table)[: len(table) // 2]:
            del table[key]


class RateLimiter:
    """Nejvýš `per_minute` dotazů z jedné adresy za posledních 60 sekund."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = {}

    def allow(self, key: str, now: float) -> bool:
        times = self.hits.setdefault(key, deque())
        while times and now - times[0] >= 60.0:
            times.popleft()
        if len(times) >= self.per_minute:
            return False
        times.append(now)
        _prune(self.hits)
        return True


class DailyLimiter:
    """Nejvýš `limit` placených dotazů z jedné adresy za posledních 24 hodin – pro veřejnou ukázku s malým rozpočtem.

    Zjištění (`used`) a zápis (`add`) jsou zvlášť: dotaz, který pak zastaví hodinový strop, se adrese nepřičte.
    """

    WINDOW_S = 86400.0

    def __init__(self, limit: int):
        self.limit = limit
        self.hits: dict[str, deque[float]] = {}

    def used(self, key: str, now: float) -> int:
        times = self.hits.get(key)
        if not times:
            return 0
        while times and now - times[0] >= self.WINDOW_S:
            times.popleft()
        return len(times)

    def add(self, key: str, now: float) -> None:
        self.hits.setdefault(key, deque()).append(now)
        _prune(self.hits)


class AbuseTracker:
    """Pauza pro prohlížeč (adresa + značka prohlížeče), ze kterého opakovaně chodí pokusy o obcházení nebo
    vulgarity. Překlep, nesmysl ani prosba o vtip se nepočítají.

    `strikes` takových dotazů během `window_s` sekund → `block_s` sekund se mu neodpovídá.
    """

    def __init__(self, strikes: int, window_s: float, block_s: float):
        self.strikes, self.window_s, self.block_s = strikes, window_s, block_s
        self.marks: dict[str, deque[float]] = {}
        self.until: dict[str, float] = {}

    def strike(self, key: str, now: float) -> None:
        times = self.marks.setdefault(key, deque())
        while times and now - times[0] >= self.window_s:
            times.popleft()
        times.append(now)
        if len(times) >= self.strikes:
            self.until[key] = now + self.block_s
            times.clear()
        _prune(self.marks)
        _prune(self.until)

    def blocked(self, key: str, now: float) -> bool:
        until = self.until.get(key)
        if until is None:
            return False
        if now >= until:
            del self.until[key]
            return False
        return True


class HourlyCap:
    """Strop dotazů za hodinu ze všech adres dohromady – brzda pro případ, že útočník střídá adresy."""

    def __init__(self, limit: int):
        self.limit = limit
        self.times: deque[float] = deque()

    def allow(self, now: float) -> bool:
        while self.times and now - self.times[0] >= 3600.0:
            self.times.popleft()
        if len(self.times) >= self.limit:
            return False
        self.times.append(now)
        return True
