import pytest

from app.services.similarity import Candidate, top_candidates


def test_identical_name():
    """Identický název by měl být první s skóre blízkým 1.0."""
    records = [
        ("id1", "Sumarizace smluv", "Automatické sumarizace právních smluv"),
        ("id2", "Generátor e-mailů", "Generuje e-mailové šablony"),
    ]
    result = top_candidates(
        "Sumarizace smluv",
        "Automatické sumarizace právních smluv",
        records
    )
    assert len(result) > 0
    assert result[0].record_id == "id1"
    assert result[0].score > 0.99


def test_empty_records():
    """Prázdný seznam záznamů by měl vrátit prázdný seznam."""
    result = top_candidates("Něco", "Popis", [])
    assert result == []


def test_limit_respected():
    """Nikdy nevrátí víc než limit (i když je kandidátů víc)."""
    # Vytvoř 15 velmi podobných záznamů
    records = [(f"id{i}", "Sumarizace smluv", f"Verze {i}") for i in range(15)]
    result = top_candidates("Sumarizace smluv", "Všechny verze", records, limit=10)
    assert len(result) <= 10
    # Všechny výsledky by měly přejít
    assert len(result) == 10


def test_threshold_filters():
    """Úplně odlišný text (score < 0.3) se nevrací."""
    records = [
        ("id1", "Xyzabc", "Qwertyasdfzxcv"),
    ]
    result = top_candidates("Nový název", "Úplně jiný popis", records)
    # Všechny výsledky musí mít score >= 0.3
    assert all(c.score >= 0.3 for c in result)


def test_diacritics_ignored():
    """Diakritika by neměla hrát roli."""
    records = [
        ("id1", "Sumarizace smluv", "Popis se znaménky"),
    ]
    # Verze bez diakritiky
    result = top_candidates("sumarizace smluv", "popis se znamenky", records)
    assert len(result) > 0
    # Mělo by to být velmi podobné, i bez diakritiky
    assert result[0].score > 0.9


def test_ranking():
    """Výsledky jsou seřazeny sestupně podle skóre."""
    records = [
        ("id1", "AAA", "BBB"),
        ("id2", "Sumarizace smluv", "Automatické zpracování"),
        ("id3", "Sumarizace", "Něco"),
    ]
    result = top_candidates("Sumarizace smluv", "Automatické sumarizace", records)
    if len(result) > 1:
        # Skóre by měla být klesající
        for i in range(len(result) - 1):
            assert result[i].score >= result[i + 1].score


def test_candidate_dataclass():
    """Candidate by měl být frozen dataclass."""
    records = [("id1", "Test", "Test")]
    result = top_candidates("Test", "Test", records)
    assert len(result) == 1
    candidate = result[0]
    # Zkontrolovat, že je to Candidate
    assert isinstance(candidate, Candidate)
    assert candidate.record_id == "id1"
    assert candidate.nazev == "Test"
    assert candidate.popis == "Test"
    # Frozen = nelze měnit
    with pytest.raises(AttributeError):
        candidate.score = 0.5


def test_case_insensitive():
    """Velká a malá písmena by neměla hrát roli."""
    records = [
        ("id1", "SUMARIZACE SMLUV", "POPIS V CAPS"),
    ]
    result = top_candidates("sumarizace smluv", "popis v caps", records)
    assert len(result) > 0
    assert result[0].score > 0.9


def test_whitespace_normalized():
    """Běhoucí whitespace by měl být normalizován."""
    records = [
        ("id1", "Sumarizace  smluv", "Popis   s   mezerami"),
    ]
    result = top_candidates(
        "Sumarizace smluv",
        "Popis s mezerami",
        records
    )
    assert len(result) > 0
    assert result[0].score > 0.9
