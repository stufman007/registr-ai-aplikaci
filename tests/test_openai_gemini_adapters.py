"""Adaptery pro OpenAI a Gemini (app/llm/openai_adapter.py, gemini_adapter.py).

Žádné volání do skutečného API — klienti obou SDK jsou vždy nahrazeni dvojníky.
Testy ověřují: úspěšnou odpověď včetně tokenů, mapování výjimek SDK na naše
(retry v gateway závisí právě na typu), override modelu v konstruktoru (potřebuje
eval harness) a fail-fast `get_adapter()` bez API klíče.
"""

from __future__ import annotations

from typing import Any

import httpx
import httpx2
import openai as openai_sdk
import pytest
from google import genai as genai_sdk
from google.genai import errors as genai_errors

from app.llm import gateway
from app.llm.base import LlmRateLimited, LlmServerError, LlmTimeout, LlmTransportError
from app.llm.gemini_adapter import GeminiAdapter
from app.llm.openai_adapter import OpenAiAdapter
from app.schemas import LlmPurpose


# --- Dvojníci OpenAI SDK -----------------------------------------------------


class _FakeOpenAiUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeOpenAiChoice:
    def __init__(self, content: str | None) -> None:
        self.message = type("_Msg", (), {"content": content})()


class _FakeChatCompletion:
    def __init__(self, text: str | None, model: str, tokens_in: int, tokens_out: int) -> None:
        self.choices = [_FakeOpenAiChoice(text)]
        self.model = model
        self.usage = _FakeOpenAiUsage(tokens_in, tokens_out)


class _FakeCompletions:
    def __init__(self, result: Any, error: BaseException | None) -> None:
        self._result = result
        self._error = error
        self.create_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> Any:
        self.create_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._result


