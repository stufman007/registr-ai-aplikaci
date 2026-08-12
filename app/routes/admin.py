"""Správcovské akce: vyřazení, obnovení a úprava klasifikace (Fáze 13, spec
kap. 3, 5.5, 14 obrazovky 4/5).

Fyzický DELETE neexistuje nikde v tomto modulu (spec kap. 4.1) — vyřazení jen
nastaví `deleted_at/deleted_by/delete_reason`, obnovení je vynuluje; historie
(`RETIRE`/`RESTORE`/`CLASSIFY`) je append-only přes `app.services.history`.

Vzory sdílené s `app.routes.edit`:

- Optimistic locking: skryté pole `version` z formuláře se porovná s DB
  hodnotou **těsně před zápisem** — na neshodu se nikdy nesahá na atributy
  záznamu.
- `klasifikace_minimum` se u překlasifikace počítá **znovu** z uloženého
  `dotaznik_odpovedi` a aktuálních AI komponent, nikdy z klientského vstupu —
  ruční tier pod minimum vyhodí `PolicyViolation` (handler v `app.main` z ní
  udělá 400), a to i při ručně sestaveném POSTu mimo UI.

Dvě routery v jednom modulu: `router` (prefix `/admin`) drží jen kontrolní
`/admin/ping` z Fáze 8; `aplikace_router` (bez prefixu) nese akce nad
konkrétním záznamem na cestě `/aplikace/{id}/...`, aby se sjednotila se
zbytkem registru (`registry.py`, `edit.py`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.auth import SessionUser, require_admin
from app.db import get_db
from app.flash import set_flash
from app.models import Application
from app.questionnaire import CHOICE_QUESTIONS, FREE_TEXT_QUESTION
from app.routes.classification import MAX_LONG_TEXT
from app.schemas import AuditAction, DotaznikOdpovedi, KomponentaInfo, Tier
from app.security import VersionConflict, verify_csrf
from app.services import history
from app.services.policy import MinimumResult, compute_minimum, effective_tier
from app.services.regulatory import Flag, compute_flags
from app.templating import templates

#: `/admin/ping` — kontrolní endpoint role z Fáze 8, beze změny.
router = APIRouter(prefix="/admin", tags=["admin"])

#: Akce nad konkrétním záznamem (`/aplikace/{id}/...`) — vlastní router, aby
#: prefix `/admin` neprosakoval do cest sdílených se `registry.py`/`edit.py`.
aplikace_router = APIRouter(tags=["admin"])


@router.get("/ping")
def admin_ping(user: Annotated[SessionUser, Depends(require_admin)]) -> dict[str, str]:
    """Ověřuje, že se role vynucuje na backendu: bez role `admin` vrací 403."""
    return {"status": "ok", "username": user.username}


# --- Společné pomocníky -------------------------------------------------------


def _load_admin_application(db: Session, application_id: str) -> Application:
    """Admin vidí i vyřazené záznamy — na rozdíl od `edit._load_editable_application`
    tady chybějící záznam vždy znamená 404, žádné dodatečné oprávnění se neřeší
    (`require_admin` už proběhlo jako dependency)."""
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Aplikace nenalezena."
        )
    return application


def _field(form: FormData, name: str) -> str:
    raw = form.get(name)
    return raw.strip() if isinstance(raw, str) else ""


def _check_version(application: Application, form: FormData) -> None:
    """Optimistic locking těsně před zápisem (spec kap. 11.5) — stejný vzor
    jako `edit.submit_edit`."""
    raw = _field(form, "version")
    try:
        version_from_form = int(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Neplatná verze formuláře."
        ) from None
    if application.version != version_from_form:
        raise VersionConflict("Záznam mezitím změnil někdo jiný.")


def _answers_and_components(
    application: Application,
) -> tuple[DotaznikOdpovedi, list[KomponentaInfo]]:
    """Rekonstrukce vstupů pro `policy.compute_minimum`/`regulatory.compute_flags`
    přímo z uloženého záznamu — zdroj pravdy pro admin překlasifikaci, nikdy
    hodnota z formuláře (spec kap. 5.5: admin nesmí obejít policy floor)."""
    odpovedi = application.dotaznik_odpovedi
    answers = DotaznikOdpovedi(
        ucel=odpovedi[FREE_TEXT_QUESTION.field],
        **{
            question.field: question.enum[odpovedi[question.field]]  # type: ignore[index]
            for question in CHOICE_QUESTIONS
        },
    )
    components = [
        KomponentaInfo(provider=c.provider, hosting_type=c.hosting_type)
        for c in application.components
    ]
    return answers, components


def _redirect_to_detail(application_id: str) -> RedirectResponse:
    return RedirectResponse(
        f"/aplikace/{application_id}", status_code=status.HTTP_303_SEE_OTHER
    )


# --- POST /aplikace/{id}/vyradit ----------------------------------------------


@aplikace_router.post(
    "/aplikace/{application_id}/vyradit",
    dependencies=[Depends(verify_csrf)],
    name="retire_application",
)
async def retire_application(
    request: Request,
    application_id: str,
    user: Annotated[SessionUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Soft delete s povinným důvodem (spec kap. 4.1, 15 kritérium 10).

    Fyzický DELETE se nekoná — jen se nastaví `deleted_at/deleted_by/
    delete_reason` a zapíše historie `RETIRE`. Už vyřazený záznam → 400.
    """
    application = _load_admin_application(db, application_id)
    if application.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Aplikace je již vyřazená."
        )

    form = await request.form()
    reason = _field(form, "reason")
    if not reason:
        set_flash(request, "Uveďte prosím důvod vyřazení.")
        return _redirect_to_detail(application_id)
    if len(reason) > MAX_LONG_TEXT:
        set_flash(request, f"Důvod vyřazení smí mít nejvýš {MAX_LONG_TEXT} znaků.")
        return _redirect_to_detail(application_id)

    _check_version(application, form)

    application.deleted_at = datetime.now(timezone.utc)
    application.deleted_by = user.username
    application.delete_reason = reason
    application.version += 1
    application.updated_by = user.username

    history.log(db, application.id, user.username, AuditAction.RETIRE, reason=reason)

    db.commit()
    set_flash(request, f"Aplikace „{application.nazev}“ byla vyřazena.")
    return _redirect_to_detail(application_id)


