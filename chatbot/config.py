"""Nastavení chatbota.

Dvě vrstvy:
  `Settings`  – provoz a tajemství (klíč k modelu, heslo, limity). Čte se z proměnných prostředí,
                místně ze souboru `.env`. Přenos jinam (server, hosting) = změna hodnot, ne kódu.
  `Instance`  – čí chatbot to je a jak se představuje (název, organizace, texty, barva, návrhy otázek).
                Čte se ze souboru `chatbot.toml`, který se do repa ukládá – žádné tajemství v něm není.
"""
from __future__ import annotations

import hashlib
import os
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Mapping

# kořen repa (složka nad balíkem `chatbot`)
ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # --- model (rozhraní kompatibilní s OpenAI; lokální model = jiná adresa a názvy) ---
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    chat_model: str = "gpt-4.1-mini"
    # míra „uvažování“ u modelů, které uvažují (gpt-5…, gpt-6…): u gpt-5 nejníž „minimal“, u gpt-6 „none“.
    # Prázdné = rozhodne model – bývá to pomalé a dražší a krátký limit se spotřebuje dřív, než přijde odpověď.
    reasoning_effort: str = ""
    # Větší embeddingový model a rozměr 1024: na sadě běžně položených českých otázek nad vyhláškami byl správný
    # odstavec první podle významu u 68 % otázek (menší model, rozměr 512: 49 %) a model ho dostal u 98 % (93 %).
    # Změna modelu nebo rozměru = sestavit index znovu (python tasks.py build).
    embed_model: str = "text-embedding-3-large"
    embed_dim: int = 1024               # 0 = rozměr určí model sám (pro modely, které zmenšení rozměru neumějí)
    # --- soubory ---
    documents_dir: Path = ROOT / "dokumenty"                  # sem patří dokumenty, ze kterých chatbot odpovídá
    index_dir: Path = ROOT / "data" / "index"                 # hotový index (ukládá se do repa a nasazuje)
    public_dir: Path = ROOT / "public"                        # stránka s chatem
    instance_file: Path = ROOT / "chatbot.toml"               # název, texty, barva
    cache_path: Path = ROOT / "data" / "cache" / "embeddings.npz"   # mezipaměť embeddingů (do repa nepatří)
    feedback_file: Path = ROOT / "data" / "feedback.jsonl"    # zpětná vazba (palce); do repa nepatří
    # --- provoz ---
    rate_limit_per_min: int = 10        # dotazů z jedné adresy za minutu
    max_question_chars: int = 500
    max_answer_tokens: int = 900        # odpověď má být strukturovaná (přímá odpověď, výčet, souvislosti), ne jen pár vět
    min_similarity: float = 0.2         # pod touto podobností se model nevolá („nenašel jsem“)
    top_k: int = 8                      # kolik úseků dostane model jako podklad
    # kolik nejlepších úseků podle významu se k nim přidá vždy, i když ve sloučeném pořadí propadly
    # (odpověď řečená jinými slovy, než jakými se člověk ptá – viz Searcher.search); 0 = nepřidávat
    semantic_k: int = 8
    # --- ochrana proti zneužití (docs/ochrana.md) ---
    max_answer_chars: int = 2400        # delší odpověď modelu se nevydá (strukturovaná odpověď mívá 800–1 600 znaků)
    max_feedback_bytes: int = 12000     # tělo hodnocení (dotaz + odpověď + důvod); větší se odmítne dřív, než se čte
    max_feedback_comment_chars: int = 300
    # nejmenší podíl obsahových slov odpovědi, která jsou v citovaných úsecích. Je to spodní hranice proti odpovědi,
    # která s úseky nesouvisí; smysl ověřuje druhé volání modelu. Bez něj (VERIFY_ANSWER=0) nastavte aspoň 0.5.
    min_grounding: float = 0.2
    # druhé volání modelu: u každé věty hotové odpovědi ověří, že to, co tvrdí, v citovaných úsecích stojí
    # (chatbot/guard/verify.py). Dotaz je s ním asi o polovinu dražší a o vteřinu až dvě delší (měřeno).
    verify_answer: bool = True
    abuse_strikes: int = 3              # kolik pokusů o obcházení nebo vulgarit z jednoho prohlížeče, než přijde pauza
    abuse_window_min: int = 10          # v jakém čase se pokusy sčítají
    abuse_block_min: int = 10           # jak dlouhá pauza následuje
    # strop placených dotazů za hodinu ze všech adres (jedna instance); brzda, ne záruka – tvrdý strop útraty
    # se nastavuje u poskytovatele modelu
    max_asks_per_hour: int = 300
    # kolik placených dotazů za 24 hodin smí přijít z jedné adresy; 0 = bez omezení. Pro veřejnou ukázku
    # s malým rozpočtem. Počítá se v paměti jedné instance – stejná výhrada jako u hodinového stropu.
    daily_asks_per_address: int = 0
    # kolik proxy stojí před aplikací a připisuje adresu tazatele do hlavičky X-Forwarded-For. 0 = hlavičce se
    # nevěří a limity se řídí adresou spojení (klient si hlavičku napíše, jakou chce – viz docs/ochrana.md)
    trusted_proxies: int = 0
    max_body_bytes: int = 4096          # větší tělo požadavku se odmítne dřív, než se čte
    # společné heslo (chatbot/api/access.py); prázdné = chatbot je otevřený. Patří do .env / proměnných hostingu.
    access_password: str = ""
    access_days: int = 30               # jak dlouho si prohlížeč přihlášení pamatuje

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Výchozí hodnoty přepíše proměnná prostředí se stejným názvem velkými písmeny (EMBED_DIM…)."""
        env = os.environ if env is None else env
        values = {}
        for f in fields(cls):
            raw = env.get(f.name.upper())
            if raw is None or raw == "":
                continue
            default = getattr(cls, f.name)
            if isinstance(default, bool):
                values[f.name] = raw.strip().lower() in ("1", "true", "ano", "yes")
            elif isinstance(default, int):
                values[f.name] = int(raw)
            elif isinstance(default, float):
                values[f.name] = float(raw)
            elif isinstance(default, Path):
                values[f.name] = Path(raw)
            else:
                values[f.name] = raw
        return cls(**values)


def load_env_file(path: Path) -> None:
    """Načte soubor `.env` (řádky KLÍČ=hodnota) do prostředí procesu.

    Co už v prostředí je, nepřepisuje – na hostingu a na serveru mají přednost proměnné platformy.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
