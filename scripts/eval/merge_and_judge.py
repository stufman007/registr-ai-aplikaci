"""Sloučí klasifikační a duplicitní záznamy z více `--skip-judge` běhů evalu do
jednoho datasetu, spustí cross-judge nad sloučenými daty a vykreslí jeden
finální report pro všechny modely dohromady.

Proč existuje: plný ostrý běh (6 modelů × 2 běhy × klasifikace + duplicity +
cross-judge) je stovky reálných API volání a nevejde se do jednoho časově
omezeného procesu. `run_eval.py` to zvládne pro menší podmnožinu modelů najednou
(`--models`); tenhle skript pak sloučí výstupy dílčích běhů (`*_raw.json`) do
jednoho apples-to-apples reportu — přesně jak by to udělal jeden velký běh,
jen bez re-volání klasifikace (ta se jen načte z už uložených dat).

Spuštění:
    .venv/Scripts/python -m scripts.eval.merge_and_judge \
        scripts/eval/results/<a>_raw.json scripts/eval/results/<b>_raw.json ... \
        --output scripts/eval/results

Vstupní raw.json soubory musí pokrývat dohromady všechny modely, které mají být
v reportu — každý alias smí být v datech jen jednou (víc běhů pro stejný alias
zatím nepodporujeme, aby nedošlo k tichému zdvojení metrik).

I samotný cross-judge nad stovkami záznamů je moc na jeden časově omezený
proces. `--only-model-aliases` + `--verdicts-cache` umožní rozsekat i judge
krok na víc běhů: každý běh ohodnotí jen podmnožinu modelů a nové verdikty
přičte do cache souboru (JSON, bezpečné spouštět opakovaně — už ohodnocené
dvojice se přeskočí, nepřeplácí se za ně znovu). Poslední běh (bez
`--only-model-aliases`, se stejnou `--verdicts-cache`) vykreslí finální report
nad kompletní cache — bez jediného dalšího API volání, pokud už je vše
ohodnocené.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from scripts.eval import judge as judge_module
from scripts.eval.dataset import CLASSIFICATION_CASES, DATASET_VERSION, DUPLICATE_CASES
from scripts.eval.models import JUDGE_ALIASES, ModelSpec, price_for_tokens, pricing_is_verified
from scripts.eval.run_eval import (
    DEFAULT_ACCURACY_THRESHOLD,
    ClassifyRecord,
    DuplicateRecord,
    EvalPaths,
    _judge_tasks,
    _render_report,
    _summarize,
)

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "results"


def _load_records(
    paths: list[Path],
) -> tuple[list[ClassifyRecord], list[DuplicateRecord], list[ModelSpec], bool, int]:
    classify: list[ClassifyRecord] = []
    duplicate: list[DuplicateRecord] = []
    specs_by_alias: dict[str, ModelSpec] = {}
    dry_run_any = False
    runs = 0

    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        dry_run_any = dry_run_any or bool(data["meta"]["dry_run"])
        runs = max(runs, int(data["meta"]["runs"]))

        for m in data["meta"]["modely"]:
            alias = m["alias"]
            if alias in specs_by_alias:
                raise ValueError(
                    f"Alias {alias!r} se objevuje ve víc než jednom vstupním raw.json "
                    "— sloučení by tiše zdvojilo metriky. Zkontroluj vstupní soubory."
                )
            specs_by_alias[alias] = ModelSpec(**m)

        classify.extend(ClassifyRecord(**rec) for rec in data["klasifikace"])
        duplicate.extend(DuplicateRecord(**rec) for rec in data["duplicity"])

    return classify, duplicate, list(specs_by_alias.values()), dry_run_any, runs


def _verdict_key(entry: dict) -> tuple[str, str, int]:
    return (entry["model_alias"], entry["case_id"], entry["run_index"])


def _load_verdicts_cache(
    path: Path | None,
) -> tuple[dict[tuple[str, str, int], judge_module.JudgeVerdict], dict[str, judge_module.JudgeUsage]]:
    """Načte dřívější verdikty a spotřebu porotců z cache souboru (pokud existuje)."""
    if path is None or not path.exists():
        return {}, {}

    data = json.loads(path.read_text(encoding="utf-8"))
    verdikty = {
        _verdict_key(v): judge_module.JudgeVerdict(
            key=_verdict_key(v), per_judge=v["per_judge"], prumer=v["prumer"], selhani=v["selhani"]
        )
        for v in data.get("verdikty", [])
    }
    usage = {
        alias: judge_module.JudgeUsage(**u) for alias, u in data.get("judge_usage", {}).items()
    }
    return verdikty, usage


def _save_verdicts_cache(
    path: Path,
    verdikty: dict[tuple[str, str, int], judge_module.JudgeVerdict],
    usage: dict[str, judge_module.JudgeUsage],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "verdikty": [
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
                "judge_usage": {alias: asdict(u) for alias, u in usage.items()},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def merge_and_judge(
    raw_paths: list[Path],
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    skip_judge: bool = False,
    threshold: float = DEFAULT_ACCURACY_THRESHOLD,
    only_model_aliases: set[str] | None = None,
    verdicts_cache: Path | None = None,
    verbose: bool = True,
) -> EvalPaths:
    start = time.monotonic()
    classify_records, duplicate_records, specs, dry_run, runs = _load_records(raw_paths)

    verdikty, judge_usage = _load_verdicts_cache(verdicts_cache)

    if not skip_judge:
        judge_scope = (
            [r for r in classify_records if r.model_alias in only_model_aliases]
            if only_model_aliases is not None
            else classify_records
        )
        vsechny_ulohy = _judge_tasks(judge_scope, CLASSIFICATION_CASES)
        # Už ohodnocené dvojice (case, model, run) se nepřeplácí znovu — cache je idempotentní.
        nove_ulohy = [t for t in vsechny_ulohy if t.key not in verdikty]

        if nove_ulohy:
            if verbose:
                print(
                    f"[merge] judge ({', '.join(JUDGE_ALIASES)}) nad {len(nove_ulohy)} "
                    f"novými záznamy (z {len(vsechny_ulohy)} v tomto rozsahu) ..."
                )
            nove_verdikty, nova_spotreba = judge_module.run_judges(
                nove_ulohy, JUDGE_ALIASES, dry_run=dry_run
            )
            verdikty.update(nove_verdikty)
            for alias, u in nova_spotreba.items():
                soucet = judge_usage.setdefault(alias, judge_module.JudgeUsage())
                soucet.calls += u.calls
                soucet.tokens_in += u.tokens_in
                soucet.tokens_out += u.tokens_out
        elif verbose:
            print("[merge] judge: nic nového k ohodnocení (vše už je v cache) ...")

        if verdicts_cache is not None:
            _save_verdicts_cache(verdicts_cache, verdikty, judge_usage)

    souhrny = _summarize(specs, classify_records, duplicate_records, verdikty, dry_run=dry_run)
    kdy = datetime.now()
    stamp = kdy.strftime("%Y%m%d-%H%M%S")

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / f"{stamp}_merged_raw.json"
    report_path = output_dir / f"{stamp}_merged_report.md"

    judge_cena_per_alias = {
        alias: price_for_tokens(alias, u.tokens_in, u.tokens_out, dry_run=dry_run) or 0.0
        for alias, u in judge_usage.items()
    }
    skutecna_cena_celkem = sum(s.skutecna_cena_usd for s in souhrny.values()) + sum(
        judge_cena_per_alias.values()
    )

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
                    "zdrojove_raw_soubory": [str(p) for p in raw_paths],
                    "skutecna_cena_usd_per_model": {
                        alias: round(s.skutecna_cena_usd, 6) for alias, s in souhrny.items()
                    },
                    "skutecna_cena_usd_per_judge": {
                        alias: round(cena, 6) for alias, cena in judge_cena_per_alias.items()
                    },
                    "skutecna_cena_usd_celkem": round(skutecna_cena_celkem, 6),
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
            pocet_cases=len(CLASSIFICATION_CASES),
            pocet_dup=len(DUPLICATE_CASES),
            skip_judge=skip_judge,
            threshold=threshold,
            kdy=kdy,
            judge_usage=judge_usage,
        ),
        encoding="utf-8",
    )

    if verbose:
        print(f"[merge] hotovo za {time.monotonic() - start:.1f} s")
        print(f"[merge] raw:    {raw_path}")
        print(f"[merge] report: {report_path}")

    return EvalPaths(raw_path=raw_path, report_path=report_path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.eval.merge_and_judge",
        description="Sloučí raw.json z více dílčích --skip-judge běhů, spustí judge a vykreslí jeden report.",
    )
    parser.add_argument("raw_paths", nargs="+", type=Path, help="Vstupní *_raw.json soubory.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--skip-judge", action="store_true")
    parser.add_argument("--threshold", type=float, default=DEFAULT_ACCURACY_THRESHOLD)
    parser.add_argument(
        "--only-model-aliases",
        default=None,
        help=(
            "Čárkami oddělené aliasy — judge ohodnotí jen záznamy těchto modelů "
            "v tomto běhu (rozsekání velkého judge kroku na víc časově omezených "
            "procesů). Bez přepínače ohodnotí všechny modely ze vstupu."
        ),
    )
    parser.add_argument(
        "--verdicts-cache",
        type=Path,
        default=None,
        help=(
            "JSON soubor s dřívějšími verdikty a spotřebou porotců. Idempotentní: "
            "už ohodnocené dvojice (model, případ, běh) se přeskočí. Report se vždy "
            "renderuje nad kompletním obsahem cache, i při --only-model-aliases."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    only_model_aliases = (
        {a.strip() for a in args.only_model_aliases.split(",") if a.strip()}
        if args.only_model_aliases
        else None
    )
    try:
        merge_and_judge(
            args.raw_paths,
            output_dir=args.output,
            skip_judge=args.skip_judge,
            threshold=args.threshold,
            only_model_aliases=only_model_aliases,
            verdicts_cache=args.verdicts_cache,
        )
    except ValueError as exc:
        print(f"Chyba: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
