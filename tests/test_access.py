"""Společné heslo (chatbot/api/access.py): bez přihlášení 401, cookie s podpisem, pauza po špatných pokusech."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from chatbot.api.access import COOKIE, Access
from chatbot.api.app import create_app
from chatbot.config import Settings
from chatbot.llm.provider import FakeProvider

TEST_PASSWORD = "zkusebni-heslo-jen-pro-testy"
QUESTION = "Jaká je sazba poplatku za psa?"


@pytest.fixture
def settings(small_index_dir, tmp_path):
    return Settings(index_dir=small_index_dir, public_dir=tmp_path / "neni", min_similarity=0.05,
                    feedback_file=tmp_path / "zv" / "feedback.jsonl", access_password=TEST_PASSWORD)


def client_for(settings) -> TestClient:
    return TestClient(create_app(settings, FakeProvider(dim=256)))


def first_doc_id(client: TestClient) -> int:
    return client.post("/api/ask", json={"question": QUESTION}).json()["related"][0]["doc_id"]


def test_without_password_everything_is_open(settings):
    client = client_for(replace(settings, access_password=""))
    status = client.get("/api/status").json()
    assert status["password"] is False and status["locked"] is False
    assert client.post("/api/ask", json={"question": QUESTION}).status_code == 200


def test_locked_until_login(settings):
    client = client_for(settings)
    status = client.get("/api/status").json()
    assert status["ok"] is True and status["password"] is True and status["locked"] is True
    assert client.post("/api/ask", json={"question": QUESTION}).status_code == 401
    assert client.get("/api/doc/1").status_code == 401
    assert client.post("/api/feedback", json={"vote": "up", "question": "x", "answer": "y"}).status_code == 401


def test_login_opens_ask_doc_and_feedback(settings):
    client = client_for(settings)
    response = client.post("/api/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=2592000" in cookie
    assert TEST_PASSWORD not in response.headers["set-cookie"]            # cookie nese podpis, ne heslo
    assert client.get("/api/status").json()["locked"] is False
    doc_id = first_doc_id(client)
    assert client.get(f"/api/doc/{doc_id}").status_code == 200
    assert client.post("/api/feedback", json={"vote": "up", "question": "x", "answer": "y"}).status_code == 200


def test_cookie_is_secure_behind_https(settings):
    response = client_for(settings).post("/api/login", json={"password": TEST_PASSWORD}, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in response.headers["set-cookie"].lower()


@pytest.mark.parametrize("payload", [{"password": "spatne"}, {"password": ""}, {"password": 5}, {}, [], "text"])
def test_wrong_password_401_and_no_cookie(settings, payload):
    client = client_for(settings)
    response = client.post("/api/login", json=payload)
    assert response.status_code == 401
    assert response.json()["detail"] == "Heslo nesouhlasí."
    assert "set-cookie" not in response.headers
    assert client.post("/api/ask", json={"question": QUESTION}).status_code == 401


def test_logout_locks_again(settings):
    client = client_for(settings)
    client.post("/api/login", json={"password": TEST_PASSWORD})
    assert client.post("/api/logout").status_code == 200
    assert client.post("/api/ask", json={"question": QUESTION}).status_code == 401


def test_forged_expired_and_foreign_cookie_rejected():
    access = Access(TEST_PASSWORD, days=30)
    now = 1_000_000.0
    token = access.token(now)
    assert access.valid(token, now)
    assert not access.valid(token, now + 31 * 86400)                 # po 30 dnech vyprší
    expires, signature = token.split(".")
    assert not access.valid(f"{int(expires) + 86400}.{signature}", now)   # posunutá platnost bez nového podpisu
    assert not access.valid(Access("jine-heslo").token(now), now)    # změna hesla odhlásí všechny
    for junk in (None, "", "abc", "123", ".", "123.", ".abc", "\u00b2\u00b3.abc", "9" * 5000 + ".abc",
                 f"{expires}.podpis-s-h\u00e1\u010dkem"):
        assert not access.valid(junk, now)


def test_cookie_with_non_ascii_signature_is_401_not_500(settings):
    """Podpis se znaky mimo ASCII mus\u00ed skon\u010dit odm\u00edtnut\u00edm, ne chybou serveru."""
    cookie = "pristup=9999999999.podpis-\u00e9".encode("latin-1")       # hlavi\u010dky HTTP nesou bajty Latin-1
    client = client_for(settings)
    assert client.get("/api/status", headers={b"cookie": cookie}).json()["locked"] is True
    response = client.post("/api/ask", json={"question": "Kdy plat\u00ed no\u010dn\u00ed klid?"}, headers={b"cookie": cookie})
    assert response.status_code == 401


def test_too_many_wrong_passwords_pause_the_address(settings):
    client = client_for(settings)
    for _ in range(5):
        assert client.post("/api/login", json={"password": "spatne"}).status_code == 401
    blocked = client.post("/api/login", json={"password": TEST_PASSWORD})    # během pauzy neprojde ani správné heslo
    assert blocked.status_code == 429
    assert "Příliš mnoho pokusů" in blocked.json()["detail"]
    assert COOKIE not in blocked.headers.get("set-cookie", "")


def test_changing_forwarded_header_does_not_evade_the_pause(settings):
    """Hádání hesla s jinou hlavičkou X-Forwarded-For u každého pokusu: bez nastavené proxy se počítá adresa spojení."""
    client = client_for(settings)
    for n in range(5):
        wrong = client.post("/api/login", json={"password": "spatne"}, headers={"X-Forwarded-For": f"203.0.113.{n}"})
        assert wrong.status_code == 401
    blocked = client.post("/api/login", json={"password": TEST_PASSWORD}, headers={"X-Forwarded-For": "203.0.113.99"})
    assert blocked.status_code == 429


def test_pause_follows_the_address_written_by_the_proxy(settings):
    """Za proxy (TRUSTED_PROXIES=1) platí pauza pro adresu, kterou připsala proxy – podvržený začátek nepomůže
    a člověk z jiné adresy se přihlásí dál."""
    client = client_for(replace(settings, trusted_proxies=1))

    def login(password, forwarded):
        return client.post("/api/login", json={"password": password}, headers={"X-Forwarded-For": forwarded}).status_code

    assert [login("spatne", f"198.51.100.{n}, 203.0.113.5") for n in range(5)] == [401] * 5
    assert login(TEST_PASSWORD, "198.51.100.9, 203.0.113.5") == 429
    assert login(TEST_PASSWORD, "198.51.100.9, 203.0.113.6") == 200
