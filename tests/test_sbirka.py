"""Sbírka právních předpisů: čtení seznamu, detailu a stažení – na umělých stránkách smyšlené obce.

Testy nikam nevolají: stránky a soubory podává náhradní funkce `fetch`.
"""
from __future__ import annotations

import json

import pytest

from chatbot.ingest import sbirka

BASE = "https://sbirkapp.gov.cz"


def _row(detail_id: str, cislo: str, druh: str, nazev: str, vyhlaseno: str, ucinnost: str, platnost: str) -> str:
    return f"""
    <tr class="gov-journal__link">
      <td class="gov-journal__item " data-title="Publikující" >Město Horní Áíčkov<br>IČO: 00000001</td>
      <td class="gov-journal__item spp-align-center" data-title="Číslo právního předpisu" >
        <a href="/detail/{detail_id}">{cislo}</a></td>
      <td class="gov-journal__item " data-title="Druh právního předpisu" >
        {druh}
      </td>
      <td class="gov-journal__item " data-title="Název právního předpisu" >
        <a href="/detail/{detail_id}">{nazev}</a></td>
      <td class="gov-journal__item spp-align-center" data-title="Datum vyhlášení" >{vyhlaseno}</td>
      <td class="gov-journal__item spp-align-center" data-title="Datum nabytí účinnosti" >{ucinnost}</td>
      <td class="gov-journal__item spp-align-center" data-title="Platnost" >{platnost}</td>
    </tr>"""


def _list_page(rows: str) -> str:
    return f"""<html><body><h1>Město Horní Áíčkov - právní předpisy</h1>
    <table class="gov-table"><thead><tr><th>Publikující</th></tr></thead>
    <tbody class="gov-table__body">{rows}</tbody></table></body></html>"""


PAGE_1 = _list_page(
    _row("SPPAAA", "2/2026", "Obecně závazná vyhláška", "Obecně závazná vyhláška města Horní Áíčkov o nočním klidu",
         "14.04.2026", "29.04.2026", "Platné")
    + _row("SPPBBB", "2/2024", "Nařízení", "Tržní řád", "12.08.2024", "27.08.2024", "Platné")
    + _row("SPPCCC", "2/2025", "Obecně závazná vyhláška", "o nočním klidu", "15.04.2025", "30.04.2025",
           "Zrušeno k 29.04.2026"))
PAGE_2 = _list_page(
    _row("SPPDDD", "3/2007", "Obecně závazná vyhláška", "o zřízení městské policie", "11.06.2007", "01.08.2007", "Platné")
    + _row("SPPEEE", "1/2024", "Nařízení\n <span>VYŘAZENO</span>", "o zimní údržbě chodníků", "08.01.2024", "23.01.2024", "Vyřazeno"))


def _detail(detail_id: str, nazev: str, druh: str, cislo: str, ucinnost: str, files: list[str], relations: str = "") -> str:
    text = f'<div class="gov-content-block"><a href="/detail/{detail_id}/text">{files[0]}</a></div>'
    attachments = "".join(f'<li><a href="/detail/{detail_id}/prilohy/{8700 + i}">{name}</a></li>'
                          for i, name in enumerate(files[1:], start=1))
    if attachments:
        attachments = f'<div class="gov-content-block"><h3>Přílohy</h3><ul>{attachments}</ul></div>'
    return f"""<html><body><h1>{nazev}</h1>
    <div class="gov-layout"><div><aside class="gov-aside">
      <h4>Publikující</h4><p><a href="/vydavatel/abc1234?hlavni_typ=pp">Město Horní Áíčkov</a><br>ID DS: abc1234<BR>IČO: 00000001</p>
      <h4>Druh právního předpisu</h4><p>
        {druh}
      </p>
      <h4>Číslo právního předpisu</h4><p> {cislo} </p>
      <h4>Datum nabytí účinnosti</h4><p> {ucinnost} </p>
      <h4>Platnost</h4><p> Platné </p>
      <h4>Verze</h4><p> <a href="/detail/{detail_id}/verze">2</a> </p>
    </aside></div>
    <div>{text}{attachments}{relations}</div></div></body></html>"""


