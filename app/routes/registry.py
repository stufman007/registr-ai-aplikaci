"""Registr: seznam a detail karty aplikace (Fáze 10, spec kap. 12 UI obrazovky 2 a 4).

Založení/editace/vyřazení jsou pozdější fáze (11–13) — tento modul je jen
čte. Autorizace je vždy vynucená v handleru (`require_user`), nikdy jen
skrytím prvku v šabloně — UI podmínky (`can_edit`, `can_admin`) jsou kosmetika
nad touto kontrolou.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Annotated, TypeVar
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import String, func, or_, select
from sqlalchemy.orm import Session

from app.auth import SessionUser, check_owner_or_admin, require_user
from app.db import get_db
from app.models import AiComponent, Application, RecordHistory
from app.schemas import Provider, Stav, Tier
from app.services.regulatory import ALLOWED_FLAGS
from app.templating import templates

router = APIRouter(tags=["registry"])

_EnumT = TypeVar("_EnumT", Stav, Tier, Provider)

#: Max. řádků na stránku seznamu registru.
PAGE_SIZE = 20

#: Max. délka textových filtrů (název, vlastník) — jen ochrana proti
#: nesmyslně dlouhému query stringu, ne bezpečnostní opatření samo o sobě.
MAX_TEXT_FILTER_LEN = 100


def _parse_enum_filter_multi(enum_cls: type[_EnumT], raw: list[str]) -> tuple[_EnumT, ...]:
    """Převede opakované hodnoty query parametru na členy enumu (multiple choice).

    Uvnitř sloupce se hodnoty kombinují přes OR (viz `.in_()` v dotazu).
    Neznámá hodnota se tiše zahodí, zbytek platí — nikdy chyba (uživatel jen
    ručně upravil URL). Duplicity se odstraní, pořadí prvního výskytu se
    zachová (stabilní pro render checkboxů i pro `to_query_params`).
    """
    result: list[_EnumT] = []
    seen: set[_EnumT] = set()
    for value in raw:
        try:
            member = enum_cls[value]
        except KeyError:
            continue
        if member not in seen:
            seen.add(member)
            result.append(member)
    return tuple(result)


def _parse_text_filter(raw: str | None) -> str | None:
    """Ořízne textový filtr (contains) na `MAX_TEXT_FILTER_LEN` znaků.

    Prázdný (po stripu) → bez filtru.
    """
    if raw is None:
        return None
    trimmed = raw.strip()[:MAX_TEXT_FILTER_LEN]
    return trimmed or None


def _parse_signal_filter_multi(raw: list[str]) -> tuple[str, ...]:
    """Validuje opakované hodnoty `signal` proti whitelistu (`ALLOWED_FLAGS`).

    Stejná konvence jako `_parse_enum_filter_multi`: neznámá hodnota se
    zahodí, zbytek platí, duplicity se odstraní se zachováním pořadí.
    """
    result: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if value in ALLOWED_FLAGS and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _escape_like(value: str) -> str:
    """Escapuje SQL LIKE speciální znaky (`%`, `_`) v uživatelském vstupu.

    Bez escapu by název aplikace obsahující např. `%` fungoval jako
    wildcard a filtr by nechtěně matchoval libovolný záznam.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_page(raw: str | None) -> int:
    """Převede `strana` z query stringu na kladné celé číslo.

    Neplatná nebo chybějící hodnota → strana 1 (nikdy chyba — uživatel jen
    ručně upravil URL). Horní mez (počet stránek) se ořezává až po zjištění
    celkového počtu záznamů, viz `list_applications`.
    """
    if not raw:
        return 1
    try:
        page = int(raw)
    except ValueError:
        return 1
    return page if page >= 1 else 1


