"""Testy wizardu zakládání záznamu (Fáze 11, spec kap. 5, 6 a 14 obrazovka 3).

Vzor izolace je stejný jako v `test_registry_routes.py`: vlastní in-memory
SQLite přes `StaticPool` + override `get_db` jen po dobu tohoto souboru a
vlastní testovací router pro podvržení session.

LLM se nevolá po síti. `app.routes.classification` drží modul-level aliasy
`_classify` / `_find_duplicates`, takže se dají deterministicky podvrhnout
přes `monkeypatch.setattr` — testy tak nezávisí na heuristice mock adapteru.
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
from app.llm.gateway import ClassifyOutcome, DuplicateMatch, DuplicatesOutcome
from app.main import app
from app.models import Application, RecordHistory
from app.routes import classification
from app.schemas import AuditAction, Stav, Tier

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
def _wizard_db() -> Iterator[None]:
    Base.metadata.create_all(bind=_engine)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=_engine)


# --- podvržení session -------------------------------------------------------

_test_router = APIRouter()


@_test_router.post("/_test/wizard/session-login")
async def _session_login(request: Request) -> dict[str, bool]:
    request.session[SESSION_USER_KEY] = await request.json()
    return {"ok": True}


app.include_router(_test_router)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_session() -> None:
    client.cookies.clear()


def _login(username: str = "jana.nova", is_admin: bool = False) -> None:
    roles = ["user", "admin"] if is_admin else ["user"]
    response = client.post(
        "/_test/wizard/session-login",
        json={"username": username, "email": f"{username}@example.com", "roles": roles},
    )
    assert response.status_code == 200


# --- deterministické LLM náhrady --------------------------------------------


def _fake_classify(tier: Tier, fallback: bool = False) -> Any:
    def _call(*_args: Any, **_kwargs: Any) -> ClassifyOutcome:
        return ClassifyOutcome(
            llm_tier=tier,
            zduvodneni=(
                "AI zdůvodnění není momentálně dostupné. "
                "Governance tier byl určen povinnými pravidly."
                if fallback
                else "Testovací zdůvodnění návrhu AI."
            ),
            fallback_used=fallback,
        )

    return _call


def _fake_classify_with_context(tier: Tier, signal_context: dict[str, str]) -> Any:
    def _call(*_args: Any, **_kwargs: Any) -> ClassifyOutcome:
        return ClassifyOutcome(
            llm_tier=tier,
            zduvodneni="Testovací zdůvodnění návrhu AI.",
            fallback_used=False,
            signal_context=signal_context,
        )

    return _call


def _fake_duplicates(matches: list[DuplicateMatch], fallback: bool = False) -> Any:
    def _call(*_args: Any, **_kwargs: Any) -> DuplicatesOutcome:
        return DuplicatesOutcome(matches=matches, fallback_used=fallback)

    return _call


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Výchozí stav: AI navrhne MALA a nenajde žádnou duplicitu."""
    monkeypatch.setattr(classification, "_classify", _fake_classify(Tier.MALA))
    monkeypatch.setattr(classification, "_find_duplicates", _fake_duplicates([]))


# --- pomocníci pro formulář --------------------------------------------------

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _csrf_token() -> str:
    """Token se čte z reálného formuláře — stejnou cestou jako v prohlížeči."""
    response = client.get("/aplikace/nova")
    assert response.status_code == 200
    match = _CSRF_RE.search(response.text)
    assert match is not None, "Formulář neobsahuje CSRF token"
    return match.group(1)


