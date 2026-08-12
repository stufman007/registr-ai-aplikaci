"""Adapter pro OpenAI (spec kap. 7, stejný kontrakt jako `anthropic.py`).

Implementace `LlmAdapter` nad oficiálním `openai` SDK, endpoint
`chat.completions` — je stabilnější a napříč modely lépe podporovaný než
novější Responses API, a pro náš krátký structured output nic navíc nepotřebujeme.

Mapování výjimek SDK → naše (spec kap. 8 — jen tyto smí mít retry):
    APITimeoutError     → LlmTimeout
    RateLimitError      → LlmRateLimited
    APIStatusError 5xx  → LlmServerError
    APIConnectionError  → LlmTransportError

Pořadí `except` větví je významné: `APITimeoutError` je podtřída
`APIConnectionError` a `RateLimitError` podtřída `APIStatusError`, takže
konkrétnější typy musí být uvedeny dřív.

Ostatní chyby SDK (4xx mimo 429 — neplatný klíč, neznámý model, vadný
požadavek) záměrně NEmapujeme: jsou trvalé, gateway je zachytí obecným
`except Exception` jako `ADAPTER_ERROR` a nezopakuje je.

Žádné logování obsahu promptu ani odpovědi — audit píše jen gateway, a to
pouze metadata.
"""

from __future__ import annotations

import openai
from openai.types.chat import ChatCompletion

from app.config import settings
from app.llm.base import AdapterResult, LlmRateLimited, LlmServerError, LlmTimeout, LlmTransportError
from app.schemas import LlmPurpose

PROVIDER = "openai"

#: Horní limit délky odpovědi. Structured output je krátký, ale u reasoning
#: modelů se do stejného limitu počítají i interní „přemýšlecí" tokeny —
#: proto větší rezerva než u Anthropicu, jinak by odpověď došla prázdná.
MAX_OUTPUT_TOKENS = 2048


class OpenAiAdapter:
    """Implementace `LlmAdapter` nad oficiálním OpenAI SDK."""

    provider: str = PROVIDER

    def __init__(self, model: str | None = None) -> None:
        """`model` přebije `OPENAI_MODEL` ze settings (potřebuje eval harness,
        který porovnává víc modelů téhož providera v jednom běhu)."""
        self.model = model or settings.openai_model
        self._client = openai.OpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.llm_timeout_seconds,
        )

    def complete(self, prompt: str, purpose: LlmPurpose) -> AdapterResult:
        del purpose  # OpenAI API účel nerozlišuje — ten je jen součástí promptu.

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                max_completion_tokens=MAX_OUTPUT_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
        except openai.APITimeoutError as exc:
            raise LlmTimeout(str(exc)) from exc
        except openai.RateLimitError as exc:
            raise LlmRateLimited(str(exc)) from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LlmServerError(str(exc)) from exc
            raise  # 4xx mimo 429 — nepřechodná chyba, gateway ji nezopakuje.
        except openai.APIConnectionError as exc:
            raise LlmTransportError(str(exc)) from exc

        tokens_in, tokens_out = _extract_usage(response)
        return AdapterResult(
            text=_extract_text(response),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=response.model or self.model,
            provider=PROVIDER,
        )


def _extract_text(response: ChatCompletion) -> str:
    """Text první volby odpovědi; prázdný řetězec, když model nic nevrátil."""
    if not response.choices:
        return ""
    return response.choices[0].message.content or ""


def _extract_usage(response: ChatCompletion) -> tuple[int, int]:
    """Tokeny z `usage`. Když je blok chybí, vrátíme nuly (audit je snese)."""
    usage = response.usage
    if usage is None:
        return 0, 0
    return usage.prompt_tokens, usage.completion_tokens