@dataclass(frozen=True)
class RegistryFilters:
    """Aktivní filtry seznamu registru — jeden objekt místo šesti parametrů
    protahovaných přes query, statement builder i paginaci.

    `stav`, `tier`, `signal`, `provider` jsou multiple choice (výběr více
    hodnot v jednom sloupci se kombinuje přes OR); mezi sloupci navzájem
    platí AND — beze změny oproti jednohodnotové verzi filtrů.
    """

    stav: tuple[Stav, ...] = ()
    tier: tuple[Tier, ...] = ()
    nazev: str | None = None
    vlastnik: str | None = None
    signal: tuple[str, ...] = ()
    provider: tuple[Provider, ...] = ()

    def is_active(self) -> bool:
        return any(
            (self.stav, self.tier, self.nazev, self.vlastnik, self.signal, self.provider)
        )

    def to_query_params(self) -> list[tuple[str, str]]:
        params: list[tuple[str, str]] = []
        for s in self.stav:
            params.append(("stav", s.name))
        for t in self.tier:
            params.append(("tier", t.name))
        if self.nazev:
            params.append(("nazev", self.nazev))
        if self.vlastnik:
            params.append(("vlastnik", self.vlastnik))
        for sig in self.signal:
            params.append(("signal", sig))
        for p in self.provider:
            params.append(("provider", p.name))
        return params


def _signal_filter_clause(signal: str):
    """LIKE filtr přes `klasifikace_priznaky` (JSON sloupec, spec kap. 5.4).

    SQLite nemá nativní JSON typ — SQLAlchemy JSON sloupec se serializuje
    přes `json.dumps` s výchozími separátory (`", "` / `": "`), takže
    zkratka signálu se v uloženém textu vždy objeví přesně jako
    `"zkratka": "GDPR"`. Cast sloupce na text + LIKE na tenhle vzor je
    pragmatické řešení bez závislosti na `json_each`/`JSON_EXTRACT` (které se
    mezi SQLite/Postgres chovají různě) — funguje pro tři pevně dané
    zkratky (`ALLOWED_FLAGS`), `signal` je navíc validovaný proti
    whitelistu (`_parse_signal_filter`), takže LIKE escapování zde není
    potřeba. Limit: string match na serializovaný tvar, ne strukturální
    JSON dotaz — dostatečné pro tenhle rozsah dat, ne obecně přenosné.
    """
    pattern = f'%"zkratka": "{signal}"%'
    return func.cast(Application.klasifikace_priznaky, String).like(pattern)


def _provider_filter_clause(providers: tuple[Provider, ...]):
    """EXISTS subquery: aplikace má aspoň jednu komponentu s providerem
    z vybrané množiny (`IN`, tedy OR mezi vybranými providery)."""
    return (
        select(AiComponent.id)
        .where(
            AiComponent.application_id == Application.id,
            AiComponent.provider.in_(providers),
        )
        .exists()
    )


def _pagination_query(filters: RegistryFilters, show_deleted: bool, page: int) -> str:
    """Query string pro odkaz „Předchozí"/„Další" — zachovává aktivní filtry."""
    params = filters.to_query_params()
    if show_deleted:
        params.append(("zobrazit", "vyrazene"))
    params.append(("strana", str(page)))
    return urlencode(params)


