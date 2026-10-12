"""Zpětná vazba (palec nahoru / dolů): ověření vstupu, uložení do souboru, náhrada logem, limity, stav."""
import json
import logging
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from chatbot.api.app import create_app
from chatbot.config import Settings
from chatbot.feedback.store import Feedback, FileStore, LogStore, read_all
from chatbot.llm.provider import FakeProvider

QUESTION = "Jaká je sazba poplatku za psa?"
PAYLOAD = {"vote": "down", "reason": "neuplne", "comment": "Chybí výše přídělu.", "question": QUESTION,
           "answer": "Fond se tvoří přídělem [1].", "sources": ["1001:b12", "1001:b13-2"], "found": True}


@pytest.fixture
def settings(small_index_dir, tmp_path):
    return Settings(index_dir=small_index_dir, public_dir=tmp_path / "neni", feedback_file=tmp_path / "zv" / "feedback.jsonl")


def client_for(settings, feedback=None):
    return TestClient(create_app(settings, FakeProvider(dim=256), feedback=feedback))


def test_feedback_is_appended_to_file(settings):
    client = client_for(settings)
    response = client.post("/api/feedback", json=PAYLOAD)
    assert response.status_code == 200 and response.json() == {"ok": True, "stored": "soubor"}
    client.post("/api/feedback", json={**PAYLOAD, "vote": "up", "reason": "", "comment": ""})
    rows = read_all(settings.feedback_file)
    assert [r["vote"] for r in rows] == ["down", "up"]
    first = rows[0]
    assert first["question"] == QUESTION and first["reason"] == "neuplne" and first["comment"] == "Chybí výše přídělu."
    assert first["sources"] == ["1001:b12", "1001:b13-2"] and first["found"] is True
    assert first["mode"] == "ostry" and first["index_date"] == "2026-10-10" and first["ts"]
    assert client.get("/api/status").json()["feedback"] == "soubor"


@pytest.mark.parametrize("bad, message", [
    ({**PAYLOAD, "vote": "maybe"}, "Neplatné hodnocení."),
    ({**PAYLOAD, "reason": "cokoli"}, "Neplatný důvod hodnocení."),
    ({**PAYLOAD, "vote": "up", "reason": "jine"}, "Neplatný důvod hodnocení."),       # důvod patří jen k palci dolů
    ({**PAYLOAD, "comment": "x" * 301}, "Poznámka je příliš dlouhá (nejvýš 300 znaků)."),
    ({**PAYLOAD, "question": "   "}, "Hodnocení bez dotazu nejde uložit."),
    ({**PAYLOAD, "sources": "1001:b12"}, "Neplatné hodnocení."),
    ([], "Neplatné hodnocení."),
])
def test_invalid_feedback_400(settings, bad, message):
    response = client_for(settings).post("/api/feedback", json=bad)
    assert response.status_code == 400 and response.json()["detail"] == message
    assert read_all(settings.feedback_file) == []


def test_feedback_cleans_text_and_drops_odd_sources(settings):
    client = client_for(settings)
    payload = {**PAYLOAD, "question": "Jak\u200b se\ttvoří fond?", "comment": "a\u0007b",
               "sources": ["1001:b12", "../etc", 5, "x" * 200, "1:a"]}
    assert client.post("/api/feedback", json=payload).status_code == 200
    row = read_all(settings.feedback_file)[0]
    assert row["question"] == "Jak se tvoří fond?" and row["comment"] == "ab"
    assert row["sources"] == ["1001:b12", "1:a"]


def test_oversized_feedback_body_is_refused_before_reading(settings):
    client = client_for(settings)
    huge = json.dumps({**PAYLOAD, "answer": "x" * 20000}).encode()
    response = client.post("/api/feedback", content=huge, headers={"content-type": "application/json"})
    assert response.status_code == 400 and response.json()["detail"] == "Hodnocení je příliš dlouhé."
    bad_json = client.post("/api/feedback", content=b"{nope", headers={"content-type": "application/json"})
    assert bad_json.status_code == 400 and bad_json.json()["detail"] == "Neplatné hodnocení."


def test_feedback_has_own_rate_limit(settings):
    client = client_for(replace(settings, rate_limit_per_min=2))
    codes = [client.post("/api/feedback", json=PAYLOAD).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert client.post("/api/ask", json={"question": QUESTION}).status_code == 200      # dotazy mají vlastní počítadlo


def test_feedback_without_index_503(settings, tmp_path):
    client = client_for(replace(settings, index_dir=tmp_path / "prazdny"))
    assert client.post("/api/feedback", json=PAYLOAD).status_code == 503


def test_file_store_falls_back_to_log_when_disk_is_not_writable(tmp_path, caplog):
    blocker = tmp_path / "soubor"
    blocker.write_text("x", encoding="utf-8")
    item = Feedback(vote="up", reason="", comment="", question="Kdo schvaluje dovolenou?", answer="Vedoucí [1].")
    with caplog.at_level(logging.INFO, logger="chatbot"):
        store = FileStore(blocker / "feedback.jsonl")             # rodič je soubor, ne složka → zápis selže
        assert store.kind == "log"                                 # pozná to hned při startu (stav pro /api/status)
        store.add(item)
        store.add(item)
    assert caplog.text.count("stored=log") == 2 and "nejde zapsat" in caplog.text
    assert "dovolenou" not in caplog.text                          # do logu texty nejdou


def test_file_store_reports_file_when_writable(tmp_path):
    store = FileStore(tmp_path / "zv" / "feedback.jsonl")
    assert store.kind == "soubor" and (tmp_path / "zv" / "feedback.jsonl").exists()


def test_log_store_reports_kind_in_status(settings):
    client = client_for(settings, feedback=LogStore())
    assert client.get("/api/status").json()["feedback"] == "log"
    assert client.post("/api/feedback", json=PAYLOAD).json() == {"ok": True, "stored": "log"}
    assert not settings.feedback_file.exists()


def test_api_payload_of_ask_is_unchanged(settings):
    """Hodnocení nic nemění na odpovědi /api/ask (stránka si s ním poradí i beze změny serveru)."""
    body = client_for(settings).post("/api/ask", json={"question": QUESTION}).json()
    assert set(body) == {"found", "answer", "sources", "related", "index_date", "blocked", "mode"}
