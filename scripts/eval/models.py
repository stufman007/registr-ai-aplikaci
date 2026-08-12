"""Registr hodnocených modelů a ceník pro eval.

Alias je stabilní klíč (`--models`, `pricing.json`, sloupce reportu); `model_id`
je konkrétní ID u providera, které se může měnit. Když se ID změní, mění se
jeden řádek tady a nikde jinde.

POZOR: ID modelů OpenAI a Gemini jsou nejlepší známý odhad. Před ostrým během
je nutné je ověřit proti `models.list` daného SDK (postup v README.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.llm.anthropic import AnthropicAdapter
from app.llm.base import LlmAdapter
from app.llm.gemini_adapter import GeminiAdapter
from app.llm.mock import MockAdapter
from app.llm.openai_adapter import OpenAiAdapter

EVAL_DIR = Path(__file__).resolve().parent
PRICING_PATH = EVAL_DIR / "pricing.json"

#: Alias použitý pro sazby v dry-run režimu (mock nic nestojí).
DRY_RUN_PRICING_ALIAS = "mock"


@dataclass(frozen=True)
class ModelSpec:
    """Jeden hodnocený model."""

    alias: str
    #: Rodina modelů — cross-judge nikdy nenechá rodinu hodnotit sebe samu.
    family: str
    #: Provider pro volbu adapteru: anthropic | openai | gemini.
    provider: str
    model_id: str
    poznamka: str


MODELS: dict[str, ModelSpec] = {
    "claude-sonnet-5": ModelSpec(
        alias="claude-sonnet-5",
        family="claude",
        provider="anthropic",
        model_id="claude-sonnet-5",
        poznamka="Vlajkový Claude — referenční kvalita.",
    ),
    "claude-haiku-4-5": ModelSpec(
        alias="claude-haiku-4-5",
        family="claude",
        provider="anthropic",
        model_id="claude-haiku-4-5-20251001",
        poznamka="Levná varianta Claude — hlavní kandidát na sweetspot.",
    ),
    "gpt-flagship": ModelSpec(
        alias="gpt-flagship",
        family="openai",
        provider="openai",
        model_id="gpt-5.1",  # ověřit při běhu přes client.models.list()
        poznamka="Vlajkový model OpenAI. ID ověřit při ostrém běhu.",
    ),
    "gpt-mini": ModelSpec(
        alias="gpt-mini",
        family="openai",
        provider="openai",
        model_id="gpt-5.1-mini",  # ověřit při běhu přes client.models.list()
        poznamka="Levná varianta OpenAI. ID ověřit při ostrém běhu.",
    ),
    "gemini-3-pro": ModelSpec(
        alias="gemini-3-pro",
        family="gemini",
        provider="gemini",
        model_id="gemini-3-pro-preview",  # ověřit při běhu přes client.models.list()
        poznamka="Vlajkový Gemini. ID ověřit při ostrém běhu.",
    ),
    "gemini-3-flash": ModelSpec(
        alias="gemini-3-flash",
        family="gemini",
        provider="gemini",
        model_id="gemini-3-flash-preview",  # ověřit při běhu přes client.models.list()
        poznamka="Levná varianta Gemini. ID ověřit při ostrém běhu.",
    ),
}

#: Výchozí sada pro `--models` (všech šest).
DEFAULT_ALIASES: tuple[str, ...] = tuple(MODELS)

#: Porota pro cross-judge — jeden zástupce každé rodiny.
JUDGE_ALIASES: tuple[str, ...] = ("claude-sonnet-5", "gpt-flagship", "gemini-3-pro")


def resolve_aliases(raw: str | None) -> tuple[ModelSpec, ...]:
    """Přeloží `--models` na specifikace. Prázdné = všech šest."""
    if raw is None or not raw.strip():
        aliases = DEFAULT_ALIASES
    else:
        aliases = tuple(part.strip() for part in raw.split(",") if part.strip())

    nezname = [alias for alias in aliases if alias not in MODELS]
    if nezname:
        raise ValueError(
            f"Neznámé aliasy modelů: {', '.join(nezname)}. "
            f"Povolené: {', '.join(MODELS)}"
        )
    return tuple(MODELS[alias] for alias in aliases)


def build_adapter(spec: ModelSpec, *, dry_run: bool) -> LlmAdapter:
    """Adapter pro daný model. V dry-run režimu vždy `MockAdapter` — bez klíčů,
    bez sítě, bez nákladů; produkční tok gateway zůstává stejný."""
    if dry_run:
        return MockAdapter()

    if spec.provider == "anthropic":
        return AnthropicAdapter(model=spec.model_id)
    if spec.provider == "openai":
        return OpenAiAdapter(model=spec.model_id)
    if spec.provider == "gemini":
        return GeminiAdapter(model=spec.model_id)

    raise ValueError(f"Neznámý provider {spec.provider!r} pro alias {spec.alias!r}")


@lru_cache(maxsize=1)
def load_pricing() -> dict[str, Any]:
    """Načte `pricing.json`. Ceny NEJSOU ověřené — viz pole `verified`."""
    return json.loads(PRICING_PATH.read_text(encoding="utf-8"))


def price_per_1000_calls(
    alias: str, avg_tokens_in: float, avg_tokens_out: float, *, dry_run: bool
) -> float | None:
    """Cena 1000 volání v USD podle průměrné spotřeby tokenů.

    `None` = pro alias není v ceníku sazba (report to napíše místo čísla).
    """
    klic = DRY_RUN_PRICING_ALIAS if dry_run else alias
    sazby = load_pricing()["modely"].get(klic)
    if sazby is None:
        return None

    za_volani = (
        avg_tokens_in / 1_000_000 * sazby["input_per_mtok_usd"]
        + avg_tokens_out / 1_000_000 * sazby["output_per_mtok_usd"]
    )
    return za_volani * 1000


def pricing_is_verified() -> bool:
    """True, až někdo ceny ručně ověří a přepne `verified` v `pricing.json`."""
    return bool(load_pricing().get("verified", False))
