"""Testy deterministických legislativních signálů (spec kap. 5.4, kap. 16).

Pokrývá:
- GDPR: osobní údaje zaměstnanců (detail obsahuje "zaměstnanců")
- GDPR: osobní údaje klientů (detail obsahuje "klientů")
- AI-ACT: rozhodování DOPORUCUJE (mírný detail)
- AI-ACT: rozhodování ROZHODUJE_AUTOMATICKY (důraznější detail)
- DORA: kritický proces a externí AI provider
- DORA: kritický proces bez externího providera → žádná DORA
- Nevinné odpovědi → prázdný seznam signálů
- Validace: žádná neznámá zkratka se nikdy neobjeví
- to_dict() vrací všech 5 klíčů
"""

from __future__ import annotations

import pytest

from app.schemas import (
    Autonomie,
    CitlivostDat,
    DopadChyby,
    DotaznikOdpovedi,
    HostingType,
    KomponentaInfo,
    Kritictnost,
    OsobniUdaje,
    PocetUzivatelu,
    Provider,
    RozhodovaniOLidech,
    ViditelnostVystupu,
)
from app.services.regulatory import ALLOWED_FLAGS, Flag, compute_flags


def nevinne_odpovedi(**overrides: object) -> DotaznikOdpovedi:
    """Factory: odpovědi, které neaktivují žádný signál.

    Testy mění jen pole relevantní pro daný signál přes ``overrides``.
    """
    zaklad: dict[str, object] = {
        "ucel": "Interní testovací nástroj bez rizika.",
        "pocet_uzivatelu": PocetUzivatelu.DO_10,
        "kritictnost": Kritictnost.POMOCNY,
        "osobni_udaje": OsobniUdaje.NE,
        "rozhodovani": RozhodovaniOLidech.NE,
        "viditelnost": ViditelnostVystupu.NE,
        "citlivost": CitlivostDat.VEREJNA,
        "autonomie": Autonomie.NE,
        "dopad": DopadChyby.ZANEDBATELNY,
    }
    zaklad.update(overrides)
    return DotaznikOdpovedi(**zaklad)  # type: ignore[arg-type]


def interni_komponenta() -> list[KomponentaInfo]:
    """Jedna interní komponenta (ne externí API)."""
    return [KomponentaInfo(provider=Provider.INTERNI, hosting_type=HostingType.FIREMNI_CLOUD)]


def externi_komponenta() -> list[KomponentaInfo]:
    """Jedna externí komponenta (OpenAI)."""
    return [KomponentaInfo(provider=Provider.OPENAI, hosting_type=HostingType.EXTERNI_API)]


# --- GDPR signály ---


def test_gdpr_osobni_udaje_zamestnancu() -> None:
    """GDPR se aktivuje pro údaje zaměstnanců; detail obsahuje slovo 'zaměstnanců'."""
    answers = nevinne_odpovedi(osobni_udaje=OsobniUdaje.ZAMESTNANCU)
    flags = compute_flags(answers, interni_komponenta())

    assert len(flags) == 1
    assert flags[0].zkratka == "GDPR"
    assert flags[0].titulek == "GDPR — údaje zaměstnanců"
    assert "zaměstnanců" in flags[0].detail.lower()
    assert flags[0].reason_code == "PERSONAL_DATA_PROCESSING"
    assert flags[0].source == "deterministic_rule"


def test_gdpr_osobni_udaje_klientu() -> None:
    """GDPR se aktivuje pro údaje klientů; detail obsahuje slovo 'klientů'."""
    answers = nevinne_odpovedi(osobni_udaje=OsobniUdaje.KLIENTU)
    flags = compute_flags(answers, interni_komponenta())

    assert len(flags) == 1
    assert flags[0].zkratka == "GDPR"
    assert flags[0].titulek == "GDPR — údaje klientů"
    assert "klientů" in flags[0].detail.lower()
    assert flags[0].reason_code == "PERSONAL_DATA_PROCESSING"


def test_bez_osobnich_udaju_bez_gdpr() -> None:
    """Bez osobních údajů se GDPR neaktivuje."""
    answers = nevinne_odpovedi(osobni_udaje=OsobniUdaje.NE)
    flags = compute_flags(answers, interni_komponenta())

    gdpr_flags = [f for f in flags if f.zkratka == "GDPR"]
    assert len(gdpr_flags) == 0


# --- AI-ACT signály ---


