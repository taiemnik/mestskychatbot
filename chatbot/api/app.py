"""Webové rozhraní chatbota (FastAPI).

Běžná aplikace ASGI (`chatbot.api.app:app`): místně a na vlastním serveru ji spouští
`python tasks.py serve`, jinde kterýkoli server nebo hosting, který ASGI umí. Statickou stránku
z `public/` podává sama.

  POST /api/ask        dotaz → odpověď se zdroji
  POST /api/feedback   palec nahoru / dolů k odpovědi (chatbot/feedback/)
  GET  /api/doc/{id}   znění dokumentu po blocích (pro zobrazení zdroje)
  GET  /api/status     stav indexu a to, jestli je potřeba heslo
  POST /api/login      přihlášení společným heslem (chatbot/api/access.py)
  POST /api/logout     odhlášení

Je-li nastavené heslo (`ACCESS_PASSWORD`), vrací dotaz, znění i hodnocení bez přihlášení 401.

Do logu jde za každý dotaz jeden řádek: doba, výsledek, počet zdrojů, případně kód důvodu
odmítnutí. Text dotazu ani adresa tazatele se do logu nezapisují. Zpětná vazba (dotaz, odpověď,
hodnocení) se ukládá jen tam, kde to uživatel sám odešle palcem – bez jména a adresy.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import mimetypes
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from chatbot.answer.service import Answer, answer_question
from chatbot.api.access import Access
from chatbot.api.ratelimit import AbuseTracker, DailyLimiter, HourlyCap, RateLimiter
from chatbot.config import Instance, Settings
from chatbot.feedback.store import Feedback, FeedbackStore, FileStore
from chatbot.guard.question import MESSAGES, check_question
from chatbot.index.model import document_note
from chatbot.index.store import Index, IndexMismatchError, load_index
from chatbot.llm.provider import FakeProvider, LLMError, LLMProvider, SearchOnlyProvider, provider_from_settings
from chatbot.search.hybrid import Searcher

log = logging.getLogger("chatbot")
STRIKE_REASONS = ("obchazeni", "vulgarni")      # které odmítnuté dotazy se počítají do pauzy
_CLIENT_ID = re.compile(r"[A-Za-z0-9-]{8,64}")  # značka prohlížeče z hlavičky X-Client-Id


def _setup_logging() -> None:
    """Zapne řádek „ask ms=… found=…“ (úroveň INFO). Vlastní výstup přidá jen tam, kde žádný není."""
    log.setLevel(logging.INFO)
    if not log.handlers and not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(handler)

BROKEN_INDEX = "Index není úplný nebo si jeho soubory neodpovídají. Sestavte ho znovu: python tasks.py build"
NO_DATA = ("Chatbot nemá načtená data. Sestavte index: python tasks.py build")
DEMO_TEXT = ("Zkušební režim bez modelu: odpověď se nesestavuje. "
             "Níže jsou místa, která hledání v dokumentech našlo.")
JSON_ONLY = "Požadavek musí být ve formátu JSON (hlavička Content-Type: application/json)."
DAILY_LIMIT_TEXT = ("Denní limit dotazů z jedné adresy ({limit}) je vyčerpaný – chatbot má omezený rozpočet. "
                    "Zkuste to prosím zítra.")
# hlavičky na každé odpovědi: stránka se nesmí vkládat do cizích rámů a načítá jen vlastní soubory
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"),
}


@dataclass
class State:
    """Co aplikace drží v paměti: index, hledání, model a případná chyba, proč neodpovídá."""
    index: Index | None = None
    searcher: Searcher | None = None
    llm: LLMProvider | None = None
    mode: str = "ostry"             # ostry | zkusebni (bez klíče k modelu: jen hledání)
    error: str | None = None
    instance: Instance = field(default_factory=Instance)


def _load_state(settings: Settings, llm: LLMProvider | None) -> State:
    try:
        instance = Instance.load(settings.instance_file)
    except ValueError as exc:
        return State(error=str(exc))
    try:
        index = load_index(settings.index_dir)
    except IndexMismatchError as exc:
        # podrobnosti (i s cestou na disku) jen do logu serveru; stav je veřejný
        log.warning("index: %s", exc)
        return State(error=BROKEN_INDEX if (settings.index_dir / "manifest.json").is_file() else NO_DATA, instance=instance)
    state = State(index=index, searcher=Searcher(index), llm=llm, instance=instance)
    if llm is not None:
        return state
    dim = int(index.manifest["embed_dim"])
    if not settings.llm_api_key:
        # bez klíče se odpověď nesestavuje, jen se ukážou nalezená místa:
        # nad zkušebním indexem hledají náhradní embeddingy, nad ostrým indexem jen slova
        trial = index.manifest.get("embed_model") == FakeProvider().embed_model
        state.llm = FakeProvider(dim=dim) if trial else SearchOnlyProvider(dim=dim)
        state.mode = "zkusebni"
        return state
    provider = provider_from_settings(settings)
    built_with = (index.manifest.get("embed_model"), dim)
    expected = (provider.embed_model, provider.embed_dim or dim)    # EMBED_DIM=0: rozměr určil model při sestavení
    if built_with != expected:
        # dotaz by se převáděl jiným modelem než úseky – podobnost by byla náhodná
        state.error = (f"Index byl sestaven jiným modelem ({built_with[0]}, rozměr {built_with[1]}), než je nastaven "
                       f"({expected[0]}, rozměr {expected[1]}). Sestavte index znovu: python tasks.py build")
        return state
    state.llm = provider
    return state


def _plain_address(value: str) -> str | None:
    """Adresa z hlavičky v jednotném tvaru, bez portu. Co adresa není („unknown“, libovolný text), vrací None.

    Některé proxy připisují adresu i s portem („203.0.113.5:50001“, „[2001:db8::1]:50001“) a port je u každého
    spojení jiný – jako klíč omezovače by taková hodnota nepočítala nic.
    """
    text = value.strip()
    if text.startswith("["):                    # IPv6 v hranatých závorkách, případně s portem
        text = text[1:].partition("]")[0]
    elif text.count(":") == 1:                  # IPv4 s portem (samotná IPv6 má dvojteček víc)
        text = text.partition(":")[0]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def _client_key(request: Request, trusted_proxies: int) -> str:
    """Adresa tazatele pro omezovače.

    Výchozí je adresa spojení. Hlavičku X-Forwarded-For si klient napíše, jakou chce – věří se jí jen tehdy, když
    provozovatel řekl, kolik proxy před aplikací stojí (`TRUSTED_PROXIES`). Každá proxy připisuje adresu, kterou
    sama viděla, na konec seznamu; adresa tazatele je proto tolikátá odzadu, kolik je proxy, a co stojí před ní,
    napsal klient. Je-li v hlavičce adres míň nebo na tom místě adresa není, platí adresa spojení.

    Předpoklad, který kód neověřuje: k aplikaci se nikdo nedostane jinudy než přes ty proxy. Kdo by se připojil
    přímo, napsal by si hlavičku celou sám (docs/nasazeni.md, „Adresa tazatele a proxy“).
    """
    direct = request.client.host if request.client else "neznama"
    if trusted_proxies <= 0:
        return direct
    # hlavička smí přijít ve více řádcích – čtou se jako jeden seznam za sebou
    hops = [part.strip() for part in ",".join(request.headers.getlist("x-forwarded-for")).split(",") if part.strip()]
    if len(hops) < trusted_proxies:
        return direct
    return _plain_address(hops[-trusted_proxies]) or direct


def _browser_key(request: Request, address: str) -> str:
    """Klíč pro pauzu po pokusech o obcházení: adresa + značka prohlížeče (hlavička X-Client-Id).

    Lidé z jedné organizace vycházejí na internet jednou adresou – pauza vázaná jen na adresu by za
    jednoho vtipálka potrestala všechny. Značku si stránka vymyslí sama a drží ji v prohlížeči; kdo ji
    střídá, pauze unikne, ale limit dotazů za minutu z adresy platí dál. Bez značky platí pauza pro adresu.
    """
    client = request.headers.get("x-client-id", "")
    return f"{address}|{client}" if _CLIENT_ID.fullmatch(client) else address


def _require_json(request: Request) -> None:
    """Tělo požadavku se přijímá jen s hlavičkou `Content-Type: application/json`.

    Stránka ji posílá vždy. Formulář nebo skript na cizím webu umí bez svolení serveru poslat jen prostý text
    nebo data formuláře – bez téhle kontroly by cizí stránka mohla z prohlížečů svých návštěvníků posílat
    dotazy a čerpat hodinový strop (a tím i peníze)."""
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, JSON_ONLY)


async def _read_question(request: Request, settings: Settings) -> str:
    """Přečte a zkontroluje dotaz z těla požadavku. Chyba = 400 s českou hláškou."""
    too_long = HTTPException(400, f"Dotaz je příliš dlouhý (nejvýš {settings.max_question_chars} znaků).")
    empty = HTTPException(400, "Napište prosím dotaz.")
    declared = request.headers.get("content-length", "0")
    if declared.isdigit() and int(declared) > settings.max_body_bytes:
        raise too_long                                  # velké tělo se ani nečte
    body = await request.body()
    if len(body) > settings.max_body_bytes:
        raise too_long
    try:
        data = json.loads(body)
    except ValueError:
        raise empty from None
    question = data.get("question") if isinstance(data, dict) else None
    if not isinstance(question, str) or not question.strip():
        raise empty
    if len(question.strip()) > settings.max_question_chars:
        raise too_long
    return question.strip()


def _payload(answer: Answer, state: State) -> dict:
    """Odpověď pro stránku. Kód důvodu odmítnutí ven nejde – jen příznak, že dotaz filtr nepustil."""
    refused = answer.blocked in MESSAGES            # odmítl vstupní filtr (ne kontrola odpovědi modelu)
    text = DEMO_TEXT if state.mode == "zkusebni" and not answer.found and not refused else answer.answer
    return {
        "found": answer.found,
        "answer": text,
        "sources": [asdict(s) for s in answer.sources],
        "related": [asdict(r) for r in answer.related],
        "index_date": state.index.manifest.get("built") if state.index else None,
        "blocked": refused,
        # „bez-modelu“: model právě není dostupný (výpadek, vyčerpaný kredit) – stránka ukáže nalezená místa
        "mode": "bez-modelu" if answer.degraded else state.mode,
    }


class Limits:
    """Omezovače pohromadě: dotazy za minutu z adresy, pauza po pokusech o obcházení, hodinový strop,
    a zvlášť počítadlo pro zpětnou vazbu (hodnocení nemá ukrajovat z limitu dotazů)."""

    def __init__(self, settings: Settings):
        self.block_min = settings.abuse_block_min
        self.limiter = RateLimiter(settings.rate_limit_per_min)
        self.feedback = RateLimiter(settings.rate_limit_per_min)
        self.abuse = AbuseTracker(settings.abuse_strikes, settings.abuse_window_min * 60, settings.abuse_block_min * 60)
        self.cap = HourlyCap(settings.max_asks_per_hour)
        self.daily = DailyLimiter(settings.daily_asks_per_address) if settings.daily_asks_per_address > 0 else None

    def check(self, address: str, browser: str, now: float) -> None:
        """Vyhodí 429 s českou hláškou, když je prohlížeč v pauze nebo adresa překročila limit za minutu."""
        if self.abuse.blocked(browser, now):
            raise HTTPException(429, f"Příliš mnoho nevhodných dotazů. Zkuste to znovu za {self.block_min} minut.")
        if not self.limiter.allow(address, now):
            raise HTTPException(429, "Příliš mnoho dotazů, zkuste to za chvíli.")

    def spend(self, address: str, now: float) -> None:
        """Denní limit adresy a hodinový strop se počítají až u dotazu, který projde kontrolou i filtrem a bude
        stát peníze. Nejdřív adresa: kdo má vyčerpáno a zkouší to dál, nesmí ostatním ubírat ze společného stropu."""
        if self.daily and self.daily.used(address, now) >= self.daily.limit:
            raise HTTPException(429, DAILY_LIMIT_TEXT.format(limit=self.daily.limit))
        if not self.cap.allow(now):
            raise HTTPException(429, "Chatbot je teď hodně vytížený, zkuste to prosím později.")
        if self.daily:
            self.daily.add(address, now)

    def strike(self, browser: str, reason: str | None, now: float) -> None:
        """Do pauzy se počítá jen zjevná zlá vůle. Nesmysl („PO“, „42“) nebo prosba o vtip prohřešek není."""
        if reason in STRIKE_REASONS:
            self.abuse.strike(browser, now)


def _require_ready(state: State) -> None:
    if state.index is None or state.error:
        raise HTTPException(503, "Chatbot teď nemá načtená data.")


def _add_guards(app: FastAPI) -> None:
    """Bezpečnostní hlavičky na každé odpovědi a jednotná hláška, když model neodpoví."""

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(LLMError)
    async def llm_failed(request: Request, exc: LLMError):
        log.warning("ask model-error")                  # bez podrobností: zpráva chyby může nést cizí text
        return JSONResponse({"detail": "Model teď neodpovídá, zkuste to prosím znovu."}, status_code=502)


def _add_ask_route(app: FastAPI, state: State, settings: Settings, limits: Limits, access: Access) -> None:
    @app.post("/api/ask")
    async def ask(request: Request) -> dict:
        started = time.monotonic()
        _require_json(request)
        access.require(request)
        _require_ready(state)
        address = _client_key(request, settings.trusted_proxies)
        browser = _browser_key(request, address)
        limits.check(address, browser, started)
        question = await _read_question(request, settings)
        verdict = check_question(question)
        if verdict.ok:
            limits.spend(address, started)              # teprve teď jde o dotaz, který něco stojí
            answer = await run_in_threadpool(answer_question, question, state.index, state.searcher, state.llm,
                                             settings, state.instance)
        else:
            limits.strike(browser, verdict.reason, started)
            answer = Answer(found=False, answer=verdict.message or "", blocked=verdict.reason)
        log.info("ask ms=%d found=%s sources=%d blocked=%s", (time.monotonic() - started) * 1000,
                 answer.found, len(answer.sources), answer.blocked or "-")
        return _payload(answer, state)


async def _read_feedback(request: Request, settings: Settings) -> object:
    """Přečte tělo hodnocení jako JSON. Větší tělo než limit se ani nečte."""
    too_big = HTTPException(400, "Hodnocení je příliš dlouhé.")
    declared = request.headers.get("content-length", "0")
    if declared.isdigit() and int(declared) > settings.max_feedback_bytes:
        raise too_big
    body = await request.body()
    if len(body) > settings.max_feedback_bytes:
        raise too_big
    try:
        return json.loads(body)
    except ValueError:
        raise HTTPException(400, "Neplatné hodnocení.") from None


def _add_feedback_route(app: FastAPI, state: State, settings: Settings, limits: Limits, store: FeedbackStore,
                        access: Access) -> None:
    @app.post("/api/feedback")
    async def feedback(request: Request) -> dict:
        """Palec nahoru / dolů k odpovědi. Záznam nese dotaz, odpověď, citované úseky, důvod a poznámku –
        nic o tazateli. Kam se ukládá, říká `stored` (soubor | log)."""
        _require_json(request)
        access.require(request)
        _require_ready(state)
        address = _client_key(request, settings.trusted_proxies)
        if not limits.feedback.allow(address, time.monotonic()):
            raise HTTPException(429, "Příliš mnoho hodnocení, zkuste to za chvíli.")
        data = await _read_feedback(request, settings)
        try:
            item = Feedback.from_payload(data, max_comment_chars=settings.max_feedback_comment_chars, mode=state.mode,
                                         index_date=state.index.manifest.get("built") if state.index else None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        store.add(item)
        return {"ok": True, "stored": store.kind}


def _is_https(request: Request) -> bool:
    """Běží stránka přes https? (Za proxy to říká hlavička X-Forwarded-Proto.)"""
    return request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip() == "https"


def _add_access_routes(app: FastAPI, settings: Settings, access: Access) -> None:
    @app.post("/api/login")
    async def login(request: Request) -> JSONResponse:
        """Přihlášení společným heslem. Úspěch nastaví cookie; heslo se nikam nezapisuje ani neloguje."""
        if not access.enabled:
            return JSONResponse({"ok": True})
        _require_json(request)
        declared = request.headers.get("content-length", "0")
        if declared.isdigit() and int(declared) > settings.max_body_bytes:
            raise HTTPException(400, "Neplatné přihlášení.")
        try:
            data = json.loads((await request.body())[: settings.max_body_bytes])
        except ValueError:
            data = None
        access.login(data.get("password") if isinstance(data, dict) else None, _client_key(request, settings.trusted_proxies),
                     time.monotonic())
        response = JSONResponse({"ok": True})
        access.set_cookie(response, secure=_is_https(request))  # na https jen po https; místně (http) i bez něj
        return response

    @app.post("/api/logout")
    def logout() -> JSONResponse:
        response = JSONResponse({"ok": True})
        access.clear_cookie(response)
        return response


def _add_read_routes(app: FastAPI, state: State, store: FeedbackStore, access: Access, daily_limit: int = 0) -> None:
    @app.get("/api/doc/{doc_id}")
    def doc(request: Request, doc_id: int) -> dict:
        """Znění dokumentu po blocích s kotvami – stránka podle kotvy zvýrazní citovaný odstavec."""
        access.require(request)
        _require_ready(state)
        record = state.index.docs.get(doc_id)
        if record is None:
            raise HTTPException(404, "Tento dokument chatbot nemá.")
        # upozornění u dokumentu se počítá teď (předpis, který ještě není účinný, se pozná podle dnešního data)
        meta = {**record["meta"], "poznamka": document_note(record["meta"], date.today().isoformat())}
        return {"id": record["id"], "label": record["label"], "meta": meta, "blocks": record["blocks"],
                "flags": record.get("flags", [])}

    @app.get("/api/status")
    def status(request: Request) -> dict:
        """Stav indexu: kdy byl sestaven a kolik toho obsahuje. Slouží i jako rychlá kontrola po nasazení.
        `password` říká, jestli je zapnuté heslo, `locked`, jestli se tento prohlížeč musí teprve přihlásit,
        `daily_limit`, kolik placených dotazů denně smí přijít z jedné adresy (0 = bez omezení)."""
        gate = {"password": access.enabled, "locked": not access.allowed(request), "daily_limit": daily_limit}
        if state.index is None or state.error:
            return {"ok": False, "built": None, "docs": 0, "chunks": 0, "mode": state.mode, "error": state.error or NO_DATA,
                    "feedback": store.kind, **gate}
        return {"ok": True, "built": state.index.manifest.get("built"), "docs": len(state.index.docs),
                "chunks": len(state.index.chunks), "mode": state.mode, "error": None, "feedback": store.kind, **gate}


def _fix_mime_types() -> None:
    """Typy souborů stránky napevno. Windows je bere z registru a na některých počítačích má u `.js`
    „text/plain“ – prohlížeč by pak skript kvůli hlavičce `nosniff` nespustil a stránka by zůstala prázdná."""
    for extension, kind in ((".js", "text/javascript"), (".css", "text/css"), (".svg", "image/svg+xml"),
                            (".json", "application/json")):
        mimetypes.add_type(kind, extension)


def create_app(settings: Settings | None = None, llm: LLMProvider | None = None,
               feedback: FeedbackStore | None = None) -> FastAPI:
    """Sestaví aplikaci. `llm` a `feedback` jde podstrčit v testech; jinak se berou z nastavení."""
    settings = settings or Settings.from_env()
    _setup_logging()
    _fix_mime_types()
    app = FastAPI(title="Městský chatbot", docs_url=None, redoc_url=None, openapi_url=None)
    state = _load_state(settings, llm)
    store = feedback or FileStore(settings.feedback_file)
    limits = Limits(settings)
    access = Access(settings.access_password, days=settings.access_days)
    _add_guards(app)
    _add_access_routes(app, settings, access)
    _add_ask_route(app, state, settings, limits, access)
    _add_feedback_route(app, state, settings, limits, store, access)
    _add_read_routes(app, state, store, access, settings.daily_asks_per_address)
    # statická stránka: jen když složka existuje (kdo ji podává jinak, složku na server nedává)
    if settings.public_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.public_dir, html=True), name="public")
    return app


# instance pro server ASGI (`uvicorn chatbot.api.app:app`, `python tasks.py serve`)
app = create_app()
