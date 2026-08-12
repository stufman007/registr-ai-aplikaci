"""Eval harness — srovnání LLM modelů nad zlatým datasetem registru.

Spuštění z kořene repa:

    .venv/Scripts/python -m scripts.eval.run_eval --dry-run --runs 1 --skip-judge
    .venv/Scripts/python -m scripts.eval.run_eval --models claude-haiku-4-5,gpt-mini

Klíčové rozhodnutí: eval **nevolá adaptery přímo**, ale jde přes
`app.llm.gateway.classify` / `find_duplicates` s injektovaným adapterem. Měří se
tedy identický produkční tok — stejný prompt, stejná pseudonymizace, stejná
Pydantic validace i stejný fallback. Kdyby eval stavěl vlastní prompt, měřil by
něco jiného, než co poběží v provozu.

Audit volání se sbírá do **in-memory SQLite**, aby se produkční `registr.db`
evalem nezaplnila.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app import models as app_models  # noqa: F401 — registruje tabulky do Base.metadata
from app.db import Base
from app.llm import gateway
from app.llm.base import LlmAdapter
from app.models import LlmAudit
from app.services.similarity import top_candidates
from scripts.eval import judge as judge_module
from scripts.eval.dataset import (
    CLASSIFICATION_CASES,
    DATASET_VERSION,
    DUPLICATE_CASES,
    ClassificationCase,
    DuplicateCase,
)
from scripts.eval.models import (
    JUDGE_ALIASES,
    ModelSpec,
    build_adapter,
    load_pricing,
    price_per_1000_calls,
    pricing_is_verified,
    resolve_aliases,
)

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "results"

#: Práh přesnosti pro doporučení sweetspotu (exact match).
DEFAULT_ACCURACY_THRESHOLD = 0.90

DEFAULT_RUNS = 2


# --- Záznamy měření ---------------------------------------------------------


@dataclass(frozen=True)
class ClassifyRecord:
    """Jedno volání klasifikace: model × případ × běh."""

    model_alias: str
    case_id: str
    run_index: int
    navrzeny_tier: str | None
    expected_tier: str
    acceptable_tiers: list[str]
    exact: bool
    acceptable: bool
    fallback_used: bool
    latence_ms: int
    tokens_in: int
    tokens_out: int
    mentions_hit: list[str]
    mentions_missed: list[str]
    zduvodneni: str


@dataclass(frozen=True)
class DuplicateRecord:
    """Jedno volání kontroly duplicit: model × případ × běh."""

    model_alias: str
    dup_case_id: str
    run_index: int
    predicted_ids: list[str]
    expected_ids: list[str]
    true_positives: int
    false_positives: int
    false_negatives: int
    fallback_used: bool
    latence_ms: int
    tokens_in: int
    tokens_out: int


@dataclass
class ModelSummary:
    """Agregace přes všechny běhy jednoho modelu."""

    alias: str
    model_id: str
    klasifikaci: int = 0
    exact: int = 0
    acceptable: int = 0
    fallback: int = 0
    latence: list[int] = field(default_factory=list)
    tokens_in: list[int] = field(default_factory=list)
    tokens_out: list[int] = field(default_factory=list)
    konceptu_ocekavano: int = 0
    konceptu_zmineno: int = 0
    dup_volani: int = 0
    dup_fallback: int = 0
    dup_tp: int = 0
    dup_fp: int = 0
    dup_fn: int = 0
    dup_latence: list[int] = field(default_factory=list)
    judge_skore: list[float] = field(default_factory=list)
    judge_neohodnoceno: int = 0


# --- Pomocné výpočty --------------------------------------------------------


def _bez_diakritiky(text: str) -> str:
    rozlozeno = unicodedata.normalize("NFD", text.lower())
    return "".join(z for z in rozlozeno if unicodedata.category(z) != "Mn")


def _rozdel_koncepty(
    zduvodneni: str, koncepty: Sequence[str]
) -> tuple[list[str], list[str]]:
    """Rozdělí očekávané koncepty na zmíněné a chybějící (bez ohledu na diakritiku)."""
    text = _bez_diakritiky(zduvodneni)
    hit = [k for k in koncepty if _bez_diakritiky(k) in text]
    missed = [k for k in koncepty if _bez_diakritiky(k) not in text]
    return hit, missed


def _podil(cast: int, celek: int) -> float | None:
    return cast / celek if celek else None


def _procenta(hodnota: float | None) -> str:
    return "—" if hodnota is None else f"{hodnota * 100:.1f} %"


def _cislo(hodnota: float | None, desetiny: int = 1) -> str:
    return "—" if hodnota is None else f"{hodnota:.{desetiny}f}"


def _median(hodnoty: list[int]) -> float | None:
    return statistics.median(hodnoty) if hodnoty else None


def _prumer(hodnoty: list[int]) -> float | None:
    return sum(hodnoty) / len(hodnoty) if hodnoty else None


# --- In-memory audit --------------------------------------------------------


def _make_session_factory() -> sessionmaker[Session]:
    """Sezení nad in-memory SQLite — audit evalu se nikdy nedotkne produkční DB."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _drain_audit(session: Session) -> LlmAudit | None:
    """Vybere poslední audit řádek a tabulku vyprázdní (další volání začne čistě)."""
    rows = list(session.scalars(select(LlmAudit)))
    session.execute(delete(LlmAudit))
    session.commit()
    return rows[-1] if rows else None


