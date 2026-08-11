"""Deterministické legislativní signály (spec kap. 5.4).

Signály GDPR / AI-ACT / DORA vznikají v kódu na základě odpovědí dotazníku
a AI komponent. LLM je nemůže ani vytvořit, ani odstranit — jsou to čisté
governance alarmy. Whitelist zkratek je zakódovaný; neznámá zkratka se
nikdy neobjeví.

Struktura `Flag`: {zkratka, titulek, detail, reason_code, source}
UI zobrazí badge se zkratkou, tooltip s titulkem a detailem. Disclaimer:
signály jsou podněty k review, ne právní verdikty.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas import (
    DotaznikOdpovedi,
    KomponentaInfo,
    Kritictnost,
    OsobniUdaje,
    RozhodovaniOLidech,
)
from app.services.policy import has_external_provider

ALLOWED_FLAGS = frozenset({"GDPR", "AI-ACT", "DORA"})


@dataclass(frozen=True)
class Flag:
    """Jeden legislativní signál — deterministic, nikdy vytvořený LLM."""

    zkratka: str
    titulek: str
    detail: str
    reason_code: str
    source: str = "deterministic_rule"

    def __post_init__(self) -> None:
        """Validace: zkratka musí být v whitelistu."""
        if self.zkratka not in ALLOWED_FLAGS:
            raise ValueError(
                f"Neznámá zkratka '{self.zkratka}'. "
                f"Povolené: {sorted(ALLOWED_FLAGS)}."
            )

    def to_dict(self) -> dict:
        """Serializace do JSON pro uložení do DB (klasifikace_priznaky)."""
        return {
            "zkratka": self.zkratka,
            "titulek": self.titulek,
            "detail": self.detail,
            "reason_code": self.reason_code,
            "source": self.source,
        }


def compute_flags(
    answers: DotaznikOdpovedi, components: list[KomponentaInfo]
) -> list[Flag]:
    """Vyhodnotí deterministické legislativní signály.

    Pravidla dle spec kap. 5.4:
    - GDPR: ot. 4 ≠ Ne (zpracovává osobní údaje)
    - AI-ACT: ot. 5 ≠ Ne (rozhoduje o lidech)
    - DORA: kritický proces AND externí AI provider

    Vrací seznam `Flag` objektů. Pouze signály, které podmínky splňují.
    Výstup se serializuje do `applications.klasifikace_priznaky` (JSON).
    """
    flags: list[Flag] = []

    # --- GDPR: osobní údaje zaměstnanců nebo klientů ---
    if answers.osobni_udaje != OsobniUdaje.NE:
        if answers.osobni_udaje == OsobniUdaje.ZAMESTNANCU:
            titulek = "GDPR — údaje zaměstnanců"
            detail = (
                "Aplikace zpracovává osobní údaje zaměstnanců. Vyžaduje "
                "zhodnocení legality zpracování pod GDPR."
            )
        else:  # OsobniUdaje.KLIENTU
            titulek = "GDPR — údaje klientů"
            detail = (
                "Aplikace zpracovává osobní údaje klientů. Jedná se o nejpřísnější "
                "režim GDPR; vyžaduje právní review a zajištění souladu."
            )

        flags.append(
            Flag(
                zkratka="GDPR",
                titulek=titulek,
                detail=detail,
                reason_code="PERSONAL_DATA_PROCESSING",
            )
        )

    # --- AI-ACT: rozhodování o lidech ---
    if answers.rozhodovani != RozhodovaniOLidech.NE:
        if answers.rozhodovani == RozhodovaniOLidech.DOPORUCUJE:
            titulek = "EU AI Act — doporučující rozhodování"
            detail = (
                "Aplikace doporučuje rozhodnutí o konkrétní osobě. Vyžaduje "
                "human-in-the-loop governance; člověk finalizuje rozhodnutí."
            )
        else:  # RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY
            titulek = "EU AI Act — automatizované rozhodování (vysoké riziko)"
            detail = (
                "Aplikace rozhoduje automatizovaně o konkrétní osobě bez lidské "
                "kontroly. Silný signál vysokého rizika dle Přílohy III EU AI Act — "
                "vyžaduje governance review a právní posouzení před nasazením. "
                "Není to právní závěr, ale podnět k prověření."
            )

        flags.append(
            Flag(
                zkratka="AI-ACT",
                titulek=titulek,
                detail=detail,
                reason_code="DECISION_ABOUT_PERSON",
            )
        )

    # --- DORA: kritický proces s externím AI providerem ---
    if (
        answers.kritictnost == Kritictnost.KRITICKY
        and has_external_provider(components)
    ):
        flags.append(
            Flag(
                zkratka="DORA",
                titulek="DORA — kritické operace s externím AI",
                detail=(
                    "Aplikace provádí kritické operace skrze externí AI providera. "
                    "Vyžaduje interní review bezpečnosti a compliance; signál "
                    "k dokumentaci SLA a failure recovery plan."
                ),
                reason_code="CRITICAL_OPERATIONS_EXTERNAL_PROVIDER",
            )
        )

    # --- Validace: všechny zkratky musí být v ALLOWED_FLAGS ---
    for flag in flags:
        # __post_init__ už validuje, ale pojistka pro jistotu
        assert flag.zkratka in ALLOWED_FLAGS, (
            f"Flag '{flag.zkratka}' se dostal mimo whitelist. "
            f"Chyba v compute_flags()."
        )

    return flags
