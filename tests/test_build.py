"""Úseky a sestavení indexu ze složky dokumentů (zkušební předpisy města Horní Áíčkov, náhradní embeddingy)."""
import json
import shutil

import pytest

from chatbot.index.build import build_index, format_report
from chatbot.index.chunks import MAX_CHUNK_CHARS, make_chunks
from chatbot.index.store import load_index
from chatbot.ingest.folder import load_documents
from chatbot.ingest.model import Block, DocMeta, ParsedDoc, SubItem
from chatbot.llm.provider import FakeProvider, LLMError
from conftest import AICKOV


def doc_by_number(number: str) -> ParsedDoc:
    return next(d for d in load_documents(AICKOV)[0] if d.meta.cislo == number)


# ------------------------------------------------------------------ úseky
def test_chunks_citace_clanku_a_odstavce():
    chunks = make_chunks(doc_by_number("5/2025"))
    by_label = {c.label: c for c in chunks}
    rate = by_label["čl. 4 Sazba poplatku, odst. 1"]
    assert rate.path == ["Obecně závazná vyhláška č. 5/2025 o místním poplatku ze psů", "čl. 4 Sazba poplatku", "odst. 1"]
    # odstavec jde do úseku celý i se svými body a značkami z originálu
    assert rate.text.splitlines() == ["Sazba poplatku za kalendářní rok činí:", "1. za prvního psa v rodinném domě 250 Kč,",
                                      "2. za prvního psa v bytovém domě 900 Kč,",
                                      "3. za druhého a každého dalšího psa téhož držitele 1 400 Kč."]
    assert "úvodní ustanovení" in by_label                       # věta před prvním článkem
    assert by_label["čl. 7 Účinnost"].text == "Tato vyhláška nabývá účinnosti dnem 1. ledna 2026."
    assert all(c.id == f"{c.doc_id}:{c.anchor}" for c in chunks)


def test_chunks_priloha_a_tabulka():
    chunks = make_chunks(doc_by_number("5/2025"))
    table = next(c for c in chunks if c.label.endswith("tabulka"))
    assert table.label == "Příloha č. 1 Přehled sazeb, tabulka"
    assert "Druh bydlení: rodinný dům; První pes: 250 Kč; Další pes: 1 400 Kč" in table.text


def test_chunks_podpis_v_usecich_neni():
    text = " ".join(c.text for c in make_chunks(doc_by_number("5/2025")))
    assert "Áíčková" not in text and "starostka" not in text


def test_chunks_dokument_bez_clanku_cituje_stranou():
    doc = ParsedDoc(meta=DocMeta(id=7, soubor="rad.pdf", nazev="Návštěvní řád"),
                    blocks=[Block(kind="title", text="Návštěvní řád", anchor="b0"),
                            Block(kind="text", text="Knihovna je otevřena v pracovní dny od osmi do osmnácti hodin.", anchor="b1", page=2)])
    chunk = make_chunks(doc)[0]
    assert (chunk.label, chunk.path) == ("strana 2", ["Návštěvní řád"])


def test_chunks_dlouhy_odstavec_se_deli_po_bodech_a_nese_naveti():
    items = [SubItem(text="bod " + "x" * 900, marker=f"{n}.") for n in range(1, 13)]
    doc = ParsedDoc(meta=DocMeta(id=9, soubor="a.md", nazev="Řád"),
                    blocks=[Block(kind="article", text="Výčet", number="1", anchor="b0"),
                            Block(kind="para", text="Zakazuje se:", number="1", anchor="b1", items=items)])
    chunks = make_chunks(doc)
    assert len(chunks) > 1 and all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)
    assert all(c.text.startswith("Zakazuje se:") for c in chunks)
    assert [c.anchor for c in chunks] == [f"b1-{n}" for n in range(1, len(chunks) + 1)]


# ------------------------------------------------------------------ sestavení
@pytest.fixture
def docs_dir(tmp_path):
    target = tmp_path / "dokumenty"
    shutil.copytree(AICKOV, target)
    return target


def build(docs_dir, tmp_path, **kwargs):
    return build_index(docs_dir, FakeProvider(dim=64), tmp_path / "index", tmp_path / "cache" / "emb.npz",
                       today="2026-10-10", **kwargs)