def test_ai_act_doporucuje() -> None:
    """AI-ACT se aktivuje pro doporučující rozhodování s mírným detailem."""
    answers = nevinne_odpovedi(rozhodovani=RozhodovaniOLidech.DOPORUCUJE)
    flags = compute_flags(answers, interni_komponenta())

    ai_act_flags = [f for f in flags if f.zkratka == "AI-ACT"]
    assert len(ai_act_flags) == 1
    flag = ai_act_flags[0]
    assert flag.titulek == "EU AI Act — doporučující rozhodování"
    assert "human-in-the-loop" in flag.detail.lower()
    assert "vysokého rizika" not in flag.detail  # mírný detail
    assert flag.reason_code == "DECISION_ABOUT_PERSON"


def test_ai_act_rozhoduje_automaticky() -> None:
    """AI-ACT s ROZHODUJE_AUTOMATICKY má důraznější titulek a detail."""
    answers = nevinne_odpovedi(rozhodovani=RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY)
    flags = compute_flags(answers, interni_komponenta())

    ai_act_flags = [f for f in flags if f.zkratka == "AI-ACT"]
    assert len(ai_act_flags) == 1
    flag = ai_act_flags[0]
    assert "vysoké riziko" in flag.titulek.lower()
    assert "automatizovaně" in flag.detail.lower()
    assert "bez lidské kontroly" in flag.detail.lower()
    assert "iii" in flag.detail.lower()  # Příloha III — jen zkontrolujeme její číslovku
    assert "governance review" in flag.detail.lower()
    assert flag.reason_code == "DECISION_ABOUT_PERSON"


def test_bez_rozhodovani_bez_ai_act() -> None:
    """Bez rozhodování se AI-ACT neaktivuje."""
    answers = nevinne_odpovedi(rozhodovani=RozhodovaniOLidech.NE)
    flags = compute_flags(answers, interni_komponenta())

    ai_act_flags = [f for f in flags if f.zkratka == "AI-ACT"]
    assert len(ai_act_flags) == 0


# --- DORA signály ---


def test_dora_kriticky_proces_s_externim_providerem() -> None:
    """DORA se aktivuje pro kritický proces s externím AI providerem."""
    answers = nevinne_odpovedi(kritictnost=Kritictnost.KRITICKY)
    flags = compute_flags(answers, externi_komponenta())

    dora_flags = [f for f in flags if f.zkratka == "DORA"]
    assert len(dora_flags) == 1
    flag = dora_flags[0]
    assert flag.titulek == "DORA — kritické operace s externím AI"
    assert "kritické operace" in flag.detail.lower()
    assert "externí" in flag.detail.lower()
    assert flag.reason_code == "CRITICAL_OPERATIONS_EXTERNAL_PROVIDER"


def test_bez_dora_pro_kriticky_bez_externiho_providera() -> None:
    """DORA se NEaktivuje pro kritický proces bez externího providera."""
    answers = nevinne_odpovedi(kritictnost=Kritictnost.KRITICKY)
    flags = compute_flags(answers, interni_komponenta())

    dora_flags = [f for f in flags if f.zkratka == "DORA"]
    assert len(dora_flags) == 0


def test_bez_dora_pro_nekriticky_s_externim_providerem() -> None:
    """DORA se NEaktivuje pro nekritický proces, i s externím providerem."""
    answers = nevinne_odpovedi(
        kritictnost=Kritictnost.POMOCNY,
    )
    flags = compute_flags(answers, externi_komponenta())

    dora_flags = [f for f in flags if f.zkratka == "DORA"]
    assert len(dora_flags) == 0


# --- Kombinace signálů ---


def test_kombinace_gdpr_a_ai_act() -> None:
    """Když jsou splněny podmínky pro GDPR i AI-ACT, obě se vrátí."""
    answers = nevinne_odpovedi(
        osobni_udaje=OsobniUdaje.KLIENTU,
        rozhodovani=RozhodovaniOLidech.DOPORUCUJE,
    )
    flags = compute_flags(answers, interni_komponenta())

    zkratky = {f.zkratka for f in flags}
    assert "GDPR" in zkratky
    assert "AI-ACT" in zkratky


def test_kombinace_vsech_tri_signalu() -> None:
    """Pokud všechny tři podmínky jsou splněny, vrátí se všechny tři signály."""
    answers = nevinne_odpovedi(
        osobni_udaje=OsobniUdaje.KLIENTU,
        rozhodovani=RozhodovaniOLidech.DOPORUCUJE,
        kritictnost=Kritictnost.KRITICKY,
    )
    flags = compute_flags(answers, externi_komponenta())

    zkratky = {f.zkratka for f in flags}
    assert zkratky == {"GDPR", "AI-ACT", "DORA"}


