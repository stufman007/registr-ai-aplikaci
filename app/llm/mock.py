"""Deterministický mock adapter — kompletní demo bez sítě a bez API klíče.

Není to „náhodný generátor". Nad datovými bloky promptu běží jednoduchá váhová
heuristika: čím víc rizikových odpovědí, tím vyšší tier. Stejný vstup proto vždy
vrátí stejný výstup, což dělá testy i demo reprodukovatelné.

Mock čte **jen obsah datových bloků** (`<<<DATA_…>>>`), nikoli instrukční část
promptu — jinak by heuristiku ovlivnila slova ze šablony.
"""

from __future__ import annotations

import json
import logging
import re

from app.config import settings
from app.llm.base import AdapterResult, LlmTimeout, extract_data_block
from app.schemas import LlmPurpose, Tier

logger = logging.getLogger(__name__)

PROVIDER = "mock"
MODEL = "mock-deterministic-v1"

# Rizikové odpovědi s vahou. Klíč = kód enum hodnoty tak, jak ho gateway
# vypisuje do datového bloku; hodnota = (váha, česká citace do zdůvodnění).
_WEIGHTED_ANSWERS: dict[str, tuple[int, str]] = {
    "ROZHODUJE_AUTOMATICKY": (2, "rozhoduje o lidech automatizovaně"),
    "ANO_AUTOMATICKY": (2, "jedná autonomně bez schválení člověkem"),
    "ZASADNI": (2, "dopad chyby je zásadní"),
    "VYSOCE_CITLIVA": (2, "pracuje s vysoce citlivými daty"),
    "KLIENTU": (2, "zpracovává osobní údaje klientů"),
    "KRITICKY": (2, "je pro provoz kritická"),
    "DOPORUCUJE": (1, "doporučuje rozhodnutí o lidech, rozhoduje člověk"),
    "CLOVEK_SCHVALUJE": (1, "koná až po schválení člověkem"),
    "FINANCNI_NEBO_KLIENTSKY": (1, "chyba má finanční nebo klientský dopad"),
    "DUVERNA": (1, "pracuje s důvěrnými daty"),
    "ZAMESTNANCU": (1, "zpracovává osobní údaje zaměstnanců"),
    "DULEZITY": (1, "je pro provoz důležitá"),
    "PRES_50": (1, "má přes 50 uživatelů"),
    "ANO_PRIMO": (1, "výstup vidí klient přímo"),
    "NEPRIMO": (1, "výstup nepřímo vstupuje do klientských výstupů"),
}

# Prahy heuristiky nad součtem vah.
_THRESHOLD_VELKA = 6
_THRESHOLD_STREDNI = 3

_PLACEHOLDER_RE = re.compile(r"\[(?:OSOBA|EMAIL|TEL)_\d+\]")
_CANDIDATE_RE = re.compile(
    r"record_id:\s*(?P<id>[^\s|]+).*?skóre:\s*(?P<score>\d+(?:\.\d+)?)"
)
#: Zkratky aktivních signálů v bloku `{{SIGNALY}}` — viz `gateway._render_active_flags`.
_SIGNAL_ZKRATKA_RE = re.compile(r"zkratka:\s*(?P<zkratka>[A-Z-]+)")

# Kandidáti nad tímto skóre se v mocku považují za „skutečně podobné".
_MOCK_MATCH_THRESHOLD = 0.6
_MOCK_MAX_MATCHES = 2