def test_build_zapise_index_a_vypis(docs_dir, tmp_path):
    report = build(docs_dir, tmp_path)
    assert (report.docs, len(report.new), report.changed, report.removed) == (4, 4, [], [])
    assert report.embedded_now == report.chunks > 20
    index = load_index(tmp_path / "index")
    assert index.manifest == {"built": "2026-10-10", "embed_model": "fake-bag-of-words", "embed_dim": 64, "docs": 4,
                              "chunks": report.chunks, "content_hash": index.manifest["content_hash"]}
    police = next(d for d in index.docs.values() if d["meta"]["cislo"] == "3/2007")
    assert police["meta"]["zdroj"] == "https://sbirkapp.gov.cz/detail/SPPVZOR0000000A4"
    assert police["blocks"][0]["kind"] == "title"
    text = format_report(report)
    assert "Dokumentů v indexu: 4" in text
    assert "byl změněn předpisem č. 2/2022" in text              # novelizace je ve výpisu vidět
    assert "podpisové řádky na konci vynechány" in text


def test_build_podruhe_nic_nepocita_a_hlasi_zmeny(docs_dir, tmp_path):
    build(docs_dir, tmp_path)
    again = build(docs_dir, tmp_path)
    assert (again.embedded_now, again.new, again.changed, again.removed) == (0, [], [], [])
    night = docs_dir / "sbirka" / "ozv-2026-02-o-nocnim-klidu.md"
    night.write_text(night.read_text(encoding="utf-8").replace("do šesté hodiny", "do sedmé hodiny"), encoding="utf-8")
    (docs_dir / "sbirka" / "narizeni-2024-02-trzni-rad.md").rename(docs_dir / "sbirka" / "_trzni-rad.md")
    changed = build(docs_dir, tmp_path)
    assert changed.changed == ["Obecně závazná vyhláška č. 2/2026 o nočním klidu"]
    assert changed.removed == ["Nařízení č. 2/2024 Tržní řád"]
    assert 0 < changed.embedded_now < 5                         # počítá se jen změněný odstavec


def test_build_prazdna_slozka_je_chyba_a_index_nezapise(tmp_path):
    (tmp_path / "dokumenty").mkdir()
    report = build(tmp_path / "dokumenty", tmp_path)
    assert report.errors and "tasks.py sbirka" in report.errors[0]
    assert not (tmp_path / "index").exists()
    assert "INDEX NEBYL ZAPSÁN" in format_report(report)


