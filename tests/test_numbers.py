"""Čísla v odpovědi: co odpověď tvrdí číslem nebo měsícem, musí být v citovaných úsecích."""
from chatbot.guard.numbers import answer_numbers, source_numbers, unsupported_numbers


def test_cisla_v_odpovedi_v_jednotnem_tvaru():
    assert answer_numbers("Poplatek je 2 000 Kč, za druhého psa 2.500,- Kč.") == {"2000", "2500"}
    assert answer_numbers("Noční klid je od 22:00 do 6:00, tržiště zavírá ve 20:30.") == {"22", "6", "20", "30"}
    assert answer_numbers("Koeficient je 2,5 a sazba 650,00 Kč, tedy 0,5 %.") == {"2,5", "650", "0,5"}
    assert answer_numbers("Účinnost od 01. 09. 2024.") == {"1", "9", "2024"}


def test_mesic_slovem_je_cislo():
    assert answer_numbers("Splatnost je do 31. března, ohlášení do 15. května.") == {"31", "3", "15", "5"}
    assert answer_numbers("V červenci a v září se neplatí.") == {"7", "9"}


def test_cislovani_radku_a_znacky_zdroju_nejsou_tvrzeni():
    assert answer_numbers("1. Podejte ohlášení [2].\n2) Zaplaťte do 30 dnů [1][3].") == {"30"}


def test_cisla_slovem_se_ctou_jen_v_podkladu():
    # odpověď smí napsat „jeden pes“, i když podklad říká „za psa“ – slova se v odpovědi nehlídají
    assert answer_numbers("Platí se za jeden nebo dva psy.") == set()
    numbers = source_numbers(["Dobou nočního klidu se rozumí doba od dvacáté druhé do šesté hodiny."])
    assert {"22", "6"} <= numbers
    assert {"5", "15", "3", "12"} <= source_numbers(["nejméně pět dnů předem", "do patnáctého dne", "třikrát ročně",
                                                     "o jednu dvanáctinu"])


def test_cislo_ktere_v_podkladu_neni():
    zdroj = ["Poplatek je splatný nejpozději do 30. 9. příslušného roku."]
    assert unsupported_numbers("Poplatek je splatný do 30. 12. [1].", zdroj) == {"12"}
    assert unsupported_numbers("Poplatek je splatný do 30. prosince [1].", zdroj) == {"12"}
    assert unsupported_numbers("Poplatek je splatný do 30. září [1].", zdroj) == set()


# --- nálezy nezávislé kontroly (12. 10. 2026) ---

def test_cislo_v_hranatych_zavorkach_neni_znacka_zdroje():
    # značka zdroje má nejvýš tři číslice; „[1500] Kč“ je částka, kterou si někdo nechal dát do závorek
    assert answer_numbers("Sazba činí [1500] Kč [1].") == {"1500"}
    assert answer_numbers("Sazba činí 500 Kč [1, 2][12].") == {"500"}


def test_den_na_zacatku_radku_neni_cislovani():
    assert answer_numbers("30. dubna je poplatek splatný [1].") == {"30", "4"}
    assert answer_numbers("1. Podejte ohlášení.\n2. Zaplaťte do 30 dnů.") == {"30"}     # řada 1, 2 číslování je
    assert answer_numbers("2. Zaplaťte do 30 dnů.") == {"2", "30"}                      # osamělé číslo ne


def test_nula_s_jednotkou_je_tvrzeni():
    assert answer_numbers("Sazba poplatku činí 0 Kč [1].") == {"0"}
    assert answer_numbers("Noční klid je od 22:00 do 6:00.") == {"22", "6"}             # minuty „00“ tvrzení nejsou


def test_rada_cisel_ve_zdroji_se_cte_i_po_castech():
    # tabulka slitá z PDF a telefon: skupiny číslic vypadají jako tisíce, ale jsou to samostatná čísla
    assert {"5", "100", "150", "200"} <= source_numbers(["do 5 m2 100 150 200 Kč"])
    assert "555123456" in source_numbers(["telefon +420 555 123 456"])
    assert unsupported_numbers("Volejte 555 123 456 [1].", ["telefon +420 555 123 456"]) == set()
    assert source_numbers(["9" * 5000 + " a " + "1 " + "000 " * 2000])                 # dlouhá řada číslic nesmí skončit výjimkou
    assert answer_numbers("7" * 5000)


def test_dalsi_tvary_cislovek_ve_zdroji():
    numbers = source_numbers(["nejméně jeden měsíc předem", "do devíti dnů", "ke třem osobám", "dvě stě korun", "pět set korun",
                              "dva tisíce pět set korun", "od 6,30 hodin"])
    assert {"1", "9", "3", "200", "500", "2000", "2500", "6", "30"} <= numbers
