"""Syntetická počáteční data registru (Fáze 14, spec kap. 12).

`seed_if_empty` vloží ~8 fiktivních aplikací, ale **jen do prázdné** tabulky
`applications` — nikdy nepřepisuje ani nedoplňuje existující data (druhé
spuštění nad neprázdnou DB vrací 0).

Klíčová vlastnost: tiery a legislativní signály se v tomto souboru nikde
neopisují ručně. Pro každou aplikaci se sestaví skutečný dotazník
(`DotaznikOdpovedi`) a AI komponenty a `klasifikace_minimum` /
`klasifikace_priznaky` se spočítají přes tytéž funkce
(`policy.compute_minimum`, `regulatory.compute_flags`), jaké používá ostrý
wizard v `app.routes.classification`. Seed data jsou tak zaručeně
konzistentní s deterministickými pravidly.

Root `seed.py` je tenký CLI wrapper nad `seed_if_empty` (spustitelné jako
`python seed.py`); `app.main` volá tuto funkci přímo při startu (po
`init_db()`, před úklidem LLM auditu — pořadí dle spec kap. 12).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AiComponent, Application
from app.questionnaire import CHOICE_QUESTIONS, FREE_TEXT_QUESTION
from app.schemas import (
    AuditAction,
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
    Stav,
    Tier,
    ViditelnostVystupu,
)
from app.services import history
from app.services.policy import MinimumResult, compute_minimum, effective_tier
from app.services.regulatory import compute_flags


@dataclass(frozen=True)
class _Contact:
    jmeno: str
    email: str


@dataclass(frozen=True)
class _ComponentSpec:
    provider: Provider
    hosting_type: HostingType
    model_name: str
    purpose: str

    def to_info(self) -> KomponentaInfo:
        return KomponentaInfo(provider=self.provider, hosting_type=self.hosting_type)


@dataclass(frozen=True)
class _SeedApp:
    """Jedna syntetická aplikace před uložením do DB.

    `llm_tier` simuluje návrh AI (baseline pro `effective_tier`), tier
    a signály samotné se dopočítávají z `answers`/`components` skutečnými
    deterministickými funkcemi — viz `_build_application`.
    """

    nazev: str
    stav: Stav
    vlastnik: _Contact
    zastupce: _Contact
    spravce: _Contact
    answers: DotaznikOdpovedi
    components: tuple[_ComponentSpec, ...]
    llm_tier: Tier
    zduvodneni_navrhu: str
    review_required: bool = False
    delete_reason: str | None = None
    deleted_by: str | None = None

    @property
    def created_by(self) -> str:
        return self.vlastnik.email.split("@", 1)[0]


def _odpovedi_to_json(answers: DotaznikOdpovedi) -> dict[str, str]:
    """`DotaznikOdpovedi` → stejný JSON tvar, jaký ukládá ostrý wizard
    (`.name` enumů; volný text pod klíčem otázky 1)."""
    data: dict[str, str] = {FREE_TEXT_QUESTION.field: answers.ucel}
    for question in CHOICE_QUESTIONS:
        value = getattr(answers, question.field)
        data[question.field] = value.name
    return data


def _compose_zduvodneni(zduvodneni: str, minimum: MinimumResult, llm_tier: Tier) -> str:
    """Stejná logika jako `app.routes.classification._compose_zduvodneni`:
    pokud návrh AI nedosahuje policy minima, doplní se věta o eskalaci
    s konkrétními reason codes."""
    if llm_tier >= minimum.tier:
        return zduvodneni

    reason_codes = ", ".join(rule.reason_code for rule in minimum.triggered_rules)
    eskalace = (
        f"Návrh AI byl {llm_tier.label().upper()}, ale povinné minimum "
        f"{minimum.tier.label().upper()} vyplývá z deterministických pravidel "
        f"({reason_codes}) a má přednost před návrhem AI."
    )
    return f"{zduvodneni} {eskalace}".strip()


# --- 8 syntetických aplikací (spec kap. 12) --------------------------------


def _seed_definitions() -> list[_SeedApp]:
    return [
        _SeedApp(
            nazev="Generátor e-mailových podpisů",
            stav=Stav.VYVOJ,
            vlastnik=_Contact("Tereza Malá", "tereza.mala@example.com"),
            zastupce=_Contact("Petr Dvořák", "petr.dvorak@example.com"),
            spravce=_Contact("Jakub Král", "jakub.kral@example.com"),
            answers=DotaznikOdpovedi(
                ucel=(
                    "Automaticky generuje jednotný e-mailový podpis podle šablony "
                    "a profilu zaměstnance v HR systému."
                ),
                pocet_uzivatelu=PocetUzivatelu.PRES_50,
                kritictnost=Kritictnost.POMOCNY,
                osobni_udaje=OsobniUdaje.NE,
                rozhodovani=RozhodovaniOLidech.NE,
                viditelnost=ViditelnostVystupu.NE,
                citlivost=CitlivostDat.VEREJNA,
                autonomie=Autonomie.NE,
                dopad=DopadChyby.ZANEDBATELNY,
            ),
            components=(
                _ComponentSpec(
                    Provider.INTERNI,
                    HostingType.ON_PREM,
                    "Interní šablonovač v1",
                    "Sestavuje e-mailový podpis z profilu zaměstnance v HR systému.",
                ),
            ),
            llm_tier=Tier.MALA,
            zduvodneni_navrhu=(
                "Nástroj má pomocný charakter, nezpracovává osobní údaje ani "
                "nerozhoduje o lidech a jeho výstup zůstává jen uvnitř firmy — "
                "klasifikace MALÁ odpovídá zanedbatelnému riziku."
            ),
        ),
        _SeedApp(
            nazev="Sumarizátor smluv pro právní tým",
            stav=Stav.PILOT,
            vlastnik=_Contact("Eva Procházková", "eva.prochazkova@example.com"),
            zastupce=_Contact("Martin Beneš", "martin.benes@example.com"),
            spravce=_Contact("Lucie Horáková", "lucie.horakova@example.com"),
            answers=DotaznikOdpovedi(
                ucel=(
                    "Sumarizuje nahrané smlouvy a upozorňuje na neobvyklá "
                    "ustanovení pro právní tým."
                ),
                pocet_uzivatelu=PocetUzivatelu.OD_10_DO_50,
                kritictnost=Kritictnost.DULEZITY,
                osobni_udaje=OsobniUdaje.KLIENTU,
                rozhodovani=RozhodovaniOLidech.NE,
                viditelnost=ViditelnostVystupu.NEPRIMO,
                citlivost=CitlivostDat.VYSOCE_CITLIVA,
                autonomie=Autonomie.CLOVEK_SCHVALUJE,
                dopad=DopadChyby.FINANCNI_NEBO_KLIENTSKY,
            ),
            components=(
                _ComponentSpec(
                    Provider.ANTHROPIC,
                    HostingType.EXTERNI_API,
                    "Claude Sonnet",
                    "Sumarizace a extrakce klíčových ustanovení ze smluvních dokumentů.",
                ),
            ),
            llm_tier=Tier.STREDNI,
            zduvodneni_navrhu=(
                "Aplikace pracuje s osobními údaji klientů a vysoce citlivými "
                "smluvními daty přes externího AI providera; navržená klasifikace "
                "STŘEDNÍ odpovídá důležitosti procesu a citlivosti dat."
            ),
        ),
        _SeedApp(
            nazev="Skórovací nástroj pro schvalování úvěrů",
            stav=Stav.PROVOZ,
            vlastnik=_Contact("Pavel Novotný", "pavel.novotny@example.com"),
            zastupce=_Contact("Kateřina Svobodová", "katerina.svobodova@example.com"),
            spravce=_Contact("Ondřej Veselý", "ondrej.vesely@example.com"),
            answers=DotaznikOdpovedi(
                ucel=(
                    "Vypočítá rizikové skóre žadatele o úvěr a automaticky "
                    "rozhodne o schválení nebo zamítnutí."
                ),
                pocet_uzivatelu=PocetUzivatelu.PRES_50,
                kritictnost=Kritictnost.KRITICKY,
                osobni_udaje=OsobniUdaje.KLIENTU,
                rozhodovani=RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY,
                viditelnost=ViditelnostVystupu.ANO_PRIMO,
                citlivost=CitlivostDat.VYSOCE_CITLIVA,
                autonomie=Autonomie.ANO_AUTOMATICKY,
                dopad=DopadChyby.ZASADNI,
            ),
            components=(
                _ComponentSpec(
                    Provider.ANTHROPIC,
                    HostingType.EXTERNI_API,
                    "Claude Opus",
                    "Výpočet rizikového skóre žadatele o úvěr.",
                ),
                _ComponentSpec(
                    Provider.INTERNI,
                    HostingType.ON_PREM,
                    "Interní scoring model v2",
                    "Záložní deterministický scoring při výpadku externího modelu.",
                ),
            ),
            # Záměrně nízký návrh AI — ukazuje, že povinné minimum
            # (AUTO_DECISION_PERSON a další) má vždy přednost (spec kap. 5.3
            # příklad „LLM MALÁ + floor VELKÁ → VELKÁ").
            llm_tier=Tier.MALA,
            zduvodneni_navrhu=(
                "Aplikace automatizovaně rozhoduje o schválení úvěru na základě "
                "vypočteného rizikového skóre žadatele."
            ),
        ),
        _SeedApp(
            nazev="Nástroj pro hodnocení výkonu zaměstnanců",
            stav=Stav.PROVOZ,
            vlastnik=_Contact("Hana Kučerová", "hana.kucerova@example.com"),
            zastupce=_Contact("Tomáš Marek", "tomas.marek@example.com"),
            spravce=_Contact("Barbora Poláková", "barbora.polakova@example.com"),
            answers=DotaznikOdpovedi(
                ucel=(
                    "Na základě KPI a docházky automaticky vyhodnocuje výkon "
                    "zaměstnanců a generuje doporučení pro odměňování."
                ),
                pocet_uzivatelu=PocetUzivatelu.OD_10_DO_50,
                kritictnost=Kritictnost.DULEZITY,
                osobni_udaje=OsobniUdaje.ZAMESTNANCU,
                rozhodovani=RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY,
                viditelnost=ViditelnostVystupu.NE,
                citlivost=CitlivostDat.DUVERNA,
                autonomie=Autonomie.CLOVEK_SCHVALUJE,
                dopad=DopadChyby.FINANCNI_NEBO_KLIENTSKY,
            ),
            components=(
                _ComponentSpec(
                    Provider.INTERNI,
                    HostingType.FIREMNI_CLOUD,
                    "Interní model hodnocení výkonu",
                    "Automatizované hodnocení výkonu zaměstnanců na základě KPI.",
                ),
            ),
            llm_tier=Tier.STREDNI,
            zduvodneni_navrhu=(
                "Nástroj hodnotí konkrétní zaměstnance na základě jejich KPI a "
                "docházky a generuje doporučení promítající se do odměňování — "
                "typický případ EU AI Act Annex III (HR nástroj hodnotící "
                "zaměstnance)."
            ),
            # Nedávno upravené AI komponenty čekají na potvrzenou re-klasifikaci.
            review_required=True,
        ),
        _SeedApp(
            nazev="Přehled obědů v kantýně",
            stav=Stav.UTLUMENO,
            vlastnik=_Contact("Jiří Kraus", "jiri.kraus@example.com"),
            zastupce=_Contact("Simona Fialová", "simona.fialova@example.com"),
            spravce=_Contact("David Sedláček", "david.sedlacek@example.com"),
            answers=DotaznikOdpovedi(
                ucel=(
                    "Zobrazuje denní nabídku obědů a umožňuje zaměstnancům "
                    "rezervovat jídlo v kantýně."
                ),
                pocet_uzivatelu=PocetUzivatelu.PRES_50,
                kritictnost=Kritictnost.POMOCNY,
                osobni_udaje=OsobniUdaje.NE,
                rozhodovani=RozhodovaniOLidech.NE,
                viditelnost=ViditelnostVystupu.NE,
                citlivost=CitlivostDat.VEREJNA,
                autonomie=Autonomie.NE,
                dopad=DopadChyby.ZANEDBATELNY,
            ),
            components=(
                _ComponentSpec(
                    Provider.INTERNI,
                    HostingType.ON_PREM,
                    "Jídelníček bot",
                    "Zobrazuje denní nabídku obědů v kantýně.",
                ),
            ),
            llm_tier=Tier.MALA,
            zduvodneni_navrhu=(
                "Jednoduchý pomocný nástroj bez zpracování osobních údajů a bez "
                "jakéhokoli rozhodování o lidech — klasifikace MALÁ."
            ),
        ),
        _SeedApp(
            nazev="Chatbot podpory pro klienty",
            stav=Stav.PROVOZ,
            vlastnik=_Contact("Markéta Černá", "marketa.cerna@example.com"),
            zastupce=_Contact("Filip Urban", "filip.urban@example.com"),
            spravce=_Contact("Adéla Bartošová", "adela.bartosova@example.com"),
            answers=DotaznikOdpovedi(
                ucel="Automaticky odpovídá klientům na časté dotazy v live chatu na webu.",
                pocet_uzivatelu=PocetUzivatelu.PRES_50,
                kritictnost=Kritictnost.DULEZITY,
                osobni_udaje=OsobniUdaje.KLIENTU,
                rozhodovani=RozhodovaniOLidech.NE,
                viditelnost=ViditelnostVystupu.ANO_PRIMO,
                citlivost=CitlivostDat.DUVERNA,
                autonomie=Autonomie.ANO_AUTOMATICKY,
                dopad=DopadChyby.FINANCNI_NEBO_KLIENTSKY,
            ),
            components=(
                _ComponentSpec(
                    Provider.ANTHROPIC,
                    HostingType.EXTERNI_API,
                    "Claude Haiku",
                    "Automatické odpovídání na dotazy klientů v chatu.",
                ),
            ),
            llm_tier=Tier.STREDNI,
            zduvodneni_navrhu=(
                "Chatbot automaticky odpovídá klientům a zpracovává jejich "
                "osobní údaje přes externího AI providera bez schválení "
                "člověkem — klasifikace STŘEDNÍ."
            ),
        ),
        _SeedApp(
            nazev="Plánovač směn v kavárnách",
            stav=Stav.PILOT,
            vlastnik=_Contact("Roman Jelínek", "roman.jelinek@example.com"),
            zastupce=_Contact("Nikola Doležalová", "nikola.dolezalova@example.com"),
            spravce=_Contact("Vojtěch Pokorný", "vojtech.pokorny@example.com"),
            answers=DotaznikOdpovedi(
                ucel=(
                    "Navrhoval rozvržení směn zaměstnanců v kavárnách na "
                    "základě jejich dostupnosti."
                ),
                pocet_uzivatelu=PocetUzivatelu.DO_10,
                kritictnost=Kritictnost.DULEZITY,
                osobni_udaje=OsobniUdaje.ZAMESTNANCU,
                rozhodovani=RozhodovaniOLidech.DOPORUCUJE,
                viditelnost=ViditelnostVystupu.NE,
                citlivost=CitlivostDat.INTERNI,
                autonomie=Autonomie.CLOVEK_SCHVALUJE,
                dopad=DopadChyby.PROVOZNI,
            ),
            components=(
                _ComponentSpec(
                    Provider.JINY,
                    HostingType.NEZNAME,
                    "Externí plánovací nástroj (SaaS)",
                    "Navrhoval směny na základě dostupnosti zaměstnanců.",
                ),
            ),
            llm_tier=Tier.MALA,
            zduvodneni_navrhu=(
                "Nástroj pouze doporučoval rozvržení směn, konečné rozhodnutí "
                "dělal vedoucí; zpracovává osobní údaje zaměstnanců v omezeném "
                "rozsahu — klasifikace MALÁ."
            ),
            delete_reason=(
                "Pilot ukončen, nástroj byl po vyhodnocení nahrazen "
                "dodavatelským plánovacím systémem."
            ),
            deleted_by="admin",
        ),
        _SeedApp(
            nazev="Analyzátor sentimentu zpětné vazby zaměstnanců",
            stav=Stav.VYVOJ,
            vlastnik=_Contact("Zuzana Vlčková", "zuzana.vlckova@example.com"),
            zastupce=_Contact("Michal Kopecký", "michal.kopecky@example.com"),
            spravce=_Contact("Klára Šimková", "klara.simkova@example.com"),
            answers=DotaznikOdpovedi(
                ucel=(
                    "Analyzuje sentiment a témata v anonymních interních "
                    "průzkumech spokojenosti zaměstnanců."
                ),
                pocet_uzivatelu=PocetUzivatelu.OD_10_DO_50,
                kritictnost=Kritictnost.KRITICKY,
                osobni_udaje=OsobniUdaje.ZAMESTNANCU,
                rozhodovani=RozhodovaniOLidech.NE,
                viditelnost=ViditelnostVystupu.NE,
                citlivost=CitlivostDat.INTERNI,
                autonomie=Autonomie.NE,
                dopad=DopadChyby.PROVOZNI,
            ),
            components=(
                _ComponentSpec(
                    Provider.ANTHROPIC,
                    HostingType.EXTERNI_API,
                    "Claude Sonnet",
                    "Analýza sentimentu textových odpovědí z interních průzkumů.",
                ),
                _ComponentSpec(
                    Provider.INTERNI,
                    HostingType.ON_PREM,
                    "Interní klasifikátor kategorií zpětné vazby",
                    "Třídí zpětnou vazbu do tematických kategorií.",
                ),
            ),
            llm_tier=Tier.STREDNI,
            zduvodneni_navrhu=(
                "Analýza probíhá nad interním, kritickým procesem monitoringu "
                "spokojenosti zaměstnanců a zahrnuje jejich osobní údaje — "
                "klasifikace STŘEDNÍ odpovídá kritičnosti procesu."
            ),
        ),
    ]


def _build_application(seed: _SeedApp) -> tuple[Application, Tier]:
    """Sestaví `Application` + připojené `AiComponent` řádky. Tier a signály
    se počítají skutečnými deterministickými funkcemi, ne opsané ručně."""
    components_info = [c.to_info() for c in seed.components]
    minimum = compute_minimum(seed.answers, components_info)
    flags = compute_flags(seed.answers, components_info)
    effective = effective_tier(seed.llm_tier, minimum)
    zduvodneni = _compose_zduvodneni(seed.zduvodneni_navrhu, minimum, seed.llm_tier)

    application = Application(
        nazev=seed.nazev,
        popis=seed.answers.ucel,
        vlastnik_jmeno=seed.vlastnik.jmeno,
        vlastnik_email=seed.vlastnik.email,
        zastupce_jmeno=seed.zastupce.jmeno,
        zastupce_email=seed.zastupce.email,
        spravce_jmeno=seed.spravce.jmeno,
        spravce_email=seed.spravce.email,
        klasifikace_llm=seed.llm_tier,
        klasifikace_minimum=minimum.tier,
        klasifikace=effective,
        klasifikace_zduvodneni=zduvodneni,
        klasifikace_priznaky=[flag.to_dict() for flag in flags],
        klasifikace_potvrdil=seed.created_by,
        klasifikace_poznamka=None,
        dotaznik_odpovedi=_odpovedi_to_json(seed.answers),
        stav=seed.stav,
        review_required=seed.review_required,
        created_by=seed.created_by,
        updated_by=seed.created_by,
    )
    for component in seed.components:
        application.components.append(
            AiComponent(
                provider=component.provider,
                model_name=component.model_name,
                purpose=component.purpose,
                hosting_type=component.hosting_type,
            )
        )
    return application, effective


def seed_if_empty(session: Session) -> int:
    """Vloží syntetické aplikace, jen pokud je tabulka `applications` prázdná.

    Vrací počet vložených záznamů (0 při opakovaném spuštění nad neprázdnou
    DB — idempotentní). Každá aplikace dostane historii `CREATE` + `CLASSIFY`
    (vyřazená navíc `RETIRE`), stejně jako ostrý wizard.
    """
    already_seeded = session.execute(select(Application.id).limit(1)).first()
    if already_seeded is not None:
        return 0

    inserted = 0
    for seed in _seed_definitions():
        application, effective = _build_application(seed)
        session.add(application)
        session.flush()  # přidělí application.id, potřebné pro historii

        history.log(session, application.id, seed.created_by, AuditAction.CREATE)
        history.log(
            session,
            application.id,
            seed.created_by,
            AuditAction.CLASSIFY,
            pole="klasifikace",
            nova=effective.name,
        )

        if seed.delete_reason:
            deleted_by = seed.deleted_by or "admin"
            application.deleted_at = datetime.now(timezone.utc)
            application.deleted_by = deleted_by
            application.delete_reason = seed.delete_reason
            application.version += 1
            application.updated_by = deleted_by
            history.log(
                session,
                application.id,
                deleted_by,
                AuditAction.RETIRE,
                reason=seed.delete_reason,
            )

        inserted += 1

    session.commit()
    return inserted
