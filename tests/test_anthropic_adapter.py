"""Adapter pro Anthropic Claude (spec kap. 7, plán Fáze 7).

Žádné volání do skutečného API — `anthropic.Anthropic` klient je vždy
nahrazen dvojníkem. Testy ověřují: úspěšnou odpověď, mapování vybraných
výjimek SDK, že konstruktor klienta dostane timeout z konfigurace, a že
`get_adapter()` selže/uspěje podle `LLM_PROVIDER` a `ANTHROPIC_API_KEY`.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import anthropic as anthropic_sdk
from app.llm import gateway
from app.llm.anthropic import AnthropicAdapter
from app.llm.base import LlmRateLimited, LlmServerError, LlmTimeout, LlmTransportError
from app.schemas import LlmPurpose


# --- Dvojníci odpovědi SDK --------------------------------------------------


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str, model: str, tokens_in: int, tokens_out: int) -> None:
        self.content = [_FakeTextBlock(text)]
        self.model = model
        self.usage = _FakeUsage(tokens_in, tokens_out)


class _FakeMessages:
    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self._result = result
        self._error = error
        self.create_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> Any:
        self.create_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._result


class _FakeAnthropicClient:
    """Dvojník `anthropic.Anthropic` — zaznamená konstrukční argumenty."""

    last_instance: "_FakeAnthropicClient | None" = None

    def __init__(self, *, result: Any = None, error: BaseException | None = None, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.messages = _FakeMessages(result=result, error=error)
        _FakeAnthropicClient.last_instance = self


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, *, result: Any = None, error: BaseException | None = None) -> _FakeAnthropicClient:
    """Nahradí `anthropic.Anthropic` továrnou vracející připravený dvojník."""

    def factory(**kwargs: Any) -> _FakeAnthropicClient:
        return _FakeAnthropicClient(result=result, error=error, **kwargs)

    monkeypatch.setattr(anthropic_sdk, "Anthropic", factory)
    return factory  # type: ignore[return-value]


def _dummy_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _dummy_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=_dummy_request())


# --- Úspěšná odpověď ---------------------------------------------------------


def test_complete_vraci_adapter_result_se_spravnymi_tokeny_a_modelem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "test-key")
    monkeypatch.setattr("app.config.settings.anthropic_model", "claude-sonnet-5")
    monkeypatch.setattr("app.config.settings.llm_timeout_seconds", 7)

    fake_message = _FakeMessage(
        text='{"klasifikace": "MALA", "zduvodneni": "test"}',
        model="claude-sonnet-5-20260101",
        tokens_in=42,
        tokens_out=13,
    )
    _install_fake_client(monkeypatch, result=fake_message)

    adapter = AnthropicAdapter()
    result = adapter.complete("prompt text", LlmPurpose.CLASSIFY)

    assert result.text == '{"klasifikace": "MALA", "zduvodneni": "test"}'
    assert result.tokens_in == 42
    assert result.tokens_out == 13
    assert result.provider == "anthropic"
    # Skutečně použitý model z odpovědi, ne jen konfigurovaný alias.
    assert result.model == "claude-sonnet-5-20260101"


def test_complete_posila_spravny_model_a_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "test-key")
    monkeypatch.setattr("app.config.settings.anthropic_model", "claude-sonnet-5")

    fake_message = _FakeMessage("{}", "claude-sonnet-5", 1, 1)
    _install_fake_client(monkeypatch, result=fake_message)

    adapter = AnthropicAdapter()
    adapter.complete("muj prompt", LlmPurpose.DUPLICATES)

    client = _FakeAnthropicClient.last_instance
    assert client is not None
    sent = client.messages.create_kwargs
    assert sent is not None
    assert sent["model"] == "claude-sonnet-5"
    assert sent["messages"] == [{"role": "user", "content": "muj prompt"}]


# --- Konstruktor klienta dostane timeout -------------------------------------


def test_konstruktor_klienta_dostane_timeout_ze_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "test-key")
    monkeypatch.setattr("app.config.settings.llm_timeout_seconds", 42)
    _install_fake_client(monkeypatch, result=_FakeMessage("{}", "m", 1, 1))

    AnthropicAdapter()

    client = _FakeAnthropicClient.last_instance
    assert client is not None
    assert client.init_kwargs["timeout"] == 42
    assert client.init_kwargs["api_key"] == "test-key"


# --- Mapování výjimek --------------------------------------------------------


def test_api_timeout_error_se_mapuje_na_llm_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "test-key")
    error = anthropic_sdk.APITimeoutError(request=_dummy_request())
    _install_fake_client(monkeypatch, error=error)

    adapter = AnthropicAdapter()
    with pytest.raises(LlmTimeout):
        adapter.complete("prompt", LlmPurpose.CLASSIFY)


def test_rate_limit_error_se_mapuje_na_llm_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "test-key")
    error = anthropic_sdk.RateLimitError(
        "rate limited", response=_dummy_response(429), body=None
    )
    _install_fake_client(monkeypatch, error=error)

    adapter = AnthropicAdapter()
    with pytest.raises(LlmRateLimited):
        adapter.complete("prompt", LlmPurpose.CLASSIFY)


def test_internal_server_error_se_mapuje_na_llm_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "test-key")
    error = anthropic_sdk.InternalServerError(
        "server error", response=_dummy_response(500), body=None
    )
    _install_fake_client(monkeypatch, error=error)

    adapter = AnthropicAdapter()
    with pytest.raises(LlmServerError):
        adapter.complete("prompt", LlmPurpose.CLASSIFY)


def test_overloaded_5xx_se_take_mapuje_na_llm_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """529 (`OverloadedError`) je taky `APIStatusError` s 5xx statusem."""
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "test-key")
    error = anthropic_sdk.OverloadedError(
        "overloaded", response=_dummy_response(529), body=None
    )
    _install_fake_client(monkeypatch, error=error)

    adapter = AnthropicAdapter()
    with pytest.raises(LlmServerError):
        adapter.complete("prompt", LlmPurpose.CLASSIFY)


def test_api_connection_error_se_mapuje_na_llm_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "test-key")
    error = anthropic_sdk.APIConnectionError(request=_dummy_request())
    _install_fake_client(monkeypatch, error=error)

    adapter = AnthropicAdapter()
    with pytest.raises(LlmTransportError):
        adapter.complete("prompt", LlmPurpose.CLASSIFY)


def test_4xx_mimo_429_propadne_nezmapovane(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nepřechodná chyba (např. neplatný požadavek) se nemapuje — gateway ji
    zachytí obecným `except Exception` jako ADAPTER_ERROR, bez retry."""
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "test-key")
    error = anthropic_sdk.BadRequestError(
        "bad request", response=_dummy_response(400), body=None
    )
    _install_fake_client(monkeypatch, error=error)

    adapter = AnthropicAdapter()
    with pytest.raises(anthropic_sdk.BadRequestError):
        adapter.complete("prompt", LlmPurpose.CLASSIFY)


# --- get_adapter() ------------------------------------------------------------


def test_get_adapter_anthropic_bez_klice_selze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.config.settings.anthropic_api_key", None)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        gateway.get_adapter()


def test_get_adapter_anthropic_s_klicem_vraci_anthropic_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.config.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "test-key")

    adapter = gateway.get_adapter()

    assert isinstance(adapter, AnthropicAdapter)