def _form_payload(**overrides: str) -> dict[str, str]:
    payload = {
        "nazev": "Sumarizátor smluv",
        "stav": Stav.PILOT.name,
        "vlastnik_jmeno": "Jana Nová",
        "vlastnik_email": "jana.nova@example.com",
        "zastupce_jmeno": "Petr Svoboda",
        "zastupce_email": "petr.svoboda@example.com",
        "spravce_jmeno": "Tomáš Malý",
        "spravce_email": "tomas.maly@example.com",
        "komponenta_provider": "ANTHROPIC",
        "komponenta_model_name": "claude-sonnet-5",
        "komponenta_purpose": "Sumarizace textu smluv",
        "komponenta_hosting_type": "EXTERNI_API",
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
    payload.update(overrides)
    return payload


def _submit_form(**overrides: str) -> Any:
    data = _form_payload(**overrides)
    data["csrf_token"] = _csrf_token()
    return client.post("/aplikace/nova", data=data, follow_redirects=False)


def _save(tier: str, poznamka: str = "") -> Any:
    return client.post(
        "/aplikace/nova/ulozit",
        data={"csrf_token": _csrf_token(), "tier": tier, "poznamka": poznamka},
        follow_redirects=False,
    )


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


def _applications() -> list[Application]:
    """Načte záznamy včetně komponent — session se hned zavírá, lazy load by selhal."""
    session = _TestSessionLocal()
    try:
        return list(
            session.execute(
                select(Application).options(selectinload(Application.components))
            )
            .scalars()
            .all()
        )
    finally:
        session.close()


def _seed_application(nazev: str, popis: str) -> Application:
    session = _TestSessionLocal()
    try:
        entity = Application(
            nazev=nazev,
            popis=popis,
            vlastnik_jmeno="Jana Nová",
            vlastnik_email="jana.nova@example.com",
            zastupce_jmeno="Petr Svoboda",
            zastupce_email="petr.svoboda@example.com",
            spravce_jmeno="Tomáš Malý",
            spravce_email="tomas.maly@example.com",
            klasifikace_llm=Tier.MALA,
            klasifikace_minimum=Tier.MALA,
            klasifikace=Tier.MALA,
            klasifikace_zduvodneni="Seed.",
            klasifikace_potvrdil="jana.nova",
            dotaznik_odpovedi={},
            stav=Stav.PROVOZ,
            created_by="jana.nova",
            updated_by="jana.nova",
        )
        session.add(entity)
        session.commit()
        session.refresh(entity)
        return entity
    finally:
        session.close()


# --- přístup a CSRF ----------------------------------------------------------


def test_form_redirects_anonymous_to_login() -> None:
    response = client.get("/aplikace/nova", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_post_without_csrf_token_is_rejected() -> None:
    _login()
    response = client.post("/aplikace/nova", data=_form_payload(), follow_redirects=False)
    assert response.status_code == 403
    assert _applications() == []


def test_direct_access_to_later_steps_redirects_to_start() -> None:
    _login()
    for path in ("/aplikace/nova/duplicity", "/aplikace/nova/klasifikace"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/aplikace/nova"


def test_form_shows_tooltip_texts_from_spec() -> None:
    _login()
    response = client.get("/aplikace/nova")
    assert response.status_code == 200
    assert "Usnadňuje práci, bez něj se obejdeme; výpadek nikoho nezastaví." in response.text
    assert "Výstup přímo spouští akci — odeslání, zápis, transakci." in response.text


# --- validace formuláře ------------------------------------------------------


def test_invalid_form_returns_errors_and_keeps_values() -> None:
    _login()
    response = _submit_form(vlastnik_email="neplatny-email", nazev="")
    assert response.status_code == 400
    assert "je povinné pole" in response.text
    assert "nemá platný tvar" in response.text
    # Vyplněné hodnoty zůstávají ve formuláři.
    assert "Petr Svoboda" in response.text
    assert _applications() == []


# --- happy path --------------------------------------------------------------


def test_happy_path_creates_record_with_history() -> None:
    _login()

    response = _submit_form()
    assert response.status_code == 303
    assert response.headers["location"] == "/aplikace/nova/klasifikace"

    classify_page = client.get("/aplikace/nova/klasifikace")
    assert classify_page.status_code == 200
    assert "Testovací zdůvodnění návrhu AI." in classify_page.text

    saved = _save(Tier.MALA.name)
    assert saved.status_code == 303

    applications = _applications()
    assert len(applications) == 1
    record = applications[0]
    assert saved.headers["location"] == f"/aplikace/{record.id}"
    assert record.klasifikace is Tier.MALA
    assert record.klasifikace_minimum is Tier.MALA
    assert record.klasifikace_potvrdil == "jana.nova"
    assert record.stav is Stav.PILOT
    assert record.dotaznik_odpovedi["kritictnost"] == "POMOCNY"
    assert [c.model_name for c in record.components] == ["claude-sonnet-5"]

    actions = {entry.action for entry in _history(record.id)}
    assert actions == {AuditAction.CREATE, AuditAction.CLASSIFY}


def test_flags_are_stored_for_personal_data_answers() -> None:
    _login()
    _submit_form(osobni_udaje="KLIENTU", rozhodovani="DOPORUCUJE")
    client.get("/aplikace/nova/klasifikace")

    assert _save(Tier.STREDNI.name).status_code == 303

    record = _applications()[0]
    zkratky = {flag["zkratka"] for flag in record.klasifikace_priznaky}
    assert zkratky == {"GDPR", "AI-ACT"}
    assert record.klasifikace_minimum is Tier.STREDNI
    # Fallback fixture `_fake_classify` nevrací `signal_context` — kostra
    # signálu stačí, žádný z nich nemá `ai_kontext` (graceful degradation).
    assert all("ai_kontext" not in flag for flag in record.klasifikace_priznaky)


def test_ai_kontext_se_uklada_do_klasifikace_priznaky(monkeypatch: pytest.MonkeyPatch) -> None:
    """AI kontextová věta (spec kap. 5.4) se propíše z klasifikačního kroku
    (session) až do uloženého `klasifikace_priznaky.ai_kontext` — bez ovlivnění
    existence/kostry signálu, kterou pořád počítá `regulatory.compute_flags`."""
    monkeypatch.setattr(
        classification,
        "_classify",
        _fake_classify_with_context(
            Tier.STREDNI, {"GDPR": "Tento záznam ukládá e-maily klientů z kontaktního formuláře."}
        ),
    )
    _login()
    _submit_form(osobni_udaje="KLIENTU")
    client.get("/aplikace/nova/klasifikace")

    assert _save(Tier.STREDNI.name).status_code == 303

    record = _applications()[0]
    podle_zkratky = {flag["zkratka"]: flag for flag in record.klasifikace_priznaky}
    assert podle_zkratky["GDPR"]["ai_kontext"] == (
        "Tento záznam ukládá e-maily klientů z kontaktního formuláře."
    )
    # Kostra signálu je beze změny — stejná jako u signálu bez AI kontextu.
    assert podle_zkratky["GDPR"]["zkratka"] == "GDPR"
    assert podle_zkratky["GDPR"]["reason_code"] == "PERSONAL_DATA_PROCESSING"


# --- policy floor ------------------------------------------------------------


def test_manual_tier_below_policy_minimum_is_rejected() -> None:
    _login()
    _submit_form(rozhodovani="ROZHODUJE_AUTOMATICKY")

    classify_page = client.get("/aplikace/nova/klasifikace")
    assert classify_page.status_code == 200
    assert "AUTO_DECISION_PERSON" in classify_page.text

    # Ručně sestavený POST mimo UI: MALA je pod povinným minimem VELKA.
    response = _save(Tier.MALA.name, poznamka="Chci to níž.")
    assert response.status_code == 400
    assert "AUTO_DECISION_PERSON" in response.text
    assert _applications() == []


def test_note_is_required_when_tier_differs_from_suggestion() -> None:
    _login()
    _submit_form()

    client.get("/aplikace/nova/klasifikace")
    response = _save(Tier.VELKA.name)
    assert response.status_code == 400
    assert "je poznámka povinná" in response.text
    assert _applications() == []

    saved = _save(Tier.VELKA.name, poznamka="Nástroj poroste do rizikovější oblasti.")
    assert saved.status_code == 303
    record = _applications()[0]
    assert record.klasifikace is Tier.VELKA
    assert record.klasifikace_poznamka == "Nástroj poroste do rizikovější oblasti."


def test_user_manual_tier_below_suggestion_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresní scénář z hlášení bugu: AI navrhne VELKA (minimum je jen MALA),
    běžný user se pokusí ručně sestaveným POSTem snížit na STREDNI. Dřív se
    snížení tiše přebilo zpět na VELKA (poznámka se uložila, klasifikace ne);
    teď musí backend vrátit 400 a záznam vůbec nevznikne."""
    monkeypatch.setattr(classification, "_classify", _fake_classify(Tier.VELKA))
    _login()
    _submit_form()

    client.get("/aplikace/nova/klasifikace")
    response = _save(Tier.STREDNI.name, poznamka="Chci to níž.")
    assert response.status_code == 400
    assert "administrátor" in response.text
    assert _applications() == []


def test_admin_manual_tier_below_suggestion_is_saved_in_wizard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresní test hlášeného bugu v samotném wizardu: admin sníží AI návrh
    VELKA na STREDNI (nad policy minimem MALA) — musí se uložit snížená
    hodnota, ne tiše zůstat VELKA."""
    monkeypatch.setattr(classification, "_classify", _fake_classify(Tier.VELKA))
    _login("admin.spravce", is_admin=True)
    _submit_form()

    client.get("/aplikace/nova/klasifikace")
    saved = _save(Tier.STREDNI.name, poznamka="AI přecenila riziko, snižuji.")
    assert saved.status_code == 303

    record = _applications()[0]
    assert record.klasifikace is Tier.STREDNI
    assert record.klasifikace_poznamka == "AI přecenila riziko, snižuji."

    classify_entries = [
        entry for entry in _history(record.id) if entry.action is AuditAction.CLASSIFY
    ]
    assert len(classify_entries) == 1
    assert classify_entries[0].nova_hodnota == "STREDNI"


# --- duplicity ---------------------------------------------------------------


def test_duplicate_step_requires_reason_and_logs_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _login()
    existing = _seed_application(
        "Sumarizátor smluv", "Shrnuje nahrané smlouvy do bodů pro právní tým."
    )
    monkeypatch.setattr(
        classification,
        "_find_duplicates",
        _fake_duplicates(
            [
                DuplicateMatch(
                    record_id=existing.id,
                    nazev=existing.nazev,
                    duvod="Stejný název i popis.",
                )
            ]
        ),
    )

    response = _submit_form()
    assert response.status_code == 303
    assert response.headers["location"] == "/aplikace/nova/duplicity"

    step = client.get("/aplikace/nova/duplicity")
    assert step.status_code == 200
    assert "Stejný název i popis." in step.text

    bez_duvodu = client.post(
        "/aplikace/nova/duplicity/pokracovat",
        data={"csrf_token": _csrf_token()},
        follow_redirects=False,
    )
    assert bez_duvodu.status_code == 400
    assert "Uveďte prosím důvod" in bez_duvodu.text

    s_duvodem = client.post(
        "/aplikace/nova/duplicity/pokracovat",
        data={"csrf_token": _csrf_token(), "duvod": "Jiný proces, jiný tým."},
        follow_redirects=False,
    )
    assert s_duvodem.status_code == 303
    assert s_duvodem.headers["location"] == "/aplikace/nova/klasifikace"

    client.get("/aplikace/nova/klasifikace")
    assert _save(Tier.MALA.name).status_code == 303

    novy = [a for a in _applications() if a.id != existing.id][0]
    override = [
        entry
        for entry in _history(novy.id)
        if entry.action is AuditAction.DUPLICATE_OVERRIDE
    ]
    assert len(override) == 1
    assert override[0].reason == "Jiný proces, jiný tým."


def test_duplicate_cancel_discards_wizard_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _login()
    existing = _seed_application(
        "Sumarizátor smluv", "Shrnuje nahrané smlouvy do bodů pro právní tým."
    )
    monkeypatch.setattr(
        classification,
        "_find_duplicates",
        _fake_duplicates(
            [DuplicateMatch(record_id=existing.id, nazev=existing.nazev, duvod="Shoda.")]
        ),
    )
    _submit_form()

    response = client.post(
        "/aplikace/nova/duplicity/zrusit",
        data={"csrf_token": _csrf_token()},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    # Mezistav je pryč — klasifikační krok posílá zpět na začátek.
    dalsi = client.get("/aplikace/nova/klasifikace", follow_redirects=False)
    assert dalsi.status_code == 303
    assert dalsi.headers["location"] == "/aplikace/nova"
    assert len(_applications()) == 1


def test_duplicate_fallback_note_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    _login()
    existing = _seed_application(
        "Sumarizátor smluv", "Shrnuje nahrané smlouvy do bodů pro právní tým."
    )
    monkeypatch.setattr(
        classification,
        "_find_duplicates",
        _fake_duplicates(
            [DuplicateMatch(record_id=existing.id, nazev=existing.nazev, duvod="Shoda.")],
            fallback=True,
        ),
    )
    _submit_form()

    step = client.get("/aplikace/nova/duplicity")
    assert step.status_code == 200
    assert "AI kontrola není dostupná; zobrazeny výsledky lokální kontroly." in step.text


# --- fallback klasifikace ----------------------------------------------------


def test_classification_fallback_keeps_wizard_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _login()
    monkeypatch.setattr(
        classification, "_classify", _fake_classify(Tier.MALA, fallback=True)
    )

    assert _submit_form().status_code == 303

    prvni = client.get("/aplikace/nova/klasifikace")
    assert prvni.status_code == 200
    assert "AI zdůvodnění není momentálně dostupné." in prvni.text

    # Stav v session přežil — opakovaný GET i návrat na formulář fungují.
    druhy = client.get("/aplikace/nova/klasifikace")
    assert druhy.status_code == 200
    assert "AI zdůvodnění není momentálně dostupné." in druhy.text

    formular = client.get("/aplikace/nova")
    assert formular.status_code == 200
    assert "Shrnuje nahrané smlouvy do bodů pro právní tým." in formular.text

    assert _save(Tier.MALA.name).status_code == 303
    assert len(_applications()) == 1
