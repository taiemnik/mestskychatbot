"""Odpověď na dotaz: filtr → hledání → model → ověření → odpověď se zdroji.

Co se vydá, rozhoduje server, ne model:
  - dotaz, který neprojde filtrem, dostane pevnou větu a model se nevolá;
  - když hledání nic podobného nenajde, model se nevolá („nenašel jsem“);
  - odpověď modelu se vydá jen s platnými citacemi nalezených úseků a po kontrole obsahu (slova, čísla);
  - hotovou odpověď ověří druhé volání modelu větu po větě proti úsekům citovaných předpisů: nepodložená věta se
    vynechá, a když neobstojí víc než polovina vět nebo ověření selže, nevydá se nic;
  - při „nenašel jsem“ se text modelu nikdy nevydává – jen pevná věta a nejbližší úseky;
  - žádost o názor nebo osobní rozhodnutí model jen označí (`nazor`); větu, že od názorů chatbot není, píše server;
  - když se cituje předpis změněný novelou, dostane model do podkladu i úseky novely; z ustanovení, které
    novela výslovně zrušila, se neodpovídá nikdy – místo odpovědi je pevná věta, že ustanovení neplatí;
  - upozornění k citovanému předpisu (novela, zrušení, budoucí účinnost) a na mapu nebo jinou grafiku, kterou
    chatbot nečte, připisuje pod odpověď server, ne model;
  - když model není dostupný (výpadek, vyčerpaný kredit), neskončí dotaz chybou: uživatel dostane
    místa, která našlo hledání podle slov.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from chatbot.answer.prompt import build_messages, verify_messages
from chatbot.config import Instance, Settings
from chatbot.guard.answer import check_answer, drop_unsupported, sentences
from chatbot.guard.question import MESSAGES, check_question, normalize
from chatbot.guard.verify import keep_supported, read_verdict
from chatbot.index.model import amended_by, amends, chunk_part, document_note, repealed_parts
from chatbot.index.store import Index
from chatbot.llm.provider import LLMError, LLMFormatError, LLMProvider
from chatbot.search.hybrid import Hit, Searcher

# Když odpověď v dokumentech není. Věta má říct, z čeho chatbot odpovídá: „kdo je starosta“ není špatně položený
# dotaz, jen v předpisech není – výzva „zkuste přeformulovat“ tu vypadala jako neschopnost. Vlastní znění
# (z čeho odpovídá a kam jít jinam) se nastaví v chatbot.toml, `nenalezeno`.
NOT_FOUND_TEXT = ("Na tohle jsem v dokumentech, které mám k dispozici, odpověď nenašel. Odpovídám jen z dokumentů – "
                  "co v nich není, nevím. Ptáte-li se na něco, co v nich být má, zkuste dotaz upřesnit.")
VERIFY_TOKENS = 300             # verdikt ověření je krátký: jedna hodnota na větu
AMENDMENT_CHUNKS = 6            # kolik úseků novel se nejvýš přidá do podkladu (novely bývají krátké)
NOTE_LINE = "Pozor, {label}: {note}"
# u úseku, který novela výslovně zrušila: věta pro model (v podkladu)
REPEALED_FOR_MODEL = "Toto ustanovení zrušil předpis č. {number}. Neuváděj ho jako platné."
# Místo odpovědi, když model ze zrušeného ustanovení odpověděl, nebo když odpověď nevyšla a zrušené ustanovení je
# první mezi nálezy. Věta říká jen to, co server ví jistě: co našel a že to neplatí – ne, na co se člověk ptal.
REPEALED_TEXT = ("Nejbližší místo, které jsem k dotazu našel, je {label}, {part}. Toto ustanovení ale zrušil předpis "
                 "č. {number}, proto z něj neodpovídám.")
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


class _Withheld(Exception):
    """Odpověď se nevydá. Nese to, co uživatel dostane místo ní (pevnou větu a nalezená místa)."""

    def __init__(self, answer: Answer):
        super().__init__(answer.blocked or "")
        self.answer = answer


@dataclass
class _Found:
    """Co hledání k dotazu našlo, i s poznámkami pro model – podklad všech kroků za hledáním."""
    index: Index
    hits: list[Hit]                 # nalezené úseky a za nimi úseky novel
    repealed: dict[str, str]        # nalezený úsek, který novela výslovně zrušila → číslo novely
    notes: dict[int, str]           # dokument → upozornění (novela, zrušení, budoucí účinnost)
    chunk_notes: dict[str, str]     # úsek → upozornění jen k němu (zrušené ustanovení)
    instance: Instance

    def miss(self, blocked: str | None = None) -> Answer:
        """Odpověď se nevydá. Je-li první mezi nálezy ustanovení zrušené novelou, řekne se to – člověk se ptal
        nejspíš na ně a „zkuste dotaz upřesnit“ by ho poslalo hledat znění, které neplatí."""
        if self.hits[0].chunk.id in self.repealed:
            return _repealed(self, self.hits[0])
        return _not_found(self.index, self.hits, self.instance, blocked=blocked)

    def grounds(self, citations: list[int]) -> list[str]:
        """Opora odpovědi: citované úseky, názvy jejich předpisů a článků a upozornění k nim – to vše model
        u úseku vidí a smí to jmenovat."""
        cited = [self.hits[n - 1].chunk for n in citations]
        return ([chunk.text for chunk in cited] + [" ".join(chunk.path) for chunk in cited]
                + [self.notes[chunk.doc_id] for chunk in cited if chunk.doc_id in self.notes]
                + [self.chunk_notes[chunk.id] for chunk in cited if chunk.id in self.chunk_notes])


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
        return _not_found(index, hits, instance)          # nic podobného: model se nevolá

    found = _gather(index, hits, instance)
    try:
        raw = llm.chat_json(*build_messages(question, found.hits, instance, found.notes, found.chunk_notes),
                            settings.max_answer_tokens)
    except LLMFormatError:
        # model odpověděl, ale ne ve smluveném tvaru – to není výpadek, uživatel dostane nalezená místa
        return found.miss("odp-necitelna")
    except LLMError:
        return _model_down(index, found.hits)
    if isinstance(raw, dict) and raw.get("nazor") is True:      # jen true; „ano“ ani 1 příznakem nejsou
        return _opinion(index, found.hits)
    parsed = _validate(raw, len(found.hits))
    if parsed is None:
        return found.miss()
    try:
        text, citations = _checked(question, *parsed, found, llm, settings)
    except _Withheld as stop:
        return stop.answer

    text, order = _renumber(text, citations)
    sources = [_ref(index, found.hits[old - 1], new) for new, old in enumerate(order, start=1)]
    related = [_ref(index, hit, 0) for n, hit in enumerate(found.hits, start=1) if n not in citations]
    notes_below = _document_notes(index, sources) + _graphics_note(index, sources)
    return Answer(found=True, answer=text + notes_below, sources=sources, related=related)


def _gather(index: Index, hits: list[Hit], instance: Instance) -> _Found:
    """K nalezeným úsekům přidá novely a poznámky, které má model u úseků vidět."""
    hits, repealed = _with_amendments(index, hits)
    notes = {hit.chunk.doc_id: _note(index, hit.chunk.doc_id) for hit in hits}
    return _Found(index=index, hits=hits, repealed=repealed, notes={doc: note for doc, note in notes.items() if note},
                  chunk_notes={chunk_id: REPEALED_FOR_MODEL.format(number=number) for chunk_id, number in repealed.items()},
                  instance=instance)


def _checked(question: str, text: str, citations: list[int], found: _Found, llm: LLMProvider,
             settings: Settings) -> tuple[str, list[int]]:
    """Kontroly odpovědi modelu od nejlevnější: zrušené ustanovení, slova a čísla, ověření vět druhým voláním.
    Vrací text a citace, které se vydají; co vydat nejde, ohlásí výjimkou `_Withheld`."""
    # Ze zrušeného ustanovení se neodpovídá – o tom rozhoduje server, ne model ani kontrolor, a neplatí z toho
    # výjimka ani pro odpověď, která cituje i novelu: stačilo by ji přicitovat a zrušené znění by prošlo.
    # Odpověď „ustanovení bylo zrušeno“ má citovat novelu, ne zrušený úsek.
    gone = next((found.hits[n - 1] for n in citations if found.hits[n - 1].chunk.id in found.repealed), None)
    if gone:
        raise _Withheld(_repealed(found, gone))
    text = _keep_cited_markers(text, citations)
    grounds = found.grounds(citations)
    # čísla se smějí opřít i o ostatní nalezené úseky citovaných předpisů (stejný podklad dostane kontrolor)
    cited_docs = {found.hits[n - 1].chunk.doc_id for n in citations}
    checks = {"leak_marker": found.instance.leak_marker, "max_chars": settings.max_answer_chars,
              "min_grounding": settings.min_grounding,
              "number_texts": grounds + [hit.chunk.text for hit in found.hits if hit.chunk.doc_id in cited_docs]}
    problem = check_answer(text, grounds, **checks)     # celá odpověď tak, jak ji model napsal (opsané zadání, odkazy…)
    if not problem:
        text = drop_unsupported(text, grounds)           # věta bez opory (přilepená na pokyn z dotazu) se nevydá
        problem = check_answer(text, grounds, **checks)  # po vynechání nesmí zbýt jen torzo
    if problem:
        # kódy kontroly odpovědi mají předponu „odp-“, aby se nepletly s kódy filtru dotazu
        raise _Withheld(found.miss(f"odp-{problem}"))
    if settings.verify_answer:
        text, citations = _verified(question, text, citations, grounds, checks, found, llm)
    return text, citations


def _verified(question: str, text: str, citations: list[int], grounds: list[str], checks: dict, found: _Found,
              llm: LLMProvider) -> tuple[str, list[int]]:
    """Druhé volání modelu: stojí to, co věty tvrdí, v úsecích citovaných předpisů? Co se neověří, se nevydá."""
    pieces = sentences(text)
    # kontrolor dostane citované úseky a ostatní nalezené úseky týchž předpisů: odpověď bývá správně, jen
    # necituje všechno, o co se opírá. Úseky jiných předpisů ne – z těch čerpat nesmí.
    cited_docs = {found.hits[n - 1].chunk.doc_id for n in citations}
    context = [(n, found.hits[n - 1]) for n in citations]
    context += [(n, hit) for n, hit in enumerate(found.hits, start=1) if n not in citations and hit.chunk.doc_id in cited_docs]
    try:
        verdict = llm.chat_json(*verify_messages(question, pieces, context, found.notes, found.chunk_notes), VERIFY_TOKENS)
        unsupported = read_verdict(verdict, len(pieces))
    except (LLMFormatError, ValueError):
        raise _Withheld(found.miss("odp-neoverena")) from None
    except LLMError:
        raise _Withheld(_model_down(found.index, found.hits)) from None
    if not unsupported:
        return text, citations
    text = keep_supported(text, unsupported)
    if not text:
        raise _Withheld(found.miss("odp-neoverena"))
    # Zdroj, o který se opírala jen vynechaná věta, už zdrojem odpovědi není. Když ale ve zbylém textu žádná značka
    # není – model je napsal až za poslední větu, nebo vůbec –, zůstávají zdroje, jak je model uvedl: každá zbylá
    # věta je ověřená proti úsekům právě těchto předpisů. Do 13. 10. 2026 se taková odpověď nevydala a v měření tak
    # padaly správné odpovědi (první věta odpověděla, kontrolor vynechal až dovětek, za kterým byly značky). Cena:
    # občas zbude jen věta, která říká, o čem ustanovení je – je pravdivá a zdroj u ní je (docs/ochrana.md, Meze).
    citations = [n for n in citations if f"[{n}]" in text] or citations
    if check_answer(text, grounds, **checks):
        raise _Withheld(found.miss("odp-neoverena"))
    return text, citations


def _amendments(index: Index, meta: dict) -> dict[str, list]:
    """Novely předpisu, které jsou v indexu: číslo novely → její úseky. Novela se pozná z poznámek obou předpisů;
    shoda jen čísla nestačí (nařízení i vyhláška mívají stejné číslo)."""
    found = {}
    for number in amended_by(meta):
        novels = {doc_id for doc_id, doc in index.docs.items()
                  if (doc.get("meta") or {}).get("cislo") == number and meta.get("cislo") in amends(doc.get("meta") or {})}
        if novels:
            found[number] = [chunk for chunk in index.chunks if chunk.doc_id in novels]
    return found


def _is_gone(label: str, gone: set[tuple[int, int | None]]) -> bool:
    """Je úsek s tímto označením mezi zrušenými (celý článek, nebo právě jeho odstavec)?"""
    article, paragraph = chunk_part(label)
    return bool(article) and ((article, None) in gone or (article, paragraph) in gone)


def _with_amendments(index: Index, hits: list[Hit]) -> tuple[list[Hit], dict[str, str]]:
    """K nalezeným úsekům přidá úseky novel, které jejich předpisy mění (jsou-li v indexu), a řekne, které
    z nalezených úseků novela výslovně zrušila (úsek → číslo novely).

    Sbírka vede novelu jako samostatný předpis a původní znění nechává, jak bylo. Dotaz na zrušený článek najde
    původní předpis, ne novelu (dotaz na obsah článku × „Článek 3 se zrušuje.“) – model by odpověděl ze zrušeného
    znění. Zrušené ustanovení se pozná jen z vět „Článek 5 až 7 se zrušují.“ a „V čl. 3 se odstavec 2 zrušuje.“;
    jinou změnu znění má model přečíst z úseků novely sám."""
    present = {hit.chunk.id for hit in hits}
    extra, repealed = [], {}
    for doc_id in dict.fromkeys(hit.chunk.doc_id for hit in hits):
        for number, texts in _amendments(index, index.docs.get(doc_id, {}).get("meta") or {}).items():
            extra += [chunk for chunk in texts if chunk.id not in present]
            gone = set().union(*(repealed_parts(chunk.text) for chunk in texts))
            repealed.update({hit.chunk.id: number for hit in hits if hit.chunk.doc_id == doc_id and _is_gone(hit.chunk.label, gone)})
    extra = list({chunk.id: chunk for chunk in extra}.values())[:AMENDMENT_CHUNKS]
    return hits + [Hit(chunk=chunk, score=0.0, similarity=0.0) for chunk in extra], repealed


def _repealed(found: _Found, hit: Hit) -> Answer:
    """Místo odpovědi, když jde o ustanovení, které novela zrušila: pevná věta, která to řekne, a nalezená místa."""
    text = REPEALED_TEXT.format(label=_short_label(found.index.docs.get(hit.chunk.doc_id, {})), part=hit.chunk.label,
                                number=found.repealed[hit.chunk.id])
    return Answer(found=False, answer=text, related=[_ref(found.index, item, 0) for item in found.hits], blocked="odp-zruseno")


def _not_found(index: Index, hits: list[Hit], instance: Instance, blocked: str | None = None) -> Answer:
    return Answer(found=False, answer=instance.nenalezeno or NOT_FOUND_TEXT, related=[_ref(index, hit, 0) for hit in hits],
                  blocked=blocked)


def _document_notes(index: Index, sources: list[SourceRef]) -> str:
    """Odstavce pod odpověď: upozornění k citovaným předpisům (novela, zrušení, budoucí účinnost).

    Píše je server, aby nezáleželo na tom, jestli si model poznámky všimne. Upozornění začínají „Pozor: tento
    předpis…“ – aby bylo jasné, kterého z citovaných předpisů se týkají, předřadí se jim jeho označení."""
    lines = []
    for doc_id in dict.fromkeys(ref.doc_id for ref in sources):
        doc = index.docs.get(doc_id, {})
        note = document_note(doc.get("meta") or {}, _today()).replace("Pozor: ", "")
        if note:
            lines.append(NOTE_LINE.format(label=_short_label(doc), note=note[0].lower() + note[1:]))
    return "".join("\n\n" + line for line in lines)


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
