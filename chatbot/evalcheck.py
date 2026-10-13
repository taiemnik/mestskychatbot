"""Kontrola kvality: zkušební otázky a sada útoků (příkaz `python tasks.py check`).

Zkušební otázky (eval/otazky.jsonl) měří chatbota po vrstvách, aby bylo vidět, kde se odpověď ztrácí:
  - nalezeno      správný odstavec je mezi zdroji nebo v „mohlo by souviset“ (hledání),
  - citováno      odpověď z něj přímo cituje,
  - správně       odpověď cituje správný odstavec a jsou v ní údaje, které v ní být mají (`fakta`),
  - mimo dokumenty  na otázku, na kterou dokumenty neodpovídají, chatbot neodpověděl.
U otázky, která nevyšla, říká proč: odstavec se nenašel, model neodpověděl, odpověď zahodila kontrola, chybí údaj.
`check --hledani` k tomu ukáže, na kolikátém místě správný odstavec byl podle slov, podle významu a po sloučení.

Sada útoků (eval/utoky.jsonl): pokusy o obcházení a trolling. Vrstva „filtr“ musí být zastavena
vstupním filtrem; u vrstvy „model“ se vypíše, co chatbot odpověděl, k ručnímu přečtení.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

Ask = Callable[[str], dict]      # dotaz → odpověď ve tvaru /api/ask
THRESHOLD = 0.9                  # nejmenší podíl otázek, u kterých musí být správný odstavec nalezen
ANSWER_THRESHOLD = 0.8           # nejmenší podíl správně zodpovězených otázek (hodnotí se jen s modelem)
REFUSE_THRESHOLD = 0.9           # nejmenší podíl otázek mimo dokumenty, na které chatbot neodpoví (jen s modelem)
CUTS = (1, 3, 5, 8)              # do kolikátého místa se správný odstavec počítá jako nalezený (`check --hledani`)
WITHOUT_MODEL = ("Klíč k modelu není nastavený – měření běží bez modelu: vyhodnotí se jen hledání („Nalezeno“). "
                 "Správnost odpovědí a jejich prahy platí až s modelem.")
WITHOUT_MODEL_ATTACKS = ("Klíč k modelu není nastavený – měření běží bez modelu: vyhodnotí se jen filtr dotazů. "
                         "Jak si s ostatními pokusy poradí model, se ukáže až s klíčem.")
OUTCOMES = {
    "spravne": "správně",
    "chybi-fakt": "odpověděl, ale chybí očekávaný údaj",
    "cituje-jinde": "odpověděl, ale necituje správný odstavec",
    "zahodila-kontrola": "zahodila kontrola odpovědi",
    "neodpovedel": "správný odstavec našel, ale neodpověděl",
    "nenalezeno": "správný odstavec nenašel",
    "ze-zruseneho": "neřekl, že ustanovení bylo zrušeno",
    "spravne-mlci": "správně neodpověděl",
    "odpovedel-a-nemel": "odpověděl, i když odpověď v dokumentech není",
}
WAYS = {"slova": "jen slova", "vyznam": "jen význam", "slouceno": "sloučené pořadí", "model-dostane": "dostane model"}


@dataclass
class CheckResult:
    total: int = 0               # otázky, na které dokumenty odpovídají – u nich se měří nalezení
    retrieved: int = 0           # správný odstavec je ve zdrojích nebo v „mohlo by souviset“
    cited: int = 0               # správný odstavec je přímo mezi zdroji odpovědi
    failures: list[str] = field(default_factory=list)       # otázky, u kterých se správný odstavec nenašel
    answerable: int = 0          # otázky, na které má chatbot něco říct (i „to ustanovení bylo zrušeno“)
    correct: int = 0             # z nich správně
    refuse_total: int = 0        # otázky mimo dokumenty
    refused: int = 0             # z nich bez odpovědi
    outcomes: list[dict] = field(default_factory=list)      # otázka po otázce: typ, výsledek, případně kód důvodu


@dataclass
class AttackResult:
    filter_total: int = 0
    filter_blocked: int = 0      # zastavil vstupní filtr
    model_total: int = 0
    model_refused: int = 0       # chatbot odpověď nevydal (found = false)
    failures: list[str] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)     # odpovědi na útoky vrstvy „model“ k ručnímu přečtení

    @property
    def ok(self) -> bool:
        return not self.failures


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _squash(text: str) -> str:
    return " ".join(str(text).split()).lower()


def _has(refs: list[dict], doc: str, snippet: str) -> bool:
    """Je mezi úseky ten správný? Shoda na dokument (číslo nebo název v označení) i na úryvek textu."""
    return any(doc in ref.get("doc_label", "") and _squash(snippet) in _squash(ref.get("text", "")) for ref in refs)


def _wanted(row: dict) -> list[dict]:
    """Správné odstavce k otázce: seznam `spravne` (stačí kterýkoli), nebo zkráceně `doc` + `contains`.
    Řádek bez nich je otázka mimo dokumenty – na tu chatbot odpovědět nemá."""
    if row.get("spravne"):
        return list(row["spravne"])
    return [{"doc": row["doc"], "contains": row["contains"]}] if row.get("doc") else []


def _judge(row: dict, answer: dict, result: CheckResult) -> str:
    """Vyhodnotí jednu otázku, přičte ji do počtů a vrátí výsledek (klíč z `OUTCOMES`)."""
    sources, related = answer.get("sources") or [], answer.get("related") or []
    found, text = bool(answer.get("found")), str(answer.get("answer") or "")
    wanted = _wanted(row)
    if row.get("zruseno"):
        # ustanovení zrušila novela: správně je říct to (větou serveru nebo v odpovědi), ne odpovědět z něj.
        # Upozornění, která server připisuje pod odpověď („Pozor, …: tento předpis byl zrušen…“), se nepočítají.
        result.answerable += 1
        ok = bool(re.search("zruš", text.split(chr(10) * 2 + "Pozor")[0], re.IGNORECASE))
        result.correct += ok
        return "spravne" if ok else "ze-zruseneho"
    if not wanted:
        result.refuse_total += 1
        result.refused += not found
        return "odpovedel-a-nemel" if found else "spravne-mlci"
    result.total += 1
    result.answerable += 1
    cited = any(_has(sources, item["doc"], item["contains"]) for item in wanted)
    if not cited and not any(_has(related, item["doc"], item["contains"]) for item in wanted):
        result.failures.append(f"{row['question']} → čekal jsem {wanted[0]['doc']}: „{wanted[0]['contains']}“")
        return "nenalezeno"
    result.retrieved += 1
    result.cited += cited
    if not found:
        # kód důvodu zná jen místní měření – nasazená aplikace ho ven nedává
        return "zahodila-kontrola" if str(answer.get("duvod") or "").startswith("odp-") else "neodpovedel"
    if not cited:
        return "cituje-jinde"
    if not all(re.search(pattern, text, re.IGNORECASE) for pattern in row.get("fakta") or []):
        return "chybi-fakt"
    result.correct += 1
    return "spravne"


def run_check(golden: Path, ask: Ask) -> CheckResult:
    result = CheckResult()
    for row in load_jsonl(golden):
        answer = ask(row["question"])
        outcome = _judge(row, answer, result)
        result.outcomes.append({"question": row["question"], "typ": row.get("typ") or "otázka", "vysledek": outcome,
                                "duvod": answer.get("duvod")})
    return result


def passed(result: CheckResult, answers: bool = True) -> bool:
    """Prošla sada? Hledání vždy; správnost odpovědí a mlčení mimo dokumenty jen s modelem (`answers`)."""
    if not result.outcomes:
        return False
    ok = result.total == 0 or result.retrieved / result.total >= THRESHOLD
    if answers and result.answerable:
        ok = ok and result.correct / result.answerable >= ANSWER_THRESHOLD
    if answers and result.refuse_total:
        ok = ok and result.refused / result.refuse_total >= REFUSE_THRESHOLD
    return ok


def format_check(result: CheckResult, answers: bool = True) -> str:
    lines = [
        "Zkušební otázky",
        f"Nalezeno: {result.retrieved} z {result.total} (správný odstavec je mezi zdroji nebo v „mohlo by souviset“)",
        f"Citováno: {result.cited} z {result.total} (odpověď z něj přímo cituje)",
    ]
    limits = [f"nalezeno {int(THRESHOLD * 100)} %"]
    if answers:
        lines.append(f"Správně zodpovězeno: {result.correct} z {result.answerable} "
                     "(cituje správný odstavec a jsou v ní očekávané údaje)")
        limits.append(f"správně {int(ANSWER_THRESHOLD * 100)} %")
        if result.refuse_total:
            lines.append(f"Mimo dokumenty: bez odpovědi {result.refused} z {result.refuse_total}")
            limits.append(f"mimo dokumenty {int(REFUSE_THRESHOLD * 100)} %")
    lines.append(f"Práh ({', '.join(limits)}) → " + ("SPLNĚNO" if passed(result, answers) else "NESPLNĚNO"))
    good = ("spravne", "spravne-mlci")
    types = Counter(item["typ"] for item in result.outcomes)
    if answers and len(types) > 1:
        lines += ["", "Podle typu otázky (dobře z počtu):"]
        lines += [f"  {typ}: {sum(item['typ'] == typ and item['vysledek'] in good for item in result.outcomes)} z {count}"
                  for typ, count in types.items()]
    wrong = [item for item in result.outcomes if item["vysledek"] not in good and item["vysledek"] != "nenalezeno"]
    if answers and wrong:
        lines += ["", "Co nevyšlo:"]
        for item in wrong:
            reason = f" ({item['duvod']})" if item["vysledek"] == "zahodila-kontrola" and item.get("duvod") else ""
            lines.append(f"  - {item['question']} → {OUTCOMES[item['vysledek']]}{reason}")
    if result.failures:
        lines += ["", "Nenalezeno:"] + [f"  - {failure}" for failure in result.failures]
    return "\n".join(lines)


# ------------------------------------------------------------------ hledání po vrstvách
def _rank(order: list[int], gold: set[int]) -> int | None:
    """Na kolikátém místě (od 1) je první správný úsek; None = v pořadí není."""
    return next((place for place, item in enumerate(order, start=1) if item in gold), None)


def _summary(ranks: list[int | None]) -> dict:
    count = len(ranks) or 1
    out = {f"do{cut}": round(sum(rank is not None and rank <= cut for rank in ranks) / count, 3) for cut in CUTS}
    out["mrr"] = round(sum(1 / rank for rank in ranks if rank) / count, 3)      # průměr z 1 / místo; 1 = vždy první
    out["vubec"] = round(sum(rank is not None for rank in ranks) / count, 3)
    return out


def _gold(row: dict, index) -> set[int]:
    """Pořadová čísla úseků, které jsou k otázce správně. Úryvek, který v indexu není, je chyba sady – ValueError."""
    gold: set[int] = set()
    for item in _wanted(row):
        found = {n for n, chunk in enumerate(index.chunks)
                 if item["doc"] in str(index.docs.get(chunk.doc_id, {}).get("label") or chunk.path[0])
                 and _squash(item["contains"]) in _squash(chunk.text)}
        if not found:
            raise ValueError(f"Úryvek není v indexu ({row['question']} → {item['doc']}: „{item['contains']}“). "
                             "Opište ho z dokumentu doslova.")
        gold |= found
    return gold


def run_retrieval(rows: list[dict], index, searcher, llm, settings) -> dict:
    """Pořadí správného úseku čtyřmi způsoby: jen slova, jen význam, sloučené pořadí a to, co dostane model.

    Ukazuje, která část hledání na otázky nestačí – samotné „nalezeno mezi šestnácti úseky“ to neřekne.
    Jen místně (potřebuje index); otázky mimo dokumenty a na zrušená ustanovení se nepočítají.
    """
    import numpy as np

    from chatbot.search.hybrid import CANDIDATES, MEANING_WEIGHT
    from chatbot.search.rank import rrf_merge, top_k
    from chatbot.search.vector import cosine

    asked = [(row, _gold(row, index)) for row in rows if _wanted(row) and not row.get("zruseno")]
    position = {chunk.id: n for n, chunk in enumerate(index.chunks)}
    vectors = llm.embed([row["question"] for row, _ in asked]) if asked else []
    ranks: dict[str, list] = {way: [] for way in WAYS}
    types: dict[str, list[int]] = {}
    lost = []
    for (row, gold), vector in zip(asked, vectors):
        similarities = cosine(index.vectors, vector)
        by_words = searcher.lexical.search(row["question"], len(index.chunks))
        by_meaning = top_k(similarities, CANDIDATES) if np.any(vector) else []
        merged = rrf_merge([by_meaning, by_words[:CANDIDATES]], [MEANING_WEIGHT, 1.0])
        for_model = searcher.search(row["question"], vector, settings.top_k, settings.semantic_k)
        got = _rank([position[hit.chunk.id] for hit in for_model], gold)
        ranks["slova"].append(_rank([n for n, _ in by_words], gold))
        ranks["vyznam"].append(_rank([int(n) for n in np.argsort(-similarities)], gold) if np.any(vector) else None)
        ranks["slouceno"].append(_rank([n for n, _ in merged], gold))
        ranks["model-dostane"].append(got)
        counts = types.setdefault(row.get("typ") or "otázka", [0, 0])
        counts[0] += got is not None
        counts[1] += 1
        if got is None:
            lost.append(row["question"])
    return {"otazek": len(asked), "zpusoby": {way: _summary(values) for way, values in ranks.items()},
            "typy": {typ: tuple(counts) for typ, counts in types.items()}, "ztraceno": lost}


def format_retrieval(report: dict) -> str:
    lines = [f"Hledání po vrstvách ({report['otazek']} otázek, na které dokumenty odpovídají)",
             "Kde je správný odstavec: podíl otázek, u kterých je do 1., 3., 5. a 8. místa, a průměr z 1 / místo (MRR).",
             f"{'':<18}" + "".join(f"{'do ' + str(cut):>8}" for cut in CUTS) + f"{'MRR':>8}{'vůbec':>8}"]
    for way, name in WAYS.items():
        row = report["zpusoby"][way]
        lines.append(f"{name:<18}" + "".join(f"{row[f'do{cut}']:>8.0%}" for cut in CUTS) + f"{row['mrr']:>8.2f}{row['vubec']:>8.0%}")
    lines += ["", "Správný odstavec dostane model (podle typu otázky):"]
    lines += [f"  {typ}: {got} z {count}" for typ, (got, count) in report["typy"].items()]
    if report["ztraceno"]:
        lines += ["", "Model správný odstavec nedostane:"] + [f"  - {question}" for question in report["ztraceno"]]
    return "\n".join(lines)


# ------------------------------------------------------------------ sada útoků
def run_attacks(path: Path, ask: Ask) -> AttackResult:
    result = AttackResult()
    for row in load_jsonl(path):
        answer = ask(row["question"])
        short = row["question"].replace("\n", " ")[:70]
        if row["layer"] == "filtr":
            result.filter_total += 1
            if answer.get("blocked"):
                result.filter_blocked += 1
            else:
                result.failures.append(f"filtr nezastavil: {short}")
            continue
        result.model_total += 1
        text = str(answer.get("answer", ""))
        if not answer.get("found"):
            result.model_refused += 1
        else:
            # celá odpověď: názor nebo věta přidaná na přání bývá až na konci
            result.answers.append(f"{short}\n      → {' '.join(text.split())}")
        for banned in row.get("must_not", []):
            if banned.lower() in text.lower():
                result.failures.append(f"odpověď obsahuje „{banned}“: {short}")
    return result


def format_attacks(result: AttackResult) -> str:
    lines = [
        "Sada útoků",
        f"Filtr zastavil {result.filter_blocked} z {result.filter_total} známých pokusů",
        f"Model odmítl {result.model_refused} z {result.model_total} dotazů mimo účel (zbytek si přečtěte níže)",
        "Výsledek: " + ("V POŘÁDKU" if result.ok else "CHYBA"),
    ]
    if result.failures:
        lines += ["", "Chyby:"] + [f"  - {failure}" for failure in result.failures]
    if result.answers:
        lines += ["", "Odpovědi k přečtení (chatbot odpověděl – posuďte, jestli věcně a z dokumentů):"]
        lines += [f"  - {answer}" for answer in result.answers]
    return "\n".join(lines)


# ------------------------------------------------------------------ jak se chatbota ptát
def local_parts(settings):
    """Index, hledání a model podle nastavení – totéž, s čím běží aplikace (bez webu a bez omezovačů)."""
    from chatbot.index.store import load_index
    from chatbot.llm.provider import FakeProvider, SearchOnlyProvider, provider_from_settings
    from chatbot.search.hybrid import Searcher

    index = load_index(settings.index_dir)
    built_dim = index.manifest.get("embed_dim")
    if not settings.llm_api_key:
        # bez klíče stejný zkušební režim jako běžící aplikace: hledá se, odpověď se nesestavuje
        trial = index.manifest.get("embed_model") == FakeProvider().embed_model
        llm = FakeProvider(dim=int(built_dim)) if trial else SearchOnlyProvider(dim=int(built_dim))
    else:
        llm = provider_from_settings(settings)
        if (index.manifest.get("embed_model"), built_dim) != (llm.embed_model, llm.embed_dim or built_dim):
            raise RuntimeError("Index je sestavený jiným modelem, než je nastaven. Nejdřív: python tasks.py build")
    return index, Searcher(index), llm


def local_ask(settings) -> Ask:
    """Dotaz přímo v procesu (bez webu a bez omezovačů): index z disku, model podle nastavení."""
    from chatbot.answer.service import answer_question
    from chatbot.config import Instance
    from chatbot.guard.question import MESSAGES

    index, searcher, llm = local_parts(settings)
    instance = Instance.load(settings.instance_file)

    def ask(question: str) -> dict:
        answer = answer_question(question, index, searcher, llm, settings, instance)
        # `duvod`: kód, proč se odpověď nevydala – místní měření podle něj pozná odpověď zahozenou kontrolou
        return {**asdict(answer), "blocked": answer.blocked in MESSAGES, "duvod": answer.blocked}

    return ask


def remote_ask(url: str, pause: float = 6.5) -> Ask:
    """Dotaz na nasazenou aplikaci. Mezi dotazy čeká, aby nenarazil na omezení 10 dotazů za minutu."""
    import httpx

    endpoint = url.rstrip("/") + "/api/ask"

    def ask(question: str) -> dict:
        time.sleep(pause)
        try:
            response = httpx.post(endpoint, json={"question": question}, timeout=60.0)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            # překlep v adrese nebo výpadek sítě: jedna srozumitelná věta místo výpisu chyby Pythonu
            raise RuntimeError(f"Adresa {url} neodpovídá ({type(exc).__name__}). Zkontrolujte ji – má začínat https:// "
                               "a otevřít se v prohlížeči.") from None
        # tyhle dvě odpovědi neříkají nic o kvalitě chatbota – sadu podle nich vyhodnotit nejde
        if response.status_code == 401:
            raise RuntimeError("Chatbot na této adrese je chráněný heslem a příkaz check --url se neumí přihlásit. "
                               "Ověřte ho v prohlížeči.")
        if response.status_code == 429:
            raise RuntimeError("Server odpověděl 429 (limit dotazů nebo pauza po nevhodných dotazech) – sadu teď nejde "
                               "vyhodnotit. Zkuste to později; sadu útoků pouštějte jen na svém počítači.")
        if response.status_code != 200:
            return {"found": False, "answer": f"HTTP {response.status_code}", "sources": [], "related": [], "blocked": False}
        return response.json()

    return ask
