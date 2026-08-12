"""Testy admin akcí: vyřazení, obnovení, překlasifikace (Fáze 13, spec kap. 3,
4.1, 5.5, 15 kritérium 4 a 10).

Stejný izolační vzor jako `test_edit.py`: vlastní in-memory SQLite + override
`get_db` jen pro tento soubor, vlastní testovací router pro podvržení session.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import SESSION_USER_KEY
from app.db import Base, get_db
from app.main import app
from app.models import AiComponent, Application, RecordHistory
from app.schemas import AuditAction, HostingType, Provider, Stav, Tier

# --- in-memory DB sdílená přes všechny requesty v tomto souboru ------------

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def _override_get_db() -> Iterator[Session]:
    db = _TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _admin_db() -> Iterator[None]:
    Base.metadata.create_all(bind=_engine)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=_engine)


# --- podvržení session -------------------------------------------------------

_test_router = APIRouter()


@_test_router.post("/_test/admin/session-login")
async def _session_login(request: Request) -> dict[str, bool]:
    request.session[SESSION_USER_KEY] = await request.json()
    return {"ok": True}


app.include_router(_test_router)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_session() -> None:
    client.cookies.clear()


def _login(username: str, is_admin: bool = False) -> None:
    roles = ["user", "admin"] if is_admin else ["user"]
    response = client.post(
        "/_test/admin/session-login",
        json={"username": username, "email": f"{username}@example.com", "roles": roles},
    )
    assert response.status_code == 200


# --- seed helpers --------------------------------------------------------------

DEFAULT_ODPOVEDI: dict[str, str] = {
    "ucel": "Shrnuje nahrané smlouvy do bodů pro právní tým.",
    "pocet_uzivatelu": "DO_10",
    "kritictnost": "POMOCNY",
    "osobni_udaje": "NE",
    "rozhodovani": "NE",
    "viditelnost": "NE",
    "citlivost": "INTERNI",
    "autonomie": "NE",
    "dopad": "PROVOZNI",
}

#: Odpovědi aktivující policy floor VELKA (AUTO_DECISION_PERSON) — pro test
#: „snížení pod minimum".
VELKA_ODPOVEDI: dict[str, str] = {
    **DEFAULT_ODPOVEDI,
    "rozhodovani": "ROZHODUJE_AUTOMATICKY",
}


def _seed_application(owner: str = "jana.nova", **overrides: Any) -> Application:
    session = _TestSessionLocal()
    try:
        defaults: dict[str, Any] = dict(
            nazev="Sumarizátor smluv",
            popis=DEFAULT_ODPOVEDI["ucel"],
            vlastnik_jmeno="Jana Nová",
            vlastnik_email=f"{owner}@example.com",
            zastupce_jmeno="Petr Svoboda",
            zastupce_email="petr.svoboda@example.com",
            spravce_jmeno="Tomáš Malý",
            spravce_email="tomas.maly@example.com",
            klasifikace_llm=Tier.MALA,
            klasifikace_minimum=Tier.MALA,
            klasifikace=Tier.MALA,
            klasifikace_zduvodneni="Seed.",
            klasifikace_priznaky=[],
            klasifikace_potvrdil=owner,
            dotaznik_odpovedi=dict(DEFAULT_ODPOVEDI),
            stav=Stav.PILOT,
            review_required=False,
            version=1,
            created_by=owner,
            updated_by=owner,
        )
        defaults.update(overrides)
        entity = Application(**defaults)
        entity.components.append(
            AiComponent(
                provider=Provider.ANTHROPIC,
                model_name="claude-sonnet-5",
                purpose="Sumarizace textu smluv",
                hosting_type=HostingType.EXTERNI_API,
            )
        )
        session.add(entity)
        session.commit()
        application_id = entity.id
    finally:
        session.close()
    return _reload(application_id)


def _reload(application_id: str) -> Application:
    session = _TestSessionLocal()
    try:
        return (
            session.execute(
                select(Application)
                .options(selectinload(Application.components))
                .where(Application.id == application_id)
            )
            .scalars()
            .one()
        )
    finally:
        session.close()


def _history(record_id: str) -> list[RecordHistory]:
    session = _TestSessionLocal()
    try:
        return list(
            session.execute(
                select(RecordHistory).where(RecordHistory.record_id == record_id)
            )
            .scalars()
            .all()
        )
    finally:
        session.close()


# --- formulářoví pomocníci ----------------------------------------------------

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _csrf_token(path: str = "/aplikace/nova") -> str:
    response = client.get(path)
    assert response.status_code == 200
    match = _CSRF_RE.search(response.text)
    assert match is not None, f"Stránka {path} neobsahuje CSRF token"
    return match.group(1)


def _retire(application: Application, **overrides: str) -> Any:
    data = {"reason": "Nahrazeno novým nástrojem.", "version": str(application.version)}
    data.update(overrides)
    data["csrf_token"] = _csrf_token()
    return client.post(
        f"/aplikace/{application.id}/vyradit", data=data, follow_redirects=False
    )


def _restore(application: Application, **overrides: str) -> Any:
    data = {"version": str(application.version)}
    data.update(overrides)
    data["csrf_token"] = _csrf_token()
    return client.post(
        f"/aplikace/{application.id}/obnovit", data=data, follow_redirects=False
    )


def _reclassify(application: Application, tier: str, poznamka: str = "", **overrides: str) -> Any:
    data = {"tier": tier, "poznamka": poznamka, "version": str(application.version)}
    data.update(overrides)
    data["csrf_token"] = _csrf_token()
    return client.post(
        f"/aplikace/{application.id}/preklasifikovat", data=data, follow_redirects=False
    )


# --- vyřazení -------------------------------------------------------------------


def test_user_cannot_retire_record() -> None:
    application = _seed_application(owner="jana.nova")
    _login("petr.jiny")

    response = _retire(application)
    assert response.status_code == 403
    assert _reload(application.id).deleted_at is None


def test_retire_without_reason_leaves_record_untouched() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)

    response = _retire(application, reason="")
    assert response.status_code == 303  # redirect zpět na detail s flash chybou

    unchanged = _reload(application.id)
    assert unchanged.deleted_at is None
    assert unchanged.version == 1
    assert not any(h.action is AuditAction.RETIRE for h in _history(application.id))


def test_retire_with_reason_soft_deletes_and_logs_history() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)

    response = _retire(application, reason="Nahrazeno novým nástrojem.")
    assert response.status_code == 303
    assert response.headers["location"] == f"/aplikace/{application.id}"

    retired = _reload(application.id)
    assert retired.deleted_at is not None
    assert retired.deleted_by == "admin.spravce"
    assert retired.delete_reason == "Nahrazeno novým nástrojem."
    assert retired.version == 2

    retire_entries = [h for h in _history(application.id) if h.action is AuditAction.RETIRE]
    assert len(retire_entries) == 1
    assert retire_entries[0].reason == "Nahrazeno novým nástrojem."

    # Vyřazený záznam je pro běžného usera 404 (nejen mimo aktivní seznam).
    _login("jana.nova")
    detail = client.get(f"/aplikace/{application.id}")
    assert detail.status_code == 404


def test_retire_already_retired_returns_400() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)

    first = _retire(application)
    assert first.status_code == 303

    second = _retire(_reload(application.id))
    assert second.status_code == 400


def test_retire_stale_version_returns_409() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)

    response = _retire(application, version="999")
    assert response.status_code == 409

    unchanged = _reload(application.id)
    assert unchanged.deleted_at is None
    assert unchanged.version == 1


# --- obnovení -------------------------------------------------------------------


def test_restore_clears_soft_delete_and_logs_history() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)
    _retire(application)
    retired = _reload(application.id)

    response = _restore(retired)
    assert response.status_code == 303
    assert response.headers["location"] == f"/aplikace/{application.id}"

    restored = _reload(application.id)
    assert restored.deleted_at is None
    assert restored.deleted_by is None
    assert restored.delete_reason is None
    assert restored.version == 3

    assert any(h.action is AuditAction.RESTORE for h in _history(application.id))


def test_restore_non_retired_record_returns_400() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)

    response = _restore(application)
    assert response.status_code == 400


def test_user_cannot_restore_record() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)
    _retire(application)
    retired = _reload(application.id)

    _login("petr.jiny")
    response = _restore(retired)
    assert response.status_code == 403


def test_restore_stale_version_returns_409() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)
    _retire(application)
    retired = _reload(application.id)

    response = _restore(retired, version="999")
    assert response.status_code == 409

    unchanged = _reload(application.id)
    assert unchanged.deleted_at is not None
    assert unchanged.version == retired.version


# --- překlasifikace ---------------------------------------------------------


def test_reclassify_below_minimum_returns_400_and_leaves_record_unchanged() -> None:
    application = _seed_application(owner="jana.nova", dotaznik_odpovedi=dict(VELKA_ODPOVEDI))
    _login("admin.spravce", is_admin=True)

    # Policy floor pro tyto odpovědi je VELKA (AUTO_DECISION_PERSON) — pokus
    # o MALA musí spadnout na 400 přes `PolicyViolation`, i s poznámkou.
    response = _reclassify(application, Tier.MALA.name, poznamka="Chci to snížit.")
    assert response.status_code == 400

    unchanged = _reload(application.id)
    assert unchanged.klasifikace is Tier.MALA  # seed hodnota, nezměněno
    assert unchanged.version == 1
    assert not any(h.action is AuditAction.CLASSIFY for h in _history(application.id))


def test_reclassify_with_note_saves_new_tier_and_logs_history() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)

    response = _reclassify(
        application, Tier.STREDNI.name, poznamka="Zvýšeno po review governance výboru."
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/aplikace/{application.id}"

    updated = _reload(application.id)
    assert updated.klasifikace is Tier.STREDNI
    assert updated.klasifikace_poznamka == "Zvýšeno po review governance výboru."
    assert updated.klasifikace_potvrdil == "admin.spravce"
    assert updated.version == 2

    classify_entries = [h for h in _history(application.id) if h.action is AuditAction.CLASSIFY]
    assert len(classify_entries) == 1
    assert classify_entries[0].stara_hodnota == "MALA"
    assert classify_entries[0].nova_hodnota == "STREDNI"
    assert classify_entries[0].reason == "Zvýšeno po review governance výboru."


def test_reclassify_admin_can_lower_below_ai_suggestion() -> None:
    """Regresní test hlášeného bugu: admin snižuje z VELKA (AI návrh) na
    STREDNI (nad policy minimem MALA pro výchozí odpovědi). Dřív
    `effective_tier` počítala `max(llm_tier, minimum, manual)`, takže se
    ruční snížení tiše přebilo zpátky na VELKA — poznámka se uložila,
    klasifikace ne. Teď se musí uložit skutečně snížená hodnota."""
    application = _seed_application(
        owner="jana.nova", klasifikace_llm=Tier.VELKA, klasifikace=Tier.VELKA
    )
    _login("admin.spravce", is_admin=True)

    response = _reclassify(
        application, Tier.STREDNI.name, poznamka="AI přecenila riziko, snižuji."
    )
    assert response.status_code == 303

    updated = _reload(application.id)
    assert updated.klasifikace is Tier.STREDNI
    assert updated.klasifikace_poznamka == "AI přecenila riziko, snižuji."
    assert updated.version == 2

    classify_entries = [h for h in _history(application.id) if h.action is AuditAction.CLASSIFY]
    assert len(classify_entries) == 1
    assert classify_entries[0].stara_hodnota == "VELKA"
    assert classify_entries[0].nova_hodnota == "STREDNI"


def test_reclassify_without_note_is_rejected() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)

    response = _reclassify(application, Tier.STREDNI.name, poznamka="")
    assert response.status_code == 400

    unchanged = _reload(application.id)
    assert unchanged.klasifikace is Tier.MALA
    assert unchanged.version == 1


def test_reclassify_retired_record_returns_400() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)
    _retire(application)
    retired = _reload(application.id)

    response = _reclassify(retired, Tier.STREDNI.name, poznamka="Pokus po vyřazení.")
    assert response.status_code == 400


def test_user_cannot_reclassify_record() -> None:
    application = _seed_application(owner="jana.nova")
    _login("petr.jiny")

    response = _reclassify(application, Tier.STREDNI.name, poznamka="Pokus bez práv.")
    assert response.status_code == 403


def test_reclassify_stale_version_returns_409() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)

    response = _reclassify(
        application, Tier.STREDNI.name, poznamka="Zvýšeno.", version="999"
    )
    assert response.status_code == 409

    unchanged = _reload(application.id)
    assert unchanged.klasifikace is Tier.MALA
    assert unchanged.version == 1


# --- CSRF ---------------------------------------------------------------------


def test_admin_post_routes_reject_missing_csrf_token() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)

    retire_without_csrf = client.post(
        f"/aplikace/{application.id}/vyradit",
        data={"reason": "Bez CSRF tokenu.", "version": str(application.version)},
        follow_redirects=False,
    )
    assert retire_without_csrf.status_code == 403

    restore_without_csrf = client.post(
        f"/aplikace/{application.id}/obnovit",
        data={"version": str(application.version)},
        follow_redirects=False,
    )
    assert restore_without_csrf.status_code == 403

    reclassify_without_csrf = client.post(
        f"/aplikace/{application.id}/preklasifikovat",
        data={
            "tier": Tier.STREDNI.name,
            "poznamka": "Bez CSRF tokenu.",
            "version": str(application.version),
        },
        follow_redirects=False,
    )
    assert reclassify_without_csrf.status_code == 403

    unchanged = _reload(application.id)
    assert unchanged.deleted_at is None
    assert unchanged.klasifikace is Tier.MALA
    assert unchanged.version == 1


# --- vykreslení šablon (smoke) -----------------------------------------------


def test_reclassify_form_renders_for_admin() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)

    response = client.get(f"/aplikace/{application.id}/preklasifikovat")
    assert response.status_code == 200
    assert application.nazev in response.text
    assert 'name="tier"' in response.text
    assert 'name="poznamka"' in response.text


def test_reclassify_form_forbidden_for_user() -> None:
    application = _seed_application(owner="jana.nova")
    _login("jana.nova")

    response = client.get(f"/aplikace/{application.id}/preklasifikovat")
    assert response.status_code == 403


def test_detail_page_renders_admin_actions() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)

    response = client.get(f"/aplikace/{application.id}")
    assert response.status_code == 200
    assert f"/aplikace/{application.id}/preklasifikovat" in response.text
    assert f"/aplikace/{application.id}/vyradit" in response.text

    _retire(application)
    retired_page = client.get(f"/aplikace/{application.id}")
    assert retired_page.status_code == 200
    assert f"/aplikace/{application.id}/obnovit" in retired_page.text


# --- žádné fyzické mazání ----------------------------------------------------


def test_no_physical_delete_of_application_records() -> None:
    """Grep kontrola spec kap. 4.1 — `Application`/`AiComponent`/`RecordHistory`
    se nikdy fyzicky nemažou. Jediné povolené volání `delete(...)` je retenční
    purge `llm_audit` v `app/llm/audit.py` (spec kap. 4.4, 13)."""
    app_dir = Path(__file__).resolve().parent.parent / "app"
    pattern = re.compile(r"session\.delete|\.delete\(")

    offending: list[str] = []
    for path in app_dir.rglob("*.py"):
        if path.name == "audit.py" and path.parent.name == "llm":
            continue  # povolená retenční purge LlmAudit
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offending.append(f"{path.relative_to(app_dir.parent)}:{lineno}: {line.strip()}")

    assert offending == [], "Nalezeno zakázané mazání záznamů:\n" + "\n".join(offending)