def test_build_sken_a_poskozeny_soubor_jsou_ve_vypisu_jmenovite(docs_dir, tmp_path):
    """PDF bez textové vrstvy (sken) i poškozený soubor se ohlásí jménem a ostatní dokumenty se sestaví."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)                # platné PDF, ale bez jediného písmena
    with (docs_dir / "sken.pdf").open("wb") as handle:
        writer.write(handle)
    (docs_dir / "vadny.pdf").write_bytes(b"%PDF-1.4 tohle neni pdf")
    report = build(docs_dir, tmp_path)
    assert report.docs == 4
    reasons = {s.path: s.reason for s in report.skipped}
    assert "sken" in reasons["sken.pdf"] and "Markdownu" in reasons["sken.pdf"]
    assert "nepodařilo přečíst" in reasons["vadny.pdf"]
    text = format_report(report)
    assert "sken.pdf" in text and "vadny.pdf" in text


def test_build_osobni_udaje_hlasit_a_vynechat(docs_dir, tmp_path):
    (docs_dir / "kontakty.md").write_text("# Kontakty\n\nDotazy vyřizuje paní Nováková, telefon 731 123 456, "
                                          "e-mail jana.novakova@horniaickov.example.\n", encoding="utf-8")
    reported = build(docs_dir, tmp_path)
    assert reported.docs == 5 and [label for label, _ in reported.pii] == ["Kontakty"]
    text = format_report(reported)
    assert "mobilní číslo 1×" in text and "731" not in text and "novakova" not in text      # jen počty, žádné hodnoty
    strict = build(docs_dir, tmp_path, pii_mode="vynechat")
    assert strict.docs == 4 and any("osobní údaje" in s.reason for s in strict.skipped)
    chunks = json.loads((tmp_path / "index" / "chunks.json").read_text(encoding="utf-8"))
    assert not any("731 123 456" in c["text"] for c in chunks)


def test_build_vypadek_modelu_stary_index_neprepise(docs_dir, tmp_path):
    build(docs_dir, tmp_path)
    before = (tmp_path / "index" / "manifest.json").read_bytes()

    class Broken(FakeProvider):
        def embed(self, texts):
            raise LLMError("Model je nedostupný.")

    (docs_dir / "novy.md").write_text("# Nový řád\n\nTento řád upravuje provoz tržnice ve městě Horní Áíčkov.\n", encoding="utf-8")
    with pytest.raises(LLMError):
        build_index(docs_dir, Broken(dim=64), tmp_path / "index", tmp_path / "cache" / "emb.npz")
    assert (tmp_path / "index" / "manifest.json").read_bytes() == before


def test_vypis_rika_kde_je_osobni_udaj_a_ze_predpis_jeste_neni_ucinny(tmp_path):
    """U osobních údajů výpis říká i místo (článek, příloha) – nikdy hodnotu. Předpis, který nabude účinnosti
    až za čas, je mezi dokumenty s upozorněním."""
    docs = tmp_path / "dokumenty"
    docs.mkdir()
    (docs / "rad.md").write_text(
        "---\nnazev: Požární řád\ndruh: Obecně závazná vyhláška\ncislo: 2/2030\nucinnost_od: 2031-01-01\n---\n\n"
        "# Požární řád města Horní Áíčkov\n\n### Čl. 1 Úvod\n\nŘád upravuje požární ochranu ve městě Horní Áíčkov.\n\n"
        "### Čl. 7 Ohlašovna\n\nPožár hlaste panu Novákovi na čísle 777 111 222.\n", encoding="utf-8")
    report = build_index(docs, FakeProvider(dim=32), tmp_path / "index", tmp_path / "cache" / "emb.npz", today="2030-06-01")
    text = format_report(report)
    assert "kde: čl. 7 Ohlašovna" in text and "777" not in text and "Novák" not in text
    assert "nabývá účinnosti až 1. 1. 2031" in text


def test_vypis_nevypise_osobni_udaj_ktery_je_primo_v_nadpisu(tmp_path):
    """Místo nálezu je text nadpisu z dokumentu. Stojí-li jméno přímo v nadpisu, výpis uvede jen pořadí úseku."""
    docs = tmp_path / "dokumenty"
    docs.mkdir()
    (docs / "rad.md").write_text(
        "# Provozní řád sběrného dvora\n\n### Čl. 1 Provoz\n\nSběrný dvůr je otevřen ve středu a v sobotu.\n\n"
        "### Čl. 2 Mgr. Jana Nováková, vedoucí odboru\n\nVedoucí odboru rozhoduje o výjimkách z provozní doby.\n",
        encoding="utf-8")
    report = build_index(docs, FakeProvider(dim=32), tmp_path / "index", tmp_path / "cache" / "emb.npz", today="2030-06-01")
    text = format_report(report)
    assert "jméno s titulem 1×" in text and "kde: 2. úsek dokumentu (údaj je v nadpisu)" in text
    assert "Nováková" not in text


def test_kratky_text_se_neztrati_a_samotne_cislo_ano():
    """„Vstup volný.“ je celý obsah článku – článek nesmí zůstat bez úseku. Krátký dovětek se připojí
    k předchozímu úseku téhož článku; samotné číslo a zbytek záhlaví před prvním nadpisem se zahodí."""
    from chatbot.ingest.model import Block, DocMeta, ParsedDoc

    doc = ParsedDoc(meta=DocMeta(id=1, soubor="rad.txt", nazev="Návštěvní řád"), blocks=[
        Block(kind="text", text="č. 3/2026", anchor="b0"),
        Block(kind="article", text="Vstupné", number="1", anchor="b1"), Block(kind="text", text="Vstup volný.", anchor="b2"),
        Block(kind="article", text="Ceník", number="2", anchor="b3"),
        Block(kind="text", text="Kopírování se platí podle počtu stran.", anchor="b4"),
        Block(kind="text", text="1 ks 20 Kč", anchor="b5"), Block(kind="text", text="12", anchor="b6")])
    assert [(chunk.label, chunk.text) for chunk in make_chunks(doc)] == [
        ("čl. 1 Vstupné", "Vstup volný."), ("čl. 2 Ceník", "Kopírování se platí podle počtu stran.\n1 ks 20 Kč")]
