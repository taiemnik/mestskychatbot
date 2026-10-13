"""Nastavení (`chatbot.toml`), soubory pro stránku, kontrola před zveřejněním a příkazy projektu."""
import json
import shutil

import pytest

import tasks
from chatbot.config import Instance, Settings, load_env_file
from chatbot.precheck import run_precheck
from chatbot.web import known_words, luminance, mix, stem, theme_css, write_public
from conftest import ROOT, AICKOV

TOML = """
[chatbot]
nazev = "Áíčkovský   chatbot"
organizace = "Město Horní Áíčkov"
popis = "Odpovídám z vyhlášek města Horní Áíčkov."

[vzhled]
barva = "#0A7A3C"

[otazky]
navrhy = ["Kdy platí noční klid?", "Kolik stojí poplatek za psa?"]

[sbirka]
vydavatel = "abc1234"

[osobni_udaje]
rezim = "vynechat"
"""


def write_toml(tmp_path, text=TOML):
    path = tmp_path / "chatbot.toml"
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------ nastavení
def test_instance_nacte_soubor(tmp_path):
    instance = Instance.load(write_toml(tmp_path))
    assert (instance.nazev, instance.organizace, instance.barva) == ("Áíčkovský chatbot", "Město Horní Áíčkov", "#0a7a3c")
    assert instance.navrhy == ("Kdy platí noční klid?", "Kolik stojí poplatek za psa?")
    assert (instance.sbirka_vydavatel, instance.osobni_udaje) == ("abc1234", "vynechat")
    assert instance.upozorneni == Instance().upozorneni             # co v souboru není, má výchozí hodnotu


def test_instance_bez_souboru_ma_vychozi_hodnoty(tmp_path):
    assert Instance.load(tmp_path / "neni.toml") == Instance()


@pytest.mark.parametrize("text, hint", [
    ('[vzhled]\nbarva = "modrá"', "#rrggbb"),
    ('[osobni_udaje]\nrezim = "mazat"', "hlasit"),
    ('[otazky]\nnavrhy = "jedna otázka"', "seznam"),
    ("[chatbot\nnazev = 1", "nejde přečíst"),
])
def test_instance_nesmyslna_hodnota_je_srozumitelna_chyba(tmp_path, text, hint):
    with pytest.raises(ValueError, match=hint):
        Instance.load(write_toml(tmp_path, text))


def test_vychozi_soubor_v_repu_je_platny_a_neutralni():
    instance = Instance.load(ROOT / "chatbot.toml")
    assert instance.nazev == "Městský chatbot" and instance.organizace == "" and instance.sbirka_vydavatel == ""
    assert instance.navod.startswith("https://github.com/") and instance.navrhy == ()


def test_settings_z_prostredi_a_env_soubor(tmp_path, monkeypatch):
    settings = Settings.from_env({"EMBED_DIM": "0", "CHAT_MODEL": "jiny-model", "RATE_LIMIT_PER_MIN": "3"})
    assert (settings.embed_dim, settings.chat_model, settings.rate_limit_per_min) == (0, "jiny-model", 3)
    assert settings.embed_model == "text-embedding-3-large"
    env_file = tmp_path / ".env"
    env_file.write_text("# komentář\nCHATBOT_TEST_A=ze-souboru\nCHATBOT_TEST_B = \"v uvozovkách\"\n", encoding="utf-8")
    monkeypatch.setenv("CHATBOT_TEST_A", "z-prostredi")
    monkeypatch.delenv("CHATBOT_TEST_B", raising=False)
    load_env_file(env_file)
    import os

    assert os.environ["CHATBOT_TEST_A"] == "z-prostredi" and os.environ["CHATBOT_TEST_B"] == "v uvozovkách"


def test_vzor_env_ma_klic_i_heslo_prazdne():
    text = (ROOT / "env.vzor.txt").read_text(encoding="utf-8")
    assert "LLM_API_KEY=\n" in text and "ACCESS_PASSWORD=\n" in text


