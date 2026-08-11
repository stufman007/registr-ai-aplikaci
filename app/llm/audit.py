"""Zápis a retence metadat o voláních LLM (spec kap. 4.4, 8 a 13).

Invarianta: do auditu teče **jen metadata**. Funkce v tomto modulu záměrně
nepřijímají prompt ani odpověď — co sem nelze předat, to sem nemůže uniknout.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import LlmAudit
from app.schemas import LlmPurpose
from app.services.policy import RULES_VERSION

logger = logging.getLogger(__name__)


def record_call(
    session: Session,
    *,
    ucel: LlmPurpose,
    provider: str,
    model: str,
    prompt_version: str,
    tokens_in: int,
    tokens_out: int,
    latence_ms: int,
    uspech: bool,
    error_code: str | None = None,
    fallback_used: bool = False,
) -> LlmAudit:
    """Zapíše jeden řádek auditu. Commit je záměrný — audit musí přežít i to,
    když volající transakci později zahodí (např. při chybě uložení záznamu)."""
    row = LlmAudit(
        ucel=ucel,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        rules_version=RULES_VERSION,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latence_ms=latence_ms,
        uspech=uspech,
        error_code=error_code,
        fallback_used=fallback_used,
    )
    session.add(row)
    session.commit()

    logger.info(
        "LLM audit: ucel=%s provider=%s model=%s uspech=%s error_code=%s fallback=%s",
        ucel,
        provider,
        model,
        uspech,
        error_code,
        fallback_used,
    )
    return row


def purge_older_than(session: Session, days: int) -> int:
    """Smaže záznamy auditu starší než `days` dní. Vrací počet smazaných řádků."""
    if days < 0:
        raise ValueError("Retence nemůže být záporná")

    hranice = datetime.now(timezone.utc) - timedelta(days=days)
    result = session.execute(delete(LlmAudit).where(LlmAudit.kdy < hranice))
    session.commit()

    smazano = result.rowcount or 0
    if smazano:
        logger.info("LLM audit: smazáno %d záznamů starších než %d dní", smazano, days)
    return smazano
