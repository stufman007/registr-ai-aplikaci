"""Testy editace, optimistic lockingu a review light (Fáze 12, spec kap. 9 a 11.5).

Stejný izolační vzor jako `test_wizard.py`: vlastní in-memory SQLite +
override `get_db` jen pro tento soubor, vlastní testovací router pro
podvržení session. LLM se nevolá po síti — `app.routes.classification`
drží modul-level alias `_classify`, editace ho volá přes `classification.\
_classify(...)`, takže stejné `monkeypatch.setattr(classification,
"_classify", ...)` funguje pro obě cesty (založení i editace).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import SESSION_USER_KEY
from app.db import Base, get_db
from app.llm.gateway import ClassifyOutcome
from app.main import app
from app.models import AiComponent, Application, RecordHistory
from app.routes import classification
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
def _edit_db() -> Iterator[None]:
    Base.metadata.create_all(bind=_engine)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=_engine)


# --- podvržení session -------------------------------------------------------

_test_router = APIRouter()


@_test_router.post("/_test/edit/session-login")
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
        "/_test/edit/session-login",
        json={"username": username, "email": f"{username}@example.com", "roles": roles},
    )
    assert response.status_code == 200


# --- deterministické LLM náhrady --------------------------------------------


def _fake_classify(tier: Tier) -> Any:
    def _call(*_args: Any, **_kwargs: Any) -> ClassifyOutcome:
        return ClassifyOutcome(
            llm_tier=tier, zduvodneni="Testovací zdůvodnění návrhu AI.", fallback_used=False
        )

    return _call


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(classification, "_classify", _fake_classify(Tier.MALA))


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


def _csrf_token(path: str = "/") -> str:
    response = client.get(path)
    assert response.status_code == 200
    match = _CSRF_RE.search(response.text)
    assert match is not None, f"Stránka {path} neobsahuje CSRF token"
    return match.group(1)


def _edit_payload(application: Application, **overrides: str) -> dict[str, str]:
    odpovedi = dict(application.dotaznik_odpovedi)
    component = application.components[0]
    payload = {
        "nazev": application.nazev,
        "stav": application.stav.name,
        "vlastnik_jmeno": application.vlastnik_jmeno,
        "vlastnik_email": application.vlastnik_email,
        "zastupce_jmeno": application.zastupce_jmeno,
        "zastupce_email": application.zastupce_email,
        "spravce_jmeno": application.spravce_jmeno,
        "spravce_email": application.spravce_email,
        "komponenta_provider": component.provider.name,
        "komponenta_model_name": component.model_name,
        "komponenta_purpose": component.purpose,
        "komponenta_hosting_type": component.hosting_type.name,
        "ucel": odpovedi["ucel"],
        "pocet_uzivatelu": odpovedi["pocet_uzivatelu"],
        "kritictnost": odpovedi["kritictnost"],
        "osobni_udaje": odpovedi["osobni_udaje"],
        "rozhodovani": odpovedi["rozhodovani"],
        "viditelnost": odpovedi["viditelnost"],
        "citlivost": odpovedi["citlivost"],
        "autonomie": odpovedi["autonomie"],
        "dopad": odpovedi["dopad"],
        "version": str(application.version),
    }
    payload.update(overrides)
    return payload


def _submit_edit(application: Application, **overrides: str) -> Any:
    # CSRF token je vázaný na session, ne na konkrétní stránku — bere se ze
    # stránky "/aplikace/nova" (dostupná každému přihlášenému), aby test 403
    # (neoprávněný přístup k editaci) fungoval i pro uživatele, který samotný
    # editační formulář vůbec nesmí otevřít.
    data = _edit_payload(application, **overrides)
    data["csrf_token"] = _csrf_token("/aplikace/nova")
    return client.post(
        f"/aplikace/{application.id}/upravit", data=data, follow_redirects=False
    )


def _confirm_classification(application_id: str, tier: str, poznamka: str = "") -> Any:
    return client.post(
        f"/aplikace/{application_id}/upravit/klasifikace",
        data={
            "csrf_token": _csrf_token("/aplikace/nova"),
            "tier": tier,
            "poznamka": poznamka,
        },
        follow_redirects=False,
    )


# --- přístup -------------------------------------------------------------------


def test_owner_can_open_edit_form() -> None:
    application = _seed_application(owner="jana.nova")
    _login("jana.nova")

    response = client.get(f"/aplikace/{application.id}/upravit")
    assert response.status_code == 200
    assert application.nazev in response.text


def test_non_owner_forbidden_for_get_and_post() -> None:
    application = _seed_application(owner="jana.nova")
    _login("petr.jiny")

    get_response = client.get(f"/aplikace/{application.id}/upravit")
    assert get_response.status_code == 403

    post_response = _submit_edit(application, nazev="Pokus o cizí úpravu")
    assert post_response.status_code == 403
    assert _reload(application.id).nazev == "Sumarizátor smluv"


def test_admin_can_edit_foreign_record() -> None:
    application = _seed_application(owner="jana.nova")
    _login("admin.spravce", is_admin=True)

    response = _submit_edit(application, nazev="Admin upravil název")
    assert response.status_code == 303
    assert response.headers["location"] == f"/aplikace/{application.id}"
    assert _reload(application.id).nazev == "Admin upravil název"


def test_retired_record_cannot_be_edited() -> None:
    application = _seed_application(
        owner="jana.nova",
        deleted_at=datetime.now(timezone.utc),
        deleted_by="admin.spravce",
        delete_reason="Nahrazeno novým nástrojem.",
    )
    _login("jana.nova")

    get_response = client.get(f"/aplikace/{application.id}/upravit")
    assert get_response.status_code == 400

    post_response = _submit_edit(application, nazev="Nemělo by projít")
    assert post_response.status_code == 400
    assert _reload(application.id).nazev == "Sumarizátor smluv"


# --- přímá úprava nerizikových polí ------------------------------------------


def test_name_change_saves_directly_and_logs_history() -> None:
    application = _seed_application(owner="jana.nova")
    _login("jana.nova")

    response = _submit_edit(application, nazev="Sumarizátor smluv v2")
    assert response.status_code == 303
    assert response.headers["location"] == f"/aplikace/{application.id}"

    updated = _reload(application.id)
    assert updated.nazev == "Sumarizátor smluv v2"
    assert updated.version == 2

    history_entries = _history(application.id)
    nazev_entries = [h for h in history_entries if h.pole == "nazev"]
    assert len(nazev_entries) == 1
    assert nazev_entries[0].action is AuditAction.UPDATE
    assert nazev_entries[0].stara_hodnota == "Sumarizátor smluv"
    assert nazev_entries[0].nova_hodnota == "Sumarizátor smluv v2"


def test_stale_version_returns_409_and_data_is_not_overwritten() -> None:
    application = _seed_application(owner="jana.nova")
    _login("jana.nova")

    response = _submit_edit(application, nazev="Neměl by se uložit", version="999")
    assert response.status_code == 409

    unchanged = _reload(application.id)
    assert unchanged.nazev == "Sumarizátor smluv"
    assert unchanged.version == 1


# --- review light: rizikové otázky a AI komponenty ---------------------------


def test_component_change_sets_review_required_then_reclassification_clears_it() -> None:
    application = _seed_application(owner="jana.nova")
    _login("jana.nova")

    # 1) Jen změna AI komponenty (nerizikové otázky beze změny) → přímé uložení,
    #    review_required = True, historie s polem "komponenty".
    response = _submit_edit(
        application,
        komponenta_model_name="claude-opus-5",
        komponenta_purpose="Sumarizace a extrakce klíčových bodů ze smluv",
    )
    assert response.status_code == 303

    after_component_change = _reload(application.id)
    assert after_component_change.review_required is True
    assert after_component_change.version == 2
    assert after_component_change.components[0].model_name == "claude-opus-5"

    komponenty_entries = [h for h in _history(application.id) if h.pole == "komponenty"]
    assert len(komponenty_entries) == 1
    assert komponenty_entries[0].action is AuditAction.UPDATE

    # 2) Změna rizikové odpovědi (ot. 5 — rozhodování o lidech) → redirect na
    #    klasifikační krok, přímé uložení je zakázané.
    risky_response = _submit_edit(after_component_change, rozhodovani="DOPORUCUJE")
    assert risky_response.status_code == 303
    assert risky_response.headers["location"] == f"/aplikace/{application.id}/upravit/klasifikace"

    classify_page = client.get(f"/aplikace/{application.id}/upravit/klasifikace")
    assert classify_page.status_code == 200
    assert "Testovací zdůvodnění návrhu AI." in classify_page.text

    confirm = _confirm_classification(application.id, Tier.MALA.name)
    assert confirm.status_code == 303
    assert confirm.headers["location"] == f"/aplikace/{application.id}"

    reclassified = _reload(application.id)
    assert reclassified.review_required is False
    assert reclassified.klasifikace is Tier.MALA
    assert reclassified.dotaznik_odpovedi["rozhodovani"] == "DOPORUCUJE"
    assert reclassified.klasifikace_potvrdil == "jana.nova"

    actions = [h.action for h in _history(application.id)]
    assert AuditAction.CLASSIFY in actions


def test_admin_can_lower_tier_below_ai_suggestion_during_reclassification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresní test hlášeného bugu: re-klasifikace v editaci sdílí
    `effective_tier` s wizardem zakládání (`app.routes.classification`) i s
    admin překlasifikací (`app.routes.admin`) — admin tu musí umět snížit AI
    návrh VELKA na STREDNI (nad minimem MALA), ne dostat ho tiše zpět."""
    application = _seed_application(owner="jana.nova")
    monkeypatch.setattr(classification, "_classify", _fake_classify(Tier.VELKA))
    _login("admin.spravce", is_admin=True)

    risky = _submit_edit(application, rozhodovani="DOPORUCUJE")
    assert risky.status_code == 303
    assert risky.headers["location"] == f"/aplikace/{application.id}/upravit/klasifikace"

    client.get(f"/aplikace/{application.id}/upravit/klasifikace")
    confirm = _confirm_classification(
        application.id, Tier.STREDNI.name, poznamka="AI přecenila riziko, snižuji."
    )
    assert confirm.status_code == 303

    updated = _reload(application.id)
    assert updated.klasifikace is Tier.STREDNI
    assert updated.klasifikace_poznamka == "AI přecenila riziko, snižuji."


def test_risky_answer_change_requires_reclassification_before_save() -> None:
    application = _seed_application(owner="jana.nova")
    _login("jana.nova")

    response = _submit_edit(application, kritictnost="KRITICKY")
    assert response.status_code == 303
    assert response.headers["location"] == f"/aplikace/{application.id}/upravit/klasifikace"

    # Přímé uložení se nekonalo — v DB je stále původní odpověď a verze.
    untouched = _reload(application.id)
    assert untouched.dotaznik_odpovedi["kritictnost"] == "POMOCNY"
    assert untouched.version == 1

    confirm = _confirm_classification(application.id, Tier.STREDNI.name)
    assert confirm.status_code == 303

    updated = _reload(application.id)
    assert updated.dotaznik_odpovedi["kritictnost"] == "KRITICKY"
    assert updated.klasifikace is Tier.STREDNI
    assert updated.klasifikace_minimum is Tier.STREDNI
    assert updated.version == 2
