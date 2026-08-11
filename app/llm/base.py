"""Abstrakce nad LLM providerem (spec kap. 7, plán Fáze 6).

`LlmAdapter` je jediné místo, kudy aplikace mluví s modelem. Výměna za firemní
AI Gateway = nová třída implementující tento protokol; zbytek aplikace se nemění.

Modul obsahuje jen kontrakt (protokol, výsledek, výjimky, značky datových bloků).
Žádnou síť, žádnou konfiguraci, žádné volání — aby šel importovat odkudkoli.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.schemas import LlmPurpose


@dataclass(frozen=True)
class AdapterResult:
    """Surová odpověď adapteru. `text` se dál validuje v gateway, nikam se neloguje."""

    text: str
    tokens_in: int
    tokens_out: int
    model: str
    provider: str


class LlmAdapter(Protocol):
    """Kontrakt každého LLM adapteru (mock, anthropic, firemní gateway…).

    Implementace si timeout drží interně (z konfigurace) — gateway ho neposílá
    v každém volání, aby zůstala signatura minimální a vyměnitelná.
    """

    def complete(self, prompt: str, purpose: LlmPurpose) -> AdapterResult:
        """Pošle prompt modelu a vrátí surový text odpovědi + metadata."""
        ...


# --- Výjimky ---------------------------------------------------------------
#
# Dělení není kosmetické: gateway podle typu rozhoduje, jestli smí zopakovat
# volání (spec kap. 8 — retry jen na timeout / transport / rate-limit / 5xx).


class LlmError(Exception):
    """Základ všech chyb LLM vrstvy."""


class LlmTimeout(LlmError):
    """Provider neodpověděl do `LLM_TIMEOUT_SECONDS`."""


class LlmTransportError(LlmError):
    """Síťová/transportní chyba (DNS, spojení, TLS)."""


class LlmRateLimited(LlmError):
    """Provider odmítl volání kvůli rate limitu (HTTP 429)."""


class LlmServerError(LlmError):
    """Chyba na straně providera (HTTP 5xx)."""


#: Chyby, u kterých má smysl zopakovat volání (max 1 retry).
RETRYABLE_ERRORS: tuple[type[LlmError], ...] = (
    LlmTimeout,
    LlmTransportError,
    LlmRateLimited,
    LlmServerError,
)

#: Kód selhání do `llm_audit.error_code` (metadata, nikdy obsah odpovědi).
ERROR_CODE_TIMEOUT = "TIMEOUT"
ERROR_CODE_TRANSPORT = "TRANSPORT"
ERROR_CODE_RATE_LIMIT = "RATE_LIMIT"
ERROR_CODE_PROVIDER_5XX = "PROVIDER_5XX"
ERROR_CODE_INVALID_RESPONSE = "INVALID_RESPONSE"
ERROR_CODE_ADAPTER_ERROR = "ADAPTER_ERROR"

_ERROR_CODES: dict[type[BaseException], str] = {
    LlmTimeout: ERROR_CODE_TIMEOUT,
    LlmTransportError: ERROR_CODE_TRANSPORT,
    LlmRateLimited: ERROR_CODE_RATE_LIMIT,
    LlmServerError: ERROR_CODE_PROVIDER_5XX,
}


def error_code_for(exc: BaseException) -> str:
    """Přeloží výjimku na stabilní kód do auditu (nikdy text výjimky)."""
    for exc_type, code in _ERROR_CODES.items():
        if isinstance(exc, exc_type):
            return code
    return ERROR_CODE_ADAPTER_ERROR


# --- Značky datových bloků -------------------------------------------------
#
# Uživatelské texty se v promptu ohraničují jako DATA, ne instrukce (obrana
# proti prompt injection, spec kap. 7.3). Značky žijí tady, protože je používá
# gateway (sestavení promptu) i mock adapter (čtení dat z promptu).


def block_start(name: str) -> str:
    return f"<<<DATA_{name}>>>"


def block_end(name: str) -> str:
    return f"<<<KONEC_DATA_{name}>>>"


def extract_data_block(text: str, name: str) -> str:
    """Vrátí obsah mezi značkami bloku `name`, nebo prázdný řetězec."""
    start_marker = block_start(name)
    end_marker = block_end(name)

    start = text.find(start_marker)
    if start == -1:
        return ""
    start += len(start_marker)

    end = text.find(end_marker, start)
    if end == -1:
        return ""

    return text[start:end].strip()
