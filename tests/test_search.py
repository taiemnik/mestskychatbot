"""Hledání – podle slov (BM25), podle významu (embeddingy) a sloučení obou pořadí."""
import numpy as np

from chatbot.index.chunks import Chunk
from chatbot.index.store import Index
from chatbot.search.hybrid import Hit, Searcher
from chatbot.search.lexical import LexicalIndex, tokenize
from chatbot.search.vector import vector_search

TEXTS = [
    "Dovolená zaměstnanců se čerpá podle plánu dovolených schváleného vedoucím odboru.",
    "Rozpočet města schvaluje zastupitelstvo na návrh rady města.",
    "Služební vozidlo smí řídit jen zaměstnanec s platným referentským školením.",
    "Podpisové právo tajemníka se řídí podpisovým řádem.",
]


def unit(*values) -> np.ndarray:
    v = np.array(values, dtype=np.float32)
    return v / np.linalg.norm(v)


def make_index(texts, vectors) -> Index:
    chunks = [Chunk(id=f"1:b{i}", doc_id=1, path=["Směrnice č. 1/2030 Zkušební", f"čl. {i + 1}", "odst. 1"],
                    label=f"čl. {i + 1}, odst. 1", text=text, anchor=f"b{i}") for i, text in enumerate(texts)]
    return Index(manifest={}, docs={}, chunks=chunks, vectors=np.stack(vectors).astype(np.float32))


def test_tokenize_diacritics_and_truncation():
    assert tokenize("Zaměstnanci úřadu") == ["zamest", "urad"]
    assert tokenize("a v § 5, odst. 2 – Čl.12!") == ["odst2", "cl12", "odst", "cl", "12"]   # krátké kusy pryč, odkazy navíc vcelku


def test_tokenize_unifies_common_czech_endings():
    # samotné zkrácení na 6 znaků nespojí „odměna“ a „odměny“ – proto se ubírá i koncovka
    assert len(set(tokenize("odměna odměny odměnu odměnou"))) == 1
    assert len(set(tokenize("fond fondu fondem"))) == 1
    assert len(set(tokenize("cesta cestu cesty cestách"))) == 1
    assert tokenize("řád řádu") == ["rad", "rad"]
    assert tokenize("2026 100000") == ["2026", "100000"]                       # čísla se nekrátí o „koncovku“
    assert tokenize("my se") == ["my", "se"]                                    # krátká slova zůstávají celá


def test_lexical_finds_inflected_form():
    hits = LexicalIndex(TEXTS).search("dovolená zaměstnance", k=3)
    assert hits[0][0] == 0                                   # „zaměstnanců“ i „dovolených“ se potkají se slovy dotazu
    assert hits[0][1] > 0
    assert all(score > 0 for _, score in hits)               # úseky bez shody ve výsledku nejsou


def test_lexical_prefers_rare_words():
    hits = LexicalIndex(TEXTS).search("města referentské školení", k=4)
    assert hits[0][0] == 2                                   # vzácná slova váží víc než časté „města“


def test_vector_search_orders_by_cosine():
    vectors = np.stack([unit(1, 0, 0), unit(0, 1, 0), unit(1, 1, 0), unit(0, 0, 1)])
    hits = vector_search(vectors, np.array([2.0, 0.1, 0.0]), k=3)      # dotaz nemusí být normovaný
    assert [i for i, _ in hits] == [0, 2, 1]
    assert hits[0][1] > 0.99 and abs(hits[1][1] - 0.74) < 0.02


def test_hybrid_merges_both_lists():
    # úsek 0 odpovídá slovy, ale ne významem; úsek 1 naopak
    index = make_index(TEXTS, [unit(0, 1, 0), unit(1, 0, 0), unit(0, 0, 1), unit(0, 0, 1)])
    hits = Searcher(index).search("dovolená zaměstnanců", unit(1, 0, 0), k=4)
    found = {h.chunk.anchor: h for h in hits}
    assert {"b0", "b1"} <= set(found)
    assert isinstance(hits[0], Hit)
    assert abs(found["b0"].similarity) < 1e-6                # podobnost je kosinová, i když úsek našla jen slova
    assert found["b1"].similarity > 0.99
    assert all(a.score >= b.score for a, b in zip(hits, hits[1:]))


def test_hit_found_by_both_ranks_first():
    index = make_index(TEXTS, [unit(1, 0, 0), unit(0.9, 0.1, 0), unit(0, 1, 0), unit(0, 0, 1)])
    hits = Searcher(index).search("dovolená zaměstnanců", unit(1, 0, 0), k=2)
    assert hits[0].chunk.anchor == "b0"


def test_search_finds_regulation_by_its_name():
    # název předpisu je v cestě úseku, ne v jeho textu – slovní hledání ho musí vidět
    index = make_index(["Prodejní místa přiděluje správce."] * 2, [unit(0, 0, 1)] * 2)
    index.chunks[1].path[0] = "Nařízení č. 2/2024 Tržní řád"
    searcher = Searcher(index)
    assert searcher.lexical.search("tržní řád", k=2)[0][0] == 1


def test_zero_query_vector_means_word_search_only():
    # bez modelu není embedding dotazu (samé nuly): hledá se jen podle slov a nic náhodného se nepřimíchá
    index = make_index(TEXTS, [unit(1, 0, 0), unit(0, 1, 0), unit(1, 1, 0), unit(0, 0, 1)])
    hits = Searcher(index).search("rozpočet zastupitelstvo", np.zeros(3, dtype=np.float32), k=4)
    assert [h.chunk.anchor for h in hits] == ["b1"]
    assert hits[0].similarity == 0.0