# --- Běh jednoho modelu -----------------------------------------------------


def _run_classification(
    adapter: LlmAdapter,
    spec: ModelSpec,
    session: Session,
    cases: Sequence[ClassificationCase],
    runs: int,
) -> list[ClassifyRecord]:
    zaznamy: list[ClassifyRecord] = []

    for case in cases:
        for run_index in range(1, runs + 1):
            outcome = gateway.classify(
                session,
                case.answers,
                list(case.components),
                adapter=adapter,
            )
            audit = _drain_audit(session)

            navrzeny = None if outcome.fallback_used else outcome.llm_tier
            hit, missed = _rozdel_koncepty(
                outcome.zduvodneni if not outcome.fallback_used else "",
                case.rationale_must_mention,
            )
            zaznamy.append(
                ClassifyRecord(
                    model_alias=spec.alias,
                    case_id=case.case_id,
                    run_index=run_index,
                    navrzeny_tier=navrzeny.name if navrzeny else None,
                    expected_tier=case.expected_tier.name,
                    acceptable_tiers=sorted(t.name for t in case.acceptable_tiers),
                    exact=navrzeny == case.expected_tier,
                    acceptable=navrzeny in case.acceptable_tiers,
                    fallback_used=outcome.fallback_used,
                    latence_ms=audit.latence_ms if audit else 0,
                    tokens_in=audit.tokens_in if audit else 0,
                    tokens_out=audit.tokens_out if audit else 0,
                    mentions_hit=hit,
                    mentions_missed=missed,
                    zduvodneni=outcome.zduvodneni,
                )
            )
    return zaznamy


def _run_duplicates(
    adapter: LlmAdapter,
    spec: ModelSpec,
    session: Session,
    cases: Sequence[DuplicateCase],
    runs: int,
) -> list[DuplicateRecord]:
    zaznamy: list[DuplicateRecord] = []

    for case in cases:
        # Stejný lokální předvýběr jako v produkční routě (spec kap. 6).
        kandidati = top_candidates(
            case.nazev,
            case.popis,
            [(r.record_id, r.nazev, r.popis) for r in case.existing],
        )

        for run_index in range(1, runs + 1):
            outcome = gateway.find_duplicates(
                session, case.nazev, case.popis, kandidati, adapter=adapter
            )
            audit = _drain_audit(session)

            predicted = {m.record_id for m in outcome.matches}
            expected = set(case.expected_match_ids)
            zaznamy.append(
                DuplicateRecord(
                    model_alias=spec.alias,
                    dup_case_id=case.dup_case_id,
                    run_index=run_index,
                    predicted_ids=sorted(predicted),
                    expected_ids=sorted(expected),
                    true_positives=len(predicted & expected),
                    false_positives=len(predicted - expected),
                    false_negatives=len(expected - predicted),
                    fallback_used=outcome.fallback_used,
                    latence_ms=audit.latence_ms if audit else 0,
                    tokens_in=audit.tokens_in if audit else 0,
                    tokens_out=audit.tokens_out if audit else 0,
                )
            )
    return zaznamy


