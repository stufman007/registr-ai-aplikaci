"""Testy immutable historie a datového modelu (F5, spec kap. 4.3, 16)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import Application, LlmAudit, RecordHistory
from app.schemas import AuditAction, LlmPurpose, Tier
from app.services.history import diff_fields, log


@pytest.fixture()
def db_session() -> Iterator[Session]:
    """Vlastní in-memory SQLite engine — nezávislý na `settings.database_url`."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_application(**overrides: object) -> Application:
    defaults = dict(
        nazev="Testovací aplikace",
        popis="Popis pro testy historie.",
        vlastnik_jmeno="Jana Nová",
        vlastnik_email="jana.nova@example.com",
        zastupce_jmeno="Petr Svoboda",
        zastupce_email="petr.svoboda@example.com",
        spravce_jmeno="Tomáš Malý",
        spravce_email="tomas.maly@example.com",
        klasifikace_minimum=Tier.MALA,
        klasifikace=Tier.MALA,
        klasifikace_zduvodneni="Bez zvláštních rizik.",
        klasifikace_potvrdil="jana.nova",
        dotaznik_odpovedi={},
        created_by="jana.nova",
        updated_by="jana.nova",
    )
    defaults.update(overrides)
    return Application(**defaults)


def test_update_two_fields_writes_two_history_rows_with_old_and_new_value(
    db_session: Session,
) -> None:
    app = _make_application()
    db_session.add(app)
    db_session.commit()

    old = {"nazev": "Testovací aplikace", "stav": "VYVOJ"}
    new = {"nazev": "Přejmenovaná aplikace", "stav": "PILOT"}

    changes = diff_fields(old, new, tracked_fields=["nazev", "stav"])
    assert len(changes) == 2

    for pole, stara, nova in changes:
        log(
            db_session,
            record_id=app.id,
            actor="jana.nova",
            action=AuditAction.UPDATE,
            pole=pole,
            stara=stara,
            nova=nova,
        )
    db_session.commit()

    rows = (
        db_session.query(RecordHistory)
        .filter(RecordHistory.record_id == app.id)
        .order_by(RecordHistory.pole)
        .all()
    )
    assert len(rows) == 2

    by_field = {row.pole: row for row in rows}
    assert by_field["nazev"].stara_hodnota == "Testovací aplikace"
    assert by_field["nazev"].nova_hodnota == "Přejmenovaná aplikace"
    assert by_field["stav"].stara_hodnota == "VYVOJ"
    assert by_field["stav"].nova_hodnota == "PILOT"


def test_retire_without_reason_raises_value_error(db_session: Session) -> None:
    with pytest.raises(ValueError):
        log(
            db_session,
            record_id="some-record-id",
            actor="admin",
            action=AuditAction.RETIRE,
        )


def test_duplicate_override_without_reason_raises_value_error(db_session: Session) -> None:
    with pytest.raises(ValueError):
        log(
            db_session,
            record_id="some-record-id",
            actor="jana.nova",
            action=AuditAction.DUPLICATE_OVERRIDE,
        )


def test_retire_with_reason_succeeds(db_session: Session) -> None:
    entry = log(
        db_session,
        record_id="some-record-id",
        actor="admin",
        action=AuditAction.RETIRE,
        reason="Nahrazeno novým nástrojem.",
    )
    db_session.commit()
    assert entry.id is not None
    assert entry.reason == "Nahrazeno novým nástrojem."


def test_sensitive_field_prompt_text_raises_value_error(db_session: Session) -> None:
    with pytest.raises(ValueError):
        log(
            db_session,
            record_id="some-record-id",
            actor="jana.nova",
            action=AuditAction.UPDATE,
            pole="prompt_text",
            stara="a",
            nova="b",
        )


@pytest.mark.parametrize("blacklisted_field", ["session_id", "auth_token", "api_secret", "llm_response"])
def test_other_sensitive_fields_raise_value_error(
    db_session: Session, blacklisted_field: str
) -> None:
    with pytest.raises(ValueError):
        log(
            db_session,
            record_id="some-record-id",
            actor="jana.nova",
            action=AuditAction.UPDATE,
            pole=blacklisted_field,
            stara="a",
            nova="b",
        )


def test_history_survives_soft_delete(db_session: Session) -> None:
    app = _make_application()
    db_session.add(app)
    db_session.commit()

    log(
        db_session,
        record_id=app.id,
        actor="jana.nova",
        action=AuditAction.CREATE,
    )
    db_session.commit()

    from datetime import datetime, timezone

    app.deleted_at = datetime.now(timezone.utc)
    app.deleted_by = "admin"
    app.delete_reason = "Aplikace se dál nepoužívá."
    log(
        db_session,
        record_id=app.id,
        actor="admin",
        action=AuditAction.RETIRE,
        reason=app.delete_reason,
    )
    db_session.commit()

    rows = (
        db_session.query(RecordHistory)
        .filter(RecordHistory.record_id == app.id)
        .all()
    )
    assert len(rows) == 2
    actions = {row.action for row in rows}
    assert actions == {AuditAction.CREATE, AuditAction.RETIRE}

    reloaded = db_session.get(Application, app.id)
    assert reloaded is not None
    assert reloaded.deleted_at is not None


def test_llm_audit_row_can_be_created(db_session: Session) -> None:
    entry = LlmAudit(
        ucel=LlmPurpose.CLASSIFY,
        provider="mock",
        model="mock-v1",
        prompt_version="2026-08-v1",
        rules_version="2026-08-v1",
        tokens_in=0,
        tokens_out=0,
        latence_ms=5,
        uspech=True,
    )
    db_session.add(entry)
    db_session.commit()
    assert entry.id is not None


def test_llm_audit_has_no_column_for_prompt_content() -> None:
    column_names = set(LlmAudit.__table__.columns.keys())
    forbidden_substrings = ("prompt_text", "response", "content", "output", "obsah")
    for column_name in column_names:
        lowered = column_name.lower()
        for forbidden in forbidden_substrings:
            assert forbidden not in lowered, (
                f"Sloupec '{column_name}' v LlmAudit vypadá jako obsah promptu/odpovědi."
            )
