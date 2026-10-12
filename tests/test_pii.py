"""Brána na osobní údaje – orientační počty nálezů v textu dokumentu."""
from chatbot.index.pii import has_findings, scan_pii


def test_scan_pii_counts():
    text = ("Kontakt: jan.novak@horniaickov.example. Vyřizuje Ing. Jana Nováková, případně paní Dvořáková. "
            "Rodné číslo 850101/1234.")
    assert scan_pii(text) == {"email_osobni": 1, "jmeno_s_titulem": 1, "osloveni_prijmeni": 1, "rodne_cislo": 1,
                              "telefon_mobilni": 0}


def test_scan_pii_counts_mobile_numbers_only():
    # mobil s předvolbou i bez; pevná linka úřadu, částka v korunách ani číslo usnesení se nepočítají
    # čísla jsou vymyšlená
    text = ("Správce: 777 111 222, pohotovost +420 602111222. Ústředna 555 123 456. "
            "Rozpočet 600 000 000 Kč, usnesení 1234/2026, zakázka nad 10000000 Kč.")
    assert scan_pii(text)["telefon_mobilni"] == 2


def test_scan_pii_ignores_functional_mailbox():
    counts = scan_pii("Podání posílejte na podatelna@horniaickov.example nebo posta@horniaickov.example.")
    assert counts["email_osobni"] == 0
    assert not has_findings(counts)


def test_scan_pii_ignores_reference_numbers_and_function_names():
    counts = scan_pii("Usnesení č. 1234/2026. Schvaluje tajemník městského úřadu a vedoucí odboru financí.")
    assert not has_findings(counts)


def test_has_findings():
    assert has_findings({"email_osobni": 0, "jmeno_s_titulem": 2, "osloveni_prijmeni": 0, "rodne_cislo": 0})