class _FakeOpenAiClient:
    last_instance: "_FakeOpenAiClient | None" = None

    def __init__(self, *, result: Any = None, error: BaseException | None = None, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.completions = _FakeCompletions(result, error)
        self.chat = type("_Chat", (), {"completions": self.completions})()
        _FakeOpenAiClient.last_instance = self


def _install_fake_openai(
    monkeypatch: pytest.MonkeyPatch, *, result: Any = None, error: BaseException | None = None
) -> None:
    def factory(**kwargs: Any) -> _FakeOpenAiClient:
        return _FakeOpenAiClient(result=result, error=error, **kwargs)

    monkeypatch.setattr(openai_sdk, "OpenAI", factory)


def _openai_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")


def _openai_response(status_code: int) -> httpx2.Response:
    return httpx2.Response(status_code, request=_openai_request())


# --- Dvojníci Gemini SDK -----------------------------------------------------


class _FakeGeminiUsage:
    def __init__(self, prompt: int, candidates: int, thoughts: int = 0) -> None:
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.thoughts_token_count = thoughts


class _FakeGeminiResponse:
    def __init__(self, text: str, model_version: str, usage: _FakeGeminiUsage | None) -> None:
        self.text = text
        self.model_version = model_version
        self.usage_metadata = usage


class _FakeGeminiModels:
    def __init__(self, result: Any, error: BaseException | None) -> None:
        self._result = result
        self._error = error
        self.call_kwargs: dict[str, Any] | None = None

    def generate_content(self, **kwargs: Any) -> Any:
        self.call_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._result


class _FakeGeminiClient:
    last_instance: "_FakeGeminiClient | None" = None

    def __init__(self, *, result: Any = None, error: BaseException | None = None, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.models = _FakeGeminiModels(result, error)
        _FakeGeminiClient.last_instance = self


def _install_fake_gemini(
    monkeypatch: pytest.MonkeyPatch, *, result: Any = None, error: BaseException | None = None
) -> None:
    def factory(**kwargs: Any) -> _FakeGeminiClient:
        return _FakeGeminiClient(result=result, error=error, **kwargs)

    monkeypatch.setattr(genai_sdk, "Client", factory)


@pytest.fixture
def openai_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.openai_api_key", "test-key")
    monkeypatch.setattr("app.config.settings.openai_model", "gpt-test")
    monkeypatch.setattr("app.config.settings.llm_timeout_seconds", 9)


@pytest.fixture
def gemini_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.gemini_api_key", "test-key")
    monkeypatch.setattr("app.config.settings.gemini_model", "gemini-test")
    monkeypatch.setattr("app.config.settings.llm_timeout_seconds", 9)


# --- OpenAI: úspěšná odpověď -------------------------------------------------


def test_openai_complete_vraci_adapter_result_s_tokeny(
    monkeypatch: pytest.MonkeyPatch, openai_settings: None
) -> None:
    _install_fake_openai(
        monkeypatch,
        result=_FakeChatCompletion(
            '{"klasifikace": "MALA", "zduvodneni": "test"}', "gpt-test-2026", 111, 22
        ),
    )

    result = OpenAiAdapter().complete("prompt", LlmPurpose.CLASSIFY)

    assert result.text == '{"klasifikace": "MALA", "zduvodneni": "test"}'
    assert result.tokens_in == 111
    assert result.tokens_out == 22
    assert result.provider == "openai"
    assert result.model == "gpt-test-2026"


def test_openai_posila_model_a_prompt_a_timeout(
    monkeypatch: pytest.MonkeyPatch, openai_settings: None
) -> None:
    _install_fake_openai(monkeypatch, result=_FakeChatCompletion("{}", "gpt-test", 1, 1))

    OpenAiAdapter().complete("muj prompt", LlmPurpose.DUPLICATES)

    client = _FakeOpenAiClient.last_instance
    assert client is not None
    assert client.init_kwargs["api_key"] == "test-key"
    assert client.init_kwargs["timeout"] == 9
    sent = client.completions.create_kwargs
    assert sent is not None
    assert sent["model"] == "gpt-test"
    assert sent["messages"] == [{"role": "user", "content": "muj prompt"}]


def test_openai_prazdny_obsah_zpravy_da_prazdny_text(
    monkeypatch: pytest.MonkeyPatch, openai_settings: None
) -> None:
    _install_fake_openai(monkeypatch, result=_FakeChatCompletion(None, "gpt-test", 5, 0))

    assert OpenAiAdapter().complete("p", LlmPurpose.CLASSIFY).text == ""


def test_openai_model_override_v_konstruktoru(
    monkeypatch: pytest.MonkeyPatch, openai_settings: None
) -> None:
    """Eval harness instancuje víc modelů téhož providera — override musí přebít settings."""
    _install_fake_openai(monkeypatch, result=_FakeChatCompletion("{}", "jiny-model", 1, 1))

    adapter = OpenAiAdapter(model="jiny-model")
    adapter.complete("p", LlmPurpose.CLASSIFY)

    assert adapter.model == "jiny-model"
    client = _FakeOpenAiClient.last_instance
    assert client is not None
    assert client.completions.create_kwargs is not None
    assert client.completions.create_kwargs["model"] == "jiny-model"


# --- OpenAI: mapování výjimek ------------------------------------------------


@pytest.mark.parametrize(
    ("error", "ocekavana"),
    [
        (openai_sdk.APITimeoutError(request=_openai_request()), LlmTimeout),
        (
            openai_sdk.RateLimitError("rl", response=_openai_response(429), body=None),
            LlmRateLimited,
        ),
        (
            openai_sdk.InternalServerError(
                "boom", response=_openai_response(500), body=None
            ),
            LlmServerError,
        ),
        (
            openai_sdk.APIConnectionError(message="conn", request=_openai_request()),
            LlmTransportError,
        ),
    ],
    ids=["timeout", "rate_limit", "server_5xx", "transport"],
)
def test_openai_mapovani_vyjimek(
    monkeypatch: pytest.MonkeyPatch,
    openai_settings: None,
    error: BaseException,
    ocekavana: type[Exception],
) -> None:
    _install_fake_openai(monkeypatch, error=error)

    with pytest.raises(ocekavana):
        OpenAiAdapter().complete("prompt", LlmPurpose.CLASSIFY)


def test_openai_4xx_mimo_429_propadne_nezmapovane(
    monkeypatch: pytest.MonkeyPatch, openai_settings: None
) -> None:
    """Nepřechodná chyba — gateway ji vezme jako ADAPTER_ERROR bez retry."""
    _install_fake_openai(
        monkeypatch,
        error=openai_sdk.BadRequestError("bad", response=_openai_response(400), body=None),
    )

    with pytest.raises(openai_sdk.BadRequestError):
        OpenAiAdapter().complete("prompt", LlmPurpose.CLASSIFY)


# --- Gemini: úspěšná odpověď -------------------------------------------------


def test_gemini_complete_vraci_adapter_result_s_tokeny(
    monkeypatch: pytest.MonkeyPatch, gemini_settings: None
) -> None:
    _install_fake_gemini(
        monkeypatch,
        result=_FakeGeminiResponse(
            '{"klasifikace": "STREDNI", "zduvodneni": "test"}',
            "gemini-test-001",
            _FakeGeminiUsage(prompt=200, candidates=30, thoughts=12),
        ),
    )

    result = GeminiAdapter().complete("prompt", LlmPurpose.CLASSIFY)

    assert result.text == '{"klasifikace": "STREDNI", "zduvodneni": "test"}'
    assert result.tokens_in == 200
    # Reasoning tokeny se účtují jako výstup — musí být v tokens_out.
    assert result.tokens_out == 42
    assert result.provider == "gemini"
    assert result.model == "gemini-test-001"


def test_gemini_timeout_se_prepocita_na_milisekundy(
    monkeypatch: pytest.MonkeyPatch, gemini_settings: None
) -> None:
    _install_fake_gemini(
        monkeypatch,
        result=_FakeGeminiResponse("{}", "gemini-test", _FakeGeminiUsage(1, 1)),
    )

    GeminiAdapter()

    client = _FakeGeminiClient.last_instance
    assert client is not None
    assert client.init_kwargs["api_key"] == "test-key"
    assert client.init_kwargs["http_options"].timeout == 9000


def test_gemini_chybejici_usage_metadata_da_nuly(
    monkeypatch: pytest.MonkeyPatch, gemini_settings: None
) -> None:
    _install_fake_gemini(
        monkeypatch, result=_FakeGeminiResponse("{}", "gemini-test", None)
    )

    result = GeminiAdapter().complete("p", LlmPurpose.CLASSIFY)

    assert (result.tokens_in, result.tokens_out) == (0, 0)


def test_gemini_model_override_v_konstruktoru(
    monkeypatch: pytest.MonkeyPatch, gemini_settings: None
) -> None:
    _install_fake_gemini(
        monkeypatch,
        result=_FakeGeminiResponse("{}", "gemini-flash", _FakeGeminiUsage(1, 1)),
    )

    adapter = GeminiAdapter(model="gemini-flash")
    adapter.complete("p", LlmPurpose.CLASSIFY)

    assert adapter.model == "gemini-flash"
    client = _FakeGeminiClient.last_instance
    assert client is not None
    assert client.models.call_kwargs is not None
    assert client.models.call_kwargs["model"] == "gemini-flash"


# --- Gemini: mapování výjimek ------------------------------------------------


@pytest.mark.parametrize(
    ("error", "ocekavana"),
    [
        (genai_errors.ServerError(500, {"error": {"message": "boom"}}), LlmServerError),
        (genai_errors.ClientError(429, {"error": {"message": "rl"}}), LlmRateLimited),
        (httpx.ReadTimeout("timeout"), LlmTimeout),
        (httpx.ConnectError("conn"), LlmTransportError),
    ],
    ids=["server_5xx", "rate_limit", "timeout", "transport"],
)
def test_gemini_mapovani_vyjimek(
    monkeypatch: pytest.MonkeyPatch,
    gemini_settings: None,
    error: BaseException,
    ocekavana: type[Exception],
) -> None:
    _install_fake_gemini(monkeypatch, error=error)

    with pytest.raises(ocekavana):
        GeminiAdapter().complete("prompt", LlmPurpose.CLASSIFY)


def test_gemini_4xx_mimo_429_propadne_nezmapovane(
    monkeypatch: pytest.MonkeyPatch, gemini_settings: None
) -> None:
    _install_fake_gemini(
        monkeypatch, error=genai_errors.ClientError(400, {"error": {"message": "bad"}})
    )

    with pytest.raises(genai_errors.ClientError):
        GeminiAdapter().complete("prompt", LlmPurpose.CLASSIFY)


# --- get_adapter(): fail-fast bez klíče --------------------------------------


def test_get_adapter_openai_bez_klice_selze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.llm_provider", "openai")
    monkeypatch.setattr("app.config.settings.openai_api_key", None)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        gateway.get_adapter()


def test_get_adapter_gemini_bez_klice_selze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.llm_provider", "gemini")
    monkeypatch.setattr("app.config.settings.gemini_api_key", None)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        gateway.get_adapter()


def test_get_adapter_openai_s_klicem_vraci_openai_adapter(
    monkeypatch: pytest.MonkeyPatch, openai_settings: None
) -> None:
    monkeypatch.setattr("app.config.settings.llm_provider", "openai")
    _install_fake_openai(monkeypatch, result=_FakeChatCompletion("{}", "gpt-test", 1, 1))

    assert isinstance(gateway.get_adapter(), OpenAiAdapter)


def test_get_adapter_gemini_s_klicem_vraci_gemini_adapter(
    monkeypatch: pytest.MonkeyPatch, gemini_settings: None
) -> None:
    monkeypatch.setattr("app.config.settings.llm_provider", "gemini")
    _install_fake_gemini(
        monkeypatch, result=_FakeGeminiResponse("{}", "gemini-test", None)
    )

    assert isinstance(gateway.get_adapter(), GeminiAdapter)


def test_get_adapter_neznamy_provider_hlasi_povolene_hodnoty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.config.settings.llm_provider", "nesmysl")

    with pytest.raises(ValueError, match="openai, gemini"):
        gateway.get_adapter()