def _relations(heading: str, other_id: str, nazev: str, cislo: str, druh: str) -> str:
    return f"""<div class="gov-content-block"><h3>{heading}</h3><table class="gov-table"><tbody>
      <tr class="gov-journal__link">
        <td data-title="Název podání"><a href="/detail/{other_id}">{nazev}</a></td>
        <td data-title="Číslo právního předpisu"> {cislo} </td>
        <td data-title="Druh návaznosti"> {druh} </td>
        <td data-title="Datum nabytí účinnosti"> 22.02.2022 </td>
        <td data-title="Datum vyhlášení"> 07.02.2022 </td>
      </tr></tbody></table></div>"""


PDF = b"%PDF-1.4 obsah"
DOCX = b"PK\x03\x04 obsah"

PAGES = {
    f"{BASE}/vydavatel/abc1234?page=1": PAGE_1,
    f"{BASE}/vydavatel/abc1234?page=2": PAGE_2,
    f"{BASE}/detail/SPPAAA": _detail("SPPAAA", "Obecně závazná vyhláška města Horní Áíčkov o nočním klidu",
                                     "Obecně závazná vyhláška", "2/2026", "29.04.2026", ["OZV_2_2026.pdf", "mapa.pdf"],
                                     _relations("Předpisy, ke kterým existuje návaznost", "SPPCCC", "o nočním klidu",
                                                "2/2025", "Zrušení")),
    f"{BASE}/detail/SPPBBB": _detail("SPPBBB", "Tržní řád", "Nařízení", "2/2024", "27.08.2024", ["Tržní řád.docx"]),
    f"{BASE}/detail/SPPDDD": _detail("SPPDDD", "o zřízení městské policie", "Obecně závazná vyhláška", "3/2007",
                                     "01.08.2007", ["vyhlaska"],
                                     _relations("Předpisy, které navazují na tento", "SPPFFF", "kterou se mění vyhláška 3/2007",
                                                "2/2022", "Novelizace")),
}
FILES = {
    f"{BASE}/detail/SPPAAA/text": PDF,
    f"{BASE}/detail/SPPAAA/prilohy/8701": PDF,
    f"{BASE}/detail/SPPBBB/text": DOCX,
    f"{BASE}/detail/SPPDDD/text": PDF,
}


