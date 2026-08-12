"""Centralizované texty pro CSS hover tooltips u odznaků v UI.

Jedno místo pro všechny tooltip texty — šablony si je jen vyzvednou jako
Jinja globály (registrace v `app/templating.py`), místo aby byly rozházené
po jednotlivých `.html` souborech.
"""

from __future__ import annotations

from app.schemas import Stav, Tier

#: Badge „vyžaduje review" (spec: review_required po změně AI komponent).
REVIEW_BADGE_TOOLTIP = (
    "Od poslední klasifikace se změnily AI komponenty. Klasifikace nemusí "
    "odpovídat skutečnosti — otevřete editaci a projděte znovu dotazník."
)

_TIER_GOVERNANCE_SUFFIX = "Interní governance klasifikace, ne právní."

_TIER_TOOLTIPS: dict[Tier, str] = {
    Tier.MALA: f"Nízký interní dopad, bez legislativních eskalací. {_TIER_GOVERNANCE_SUFFIX}",
    Tier.STREDNI: f"Zvýšený interní dopad, doporučené pravidelné review. {_TIER_GOVERNANCE_SUFFIX}",
    Tier.VELKA: f"Vysoký interní dopad, vyžaduje zvýšený dohled. {_TIER_GOVERNANCE_SUFFIX}",
}

_STAV_TOOLTIPS: dict[Stav, str] = {
    Stav.VYVOJ: "Aplikace se vyvíjí, ještě není nasazena pro reálné uživatele.",
    Stav.PILOT: "Aplikace běží v omezeném pilotním provozu s vybranými uživateli.",
    Stav.PROVOZ: "Aplikace je v plném provozu pro všechny určené uživatele.",
    Stav.UTLUMENO: "Provoz aplikace byl utlumen, aktivně se nepoužívá.",
}

_ROLE_TOOLTIPS: dict[str, str] = {
    "user": "Běžný uživatel — vidí registr, může zakládat a upravovat vlastní aplikace.",
    "admin": (
        "Administrátor — navíc smí překlasifikovat, vyřazovat a obnovovat "
        "záznamy libovolného vlastníka."
    ),
}


def tier_tooltip(tier: Tier) -> str:
    """Krátké vysvětlení governance tieru (bez ohledu na konkrétní zdůvodnění)."""
    return _TIER_TOOLTIPS[tier]


def stav_tooltip(stav: Stav) -> str:
    """Jedna věta vysvětlující, co daný provozní stav znamená."""
    return _STAV_TOOLTIPS[stav]


def role_tooltip(role: str) -> str:
    """Co role v aplikaci smí. Neznámá role dostane obecný text."""
    return _ROLE_TOOLTIPS.get(role, "Role v aplikaci.")