# --- POST /aplikace/{id}/obnovit ----------------------------------------------


@aplikace_router.post(
    "/aplikace/{application_id}/obnovit",
    dependencies=[Depends(verify_csrf)],
    name="restore_application",
)
async def restore_application(
    request: Request,
    application_id: str,
    user: Annotated[SessionUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Obnovení vyřazeného záznamu (spec kap. 15 kritérium 10). Nevyřazený → 400."""
    application = _load_admin_application(db, application_id)
    if application.deleted_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Aplikace není vyřazená."
        )

    form = await request.form()
    reason = _field(form, "reason")  # volitelný — RESTORE nemá povinný reason (spec kap. 4.3)
    if len(reason) > MAX_LONG_TEXT:
        set_flash(request, f"Poznámka k obnovení smí mít nejvýš {MAX_LONG_TEXT} znaků.")
        return _redirect_to_detail(application_id)

    _check_version(application, form)

    application.deleted_at = None
    application.deleted_by = None
    application.delete_reason = None
    application.version += 1
    application.updated_by = user.username

    history.log(
        db, application.id, user.username, AuditAction.RESTORE, reason=reason or None
    )

    db.commit()
    set_flash(request, f"Aplikace „{application.nazev}“ byla obnovena.")
    return _redirect_to_detail(application_id)


# --- GET/POST /aplikace/{id}/preklasifikovat ----------------------------------


def _render_reclassify(
    request: Request,
    user: SessionUser,
    application: Application,
    minimum: MinimumResult,
    flags: list[Flag],
    selected: Tier,
    poznamka: str = "",
    error: str = "",
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin_reclassify.html",
        {
            "user": user,
            "application": application,
            "llm_tier": application.klasifikace_llm,
            "minimum": minimum,
            "flags": flags,
            "current_tier": application.klasifikace,
            "tier_options": [t for t in Tier if t >= minimum.tier],
            "selected": selected,
            "poznamka": poznamka,
            "error": error,
            "version": application.version,
        },
        status_code=status_code,
    )


@aplikace_router.get(
    "/aplikace/{application_id}/preklasifikovat",
    response_class=HTMLResponse,
    name="admin_reclassify_form",
)
def reclassify_form(
    request: Request,
    application_id: str,
    user: Annotated[SessionUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Aktuální klasifikace (AI návrh / minimum / effective) + formulář nového
    tieru (spec kap. 14 obrazovka 5). Vyřazený záznam se nepřeklasifikovává."""
    application = _load_admin_application(db, application_id)
    if application.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vyřazený záznam nelze překlasifikovat.",
        )

    answers, components = _answers_and_components(application)
    minimum = compute_minimum(answers, components)
    flags = compute_flags(answers, components)

    return _render_reclassify(
        request, user, application, minimum, flags, selected=application.klasifikace
    )


@aplikace_router.post(
    "/aplikace/{application_id}/preklasifikovat",
    dependencies=[Depends(verify_csrf)],
    name="admin_reclassify_submit",
)
async def reclassify_submit(
    request: Request,
    application_id: str,
    user: Annotated[SessionUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Uloží ručně vybraný tier — nikdy pod deterministické policy minimum
    (spec kap. 3, 5.5). Minimum a signály se přepočítají znovu ze zdroje
    pravdy (uložené odpovědi + komponenty), tier a poznámka přijdou z formuláře.
    """
    application = _load_admin_application(db, application_id)
    if application.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vyřazený záznam nelze překlasifikovat.",
        )

    answers, components = _answers_and_components(application)
    minimum = compute_minimum(answers, components)
    flags = compute_flags(answers, components)
    llm_tier = application.klasifikace_llm

    form = await request.form()
    poznamka = _field(form, "poznamka")
    tier_raw = _field(form, "tier")
    manual = Tier[tier_raw] if tier_raw in Tier.__members__ else None

    if manual is None:
        return _render_reclassify(
            request, user, application, minimum, flags, application.klasifikace, poznamka,
            error="Vyberte nový governance tier.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Pořadí je záměrné (stejně jako u založení/editace): policy floor se
    # ověří dřív než formulářová pravidla, aby ručně sestavený POST bez
    # poznámky skončil na 400 z `PolicyViolation`, ne na chybě „poznámka
    # chybí" — pod minimum nejde uložit ani s poznámkou.
    #
    # `actor_is_admin=True` je tu zásadní (regresní bug): bez něj by
    # `effective_tier` počítala max(llm_tier, minimum, manual) a ruční
    # snížení admina pod AI návrh (ale nad minimem) by se tiše přebilo zpět
    # nahoru — přesně reportovaný bug VELKÁ→STŘEDNÍ.
    vysledny = effective_tier(
        llm_tier, minimum, manual_tier=manual, actor_is_admin=True
    )

    if not poznamka:
        return _render_reclassify(
            request, user, application, minimum, flags, manual, poznamka,
            error="Poznámka k překlasifikaci je povinná.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if len(poznamka) > MAX_LONG_TEXT:
        return _render_reclassify(
            request, user, application, minimum, flags, manual, poznamka,
            error=f"Poznámka smí mít nejvýš {MAX_LONG_TEXT} znaků.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    _check_version(application, form)

    stara_hodnota = application.klasifikace.name

    application.klasifikace_minimum = minimum.tier
    application.klasifikace = vysledny
    application.klasifikace_priznaky = [flag.to_dict() for flag in flags]
    application.klasifikace_poznamka = poznamka
    application.klasifikace_potvrdil = user.username
    application.version += 1
    application.updated_by = user.username

    history.log(
        db,
        application.id,
        user.username,
        AuditAction.CLASSIFY,
        pole="klasifikace",
        stara=stara_hodnota,
        nova=vysledny.name,
        reason=poznamka,
    )

    db.commit()
    set_flash(request, f"Aplikace „{application.nazev}“ byla překlasifikována.")
    return _redirect_to_detail(application_id)
