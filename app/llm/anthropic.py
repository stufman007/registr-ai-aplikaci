"""Adapter pro Anthropic Claude (spec kap. 7, plán Fáze 7).

Implementace `LlmAdapter` nad oficiálním `anthropic` SDK. Klíč a model se
berou z `app.config.settings`; SDK klientu se předává `timeout` z
`LLM_TIMEOUT_SECONDS`, aby platil stejný limit jako pro mock adapter.

Mapování výjimek SDK → naše (spec kap. 8 — jen tyto smí mít retry):
    APITimeoutError    → LlmTimeout
    RateLimitError      → LlmRateLimited
    APIStatusError 5xx  → LlmServerError
    APIConnectionError → LlmTransportError

Ostatní chyby SDK (4xx mimo 429 — např. neplatný klíč, neplatný požadavek)
záměrně NEmapujeme a necháváme je propadnout beze změny: gateway je zachytí
obecným `except Exception` jako `ADAPTER_ERROR` bez retry — takové chyby jsou
totiž trvalé (opakování volání by dopadlo stejně).

Žádné logování obsahu promptu ani odpovědi — jen `app/llm/gateway.py` smí
zapisovat audit záznam, a ten obsahuje jen metadata (tokeny, latence, kódy).
"""

from __future__ import annotations

import anthropic
from anthropic.types import Message

from app.config import settings
from app.llm.base import AdapterResult, LlmRateLimited, LlmServerError, LlmTimeout, LlmTransportError
from app.schemas import LlmPurpose

PROVIDER = "anthropic"

#: Horní limit délky odpovědi. Structured output (JSON tier/duplicity) je
#: krátký; 1024 tokenů je dostatečná rezerva i pro víceřádkové zdůvodnění.
MAX_TOKENS = 1024


class AnthropicAdapter:
    """Implementace `LlmAdapter` nad oficiálním Anthropic SDK."""

    provider: str = PROVIDER

    def __init__(self) -> None:
        self.model = settings.anthropic_model
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.llm_timeout_seconds,
        )

    def complete(self, prompt: str, purpose: LlmPurpose) -> AdapterResult:
        del purpose  # Anthropic API účel nerozlišuje — ten je jen součástí promptu.

        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APITimeoutError as exc:
            raise LlmTimeout(str(exc)) from exc
        except anthropic.RateLimitError as exc:
            raise LlmRateLimited(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LlmServerError(str(exc)) from exc
            raise  # 4xx mimo 429 — nepřechodná chyba, gateway ji nezopakuje.
        except anthropic.APIConnectionError as exc:
            raise LlmTransportError(str(exc)) from exc

        return AdapterResult(
            text=_extract_text(response),
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            model=response.model,
            provider=PROVIDER,
        )


def _extract_text(response: Message) -> str:
    """Vrátí text prvního textového bloku odpovědi; jinak prázdný řetězec."""
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""
