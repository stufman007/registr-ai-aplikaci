"""Eval harness end-to-end v dry-run režimu (scripts/eval/run_eval.py).

Běží kompletně offline: `--dry-run` nasadí `MockAdapter` pro všechny modely,
takže se ověří celý řetěz (gateway → validace → audit v in-memory SQLite →
agregace → report) bez API klíčů a bez nákladů. Dataset je zmenšený na tři
případy, aby test zůstal rychlý.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.eval import judge as judge_module
from scripts.eval import run_eval as run_eval_module
from scripts.eval.models import JUDGE_ALIASES, MODELS, price_per_1000_calls
from scripts.eval.run_eval import main, run_eval

MODELY = "claude-sonnet-5,gemini-3-flash"


@pytest.fixture
def vysledky(tmp_path: Path):
    return run_eval(
        model_aliases=MODELY,
        dry_run=True,
        runs=1,
        skip_judge=True,
        output_dir=tmp_path / "results",
        limit_cases=3,
        limit_duplicates=2,
        verbose=False,
    )


def test_dry_run_vytvori_raw_json_i_report(vysledky) -> None:
    assert vysledky.raw_path.exists()
    assert vysledky.raw_path.name.endswith("_raw.json")
    assert vysledky.report_path.exists()
    assert vysledky.report_path.name.endswith("_report.md")


def test_raw_json_obsahuje_zaznamy_pro_kazdy_model(vysledky) -> None:
    data = json.loads(vysledky.raw_path.read_text(encoding="utf-8"))

    assert data["meta"]["dry_run"] is True
    assert data["meta"]["runs"] == 1
    # 2 modely × 3 případy × 1 běh
    assert len(data["klasifikace"]) == 6
    # 2 modely × 2 případy duplicit × 1 běh
    assert len(data["duplicity"]) == 4
    assert {z["model_alias"] for z in data["klasifikace"]} == set(MODELY.split(","))


def test_raw_json_nese_metriky_jednoho_volani(vysledky) -> None:
    zaznam = json.loads(vysledky.raw_path.read_text(encoding="utf-8"))["klasifikace"][0]

    # Tokeny pocházejí z audit řádku v in-memory DB, ne z odhadu harnessu.
    assert zaznam["tokens_in"] > 0
    assert zaznam["tokens_out"] > 0
    assert zaznam["fallback_used"] is False
    assert zaznam["navrzeny_tier"] in {"MALA", "STREDNI", "VELKA"}
    assert isinstance(zaznam["exact"], bool)
    assert zaznam["expected_tier"] in zaznam["acceptable_tiers"]


def test_report_obsahuje_tabulku_a_cenu(vysledky) -> None:
    report = vysledky.report_path.read_text(encoding="utf-8")

    assert "## Klasifikace" in report
    assert "| Model | Model ID | Přesnost exact" in report
    assert "Cena / 1000 klasifikací (USD)" in report
    assert "## Kontrola duplicit" in report
    assert "## Sweetspot — doporučení" in report
    # Každý hodnocený model má v tabulce svůj řádek.
    for alias in MODELY.split(","):
        assert f"| {alias} |" in report


def test_report_upozornuje_na_dry_run(vysledky) -> None:
    report = vysledky.report_path.read_text(encoding="utf-8")
    assert "DRY-RUN" in report


def test_report_varuje_ze_ceny_nejsou_overene_kdyz_neoverene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`pricing.json` je teď ověřený (viz scripts/eval/pricing.json), ale report
    musí umět zobrazit varování i pro budoucí neověřený stav — ověřeno přes monkeypatch,
    aby test nezávisel na aktuálním (ověřeném) obsahu ceníku."""
    monkeypatch.setattr(run_eval_module, "pricing_is_verified", lambda: False)

    vysledky = run_eval(
        model_aliases=MODELY,
        dry_run=True,
        runs=1,
        skip_judge=True,
        output_dir=tmp_path / "results",
        limit_cases=2,
        limit_duplicates=1,
        verbose=False,
    )
    report = vysledky.report_path.read_text(encoding="utf-8")
    assert "Ceny nejsou ověřené" in report


def test_harness_pouziva_in_memory_databazi() -> None:
    """Audit evalu nesmí téct do produkční `registr.db`."""
    from sqlalchemy import inspect as sa_inspect

    from app.models import LlmAudit
    from scripts.eval.run_eval import _make_session_factory

    factory = _make_session_factory()
    with factory() as session:
        assert session.get_bind().url.database in (None, ":memory:")
        # Schéma auditu v paměťové DB skutečně existuje.
        assert LlmAudit.__tablename__ in sa_inspect(session.get_bind()).get_table_names()
        assert session.query(LlmAudit).count() == 0


def test_cena_za_1000_klasifikaci_pouziva_realny_cenik() -> None:
    """Mimo dry-run se počítá ze sazeb aliasu; v dry-run je nula."""
    ostra = price_per_1000_calls("claude-sonnet-5", 1000.0, 200.0, dry_run=False)
    dry = price_per_1000_calls("claude-sonnet-5", 1000.0, 200.0, dry_run=True)

    assert ostra is not None and ostra > 0
    assert dry == 0.0


def test_cli_dry_run_vraci_nulu_a_vytvori_soubory(tmp_path: Path) -> None:
    kod = main(
        [
            "--dry-run",
            "--runs",
            "1",
            "--skip-judge",
            "--models",
            "gpt-mini",
            "--limit-cases",
            "2",
            "--limit-duplicates",
            "1",
            "--output",
            str(tmp_path),
        ]
    )

    assert kod == 0
    assert list(tmp_path.glob("*_raw.json"))
    assert list(tmp_path.glob("*_report.md"))


def test_cli_odmitne_neznamy_alias(tmp_path: Path) -> None:
    kod = main(["--dry-run", "--models", "neexistuje", "--output", str(tmp_path)])
    assert kod == 2


def test_cross_judge_nikdy_nehodnoti_vlastni_rodinu() -> None:
    for alias, spec in MODELS.items():
        porota = judge_module.judges_for(alias, JUDGE_ALIASES)
        assert porota, f"{alias} nemá žádného porotce"
        assert spec.family not in {j.family for j in porota}, alias


def test_judge_parsuje_jen_platnou_rubriku() -> None:
    platny = (
        'Tady je hodnocení: {"skore": {"cestina": 5, "cituje_odpovedi": 4, '
        '"bez_vymyslenych_faktu": 5, "vysvetluje_tier": 3}, "komentar": "ok"}'
    )
    assert judge_module.parse_scores(platny) == {
        "cestina": 5,
        "cituje_odpovedi": 4,
        "bez_vymyslenych_faktu": 5,
        "vysvetluje_tier": 3,
    }

    # Chybějící kritérium, hodnota mimo rozsah i text bez JSON = nepoužitelné.
    assert judge_module.parse_scores('{"skore": {"cestina": 5}}') is None
    assert (
        judge_module.parse_scores(
            '{"skore": {"cestina": 9, "cituje_odpovedi": 4, '
            '"bez_vymyslenych_faktu": 5, "vysvetluje_tier": 3}}'
        )
        is None
    )
    assert judge_module.parse_scores("bez JSON") is None
