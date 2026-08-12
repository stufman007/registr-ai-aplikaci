"""Deterministická eskalační minima klasifikačního tieru (spec kap. 5.3).

LLM není policy engine. Tento modul rozhoduje o nejnižším povoleném tieru
(`klasifikace_minimum`) na základě odpovědí dotazníku a AI komponent. Pravidla
jsou deklarativní tabulka (ne řetěz `if`) — nové pravidlo je nový řádek v
`RULES`, testovatelné bez databáze, přihlášení i internetu.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.schemas import (
    Autonomie,
    CitlivostDat,
    DopadChyby,
    DotaznikOdpovedi,
    HostingType,
    KomponentaInfo,
    Kritictnost,
    OsobniUdaje,
    RozhodovaniOLidech,
    Tier,
    ViditelnostVystupu,
)

RULES_VERSION = "2026-08-v1"


@dataclass(frozen=True)
class PolicyRule:
    """Jedno deterministické eskalační pravidlo."""

    reason_code: str
    min_tier: Tier
    popis: str
    predicate: Callable[[DotaznikOdpovedi, list[KomponentaInfo]], bool]


def has_external_provider(components: list[KomponentaInfo]) -> bool:
    """True, pokud alespoň jedna AI komponenta běží u externího API providera."""
    return any(c.hosting_type == HostingType.EXTERNI_API for c in components)


def _je_kriticky_a_vystup_mimo_firmu(
    answers: DotaznikOdpovedi, components: list[KomponentaInfo]
) -> bool:
    return (
        answers.kritictnost == Kritictnost.KRITICKY
        and answers.viditelnost != ViditelnostVystupu.NE
    )


def _je_autonomni_a_zasadni_dopad(
    answers: DotaznikOdpovedi, components: list[KomponentaInfo]
) -> bool:
    return (
        answers.autonomie == Autonomie.ANO_AUTOMATICKY
        and answers.dopad == DopadChyby.ZASADNI
    )


def _externi_provider_a_citliva_data(
    answers: DotaznikOdpovedi, components: list[KomponentaInfo]
) -> bool:
    return has_external_provider(components) and answers.citlivost in (
        CitlivostDat.DUVERNA,
        CitlivostDat.VYSOCE_CITLIVA,
    )


RULES: list[PolicyRule] = [
    PolicyRule(
        reason_code="AUTO_DECISION_PERSON",
        min_tier=Tier.VELKA,
        popis="Aplikace rozhoduje automatizovaně o lidech.",
        predicate=lambda a, c: a.rozhodovani == RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY,
    ),
    PolicyRule(
        reason_code="CRITICAL_EXTERNAL_OUTPUT",
        min_tier=Tier.VELKA,
        popis="Kritický proces s výstupem viditelným mimo firmu.",
        predicate=_je_kriticky_a_vystup_mimo_firmu,
    ),
    PolicyRule(
        reason_code="AUTONOMOUS_HIGH_IMPACT",
        min_tier=Tier.VELKA,
        popis="Automatická akce bez člověka se zásadním potenciálním dopadem.",
        predicate=_je_autonomni_a_zasadni_dopad,
    ),
    PolicyRule(
        reason_code="CLIENT_PERSONAL_DATA",
        min_tier=Tier.STREDNI,
        popis="Zpracovává osobní údaje klientů.",
        predicate=lambda a, c: a.osobni_udaje == OsobniUdaje.KLIENTU,
    ),
    PolicyRule(
        reason_code="CRITICAL_PROCESS",
        min_tier=Tier.STREDNI,
        popis="Podporuje kritický proces.",
        predicate=lambda a, c: a.kritictnost == Kritictnost.KRITICKY,
    ),
    PolicyRule(
        reason_code="HIGHLY_SENSITIVE_DATA",
        min_tier=Tier.STREDNI,
        popis="Zpracovává vysoce citlivá data.",
        predicate=lambda a, c: a.citlivost == CitlivostDat.VYSOCE_CITLIVA,
    ),
    PolicyRule(
        reason_code="AUTONOMOUS_ACTION",
        min_tier=Tier.STREDNI,
        popis="Výstup AI může vyvolat akci automaticky, bez kontroly člověkem.",
        predicate=lambda a, c: a.autonomie == Autonomie.ANO_AUTOMATICKY,
    ),
    PolicyRule(
        reason_code="EXTERNAL_PROVIDER_SENSITIVE_DATA",
        min_tier=Tier.STREDNI,
        popis="Externí AI provider zpracovává důvěrná nebo vysoce citlivá data.",
        predicate=_externi_provider_a_citliva_data,
    ),
]


@dataclass(frozen=True)
class MinimumResult:
    """Výsledek vyhodnocení deterministických pravidel."""

    tier: Tier
    triggered_rules: list[PolicyRule]


def compute_minimum(
    answers: DotaznikOdpovedi, components: list[KomponentaInfo]
) -> MinimumResult:
    """Spočítá `klasifikace_minimum` = max aktivovaných pravidel, jinak MALA."""
    triggered = [rule for rule in RULES if rule.predicate(answers, components)]
    tier = max((rule.min_tier for rule in triggered), default=Tier.MALA)
    return MinimumResult(tier=tier, triggered_rules=triggered)


class PolicyViolation(Exception):
    """Pokus o uložení efektivního tieru pod deterministické policy minimum."""

    def __init__(self, minimum: MinimumResult, manual_tier: Tier) -> None:
        reason_codes = ", ".join(rule.reason_code for rule in minimum.triggered_rules)
        message = (
            f"Ruční tier '{manual_tier.label()}' je nižší než povinné minimum "
            f"'{minimum.tier.label()}'. Brání tomu pravidla: {reason_codes}."
        )
        super().__init__(message)
        self.minimum = minimum
        self.manual_tier = manual_tier


class ManualBelowSuggestion(Exception):
    """Běžný uživatel zkusil snížit tier pod navrhovanou hodnotu (spec kap. 5.5).

    Návrh = `max(LLM, policy minimum)`. Běžný uživatel smí návrh jen potvrdit
    nebo zvýšit — snížení (byť nad policy minimem) je vyhrazené adminovi,
    protože AI návrh se může mýlit nahoru a jen admin smí to posoudit a
    korigovat. Bez této výjimky by `effective_tier` tiché snížení buď tiše
    přebilo zpět na návrh (starý bug), nebo ho tiše uložilo bez oprávnění —
    obojí je špatně, proto explicitní chyba, kterou route vrátí jako 400.
    """

    def __init__(self, suggestion: Tier, manual_tier: Tier) -> None:
        message = (
            f"Snížení tieru pod navrhovanou hodnotu '{suggestion.label()}' smí "
            f"provést jen administrátor. Vybraný tier: '{manual_tier.label()}'."
        )
        super().__init__(message)
        self.suggestion = suggestion
        self.manual_tier = manual_tier


def effective_tier(
    llm_tier: Tier | None,
    minimum: MinimumResult,
    manual_tier: Tier | None = None,
    *,
    actor_is_admin: bool = False,
) -> Tier:
    """Efektivní tier (spec kap. 5.3 a 5.5).

    Bez ruční hodnoty: návrh = `max(LLM, policy minimum)` — LLM samo o sobě
    nikdy nesmí snížit tier pod policy floor.

    Ruční hodnota pod policy minimum je zakázaná vždy, bez ohledu na roli:
    vyhodí `PolicyViolation` — floor je jediná tvrdá hranice, kterou nesmí
    obejít ani admin.

    Ruční hodnota na/nad minimem se dál chová podle role, protože **AI není
    zdroj pravdy** — je to jen podnět, který se může mýlit oběma směry:

    - **admin**: výsledek = `max(minimum, manual_tier)`. Ruční volba admina
      platí i pod AI návrhem (AI mohla navrhnout zbytečně vysoko) — jediná
      tvrdá hranice, kterou nesmí podkročit, je policy floor.
    - **běžný uživatel**: smí návrh jen potvrdit nebo zvýšit. Snížení pod
      návrh vyhodí `ManualBelowSuggestion` — dřívější chování to tiše
      přebilo zpátky na návrh (`max(...)` přes všechny tři hodnoty), takže
      uživatel viděl uloženou poznámku, ale nesníženou klasifikaci.
    """
    suggestion = max(llm_tier or Tier.MALA, minimum.tier)

    if manual_tier is None:
        return suggestion

    if manual_tier < minimum.tier:
        raise PolicyViolation(minimum, manual_tier)

    if actor_is_admin:
        return max(minimum.tier, manual_tier)

    if manual_tier < suggestion:
        raise ManualBelowSuggestion(suggestion, manual_tier)

    return manual_tier