class FakeWeb:
    """Podává umělé stránky. Stránka, kterou nezná, je 404 – stejně jako Sbírka za poslední stranou seznamu."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if url in PAGES:
            return PAGES[url].encode("utf-8")
        if url in FILES:
            return FILES[url]
        raise sbirka.NotFound(url)


def test_parse_list_cte_radky_seznamu():
    entries = sbirka.parse_list(PAGE_1)
    assert [e.cislo for e in entries] == ["2/2026", "2/2024", "2/2025"]
    first = entries[0]
    assert first.detail_id == "SPPAAA"
    assert first.druh == "Obecně závazná vyhláška"
    assert first.nazev == "Obecně závazná vyhláška města Horní Áíčkov o nočním klidu"
    assert (first.vyhlaseno, first.ucinnost_od) == ("2026-04-14", "2026-04-29")
    assert first.platny and not entries[2].platny


def test_parse_list_druh_bez_stitku_vyrazeno():
    entry = sbirka.parse_list(PAGE_2)[1]
    assert entry.druh == "Nařízení"
    assert not entry.platny


def test_parse_detail_udaje_soubory_a_navaznosti():
    detail = sbirka.parse_detail(PAGES[f"{BASE}/detail/SPPAAA"], "SPPAAA")
    assert detail.udaje["Druh právního předpisu"] == "Obecně závazná vyhláška"
    assert detail.udaje["Číslo právního předpisu"] == "2/2026"
    # odkaz na historii verzí (…/verze) soubor není
    assert detail.soubory == [("OZV_2_2026.pdf", "/detail/SPPAAA/text"), ("mapa.pdf", "/detail/SPPAAA/prilohy/8701")]
    assert [(r.smer, r.druh, r.cislo) for r in detail.navaznosti] == [("na-jiny", "Zrušení", "2/2025")]


def test_download_stahne_jen_platne_a_zapise_prehled(tmp_path):
    web = FakeWeb()
    report = sbirka.download("abc1234", tmp_path, fetch=web, pause=0)
    assert report.nalezeno == 5 and report.platnych == 3
    names = sorted(p.name for p in (tmp_path / "sbirka").iterdir() if p.is_file())
    assert names == ["narizeni-2024-02-trzni-rad.docx",
                     "ozv-2007-03-o-zrizeni-mestske-policie.pdf",
                     "ozv-2026-02-o-nocnim-klidu-priloha-1.pdf",
                     "ozv-2026-02-o-nocnim-klidu.pdf"]
    prehled = json.loads((tmp_path / "prehled.json").read_text(encoding="utf-8"))
    assert prehled["zdroj"] == f"{BASE}/vydavatel/abc1234"
    noc = prehled["dokumenty"]["sbirka/ozv-2026-02-o-nocnim-klidu.pdf"]
    assert noc["druh"] == "Obecně závazná vyhláška" and noc["cislo"] == "2/2026"
    assert noc["ucinnost_od"] == "2026-04-29"
    assert noc["zdroj"] == f"{BASE}/detail/SPPAAA"
    assert noc["prilohy"] == ["sbirka/ozv-2026-02-o-nocnim-klidu-priloha-1.pdf"]
    # zrušené a vyřazené předpisy se nestahují vůbec – ani jejich detail
    assert f"{BASE}/detail/SPPCCC" not in web.calls and f"{BASE}/detail/SPPEEE" not in web.calls


def test_download_novelizace_je_v_poznamce_zruseni_stare_upravy_ne(tmp_path):
    sbirka.download("abc1234", tmp_path, fetch=FakeWeb(), pause=0)
    prehled = json.loads((tmp_path / "prehled.json").read_text(encoding="utf-8"))
    policie = prehled["dokumenty"]["sbirka/ozv-2007-03-o-zrizeni-mestske-policie.pdf"]
    assert "2/2022" in policie["poznamka"] and "změn" in policie["poznamka"]
    assert policie["navaznosti"] == ["změněn č. 2/2022"]
    # nový noční klid zrušil loňský – to je v přehledu návazností, ale upozornění u zdroje to není
    klid = prehled["dokumenty"]["sbirka/ozv-2026-02-o-nocnim-klidu.pdf"]
    assert klid["poznamka"] == "" and klid["navaznosti"] == ["ruší č. 2/2025"]


def test_relation_note_novela_odkazuje_na_puvodni_predpis():
    note = sbirka.relation_note([sbirka.Relation(smer="na-jiny", druh="Novelizace", cislo="3/2007", nazev="o policii")])
    assert note == "Tento předpis mění předpis č. 3/2007 – čtěte ho spolu s ním."


def test_download_nazev_bez_druhu_na_zacatku(tmp_path):
    """Název v seznamu často opakuje druh a obec („Obecně závazná vyhláška města Horní Áíčkov o …“) – do názvu souboru nepatří."""
    sbirka.download("abc1234", tmp_path, fetch=FakeWeb(), pause=0)
    assert (tmp_path / "sbirka" / "ozv-2026-02-o-nocnim-klidu.pdf").is_file()


def test_download_co_prestalo_platit_odlozi_stranou(tmp_path):
    """Odkládá se soubor předpisu, který seznam vede jako zrušený (loňský noční klid) – i s přílohou a přepisem."""
    target = tmp_path / "sbirka"
    target.mkdir(parents=True)
    for name in ("ozv-2025-02-o-nocnim-klidu.pdf", "ozv-2025-02-o-nocnim-klidu-priloha-1.pdf", "ozv-2025-02-o-nocnim-klidu.md"):
        (target / name).write_bytes(PDF)
    report = sbirka.download("abc1234", tmp_path, fetch=FakeWeb(), pause=0, today="2026-10-01")
    assert sorted(report.odlozeno) == ["ozv-2025-02-o-nocnim-klidu-priloha-1.pdf", "ozv-2025-02-o-nocnim-klidu.md",
                                       "ozv-2025-02-o-nocnim-klidu.pdf"]
    assert not list(target.glob("ozv-2025-02*")) and len(list((target / "_neplatne").iterdir())) == 3


def test_download_soubor_ktery_seznam_nezna_zustava_a_ohlasi_se(tmp_path):
    """Soubor, o kterém seznam nic neříká, se neodkládá: častěji než zrušení předpisu to znamená neúplný seznam."""
    target = tmp_path / "sbirka"
    target.mkdir(parents=True)
    (target / "ozv-2020-09-stary-predpis.pdf").write_bytes(PDF)
    (target / "desktop.ini").write_bytes(b"[.ShellClassInfo]")
    report = sbirka.download("abc1234", tmp_path, fetch=FakeWeb(), pause=0)
    assert report.odlozeno == [] and (target / "ozv-2020-09-stary-predpis.pdf").is_file() and (target / "desktop.ini").is_file()
    assert any("ozv-2020-09-stary-predpis.pdf" in line and "nezná" in line for line in report.problemy)
    assert not any("desktop.ini" in line for line in report.problemy)      # není to soubor předpisu


def test_download_zachova_udaje_k_vlastnim_dokumentum(tmp_path):
    (tmp_path / "prehled.json").write_text(json.dumps({"dokumenty": {"moje/rad.pdf": {"nazev": "Návštěvní řád"}}}),
                                           encoding="utf-8")
    sbirka.download("abc1234", tmp_path, fetch=FakeWeb(), pause=0)
    prehled = json.loads((tmp_path / "prehled.json").read_text(encoding="utf-8"))
    assert prehled["dokumenty"]["moje/rad.pdf"] == {"nazev": "Návštěvní řád"}


def test_download_prijme_i_celou_adresu(tmp_path):
    report = sbirka.download(f"{BASE}/vydavatel/abc1234", tmp_path, fetch=FakeWeb(), pause=0)
    assert report.platnych == 3


@pytest.mark.parametrize("bad", ["", "../etc", "abc 123", "https://example.com/vydavatel/abc1234", "abc1234/../x"])
def test_download_odmitne_nesmyslne_id(tmp_path, bad):
    with pytest.raises(ValueError):
        sbirka.download(bad, tmp_path, fetch=FakeWeb(), pause=0)


def test_download_neznamy_druh_souboru_hlasi_a_nestahne(tmp_path):
    class Web(FakeWeb):
        def __call__(self, url):
            if url.endswith("/detail/SPPBBB/text"):
                return b"\xd0\xcf\x11\xe0 stary word"
            return super().__call__(url)

    report = sbirka.download("abc1234", tmp_path, fetch=Web(), pause=0)
    assert not list((tmp_path / "sbirka").glob("narizeni-2024-02*"))
    assert any("2/2024" in line for line in report.problemy)


# ------------------------------------------------------------------ odolnost: výpadek sítě a změna webu nesmí nic odložit
def test_download_vypadek_detailu_nic_neodlozi_a_zaznam_zustane(tmp_path):
    """Když se detail platného předpisu nepodaří načíst, jeho dřív stažené soubory zůstávají na místě
    i v přehledu – jinak by chatbot po dalším sestavení platný předpis potichu ztratil."""
    sbirka.download("abc1234", tmp_path, fetch=FakeWeb(), pause=0)

    class Flaky(FakeWeb):
        def __call__(self, url):
            if url.endswith("/detail/SPPAAA"):
                raise sbirka.SbirkaError("spojení spadlo")
            return super().__call__(url)

    report = sbirka.download("abc1234", tmp_path, fetch=Flaky(), pause=0)
    assert report.odlozeno == []
    assert (tmp_path / "sbirka" / "ozv-2026-02-o-nocnim-klidu.pdf").is_file()
    assert (tmp_path / "sbirka" / "ozv-2026-02-o-nocnim-klidu-priloha-1.pdf").is_file()
    prehled = json.loads((tmp_path / "prehled.json").read_text(encoding="utf-8"))
    assert prehled["dokumenty"]["sbirka/ozv-2026-02-o-nocnim-klidu.pdf"]["cislo"] == "2/2026"
    assert any("2/2026" in line and "detail" in line for line in report.problemy)


def test_download_prazdny_seznam_je_chyba_a_nic_se_nezmeni(tmp_path):
    """Stránka se načetla, ale řádky v ní nejsou (Sbírka změnila podobu): nic se neodkládá ani nepřepisuje."""
    sbirka.download("abc1234", tmp_path, fetch=FakeWeb(), pause=0)
    before = sorted(p.name for p in (tmp_path / "sbirka").iterdir())
    overview = (tmp_path / "prehled.json").read_bytes()

    class Empty(FakeWeb):
        def __call__(self, url):
            if "/vydavatel/" in url:
                return _list_page("").encode("utf-8")
            return super().__call__(url)

    with pytest.raises(sbirka.SbirkaError, match="žádné předpisy"):
        sbirka.download("abc1234", tmp_path, fetch=Empty(), pause=0)
    assert sorted(p.name for p in (tmp_path / "sbirka").iterdir()) == before
    assert (tmp_path / "prehled.json").read_bytes() == overview


def test_download_neuplny_seznam_nic_neodlozi_a_prehled_zustane(tmp_path):
    """Druhá strana seznamu přijde prázdná (výpadek, změna stránkování). Předpisy z ní nesmějí skončit mezi
    neplatnými a jejich záznamy musejí zůstat v přehledu – jinak by po sestavení přišly o název, číslo i odkaz."""
    sbirka.download("abc1234", tmp_path, fetch=FakeWeb(), pause=0)
    police = "sbirka/ozv-2007-03-o-zrizeni-mestske-policie.pdf"

    class HalfList(FakeWeb):
        def __call__(self, url):
            if url.endswith("?page=2"):
                return _list_page("").encode("utf-8")
            return super().__call__(url)

    report = sbirka.download("abc1234", tmp_path, fetch=HalfList(), pause=0)
    assert report.odlozeno == [] and (tmp_path / police).is_file()
    prehled = json.loads((tmp_path / "prehled.json").read_text(encoding="utf-8"))
    assert prehled["dokumenty"][police]["cislo"] == "3/2007" and "2/2022" in prehled["dokumenty"][police]["poznamka"]
    assert any("ozv-2007-03-o-zrizeni-mestske-policie.pdf" in line and "nezná" in line for line in report.problemy)


def test_download_mnoho_neznamych_souboru_se_vypise_zkracene(tmp_path):
    target = tmp_path / "sbirka"
    target.mkdir(parents=True)
    for n in range(1, 9):
        (target / f"ozv-2020-{n:02d}-stary-predpis.pdf").write_bytes(PDF)
    report = sbirka.download("abc1234", tmp_path, fetch=FakeWeb(), pause=0)
    assert report.odlozeno == [] and len(list(target.glob("ozv-2020-*.pdf"))) == 8
    assert sum("nezná" in line for line in report.problemy) == 1 and any("a 3 dalších" in line for line in report.problemy)


def test_download_rucne_odlozeny_predpis_se_znovu_nestahuje(tmp_path):
    """Souběžně platný starší předpis nebo dokument s osobními údaji člověk odloží do složky s podtržítkem.
    Další stažení ho nesmí vrátit zpět."""
    sbirka.download("abc1234", tmp_path, fetch=FakeWeb(), pause=0)
    target = tmp_path / "sbirka"
    (target / "_neplatne").mkdir()
    (target / "narizeni-2024-02-trzni-rad.docx").replace(target / "_neplatne" / "narizeni-2024-02-trzni-rad.docx")
    (tmp_path / "_stranou").mkdir()
    (target / "ozv-2007-03-o-zrizeni-mestske-policie.pdf").replace(tmp_path / "_stranou" / "ozv-2007-03-o-zrizeni-mestske-policie.pdf")
    web = FakeWeb()
    report = sbirka.download("abc1234", tmp_path, fetch=web, pause=0)
    assert report.stazeno == [] and not (target / "narizeni-2024-02-trzni-rad.docx").exists()
    assert not (target / "ozv-2007-03-o-zrizeni-mestske-policie.pdf").exists()
    assert f"{BASE}/detail/SPPBBB" not in web.calls and f"{BASE}/detail/SPPDDD" not in web.calls
    assert any("2/2024" in line and "sbirka/_neplatne/" in line for line in report.problemy)
    assert any("3/2007" in line and "_stranou/" in line for line in report.problemy)
    prehled = json.loads((tmp_path / "prehled.json").read_text(encoding="utf-8"))
    assert list(prehled["dokumenty"]) == ["sbirka/ozv-2026-02-o-nocnim-klidu.pdf"]


def test_download_prehled_s_bom_se_precte_a_neplatny_je_chyba(tmp_path):
    """Přehled uložený editorem se značkou BOM se nesmí potichu přepsat; překlep v něm je chyba dřív, než se cokoli změní."""
    own = {"dokumenty": {"moje/rad.pdf": {"nazev": "Návštěvní řád"}}}
    (tmp_path / "prehled.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(own, ensure_ascii=False).encode("utf-8"))
    sbirka.download("abc1234", tmp_path, fetch=FakeWeb(), pause=0)
    prehled = json.loads((tmp_path / "prehled.json").read_text(encoding="utf-8"))
    assert prehled["dokumenty"]["moje/rad.pdf"] == {"nazev": "Návštěvní řád"}

    broken = b'{"dokumenty": {"moje/rad.pdf": {"nazev": "N\xc3\xa1v\xc5\xa1t\xc4\x9bvn\xc3\xad \xc5\x99\xc3\xa1d",}}}'
    (tmp_path / "prehled.json").write_bytes(broken)
    web = FakeWeb()
    with pytest.raises(sbirka.SbirkaError, match="není platný JSON"):
        sbirka.download("abc1234", tmp_path, fetch=web, pause=0)
    assert (tmp_path / "prehled.json").read_bytes() == broken and web.calls == []


def test_download_stary_word_rekne_kam_ulozit_a_ulozeny_soubor_pozna(tmp_path):
    """Hláška u souboru .doc říká cílový název. Soubor uložený pod ním další stažení pozná a zapíše do přehledu."""
    class OldWord(FakeWeb):
        def __call__(self, url):
            if url.endswith("/detail/SPPBBB/text"):
                return b"\xd0\xcf\x11\xe0 stary word"
            return super().__call__(url)

    report = sbirka.download("abc1234", tmp_path, fetch=OldWord(), pause=0)
    assert any("sbirka/narizeni-2024-02-trzni-rad.docx" in line for line in report.problemy)
    (tmp_path / "sbirka" / "narizeni-2024-02-trzni-rad.docx").write_bytes(DOCX)        # člověk soubor převedl a uložil
    report = sbirka.download("abc1234", tmp_path, fetch=OldWord(), pause=0)
    assert "narizeni-2024-02-trzni-rad.docx" in report.ponechano and report.odlozeno == []
    prehled = json.loads((tmp_path / "prehled.json").read_text(encoding="utf-8"))
    assert prehled["dokumenty"]["sbirka/narizeni-2024-02-trzni-rad.docx"]["cislo"] == "2/2024"


def test_download_odlozeni_neprepise_starsi_soubor_tehoz_jmena(tmp_path):
    target = tmp_path / "sbirka"
    (target / "_neplatne").mkdir(parents=True)
    (target / "_neplatne" / "ozv-2025-02-o-nocnim-klidu-priloha-1.pdf").write_bytes(b"%PDF starsi")
    (target / "ozv-2025-02-o-nocnim-klidu-priloha-1.pdf").write_bytes(b"%PDF novejsi")
    sbirka.download("abc1234", tmp_path, fetch=FakeWeb(), pause=0, today="2026-10-01")
    kept = sorted(path.name for path in (target / "_neplatne").iterdir())
    assert kept == ["ozv-2025-02-o-nocnim-klidu-priloha-1-2026-10-01.pdf", "ozv-2025-02-o-nocnim-klidu-priloha-1.pdf"]
    assert (target / "_neplatne" / "ozv-2025-02-o-nocnim-klidu-priloha-1.pdf").read_bytes() == b"%PDF starsi"


def test_download_bez_souboru_s_textem_nestahne_prilohu_jako_text(tmp_path):
    """Detail bez odkazu na text: první příloha se nesmí uložit pod názvem předpisu a číst jako jeho znění."""
    detail = _detail("SPPBBB", "Tržní řád", "Nařízení", "2/2024", "27.08.2024", ["Tržní řád.docx", "plánek.pdf"])
    detail = detail.replace('<a href="/detail/SPPBBB/text">Tržní řád.docx</a>', "")

    class NoText(FakeWeb):
        def __call__(self, url):
            if url.endswith("/detail/SPPBBB"):
                return detail.encode("utf-8")
            if url.endswith("/detail/SPPBBB/prilohy/8701"):
                return PDF
            return super().__call__(url)

    report = sbirka.download("abc1234", tmp_path, fetch=NoText(), pause=0)
    assert not list((tmp_path / "sbirka").glob("narizeni-2024-02*"))
    assert any("2/2024" in line and "není soubor s textem" in line for line in report.problemy)


def test_download_odkaz_na_text_dvakrat_neni_priloha(tmp_path):
    detail = PAGES[f"{BASE}/detail/SPPBBB"].replace("</body>", '<a href="/detail/SPPBBB/text">Stáhnout text</a></body>')

    class Twice(FakeWeb):
        def __call__(self, url):
            return detail.encode("utf-8") if url.endswith("/detail/SPPBBB") else super().__call__(url)

    sbirka.download("abc1234", tmp_path, fetch=Twice(), pause=0)
    assert [path.name for path in (tmp_path / "sbirka").glob("narizeni-2024-02*")] == ["narizeni-2024-02-trzni-rad.docx"]


def test_search_kdyz_naseptavac_neni_rekne_co_delat():
    def fetch(url):
        raise sbirka.NotFound(url)

    with pytest.raises(sbirka.SbirkaError, match="hledejte na https://sbirkapp.gov.cz"):
        sbirka.search("Horní Áíčkov", fetch=fetch)


def test_dlouhe_nazvy_se_stejnym_zacatkem_nejsou_stejny_predpis(tmp_path):
    start = "kterou se stanovují podmínky pro pořádání a průběh veřejnosti přístupných "
    pages = {
        f"{BASE}/vydavatel/abc1234?page=1": _list_page(
            _row("SPPAAA", "1/2024", "Obecně závazná vyhláška", start + "sportovních podniků", "01.03.2024", "16.03.2024", "Platné")
            + _row("SPPBBB", "2/2024", "Obecně závazná vyhláška", start + "kulturních podniků", "01.03.2024", "16.03.2024", "Platné")),
        f"{BASE}/detail/SPPAAA": _detail("SPPAAA", "a", "Obecně závazná vyhláška", "1/2024", "16.03.2024", ["a.pdf"]),
        f"{BASE}/detail/SPPBBB": _detail("SPPBBB", "b", "Obecně závazná vyhláška", "2/2024", "16.03.2024", ["b.pdf"]),
    }

    def fetch(url):
        if url in pages:
            return pages[url].encode("utf-8")
        if url.endswith("/text"):
            return PDF
        raise sbirka.NotFound(url)

    report = sbirka.download("abc1234", tmp_path, fetch=fetch, pause=0, today="2026-10-01")
    assert not any("stejný název" in line for line in report.problemy)


def test_predpis_zruseny_k_budoucimu_datu_jeste_plati(tmp_path):
    row = _row("SPPGGG", "4/2020", "Obecně závazná vyhláška", "o poplatku za odpad", "01.01.2020", "15.01.2020",
               "Zrušeno k 01.01.2031")
    entry = sbirka.parse_list(_list_page(row))[0]
    assert sbirka.is_valid(entry, "2030-06-01") and not sbirka.is_valid(entry, "2031-01-01")
    assert sbirka.is_valid(sbirka.parse_list(PAGE_1)[0], "2030-06-01")              # „Platné“
    assert not sbirka.is_valid(sbirka.parse_list(PAGE_2)[1], "2030-06-01")          # „Vyřazeno“
    assert sbirka.repeal_note(entry) == "Pozor: tento předpis je k 1. 1. 2031 zrušen; do té doby platí."
    by = [sbirka.Relation(smer="na-tento", druh="Zrušení", cislo="2/2030", nazev="o poplatku za odpad")]
    assert sbirka.repeal_note(entry, by) == "Pozor: tento předpis je k 1. 1. 2031 zrušen předpisem č. 2/2030; do té doby platí."
    # obecná věta o zrušení se k větě s datem nepřidává podruhé; bez data zní jednoznačně
    assert sbirka._note(entry, by) == "Pozor: tento předpis je k 1. 1. 2031 zrušen předpisem č. 2/2030; do té doby platí."
    assert sbirka.relation_note(by) == "Pozor: předpis č. 2/2030 tento předpis ruší."
    assert sbirka.repeal_note(sbirka.parse_list(PAGE_1)[0]) == ""


def test_poznamka_o_zruseni_se_ode_dne_zruseni_zmeni():
    """Věta „do té doby platí“ vzniká při stažení. Ode dne zrušení ji chatbot ukazuje jako „už neplatí“,
    i když správce předpisy ještě znovu nestáhl."""
    from chatbot.index.model import document_note

    row = _row("SPPGGG", "4/2020", "Obecně závazná vyhláška", "o poplatku za odpad", "01.01.2020", "15.01.2020",
               "Zrušeno k 01.01.2031")
    entry = sbirka.parse_list(_list_page(row))[0]
    by = [sbirka.Relation(smer="na-tento", druh="Zrušení", cislo="2/2030", nazev="o poplatku za odpad")]
    for relations, tail in ((by, " předpisem č. 2/2030"), ([], "")):
        note = sbirka.repeal_note(entry, relations)
        assert document_note({"poznamka": note}, "2030-12-31") == note
        assert document_note({"poznamka": note}, "2031-01-01") == f"Pozor: tento předpis byl k 1. 1. 2031 zrušen{tail} a už neplatí."
    assert document_note({"poznamka": "Pozor: tento předpis byl změněn předpisem č. 2/2022 – ověřte znění i tam."},
                         "2031-01-01").endswith("ověřte znění i tam.")


def test_publisher_id_prijme_velka_pismena():
    assert sbirka.publisher_id("ABC1234") == "abc1234"
    assert sbirka.publisher_id("https://sbirkapp.gov.cz/vydavatel/ABC1234?page=2") == "abc1234"


def test_vypis_rika_ci_predpisy_to_jsou(tmp_path):
    """Ve výpisu je název vydavatele ze stránky Sbírky – podle něj se pozná, že ID patří správné obci."""
    report = sbirka.download("abc1234", tmp_path, fetch=FakeWeb(), pause=0)
    assert report.nazev_vydavatele == "Město Horní Áíčkov IČO: 00000001"
    assert "abc1234 (Město Horní Áíčkov IČO: 00000001)" in report.format().splitlines()[0]


# ------------------------------------------------------------------ hledání vydavatele, překlep v ID, souběžné předpisy
def test_search_najde_vydavatele_podle_nazvu():
    payload = json.dumps([
        {"kod": "abc1234", "name": "Město Horní Áíčkov, Áíčkovský kraj (PSČ 10000, ID DS: abc1234, IČO: 00000001)"},
        {"kod": "xyz9876", "name": "Obec Horní Áíčkov, Jiný kraj (PSČ 20000, ID DS: xyz9876, IČO: 00000002)"},
        {"kod": "nesmysl", "name": 5}, "řetězec"]).encode("utf-8")
    seen = []

    def fetch(url):
        seen.append(url)
        return payload

    found = sbirka.search("Horní Áíčkov u Lhoty", fetch=fetch)
    assert found == [("abc1234", "Město Horní Áíčkov, Áíčkovský kraj (PSČ 10000, ID DS: abc1234, IČO: 00000001)"),
                     ("xyz9876", "Obec Horní Áíčkov, Jiný kraj (PSČ 20000, ID DS: xyz9876, IČO: 00000002)")]
    assert seen == [f"{BASE}/xhr/naseptavac/ovm?q=Horn%C3%AD%20%C3%81%C3%AD%C4%8Dkov%20u%20Lhoty"]
    assert sbirka.search("   ", fetch=fetch) == []


def test_download_nezname_id_rekne_ze_jde_nejspis_o_preklep(tmp_path):
    """Sbírka na neznámé ID odpovídá chybou serveru, ne „nenalezeno“ – hláška má poradit zkontrolovat ID."""
    def fetch(url):
        raise sbirka.SbirkaError("Sbírka vrátila chybu 500: " + url)

    with pytest.raises(sbirka.SbirkaError, match="Zkontrolujte ID"):
        sbirka.download("zzzzzz9", tmp_path, fetch=fetch, pause=0)


def test_download_upozorni_na_soubezne_predpisy_a_budouci_ucinnost(tmp_path):
    """Dva platné předpisy stejného názvu a předpis účinný až za čas: stáhnou se, ale člověk se to dozví."""
    pages = {
        f"{BASE}/vydavatel/abc1234?page=1": _list_page(
            _row("SPPAAA", "1/2030", "Obecně závazná vyhláška", "o nočním klidu", "14.04.2030", "01.01.2031", "Platné")
            + _row("SPPBBB", "3/2024", "Obecně závazná vyhláška", "Obecně závazná vyhláška města Horní Áíčkov o nočním klidu",
                   "01.03.2024", "16.03.2024", "Platné")),
        f"{BASE}/detail/SPPAAA": _detail("SPPAAA", "o nočním klidu", "Obecně závazná vyhláška", "1/2030", "01.01.2031", ["a.pdf"]),
        f"{BASE}/detail/SPPBBB": _detail("SPPBBB", "o nočním klidu", "Obecně závazná vyhláška", "3/2024", "16.03.2024", ["b.pdf"]),
    }

    def fetch(url):
        if url in pages:
            return pages[url].encode("utf-8")
        if url.endswith("/text"):
            return PDF
        raise sbirka.NotFound(url)

    report = sbirka.download("abc1234", tmp_path, fetch=fetch, pause=0, today="2030-06-01")
    assert len(report.stazeno) == 2
    assert any("stejný název" in line and "1/2030" in line and "3/2024" in line for line in report.problemy)
    assert any("1/2030" in line and "účinnosti až 1. 1. 2031" in line for line in report.problemy)
