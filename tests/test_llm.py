"""Rozhraní modelu – náhradní poskytovatel pro testy a poskytovatel kompatibilní s OpenAI."""
import json

import httpx
import numpy as np
import pytest

from chatbot.config import Settings
from chatbot.llm.provider import FakeProvider, LLMError, OpenAICompatProvider, provider_from_settings


def test_fake_embed_shape_and_norm():
    vectors = FakeProvider(dim=64).embed(["Dovolená zaměstnance", "x", ""])
    assert vectors.shape == (3, 64)
    assert vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)


def test_fake_similar_texts_closer_than_unrelated():
    a, b, c = FakeProvider().embed(["dovolená zaměstnanců úřadu", "Dovolena zamestnancu", "rozpočet města a pohledávky"])
    assert a @ b > a @ c


def test_fake_chat_script_then_default():
    provider = FakeProvider(script=[{"found": True, "answer": "A [1]", "citations": [1]}])
    assert provider.chat_json("s", "u", 100)["citations"] == [1]
    assert provider.chat_json("s", "u", 100) == {"found": False, "answer": "", "citations": []}
    assert provider.chat_calls == 2


def make_provider(handler, chat_model="chat-x", reasoning_effort=""):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider(base_url="https://llm.test/v1", api_key="klic", chat_model=chat_model,
                                embed_model="embed-x", embed_dim=512, client=client, reasoning_effort=reasoning_effort)


def test_openai_embed_batches_and_normalizes():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.url.path == "/v1/embeddings"
        assert request.headers["authorization"] == "Bearer klic"
        # odpověď schválně v obráceném pořadí – správné pořadí určuje pole index
        data = [{"index": i, "embedding": [3.0, 4.0] + [0.0] * 510} for i in reversed(range(len(body["input"])))]
        return httpx.Response(200, json={"data": data})

    vectors = make_provider(handler).embed([f"text {i}" for i in range(250)])
    assert [len(c["input"]) for c in calls] == [100, 100, 50]
    assert all(c["dimensions"] == 512 and c["model"] == "embed-x" for c in calls)
    assert vectors.shape == (250, 512)
    assert np.allclose(vectors[0, :2], [0.6, 0.8], atol=1e-6)        # vektor normovaný na délku 1


def test_openai_embed_empty_list():
    assert make_provider(lambda request: httpx.Response(500)).embed([]).shape == (0, 512)


def test_openai_chat_json_parses_content():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        assert request.url.path == "/v1/chat/completions"
        content = json.dumps({"found": True, "answer": "Ano [1].", "citations": [1]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

    result = make_provider(handler).chat_json("SYS", "USR", 300)
    assert result == {"found": True, "answer": "Ano [1].", "citations": [1]}
    assert seen["model"] == "chat-x"
    assert seen["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USR"}]
    assert seen["response_format"] == {"type": "json_object"}
    assert seen["max_tokens"] == 300
    assert seen["temperature"] == 0.1


def test_openai_chat_json_accepts_fenced_json():
    def handler(request):
        content = '```json\n{"found": false, "answer": "", "citations": []}\n```'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    assert make_provider(handler).chat_json("s", "u", 100)["found"] is False


def test_reasoning_model_gets_token_headroom():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    make_provider(handler, chat_model="gpt-5-mini").chat_json("s", "u", 300)
    assert "max_tokens" not in seen and "temperature" not in seen
    assert seen["max_completion_tokens"] == 300 + 1500


def test_mira_uvazovani_se_posila_jen_uvazujicim_modelum():
    """Nejlevnější modely OpenAI (gpt-5-nano, gpt-6-luna) „uvažují“: s výchozí mírou spotřebují limit dřív, než
    napíšou odpověď (měřeno: 4 odpovědi z 10 a 10 sekund na dotaz). Míra jde nastavit (REASONING_EFFORT).
    Běžný model parametr nezná a vrátil by chybu 400 – tomu se neposílá."""
    seen = {}

    def handler(request):
        seen.clear()
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    make_provider(handler, chat_model="gpt-5-nano", reasoning_effort="minimal").chat_json("s", "u", 300)
    assert seen["reasoning_effort"] == "minimal" and seen["max_completion_tokens"] == 300 + 1500
    make_provider(handler, chat_model="gpt-5-nano").chat_json("s", "u", 300)
    assert "reasoning_effort" not in seen                           # nenastaveno = rozhodne model
    make_provider(handler, chat_model="gpt-4.1-mini", reasoning_effort="minimal").chat_json("s", "u", 300)
    assert "reasoning_effort" not in seen and seen["max_tokens"] == 300 and seen["temperature"] == 0.1


def test_rada_gpt_6_je_uvazujici():
    """gpt-6-luna odmítá `max_tokens` i vlastní teplotu stejně jako řada gpt-5 (ověřeno na rozhraní 11. 10. 2026)."""
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    make_provider(handler, chat_model="gpt-6-luna", reasoning_effort="none").chat_json("s", "u", 300)
    assert "max_tokens" not in seen and "temperature" not in seen
    assert seen["max_completion_tokens"] == 300 + 1500 and seen["reasoning_effort"] == "none"


def test_openai_http_error_raises_llmerror():
    provider = make_provider(lambda request: httpx.Response(429, json={"error": {"message": "limit"}}))
    with pytest.raises(LLMError):
        provider.chat_json("s", "u", 100)
    with pytest.raises(LLMError):
        provider.embed(["a"])


def test_openai_network_error_raises_llmerror():
    def handler(request):
        raise httpx.ConnectError("síť nedostupná")

    with pytest.raises(LLMError):
        make_provider(handler).chat_json("s", "u", 100)


def test_openai_bad_json_raises_llmerror():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "tohle není JSON"}}]})

    with pytest.raises(LLMError):
        make_provider(handler).chat_json("s", "u", 100)


def test_error_message_does_not_leak_key():
    provider = make_provider(lambda request: httpx.Response(401, json={"error": {"message": "bad key klic"}}))
    with pytest.raises(LLMError) as excinfo:
        provider.embed(["a"])
    assert "klic" not in str(excinfo.value)


def test_provider_from_settings():
    provider = provider_from_settings(Settings(llm_api_key="abc", embed_dim=256))
    assert (provider.embed_model, provider.embed_dim) == ("text-embedding-3-large", 256)
    assert provider.reasoning_effort == ""
    assert provider_from_settings(Settings.from_env({"LLM_API_KEY": "abc", "REASONING_EFFORT": "low"})).reasoning_effort == "low"
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        provider_from_settings(Settings(llm_api_key=""))


def test_openai_retries_once_after_a_dropped_connection():
    # v ostrém provozu občas spadne spojení uprostřed čtení (ReadError) – jedno opakování to spraví
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadError("spojení spadlo")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"found": false}'}}]})

    assert make_provider(handler).chat_json("s", "u", 100) == {"found": False}
    assert calls["n"] == 2


def test_openai_does_not_retry_forever():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadError("spojení spadlo")

    with pytest.raises(LLMError):
        make_provider(handler).chat_json("s", "u", 100)
    assert calls["n"] == 2
