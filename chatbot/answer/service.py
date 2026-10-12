"""Odpověď na dotaz: filtr → hledání → model → ověření → odpověď se zdroji.

Co se vydá, rozhoduje server, ne model:
  - dotaz, který neprojde filtrem, dostane pevnou větu a model se nevolá;
  - když hledání nic podobného nenajde, model se nevolá („nenašel jsem“);
  - odpověď modelu se vydá jen s platnými citacemi nalezených úseků a po kontrole obsahu;
  - při „nenašel jsem“ se text modelu nikdy nevydává – jen pevná věta a nejbližší úseky;
  - žádost o názor nebo osobní rozhodnutí model jen označí (`nazor`); větu, že od názorů chatbot není, píše server;
  - když odpověď cituje předpis s mapou nebo jinou grafikou, kterou chatbot nečte, server to k odpovědi připíše;
  - když model není dostupný (výpadek, vyčerpaný kredit), neskončí dotaz chybou: uživatel dostane
    místa, která našlo hledání podle slov.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from chatbot.answer.prompt import build_messages
from chatbot.config import Instance, Settings
from chatbot.guard.answer import check_answer, drop_unsupported
from chatbot.guard.question import MESSAGES, check_question, normalize
from chatbot.index.model import document_note
from chatbot.index.store import Index
from chatbot.llm.provider import LLMError, LLMFormatError, LLMProvider
from chatbot.search.hybrid import Hit, Searcher

NOT_FOUND_TEXT = ("V dokumentech, které mám k dispozici, jsem na to odpověď nenašel. "
                  "Zkuste dotaz přeformulovat nebo upřesnit, čeho se týká.")
MODEL_DOWN_TEXT = ("Model, který odpovědi sestavuje, teď není dostupný. "
                   "Níže jsou místa, která hledání v dokumentech našlo – odpověď v nich najdete sami.")
# Mapy, výkresy a obrázky chatbot nečte (příznaky dává čtení dokumentů). Cituje-li odpověď z takového dokumentu,
# připíše se pod ni tahle věta – jinak by „kde přesně to platí“ vypadalo jako zodpovězené.
GRAPHICS_FLAGS = ("obrazky", "popisky", "priloha-bez-textu")
GRAPHICS_NOTE = "Pozor: {label} má i mapu nebo jinou grafiku, kterou chatbot nevidí. Zkontrolujte si ji v originálu."
GRAPHICS_NOTE_PLURAL = "Pozor: {label} mají i mapu nebo jinou grafiku, kterou chatbot nevidí. Zkontrolujte si ji v originálu."
_GROUP = re.compile(r"\[(\d{1,3}(?:\s*[,;]\s*\d{1,3})+)\]")   # „[1, 2]“
_MARKER = re.compile(r"\[(\d{1,3})\]")                        # „[1]“ (delší čísla značkou nejsou)


@dataclass
class SourceRef:
    """Jeden úsek dokumentu u odpovědi. `anchor` vede na blok ve znění dokumentu (/api/doc)."""
    n: int                  # číslo značky v textu odpovědi; u „mohlo by souviset“ 0
    doc_id: int
    doc_label: str          # „Obecně závazná vyhláška č. 5/2025 o místním poplatku ze psů“
    label: str              # „čl. 4 Sazba poplatku, odst. 1“
    text: str
    anchor: str
    note: str = ""          # upozornění k dokumentu (například že ho změnila novela)
    url: str = ""           # adresa originálu, když ji známe


@dataclass
class Answer:
    found: bool
    answer: str
    sources: list[SourceRef] = field(default_factory=list)
    related: list[SourceRef] = field(default_factory=list)
    blocked: str | None = None      # kód důvodu, proč se odpověď nevydala (jen pro log a testy)
    degraded: bool = False          # model nebyl dostupný – jsou tu jen nalezená místa


def answer_question(question: str, index: Index, searcher: Searcher, llm: LLMProvider, settings: Settings,
                    instance: Instance | None = None) -> Answer:
    instance = instance or Instance()
    verdict = check_question(question)
    if not verdict.ok:
        return Answer(found=False, answer=verdict.message or "", blocked=verdict.reason)

    question = normalize(question)
    try:
        query_vec = llm.embed([question])[0]
    except LLMError:
        # bez modelu se hledá jen podle slov (nulový vektor = význam se nehledá)
        hits = searcher.search(question, np.zeros(index.vectors.shape[1], dtype=np.float32), settings.top_k)
        return _model_down(index, hits)
    hits = searcher.search(question, query_vec, settings.top_k, settings.semantic_k)
    if not hits or max(hit.similarity for hit in hits) < settings.min_similarity:
        return _not_found(index, hits)                    # nic podobného: model se nevolá

    notes = {hit.chunk.doc_id: _note(index, hit.chunk.doc_id) for hit in hits}
    system, user = build_messages(question, hits, instance, {doc: note for doc, note in notes.items() if note})
    try:
        raw = llm.chat_json(system, user, settings.max_answer_tokens)
    except LLMFormatError:
        # model odpověděl, ale ne ve smluveném tvaru – to není výpadek, uživatel dostane nalezená místa
        return _not_found(index, hits, blocked="odp-necitelna")
    except LLMError:
        return _model_down(index, hits)
    if isinstance(raw, dict) and raw.get("nazor") is True:      # jen true; „ano“ ani 1 příznakem nejsou
        return _opinion(index, hits)
    parsed = _validate(raw, len(hits))
    if parsed is None:
        return _not_found(index, hits)
    text, citations = parsed
    text = _keep_cited_markers(text, citations)
    # opora odpovědi: citované úseky a upozornění k jejich dokumentům (model ho smí zmínit)
    cited = [hits[n - 1].chunk for n in citations]
    grounds = [ground for ground in [chunk.text for chunk in cited] + [notes[chunk.doc_id] for chunk in cited] if ground]
    checks = {"leak_marker": instance.leak_marker, "max_chars": settings.max_answer_chars,
              "min_grounding": settings.min_grounding}
    problem = check_answer(text, grounds, **checks)     # celá odpověď tak, jak ji model napsal (opsané zadání, odkazy…)
    if not problem:
        # věta bez opory (přilepená na pokyn z dotazu) se nevydá; oporou věty je i název předpisu a článku u úseku
        text = drop_unsupported(text, grounds + [" ".join(chunk.path) for chunk in cited])
        problem = check_answer(text, grounds, **checks)  # po vynechání nesmí zbýt jen torzo
    if problem:
        # kódy kontroly odpovědi mají předponu „odp-“, aby se nepletly s kódy filtru dotazu
        return _not_found(index, hits, blocked=f"odp-{problem}")

    text, order = _renumber(text, citations)
    sources = [_ref(index, hits[old - 1], new) for new, old in enumerate(order, start=1)]
    related = [_ref(index, hit, 0) for n, hit in enumerate(hits, start=1) if n not in citations]
    return Answer(found=True, answer=text + _graphics_note(index, sources), sources=sources, related=related)


def _not_found(index: Index, hits: list[Hit], blocked: str | None = None) -> Answer:
    return Answer(found=False, answer=NOT_FOUND_TEXT, related=[_ref(index, hit, 0) for hit in hits], blocked=blocked)


def _opinion(index: Index, hits: list[Hit]) -> Answer:
    """Žádost o názor nebo osobní rozhodnutí, kterou poznal až model: pevná věta a pod ní nalezená místa."""
    return Answer(found=False, answer=MESSAGES["nazor"], related=[_ref(index, hit, 0) for hit in hits], blocked="odp-nazor")


def _graphics_note(index: Index, sources: list[SourceRef]) -> str:
    """Odstavec pod odpověď, když některý citovaný dokument má mapu nebo jinou grafiku; jinak prázdný text."""
    labels = []
    for doc_id in dict.fromkeys(ref.doc_id for ref in sources):
        doc = index.docs.get(doc_id, {})
        if any(flag in GRAPHICS_FLAGS for flag in doc.get("flags") or []):
            labels.append(_short_label(doc))
    if not labels:
        return ""
    if len(labels) == 1:
        return "\n\n" + GRAPHICS_NOTE.format(label=labels[0])
    return "\n\n" + GRAPHICS_NOTE_PLURAL.format(label=", ".join(labels[:-1]) + " a " + labels[-1])


def _short_label(doc: dict) -> str:
    """„Obecně závazná vyhláška č. 5/2025“ místo celého názvu (bývá na tři řádky); bez čísla celé označení."""
    meta = doc.get("meta") or {}
    if meta.get("druh") and meta.get("cislo"):
        return f"{meta['druh']} č. {meta['cislo']}"
    return str(doc.get("label") or meta.get("nazev") or "dokument")


def _model_down(index: Index, hits: list[Hit]) -> Answer:
    """Model neodpovídá: žádná chyba, ale nalezená místa a věta, která to řekne."""
    return Answer(found=False, answer=MODEL_DOWN_TEXT, related=[_ref(index, hit, 0) for hit in hits],
                  blocked="model-nedostupny", degraded=True)


def _today() -> str:
    return date.today().isoformat()


def _note(index: Index, doc_id: int) -> str:
    """Upozornění k dokumentu: novela, zrušení k datu, předpis, který ještě není účinný."""
    return document_note(index.docs.get(doc_id, {}).get("meta") or {}, _today())


def _ref(index: Index, hit: Hit, n: int) -> SourceRef:
    chunk = hit.chunk
    doc = index.docs.get(chunk.doc_id, {})
    meta = doc.get("meta") or {}
    return SourceRef(n=n, doc_id=chunk.doc_id, doc_label=doc.get("label") or chunk.path[0], label=chunk.label,
                     text=chunk.text, anchor=chunk.anchor, note=document_note(meta, _today()), url=str(meta.get("zdroj") or ""))


def _validate(raw: dict, hit_count: int) -> tuple[str, list[int]] | None:
    """Zkontroluje tvar odpovědi modelu. Vrací (text, čísla citovaných úseků) nebo None („nenašel jsem“)."""
    if not isinstance(raw, dict) or raw.get("found") is not True:
        return None
    text, citations = raw.get("answer"), raw.get("citations")
    if not isinstance(text, str) or not text.strip() or not isinstance(citations, list) or not citations:
        return None
    # jen celá čísla v rozsahu nalezených úseků; True/False nejsou čísla
    if any(type(c) is not int or not 1 <= c <= hit_count for c in citations):
        return None
    return text.strip(), list(dict.fromkeys(citations))


def _keep_cited_markers(text: str, citations: list[int]) -> str:
    """„[1, 2]“ rozepíše na „[1][2]“ a značky, které nejsou v citacích, z textu odstraní."""
    text = _GROUP.sub(lambda m: "".join(f"[{part.strip()}]" for part in re.split(r"[,;]", m.group(1))), text)
    text = _MARKER.sub(lambda m: m.group(0) if int(m.group(1)) in citations else "", text)
    text = re.sub(r"[^\S\n]{2,}", " ", text)
    return re.sub(r"\s+([.,;:!?])", r"\1", text).strip()


def _renumber(text: str, citations: list[int]) -> tuple[str, list[int]]:
    """Přečísluje značky od 1 v pořadí prvního výskytu v textu.

    Vrací nový text a původní čísla úseků v novém pořadí; citovaný úsek bez značky v textu jde na konec.
    """
    order: list[int] = []
    for match in _MARKER.finditer(text):
        n = int(match.group(1))
        if n not in order:
            order.append(n)
    # Model občas citaci uvede jen v seznamu a do textu značku nenapíše. Uživatel by pak neměl na co
    # kliknout – chybějící značky se doplní na konec odpovědi.
    missing = [n for n in citations if n not in order]
    if missing:
        text = text.rstrip() + " " + "".join(f"[{n}]" for n in missing)
        order += missing
    new_number = {old: new for new, old in enumerate(order, start=1)}
    return _MARKER.sub(lambda m: f"[{new_number[int(m.group(1))]}]", text), order
