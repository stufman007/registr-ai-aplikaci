"""LLM Gateway — jediná povolená cesta aplikace k modelu (spec kap. 7 a 8).

Tok jednoho volání:

    allowlist polí → ořez délek + sanitizace → pseudonymizace → prompt assembly
    → adapter (max 1 retry na přechodné chyby) → Pydantic validace structured
    outputu → deanonymizace → výsledek; audit se zapíše VŽDY.

Architektonická invarianta: konkrétní adaptery (`mock`, později `anthropic`)
importuje **výhradně tento modul**. Zbytek aplikace zná jen `classify()` a
`find_duplicates()`, takže výměna providera za firemní AI Gateway je změna na
jednom místě.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.llm import audit
from app.llm.anonymizer import Anonymizer
from app.llm.base import (
    ERROR_CODE_ADAPTER_ERROR,
    ERROR_CODE_INVALID_RESPONSE,
    RETRYABLE_ERRORS,
    AdapterResult,
    LlmAdapter,
    LlmError,
    error_code_for,
)
from app.llm.anthropic import AnthropicAdapter
from app.llm.gemini_adapter import GeminiAdapter
from app.llm.mock import MockAdapter
from app.llm.openai_adapter import OpenAiAdapter
from app.schemas import DotaznikOdpovedi, KomponentaInfo, LlmPurpose, Tier
from app.services.similarity import FALLBACK_THRESHOLD, MAX_CANDIDATES, Candidate

logger = logging.getLogger(__name__)

#: Maximální délka jednoho volného textu v promptu (spec kap. 7.3).
MAX_FIELD_LENGTH = 4000

#: Kolik kandidátů duplicit smí maximálně odejít do modelu (spec kap. 6).
MAX_CANDIDATES_IN_PROMPT = MAX_CANDIDATES

#: Kolik shod duplicit se přijímá z odpovědi modelu.
MAX_MATCHES = 3

#: Celkový počet pokusů = 1 volání + max 1 retry (spec kap. 8).
MAX_ATTEMPTS = 2

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

FALLBACK_CLASSIFY_MESSAGE = (
    "AI zdůvodnění není momentálně dostupné. "
    "Governance tier byl určen povinnými pravidly."
)
FALLBACK_DUPLICATE_REASON = "Lokální kontrola podobnosti (AI nedostupná)"

_PROMPT_VERSION_RE = re.compile(r"^\s*PROMPT_VERSION:\s*(?P<version>\S+)\s*$", re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


# --- Výstupní typy ---------------------------------------------------------


@dataclass(frozen=True)
class ClassifyOutcome:
    """Výsledek LLM části klasifikace. Policy minimum počítá volající (routes)."""

    llm_tier: Tier | None
    zduvodneni: str
    fallback_used: bool


@dataclass(frozen=True)
class DuplicateMatch:
    """Jedna nalezená podobná aplikace, připravená pro UI."""

    record_id: str
    nazev: str
    duvod: str


@dataclass(frozen=True)
class DuplicatesOutcome:
    matches: list[DuplicateMatch]
    fallback_used: bool


# --- Schémata structured outputu ------------------------------------------


class ClassificationSuggestion(BaseModel):
    """Očekávaný tvar odpovědi pro účel `classify`."""

    klasifikace: Tier
    zduvodneni: str = Field(min_length=1)

    @field_validator("klasifikace", mode="before")
    @classmethod
    def _prijmi_nazev_tieru(cls, value: Any) -> Any:
        """Model vrací `"MALA"`, `Tier` je IntEnum — převedeme podle jména."""
        if isinstance(value, str):
            klic = value.strip().upper()
            if klic in Tier.__members__:
                return Tier[klic]
        return value


class DuplicateMatchItem(BaseModel):
    record_id: str = Field(min_length=1)
    duvod_podobnosti: str = Field(min_length=1)


class DuplicateMatches(BaseModel):
    """Očekávaný tvar odpovědi pro účel `duplicates`."""

    matches: list[DuplicateMatchItem] = Field(default_factory=list)


# --- Factory adapteru ------------------------------------------------------


def get_adapter() -> LlmAdapter:
    """Vrátí adapter dle `LLM_PROVIDER`. Jediné místo, kde se adaptery importují."""
    provider = settings.llm_provider.strip().lower()

    if provider == "mock":
        return MockAdapter()

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=anthropic vyžaduje ANTHROPIC_API_KEY v .env"
            )
        return AnthropicAdapter()

    if provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("LLM_PROVIDER=openai vyžaduje OPENAI_API_KEY v .env")
        return OpenAiAdapter()

    if provider == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError("LLM_PROVIDER=gemini vyžaduje GEMINI_API_KEY v .env")
        return GeminiAdapter()

    raise ValueError(
        f"Neznámý LLM_PROVIDER {settings.llm_provider!r}; "
        "povolené hodnoty: mock, anthropic, openai, gemini"
    )


# --- Veřejné API -----------------------------------------------------------


def classify(
    session: Session,
    answers: DotaznikOdpovedi,
    components: list[KomponentaInfo],
    known_contacts: Iterable[tuple[str, str]] | None = None,
    adapter: LlmAdapter | None = None,
) -> ClassifyOutcome:
    """Navrhne governance tier a české zdůvodnění.

    Allowlist účelu `classify`: odpovědi dotazníku + AI komponenty. Kontakty se
    do payloadu vůbec nesestavují (spec kap. 7.1) — `known_contacts` slouží jen
    anonymizéru k rozpoznání jmen ve volném textu.
    """
    adapter = adapter or get_adapter()
    anonymizer = Anonymizer(known_contacts)
    prompt_version, template = _load_prompt("classification.md")

    prompt = template.replace(
        "{{ODPOVEDI}}", _render_answers(answers, anonymizer)
    ).replace("{{KOMPONENTY}}", _render_components(components))

    result, error_code, latence_ms = _call_adapter(adapter, prompt, LlmPurpose.CLASSIFY)

    suggestion: ClassificationSuggestion | None = None
    if result is not None:
        suggestion = _validate(result.text, ClassificationSuggestion, LlmPurpose.CLASSIFY)
        if suggestion is None:
            error_code = ERROR_CODE_INVALID_RESPONSE

    fallback_used = suggestion is None
    _record(
        session,
        ucel=LlmPurpose.CLASSIFY,
        adapter=adapter,
        result=result,
        prompt_version=prompt_version,
        latence_ms=latence_ms,
        error_code=error_code,
        fallback_used=fallback_used,
    )

    if suggestion is None:
        return ClassifyOutcome(
            llm_tier=Tier.MALA,
            zduvodneni=FALLBACK_CLASSIFY_MESSAGE,
            fallback_used=True,
        )

    # Deanonymizace až po validaci — do mapy se sahá jen na ověřený tvar.
    return ClassifyOutcome(
        llm_tier=suggestion.klasifikace,
        zduvodneni=anonymizer.restore(suggestion.zduvodneni),
        fallback_used=False,
    )


def find_duplicates(
    session: Session,
    nazev: str,
    popis: str,
    candidates: list[Candidate],
    known_contacts: Iterable[tuple[str, str]] | None = None,
    adapter: LlmAdapter | None = None,
) -> DuplicatesOutcome:
    """Vybere z lokálních kandidátů ty, které jsou skutečně podobné.

    Allowlist účelu `duplicates`: název + popis nové aplikace + max 10 kandidátů.
    """
    adapter = adapter or get_adapter()
    anonymizer = Anonymizer(known_contacts)
    prompt_version, template = _load_prompt("duplicates.md")

    posilani = list(candidates[:MAX_CANDIDATES_IN_PROMPT])
    prompt = template.replace(
        "{{NOVA_APLIKACE}}", _render_new_application(nazev, popis, anonymizer)
    ).replace("{{KANDIDATI}}", _render_candidates(posilani, anonymizer))

    result, error_code, latence_ms = _call_adapter(adapter, prompt, LlmPurpose.DUPLICATES)

    parsed: DuplicateMatches | None = None
    if result is not None:
        parsed = _validate(result.text, DuplicateMatches, LlmPurpose.DUPLICATES)
        if parsed is None:
            error_code = ERROR_CODE_INVALID_RESPONSE

    fallback_used = parsed is None
    _record(
        session,
        ucel=LlmPurpose.DUPLICATES,
        adapter=adapter,
        result=result,
        prompt_version=prompt_version,
        latence_ms=latence_ms,
        error_code=error_code,
        fallback_used=fallback_used,
    )

    if parsed is None:
        return DuplicatesOutcome(
            matches=_local_fallback_matches(candidates), fallback_used=True
        )

    znami = {c.record_id: c for c in posilani}
    matches: list[DuplicateMatch] = []
    for item in parsed.matches[:MAX_MATCHES]:
        kandidat = znami.get(item.record_id)
        if kandidat is None:
            # Model si vymyslel ID — tiše zahodit, ale zaznamenat do logu.
            logger.warning("LLM vrátil neznámý record_id pro účel duplicates")
            continue
        matches.append(
            DuplicateMatch(
                record_id=kandidat.record_id,
                nazev=kandidat.nazev,
                duvod=anonymizer.restore(item.duvod_podobnosti),
            )
        )

    return DuplicatesOutcome(matches=matches, fallback_used=False)


# --- Prompt assembly -------------------------------------------------------


@lru_cache(maxsize=8)
def _load_prompt(filename: str) -> tuple[str, str]:
    """Načte šablonu z `prompts/`; vrací `(PROMPT_VERSION, tělo bez hlavičky)`."""
    path = PROMPTS_DIR / filename
    raw = path.read_text(encoding="utf-8")

    match = _PROMPT_VERSION_RE.search(raw)
    if match is None:
        raise ValueError(f"Šablona {filename} nemá hlavičku PROMPT_VERSION")
    version = match.group("version")

    body = _PROMPT_VERSION_RE.sub("", raw, count=1)
    body = _HTML_COMMENT_RE.sub("", body)  # interní poznámky do modelu neposíláme
    return version, body.strip()


def _sanitize(text: str) -> str:
    """Ořez na povolenou délku + odstranění značek datových bloků z uživatelského
    textu (jinak by šlo z popisu „utéct" z datové sekce promptu)."""
    cleaned = text.replace("<<<", "").replace(">>>", "")
    if len(cleaned) > MAX_FIELD_LENGTH:
        cleaned = cleaned[:MAX_FIELD_LENGTH] + "…"
    return cleaned.strip()


def _prepare_free_text(text: str, anonymizer: Anonymizer) -> str:
    """Volný text: sanitizace + ořez, teprve pak pseudonymizace."""
    return anonymizer.pseudonymize(_sanitize(text))


def _render_answers(answers: DotaznikOdpovedi, anonymizer: Anonymizer) -> str:
    """Allowlist `classify` — POUZE odpovědi dotazníku, žádné kontaktní pole."""
    radky = [f"- Účel použití: {_prepare_free_text(answers.ucel, anonymizer)}"]
    polozky = [
        ("Počet uživatelů", answers.pocet_uzivatelu),
        ("Kritičnost pro provoz", answers.kritictnost),
        ("Osobní údaje", answers.osobni_udaje),
        ("Rozhodování o lidech", answers.rozhodovani),
        ("Viditelnost výstupu pro klienta", answers.viditelnost),
        ("Citlivost dat", answers.citlivost),
        ("Autonomie jednání", answers.autonomie),
        ("Dopad chyby", answers.dopad),
    ]
    radky.extend(f"- {popisek}: {hodnota.value} ({hodnota.label()})" for popisek, hodnota in polozky)
    return "\n".join(radky)


def _render_components(components: list[KomponentaInfo]) -> str:
    if not components:
        return "- (žádná AI komponenta nebyla uvedena)"
    return "\n".join(
        f"- Poskytovatel: {c.provider.value} ({c.provider.label()}), "
        f"hosting: {c.hosting_type.value} ({c.hosting_type.label()})"
        for c in components
    )


def _render_new_application(nazev: str, popis: str, anonymizer: Anonymizer) -> str:
    return (
        f"- název: {_prepare_free_text(nazev, anonymizer)}\n"
        f"- popis: {_prepare_free_text(popis, anonymizer)}"
    )


def _render_candidates(candidates: list[Candidate], anonymizer: Anonymizer) -> str:
    if not candidates:
        return "- (registr neobsahuje žádné podobné záznamy)"
    return "\n".join(
        f"- record_id: {c.record_id} "
        f"| název: {_prepare_free_text(c.nazev, anonymizer)} "
        f"| popis: {_prepare_free_text(c.popis, anonymizer)} "
        f"| skóre: {c.score:.2f}"
        for c in candidates
    )


# --- Volání adapteru, validace, audit --------------------------------------


def _call_adapter(
    adapter: LlmAdapter, prompt: str, purpose: LlmPurpose
) -> tuple[AdapterResult | None, str | None, int]:
    """Zavolá adapter; na přechodné chyby zopakuje nejvýš jednou (spec kap. 8).

    Vrací `(výsledek | None, error_code | None, latence v ms)`.
    """
    start = time.monotonic()
    error_code: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = adapter.complete(prompt, purpose)
            return result, None, _elapsed_ms(start)
        except RETRYABLE_ERRORS as exc:
            error_code = error_code_for(exc)
            logger.warning(
                "LLM volání selhalo (ucel=%s, pokus=%d/%d, error_code=%s)",
                purpose,
                attempt,
                MAX_ATTEMPTS,
                error_code,
            )
            if attempt == MAX_ATTEMPTS:
                break
        except LlmError as exc:
            # Neopakovatelná chyba adapteru — druhý pokus by dopadl stejně.
            error_code = error_code_for(exc)
            logger.warning(
                "LLM volání selhalo neopakovatelně (ucel=%s, error_code=%s)",
                purpose,
                error_code,
            )
            break
        except Exception:
            # Failure contract: selhání LLM nikdy neshodí formulář uživatele.
            # Stack trace jde do logu (neobsahuje prompt), aplikace jde do fallbacku.
            error_code = ERROR_CODE_ADAPTER_ERROR
            logger.exception("Neočekávaná chyba LLM adapteru (ucel=%s)", purpose)
            break

    return None, error_code, _elapsed_ms(start)


def _validate[ModelT: BaseModel](
    text: str, model: type[ModelT], purpose: LlmPurpose
) -> ModelT | None:
    """Vytáhne JSON z odpovědi a ověří tvar. `None` = nevalidní (→ fallback)."""
    payload = _extract_json(text)
    if payload is None:
        logger.warning("LLM odpověď neobsahuje JSON (ucel=%s)", purpose)
        return None
    try:
        return model.model_validate(payload)
    except ValidationError:
        # Do logu nikdy neteče obsah odpovědi — jen účel a fakt selhání.
        logger.warning("LLM odpověď neprošla schema validací (ucel=%s)", purpose)
        return None


def _extract_json(text: str) -> dict[str, Any] | None:
    """Najde první `{` a poslední `}` — model občas JSON obalí textem/markdownem."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _record(
    session: Session,
    *,
    ucel: LlmPurpose,
    adapter: LlmAdapter,
    result: AdapterResult | None,
    prompt_version: str,
    latence_ms: int,
    error_code: str | None,
    fallback_used: bool,
) -> None:
    """Audit se zapisuje vždy — i když volání selhalo (spec kap. 8)."""
    audit.record_call(
        session,
        ucel=ucel,
        provider=result.provider if result else _identity(adapter, "provider"),
        model=result.model if result else _identity(adapter, "model"),
        prompt_version=prompt_version,
        tokens_in=result.tokens_in if result else 0,
        tokens_out=result.tokens_out if result else 0,
        latence_ms=latence_ms,
        uspech=not fallback_used,
        error_code=error_code,
        fallback_used=fallback_used,
    )


def _identity(adapter: LlmAdapter, attribute: str) -> str:
    """Provider/model pro audit, když volání selhalo a nemáme `AdapterResult`."""
    return str(getattr(adapter, attribute, "unknown"))


def _local_fallback_matches(candidates: list[Candidate]) -> list[DuplicateMatch]:
    """Fallback duplicit: lokální kandidáti nad prahem (spec kap. 6)."""
    return [
        DuplicateMatch(
            record_id=c.record_id, nazev=c.nazev, duvod=FALLBACK_DUPLICATE_REASON
        )
        for c in candidates
        if c.score >= FALLBACK_THRESHOLD
    ][:MAX_MATCHES]


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
