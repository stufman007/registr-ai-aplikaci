"""Testy číselníku oddělení a checkboxu „vlastník = technický správce"
(spec kap. 4.5, 12; PO zadání „číselník oddělení + checkbox správce=vlastník").

Stejný izolační vzor jako `test_admin.py`/`test_wizard.py`: vlastní in-memory
SQLite + override `get_db` jen pro tento soubor, vlastní testovací router pro
podvržení session.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import SESSION_USER_KEY
from app.db import Base, get_db
from app.llm.gateway import ClassifyOutcome, DuplicatesOutcome
from app.main import app
from app.models import AiComponent, Application, Department, RecordHistory
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
def _departments_db() -> Iterator[None]:
    Base.metadata.create_all(bind=_engine)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=_engine)


# --- podvržení session -------------------------------------------------------

_test_router = APIRouter()


@_test_router.post("/_test/departments/session-login")
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
        "/_test/departments/session-login",
        json={"username": username, "email": f"{username}@example.com", "roles": roles},
    )
    assert response.status_code == 200


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Výchozí stav: AI navrhne MALA a nenajde žádnou duplicitu."""

    def _fake_classify(*_args: Any, **_kwargs: Any) -> ClassifyOutcome:
        return ClassifyOutcome(
            llm_tier=Tier.MALA, zduvodneni="Testovací zdůvodnění.", fallback_used=False
        )

    def _fake_duplicates(*_args: Any, **_kwargs: Any) -> DuplicatesOutcome:
        return DuplicatesOutcome(matches=[], fallback_used=False)

    monkeypatch.setattr(classification, "_classify", _fake_classify)
    monkeypatch.setattr(classification, "_find_duplicates", _fake_duplicates)


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


def _seed_department(nazev: str, aktivni: bool = True) -> Department:
    session = _TestSessionLocal()
    try:
        department = Department(nazev=nazev, aktivni=aktivni)
        session.add(department)
        session.commit()
        session.refresh(department)
        return department
    finally:
        session.close()


