"""Adapter pro Google Gemini (spec kap. 7, stejný kontrakt jako `anthropic.py`).

Implementace `LlmAdapter` nad oficiálním SDK `google-genai` (`from google import
genai`). Klíč a model z `app.config.settings`, timeout přes `HttpOptions`.
Pozor: `HttpOptions.timeout` je v **milisekundách**, kdežto
`LLM_TIMEOUT_SECONDS` v sekundách — proto přepočet.

Mapování výjimek SDK → naše (spec kap. 8 — jen tyto smí mít retry):
    errors.ServerError               → LlmServerError   (HTTP 5xx)
    errors.ClientError s code 429    → LlmRateLimited
    httpx.TimeoutException           → LlmTimeout
    ostatní httpx.TransportError     → LlmTransportError

`google-genai` sám síťové chyby nezabaluje — pod kapotou jezdí na `httpx`,
takže timeout a spadlé spojení propadnou jako `httpx` výjimky. `TimeoutException`
je podtřída `TransportError`, proto musí být uvedena dřív.

Ostatní `ClientError` (4xx mimo 429 — neplatný klíč, neznámý model) záměrně
NEmapujeme: jsou trvalé, gateway je zachytí jako `ADAPTER_ERROR` bez retry.

Žádné logování obsahu promptu ani odpovědi.
"""

from __future__ import annotations

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.config import settings
from app.llm.base import AdapterResult, LlmRateLimited, LlmServerError, LlmTimeout, LlmTransportError
from app.schemas import LlmPurpose

PROVIDER = "gemini"

#: HTTP status rate limitu — SDK ho nese v `ClientError.code`.
HTTP_TOO_MANY_REQUESTS = 429

#: Horní limit délky odpovědi (viz poznámka o reasoning tokenech u OpenAI).
MAX_OUTPUT_TOKENS = 2048


class GeminiAdapter:
    """Implementace `LlmAdapter` nad oficiálním SDK `google-genai`."""

    provider: str = PROVIDER

    def __init__(self, model: str | None = None) -> None:
        """`model` přebije `GEMINI_MODEL` ze settings (kvůli eval harnessu,
        který porovnává víc modelů téhož providera v jednom běhu)."""
        self.model = model or settings.gemini_model
        self._client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=genai_types.HttpOptions(
                timeout=settings.llm_timeout_seconds * 1000
            ),
        )

    def complete(self, prompt: str, purpose: LlmPurpose) -> AdapterResult:
        del purpose  # Gemini API účel nerozlišuje — ten je jen součástí promptu.

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    max_output_tokens=MAX_OUTPUT_TOKENS
                ),
            )
        except genai_errors.ServerError as exc:
            raise LlmServerError(str(exc)) from exc
        except genai_errors.ClientError as exc:
            if exc.code == HTTP_TOO_MANY_REQUESTS:
                raise LlmRateLimited(str(exc)) from exc
            raise  # 4xx mimo 429 — nepřechodná chyba, gateway ji nezopakuje.
        except httpx.TimeoutException as exc:
            raise LlmTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            raise LlmTransportError(str(exc)) from exc

        tokens_in, tokens_out = _extract_usage(response)
        return AdapterResult(
            text=_extract_text(response),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=response.model_version or self.model,
            provider=PROVIDER,
        )


def _extract_text(response: genai_types.GenerateContentResponse) -> str:
    """Text odpovědi; prázdný řetězec, když model nevrátil žádnou textovou část
    (např. zásah bezpečnostního filtru) — gateway to vyhodnotí jako nevalidní
    odpověď a jde do fallbacku."""
    try:
        return response.text or ""
    except (ValueError, AttributeError):
        # SDK u některých stavů odpovědi `.text` neumí složit; obsah nikam
        # nelogujeme, jen degradujeme na prázdný text.
        return ""


def _extract_usage(response: genai_types.GenerateContentResponse) -> tuple[int, int]:
    """Tokeny z `usage_metadata`.

    Do výstupních tokenů se přičítají i `thoughts_token_count` — reasoning
    tokeny se u Gemini účtují jako výstup, takže bez nich by odhad ceny
    v evalu podstřeloval.
    """
    usage = response.usage_metadata
    if usage is None:
        return 0, 0
    tokens_in = usage.prompt_token_count or 0
    tokens_out = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
    return tokens_in, tokens_out
