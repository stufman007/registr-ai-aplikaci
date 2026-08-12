"""Golden dataset pro eval klasifikace a kontroly duplicit.

Dataset je **data, ne kód**: seznam zmrazených dataclass záznamů, aby šel
strojově validovat i verzovat v gitu. Klíčová invarianta (ověřuje
`tests/test_eval_dataset.py`):

    expected_tier NIKDY nesmí být pod deterministickým policy minimem
    (`app.services.policy.compute_minimum`).

Dataset se tedy nemůže rozejít s pravidly — kdyby někdo pravidlo zpřísnil
a dataset neupravil, test spadne. Naopak `expected_tier` **smí** být nad
minimem: tam se testuje, jestli model chytí nuanci, kterou pravidla neumí.

`acceptable_tiers` je tolerance u hraničních případů, kde se dá legitimně
argumentovat dvěma tiery. Metrika „exact" měří shodu s `expected_tier`,
metrika „acceptable" příslušnost do `acceptable_tiers`.
"""

from __future__ import annotations

from dataclasses import dataclass

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

DATASET_VERSION = "2026-08-v1"


# --- Typy záznamů ----------------------------------------------------------


@dataclass(frozen=True)
class ClassificationCase:
    """Jeden klasifikační případ zlatého datasetu."""

    case_id: str
    answers: DotaznikOdpovedi
    components: tuple[KomponentaInfo, ...]
    expected_tier: Tier
    #: Tiery, které se ještě počítají jako správné (vždy obsahuje `expected_tier`).
    acceptable_tiers: frozenset[Tier]
    #: Klíčové koncepty, které má zdůvodnění zmínit (hledá se podřetězec, bez diakritiky).
    rationale_must_mention: tuple[str, ...]
    #: Proč je tento případ v datasetu — co konkrétně testuje.
    note: str


@dataclass(frozen=True)
class ExistingRecord:
    """Existující záznam registru pro účel kontroly duplicit."""

    record_id: str
    nazev: str
    popis: str


@dataclass(frozen=True)
class DuplicateCase:
    """Jeden případ kontroly duplicit: nová aplikace vs. výsek registru."""

    dup_case_id: str
    nazev: str
    popis: str
    existing: tuple[ExistingRecord, ...]
    #: Očekávané shody; prázdná množina = model nemá označit nic.
    expected_match_ids: frozenset[str]
    note: str


# --- Pomocníci pro čitelný zápis případů -----------------------------------


def _odpovedi(
    ucel: str,
    *,
    pocet_uzivatelu: PocetUzivatelu = PocetUzivatelu.DO_10,
    kritictnost: Kritictnost = Kritictnost.POMOCNY,
    osobni_udaje: OsobniUdaje = OsobniUdaje.NE,
    rozhodovani: RozhodovaniOLidech = RozhodovaniOLidech.NE,
    viditelnost: ViditelnostVystupu = ViditelnostVystupu.NE,
    citlivost: CitlivostDat = CitlivostDat.INTERNI,
    autonomie: Autonomie = Autonomie.NE,
    dopad: DopadChyby = DopadChyby.ZANEDBATELNY,
) -> DotaznikOdpovedi:
    """Neutrální základ (= policy minimum MALA), měněný jen tam, kde to případ testuje."""
    return DotaznikOdpovedi(
        ucel=ucel,
        pocet_uzivatelu=pocet_uzivatelu,
        kritictnost=kritictnost,
        osobni_udaje=osobni_udaje,
        rozhodovani=rozhodovani,
        viditelnost=viditelnost,
        citlivost=citlivost,
        autonomie=autonomie,
        dopad=dopad,
    )


_EXTERNI = (KomponentaInfo(provider=Provider.OPENAI, hosting_type=HostingType.EXTERNI_API),)
_ON_PREM = (KomponentaInfo(provider=Provider.INTERNI, hosting_type=HostingType.ON_PREM),)
_FIREMNI_CLOUD = (
    KomponentaInfo(provider=Provider.AZURE, hosting_type=HostingType.FIREMNI_CLOUD),
)