@router.get("/", response_class=HTMLResponse, name="registry_list")
def list_applications(
    request: Request,
    user: Annotated[SessionUser, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
    stav: Annotated[list[str], Query()] = [],  # noqa: B006 — FastAPI čte, nemutuje
    tier: Annotated[list[str], Query()] = [],  # noqa: B006
    nazev: Annotated[str | None, Query()] = None,
    vlastnik: Annotated[str | None, Query()] = None,
    signal: Annotated[list[str], Query()] = [],  # noqa: B006
    provider: Annotated[list[str], Query()] = [],  # noqa: B006
    zobrazit: Annotated[str | None, Query()] = None,
    strana: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    """Seznam registru (spec kap. 14, obrazovka 2) — per-sloupcové filtry.

    Výchozí pohled: jen `deleted_at IS NULL`. Admin s `?zobrazit=vyrazene`
    vidí místo toho vyřazené záznamy; u ne-admina se parametr tiše ignoruje —
    nikdy nesmí uvidět vyřazený záznam jen podle URL.

    Filtry (všechny volitelné; mezi sloupci se kombinují přes AND):
    - `nazev`, `vlastnik` — textové „obsahuje" (case-insensitive), `vlastnik`
      hledá v `vlastnik_jmeno` i `vlastnik_email`. Jednohodnotové.
    - `stav`, `tier`, `provider`, `signal` — multiple choice: opakovaný query
      parametr (`?tier=MALA&tier=VELKA`), uvnitř sloupce se hodnoty
      kombinují přes OR. Neznámá hodnota se tiše zahodí, zbytek platí
      (uživatel jen ručně upravil URL, nikdy chyba). Jedna hodnota funguje
      stejně jako dřív (zpětná kompatibilita).
    - `signal` — GDPR/AI-ACT/DORA přes `klasifikace_priznaky` (JSON sloupec),
      viz `_signal_filter_clause`.

    Stránkováno po `PAGE_SIZE` řádcích (`?strana=`, 1-based). Stránka mimo
    rozsah (moc velká i neplatná hodnota) se tiše ořeže na poslední platnou.
    """
    show_deleted = user.is_admin and zobrazit == "vyrazene"

    filters = RegistryFilters(
        stav=_parse_enum_filter_multi(Stav, stav),
        tier=_parse_enum_filter_multi(Tier, tier),
        nazev=_parse_text_filter(nazev),
        vlastnik=_parse_text_filter(vlastnik),
        signal=_parse_signal_filter_multi(signal),
        provider=_parse_enum_filter_multi(Provider, provider),
    )

    stmt = select(Application).where(
        Application.deleted_at.is_not(None)
        if show_deleted
        else Application.deleted_at.is_(None)
    )
    if filters.stav:
        stmt = stmt.where(Application.stav.in_(filters.stav))
    if filters.tier:
        stmt = stmt.where(Application.klasifikace.in_(filters.tier))
    if filters.nazev:
        stmt = stmt.where(
            Application.nazev.ilike(f"%{_escape_like(filters.nazev)}%", escape="\\")
        )
    if filters.vlastnik:
        pattern = f"%{_escape_like(filters.vlastnik)}%"
        stmt = stmt.where(
            or_(
                Application.vlastnik_jmeno.ilike(pattern, escape="\\"),
                Application.vlastnik_email.ilike(pattern, escape="\\"),
            )
        )
    if filters.signal:
        stmt = stmt.where(or_(*(_signal_filter_clause(sig) for sig in filters.signal)))
    if filters.provider:
        stmt = stmt.where(_provider_filter_clause(filters.provider))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    total_pages = max(1, ceil(total / PAGE_SIZE))
    page = min(_parse_page(strana), total_pages)

    applications = (
        db.execute(
            stmt.order_by(Application.nazev)
            .limit(PAGE_SIZE)
            .offset((page - 1) * PAGE_SIZE)
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request,
        "registry_list.html",
        {
            "user": user,
            "applications": applications,
            "show_deleted": show_deleted,
            "filter_stav": filters.stav,
            "filter_tier": filters.tier,
            "filter_nazev": filters.nazev or "",
            "filter_vlastnik": filters.vlastnik or "",
            "filter_signal": filters.signal,
            "filter_provider": filters.provider,
            "filters_active": filters.is_active(),
            "all_stav": list(Stav),
            "all_tier": list(Tier),
            "all_signal": sorted(ALLOWED_FLAGS),
            "all_provider": list(Provider),
            "page": page,
            "total_pages": total_pages,
            "prev_query": (
                _pagination_query(filters, show_deleted, page - 1) if page > 1 else None
            ),
            "next_query": (
                _pagination_query(filters, show_deleted, page + 1)
                if page < total_pages
                else None
            ),
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
