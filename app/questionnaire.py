"""Deklarativní definice klasifikačního dotazníku (spec kap. 5.1).

Devět pevných otázek: první je volný text, zbývajících osm jsou výběry z enumů
v `app.schemas`. Texty tooltipů (`ⓘ`) jsou převzaté **doslova ze specifikace** —
jsou to jediné vysvětlení, které uživatel při vyplňování dostane, proto žijí
v datech, ne v šabloně.

Šablona `app_form.html` nad touto tabulkou jen iteruje; přidat otázku znamená
přidat řádek sem (a pole do `DotaznikOdpovedi`), ne psát HTML.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.schemas import (
    Autonomie,
    CitlivostDat,
    DopadChyby,
    Kritictnost,
    OsobniUdaje,
    PocetUzivatelu,
    RozhodovaniOLidech,
    ViditelnostVystupu,
)


@dataclass(frozen=True)
class Choice:
    """Jedna volba odpovědi. `tooltip` je prázdný tam, kde ho spec neuvádí."""

    value: str
    label: str
    tooltip: str = ""


@dataclass(frozen=True)
class Question:
    """Jedna otázka dotazníku. `choices` prázdné = volný text (otázka 1)."""

    number: int
    field: str
    text: str
    enum: type[StrEnum] | None
    choices: tuple[Choice, ...] = ()


def _choices(enum: type[StrEnum], tooltips: dict[str, str]) -> tuple[Choice, ...]:
    """Poskládá volby z enumu; popisky bere z `label()`, tooltipy z mapy."""
    return tuple(
        Choice(value=member.name, label=member.label(), tooltip=tooltips.get(member.name, ""))
        for member in enum
    )


# --- Tooltipy doslova dle spec kap. 5.1 ------------------------------------

_KRITICTNOST_TOOLTIPS = {
    "POMOCNY": (
        "Usnadňuje práci, bez něj se obejdeme; výpadek nikoho nezastaví. "
        "Příklad: generátor e-mailových podpisů, přehled obědů."
    ),
    "DULEZITY": (
        "Tým se bez něj citelně zpomalí, existuje ruční náhrada. "
        "Příklad: příprava reportů, sumarizace smluv."
    ),
    "KRITICKY": (
        "Výpadek zastaví část firmy nebo ohrozí povinnost vůči klientům či "
        "regulátorovi. Příklad: podpora schvalování úvěrů, výpočet rizikových skóre."
    ),
}

_OSOBNI_UDAJE_TOOLTIPS = {
    "NE": "Žádná jména, e-maily, telefony, identifikátory osob — ani v logách.",
    "ZAMESTNANCU": "Údaje kolegů: docházka, výkonnost, interní adresář.",
    "KLIENTU": "Údaje zákazníků: smlouvy, transakce, kontakty. Nejpřísnější režim.",
}

_ROZHODOVANI_TOOLTIPS = {
    "NE": "Výstup se netýká hodnocení ani třídění konkrétních osob.",
    "DOPORUCUJE": (
        "AI připraví návrh (pořadí kandidátů, skóre), finální rozhodnutí dělá člověk."
    ),
    "ROZHODUJE_AUTOMATICKY": (
        "Výstup se přímo promítne do rozhodnutí o osobě bez lidské kontroly. "
        "Signál vysokého rizika dle EU AI Act."
    ),
}

_CITLIVOST_TOOLTIPS = {
    "VEREJNA": "Informace běžně dostupné mimo firmu.",
    "INTERNI": "Běžné pracovní dokumenty; únik je nepříjemný, ne kritický.",
    "DUVERNA": "Smlouvy, finanční data, strategie; únik = reálná škoda.",
    "VYSOCE_CITLIVA": (
        "Klientská portfolia, platební data, zvláštní kategorie osobních údajů; "
        "únik = incident vůči regulátorovi."
    ),
}

_AUTONOMIE_TOOLTIPS = {
    "NE": "Výstup si člověk jen přečte.",
    "CLOVEK_SCHVALUJE": (
        "AI akci připraví (draft e-mailu, návrh transakce), člověk odesílá."
    ),
    "ANO_AUTOMATICKY": "Výstup přímo spouští akci — odeslání, zápis, transakci.",
}

_DOPAD_TOOLTIPS = {
    "ZANEDBATELNY": "Chybu si nikdo nemusí všimnout, nic se nestane.",
    "PROVOZNI": "Ztracený čas, nutná ruční oprava.",
    "FINANCNI_NEBO_KLIENTSKY": "Špatné číslo v reportu, chybná informace klientovi.",
    "ZASADNI": "Regulatorní incident, významná finanční ztráta, poškození klientů.",
}


#: Pořadí otázek odpovídá spec kap. 5.1; `field` odpovídá poli `DotaznikOdpovedi`.
QUESTIONS: tuple[Question, ...] = (
    Question(
        number=1,
        field="ucel",
        text="K čemu aplikace slouží a jaký proces podporuje?",
        enum=None,
    ),
    Question(
        number=2,
        field="pocet_uzivatelu",
        text="Kolik lidí ji používá / bude používat?",
        enum=PocetUzivatelu,
        choices=_choices(PocetUzivatelu, {}),
    ),
    Question(
        number=3,
        field="kritictnost",
        text="Jak kritický proces podporuje?",
        enum=Kritictnost,
        choices=_choices(Kritictnost, _KRITICTNOST_TOOLTIPS),
    ),
    Question(
        number=4,
        field="osobni_udaje",
        text="Zpracovává osobní údaje?",
        enum=OsobniUdaje,
        choices=_choices(OsobniUdaje, _OSOBNI_UDAJE_TOOLTIPS),
    ),
    Question(
        number=5,
        field="rozhodovani",
        text="Rozhoduje nebo doporučuje o lidech?",
        enum=RozhodovaniOLidech,
        choices=_choices(RozhodovaniOLidech, _ROZHODOVANI_TOOLTIPS),
    ),
    Question(
        number=6,
        field="viditelnost",
        text="Je výstup viditelný mimo firmu?",
        enum=ViditelnostVystupu,
        choices=_choices(ViditelnostVystupu, {}),
    ),
    Question(
        number=7,
        field="citlivost",
        text="Jak citlivá data zpracovává?",
        enum=CitlivostDat,
        choices=_choices(CitlivostDat, _CITLIVOST_TOOLTIPS),
    ),
    Question(
        number=8,
        field="autonomie",
        text="Může výstup AI vyvolat akci bez kontroly člověkem?",
        enum=Autonomie,
        choices=_choices(Autonomie, _AUTONOMIE_TOOLTIPS),
    ),
    Question(
        number=9,
        field="dopad",
        text="Jaký je potenciální dopad chybného výstupu AI?",
        enum=DopadChyby,
        choices=_choices(DopadChyby, _DOPAD_TOOLTIPS),
    ),
)

#: Otázka 1 — volný text, ukládá se zároveň jako `popis` aplikace.
FREE_TEXT_QUESTION = QUESTIONS[0]

#: Otázky 2–9 — povinný výběr z enumu.
CHOICE_QUESTIONS: tuple[Question, ...] = QUESTIONS[1:]