# --- Agregace ---------------------------------------------------------------


def _summarize(
    specs: Sequence[ModelSpec],
    classify_records: Sequence[ClassifyRecord],
    duplicate_records: Sequence[DuplicateRecord],
    verdikty: dict[tuple[str, str, int], judge_module.JudgeVerdict],
) -> dict[str, ModelSummary]:
    souhrny = {s.alias: ModelSummary(alias=s.alias, model_id=s.model_id) for s in specs}

    for rec in classify_records:
        s = souhrny[rec.model_alias]
        s.klasifikaci += 1
        s.exact += int(rec.exact)
        s.acceptable += int(rec.acceptable)
        s.fallback += int(rec.fallback_used)
        s.latence.append(rec.latence_ms)
        s.tokens_in.append(rec.tokens_in)
        s.tokens_out.append(rec.tokens_out)
        s.konceptu_ocekavano += len(rec.mentions_hit) + len(rec.mentions_missed)
        s.konceptu_zmineno += len(rec.mentions_hit)

        verdikt = verdikty.get((rec.model_alias, rec.case_id, rec.run_index))
        if verdikt is None or verdikt.prumer is None:
            if verdikty:
                s.judge_neohodnoceno += 1
        else:
            s.judge_skore.append(verdikt.prumer)

    for rec in duplicate_records:
        s = souhrny[rec.model_alias]
        s.dup_volani += 1
        s.dup_latence.append(rec.latence_ms)
        if rec.fallback_used:
            s.dup_fallback += 1
            continue  # Fallback = lokální heuristika, ne odpověď modelu.
        s.dup_tp += rec.true_positives
        s.dup_fp += rec.false_positives
        s.dup_fn += rec.false_negatives

    return souhrny


def _precision_recall(s: ModelSummary) -> tuple[float | None, float | None]:
    precision = _podil(s.dup_tp, s.dup_tp + s.dup_fp)
    recall = _podil(s.dup_tp, s.dup_tp + s.dup_fn)
    return precision, recall


# --- Report -----------------------------------------------------------------