# ------------------------------------------------------------------ soubory pro stránku
def test_barvy():
    assert mix("#000000", "#ffffff", 0.5) == "#808080"
    assert luminance("#ffffff") > 0.99 and luminance("#000000") == 0
    css = theme_css("#0a7a3c")
    assert "--c-navy: #0a7a3c;" in css and "--accent-text: #ffffff;" in css and '[data-rezim="tmavy"]' in css
    assert "--accent-text: #10202f;" in theme_css("#ffd400")       # na světlé barvě tmavý text


def test_znama_slova_jsou_zkracene_tvary_bez_diakritiky():
    words = known_words(["V místní části Horní Lhota a v ulici Boženy Němcové platí noční klid.", "Horní Lhotě"])
    assert {"horni", "lhota", "lhote", "bozen", "nemco"} <= set(words)
    assert "plati" not in words and stem("Áíčkově") == "aicko"


def test_write_public_zapise_ctyri_soubory(tmp_path, small_index_dir):
    instance = Instance.load(write_toml(tmp_path))
    write_public(instance, tmp_path / "public", small_index_dir)
    script = (tmp_path / "public" / "nastaveni.js").read_text(encoding="utf-8")
    assert script.startswith("/*") and "window.NASTAVENI = {" in script
    data = json.loads(script.split("window.NASTAVENI = ", 1)[1].rstrip().rstrip(";"))
    assert data["nazev"] == "Áíčkovský chatbot" and data["barva"] == "#0a7a3c"
    assert {"horni", "aicko", "lhota"} <= set(data["znama_slova"])                # názvy z dokumentů nejsou jména
    assert json.loads((tmp_path / "public" / "otazky.json").read_text(encoding="utf-8")) == list(instance.navrhy)
    assert "#0a7a3c" in (tmp_path / "public" / "logo.svg").read_text(encoding="utf-8")
    assert "--c-navy: #0a7a3c" in (tmp_path / "public" / "vzhled.css").read_text(encoding="utf-8")
    assert b"\r\n" not in (tmp_path / "public" / "nastaveni.js").read_bytes()


# ------------------------------------------------------------------ příkazy a kontrola před zveřejněním
@pytest.fixture
def project(tmp_path, monkeypatch):
    """Dočasný projekt: zkušební dokumenty, nastavení a prázdné složky; prostředí míří do něj."""
    shutil.copytree(AICKOV, tmp_path / "dokumenty")
    write_toml(tmp_path, TOML.replace('rezim = "vynechat"', 'rezim = "hlasit"'))
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / "eval").mkdir()
    for name, value in {"DOCUMENTS_DIR": tmp_path / "dokumenty", "INDEX_DIR": tmp_path / "data" / "index",
                        "CACHE_PATH": tmp_path / "data" / "cache" / "emb.npz", "PUBLIC_DIR": tmp_path / "public",
                        "INSTANCE_FILE": tmp_path / "chatbot.toml", "EMBED_DIM": "64"}.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    # příkazy načítají `.env` z kořene projektu – v testu jím musí být dočasná složka, ne repo s ostrým klíčem
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    return tmp_path


def test_build_bez_klice_poradi_zkusebni_index(project, capsys):
    assert tasks.main(["build"]) == 2
    assert "build --fake" in capsys.readouterr().out
    assert tasks.main(["build", "--fake"]) == 0
    out = capsys.readouterr().out
    assert "Dokumentů v indexu: 4" in out and "zkušební" in out
    assert (project / "public" / "nastaveni.js").is_file() and (project / "data" / "index" / "manifest.json").is_file()


def test_sbirka_bez_id_rekne_kde_ho_vzit(project, capsys, monkeypatch):
    write_toml(project, "[chatbot]\nnazev = \"Chatbot\"\n")
    assert tasks.main(["sbirka"]) == 2
    assert "sbirkapp.gov.cz" in capsys.readouterr().out
    assert tasks.main(["sbirka", "tohle neni id"]) == 2
    assert "ID datové schránky" in capsys.readouterr().out