PII_MODES = ("hlasit", "vynechat")


@dataclass(frozen=True)
class Instance:
    """Čí chatbot to je. Výchozí hodnoty jsou obecné, aby chatbot běžel i bez souboru `chatbot.toml`."""
    nazev: str = "Městský chatbot"
    organizace: str = ""
    popis: str = "Odpovídám z dokumentů, které mám k dispozici, a u každé odpovědi ukážu, kde přesně to v nich stojí."
    upozorneni: str = "Odpověď může obsahovat chyby. Závazné je vždy znění předpisu."
    kontakt: str = ""                   # kam se obrátit, když chatbot odpověď nezná
    # co chatbot řekne, když odpověď v dokumentech není: z čeho odpovídá a kam jít jinam (prázdné = obecná věta)
    nenalezeno: str = ""
    odkaz: str = ""                     # adresa, kde jsou dokumenty zveřejněné
    navod: str = ""                     # adresa návodu, jak si takového chatbota postavit (prázdné = neukazovat)
    barva: str = "#1d4f91"              # hlavní barva stránky
    navrhy: tuple[str, ...] = field(default_factory=tuple)      # návrhy otázek na úvodní stránce
    sbirka_vydavatel: str = ""          # ID datové schránky obce (stažení ze Sbírky právních předpisů)
    osobni_udaje: str = "hlasit"        # hlasit | vynechat – co s dokumentem, ve kterém sestavení najde osobní údaj

    @property
    def leak_marker(self) -> str:
        """Značka vložená do zadání pro model. Když se objeví v odpovědi, model zadání opisuje a odpověď
        se nevydá. Není to tajemství – jen nástraha; liší se podle názvu, aby nebyla u všech stejná."""
        digest = hashlib.sha1(f"{self.nazev}|{self.organizace}".encode("utf-8")).hexdigest()[:8].upper()
        return f"ZADANI-{digest}-NEOPISOVAT"

    @classmethod
    def load(cls, path: Path) -> "Instance":
        """Načte `chatbot.toml`. Chybějící soubor = výchozí hodnoty; nesmyslná hodnota = srozumitelná chyba."""
        path = Path(path)
        if not path.is_file():
            return cls()
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"Soubor {path.name} nejde přečíst: {exc}") from None
        chatbot = data.get("chatbot") or {}
        values: dict = {key: " ".join(str(chatbot[key]).split())
                        for key in ("nazev", "organizace", "popis", "upozorneni", "kontakt", "nenalezeno", "odkaz", "navod")
                        if chatbot.get(key)}
        color = str((data.get("vzhled") or {}).get("barva") or "").strip()
        if color:
            if not _COLOR.fullmatch(color):
                raise ValueError(f"{path.name}: barva má být ve tvaru #rrggbb (například #1d4f91), je „{color}“.")
            values["barva"] = color.lower()
        questions = (data.get("otazky") or {}).get("navrhy") or []
        if not isinstance(questions, list):
            raise ValueError(f"{path.name}: [otazky] navrhy má být seznam otázek v hranatých závorkách.")
        values["navrhy"] = tuple(" ".join(str(q).split()) for q in questions if str(q).strip())
        values["sbirka_vydavatel"] = str((data.get("sbirka") or {}).get("vydavatel") or "").strip()
        mode = str((data.get("osobni_udaje") or {}).get("rezim") or "hlasit").strip().lower()
        if mode not in PII_MODES:
            raise ValueError(f"{path.name}: [osobni_udaje] rezim smí být jen „hlasit“ nebo „vynechat“, je „{mode}“.")
        values["osobni_udaje"] = mode
        return cls(**values)