def _seed_application(owner: str = "jana.nova", **overrides: Any) -> Application:
    session = _TestSessionLocal()
    try:
        defaults: dict[str, Any] = dict(
            nazev="Sumarizátor smluv",
            popis=DEFAULT_ODPOVEDI["ucel"],
            vlastnik_jmeno="Jana Nová",
            vlastnik_email=f"{owner}@example.com",
            vlastnik_oddeleni="IT",
            zastupce_jmeno="Petr Svoboda",
            zastupce_email="petr.svoboda@example.com",
            zastupce_oddeleni="IT",
            spravce_jmeno="Tomáš Malý",
            spravce_email="tomas.maly@example.com",
            spravce_oddeleni="IT",
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


def _wizard_payload(**overrides: str) -> dict[str, str]:
    payload = {
        "nazev": "Testovací aplikace",
        "stav": Stav.VYVOJ.name,
        "vlastnik_jmeno": "Jana Nová",
        "vlastnik_email": "jana.nova@example.com",
        "vlastnik_oddeleni": "",
        "zastupce_jmeno": "Petr Svoboda",
        "zastupce_email": "petr.svoboda@example.com",
        "zastupce_oddeleni": "",
        "spravce_jmeno": "Tomáš Malý",
        "spravce_email": "tomas.maly@example.com",
        "spravce_oddeleni": "",
        "komponenta_provider": "ANTHROPIC",
        "komponenta_model_name": "claude-sonnet-5",
        "komponenta_purpose": "Sumarizace textu smluv",
        "komponenta_hosting_type": "EXTERNI_API",
        "ucel": DEFAULT_ODPOVEDI["ucel"],
        "pocet_uzivatelu": DEFAULT_ODPOVEDI["pocet_uzivatelu"],
        "kritictnost": DEFAULT_ODPOVEDI["kritictnost"],
        "osobni_udaje": DEFAULT_ODPOVEDI["osobni_udaje"],
        "rozhodovani": DEFAULT_ODPOVEDI["rozhodovani"],
        "viditelnost": DEFAULT_ODPOVEDI["viditelnost"],
        "citlivost": DEFAULT_ODPOVEDI["citlivost"],
        "autonomie": DEFAULT_ODPOVEDI["autonomie"],
        "dopad": DEFAULT_ODPOVEDI["dopad"],
    }
    payload.update(overrides)
    return payload


def _submit_wizard(**overrides: str) -> Any:
    data = _wizard_payload(**overrides)
    data["csrf_token"] = _csrf_token()
    return client.post("/aplikace/nova", data=data, follow_redirects=False)


def _add_department(nazev: str) -> Any:
    return client.post(
        "/admin/oddeleni",
        data={"csrf_token": _csrf_token(), "nazev": nazev},
        follow_redirects=False,
    )


def _deactivate_department(department: Department) -> Any:
    return client.post(
        f"/admin/oddeleni/{department.id}/deaktivovat",
        data={"csrf_token": _csrf_token()},
        follow_redirects=False,
    )


# --- admin správa oddělení ----------------------------------------------------


def test_non_admin_gets_403_on_department_page() -> None:
    _login("jana.nova")
    response = client.get("/admin/oddeleni")
    assert response.status_code == 403


def test_admin_adds_department_and_it_appears_in_wizard_dropdown() -> None:
    _login("admin.spravce", is_admin=True)
    response = _add_department("IT")
    assert response.status_code == 303

    departments_page = client.get("/admin/oddeleni")
    assert "IT" in departments_page.text

    form = client.get("/aplikace/nova")
    assert form.status_code == 200
    assert '<option value="IT"' in form.text


def test_duplicate_department_name_returns_error() -> None:
    _login("admin.spravce", is_admin=True)
    assert _add_department("IT").status_code == 303

    response = _add_department("IT")
    assert response.status_code == 400
    assert "už v číselníku existuje" in response.text


def test_deactivated_department_missing_from_dropdown_but_visible_on_detail() -> None:
    department = _seed_department("Legacy")
    application = _seed_application(
        owner="jana.nova",
        vlastnik_oddeleni="Legacy",
        zastupce_oddeleni="Legacy",
        spravce_oddeleni="Legacy",
    )
    _login("admin.spravce", is_admin=True)

    response = _deactivate_department(department)
    assert response.status_code == 303

    form = client.get("/aplikace/nova")
    assert 'value="Legacy"' not in form.text

    detail = client.get(f"/aplikace/{application.id}")
    assert detail.status_code == 200
    assert "Legacy" in detail.text


# --- checkbox „vlastník je zároveň technický správce" ------------------------


def test_checked_checkbox_copies_owner_into_manager_fields() -> None:
    _seed_department("IT")
    _login("jana.nova")

    response = _submit_wizard(
        vlastnik_oddeleni="IT",
        zastupce_oddeleni="IT",
        spravce_je_vlastnik="1",
        spravce_jmeno="",
        spravce_email="",
        spravce_oddeleni="",
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/aplikace/nova/klasifikace"

    client.get("/aplikace/nova/klasifikace")
    saved = client.post(
        "/aplikace/nova/ulozit",
        data={"csrf_token": _csrf_token(), "tier": Tier.MALA.name, "poznamka": ""},
        follow_redirects=False,
    )
    assert saved.status_code == 303

    record = _TestSessionLocal().execute(select(Application)).scalars().one()
    assert record.spravce_jmeno == "Jana Nová"
    assert record.spravce_email == "jana.nova@example.com"
    assert record.spravce_oddeleni == "IT"


def test_unchecked_checkbox_requires_manager_fields() -> None:
    _login("jana.nova")

    response = _submit_wizard(spravce_jmeno="", spravce_email="")
    assert response.status_code == 400
    assert "Technický správce" in response.text


def _input_tag(html: str, name: str) -> str:
    match = re.search(rf'<input[^>]*name="{name}"[^>]*>', html)
    assert match is not None, f"Pole {name} nenalezeno ve formuláři"
    return match.group(0)


def test_default_checked_checkbox_omits_required_on_hidden_manager_fields() -> None:
    """Regrese: skryté `required` pole nesmí blokovat odeslání formuláře.
    Spoléhat jen na to, že prohlížeč vyřadí required pole se skrytým předkem
    z constraint validation, není napříč prohlížeči spolehlivé — server proto
    `required` na skrytých polích správce rovnou nevykresluje (viz
    `app_form.html`, `spravce_required`). Výchozí stav nového formuláře má
    checkbox zaškrtnutý (sekce správce skrytá)."""
    _login("jana.nova")

    form = client.get("/aplikace/nova")
    assert form.status_code == 200
    assert "required" not in _input_tag(form.text, "spravce_jmeno")
    assert "required" not in _input_tag(form.text, "spravce_email")
    # Vlastník a zástupce jsou vždy viditelní a povinní, bez ohledu na checkbox.
    assert "required" in _input_tag(form.text, "vlastnik_jmeno")
    assert "required" in _input_tag(form.text, "zastupce_email")


def test_error_rerender_keeps_required_in_sync_with_submitted_checkbox() -> None:
    """Po chybném submitu (400 — jiné pole je špatně) se skrytost sekce
    správce i `required` na jejích polích musí řídit PRÁVĚ ODESLANÝM stavem
    checkboxu, ne serverovým defaultem. Jinak by po opravě chyby a druhém
    kliknutí na „Pokračovat“ formulář dál obsahoval skryté required pole se
    starým (nesprávným) stavem a odeslání by v prohlížečích, které tohle
    nehlídají korektně, tiše neproběhlo."""
    _login("jana.nova")

    # Checkbox zaškrtnutý (spravce skrytý) + chyba jinde -> 400, sekce
    # správce musí zůstat skrytá A required-free navzdory prázdným polím.
    response = _submit_wizard(
        spravce_je_vlastnik="1",
        spravce_jmeno="",
        spravce_email="",
        vlastnik_email="not-an-email",
    )
    assert response.status_code == 400
    assert "required" not in _input_tag(response.text, "spravce_jmeno")
    assert "required" not in _input_tag(response.text, "spravce_email")

    # Checkbox odškrtnutý (spravce viditelný) + prázdná pole správce -> 400,
    # required se musí vrátit, aby uživatel dostal okamžitou zpětnou vazbu.
    response = _submit_wizard(spravce_jmeno="", spravce_email="")
    assert response.status_code == 400
    assert "required" in _input_tag(response.text, "spravce_jmeno")
    assert "required" in _input_tag(response.text, "spravce_email")


def test_empty_department_registry_wizard_still_works() -> None:
    """Prázdný číselník: pole oddělení je jen volitelné, formulář nesmí
    zablokovat založení záznamu (graceful degradation, spec kap. 4.5)."""
    _login("jana.nova")

    form = client.get("/aplikace/nova")
    assert form.status_code == 200
    assert "Nejsou definována žádná oddělení" in form.text

    response = _submit_wizard()
    assert response.status_code == 303
    assert response.headers["location"] == "/aplikace/nova/klasifikace"

    client.get("/aplikace/nova/klasifikace")
    saved = client.post(
        "/aplikace/nova/ulozit",
        data={"csrf_token": _csrf_token(), "tier": Tier.MALA.name, "poznamka": ""},
        follow_redirects=False,
    )
    assert saved.status_code == 303


# --- editace: změna oddělení v historii --------------------------------------


def test_department_change_on_edit_is_recorded_in_history() -> None:
    _seed_department("IT")
    _seed_department("Finance")
    application = _seed_application(owner="jana.nova", vlastnik_oddeleni="IT")
    _login("jana.nova")

    payload = {
        "nazev": application.nazev,
        "stav": application.stav.name,
        "vlastnik_jmeno": application.vlastnik_jmeno,
        "vlastnik_email": application.vlastnik_email,
        "vlastnik_oddeleni": "Finance",
        "zastupce_jmeno": application.zastupce_jmeno,
        "zastupce_email": application.zastupce_email,
        "zastupce_oddeleni": "IT",
        "spravce_jmeno": application.spravce_jmeno,
        "spravce_email": application.spravce_email,
        "spravce_oddeleni": "IT",
        "komponenta_provider": application.components[0].provider.name,
        "komponenta_model_name": application.components[0].model_name,
        "komponenta_purpose": application.components[0].purpose,
        "komponenta_hosting_type": application.components[0].hosting_type.name,
        "ucel": DEFAULT_ODPOVEDI["ucel"],
        "pocet_uzivatelu": DEFAULT_ODPOVEDI["pocet_uzivatelu"],
        "kritictnost": DEFAULT_ODPOVEDI["kritictnost"],
        "osobni_udaje": DEFAULT_ODPOVEDI["osobni_udaje"],
        "rozhodovani": DEFAULT_ODPOVEDI["rozhodovani"],
        "viditelnost": DEFAULT_ODPOVEDI["viditelnost"],
        "citlivost": DEFAULT_ODPOVEDI["citlivost"],
        "autonomie": DEFAULT_ODPOVEDI["autonomie"],
        "dopad": DEFAULT_ODPOVEDI["dopad"],
        "version": str(application.version),
        "csrf_token": _csrf_token(),
    }
    response = client.post(
        f"/aplikace/{application.id}/upravit", data=payload, follow_redirects=False
    )
    assert response.status_code == 303

    updated = _reload(application.id)
    assert updated.vlastnik_oddeleni == "Finance"

    entries = [h for h in _history(application.id) if h.pole == "vlastnik_oddeleni"]
    assert len(entries) == 1
    assert entries[0].action is AuditAction.UPDATE
    assert entries[0].stara_hodnota == "IT"
    assert entries[0].nova_hodnota == "Finance"
