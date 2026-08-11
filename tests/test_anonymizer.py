"""Testy pseudonymizéru (spec kap. 7.2, kap. 16)."""

from app.llm.anonymizer import Anonymizer

KNOWN_CONTACTS = [
    ("Jan Novák", "jan.novak@firma.cz"),
    ("Petr Svoboda", "petr.svoboda@firma.cz"),
]


def test_round_trip_jmeno() -> None:
    anonymizer = Anonymizer(KNOWN_CONTACTS)
    text = "Aplikaci spravuje Jan Novák."
    pseudonymized = anonymizer.pseudonymize(text)
    assert anonymizer.restore(pseudonymized) == text


def test_round_trip_email() -> None:
    anonymizer = Anonymizer(KNOWN_CONTACTS)
    text = "Kontakt: jan.novak@firma.cz"
    pseudonymized = anonymizer.pseudonymize(text)
    assert anonymizer.restore(pseudonymized) == text


def test_round_trip_telefon() -> None:
    anonymizer = Anonymizer(KNOWN_CONTACTS)
    text = "Zavolejte na +420 601 123 456."
    pseudonymized = anonymizer.pseudonymize(text)
    assert anonymizer.restore(pseudonymized) == text


def test_round_trip_kombinace_vsech_typu() -> None:
    anonymizer = Anonymizer(KNOWN_CONTACTS)
    text = "Sumarizace smluv, technicky spravuje Jan Novák (jan.novak@firma.cz), tel. +420-601-123-456."
    pseudonymized = anonymizer.pseudonymize(text)
    assert anonymizer.restore(pseudonymized) == text


def test_puvodni_hodnoty_se_v_pseudonymizovanem_textu_nevyskytuji() -> None:
    anonymizer = Anonymizer(KNOWN_CONTACTS)
    text = "Jan Novák (jan.novak@firma.cz), tel. 601123456."
    pseudonymized = anonymizer.pseudonymize(text)
    assert "Jan Novák" not in pseudonymized
    assert "jan.novak@firma.cz" not in pseudonymized
    assert "601123456" not in pseudonymized


def test_dve_ruzne_osoby_dostanou_ruzne_placeholdery() -> None:
    anonymizer = Anonymizer(KNOWN_CONTACTS)
    text = "Vlastník Jan Novák, zástupce Petr Svoboda."
    pseudonymized = anonymizer.pseudonymize(text)
    assert "[OSOBA_1]" in pseudonymized
    assert "[OSOBA_2]" in pseudonymized
    assert pseudonymized.count("[OSOBA_1]") == 1
    assert pseudonymized.count("[OSOBA_2]") == 1


def test_stejna_osoba_dvakrat_dostane_stejny_placeholder() -> None:
    anonymizer = Anonymizer(KNOWN_CONTACTS)
    text = "Jan Novák aplikaci založil. Jan Novák ji i spravuje."
    pseudonymized = anonymizer.pseudonymize(text)
    assert pseudonymized.count("[OSOBA_1]") == 2
    assert "[OSOBA_2]" not in pseudonymized


def test_stejny_email_dvakrat_dostane_stejny_placeholder() -> None:
    anonymizer = Anonymizer(KNOWN_CONTACTS)
    text = "jan.novak@firma.cz je vlastník, jan.novak@firma.cz je i správce."
    pseudonymized = anonymizer.pseudonymize(text)
    assert pseudonymized.count("[EMAIL_1]") == 2


def test_nova_instance_nezna_mapovani_stare() -> None:
    prvni = Anonymizer(KNOWN_CONTACTS)
    text = "Jan Novák (jan.novak@firma.cz), tel. 601123456."
    pseudonymized = prvni.pseudonymize(text)

    druhy = Anonymizer(KNOWN_CONTACTS)
    restored = druhy.restore(pseudonymized)

    assert restored == pseudonymized


def test_text_bez_osobnich_udaju_se_nemeni() -> None:
    anonymizer = Anonymizer(KNOWN_CONTACTS)
    text = "Aplikace sumarizuje smlouvy a generuje report jednou týdně."
    assert anonymizer.pseudonymize(text) == text


def test_cele_jmeno_se_nahradi_jako_celek() -> None:
    # Kratší jméno ("Novák") je v known_contacts uvedeno první, aby test
    # ověřil, že se přesto nahrazuje delší "Jan Novák" jako celek.
    known_contacts = [
        ("Novák", "novak@firma.cz"),
        ("Jan Novák", "jan.novak@firma.cz"),
    ]
    anonymizer = Anonymizer(known_contacts)
    text = "Kontaktujte Jan Novák ohledně aplikace."
    pseudonymized = anonymizer.pseudonymize(text)
    assert "[OSOBA_1]" in pseudonymized
    assert "Novák" not in pseudonymized
    # Nesmí zůstat rozbité na "[OSOBA_1] Novák" ani podobně.
    assert pseudonymized == "Kontaktujte [OSOBA_1] ohledně aplikace."


def test_email_v_zavorce_a_za_carkou() -> None:
    anonymizer = Anonymizer(KNOWN_CONTACTS)
    text = "Správce (jan.novak@firma.cz), viz kontakt petr.svoboda@firma.cz, dotaz."
    pseudonymized = anonymizer.pseudonymize(text)
    assert "[EMAIL_1]" in pseudonymized
    assert "[EMAIL_2]" in pseudonymized
    assert "(jan.novak@firma.cz)" not in pseudonymized
    assert "jan.novak@firma.cz" not in pseudonymized
    assert "petr.svoboda@firma.cz" not in pseudonymized


def test_kratke_cislo_se_nechyta_jako_telefon() -> None:
    anonymizer = Anonymizer(KNOWN_CONTACTS)
    text = "Verze dokumentu 12345, revize 987."
    pseudonymized = anonymizer.pseudonymize(text)
    assert "[TEL_1]" not in pseudonymized
    assert pseudonymized == text


def test_repr_nevypisuje_mapu() -> None:
    anonymizer = Anonymizer(KNOWN_CONTACTS)
    anonymizer.pseudonymize("Jan Novák (jan.novak@firma.cz), tel. 601123456.")
    representation = repr(anonymizer)
    assert "Jan Novák" not in representation
    assert "jan.novak@firma.cz" not in representation
    assert "601123456" not in representation
    assert "osoby=1" in representation
    assert "emaily=1" in representation
    assert "telefony=1" in representation