def test_kontrola_zkusebni_index_a_tajemstvi_nepusti_ven(project, capsys):
    tasks.main(["build", "--fake"])
    result = run_precheck(project, Settings.from_env())
    levels = [level for level, _ in result.lines]
    assert not result.ok and any("zkušební" in text for level, text in result.lines if level == "CHYBA")
    (project / "poznamky.txt").write_text("klíč: sk-" + "a1B2" * 9 + "\n", encoding="utf-8")
    leaked = run_precheck(project, Settings.from_env())
    secret_line = next(text for level, text in leaked.lines if level == "CHYBA" and "tajemství" in text)
    assert "poznamky.txt" in secret_line and "a1B2" not in secret_line          # říká kde, hodnotu neopakuje
    assert "POZOR" in levels                                                    # strop útraty se připomíná vždy


def test_kontrola_najde_klic_vyplneny_do_vzoru_nastaveni(project):
    """Vzor nastavení je v repu. Kdo klíč omylem vyplní do něj místo do .env, poslal by ho ven – kontrola to musí říct."""
    tasks.main(["build", "--fake"])
    (project / "env.vzor.txt").write_text("LLM_API_KEY=\nACCESS_PASSWORD=\n", encoding="utf-8")
    assert not any("env.vzor.txt" in text for _, text in run_precheck(project, Settings.from_env()).lines)
    (project / "env.vzor.txt").write_text("LLM_API_KEY=sk-" + "a1B2" * 9 + "\n", encoding="utf-8")
    result = run_precheck(project, Settings.from_env())
    assert any(level == "CHYBA" and "env.vzor.txt" in text for level, text in result.lines)


def test_kontrola_ostry_index_projde(project, monkeypatch):
    """Ostrý index se pozná podle modelu v manifestu – stačí přepsat název, embeddingy kontrola nečte."""
    tasks.main(["build", "--fake"])
    manifest = project / "data" / "index" / "manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("fake-bag-of-words", "text-embedding-3-large"), encoding="utf-8")
    (project / "eval" / "otazky.jsonl").write_text('{"question": "Kdy platí noční klid?", "doc": "2/2026", "contains": "do šesté hodiny"}\n',
                                                   encoding="utf-8")
    result = run_precheck(project, Settings.from_env())
    assert result.ok, result.lines
    assert any("Zkušebních otázek: 1" in text for _, text in result.lines)
    # změna nastavení bez nového sestavení = zastaralé texty stránky
    write_toml(project, TOML.replace("Áíčkovský   chatbot", "Úplně jiný název"))
    assert not run_precheck(project, Settings.from_env()).ok


def test_check_bez_sady_otazek_rekne_co_delat(project, capsys, monkeypatch):
    tasks.main(["build", "--fake"])
    monkeypatch.setenv("LLM_API_KEY", "zkusebni-klic-pro-test")
    capsys.readouterr()
    assert tasks.main(["check"]) == 2
    assert "neexistuje" in capsys.readouterr().out                # hláška o chybějící sadě, ne o modelu


def test_check_bez_klice_meri_jen_hledani(project, capsys):
    """Nanečisto (bez klíče) jde změřit aspoň hledání – návod pro agenta s tím počítá."""
    tasks.main(["build", "--fake"])
    (project / "eval" / "otazky.jsonl").write_text(
        '{"question": "Kdy platí noční klid?", "doc": "2/2026", "contains": "do šesté hodiny"}\n', encoding="utf-8")
    capsys.readouterr()
    code = tasks.main(["check"])
    out = capsys.readouterr().out
    assert code == 0 and "Nalezeno: 1 z 1" in out and "bez modelu" in out