def _render_report(
    souhrny: dict[str, ModelSummary],
    *,
    dry_run: bool,
    runs: int,
    pocet_cases: int,
    pocet_dup: int,
    skip_judge: bool,
    threshold: float,
    kdy: datetime,
) -> str:
    radky: list[str] = []
    radky.append("# Eval report — Registr interních AI aplikací")
    radky.append("")
    radky.append(f"- **Datum běhu:** {kdy.strftime('%Y-%m-%d %H:%M:%S')}")
    radky.append(
        f"- **Režim:** {'DRY-RUN (MockAdapter, bez sítě a bez nákladů)' if dry_run else 'OSTRÝ (reálná API volání)'}"
    )
    radky.append(f"- **Dataset:** {pocet_cases} klasifikačních případů, {pocet_dup} případů duplicit (verze {DATASET_VERSION})")
    radky.append(f"- **Běhů na případ:** {runs}")
    radky.append(f"- **Judge:** {'přeskočen (--skip-judge)' if skip_judge else 'cross-judge, ' + ', '.join(JUDGE_ALIASES)}")
    radky.append(
        f"- **Ceník:** `pricing.json`, měna {load_pricing()['mena']}, "
        f"verified = `{str(pricing_is_verified()).lower()}`"
    )
    if not pricing_is_verified():
        radky.append("")
        radky.append(
            "> **Ceny nejsou ověřené.** Sazby v `pricing.json` jsou odhad; "
            "před rozhodnutím o modelu je nutné je zkontrolovat proti aktuálnímu "
            "ceníku providera a přepnout `verified` na `true`."
        )
    if dry_run:
        radky.append("")
        radky.append(
            "> **Dry-run běh.** Všechny modely obsluhoval deterministický "
            "`MockAdapter` — čísla ověřují funkčnost harnessu, ne kvalitu modelů."
        )

    radky.append("")
    radky.append("## Klasifikace")
    radky.append("")
    radky.append(
        "| Model | Model ID | Přesnost exact | Přesnost acceptable | JSON validita | "
        "Medián latence (ms) | Tokeny in (prům.) | Tokeny out (prům.) | "
        "Cena / 1000 klasifikací (USD) | Zmínka konceptů | Judge (1–5) |"
    )
    radky.append("|---|---|---|---|---|---|---|---|---|---|---|")

    for s in souhrny.values():
        avg_in = _prumer(s.tokens_in) or 0.0
        avg_out = _prumer(s.tokens_out) or 0.0
        cena = price_per_1000_calls(s.alias, avg_in, avg_out, dry_run=dry_run)
        judge_prumer = (
            sum(s.judge_skore) / len(s.judge_skore) if s.judge_skore else None
        )
        radky.append(
            "| {alias} | `{mid}` | {exact} | {acc} | {json_ok} | {lat} | {tin} | {tout} | {cena} | {kon} | {judge} |".format(
                alias=s.alias,
                mid=s.model_id,
                exact=_procenta(_podil(s.exact, s.klasifikaci)),
                acc=_procenta(_podil(s.acceptable, s.klasifikaci)),
                json_ok=_procenta(_podil(s.klasifikaci - s.fallback, s.klasifikaci)),
                lat=_cislo(_median(s.latence), 0),
                tin=_cislo(_prumer(s.tokens_in), 0),
                tout=_cislo(_prumer(s.tokens_out), 0),
                cena="—" if cena is None else f"{cena:.2f}",
                kon=_procenta(_podil(s.konceptu_zmineno, s.konceptu_ocekavano)),
                judge="—" if judge_prumer is None else f"{judge_prumer:.2f}",
            )
        )

    radky.append("")
    radky.append("## Kontrola duplicit")
    radky.append("")
    radky.append(
        "| Model | Precision | Recall | F1 | JSON validita | Medián latence (ms) |"
    )
    radky.append("|---|---|---|---|---|---|")
    for s in souhrny.values():
        precision, recall = _precision_recall(s)
        if precision and recall:
            f1: float | None = 2 * precision * recall / (precision + recall)
        else:
            f1 = None
        radky.append(
            "| {alias} | {p} | {r} | {f1} | {json_ok} | {lat} |".format(
                alias=s.alias,
                p=_procenta(precision),
                r=_procenta(recall),
                f1=_procenta(f1),
                json_ok=_procenta(
                    _podil(s.dup_volani - s.dup_fallback, s.dup_volani)
                ),
                lat=_cislo(_median(s.dup_latence), 0),
            )
        )

    radky.append("")
    radky.extend(_render_sweetspot(souhrny, dry_run=dry_run, threshold=threshold))

    radky.append("")
    radky.append("## Jak číst tabulky")
    radky.append("")
    radky.append(
        "- **Přesnost exact** — shoda s `expected_tier` zlatého datasetu. "
        "Hlavní metrika kvality."
    )
    radky.append(
        "- **Přesnost acceptable** — návrh spadl do `acceptable_tiers` "
        "(tolerance u hraničních případů)."
    )
    radky.append(
        "- **JSON validita** — podíl volání, kde odpověď prošla Pydantic validací "
        "gateway. Zbytek skončil ve fallbacku (tier určila jen pravidla)."
    )
    radky.append(
        "- **Zmínka konceptů** — podíl klíčových konceptů z datasetu, které se ve "
        "zdůvodnění skutečně objevily. Nízká hodnota = obecné, nekonkrétní texty."
    )
    radky.append(
        "- **Judge** — průměr cross-judge rubriky (1–5): čeština, citace odpovědí, "
        "absence vymyšlených faktů, vysvětlení tieru. Modely nehodnotí vlastní rodinu."
    )
    radky.append(
        "- **Duplicity** — precision/recall proti `expected_match_ids`; volání, která "
        "spadla do fallbacku, se do P/R nepočítají (odpověděla lokální heuristika, ne model)."
    )
    return "\n".join(radky) + "\n"