# --- Nevinné odpovědi ---


def test_nevinne_odpovedi_daji_prazdny_seznam() -> None:
    """Nevinné odpovědi neaktivují žádný signál."""
    answers = nevinne_odpovedi()
    flags = compute_flags(answers, interni_komponenta())

    assert flags == []


# --- ValidaceWhiteListu ---


def test_vsechny_zkratky_v_allowed_flags() -> None:
    """Žádný flag se nesmí dostat mimo ALLOWED_FLAGS; všechny signály v testu jsou validní."""
    answers = nevinne_odpovedi(
        osobni_udaje=OsobniUdaje.KLIENTU,
        rozhodovani=RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY,
        kritictnost=Kritictnost.KRITICKY,
    )
    flags = compute_flags(answers, externi_komponenta())

    for flag in flags:
        assert flag.zkratka in ALLOWED_FLAGS


def test_flag_s_neznama_zkratkou_vyhodi_valueerror() -> None:
    """Pokud by se někde dostala neznámá zkratka, __post_init__ vyhodí ValueError."""
    with pytest.raises(ValueError, match="Neznámá zkratka"):
        Flag(
            zkratka="NEZNAMA",
            titulek="Test",
            detail="Test",
            reason_code="TEST",
        )


# --- Serializace to_dict() ---


def test_flag_to_dict_ma_vsech_pet_klicu() -> None:
    """Metoda to_dict() vrací všech 5 očekávaných klíčů."""
    flag = Flag(
        zkratka="GDPR",
        titulek="Test",
        detail="Detail",
        reason_code="TEST_REASON",
        source="deterministic_rule",
    )
    d = flag.to_dict()

    assert set(d.keys()) == {"zkratka", "titulek", "detail", "reason_code", "source"}
    assert d["zkratka"] == "GDPR"
    assert d["titulek"] == "Test"
    assert d["detail"] == "Detail"
    assert d["reason_code"] == "TEST_REASON"
    assert d["source"] == "deterministic_rule"


def test_flag_to_dict_json_serializable() -> None:
    """Výstup to_dict() je JSON-serializable (pro uložení do DB)."""
    import json

    answers = nevinne_odpovedi(osobni_udaje=OsobniUdaje.ZAMESTNANCU)
    flags = compute_flags(answers, interni_komponenta())

    # Všechny flagi by měly být JSON-serializable
    for flag in flags:
        d = flag.to_dict()
        json_str = json.dumps(d)
        reloaded = json.loads(json_str)
        assert reloaded == d


# --- Parametrizovaný test: všechny kombinace vstupů nebyly nikdy vynechány ---


@pytest.mark.parametrize(
    "osobni_udaje,rozhodovani,kritictnost,hosting",
    [
        (OsobniUdaje.NE, RozhodovaniOLidech.NE, Kritictnost.POMOCNY, "interni"),
        (OsobniUdaje.ZAMESTNANCU, RozhodovaniOLidech.NE, Kritictnost.POMOCNY, "interni"),
        (OsobniUdaje.KLIENTU, RozhodovaniOLidech.NE, Kritictnost.POMOCNY, "interni"),
        (OsobniUdaje.NE, RozhodovaniOLidech.DOPORUCUJE, Kritictnost.POMOCNY, "interni"),
        (OsobniUdaje.NE, RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY, Kritictnost.POMOCNY, "interni"),
        (OsobniUdaje.NE, RozhodovaniOLidech.NE, Kritictnost.KRITICKY, "interni"),
        (OsobniUdaje.NE, RozhodovaniOLidech.NE, Kritictnost.KRITICKY, "externi"),
    ],
)
def test_paramet_compute_flags_vraci_jen_allowed_zkratky(
    osobni_udaje: OsobniUdaje,
    rozhodovani: RozhodovaniOLidech,
    kritictnost: Kritictnost,
    hosting: str,
) -> None:
    """Parametrizovaný test: všechny kombinace vrací jen validní zkratky."""
    answers = nevinne_odpovedi(
        osobni_udaje=osobni_udaje,
        rozhodovani=rozhodovani,
        kritictnost=kritictnost,
    )
    components = externi_komponenta() if hosting == "externi" else interni_komponenta()
    flags = compute_flags(answers, components)

    for flag in flags:
        assert flag.zkratka in ALLOWED_FLAGS