# --- Klasifikační případy ---------------------------------------------------

CLASSIFICATION_CASES: tuple[ClassificationCase, ...] = (
    # ---- Nevinné MALA: žádné pravidlo se nesmí aktivovat ------------------
    ClassificationCase(
        case_id="mala-podpisy",
        answers=_odpovedi(
            "Generuje jednotné patičky e-mailů z firemní šablony a jména zaměstnance.",
            citlivost=CitlivostDat.VEREJNA,
        ),
        components=_ON_PREM,
        expected_tier=Tier.MALA,
        acceptable_tiers=frozenset({Tier.MALA}),
        rationale_must_mention=("pomocn",),
        note="Učebnicová MALA — kontrola, že model neeskaluje bez důvodu.",
    ),
    ClassificationCase(
        case_id="mala-prepis-porad",
        answers=_odpovedi(
            "Přepisuje záznamy interních týmových porad do textu a dělá stručný souhrn.",
            pocet_uzivatelu=PocetUzivatelu.OD_10_DO_50,
            dopad=DopadChyby.PROVOZNI,
        ),
        components=_ON_PREM,
        expected_tier=Tier.MALA,
        acceptable_tiers=frozenset({Tier.MALA}),
        rationale_must_mention=("intern",),
        note="Interní data, on-prem model, výstup čte člověk — stále MALA.",
    ),
    ClassificationCase(
        case_id="mala-preklad-dokumentace",
        answers=_odpovedi(
            "Překládá veřejnou technickou dokumentaci produktu z angličtiny do češtiny.",
            citlivost=CitlivostDat.VEREJNA,
            dopad=DopadChyby.PROVOZNI,
        ),
        components=_EXTERNI,
        expected_tier=Tier.MALA,
        acceptable_tiers=frozenset({Tier.MALA}),
        rationale_must_mention=("veřejn",),
        note=(
            "Externí API provider sám o sobě NEeskaluje — pravidlo "
            "EXTERNAL_PROVIDER_SENSITIVE_DATA vyžaduje důvěrná data."
        ),
    ),
    ClassificationCase(
        case_id="mala-napoveda-intranet",
        answers=_odpovedi(
            "Chatbot odpovídá na dotazy k veřejné intranetové nápovědě a firemním benefitům.",
            pocet_uzivatelu=PocetUzivatelu.PRES_50,
            citlivost=CitlivostDat.VEREJNA,
        ),
        components=_FIREMNI_CLOUD,
        expected_tier=Tier.MALA,
        acceptable_tiers=frozenset({Tier.MALA, Tier.STREDNI}),
        rationale_must_mention=("uživatel",),
        note="Hodně uživatelů, ale nulové riziko — počet lidí není policy floor.",
    ),
    ClassificationCase(
        case_id="mala-testovaci-data",
        answers=_odpovedi(
            "Generuje syntetická testovací data pro vývojové prostředí, nepracuje s ostrými daty.",
            citlivost=CitlivostDat.VEREJNA,
            dopad=DopadChyby.PROVOZNI,
        ),
        components=_ON_PREM,
        expected_tier=Tier.MALA,
        acceptable_tiers=frozenset({Tier.MALA}),
        rationale_must_mention=("syntetick", "testov"),
        note="Kontrola, že slovo „data“ samo o sobě model neeskaluje.",
    ),
    ClassificationCase(
        case_id="mala-sumarizace-novinek",
        answers=_odpovedi(
            "Sumarizuje veřejné zpravodajské články o oboru do denního přehledu pro tým.",
            pocet_uzivatelu=PocetUzivatelu.OD_10_DO_50,
            citlivost=CitlivostDat.VEREJNA,
        ),
        components=_EXTERNI,
        expected_tier=Tier.MALA,
        acceptable_tiers=frozenset({Tier.MALA}),
        rationale_must_mention=("veřejn",),
        note="Šestý nevinný případ — základ pro měření falešných eskalací.",
    ),
    # ---- Čisté případy jednotlivých policy pravidel ------------------------
    ClassificationCase(
        case_id="pravidlo-auto-rozhodnuti-o-lidech",
        answers=_odpovedi(
            "Automaticky schvaluje nebo zamítá žádosti zaměstnanců o dovolenou bez zásahu vedoucího.",
            osobni_udaje=OsobniUdaje.ZAMESTNANCU,
            rozhodovani=RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY,
            dopad=DopadChyby.PROVOZNI,
        ),
        components=_FIREMNI_CLOUD,
        expected_tier=Tier.VELKA,
        acceptable_tiers=frozenset({Tier.VELKA}),
        rationale_must_mention=("automat", "rozhod"),
        note="Pravidlo AUTO_DECISION_PERSON — samo o sobě vždy VELKA.",
    ),
    ClassificationCase(
        case_id="pravidlo-kriticky-vystup-ven",
        answers=_odpovedi(
            "Sestavuje texty povinných sdělení, která jdou přímo klientům v rámci regulovaného procesu.",
            kritictnost=Kritictnost.KRITICKY,
            viditelnost=ViditelnostVystupu.ANO_PRIMO,
            dopad=DopadChyby.FINANCNI_NEBO_KLIENTSKY,
        ),
        components=_FIREMNI_CLOUD,
        expected_tier=Tier.VELKA,
        acceptable_tiers=frozenset({Tier.VELKA}),
        rationale_must_mention=("kritick", "klient"),
        note="Pravidlo CRITICAL_EXTERNAL_OUTPUT (kritický proces + výstup mimo firmu).",
    ),
    ClassificationCase(
        case_id="pravidlo-kriticky-vystup-neprimo",
        answers=_odpovedi(
            "Počítá podklady, které se bez další kontroly propisují do klientských výpisů.",
            kritictnost=Kritictnost.KRITICKY,
            viditelnost=ViditelnostVystupu.NEPRIMO,
            dopad=DopadChyby.FINANCNI_NEBO_KLIENTSKY,
        ),
        components=_ON_PREM,
        expected_tier=Tier.VELKA,
        acceptable_tiers=frozenset({Tier.VELKA}),
        rationale_must_mention=("kritick",),
        note="Hrana pravidla: viditelnost NEPRIMO se počítá stejně jako ANO_PRIMO.",
    ),
    ClassificationCase(
        case_id="pravidlo-autonomni-zasadni-dopad",
        answers=_odpovedi(
            "Sama odesílá platební příkazy vygenerované z faktur, bez schválení člověkem.",
            autonomie=Autonomie.ANO_AUTOMATICKY,
            dopad=DopadChyby.ZASADNI,
        ),
        components=_FIREMNI_CLOUD,
        expected_tier=Tier.VELKA,
        acceptable_tiers=frozenset({Tier.VELKA}),
        rationale_must_mention=("automat", "dopad"),
        note="Pravidlo AUTONOMOUS_HIGH_IMPACT (autonomní akce + zásadní dopad).",
    ),
    ClassificationCase(
        case_id="pravidlo-osobni-udaje-klientu",
        answers=_odpovedi(
            "Třídí příchozí e-maily od klientů do kategorií podle tématu pro operátory.",
            pocet_uzivatelu=PocetUzivatelu.OD_10_DO_50,
            osobni_udaje=OsobniUdaje.KLIENTU,
            dopad=DopadChyby.PROVOZNI,
        ),
        components=_ON_PREM,
        expected_tier=Tier.STREDNI,
        acceptable_tiers=frozenset({Tier.STREDNI}),
        rationale_must_mention=("osobní údaj", "klient"),
        note="Pravidlo CLIENT_PERSONAL_DATA — jinak nevinná aplikace.",
    ),
    ClassificationCase(
        case_id="pravidlo-kriticky-proces",
        answers=_odpovedi(
            "Podporuje uzávěrku, bez které nelze odeslat povinný report regulátorovi; výstup zůstává uvnitř firmy.",
            kritictnost=Kritictnost.KRITICKY,
            dopad=DopadChyby.PROVOZNI,
        ),
        components=_ON_PREM,
        expected_tier=Tier.STREDNI,
        acceptable_tiers=frozenset({Tier.STREDNI, Tier.VELKA}),
        rationale_must_mention=("kritick",),
        note="Pravidlo CRITICAL_PROCESS bez výstupu ven — minimum je jen STREDNI.",
    ),
    ClassificationCase(
        case_id="pravidlo-vysoce-citliva-data",
        answers=_odpovedi(
            "Vyhledává v klientských platebních transakcích pro potřeby interní analytiky.",
            citlivost=CitlivostDat.VYSOCE_CITLIVA,
            dopad=DopadChyby.PROVOZNI,
        ),
        components=_ON_PREM,
        expected_tier=Tier.STREDNI,
        acceptable_tiers=frozenset({Tier.STREDNI, Tier.VELKA}),
        rationale_must_mention=("citliv",),
        note="Pravidlo HIGHLY_SENSITIVE_DATA — data ale zůstávají on-prem.",
    ),
    ClassificationCase(
        case_id="pravidlo-autonomni-akce",
        answers=_odpovedi(
            "Sama zakládá tikety v servisdesku podle obsahu příchozích hlášení.",
            autonomie=Autonomie.ANO_AUTOMATICKY,
            dopad=DopadChyby.PROVOZNI,
        ),
        components=_FIREMNI_CLOUD,
        expected_tier=Tier.STREDNI,
        acceptable_tiers=frozenset({Tier.STREDNI, Tier.VELKA}),
        rationale_must_mention=("automat",),
        note="Pravidlo AUTONOMOUS_ACTION bez zásadního dopadu — minimum STREDNI.",
    ),
    ClassificationCase(
        case_id="pravidlo-externi-provider-duverna-data",
        answers=_odpovedi(
            "Shrnuje obsah důvěrných dodavatelských smluv pro nákupní oddělení.",
            citlivost=CitlivostDat.DUVERNA,
            dopad=DopadChyby.PROVOZNI,
        ),
        components=_EXTERNI,
        expected_tier=Tier.STREDNI,
        acceptable_tiers=frozenset({Tier.STREDNI}),
        rationale_must_mention=("extern", "důvěrn"),
        note="Pravidlo EXTERNAL_PROVIDER_SENSITIVE_DATA — data odcházejí ven.",
    ),
    # ---- Kombinace více pravidel ------------------------------------------
    ClassificationCase(
        case_id="kombinace-uverovy-scoring",
        answers=_odpovedi(
            "Počítá skóre bonity žadatele a automaticky zamítá žádosti pod hranicí; "
            "výsledek se sděluje klientovi.",
            pocet_uzivatelu=PocetUzivatelu.PRES_50,
            kritictnost=Kritictnost.KRITICKY,
            osobni_udaje=OsobniUdaje.KLIENTU,
            rozhodovani=RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY,
            viditelnost=ViditelnostVystupu.ANO_PRIMO,
            citlivost=CitlivostDat.VYSOCE_CITLIVA,
            autonomie=Autonomie.ANO_AUTOMATICKY,
            dopad=DopadChyby.ZASADNI,
        ),
        components=_EXTERNI,
        expected_tier=Tier.VELKA,
        acceptable_tiers=frozenset({Tier.VELKA}),
        rationale_must_mention=("rozhod", "klient", "citliv"),
        note="Maximální riziko — aktivuje prakticky všechna pravidla najednou.",
    ),
    ClassificationCase(
        case_id="kombinace-aml-monitoring",
        answers=_odpovedi(
            "Označuje podezřelé transakce k prošetření; alert vždy posuzuje analytik AML.",
            pocet_uzivatelu=PocetUzivatelu.OD_10_DO_50,
            kritictnost=Kritictnost.KRITICKY,
            osobni_udaje=OsobniUdaje.KLIENTU,
            citlivost=CitlivostDat.VYSOCE_CITLIVA,
            autonomie=Autonomie.CLOVEK_SCHVALUJE,
            dopad=DopadChyby.ZASADNI,
        ),
        components=_FIREMNI_CLOUD,
        expected_tier=Tier.VELKA,
        acceptable_tiers=frozenset({Tier.STREDNI, Tier.VELKA}),
        rationale_must_mention=("citliv", "kritick"),
        note=(
            "Policy minimum je jen STREDNI (člověk schvaluje), ale zásadní dopad "
            "a vysoce citlivá data mluví pro VELKA — nuance nad rámec pravidel."
        ),
    ),
    ClassificationCase(
        case_id="kombinace-personalizace-nabidek",
        answers=_odpovedi(
            "Skládá personalizované nabídky produktů a sama je rozesílá klientům e-mailem.",
            pocet_uzivatelu=PocetUzivatelu.PRES_50,
            kritictnost=Kritictnost.DULEZITY,
            osobni_udaje=OsobniUdaje.KLIENTU,
            viditelnost=ViditelnostVystupu.ANO_PRIMO,
            citlivost=CitlivostDat.DUVERNA,
            autonomie=Autonomie.ANO_AUTOMATICKY,
            dopad=DopadChyby.FINANCNI_NEBO_KLIENTSKY,
        ),
        components=_EXTERNI,
        expected_tier=Tier.STREDNI,
        acceptable_tiers=frozenset({Tier.STREDNI, Tier.VELKA}),
        rationale_must_mention=("klient", "automat"),
        note="Tři pravidla naráz, ale proces není kritický ani dopad zásadní.",
    ),
    ClassificationCase(
        case_id="kombinace-predvyber-kandidatu",
        answers=_odpovedi(
            "Řadí životopisy uchazečů podle shody s inzerátem; o pozvání rozhoduje personalista.",
            osobni_udaje=OsobniUdaje.ZAMESTNANCU,
            rozhodovani=RozhodovaniOLidech.DOPORUCUJE,
            citlivost=CitlivostDat.DUVERNA,
            dopad=DopadChyby.FINANCNI_NEBO_KLIENTSKY,
        ),
        components=_EXTERNI,
        expected_tier=Tier.STREDNI,
        acceptable_tiers=frozenset({Tier.STREDNI, Tier.VELKA}),
        rationale_must_mention=("doporuč", "člověk"),
        note=(
            "Doporučuje, ale nerozhoduje — pravidlo AUTO_DECISION_PERSON se "
            "neaktivuje; minimum drží jen externí provider nad důvěrnými daty."
        ),
    ),
    # ---- Hraniční případy --------------------------------------------------
    ClassificationCase(
        case_id="hranicni-dulezity-proces-interni-data",
        answers=_odpovedi(
            "Připravuje podklady pro měsíční manažerský report; bez ní se tým citelně zpomalí.",
            pocet_uzivatelu=PocetUzivatelu.PRES_50,
            kritictnost=Kritictnost.DULEZITY,
            dopad=DopadChyby.PROVOZNI,
        ),
        components=_ON_PREM,
        expected_tier=Tier.MALA,
        acceptable_tiers=frozenset({Tier.MALA, Tier.STREDNI}),
        rationale_must_mention=("důležit",),
        note="Důležitý, ale ne kritický proces s interními daty — klasická hrana MALA/STREDNI.",
    ),
    ClassificationCase(
        case_id="hranicni-doporucuje-o-lidech",
        answers=_odpovedi(
            "Navrhuje pořadí zaměstnanců pro školení podle mezer v dovednostech; schvaluje vedoucí.",
            kritictnost=Kritictnost.DULEZITY,
            osobni_udaje=OsobniUdaje.ZAMESTNANCU,
            rozhodovani=RozhodovaniOLidech.DOPORUCUJE,
            dopad=DopadChyby.PROVOZNI,
        ),
        components=_ON_PREM,
        expected_tier=Tier.STREDNI,
        acceptable_tiers=frozenset({Tier.MALA, Tier.STREDNI}),
        rationale_must_mention=("doporuč",),
        note=(
            "Deterministické minimum je MALA (osobní údaje zaměstnanců pravidlo "
            "nespouští) — testuje, zda model chytne nuanci hodnocení lidí."
        ),
    ),
    ClassificationCase(
        case_id="hranicni-koncept-emailu-klientovi",
        answers=_odpovedi(
            "Píše koncepty odpovědí na dotazy klientů; operátor je před odesláním upravuje.",
            pocet_uzivatelu=PocetUzivatelu.OD_10_DO_50,
            kritictnost=Kritictnost.DULEZITY,
            osobni_udaje=OsobniUdaje.KLIENTU,
            viditelnost=ViditelnostVystupu.NEPRIMO,
            citlivost=CitlivostDat.DUVERNA,
            autonomie=Autonomie.CLOVEK_SCHVALUJE,
            dopad=DopadChyby.FINANCNI_NEBO_KLIENTSKY,
        ),
        components=_EXTERNI,
        expected_tier=Tier.STREDNI,
        acceptable_tiers=frozenset({Tier.STREDNI, Tier.VELKA}),
        rationale_must_mention=("klient", "schval"),
        note="Klientská data ven, ale člověk je poslední kontrola — nemá spadnout do VELKA.",
    ),
    ClassificationCase(
        case_id="hranicni-vyhledavani-duverne-on-prem",
        answers=_odpovedi(
            "Vyhledává v archivu důvěrných smluv; model běží ve firemním datovém centru.",
            pocet_uzivatelu=PocetUzivatelu.OD_10_DO_50,
            kritictnost=Kritictnost.DULEZITY,
            citlivost=CitlivostDat.DUVERNA,
            dopad=DopadChyby.PROVOZNI,
        ),
        components=_ON_PREM,
        expected_tier=Tier.STREDNI,
        acceptable_tiers=frozenset({Tier.MALA, Tier.STREDNI}),
        rationale_must_mention=("důvěrn",),
        note=(
            "Stejná data jako u pravidla EXTERNAL_PROVIDER_SENSITIVE_DATA, ale "
            "on-prem — minimum zůstává MALA. Testuje citlivost modelu na hosting."
        ),
    ),
    ClassificationCase(
        case_id="hranicni-monitoring-kriticke-sluzby",
        answers=_odpovedi(
            "Vyhodnocuje logy kritické platební služby a navrhuje zásah; provede ho operátor.",
            kritictnost=Kritictnost.KRITICKY,
            autonomie=Autonomie.CLOVEK_SCHVALUJE,
            dopad=DopadChyby.PROVOZNI,
        ),
        components=_ON_PREM,
        expected_tier=Tier.STREDNI,
        acceptable_tiers=frozenset({Tier.STREDNI, Tier.VELKA}),
        rationale_must_mention=("kritick", "schval"),
        note="Kritický proces, ale výstup nejde ven a akci provádí člověk.",
    ),
)


