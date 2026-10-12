"""Zpětná vazba k odpovědím: palec nahoru / dolů, důvod a poznámka od uživatele.

Smysl: správce chatbota se má dozvědět, co lidem pomohlo a co ne. Záznam nese dotaz, odpověď, citované
úseky a hodnocení – bez jména a bez adresy tazatele. Záznamy se čtou ručně (soubor JSONL) a podle nich
se upravuje zadání pro model, zkušební sada otázek nebo samotné dokumenty.

Úložiště je za rozhraním `FeedbackStore`, aby šlo vyměnit bez zásahu do API:
  - `FileStore`  – řádek JSON do souboru (`data/feedback.jsonl`); místně a na vlastním serveru
  - `LogStore`   – jen řádek do logu bez textů; náhrada tam, kde se na disk psát nedá. Disk, na který se
                   psát dá, ale při restartu se maže, se nepozná – soubor má ležet na trvalém místě (FEEDBACK_FILE)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from chatbot.guard.question import normalize

log = logging.getLogger("chatbot")

VOTES = ("up", "down")
# důvody u palce dolů; kód jde do záznamu, text zobrazuje stránka
REASONS = ("spatny-predpis", "neuplne", "nesrozumitelne", "melo-se-najit", "jine")
_SOURCE_REF = re.compile(r"^\d{1,9}:[A-Za-z0-9_-]{1,32}$")      # „1001:b12“ – dokument a kotva úseku
MAX_SOURCES = 20
MAX_QUESTION = 500
MAX_ANSWER = 4000


@dataclass(frozen=True)
class Feedback:
    vote: str                       # up | down
    reason: str                     # jeden z REASONS, u palce nahoru prázdné
    comment: str                    # volitelná poznámka uživatele
    question: str
    answer: str
    sources: list[str] = field(default_factory=list)    # „doc_id:anchor“ citovaných úseků
    found: bool = True              # odpověď byla vydána (False = „nenašel jsem“)
    mode: str = "ostry"
    index_date: str | None = None
    ts: str = ""

    @classmethod
    def from_payload(cls, data: object, *, max_comment_chars: int, mode: str, index_date: str | None) -> "Feedback":
        """Sestaví záznam z těla požadavku. Co nesedí, je `ValueError` s českou hláškou pro uživatele."""
        if not isinstance(data, dict):
            raise ValueError("Neplatné hodnocení.")
        vote = data.get("vote")
        if vote not in VOTES:
            raise ValueError("Neplatné hodnocení.")
        reason = data.get("reason") or ""
        if reason and (vote != "down" or reason not in REASONS):
            raise ValueError("Neplatný důvod hodnocení.")
        comment = _text(data.get("comment"))
        if len(comment) > max_comment_chars:
            raise ValueError(f"Poznámka je příliš dlouhá (nejvýš {max_comment_chars} znaků).")
        question = _text(data.get("question"))
        answer = _text(data.get("answer"))
        if not question:
            raise ValueError("Hodnocení bez dotazu nejde uložit.")
        raw_sources = data.get("sources") or []
        if not isinstance(raw_sources, list):
            raise ValueError("Neplatné hodnocení.")
        sources = [s for s in raw_sources[:MAX_SOURCES] if isinstance(s, str) and _SOURCE_REF.match(s)]
        return cls(vote=vote, reason=reason, comment=comment, question=question[:MAX_QUESTION], answer=answer[:MAX_ANSWER],
                   sources=sources, found=bool(data.get("found", True)), mode=mode, index_date=index_date,
                   ts=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def _text(value: object) -> str:
    """Prostý text bez řídicích a neviditelných znaků (stejná očista jako u dotazu)."""
    return normalize(str(value)) if isinstance(value, str) else ""


class FeedbackStore(Protocol):
    kind: str                       # „soubor“ | „log“ – hlásí ho /api/status

    def add(self, item: Feedback) -> None: ...


class LogStore:
    """Jen řádek do logu: hodnocení, důvod, výsledek, počet úseků. Texty do logu nejdou (jako u dotazů)."""
    kind = "log"

    def add(self, item: Feedback) -> None:
        log.info("feedback vote=%s reason=%s found=%s sources=%d stored=log", item.vote, item.reason or "-", item.found,
                 len(item.sources))


class FileStore:
    """Řádek JSON na konec souboru. Když se na disk psát nedá (hosting bez zápisu, práva), přepne se napořád na log.

    Zapisovatelnost se zkouší hned při startu, aby `/api/status` hlásil pravdu od začátku, ne až po prvním palci.
    """
    kind = "soubor"

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fallback: LogStore | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8"):
                pass
        except OSError as exc:
            self._switch_to_log(exc)

    def _switch_to_log(self, exc: OSError) -> None:
        log.warning("feedback soubor nejde zapsat (%s) – dál jen do logu", exc.__class__.__name__)
        self._fallback = LogStore()
        self.kind = "log"

    def add(self, item: Feedback) -> None:
        if self._fallback is None:
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
            except OSError as exc:
                self._switch_to_log(exc)
        if self._fallback is not None:
            self._fallback.add(item)
            return
        log.info("feedback vote=%s reason=%s found=%s sources=%d stored=soubor", item.vote, item.reason or "-", item.found,
                 len(item.sources))


def read_all(path: Path) -> list[dict]:
    """Načte uložené záznamy (pro ruční prohlídku a budoucí vyhodnocení)."""
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