def _render_sweetspot(
    souhrny: dict[str, ModelSummary], *, dry_run: bool, threshold: float
) -> list[str]:
    """Doporučení generované z naměřených dat, ne z názoru."""
    radky = ["## Sweetspot — doporučení", ""]

    kandidati: list[tuple[str, float, float]] = []  # (alias, exact, cena)
    for s in souhrny.values():
        exact = _podil(s.exact, s.klasifikaci)
        cena = price_per_1000_calls(
            s.alias, _prumer(s.tokens_in) or 0.0, _prumer(s.tokens_out) or 0.0, dry_run=dry_run
        )
        if exact is None or cena is None:
            continue
        kandidati.append((s.alias, exact, cena))

    if not kandidati:
        radky.append("Nedostatek dat pro doporučení (chybí měření nebo ceník).")
        return radky

    radky.append(f"Práh přesnosti pro provoz: **exact ≥ {threshold * 100:.0f} %**.")
    radky.append("")

    vyhovujici = [k for k in kandidati if k[1] >= threshold]
    if vyhovujici:
        alias, exact, cena = min(vyhovujici, key=lambda k: k[2])
        radky.append(
            f"**Doporučení: `{alias}`** — nejlevnější model nad prahem "
            f"(exact {exact * 100:.1f} %, {cena:.2f} USD / 1000 klasifikací)."
        )
        nejlepsi = max(kandidati, key=lambda k: k[1])
        if nejlepsi[0] != alias:
            rozdil_ceny = nejlepsi[2] - cena
            radky.append("")
            radky.append(
                f"Nejpřesnější model je `{nejlepsi[0]}` ({nejlepsi[1] * 100:.1f} %), "
                f"ale stojí o {rozdil_ceny:.2f} USD / 1000 klasifikací víc. "
                "Rozdíl v přesnosti posoudit proti tomu, že policy minimum tier "
                "stejně nikdy nesnížíme pod deterministická pravidla."
            )
    else:
        nejlepsi = max(kandidati, key=lambda k: k[1])
        radky.append(
            f"**Žádný model práh nesplnil.** Nejblíž je `{nejlepsi[0]}` "
            f"(exact {nejlepsi[1] * 100:.1f} %, {nejlepsi[2]:.2f} USD / 1000 klasifikací)."
        )
        radky.append("")
        radky.append(
            "Postup: buď snížit práh (klasifikaci jistí policy minimum a lidské "
            "potvrzení), nebo upravit prompt a eval zopakovat."
        )

    radky.append("")
    radky.append("Pořadí podle ceny (vzestupně):")
    radky.append("")
    for alias, exact, cena in sorted(kandidati, key=lambda k: k[2]):
        radky.append(f"- `{alias}` — {cena:.2f} USD / 1000 klasifikací, exact {exact * 100:.1f} %")
    return radky


# --- Orchestrace ------------------------------------------------------------


@dataclass(frozen=True)
class EvalPaths:
    raw_path: Path
    report_path: Path