def test_check_hledani_vypise_poradi_po_vrstvach(project, capsys):
    """`check --hledani` říká, kde se správný odstavec ztrácí: podle slov, podle významu, po sloučení."""
    tasks.main(["build", "--fake"])
    (project / "eval" / "otazky.jsonl").write_text(
        '{"question": "Kdy platí noční klid?", "doc": "2/2026", "contains": "do šesté hodiny"}' + chr(10)
        + '{"question": "Kdo je starosta?", "typ": "mimo"}' + chr(10), encoding="utf-8")
    capsys.readouterr()
    code = tasks.main(["check", "--hledani"])
    out = capsys.readouterr().out
    assert code == 0 and "Hledání po vrstvách (1 otázek" in out and "jen slova" in out and "dostane model" in out
    # proti nasazené adrese to nejde – pořadí zná jen místní index
    assert tasks.main(["check", "--hledani", "--url", "https://adresa.example"]) == 2


def test_utoky_bez_klice_vyhodnoti_jen_filtr(project, capsys):
    tasks.main(["build", "--fake"])
    shutil.copy(ROOT / "eval" / "utoky.jsonl", project / "eval" / "utoky.jsonl")
    capsys.readouterr()
    tasks.main(["check", "--utoky"])
    out = capsys.readouterr().out
    rows = [json.loads(line) for line in (ROOT / "eval" / "utoky.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    known = sum(row["layer"] == "filtr" for row in rows)
    assert known >= 38 and f"Filtr zastavil {known} z {known}" in out and "bez modelu" in out


def test_serve_obsazeny_port_rekne_co_delat(project, capsys, monkeypatch):
    import socket

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: pytest.fail("server se neměl spustit"))
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        assert tasks.main(["serve", "--port", str(port)]) == 2
    out = capsys.readouterr().out
    assert "obsazený" in out and "--port" in out and "Chatbot běží" not in out


def test_serve_necha_adresu_spojeni_na_pokoji(project, monkeypatch):
    """Uvicorn umí adresu spojení sám přepsat podle hlavičky X-Forwarded-For. O tom, které hlavičce se věří, má
    rozhodovat jediné nastavení (TRUSTED_PROXIES), proto se to při spuštění vypíná – stejně jako zápis adres do logu."""
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: seen.update(kwargs))
    assert tasks.main(["serve", "--port", "0"]) == 0
    assert seen["proxy_headers"] is False and seen["access_log"] is False


def test_serve_bez_prava_zapisu_do_slozky_stranky_bezi_dal(project, capsys, monkeypatch):
    """Služba pod účtem, který do složky aplikace nesmí zapisovat, nesmí při startu spadnout: soubory stránky
    vznikly při sestavení, chatbot poběží s nimi a řekne to."""
    import uvicorn

    import chatbot.web

    def denied(*args, **kwargs):
        raise PermissionError("jen pro čtení")

    started = []
    monkeypatch.setattr(chatbot.web, "write_public", denied)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: started.append(kwargs))
    assert tasks.main(["serve", "--port", "0"]) == 0
    assert started and "nejde zapisovat" in capsys.readouterr().out


def test_zkouska_portu_umi_i_adresu_ipv6():
    """Zkouška jen pro IPv4 hlásila u „::1“ obsazený port vždy. Port 0 = jakýkoli volný."""
    assert tasks._port_is_free("::1", 0) and tasks._port_is_free("127.0.0.1", 0)


def test_kontrola_pozna_zastarale_navrhy_otazek(project):
    tasks.main(["build", "--fake"])
    manifest = project / "data" / "index" / "manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("fake-bag-of-words", "text-embedding-3-large"), encoding="utf-8")
    (project / "eval" / "otazky.jsonl").write_text('{"question": "Kdy platí noční klid?", "doc": "2/2026", "contains": "do šesté hodiny"}\n',
                                                   encoding="utf-8")
    assert run_precheck(project, Settings.from_env()).ok
    (project / "public" / "otazky.json").write_text('["Jiná otázka, než je v nastavení?"]\n', encoding="utf-8")
    assert not run_precheck(project, Settings.from_env()).ok
