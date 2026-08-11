import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Sequence

MAX_CANDIDATES = 10
FALLBACK_THRESHOLD = 0.6


@dataclass(frozen=True)
class Candidate:
    """Kandidát na duplicitní záznam."""
    record_id: str
    nazev: str
    popis: str
    score: float


def _normalize_text(text: str) -> str:
    """
    Normalizace textu: lowercase, odstranění diakritiky, sjednocení whitespace.

    Args:
        text: Text k normalizaci

    Returns:
        Normalizovaný text
    """
    # Převést na lowercase
    text = text.lower()
    # Rozložit diakritikou přes NFD normalizaci
    nfd = unicodedata.normalize('NFD', text)
    # Odfiltrovat combining znaky (diakritické značky)
    without_diacritics = ''.join(
        c for c in nfd if unicodedata.category(c) != 'Mn'
    )
    # Sjednotit whitespace
    normalized = ' '.join(without_diacritics.split())
    return normalized


def top_candidates(
    nazev: str,
    popis: str,
    records: Sequence[tuple[str, str, str]],
    limit: int = MAX_CANDIDATES,
) -> list[Candidate]:
    """
    Najde top N nejpodobnějších kandidátů z registru.

    Lokální předvýběr duplicit bez LLM. Vrací max `limit` kandidátů
    seřazených sestupně podle podobnosti, bez skóre < 0.3.

    Args:
        nazev: Název nové aplikace
        popis: Popis nové aplikace
        records: Sekvence (record_id, nazev, popis) existujících záznamů
        limit: Maximální počet výsledků (default MAX_CANDIDATES)

    Returns:
        Seznam Candidate seřazený sestupně podle skóre
    """
    if not records:
        return []

    norm_new_nazev = _normalize_text(nazev)
    norm_new_popis = _normalize_text(popis)
    combined_new = f"{norm_new_nazev} {norm_new_popis}"

    candidates = []

    for record_id, rec_nazev, rec_popis in records:
        norm_rec_nazev = _normalize_text(rec_nazev)
        norm_rec_popis = _normalize_text(rec_popis)
        combined_rec = f"{norm_rec_nazev} {norm_rec_popis}"

        # Skóre = max(podobnost názvů, podobnost kombinované)
        score_nazev = SequenceMatcher(None, norm_new_nazev, norm_rec_nazev).ratio()
        score_combined = SequenceMatcher(None, combined_new, combined_rec).ratio()
        score = max(score_nazev, score_combined)

        # Zahodíme skóre < 0.3 (šum)
        if score < 0.3:
            continue

        candidates.append(Candidate(
            record_id=record_id,
            nazev=rec_nazev,
            popis=rec_popis,
            score=score
        ))

    # Seřadit sestupně podle skóre
    candidates.sort(key=lambda c: c.score, reverse=True)

    # Vrátit max limit výsledků
    return candidates[:limit]
