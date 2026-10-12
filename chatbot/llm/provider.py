"""Rozhraní modelu – jediné místo, kde chatbot volá jazykový model.

Dva úkoly: embeddingy (text → vektor pro hledání podle významu) a odpověď ve tvaru JSON.
`OpenAICompatProvider` mluví protokolem OpenAI, který nabízejí i jiní poskytovatelé a běžné lokální
servery modelů – přechod na jiný nebo lokální model je proto změna adresy, klíče a názvů modelů
v nastavení (`LLM_BASE_URL`, `LLM_API_KEY`, `CHAT_MODEL`, `EMBED_MODEL`, `EMBED_DIM`).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Protocol

import httpx
import numpy as np

from chatbot.config import Settings

EMBED_BATCH = 100               # kolik textů v jednom požadavku na embeddingy
REASONING_PREFIXES = ("gpt-5", "gpt-6", "o1", "o3", "o4")
REASONING_HEADROOM = 1500       # rezerva tokenů na skryté uvažování (bez ní se stává, že odpověď přijde prázdná)


class LLMError(Exception):
    """Model neodpověděl nebo odpověděl nečitelně. Zpráva nikdy neobsahuje klíč ani tělo odpovědi."""


class LLMFormatError(LLMError):
    """Model odpověděl, ale ne ve tvaru, o který byl požádán (není to JSON, chybí obsah).

    Na rozdíl od výpadku sítě to není důvod k hlášce „model neodpovídá“ – chatbot řekne
    „nenašel jsem“ a nabídne nalezená místa.
    """


class LLMProvider(Protocol):
    embed_model: str
    embed_dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        """Vektory tvaru (n, embed_dim), float32, každý řádek normovaný na délku 1."""

    def chat_json(self, system: str, user: str, max_tokens: int) -> dict:
        """Odpověď modelu jako slovník (model je požádán o JSON)."""


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class FakeProvider:
    """Náhradní poskytovatel pro testy a pro sestavení bez klíče (`build --fake`).

    Embedding je „pytel slov“: každé slovo (bez diakritiky, zkrácené na 6 znaků) přičte jedničku
    na pozici danou otiskem slova. Texty se společnými slovy tak vyjdou blízko. Nic nevolá.
    """

    def __init__(self, dim: int = 64, script: list[dict] | None = None):
        self.embed_model = "fake-bag-of-words"
        self.embed_dim = dim
        self.script = list(script or [])
        self.chat_calls = 0

    def embed(self, texts: list[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.embed_dim), dtype=np.float32)
        for row, text in enumerate(texts):
            plain = unicodedata.normalize("NFKD", text.lower())
            plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
            for word in re.findall(r"[a-z0-9]{2,}", plain):
                digest = hashlib.md5(word[:6].encode()).digest()
                matrix[row, int.from_bytes(digest[:4], "little") % self.embed_dim] += 1.0
            if not matrix[row].any():
                matrix[row, 0] = 1.0            # prázdný text: aby vektor nebyl nulový
        return _normalize_rows(matrix)

    def chat_json(self, system: str, user: str, max_tokens: int) -> dict:
        self.chat_calls += 1
        if self.script:
            return self.script.pop(0)
        return {"found": False, "answer": "", "citations": []}


class SearchOnlyProvider:
    """Běh bez modelu (chybí klíč): žádné embeddingy ani odpovědi, hledání pak jede jen podle slov.

    Embedding dotazu je nulový vektor – hledání podle něj pozná, že význam hledat nemá.
    """

    def __init__(self, dim: int):
        self.embed_model = "bez-modelu"
        self.embed_dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), self.embed_dim), dtype=np.float32)

    def chat_json(self, system: str, user: str, max_tokens: int) -> dict:
        return {"found": False, "answer": "", "citations": []}


class OpenAICompatProvider:
    def __init__(self, base_url: str, api_key: str, chat_model: str, embed_model: str, embed_dim: int,
                 timeout: float = 20.0, client: httpx.Client | None = None, reasoning_effort: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.embed_dim = embed_dim
        self.reasoning_effort = reasoning_effort    # jen pro uvažující modely; prázdné = rozhodne model
        self.client = client or httpx.Client(timeout=timeout)

    def _post(self, path: str, payload: dict) -> dict:
        """Jeden požadavek na model. Jakékoli selhání převede na LLMError bez citlivých údajů."""
        response = None
        for attempt in (1, 2):
            try:
                response = self.client.post(f"{self.base_url}{path}", json=payload,
                                            headers={"Authorization": f"Bearer {self.api_key}"})
                break
            except httpx.TransportError as exc:
                # spadlé spojení (ne vypršený čas) se jednou zkusí znovu – stává se to a uživatel by jinak viděl chybu
                if attempt == 2 or isinstance(exc, httpx.TimeoutException):
                    raise LLMError(f"Model je nedostupný ({type(exc).__name__}).") from None
            except httpx.HTTPError as exc:
                raise LLMError(f"Model je nedostupný ({type(exc).__name__}).") from None
        if response.status_code >= 400:
            raise LLMError(f"Model vrátil chybu {response.status_code}.")
        try:
            return response.json()
        except ValueError:
            raise LLMError("Model vrátil nečitelnou odpověď.") from None

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.embed_dim), dtype=np.float32)
        rows: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start:start + EMBED_BATCH]
            payload = {"model": self.embed_model, "input": batch}
            if self.embed_dim > 0:
                # zmenšený rozměr šetří místo; modely, které ho neumějí, se nastaví EMBED_DIM=0 (rozměr určí model)
                payload["dimensions"] = self.embed_dim
            data = self._post("/embeddings", payload)
            try:
                items = sorted(data["data"], key=lambda item: item["index"])     # pořadí určuje pole index
                rows.extend(item["embedding"] for item in items)
            except (KeyError, TypeError):
                raise LLMError("Model vrátil embeddingy v neznámém tvaru.") from None
            if len(items) != len(batch):
                raise LLMError("Model vrátil jiný počet embeddingů, než kolik dostal textů.")
        try:
            matrix = np.asarray(rows, dtype=np.float32)
        except ValueError:
            raise LLMError("Model vrátil embeddingy různé délky.") from None
        if matrix.ndim != 2 or (self.embed_dim > 0 and matrix.shape[1] != self.embed_dim):
            raise LLMError("Model vrátil embeddingy jiného rozměru, než je nastaven (EMBED_DIM).")
        self.embed_dim = int(matrix.shape[1])
        return _normalize_rows(matrix)

    def chat_json(self, system: str, user: str, max_tokens: int) -> dict:
        payload = {
            "model": self.chat_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        if self.chat_model.startswith(REASONING_PREFIXES):
            # uvažující modely: jiný název limitu, rezerva na uvažování, teplotu nepřijímají
            payload["max_completion_tokens"] = max_tokens + REASONING_HEADROOM
            if self.reasoning_effort:
                # na vyhledání odpovědi v podkladu stačí nejnižší míra; výchozí je pomalá a umí limit spotřebovat celý
                payload["reasoning_effort"] = self.reasoning_effort
        else:
            payload["max_tokens"] = max_tokens
            payload["temperature"] = 0.1
        data = self._post("/chat/completions", payload)
        try:
            content = data["choices"][0]["message"]["content"] or ""
            if not isinstance(content, str):
                raise TypeError("obsah odpovědi není text")
        except (KeyError, IndexError, TypeError):
            raise LLMFormatError("Model vrátil odpověď v neznámém tvaru.") from None
        # některé modely balí JSON do ```json … ```
        content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content.strip())
        try:
            parsed = json.loads(content)
        except ValueError:
            raise LLMFormatError("Model nevrátil platný JSON.") from None
        if not isinstance(parsed, dict):
            raise LLMFormatError("Model nevrátil objekt JSON.")
        return parsed


def provider_from_settings(settings: Settings) -> LLMProvider:
    if not settings.llm_api_key:
        raise LLMError("Chybí LLM_API_KEY (soubor .env nebo proměnná prostředí).")
    return OpenAICompatProvider(base_url=settings.llm_base_url, api_key=settings.llm_api_key,
                                chat_model=settings.chat_model, embed_model=settings.embed_model,
                                embed_dim=settings.embed_dim, reasoning_effort=settings.reasoning_effort)
