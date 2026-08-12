"""Testy stránky pravidel vyhodnocení (Fáze 10, pravidla_info route).

GET /pravidla má require_user — přihlášený dostane stránku s pravidly, nepřihlášený
musí na login. Stránka zobrazuje data z policy.RULES a regulatory signálů
přímo ze zdrojového kódu (single source of truth).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import SESSION_USER_KEY
from app.db import Base, get_db
from app.main import app
from app.services.policy import RULES, RULES_VERSION

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
def _rules_info_db() -> Iterator[None]:
    """Čerstvé schéma + `get_db` override jen po dobu testů v tomto souboru."""
    Base.metadata.create_all(bind=_engine)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=_engine)


# --- vlastní testovací router pro podvržení session ----------------------

_test_router = APIRouter()


@_test_router.post("/_test/rules/session-login")
async def _session_login(request: Request) -> dict[str, bool]:
    request.session[SESSION_USER_KEY] = await request.json()
    return {"ok": True}


@_test_router.post("/_test/rules/session-logout")
async def _session_logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


app.include_router(_test_router)

client = TestClient(app)


def _login(**session_data: object) -> None:
    assert client.post("/_test/rules/session-login", json=session_data).status_code == 200


def _logout() -> None:
    assert client.post("/_test/rules/session-logout").status_code == 200


@pytest.fixture(autouse=True)
def _clean_session() -> None:
    client.cookies.clear()


# --- Testy ---------------------------------------------------------------


def test_get_pravidla_anonymous_redirects_to_login() -> None:
    """Nepřihlášený uživatel na GET /pravidla → 303 redirect na /login."""
    response = client.get("/pravidla", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_get_pravidla_authenticated_user_gets_200() -> None:
    """Přihlášený uživatel na GET /pravidla → 200."""
    _login(username="test_user", email="test@example.com", roles=["user"])
    response = client.get("/pravidla")
    assert response.status_code == 200


def test_pravidla_page_contains_rules_version() -> None:
    """Stránka musí obsahovat RULES_VERSION (verze pravidel)."""
    _login(username="test_user", email="test@example.com", roles=["user"])
    response = client.get("/pravidla")
    assert response.status_code == 200
    assert RULES_VERSION in response.text


def test_pravidla_page_contains_all_rule_reason_codes() -> None:
    """Stránka musí obsahovat všechny reason_codes z policy.RULES."""
    _login(username="test_user", email="test@example.com", roles=["user"])
    response = client.get("/pravidla")
    assert response.status_code == 200

    # Všech 8 pravidel musí být na stránce (programově, ne hardcoded seznam)
    for rule in RULES:
        assert rule.reason_code in response.text, f"reason_code {rule.reason_code} chybí"


def test_pravidla_page_contains_legislative_signals() -> None:
    """Stránka musí obsahovat zkratky legislativních signálů: GDPR, AI-ACT, DORA."""
    _login(username="test_user", email="test@example.com", roles=["user"])
    response = client.get("/pravidla")
    assert response.status_code == 200

    # Všechny tři signály
    assert "GDPR" in response.text
    assert "AI-ACT" in response.text
    assert "DORA" in response.text


def test_pravidla_page_contains_questions() -> None:
    """Stránka musí obsahovat otázky z dotazníku."""
    _login(username="test_user", email="test@example.com", roles=["user"])
    response = client.get("/pravidla")
    assert response.status_code == 200

    # Alespoň první otázka by měla být vidět
    assert "K čemu aplikace slouží" in response.text


def test_pravidla_page_displays_tier_labels() -> None:
    """Stránka zobrazuje labels tierů v tabulce pravidel."""
    _login(username="test_user", email="test@example.com", roles=["user"])
    response = client.get("/pravidla")
    assert response.status_code == 200

    # V tabulce pravidel se zobrazují tier labels
    # (všechna pravidla mají min tier Střední nebo Velká)
    assert "Střední" in response.text
    assert "Velká" in response.text

    # Zkontroluj, že jsou v <span> s tier classes
    assert 'tier-stredni' in response.text
    assert 'tier-velka' in response.text