# --- Případy kontroly duplicit ---------------------------------------------

DUPLICATE_CASES: tuple[DuplicateCase, ...] = (
    DuplicateCase(
        dup_case_id="dup-shodny-chatbot",
        nazev="Asistent pro dotazy na HR benefity",
        popis="Chatbot odpovídá zaměstnancům na dotazy k benefitům a dovolené.",
        existing=(
            ExistingRecord(
                "app-hr-01",
                "HR asistent na dotazy k benefitům",
                "Chatbot pro zaměstnance odpovídá na dotazy k benefitům, dovolené a mzdě.",
            ),
            ExistingRecord(
                "app-fin-01",
                "Kontrola faktur",
                "Porovnává položky přijatých faktur s objednávkami.",
            ),
            ExistingRecord(
                "app-doc-01",
                "Překladač dokumentace",
                "Překládá technickou dokumentaci z angličtiny do češtiny.",
            ),
        ),
        expected_match_ids=frozenset({"app-hr-01"}),
        note="Jasná duplicita — jiný název, prakticky totožný účel.",
    ),
    DuplicateCase(
        dup_case_id="dup-zadna-shoda",
        nazev="Detekce anomálií v síťovém provozu",
        popis="Sleduje provoz na firemní síti a hlásí neobvyklé vzorce chování.",
        existing=(
            ExistingRecord(
                "app-hr-01",
                "HR asistent na dotazy k benefitům",
                "Chatbot pro zaměstnance odpovídá na dotazy k benefitům a dovolené.",
            ),
            ExistingRecord(
                "app-mkt-01",
                "Generátor textů kampaní",
                "Píše návrhy marketingových textů pro e-mailové kampaně.",
            ),
            ExistingRecord(
                "app-doc-01",
                "Překladač dokumentace",
                "Překládá technickou dokumentaci z angličtiny do češtiny.",
            ),
        ),
        expected_match_ids=frozenset(),
        note="Kontrola falešně pozitivních shod — nic podobného v registru není.",
    ),
    DuplicateCase(
        dup_case_id="dup-podobny-nazev-jiny-ucel",
        nazev="Analyzátor smluv",
        popis="Vyhledává rizikové klauzule v dodavatelských smlouvách a upozorňuje právní oddělení.",
        existing=(
            ExistingRecord(
                "app-sml-01",
                "Analyzátor smluvních SMS",
                "Rozesílá klientům potvrzovací SMS po podpisu smlouvy.",
            ),
            ExistingRecord(
                "app-sml-02",
                "Přehled smluv",
                "Zobrazuje seznam platných smluv v přehledové tabulce, bez analýzy obsahu.",
            ),
            ExistingRecord(
                "app-hr-01",
                "HR asistent na dotazy k benefitům",
                "Chatbot pro zaměstnance odpovídá na dotazy k benefitům.",
            ),
        ),
        expected_match_ids=frozenset(),
        note="Past na shodu slov — „smlouva“ je společné slovo, účel je jiný.",
    ),
    DuplicateCase(
        dup_case_id="dup-dve-shody",
        nazev="Sumarizace zápisů z porad",
        popis="Ze záznamu porady vytvoří stručný souhrn a seznam úkolů.",
        existing=(
            ExistingRecord(
                "app-mee-01",
                "Zápisy z jednání",
                "Z nahrávky jednání vytvoří přepis, souhrn a úkoly pro účastníky.",
            ),
            ExistingRecord(
                "app-mee-02",
                "Souhrn porad týmu",
                "Sumarizuje záznamy týmových porad do krátkého přehledu s úkoly.",
            ),
            ExistingRecord(
                "app-fin-01",
                "Kontrola faktur",
                "Porovnává položky přijatých faktur s objednávkami.",
            ),
            ExistingRecord(
                "app-doc-01",
                "Překladač dokumentace",
                "Překládá technickou dokumentaci z angličtiny do češtiny.",
            ),
        ),
        expected_match_ids=frozenset({"app-mee-01", "app-mee-02"}),
        note="Dvě skutečné duplicity — testuje recall při více shodách.",
    ),
    DuplicateCase(
        dup_case_id="dup-jina-cilova-skupina",
        nazev="Chatbot pro dotazy klientů k pojištění",
        popis="Odpovídá klientům na dotazy k pojistným produktům přes webový chat.",
        existing=(
            ExistingRecord(
                "app-hr-01",
                "HR asistent na dotazy k benefitům",
                "Chatbot odpovídá zaměstnancům na dotazy k benefitům a dovolené.",
            ),
            ExistingRecord(
                "app-cli-01",
                "Klientský chat k produktům",
                "Chatbot na webu odpovídá klientům na dotazy k pojistným produktům.",
            ),
            ExistingRecord(
                "app-doc-01",
                "Překladač dokumentace",
                "Překládá technickou dokumentaci z angličtiny do češtiny.",
            ),
        ),
        expected_match_ids=frozenset({"app-cli-01"}),
        note="Dva chatboti, ale jen jeden má stejnou cílovou skupinu i téma.",
    ),
    DuplicateCase(
        dup_case_id="dup-jiny-nazev-stejny-ucel",
        nazev="Kontrola párování faktur s objednávkami",
        popis="Ověřuje, že položky na přijaté faktuře odpovídají vystavené objednávce.",
        existing=(
            ExistingRecord(
                "app-fin-01",
                "Kontrola faktur",
                "Porovnává položky přijatých faktur s odpovídajícími objednávkami.",
            ),
            ExistingRecord(
                "app-fin-02",
                "Evidence objednávek",
                "Vede seznam vystavených objednávek a jejich stavů.",
            ),
            ExistingRecord(
                "app-mkt-01",
                "Generátor textů kampaní",
                "Píše návrhy marketingových textů pro e-mailové kampaně.",
            ),
        ),
        expected_match_ids=frozenset({"app-fin-01"}),
        note="Popisný název vs. krátký název — shoda musí vzniknout z popisu.",
    ),
    DuplicateCase(
        dup_case_id="dup-prazdny-vysledek-pri-blizkych-slovech",
        nazev="Generátor obrázků pro kampaně",
        popis="Vytváří ilustrační obrázky k marketingovým kampaním podle zadání.",
        existing=(
            ExistingRecord(
                "app-mkt-01",
                "Generátor textů kampaní",
                "Píše návrhy marketingových textů pro e-mailové kampaně.",
            ),
            ExistingRecord(
                "app-mkt-02",
                "Plánovač kampaní",
                "Navrhuje harmonogram marketingových kampaní podle rozpočtu.",
            ),
            ExistingRecord(
                "app-doc-01",
                "Překladač dokumentace",
                "Překládá technickou dokumentaci z angličtiny do češtiny.",
            ),
        ),
        expected_match_ids=frozenset(),
        note="Stejná doména (kampaně), jiná modalita výstupu — nesmí označit shodu.",
    ),
    DuplicateCase(
        dup_case_id="dup-prekryv-s-nadstavbou",
        nazev="Vyhledávání ve znalostní bázi IT podpory",
        popis="Najde v článcích IT podpory řešení nahlášeného problému a shrne postup.",
        existing=(
            ExistingRecord(
                "app-it-01",
                "Znalostní báze IT podpory",
                "Vyhledává v článcích IT podpory a vrací postup řešení problému.",
            ),
            ExistingRecord(
                "app-it-02",
                "Zakládání tiketů",
                "Z příchozích hlášení automaticky zakládá tikety v servisdesku.",
            ),
            ExistingRecord(
                "app-hr-01",
                "HR asistent na dotazy k benefitům",
                "Chatbot odpovídá zaměstnancům na dotazy k benefitům.",
            ),
            ExistingRecord(
                "app-doc-01",
                "Překladač dokumentace",
                "Překládá technickou dokumentaci z angličtiny do češtiny.",
            ),
        ),
        expected_match_ids=frozenset({"app-it-01"}),
        note="Sousední aplikace ze stejné domény (tikety) se označit nemá.",
    ),
)
