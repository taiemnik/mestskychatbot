"""Kontrola kvality: zkušební otázky a sada útoků (příkaz `python tasks.py check`).

Zkušební otázky (eval/otazky.jsonl): otázka + dokument + úryvek správného odstavce. Měří se, jestli
chatbot správný odstavec našel (je mezi zdroji nebo v „mohlo by souviset“) a jestli z něj i citoval.
Sada útoků (eval/utoky.jsonl): pokusy o obcházení a trolling. Vrstva „filtr“ musí být zastavena
vstupním filtrem; u vrstvy „model“ se vypíše, co chatbot odpověděl, k ručnímu přečtení.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

Ask = Callable[[str], dict]      # dotaz → odpověď ve tvaru /api/ask
THRESHOLD = 0.9                  # nejmenší podíl otázek, u kterých musí být správný odstavec nalezen
WITHOUT_MODEL = ("Klíč k modelu není nastavený – měření běží bez modelu: vyhodnotí se jen hledání („Nalezeno“). "
                 "„Citováno“ a práh pro ostrý provoz platí až s modelem.")
WITHOUT_MODEL_ATTACKS = ("Klíč k modelu není nastavený – měření běží bez modelu: vyhodnotí se jen filtr dotazů. "
                         "Jak si s ostatními pokusy poradí model, se ukáže až s klíčem.")


@dataclass
class CheckResult:
    total: int = 0
    retrieved: int = 0           # správný odstavec je ve zdrojích nebo v „mohlo by souviset“
    cited: int = 0               # správný odstavec je přímo mezi zdroji odpovědi
    failures: list[str] = field(default_factory=list)


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


def run_check(golden: Path, ask: Ask) -> CheckResult:
    result = CheckResult()
    for row in load_jsonl(golden):
        result.total += 1
        answer = ask(row["question"])
        sources, related = answer.get("sources") or [], answer.get("related") or []
        cited = _has(sources, row["doc"], row["contains"])
        if cited or _has(related, row["doc"], row["contains"]):
            result.retrieved += 1
            result.cited += cited
        else:
            result.failures.append(f"{row['question']} → čekal jsem {row['doc']}: „{row['contains']}“")
    return result


def passed(result: CheckResult) -> bool:
    return result.total > 0 and result.retrieved / result.total >= THRESHOLD


def format_check(result: CheckResult) -> str:
    lines = [
        "Zkušební otázky",
        f"Nalezeno: {result.retrieved} z {result.total} (správný odstavec je mezi zdroji nebo v „mohlo by souviset“)",
        f"Citováno: {result.cited} z {result.total} (odpověď z něj přímo cituje)",
        f"Práh: {int(THRESHOLD * 100)} % nalezených → " + ("SPLNĚNO" if passed(result) else "NESPLNĚNO"),
    ]
    if result.failures:
        lines += ["", "Nenalezeno:"] + [f"  - {failure}" for failure in result.failures]
    return "\n".join(lines)


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


def local_ask(settings) -> Ask:
    """Dotaz přímo v procesu (bez webu a bez omezovačů): index z disku, model podle nastavení."""
    from chatbot.answer.service import answer_question
    from chatbot.config import Instance
    from chatbot.guard.question import MESSAGES
    from chatbot.index.store import load_index
    from chatbot.llm.provider import FakeProvider, SearchOnlyProvider, provider_from_settings
    from chatbot.search.hybrid import Searcher

    index = load_index(settings.index_dir)
    searcher = Searcher(index)
    instance = Instance.load(settings.instance_file)
    built_dim = index.manifest.get("embed_dim")
    if not settings.llm_api_key:
        # bez klíče stejný zkušební režim jako běžící aplikace: hledá se, odpověď se nesestavuje
        trial = index.manifest.get("embed_model") == FakeProvider().embed_model
        llm = FakeProvider(dim=int(built_dim)) if trial else SearchOnlyProvider(dim=int(built_dim))
    else:
        llm = provider_from_settings(settings)
        if (index.manifest.get("embed_model"), built_dim) != (llm.embed_model, llm.embed_dim or built_dim):
            raise RuntimeError("Index je sestavený jiným modelem, než je nastaven. Nejdřív: python tasks.py build")

    def ask(question: str) -> dict:
        answer = answer_question(question, index, searcher, llm, settings, instance)
        return {**asdict(answer), "blocked": answer.blocked in MESSAGES}

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