class MockAdapter:
    """Implementace `LlmAdapter` bez sítě. Deterministická, offline, bez klíče."""

    provider: str = PROVIDER
    model: str = MODEL

    def complete(self, prompt: str, purpose: LlmPurpose) -> AdapterResult:
        if settings.llm_force_fail:
            # Demo cesta selhání: LLM_FORCE_FAIL=true ukáže fallback bez sítě.
            logger.info("MockAdapter: LLM_FORCE_FAIL aktivní, simuluji timeout (ucel=%s)", purpose)
            raise LlmTimeout("LLM_FORCE_FAIL=true — simulované selhání mock adapteru")

        if purpose == LlmPurpose.CLASSIFY:
            text = self._classify(prompt)
        elif purpose == LlmPurpose.DUPLICATES:
            text = self._duplicates(prompt)
        else:  # pragma: no cover — enum má jen dvě hodnoty
            raise ValueError(f"MockAdapter neumí účel {purpose!r}")

        return AdapterResult(
            text=text,
            tokens_in=_estimate_tokens(prompt),
            tokens_out=_estimate_tokens(text),
            model=MODEL,
            provider=PROVIDER,
        )

    # --- Účel: klasifikace -------------------------------------------------

    def _classify(self, prompt: str) -> str:
        odpovedi = extract_data_block(prompt, "ODPOVEDI")
        komponenty = extract_data_block(prompt, "KOMPONENTY")
        signaly = extract_data_block(prompt, "SIGNALY")

        score, citace = _score_answers(odpovedi)
        externi_api = "EXTERNI_API" in komponenty
        if externi_api:
            score += 1

        tier = _tier_for_score(score)
        zduvodneni = _build_reasoning(tier, citace, externi_api, odpovedi)
        signal_context = _build_signal_context(signaly, citace, odpovedi)

        return json.dumps(
            {
                "klasifikace": tier.name,
                "zduvodneni": zduvodneni,
                "signal_context": signal_context,
            },
            ensure_ascii=False,
        )

    # --- Účel: duplicity ---------------------------------------------------

    def _duplicates(self, prompt: str) -> str:
        kandidati = extract_data_block(prompt, "KANDIDATI")

        matches: list[dict[str, str]] = []
        for line in kandidati.splitlines():
            match = _CANDIDATE_RE.search(line)
            if match is None:
                continue
            score = float(match.group("score"))
            if score < _MOCK_MATCH_THRESHOLD:
                continue
            matches.append(
                {
                    "record_id": match.group("id"),
                    "duvod_podobnosti": (
                        "Název i popis se výrazně překrývají s novou aplikací "
                        f"(lokální skóre podobnosti {score:.2f})."
                    ),
                }
            )
            if len(matches) == _MOCK_MAX_MATCHES:
                break

        return json.dumps({"matches": matches}, ensure_ascii=False)


def _score_answers(odpovedi: str) -> tuple[int, list[str]]:
    """Součet vah rizikových odpovědí + české citace pro zdůvodnění."""
    score = 0
    citace: list[str] = []
    for code, (weight, popis) in _WEIGHTED_ANSWERS.items():
        if re.search(rf"\b{re.escape(code)}\b", odpovedi):
            score += weight
            citace.append(popis)
    return score, citace


def _tier_for_score(score: int) -> Tier:
    if score >= _THRESHOLD_VELKA:
        return Tier.VELKA
    if score >= _THRESHOLD_STREDNI:
        return Tier.STREDNI
    return Tier.MALA


def _build_reasoning(
    tier: Tier, citace: list[str], externi_api: bool, odpovedi: str
) -> str:
    """Sestaví 2–4 věty česky, které citují konkrétní odpovědi dotazníku."""
    vety = [f"Navrhuji tier {tier.label().upper()} na základě odpovědí dotazníku."]

    if citace:
        # Nejvýš tři citace, aby zdůvodnění zůstalo čitelné.
        vety.append("Aplikace " + ", ".join(citace[:3]) + ".")
    else:
        vety.append(
            "Odpovědi neuvádějí žádný z rizikových faktorů "
            "(osobní údaje, rozhodování o lidech, autonomní jednání, citlivá data)."
        )

    if externi_api:
        vety.append(
            "Součástí řešení je AI komponenta u externího API providera, "
            "což zvyšuje nároky na ochranu odesílaných dat."
        )

    placeholders = _PLACEHOLDER_RE.findall(odpovedi)
    if placeholders:
        vety.append(f"Účel použití zmiňuje kontaktní osobu {placeholders[0]}.")

    return " ".join(vety)


def _build_signal_context(signaly_block: str, citace: list[str], odpovedi: str) -> dict[str, str]:
    """Kontextová věta na signál — deterministická mock verze feature AI kontext
    (spec kap. 5.4). Cituje stejné odpovědi jako `_build_reasoning`, aby demo
    ukázalo záznam-specifický text bez API klíče. Placeholder z odpovědí (je-li)
    se propíše do věty, aby šla ověřit deanonymizace v `gateway.classify`."""
    zkratky = _SIGNAL_ZKRATKA_RE.findall(signaly_block)
    if not zkratky:
        return {}

    citace_text = ", ".join(citace[:2]) if citace else "standardní odpovědi dotazníku"
    veta = f"Kontext (mock): aplikace dle odpovědí {citace_text}."

    placeholders = _PLACEHOLDER_RE.findall(odpovedi)
    if placeholders:
        veta += f" Kontaktní osoba ve zdroji: {placeholders[0]}."

    return {zkratka: veta for zkratka in zkratky}


def _estimate_tokens(text: str) -> int:
    """Hrubý deterministický odhad (~4 znaky na token). Mock nemá tokenizér."""
    return max(1, len(text) // 4)
