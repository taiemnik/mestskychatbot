"""Kontrola osobních údajů v dotazu (public/osobni-udaje.js) – běží v prohlížeči, tady se zkouší v Node.

Dvě strany téže mince: údaj se má poznat, a běžný dotaz na předpis (včetně celé palety návrhů
na stránce) varování vyvolat nesmí – jinak by ho lidé začali odklikávat bez čtení.
"""
import json
import shutil
import subprocess

import pytest

from conftest import ROOT

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js není nainstalován")

SCRIPT = """
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
globalThis.NASTAVENI = { znama_slova: input.znama };     // na stránce je zapisuje sestavení indexu (nastaveni.js)
const { najdi } = require(process.argv[1]);
process.stdout.write(JSON.stringify(input.texts.map(najdi)));
"""


def najdi(texts: list[str], znama: tuple[str, ...] = ()) -> list[list[str]]:
    """`znama` = zkrácené tvary slov z dokumentů, jak je stránce předává sestavení indexu (bez nich prázdné)."""
    done = subprocess.run([NODE, "-e", SCRIPT, str(ROOT / "public" / "osobni-udaje.js")],
                          input=json.dumps({"texts": texts, "znama": list(znama)}).encode("utf-8"),
                          capture_output=True, check=True)
    return json.loads(done.stdout.decode("utf-8"))


# údaje jsou vymyšlené
FOUND = [
    ("Má Jan Novák nárok na příspěvek z fondu?", "jméno a příjmení"),
    ("Může paní Dvořáková čerpat dovolenou ve zkušební době?", "jméno a příjmení"),
    ("Kdo zastupuje Ing. Petra Svobodu při podpisu?", "jméno a příjmení"),
    ("Zaměstnanec s rodným číslem 850412/1234 žádá o odměnu.", "rodné číslo"),
    ("rodné číslo 8504121234, co s tím", "rodné číslo"),
    ("Kolegyně narozená 12. 4. 1985 chce pracovat z domu.", "datum narození"),
    ("Žadatel nar. 3.5.2015 – kdo podepisuje?", "datum narození"),
    ("Napište mi na jan.novak@horniaickov.example, kdo schvaluje cestu.", "e-mailová adresa"),
    ("Volejte 731 123 456 ohledně stravenek.", "telefonní číslo"),
    ("Občan má OP č. 123456789, jak ověřit podpis?", "číslo dokladu"),
    ("Jak vyřídit spis sp. zn. 2145/2026/ABC?", "číslo spisu nebo číslo jednací"),
    ("Kdo vyřizuje č. j. MUVZ 45123/2026?", "číslo spisu nebo číslo jednací"),
    ("Platba na účet 19-2000145399/0800 – kdo ji schvaluje?", "číslo účtu"),
]

CLEAN = [
    "Kdo schvaluje zahraniční pracovní cestu?",
    "Co říká Směrnice č. 4/2024 o cestovních náhradách?",
    "Kolik stojí roční parkovací karta pro obyvatele?",
    "Platí Organizační řád Městského úřadu i pro městskou policii?",
    "Od kdy je účinný příkaz č. 2/2024? Platí od 1. 3. 2024?",
    "Jaký je limit 50 000 Kč pro objednávku bez výběrového řízení?",
    "Kdo podepisuje smlouvy nad 500 000 Kč, starosta, nebo Rada města?",
    "Jak se eviduje docházka v docházkovém systému?",
    "Dobrý den, kdo schvaluje dovolenou?",
    "Co říká zákon č. 251/2016 Sb. o nočním klidu?",
    "Platí u nás zákon č. 128/2000 Sb., o obcích, i pro místní části?",
]


@pytest.mark.parametrize(("text", "label"), FOUND)
def test_personal_data_is_recognised(text, label):
    assert label in najdi([text])[0]


def test_ordinary_questions_do_not_warn():
    results = najdi(CLEAN)
    assert {text: hits for text, hits in zip(CLEAN, results) if hits} == {}


def test_nazev_obce_ze_dvou_slov_nevaruje_kdyz_ho_zna_sestaveni():
    """„Horní Áíčkov“ vypadá jako jméno a příjmení. Varování nepřijde, protože sestavení indexu zapíše slova
    z dokumentů mezi známá; bez nich (stránka bez sestaveného indexu) varuje – jen varuje, odeslání nezakazuje."""
    from chatbot.web import known_words

    question = "Platí Organizační řád Městského úřadu Horní Áíčkov i pro městskou policii?"
    znama = known_words(["Zastupitelstvo města Horní Áíčkov se usneslo vydat tuto vyhlášku."])
    assert najdi([question], tuple(znama))[0] == []
    assert najdi([question])[0] == ["jméno a příjmení"]
    # známý název obce jméno člověka neschová
    assert najdi(["Má Jan Novák z města Horní Áíčkov nárok na příspěvek?"], tuple(znama))[0] == ["jméno a příjmení"]


def test_one_number_is_reported_once():
    """Rodné číslo se nesmí ohlásit ještě jako účet nebo telefon."""
    assert najdi(["Zaměstnanec 850412/1234 žádá o odměnu."])[0] == ["rodné číslo"]