def test_k_limits_results():
    index = make_index(TEXTS, [unit(1, 0, 0), unit(0, 1, 0), unit(1, 1, 0), unit(0, 0, 1)])
    assert len(Searcher(index).search("města", unit(1, 0, 0), k=2)) == 2


def test_empty_query_returns_empty():
    index = make_index(TEXTS, [unit(1, 0, 0)] * 4)
    assert Searcher(index).search("   ", unit(1, 0, 0)) == []
    assert LexicalIndex(TEXTS).search("", k=5) == []
    assert LexicalIndex([]).search("cokoli", k=5) == []


def test_tokenize_unifies_adjective_forms():
    # „noční“ a „nočního“ dávaly různé kusy: dotaz „noční klid“ nenašel „doba nočního klidu“ a kontrola opory
    # zahodila správnou odpověď (měřeno 12. 10. 2026)
    assert len(set(tokenize("noční nočního nočnímu nočním nočních"))) == 1
    assert len(set(tokenize("veřejný veřejného veřejném veřejnou veřejných veřejnými veřejné"))) == 1
    assert len(set(tokenize("odpad odpadu odpadový odpadového odpadovém odpadové"))) == 1
    assert tokenize("slova") == ["slov"]                # krátký kmen se o „ova“ nekrátí


def test_tokenize_unifies_irregular_nouns():
    # „pes“ se skloňuje nepravidelně a v předpisech obcí je všude (poplatek ze psů, pohyb psů)
    assert set(tokenize("pes psa psovi psů psům psy psem psech")) == {"pes"}
    assert set(tokenize("dítě dítěte děti dětí dětem")) == {"dite"}


def test_slouceni_poradi_s_vahou_da_prednost_vyznamu():
    """Slovní hledání je na běžně položené otázky slabší než hledání podle významu (měřeno 12. 10. 2026) – při stejné
    váze stahovalo správný úsek dolů. Váha říká, kolikrát víc platí místo v prvním pořadí."""
    from chatbot.search.rank import rrf_merge

    meaning, words = [(2, 0.9), (3, 0.8)], [(1, 9.0), (2, 8.0)]
    equal = [item for item, _ in rrf_merge([meaning, words])]
    assert equal[0] == 2 and equal.index(1) < equal.index(3)            # stejná váha: první podle slov před druhým podle významu
    weighted = [item for item, _ in rrf_merge([meaning, words], [2.0, 1.0])]
    assert weighted[0] == 2 and weighted.index(3) < weighted.index(1)


def test_hledani_pouziva_vahu_vyznamu(monkeypatch):
    from chatbot.search import hybrid

    # úsek 0 je první podle slov a druhý podle významu, úsek 1 první podle významu a slovy se nenajde
    index = make_index(["Poplatek ze psů platí držitel psa.", "Úhrada je splatná do 31. března."], [unit(0.2, 1), unit(1, 0.1)])
    order = lambda: [hit.chunk.anchor for hit in Searcher(index).search("poplatek ze psů držitel", unit(1, 0), k=2)]
    monkeypatch.setattr(hybrid, "MEANING_WEIGHT", 1.0)
    assert order() == ["b0", "b1"]              # stejná váha: úsek nalezený oběma způsoby je první
    monkeypatch.setattr(hybrid, "MEANING_WEIGHT", 100.0)
    assert order() == ["b1", "b0"]              # velká váha významu pořadí otočí (od 63 při RRF_K = 60)


def test_presny_odkaz_na_clanek_jde_do_vysledku_cely(monkeypatch):
    """„Co říká čl. 4 vyhlášky 5/2030?“ – odstavce toho článku patří do podkladu všechny, i když je v pořadí
    přeskočí kratší úseky (měřeno 12. 10. 2026: model dostal odstavec 2, odstavec 1 se sazbami ne)."""
    def chunk(n, doc, article, paragraph, text):
        return Chunk(id=f"{n}:b", doc_id=n, path=[doc, article, paragraph], label=f"{article}, {paragraph}", text=text, anchor="b")

    dog, waste = "Vyhláška č. 5/2030 o poplatku ze psů", "Vyhláška č. 6/2030 o odpadu"
    chunks = [chunk(0, dog, "čl. 4 Sazba poplatku", "odst. 1", "Sazba činí za prvního psa 250 Kč a za druhého a každého dalšího 1 400 Kč, " * 6),
              chunk(1, dog, "čl. 4 Sazba poplatku", "odst. 2", "Poplatek se platí v poměrné výši."),
              chunk(2, dog, "čl. 5 Splatnost", "odst. 1", "Poplatek je splatný do 31. března."),
              chunk(3, waste, "čl. 4 Sazba poplatku", "odst. 1", "Sazba činí 650 Kč.")]
    index = Index(manifest={}, docs={}, chunks=chunks, vectors=np.stack([unit(0, 1), unit(0.1, 1), unit(1, 0), unit(1, 0.1)]))
    searcher = Searcher(index)
    hits = searcher.search("co říká čl. 4 vyhlášky 5/2030", unit(1, 0), k=1)
    assert {hit.chunk.id for hit in hits} >= {"0:b", "1:b"} and "3:b" not in {hit.chunk.id for hit in hits[1:]}
    # samotné „čl. 4“ bez čísla předpisu sedí na víc předpisů; když je takových úseků moc, nepřidá se nic
    from chatbot.search import hybrid
    monkeypatch.setattr(hybrid, "EXACT_MAX", 2)
    assert len(searcher.search("co říká čl. 4", unit(1, 0), k=1)) == 1
    # dotaz bez odkazu se nemění
    assert len(searcher.search("kolik stojí pes", unit(1, 0), k=1)) == 1
