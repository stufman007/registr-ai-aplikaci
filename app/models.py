"""SQLAlchemy modely registru (spec kap. 4.1–4.4).

Styl: SQLAlchemy 2.x `Mapped`/`mapped_column`. Enumy z `app.schemas` se ukládají
přes `sqlalchemy.Enum`, který pro PEP 435 enum třídy defaultně ukládá `.name`
(`Tier.MALA` → text `"MALA"`) — čitelné v DB, funguje i pro `Tier` (IntEnum).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.schemas import AuditAction, HostingType, LlmPurpose, Provider, Stav, Tier


def _uuid_hex() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(primary_key=True, default=_uuid_hex)

    nazev: Mapped[str] = mapped_column(nullable=False)
    popis: Mapped[str] = mapped_column(Text, nullable=False)

    vlastnik_jmeno: Mapped[str] = mapped_column(nullable=False)
    vlastnik_email: Mapped[str] = mapped_column(nullable=False)
    # `*_oddeleni` jsou záměrně obyčejný text, NE FK na `Department.id` (spec
    # kap. 4.5). Číselník oddělení je jen pomocník pro konzistentní vstup do
    # roletky formuláře — historická hodnota na kartě aplikace nesmí zmizet
    # ani se změnit, když admin položku číselníku přejmenuje nebo deaktivuje.
    # Nullable kvůli starým záznamům založeným před touto verzí.
    vlastnik_oddeleni: Mapped[str | None] = mapped_column(nullable=True)
    zastupce_jmeno: Mapped[str] = mapped_column(nullable=False)
    zastupce_email: Mapped[str] = mapped_column(nullable=False)
    zastupce_oddeleni: Mapped[str | None] = mapped_column(nullable=True)
    spravce_jmeno: Mapped[str] = mapped_column(nullable=False)
    spravce_email: Mapped[str] = mapped_column(nullable=False)
    spravce_oddeleni: Mapped[str | None] = mapped_column(nullable=True)

    # Původní návrh LLM (nebo fallback baseline). Nullable — před první
    # klasifikací (nebo pokud fallback nezapsal nic) nemusí existovat.
    klasifikace_llm: Mapped[Tier | None] = mapped_column(SqlEnum(Tier), nullable=True)
    klasifikace_minimum: Mapped[Tier] = mapped_column(SqlEnum(Tier), nullable=False)
    klasifikace: Mapped[Tier] = mapped_column(SqlEnum(Tier), nullable=False)
    klasifikace_zduvodneni: Mapped[str] = mapped_column(Text, nullable=False)
    klasifikace_priznaky: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    klasifikace_potvrdil: Mapped[str] = mapped_column(nullable=False)
    klasifikace_poznamka: Mapped[str | None] = mapped_column(Text, nullable=True)

    dotaznik_odpovedi: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    stav: Mapped[Stav] = mapped_column(SqlEnum(Stav), nullable=False, default=Stav.VYVOJ)
    review_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1)

    created_by: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_by: Mapped[str] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_by: Mapped[str | None] = mapped_column(nullable=True)
    delete_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    components: Mapped[list["AiComponent"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class AiComponent(Base):
    __tablename__ = "ai_components"

    id: Mapped[str] = mapped_column(primary_key=True, default=_uuid_hex)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id"), nullable=False
    )

    provider: Mapped[Provider] = mapped_column(SqlEnum(Provider), nullable=False)
    model_name: Mapped[str] = mapped_column(nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    hosting_type: Mapped[HostingType] = mapped_column(SqlEnum(HostingType), nullable=False)

    application: Mapped["Application"] = relationship(back_populates="components")


class RecordHistory(Base):
    """Append-only historie (spec kap. 4.3).

    Záměrně BEZ FK na `applications.id` a bez relationship/kaskády — historie
    musí přežít cokoli, co se se zdrojovým záznamem stane (soft delete i
    případnou budoucí migraci schématu aplikací).
    """

    __tablename__ = "record_history"

    id: Mapped[str] = mapped_column(primary_key=True, default=_uuid_hex)
    record_id: Mapped[str] = mapped_column(index=True, nullable=False)
    actor: Mapped[str] = mapped_column(nullable=False)
    kdy: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    action: Mapped[AuditAction] = mapped_column(SqlEnum(AuditAction), nullable=False)
    pole: Mapped[str | None] = mapped_column(nullable=True)
    stara_hodnota: Mapped[str | None] = mapped_column(Text, nullable=True)
    nova_hodnota: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class LlmAudit(Base):
    """Metadata volání LLM (spec kap. 4.4). Nikdy prompt ani odpověď — jen
    metadata + verze promptu/pravidel pro reprodukovatelnost."""

    __tablename__ = "llm_audit"

    id: Mapped[str] = mapped_column(primary_key=True, default=_uuid_hex)
    kdy: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    ucel: Mapped[LlmPurpose] = mapped_column(SqlEnum(LlmPurpose), nullable=False)
    provider: Mapped[str] = mapped_column(nullable=False)
    model: Mapped[str] = mapped_column(nullable=False)
    prompt_version: Mapped[str] = mapped_column(nullable=False)
    rules_version: Mapped[str] = mapped_column(nullable=False)
    tokens_in: Mapped[int] = mapped_column(nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(nullable=False, default=0)
    latence_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    uspech: Mapped[bool] = mapped_column(nullable=False)
    error_code: Mapped[str | None] = mapped_column(nullable=True)
    fallback_used: Mapped[bool] = mapped_column(nullable=False, default=False)


class Department(Base):
    """Číselník oddělení (spec kap. 4.5) — spravuje admin (`/admin/oddeleni`).

    Deaktivace nahrazuje mazání (konzistentní s no-hard-delete filozofií
    zbytku aplikace): `aktivni=False` položku jen vyřadí z roletky formuláře,
    historické záznamy s touto hodnotou zůstávají beze změny — právě proto
    `Application.*_oddeleni` NENÍ FK na tuto tabulku, viz poznámka u `Application`.
    """

    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(primary_key=True, default=_uuid_hex)
    nazev: Mapped[str] = mapped_column(unique=True, nullable=False)
    aktivni: Mapped[bool] = mapped_column(nullable=False, default=True)
