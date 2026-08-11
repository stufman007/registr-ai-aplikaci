from dataclasses import dataclass
from enum import IntEnum, StrEnum


class Tier(IntEnum):
    """Interní governance tier. IntEnum → přímo porovnatelné a řaditelné (max, <, >)."""

    MALA = 1
    STREDNI = 2
    VELKA = 3

    def label(self) -> str:
        return {
            Tier.MALA: "Malá",
            Tier.STREDNI: "Střední",
            Tier.VELKA: "Velká",
        }[self]


class Stav(StrEnum):
    VYVOJ = "VYVOJ"
    PILOT = "PILOT"
    PROVOZ = "PROVOZ"
    UTLUMENO = "UTLUMENO"

    def label(self) -> str:
        return {
            Stav.VYVOJ: "Vývoj",
            Stav.PILOT: "Pilot",
            Stav.PROVOZ: "Provoz",
            Stav.UTLUMENO: "Utlumeno",
        }[self]


class HostingType(StrEnum):
    EXTERNI_API = "EXTERNI_API"
    FIREMNI_CLOUD = "FIREMNI_CLOUD"
    ON_PREM = "ON_PREM"
    NEZNAME = "NEZNAME"

    def label(self) -> str:
        return {
            HostingType.EXTERNI_API: "Externí API",
            HostingType.FIREMNI_CLOUD: "Firemní cloud",
            HostingType.ON_PREM: "On-prem",
            HostingType.NEZNAME: "Neznámé",
        }[self]


class Provider(StrEnum):
    ANTHROPIC = "ANTHROPIC"
    OPENAI = "OPENAI"
    AZURE = "AZURE"
    INTERNI = "INTERNI"
    JINY = "JINY"

    def label(self) -> str:
        return {
            Provider.ANTHROPIC: "Anthropic",
            Provider.OPENAI: "OpenAI",
            Provider.AZURE: "Azure",
            Provider.INTERNI: "Interní",
            Provider.JINY: "Jiný",
        }[self]


# --- Enumy odpovědí klasifikačního dotazníku (spec kap. 5.1, otázky 2–9) ---


class PocetUzivatelu(StrEnum):
    """Otázka 2."""

    DO_10 = "DO_10"
    OD_10_DO_50 = "OD_10_DO_50"
    PRES_50 = "PRES_50"

    def label(self) -> str:
        return {
            PocetUzivatelu.DO_10: "do 10",
            PocetUzivatelu.OD_10_DO_50: "10–50",
            PocetUzivatelu.PRES_50: "přes 50",
        }[self]


class Kritictnost(StrEnum):
    """Otázka 3."""

    POMOCNY = "POMOCNY"
    DULEZITY = "DULEZITY"
    KRITICKY = "KRITICKY"

    def label(self) -> str:
        return {
            Kritictnost.POMOCNY: "Pomocný",
            Kritictnost.DULEZITY: "Důležitý",
            Kritictnost.KRITICKY: "Kritický",
        }[self]


class OsobniUdaje(StrEnum):
    """Otázka 4."""

    NE = "NE"
    ZAMESTNANCU = "ZAMESTNANCU"
    KLIENTU = "KLIENTU"

    def label(self) -> str:
        return {
            OsobniUdaje.NE: "Ne",
            OsobniUdaje.ZAMESTNANCU: "Zaměstnanců",
            OsobniUdaje.KLIENTU: "Klientů",
        }[self]


class RozhodovaniOLidech(StrEnum):
    """Otázka 5."""

    NE = "NE"
    DOPORUCUJE = "DOPORUCUJE"
    ROZHODUJE_AUTOMATICKY = "ROZHODUJE_AUTOMATICKY"

    def label(self) -> str:
        return {
            RozhodovaniOLidech.NE: "Ne",
            RozhodovaniOLidech.DOPORUCUJE: "Doporučuje, člověk rozhoduje",
            RozhodovaniOLidech.ROZHODUJE_AUTOMATICKY: "Rozhoduje automatizovaně",
        }[self]


class ViditelnostVystupu(StrEnum):
    """Otázka 6."""

    NE = "NE"
    NEPRIMO = "NEPRIMO"
    ANO_PRIMO = "ANO_PRIMO"

    def label(self) -> str:
        return {
            ViditelnostVystupu.NE: "Ne",
            ViditelnostVystupu.NEPRIMO: "Nepřímo (vstupuje do klientských výstupů)",
            ViditelnostVystupu.ANO_PRIMO: "Ano, přímo",
        }[self]


class CitlivostDat(StrEnum):
    """Otázka 7."""

    VEREJNA = "VEREJNA"
    INTERNI = "INTERNI"
    DUVERNA = "DUVERNA"
    VYSOCE_CITLIVA = "VYSOCE_CITLIVA"

    def label(self) -> str:
        return {
            CitlivostDat.VEREJNA: "Veřejná",
            CitlivostDat.INTERNI: "Interní",
            CitlivostDat.DUVERNA: "Důvěrná",
            CitlivostDat.VYSOCE_CITLIVA: "Vysoce citlivá",
        }[self]


class Autonomie(StrEnum):
    """Otázka 8."""

    NE = "NE"
    CLOVEK_SCHVALUJE = "CLOVEK_SCHVALUJE"
    ANO_AUTOMATICKY = "ANO_AUTOMATICKY"

    def label(self) -> str:
        return {
            Autonomie.NE: "Ne",
            Autonomie.CLOVEK_SCHVALUJE: "Člověk schvaluje",
            Autonomie.ANO_AUTOMATICKY: "Ano, automaticky",
        }[self]


class DopadChyby(StrEnum):
    """Otázka 9."""

    ZANEDBATELNY = "ZANEDBATELNY"
    PROVOZNI = "PROVOZNI"
    FINANCNI_NEBO_KLIENTSKY = "FINANCNI_NEBO_KLIENTSKY"
    ZASADNI = "ZASADNI"

    def label(self) -> str:
        return {
            DopadChyby.ZANEDBATELNY: "Zanedbatelný",
            DopadChyby.PROVOZNI: "Provozní",
            DopadChyby.FINANCNI_NEBO_KLIENTSKY: "Finanční nebo klientský",
            DopadChyby.ZASADNI: "Zásadní",
        }[self]


class AuditAction(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    CLASSIFY = "CLASSIFY"
    RETIRE = "RETIRE"
    RESTORE = "RESTORE"
    DUPLICATE_OVERRIDE = "DUPLICATE_OVERRIDE"


class LlmPurpose(StrEnum):
    CLASSIFY = "classify"
    DUPLICATES = "duplicates"


# --- Sdílené vstupní DTO pro policy/regulatory/gateway ---


@dataclass(frozen=True)
class DotaznikOdpovedi:
    """Snapshot odpovědí klasifikačního dotazníku (spec kap. 5.1, otázky 1–9)."""

    ucel: str
    pocet_uzivatelu: PocetUzivatelu
    kritictnost: Kritictnost
    osobni_udaje: OsobniUdaje
    rozhodovani: RozhodovaniOLidech
    viditelnost: ViditelnostVystupu
    citlivost: CitlivostDat
    autonomie: Autonomie
    dopad: DopadChyby


@dataclass(frozen=True)
class KomponentaInfo:
    """Minimální info o AI komponentě potřebné pro deterministická pravidla."""

    provider: Provider
    hosting_type: HostingType
