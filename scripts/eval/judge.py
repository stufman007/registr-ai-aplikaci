"""LLM judge — hodnotí kvalitu českého zdůvodnění (rubrika 4 kritérií, 1–5).

Cross-judge: výstup modelu z rodiny X hodnotí porotci **všech ostatních** rodin
(`models.JUDGE_ALIASES`), nikdy porotce téže rodiny. Výsledné skóre případu je
průměr přes porotce a kritéria. Smyslem je vyhnout se známé zaujatosti modelů
vůči vlastnímu stylu psaní.

Judge se volá **přes adapter přímo, ne přes gateway**: hodnotí už hotový,
pseudonymizovaný výstup produkčního toku, takže druhá pseudonymizace by nic
nepřidala a jen zkreslila text. Stejná invarianta jako všude jinde platí dál —
obsah promptu ani odpovědi se nikam neloguje.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.schemas import DotaznikOdpovedi, LlmPurpose
from scripts.eval.models import MODELS, ModelSpec, build_adapter

JUDGE_PROMPT_PATH = Path(__file__).resolve().parent / "judge_prompt.md"

#: Kritéria rubriky v pořadí, v jakém je čeká judge prompt i report.
CRITERIA: tuple[str, ...] = (
    "cestina",
    "cituje_odpovedi",
    "bez_vymyslenych_faktu",
    "vysvetluje_tier",
)

MIN_SCORE = 1
MAX_SCORE = 5

_PROMPT_VERSION_RE = re.compile(r"^\s*PROMPT_VERSION:\s*(?P<version>\S+)\s*$", re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True)
class JudgeTask:
    """Jeden hodnocený výstup. Nezná typy harnessu — vazba je jen přes klíč."""

    key: tuple[str, str, int]  # (model_alias, case_id, run_index)
    model_alias: str
    answers: DotaznikOdpovedi
    tier: str
    zduvodneni: str


@dataclass(frozen=True)
class JudgeVerdict:
    """Výsledek hodnocení jednoho výstupu celou porotou."""

    key: tuple[str, str, int]
    #: judge_alias → skóre po kritériích (jen porotci, kteří vrátili platný JSON).
    per_judge: dict[str, dict[str, int]]
    #: Průměr přes všechny porotce a kritéria; `None` = nikdo neodpověděl platně.
    prumer: float | None
    #: Počet porotců, jejichž odpověď se nepodařilo zpracovat.
    selhani: int


@lru_cache(maxsize=1)
def load_judge_prompt() -> tuple[str, str]:
    """Vrátí `(PROMPT_VERSION, tělo bez hlavičky a interních komentářů)`."""
    raw = JUDGE_PROMPT_PATH.read_text(encoding="utf-8")

    match = _PROMPT_VERSION_RE.search(raw)
    if match is None:
        raise ValueError("judge_prompt.md nemá hlavičku PROMPT_VERSION")

    body = _PROMPT_VERSION_RE.sub("", raw, count=1)
    body = _HTML_COMMENT_RE.sub("", body)
    return match.group("version"), body.strip()


def format_answers(answers: DotaznikOdpovedi) -> str:
    """Odpovědi dotazníku v čitelné podobě pro porotce (stejná pole jako v gateway)."""
    radky = [f"- Účel použití: {answers.ucel}"]
    polozky = (
        ("Počet uživatelů", answers.pocet_uzivatelu),
        ("Kritičnost pro provoz", answers.kritictnost),
        ("Osobní údaje", answers.osobni_udaje),
        ("Rozhodování o lidech", answers.rozhodovani),
        ("Viditelnost výstupu pro klienta", answers.viditelnost),
        ("Citlivost dat", answers.citlivost),
        ("Autonomie jednání", answers.autonomie),
        ("Dopad chyby", answers.dopad),
    )
    radky.extend(f"- {popisek}: {hodnota.label()}" for popisek, hodnota in polozky)
    return "\n".join(radky)


def judges_for(model_alias: str, judge_aliases: tuple[str, ...]) -> tuple[ModelSpec, ...]:
    """Porotci pro daný model — všechny rodiny kromě jeho vlastní."""
    hodnocena_rodina = MODELS[model_alias].family
    return tuple(
        MODELS[alias]
        for alias in judge_aliases
        if MODELS[alias].family != hodnocena_rodina
    )


def build_prompt(task: JudgeTask) -> str:
    _, template = load_judge_prompt()
    return (
        template.replace("{{ODPOVEDI}}", format_answers(task.answers))
        .replace("{{TIER}}", task.tier)
        .replace("{{ZDUVODNENI}}", task.zduvodneni)
    )


def run_judges(
    tasks: list[JudgeTask],
    judge_aliases: tuple[str, ...],
    *,
    dry_run: bool,
) -> dict[tuple[str, str, int], JudgeVerdict]:
    """Projede všechny úlohy porotou a vrátí verdikty podle klíče úlohy.

    Adaptery porotců se vytvářejí jednou na běh (jedno spojení na porotce).
    Selhání jednoho porotce nesmí shodit celý eval — zaznamená se a jede se dál.
    """
    adaptery = {
        alias: build_adapter(MODELS[alias], dry_run=dry_run) for alias in judge_aliases
    }

    verdikty: dict[tuple[str, str, int], JudgeVerdict] = {}
    for task in tasks:
        per_judge: dict[str, dict[str, int]] = {}
        selhani = 0

        for judge in judges_for(task.model_alias, judge_aliases):
            skore = _judge_once(adaptery[judge.alias], task)
            if skore is None:
                selhani += 1
            else:
                per_judge[judge.alias] = skore

        vsechna = [
            hodnota for skore in per_judge.values() for hodnota in skore.values()
        ]
        verdikty[task.key] = JudgeVerdict(
            key=task.key,
            per_judge=per_judge,
            prumer=(sum(vsechna) / len(vsechna)) if vsechna else None,
            selhani=selhani,
        )

    return verdikty


def _judge_once(adapter: Any, task: JudgeTask) -> dict[str, int] | None:
    """Jedno volání porotce. `None` = nedostupné nebo nevalidní skóre."""
    try:
        result = adapter.complete(build_prompt(task), LlmPurpose.CLASSIFY)
    except Exception:
        # Selhání porotce (timeout, rate limit, cokoli) nesmí shodit celý eval.
        # Obsah chyby se nikam neloguje, případ se jen započítá jako neohodnocený.
        return None
    return parse_scores(result.text)


def parse_scores(text: str) -> dict[str, int] | None:
    """Vytáhne z odpovědi porotce blok `skore`. `None` = nepoužitelná odpověď."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None

    try:
        payload = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None

    skore = payload.get("skore")
    if not isinstance(skore, dict):
        return None

    vysledek: dict[str, int] = {}
    for kriterium in CRITERIA:
        hodnota = skore.get(kriterium)
        if not isinstance(hodnota, int) or isinstance(hodnota, bool):
            return None
        if not MIN_SCORE <= hodnota <= MAX_SCORE:
            return None
        vysledek[kriterium] = hodnota
    return vysledek
