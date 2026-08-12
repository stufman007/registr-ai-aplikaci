"""Testy deterministických eskalačních minim (spec kap. 16).

Pokrývá: každé z 8 pravidel zvlášť, kombinaci více pravidel (nejvyšší
minimum vyhrává), LLM MALA + floor VELKA -> effective VELKA, ruční zvýšení
nad minimum, ruční snížení pod minimum -> PolicyViolation, žádné pravidlo
-> MALA.
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
    Tier,
    ViditelnostVystupu,
)
from app.services.policy import (
    ManualBelowSuggestion,
    PolicyViolation,
    compute_minimum,
    effective_tier,
)


def nevinne_odpovedi(**overrides: object) -> DotaznikOdpovedi:
    """Factory: odpovědi, které neaktivují žádné policy pravidlo.

    Testy mění jen pole relevantní pro dané pravidlo přes ``overrides``.
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
    """Jedna neutrální komponenta hostovaná ve firemním cloudu (ne externí API)."""
    return [KomponentaInfo(provider=Provider.INTERNI, hosting_type=HostingType.FIREMNI_CLOUD)]


def externi_komponenta() -> list[KomponentaInfo]:
    return [KomponentaInfo(provider=Provider.OPENAI, hosting_type=HostingType.EXTERNI_API)]


def reason_codes(triggered: list) -> set[str]:
    return {rule.reason_code for rule in triggered}


# --- Žádné pravidlo ---


def test_zadne_pravidlo_dava_malou() -> None:
    result = compute_minimum(nevinne_odpovedi(), interni_komponenta())
    assert result.tier == Tier.MALA
    assert result.triggered_rules == []


# --- Jednotlivá pravidla (spec kap. 5.3) ---


def test_auto_decision_person() -> None:
    answers = nevinne_odpovedi(rozhodovani=RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY)
    result = compute_minimum(answers, interni_komponenta())
    assert result.tier == Tier.VELKA
    assert reason_codes(result.triggered_rules) == {"AUTO_DECISION_PERSON"}


def test_critical_external_output() -> None:
    answers = nevinne_odpovedi(
        kritictnost=Kritictnost.KRITICKY,
        viditelnost=ViditelnostVystupu.ANO_PRIMO,
    )
    result = compute_minimum(answers, interni_komponenta())
    assert result.tier == Tier.VELKA
    assert "CRITICAL_EXTERNAL_OUTPUT" in reason_codes(result.triggered_rules)


def test_autonomous_high_impact() -> None:
    answers = nevinne_odpovedi(
        autonomie=Autonomie.ANO_AUTOMATICKY,
        dopad=DopadChyby.ZASADNI,
    )
    result = compute_minimum(answers, interni_komponenta())
    assert result.tier == Tier.VELKA
    assert "AUTONOMOUS_HIGH_IMPACT" in reason_codes(result.triggered_rules)


def test_client_personal_data() -> None:
    answers = nevinne_odpovedi(osobni_udaje=OsobniUdaje.KLIENTU)
    result = compute_minimum(answers, interni_komponenta())
    assert result.tier == Tier.STREDNI
    assert reason_codes(result.triggered_rules) == {"CLIENT_PERSONAL_DATA"}


def test_critical_process() -> None:
    answers = nevinne_odpovedi(kritictnost=Kritictnost.KRITICKY)
    result = compute_minimum(answers, interni_komponenta())
    assert result.tier == Tier.STREDNI
    assert reason_codes(result.triggered_rules) == {"CRITICAL_PROCESS"}


def test_highly_sensitive_data() -> None:
    answers = nevinne_odpovedi(citlivost=CitlivostDat.VYSOCE_CITLIVA)
    result = compute_minimum(answers, interni_komponenta())
    assert result.tier == Tier.STREDNI
    assert reason_codes(result.triggered_rules) == {"HIGHLY_SENSITIVE_DATA"}


def test_autonomous_action() -> None:
    answers = nevinne_odpovedi(autonomie=Autonomie.ANO_AUTOMATICKY)
    result = compute_minimum(answers, interni_komponenta())
    assert result.tier == Tier.STREDNI
    assert reason_codes(result.triggered_rules) == {"AUTONOMOUS_ACTION"}


def test_external_provider_sensitive_data() -> None:
    answers = nevinne_odpovedi(citlivost=CitlivostDat.DUVERNA)
    result = compute_minimum(answers, externi_komponenta())
    assert result.tier == Tier.STREDNI
    assert reason_codes(result.triggered_rules) == {"EXTERNAL_PROVIDER_SENSITIVE_DATA"}


