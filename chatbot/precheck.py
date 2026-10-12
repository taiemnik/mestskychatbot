"""Kontrola před zveřejněním (`python tasks.py kontrola`).

Projde to, co se při nasazení nejčastěji pokazí: tajemství omylem uložené v souboru, zkušební
index místo ostrého, osobní údaje v dokumentech, zastaralé texty stránky. Nic neopravuje – jen
vypíše, co je v pořádku (OK), na co se má člověk podívat (POZOR) a s čím se ven nesmí (CHYBA).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from chatbot.config import Instance, Settings
from chatbot.index.pii import has_findings, scan_pii
from chatbot.index.report import PII_TEXT
from chatbot.llm.provider import FakeProvider

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "cache"}
MAX_SCAN_BYTES = 2_000_000
# tvary běžných klíčů a tokenů; hledá se tvar, hodnota se nikdy nevypisuje
SECRETS = {
    "klíč k modelu (sk-…)": re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}"),
    "token GitHubu": re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{30,}"),
    "klíč AWS": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "soukromý klíč": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "token Slacku": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}"),
    # mezery jen na témže řádku: prázdná hodnota („LLM_API_KEY=“ ve vzoru) nesmí přeskočit na další řádek
    "vyplněný klíč v souboru": re.compile(r"^[ \t]*(?:LLM_API_KEY|OPENAI_API_KEY|ACCESS_PASSWORD)[ \t]*=[ \t]*\S{6,}", re.M),
}


@dataclass
class Precheck:
    lines: list[tuple[str, str]] = field(default_factory=list)      # (OK | POZOR | CHYBA, text)

    def add(self, level: str, text: str) -> None:
        self.lines.append((level, text))

    @property
    def ok(self) -> bool:
        return not any(level == "CHYBA" for level, _ in self.lines)


def _published_files(root: Path):
    """Soubory, které by šly do repa nebo na hosting: vše kromě `.env*`, mezipaměti a vývojových složek."""
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.name.startswith(".env") or path.name == "feedback.jsonl":
            continue
        yield path, relative.as_posix()


def _secrets(root: Path, result: Precheck) -> None:
    hits = []
    for path, relative in _published_files(root):
        if path.stat().st_size > MAX_SCAN_BYTES or path.suffix.lower() in (".bin", ".pdf", ".docx", ".png", ".jpg", ".npz"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # vzor nastavení (env.vzor.txt) se kontroluje také: je v repu, a kdo klíč omylem vyplní do něj, pošle ho ven
        hits += [f"{relative} – {name}" for name, pattern in SECRETS.items() if pattern.search(text)]
    if hits:
        result.add("CHYBA", "V souborech je něco, co vypadá jako tajemství (patří jen do .env nebo do nastavení hostingu): "
                   + "; ".join(hits))
    else:
        result.add("OK", "V souborech není nic, co by vypadalo jako klíč nebo heslo.")
    ignore = (root / ".gitignore").read_text(encoding="utf-8") if (root / ".gitignore").is_file() else ""
    if ".env" not in ignore:
        result.add("CHYBA", "Soubor .gitignore neobsahuje „.env“ – klíč by se mohl dostat do repa.")


def _index(settings: Settings, instance: Instance, result: Precheck) -> None:
    manifest_path = settings.index_dir / "manifest.json"
    if not manifest_path.is_file():
        result.add("CHYBA", "Index neexistuje. Sestavte ho: python tasks.py build")
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("embed_model") == FakeProvider().embed_model:
        result.add("CHYBA", "Index je zkušební (build --fake). Sestavte ostrý: python tasks.py build")
    else:
        result.add("OK", f"Index: {manifest.get('docs')} dokumentů, {manifest.get('chunks')} úseků, sestaven {manifest.get('built')} "
                   f"modelem {manifest.get('embed_model')}.")
    settings_js = settings.public_dir / "nastaveni.js"
    current = settings_js.read_text(encoding="utf-8") if settings_js.is_file() else ""
    expected = [json.dumps(value, ensure_ascii=False) for value in (instance.nazev, instance.organizace, instance.popis, instance.barva)]
    if not all(value in current for value in expected):
        result.add("CHYBA", "Texty stránky neodpovídají souboru chatbot.toml. Spusťte znovu: python tasks.py build")
    try:
        shown = json.loads((settings.public_dir / "otazky.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        shown = None
    if shown != list(instance.navrhy):
        result.add("CHYBA", "Návrhy otázek na stránce neodpovídají souboru chatbot.toml. Spusťte znovu: python tasks.py build")
    try:
        documents = json.loads((settings.index_dir / "documents.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    findings = []
    for doc in documents:
        parts = [str(v) for v in (doc.get("meta") or {}).values() if isinstance(v, str)]
        parts += [json.dumps(doc.get("blocks", []), ensure_ascii=False)]
        counts = scan_pii("\n".join(parts))
        if has_findings(counts):
            findings.append(f"{doc.get('label')} ({', '.join(f'{PII_TEXT.get(k, k)} {v}×' for k, v in counts.items() if v)})")
    if findings:
        result.add("POZOR", "V dokumentech jsou možné osobní údaje – ověřte, že smějí být veřejně: " + "; ".join(findings))
    else:
        result.add("OK", "V dokumentech kontrola nenašla osobní údaje (je to síto, ne záruka).")


def run_precheck(root: Path, settings: Settings) -> Precheck:
    result = Precheck()
    _secrets(root, result)
    try:
        instance = Instance.load(settings.instance_file)
    except ValueError as exc:
        result.add("CHYBA", str(exc))
        return result
    _index(settings, instance, result)
    if not instance.organizace:
        result.add("POZOR", "V chatbot.toml není vyplněná organizace – stránka neříká, čí dokumenty to jsou.")
    if not instance.navrhy:
        result.add("POZOR", "V chatbot.toml nejsou návrhy otázek – úvodní stránka nikomu nenapoví, na co se ptát.")
    golden = root / "eval" / "otazky.jsonl"
    count = len([line for line in golden.read_text(encoding="utf-8").splitlines() if line.strip()]) if golden.is_file() else 0
    if count:
        result.add("OK", f"Zkušebních otázek: {count}. Před nasazením je pusťte: python tasks.py check")
    else:
        result.add("POZOR", "Nemáte zkušební otázky (eval/otazky.jsonl) – nevíte, jestli chatbot odpovídá správně.")
    if settings.access_password:
        result.add("OK", "Místně je nastavené heslo. Na hostingu ho zadejte jako proměnnou ACCESS_PASSWORD.")
    else:
        result.add("POZOR", "Heslo není nastavené: chatbot bude otevřený komukoli. U veřejných předpisů je to v pořádku.")
    result.add("POZOR", "U poskytovatele modelu nastavte strop útraty – je to jediná tvrdá pojistka peněz.")
    return result


def format_precheck(result: Precheck) -> str:
    lines = ["Kontrola před zveřejněním"] + [f"  [{level}] {text}" for level, text in result.lines]
    lines.append("")
    lines.append("Výsledek: " + ("lze pokračovat (projděte si řádky POZOR)" if result.ok else "NEZVEŘEJŇOVAT – nejdřív vyřešte řádky CHYBA"))
    return "\n".join(lines)
