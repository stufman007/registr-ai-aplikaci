"""Validace zlatého eval datasetu (scripts/eval/dataset.py).

Nejdůležitější test celého evalu: `expected_tier` nesmí být pod deterministickým
policy minimem. Kdyby byl, eval by trestal model za to, že navrhl tier, který
aplikace stejně vynutí — a naopak by odměňoval návrh, který produkce zvedne.
Test tedy hlídá, že se dataset a `app/services/policy.py` nerozejdou.
"""

from __future__ import annotations

import pytest

from app.services.policy import RULES, compute_minimum
from scripts.eval.dataset import (
    CLASSIFICATION_CASES,
    DUPLICATE_CASES,
    ClassificationCase,
    DuplicateCase,
)

#: Minimální pokrytí požadované zadáním evalu.
MIN_NEVINNYCH_MALA = 4
MIN_KOMBINACI = 3
MIN_HRANICNICH = 4
MIN_DUPLICITNICH_PARU = 8
MAX_KONCEPTU = 3


def _minimum(case: ClassificationCase):
    return compute_minimum(case.answers, list(case.components))


# --- Konzistence s policy pravidly (kritické) -------------------------------


@pytest.mark.parametrize("case", CLASSIFICATION_CASES, ids=lambda c: c.case_id)
def test_expected_tier_neni_pod_policy_minimem(case: ClassificationCase) -> None:
    minimum = _minimum(case)
    assert case.expected_tier >= minimum.tier, (
        f"{case.case_id}: expected_tier={case.expected_tier.name} je pod policy "
        f"minimem {minimum.tier.name} "
        f"(pravidla: {', '.join(r.reason_code for r in minimum.triggered_rules)})"
    )


@pytest.mark.parametrize("case", CLASSIFICATION_CASES, ids=lambda c: c.case_id)
def test_zadny_acceptable_tier_neni_pod_policy_minimem(case: ClassificationCase) -> None:
    """Tolerance nesmí připustit tier, který by produkce stejně zvedla."""
    minimum = _minimum(case)
    pod_minimem = [t.name for t in case.acceptable_tiers if t < minimum.tier]
    assert not pod_minimem, (
        f"{case.case_id}: acceptable_tiers {pod_minimem} jsou pod minimem "
        f"{minimum.tier.name}"
    )


@pytest.mark.parametrize("case", CLASSIFICATION_CASES, ids=lambda c: c.case_id)
def test_acceptable_tiers_obsahuje_expected(case: ClassificationCase) -> None:
    assert case.expected_tier in case.acceptable_tiers


# --- Pokrytí pravidel a typů případů ----------------------------------------


def test_kazde_policy_pravidlo_ma_alespon_jeden_pripad() -> None:
    pokryto: set[str] = set()
    for case in CLASSIFICATION_CASES:
        pokryto |= {rule.reason_code for rule in _minimum(case).triggered_rules}

    nepokryto = {rule.reason_code for rule in RULES} - pokryto
    assert not nepokryto, f"Dataset nepokrývá policy pravidla: {sorted(nepokryto)}"


def test_dataset_ma_dost_kombinaci_pravidel() -> None:
    kombinace = [
        case.case_id
        for case in CLASSIFICATION_CASES
        if len(_minimum(case).triggered_rules) >= 2
    ]
    assert len(kombinace) >= MIN_KOMBINACI, kombinace


def test_dataset_ma_dost_nevinnych_mala_pripadu() -> None:
    from app.schemas import Tier

    nevinne = [
        case.case_id
        for case in CLASSIFICATION_CASES
        if case.expected_tier == Tier.MALA and _minimum(case).tier == Tier.MALA
    ]
    assert len(nevinne) >= MIN_NEVINNYCH_MALA, nevinne


def test_dataset_ma_dost_hranicnich_pripadu() -> None:
    """Hraniční = má definovanou toleranci víc než jednoho tieru."""
    hranicni = [
        case.case_id for case in CLASSIFICATION_CASES if len(case.acceptable_tiers) > 1
    ]
    assert len(hranicni) >= MIN_HRANICNICH, hranicni


# --- Tvarové invarianty ------------------------------------------------------


def test_case_id_jsou_unikatni() -> None:
    ids = [case.case_id for case in CLASSIFICATION_CASES]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("case", CLASSIFICATION_CASES, ids=lambda c: c.case_id)
def test_kazdy_pripad_ma_koncepty_i_zduvodneni_proc(case: ClassificationCase) -> None:
    assert 1 <= len(case.rationale_must_mention) <= MAX_KONCEPTU
    assert all(koncept.strip() for koncept in case.rationale_must_mention)
    assert case.note.strip(), f"{case.case_id} nemá vysvětlení, proč je v datasetu"
    assert case.components, f"{case.case_id} nemá žádnou AI komponentu"


# --- Duplicitní páry ---------------------------------------------------------


def test_dataset_ma_dost_duplicitnich_paru() -> None:
    assert len(DUPLICATE_CASES) >= MIN_DUPLICITNICH_PARU


def test_dup_case_id_jsou_unikatni() -> None:
    ids = [case.dup_case_id for case in DUPLICATE_CASES]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("case", DUPLICATE_CASES, ids=lambda c: c.dup_case_id)
def test_duplicitni_pripad_ma_3_az_5_existujicich_zaznamu(case: DuplicateCase) -> None:
    assert 3 <= len(case.existing) <= 5


@pytest.mark.parametrize("case", DUPLICATE_CASES, ids=lambda c: c.dup_case_id)
def test_ocekavane_shody_odkazuji_na_existujici_zaznamy(case: DuplicateCase) -> None:
    znama_id = {record.record_id for record in case.existing}
    neznama = case.expected_match_ids - znama_id
    assert not neznama, f"{case.dup_case_id}: neznámá record_id {sorted(neznama)}"


def test_existuje_pripad_bez_ocekavane_shody() -> None:
    """Bez negativního případu by se nedala měřit precision."""
    assert any(not case.expected_match_ids for case in DUPLICATE_CASES)
