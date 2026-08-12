"""Pravidla vyhodnocení — veřejné vysvětlení jak se klasifikace určuje (Fáze 10).

Stránka čerpá data přímo ze služeb policy.py a regulatory.py (single source of truth).
Cíl: uživatel pochopí, proč se ptáme na ta která pole a jak se z odpovědí odvozuje
povinné minimum tieru.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.auth import require_user, SessionUser
from app.questionnaire import QUESTIONS
from app.services.policy import RULES, RULES_VERSION
from app.services.regulatory import ALLOWED_FLAGS
from app.templating import templates

router = APIRouter(tags=["rules"])


@router.get("/pravidla", response_class=HTMLResponse, name="rules_info")
def rules_info(
    request: Request,
    user: Annotated[SessionUser, Depends(require_user)],
) -> HTMLResponse:
    """Stránka s vysvětlením pravidel vyhodnocení klasifikace (česky, srozumitelně).

    Čerpá data přímo z policy.RULES, regulatory signálů a questionnaire.QUESTIONS.
    Zobrazuje:
    - Úvod: jak funguje vyhodnocení (AI návrh + deterministická pravidla + člověk)
    - Tabulka otázek: k čemu se ptáme a která pravidla na odpovědi reagují
    - Tabulka pravidel: eskalační minimum podle podmínek
    - Legislativní signály: GDPR/AI-ACT/DORA a kdy se objevují
    """

    # Strukturuji data pro šablonu
    rules_data = [
        {
            "reason_code": rule.reason_code,
            "min_tier": rule.min_tier,
            "min_tier_label": rule.min_tier.label(),
            "popis": rule.popis,
        }
        for rule in RULES
    ]

    # Legislativní signály — textová vysvětlení
    signal_info = {
        "GDPR": {
            "titulek": "GDPR — zpracování osobních údajů",
            "kdy": "Otázka 4: Když aplikace zpracovává osobní údaje zaměstnanců nebo klientů",
            "znamena": (
                "Vyžaduje právní review zpracování pod GDPR. Pro klientské údaje je to "
                "nejpřísnější režim — musí být zajištěn soubor souladu (právní základ, "
                "zpracovatel, zabezpečení atd.)."
            ),
        },
        "AI-ACT": {
            "titulek": "EU AI Act — rozhodování o lidech",
            "kdy": (
                "Otázka 5: Když aplikace doporučuje nebo automaticky rozhoduje "
                "o konkrétní osobě"
            ),
            "znamena": (
                "Signál vysokého rizika dle EU AI Act (Příloha III). Automatizované "
                "rozhodování bez lidské kontroly vyžaduje governance review. Není to "
                "právní závěr, ale podnět k prověření."
            ),
        },
        "DORA": {
            "titulek": "DORA — kritické operace s externím AI",
            "kdy": (
                "Otázka 3 (kritický proces) + externí AI provider "
                "(Komponenty: hostování External API)"
            ),
            "znamena": (
                "Vyžaduje security review a dokumentaci SLA. External provider znamená "
                "větší riziko výpadku nebo nedostupnosti — kritické procesy tam jsou "
                "zvýšené riziko."
            ),
        },
    }

    return templates.TemplateResponse(
        request,
        "rules_info.html",
        {
            "user": user,
            "rules_version": RULES_VERSION,
            "rules": rules_data,
            "signal_info": signal_info,
            "allowed_flags": sorted(ALLOWED_FLAGS),
            "questions": QUESTIONS,
        },
    )
