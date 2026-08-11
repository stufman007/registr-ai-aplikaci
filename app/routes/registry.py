"""Registr: seznam a detail karty aplikace (Fáze 10, spec kap. 12 UI obrazovky 2 a 4).

Založení/editace/vyřazení jsou pozdější fáze (11–13) — tento modul je jen
čte. Autorizace je vždy vynucená v handleru (`require_user`), nikdy jen
skrytím prvku v šabloně — UI podmínky (`can_edit`, `can_admin`) jsou kosmetika
nad touto kontrolou.
"""

from __future__ import annotations

from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import SessionUser, check_owner_or_admin, require_user
from app.db import get_db
from app.models import Application, RecordHistory
from app.schemas import Stav, Tier
from app.templating import templates

router = APIRouter(tags=["registry"])

_EnumT = TypeVar("_EnumT", Stav, Tier)


def _parse_enum_filter(enum_cls: type[_EnumT], raw: str | None) -> _EnumT | None:
    """Převede hodnotu query parametru na člena enumu podle jména.

    Prázdná nebo neznámá hodnota vždy znamená „bez filtru" — nikdy chybu
    (uživatel jen ručně upravil URL).
    """
    if not raw:
        return None
    try:
        return enum_cls[raw]
    except KeyError:
        return None


@router.get("/", response_class=HTMLResponse, name="registry_list")
def list_applications(
    request: Request,
    user: Annotated[SessionUser, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
    stav: Annotated[str | None, Query()] = None,
    tier: Annotated[str | None, Query()] = None,
    zobrazit: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    """Seznam registru (spec kap. 14, obrazovka 2).

    Výchozí pohled: jen `deleted_at IS NULL`. Admin s `?zobrazit=vyrazene`
    vidí místo toho vyřazené záznamy; u ne-admina se parametr tiše ignoruje —
    nikdy nesmí uvidět vyřazený záznam jen podle URL.
    """
    show_deleted = user.is_admin and zobrazit == "vyrazene"

    stav_filter = _parse_enum_filter(Stav, stav)
    tier_filter = _parse_enum_filter(Tier, tier)

    stmt = select(Application).where(
        Application.deleted_at.is_not(None)
        if show_deleted
        else Application.deleted_at.is_(None)
    )
    if stav_filter is not None:
        stmt = stmt.where(Application.stav == stav_filter)
    if tier_filter is not None:
        stmt = stmt.where(Application.klasifikace == tier_filter)
    stmt = stmt.order_by(Application.nazev)

    applications = db.execute(stmt).scalars().all()

    return templates.TemplateResponse(
        request,
        "registry_list.html",
        {
            "user": user,
            "applications": applications,
            "show_deleted": show_deleted,
            "filter_stav": stav_filter,
            "filter_tier": tier_filter,
            "all_stav": list(Stav),
            "all_tier": list(Tier),
        },
    )


@router.get("/aplikace/{application_id}", response_class=HTMLResponse, name="registry_detail")
def application_detail(
    request: Request,
    application_id: str,
    user: Annotated[SessionUser, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    """Detail karty aplikace (spec kap. 14, obrazovka 4)."""
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Aplikace nenalezena."
        )

    # Vyřazený záznam existuje jen pro admina — pro kohokoli jiného 404,
    # aby se přes URL nedalo zjistit, že vůbec existoval.
    if application.deleted_at is not None and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Aplikace nenalezena."
        )

    history = (
        db.execute(
            select(RecordHistory)
            .where(RecordHistory.record_id == application_id)
            .order_by(RecordHistory.kdy.desc())
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request,
        "registry_detail.html",
        {
            "user": user,
            "application": application,
            "history": history,
            # Kosmetika nad backendovou kontrolou (spec kap. 3) — tlačítka se
            # zobrazí jen orientačně, samotné akce (Fáze 12/13) si právo
            # ověří znovu samy.
            "can_edit": check_owner_or_admin(user, application),
            "can_admin": user.is_admin,
        },
    )