def run_eval(
    *,
    model_aliases: str | None = None,
    dry_run: bool = False,
    runs: int = DEFAULT_RUNS,
    skip_judge: bool = False,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    limit_cases: int | None = None,
    limit_duplicates: int | None = None,
    threshold: float = DEFAULT_ACCURACY_THRESHOLD,
    verbose: bool = True,
) -> EvalPaths:
    """Provede celý eval a zapíše `<timestamp>_raw.json` a `<timestamp>_report.md`."""
    specs = resolve_aliases(model_aliases)
    cases = CLASSIFICATION_CASES[:limit_cases] if limit_cases else CLASSIFICATION_CASES
    dup_cases = (
        DUPLICATE_CASES[:limit_duplicates] if limit_duplicates else DUPLICATE_CASES
    )

    session_factory = _make_session_factory()
    classify_records: list[ClassifyRecord] = []
    duplicate_records: list[DuplicateRecord] = []

    start = time.monotonic()
    for spec in specs:
        if verbose:
            print(f"[eval] {spec.alias} ({spec.model_id}) ...")
        adapter = build_adapter(spec, dry_run=dry_run)
        with session_factory() as session:
            classify_records.extend(
                _run_classification(adapter, spec, session, cases, runs)
            )
            duplicate_records.extend(
                _run_duplicates(adapter, spec, session, dup_cases, runs)
            )

    verdikty: dict[tuple[str, str, int], judge_module.JudgeVerdict] = {}
    if not skip_judge:
        if verbose:
            print(f"[eval] judge ({', '.join(JUDGE_ALIASES)}) ...")
        verdikty = judge_module.run_judges(
            _judge_tasks(classify_records, cases), JUDGE_ALIASES, dry_run=dry_run
        )

    souhrny = _summarize(specs, classify_records, duplicate_records, verdikty)
    kdy = datetime.now()
    stamp = kdy.strftime("%Y%m%d-%H%M%S")

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / f"{stamp}_raw.json"
    report_path = output_dir / f"{stamp}_report.md"

    raw_path.write_text(
        json.dumps(
            {
                "meta": {
                    "kdy": kdy.isoformat(timespec="seconds"),
                    "dry_run": dry_run,
                    "runs": runs,
                    "skip_judge": skip_judge,
                    "threshold_exact": threshold,
                    "dataset_version": DATASET_VERSION,
                    "pricing_verified": pricing_is_verified(),
                    "trvani_s": round(time.monotonic() - start, 2),
                    "modely": [asdict(s) for s in specs],
                },
                "klasifikace": [asdict(r) for r in classify_records],
                "duplicity": [asdict(r) for r in duplicate_records],
                "judge": [
                    {
                        "model_alias": v.key[0],
                        "case_id": v.key[1],
                        "run_index": v.key[2],
                        "per_judge": v.per_judge,
                        "prumer": v.prumer,
                        "selhani": v.selhani,
                    }
                    for v in verdikty.values()
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report_path.write_text(
        _render_report(
            souhrny,
            dry_run=dry_run,
            runs=runs,
            pocet_cases=len(cases),
            pocet_dup=len(dup_cases),
            skip_judge=skip_judge,
            threshold=threshold,
            kdy=kdy,
        ),
        encoding="utf-8",
    )

    if verbose:
        print(f"[eval] hotovo za {time.monotonic() - start:.1f} s")
        print(f"[eval] raw:    {raw_path}")
        print(f"[eval] report: {report_path}")

    return EvalPaths(raw_path=raw_path, report_path=report_path)


def _judge_tasks(
    records: Sequence[ClassifyRecord], cases: Sequence[ClassificationCase]
) -> list[judge_module.JudgeTask]:
    """Judge hodnotí jen skutečné odpovědi modelu — fallback text hodnotit nemá smysl."""
    podle_id = {c.case_id: c for c in cases}
    return [
        judge_module.JudgeTask(
            key=(rec.model_alias, rec.case_id, rec.run_index),
            model_alias=rec.model_alias,
            answers=podle_id[rec.case_id].answers,
            tier=rec.navrzeny_tier or "",
            zduvodneni=rec.zduvodneni,
        )
        for rec in records
        if not rec.fallback_used
    ]


# --- CLI --------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.eval.run_eval",
        description="Srovnání LLM modelů nad zlatým datasetem registru AI aplikací.",
    )
    parser.add_argument(
        "--models",
        default=None,
        help="Čárkami oddělené aliasy modelů; výchozí je všech šest.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Všechny modely obsluhuje MockAdapter — bez klíčů, bez sítě, bez nákladů.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Počet běhů na případ (variance). Výchozí {DEFAULT_RUNS}.",
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="Vynechá LLM judge — sloupce hodnocení zůstanou prázdné.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Adresář pro raw.json a report.md.",
    )
    parser.add_argument(
        "--limit-cases",
        type=int,
        default=None,
        help="Použít jen prvních N klasifikačních případů (rychlá zkouška).",
    )
    parser.add_argument(
        "--limit-duplicates",
        type=int,
        default=None,
        help="Použít jen prvních N případů duplicit.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_ACCURACY_THRESHOLD,
        help=(
            "Práh exact přesnosti pro doporučení sweetspotu "
            f"(0–1, výchozí {DEFAULT_ACCURACY_THRESHOLD})."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.runs < 1:
        print("Chyba: --runs musí být alespoň 1.", file=sys.stderr)
        return 2
    if not 0 < args.threshold <= 1:
        print("Chyba: --threshold musí být v intervalu (0, 1].", file=sys.stderr)
        return 2

    try:
        run_eval(
            model_aliases=args.models,
            dry_run=args.dry_run,
            runs=args.runs,
            skip_judge=args.skip_judge,
            output_dir=args.output,
            limit_cases=args.limit_cases,
            limit_duplicates=args.limit_duplicates,
            threshold=args.threshold,
        )
    except ValueError as exc:
        print(f"Chyba: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        # Typicky chybějící API klíč — ostrý běh bez .env.
        print(f"Chyba: {exc}", file=sys.stderr)
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