# --- Kombinace pravidel ---


def test_kombinace_pravidel_vraci_nejvyssi_minimum() -> None:
    answers = nevinne_odpovedi(
        osobni_udaje=OsobniUdaje.KLIENTU,  # STREDNI
        rozhodovani=RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY,  # VELKA
    )
    result = compute_minimum(answers, interni_komponenta())
    assert result.tier == Tier.VELKA
    codes = reason_codes(result.triggered_rules)
    assert {"CLIENT_PERSONAL_DATA", "AUTO_DECISION_PERSON"} <= codes


# --- effective_tier ---


def test_llm_mala_a_floor_velka_da_effective_velku() -> None:
    minimum = compute_minimum(
        nevinne_odpovedi(rozhodovani=RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY),
        interni_komponenta(),
    )
    assert effective_tier(Tier.MALA, minimum) == Tier.VELKA


def test_rucni_zvyseni_nad_minimum_je_povoleno() -> None:
    minimum = compute_minimum(
        nevinne_odpovedi(osobni_udaje=OsobniUdaje.KLIENTU), interni_komponenta()
    )
    assert minimum.tier == Tier.STREDNI
    assert effective_tier(Tier.MALA, minimum, manual_tier=Tier.VELKA) == Tier.VELKA


def test_rucni_snizeni_pod_minimum_vyhodi_policy_violation() -> None:
    minimum = compute_minimum(
        nevinne_odpovedi(rozhodovani=RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY),
        interni_komponenta(),
    )
    assert minimum.tier == Tier.VELKA
    with pytest.raises(PolicyViolation) as exc_info:
        effective_tier(Tier.MALA, minimum, manual_tier=Tier.STREDNI)
    assert "AUTO_DECISION_PERSON" in str(exc_info.value)


def test_effective_tier_bez_llm_a_bez_pravidel_je_mala() -> None:
    minimum = compute_minimum(nevinne_odpovedi(), interni_komponenta())
    assert effective_tier(None, minimum) == Tier.MALA


# --- actor_is_admin: regresní bug (VELKÁ→STŘEDNÍ se tiše nepřebije zpět) ---


def test_admin_smi_snizit_pod_ai_navrh_nad_minimem() -> None:
    """Regresní test hlášeného bugu: AI navrhne VELKA, minimum je MALA, admin
    ručně sníží na STREDNI. Dřív `effective_tier` počítala
    `max(llm_tier, minimum, manual)`, takže se snížení tiše přebilo zpět na
    VELKA — teď musí platit admin volba."""
    minimum = compute_minimum(nevinne_odpovedi(), interni_komponenta())
    assert minimum.tier == Tier.MALA
    vysledek = effective_tier(
        Tier.VELKA, minimum, manual_tier=Tier.STREDNI, actor_is_admin=True
    )
    assert vysledek == Tier.STREDNI


def test_admin_snizeni_pod_minimum_stale_vyhazuje_policy_violation() -> None:
    minimum = compute_minimum(
        nevinne_odpovedi(osobni_udaje=OsobniUdaje.KLIENTU), interni_komponenta()
    )
    assert minimum.tier == Tier.STREDNI
    with pytest.raises(PolicyViolation):
        effective_tier(
            Tier.VELKA, minimum, manual_tier=Tier.MALA, actor_is_admin=True
        )


def test_uzivatel_snizeni_pod_navrh_vyhazuje_manual_below_suggestion() -> None:
    """Ne-admin smí návrh (`max(LLM, minimum)`) jen potvrdit nebo zvýšit."""
    minimum = compute_minimum(nevinne_odpovedi(), interni_komponenta())
    assert minimum.tier == Tier.MALA
    with pytest.raises(ManualBelowSuggestion) as exc_info:
        effective_tier(
            Tier.VELKA, minimum, manual_tier=Tier.STREDNI, actor_is_admin=False
        )
    assert exc_info.value.suggestion == Tier.VELKA
    assert exc_info.value.manual_tier == Tier.STREDNI


def test_uzivatel_zvyseni_nad_navrh_je_povoleno() -> None:
    minimum = compute_minimum(nevinne_odpovedi(), interni_komponenta())
    vysledek = effective_tier(
        Tier.MALA, minimum, manual_tier=Tier.VELKA, actor_is_admin=False
    )
    assert vysledek == Tier.VELKA
