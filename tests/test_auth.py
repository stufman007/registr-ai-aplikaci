"""Testy OIDC vrstvy bez běžícího Keycloaku (Fáze 9, spec kap. 3 a 11).

Živý authorization code flow proti skutečnému Keycloaku se ověřuje zvlášť
(`docker compose up keycloak`) — tady se testuje jen to, co je čistá logika:
extrakce rolí z claims, vynucení rolí v dependencies a neměnnost `SessionUser`.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi import APIRouter, Depends, Request
from fastapi.testclient import TestClient

from app.auth import (
    SESSION_USER_KEY,
    SessionUser,
    check_owner_or_admin,
    extract_roles,
    require_admin,
    require_user,
)
from app.main import app

# Testovací router jen pro tento soubor: `/_test/session-login` podstrčí obsah
# session (middleware ji podepíše jako při skutečném přihlášení), zbytek ověřuje
# dependencies na GET i na state-changing metodě.
_test_router = APIRouter()


@_test_router.post("/_test/session-login")
async def _session_login(request: Request) -> dict[str, bool]:
    request.session[SESSION_USER_KEY] = await request.json()
    return {"ok": True}


@_test_router.post("/_test/session-logout")
async def _session_logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


@_test_router.get("/_test/user-only")
def _user_only_get(user: SessionUser = Depends(require_user)) -> dict[str, str]:
    return {"username": user.username}


@_test_router.post("/_test/user-only")
def _user_only_post(user: SessionUser = Depends(require_user)) -> dict[str, str]:
    return {"username": user.username}


@_test_router.post("/_test/admin-only")
def _admin_only_post(user: SessionUser = Depends(require_admin)) -> dict[str, str]:
    return {"username": user.username}


app.include_router(_test_router)

client = TestClient(app)


def _login(**session_data: object) -> None:
    response = client.post("/_test/session-login", json=session_data)
    assert response.status_code == 200


def _logout() -> None:
    assert client.post("/_test/session-logout").status_code == 200


@pytest.fixture(autouse=True)
def _clean_session() -> None:
    client.cookies.clear()


# --- extrakce rolí z claims -------------------------------------------------


def test_extract_roles_from_plain_list() -> None:
    assert extract_roles({"roles": ["user", "admin"]}, "roles") == frozenset({"user", "admin"})


def test_extract_roles_from_nested_dict() -> None:
    """Tvar `realm_access: {"roles": [...]}` — claim je dict, ne seznam."""
    claims = {"realm_access": {"roles": ["user"]}}
    assert extract_roles(claims, "realm_access") == frozenset({"user"})


def test_extract_roles_from_single_string() -> None:
    assert extract_roles({"roles": "admin"}, "roles") == frozenset({"admin"})


def test_extract_roles_missing_claim_is_empty() -> None:
    assert extract_roles({"preferred_username": "user"}, "roles") == frozenset()


def test_extract_roles_ignores_unexpected_type() -> None:
    assert extract_roles({"roles": 42}, "roles") == frozenset()


def test_extract_roles_drops_non_string_items() -> None:
    assert extract_roles({"roles": ["user", None, 7, ""]}, "roles") == frozenset({"user"})


def test_extract_roles_uses_configured_claim_by_default() -> None:
    """Bez explicitního jména se bere `settings.oidc_roles_claim` (default `roles`)."""
    assert extract_roles({"roles": ["admin"]}) == frozenset({"admin"})


# --- SessionUser ------------------------------------------------------------


def test_session_user_is_immutable() -> None:
    user = SessionUser(username="user", email="user@example.com", roles=frozenset({"user"}))
    with pytest.raises(dataclasses.FrozenInstanceError):
        user.username = "admin"  # type: ignore[misc]


def test_session_user_is_admin_flag() -> None:
    assert SessionUser("a", "a@example.com", frozenset({"user", "admin"})).is_admin is True
    assert SessionUser("u", "u@example.com", frozenset({"user"})).is_admin is False


# --- require_user -----------------------------------------------------------


def test_require_user_without_session_redirects_on_get() -> None:
    response = client.get("/_test/user-only", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_require_user_without_session_returns_403_on_post() -> None:
    response = client.post("/_test/user-only", follow_redirects=False)
    assert response.status_code == 403


def test_require_user_with_session_passes() -> None:
    _login(username="user", email="user@example.com", roles=["user"])
    response = client.post("/_test/user-only")
    assert response.status_code == 200
    assert response.json() == {"username": "user"}


# --- require_admin ----------------------------------------------------------


def test_require_admin_without_admin_role_returns_403() -> None:
    _login(username="user", email="user@example.com", roles=["user"])
    response = client.post("/_test/admin-only")
    assert response.status_code == 403


def test_require_admin_with_admin_role_passes() -> None:
    _login(username="admin", email="admin@example.com", roles=["user", "admin"])
    response = client.post("/_test/admin-only")
    assert response.status_code == 200
    assert response.json() == {"username": "admin"}


def test_admin_ping_enforces_role_on_get() -> None:
    _login(username="user", email="user@example.com", roles=["user"])
    assert client.get("/admin/ping").status_code == 403

    _logout()
    _login(username="admin", email="admin@example.com", roles=["user", "admin"])
    response = client.get("/admin/ping")
    assert response.status_code == 200
    assert response.json()["username"] == "admin"


# --- vlastník nebo admin (spec kap. 3) --------------------------------------


class _Record:
    def __init__(self, vlastnik_email: str, created_by: str) -> None:
        self.vlastnik_email = vlastnik_email
        self.created_by = created_by


def test_check_owner_or_admin_matrix() -> None:
    record = _Record(vlastnik_email="Owner@Example.com", created_by="autor")

    admin = SessionUser("spravce", "spravce@example.com", frozenset({"admin"}))
    owner = SessionUser("nekdo", "owner@example.com", frozenset({"user"}))
    author = SessionUser("autor", "jiny@example.com", frozenset({"user"}))
    stranger = SessionUser("cizi", "cizi@example.com", frozenset({"user"}))

    assert check_owner_or_admin(admin, record) is True
    assert check_owner_or_admin(owner, record) is True
    assert check_owner_or_admin(author, record) is True
    assert check_owner_or_admin(stranger, record) is False


# --- domovská stránka -------------------------------------------------------


def test_home_shows_login_page_when_anonymous() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Přihlásit se" in response.text


def test_home_shows_username_and_roles_when_logged_in() -> None:
    _login(username="admin", email="admin@example.com", roles=["user", "admin"])
    response = client.get("/")
    assert response.status_code == 200
    assert "admin" in response.text
    assert "Odhlásit" in response.text


def test_login_redirects_to_public_keycloak_authorize_endpoint() -> None:
    """Riziko R1: authorize URL musí být veřejná (OIDC_ISSUER_URL), ne interní."""
    from app.config import settings

    response = client.get("/login", follow_redirects=False)
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert location.startswith(f"{settings.oidc_issuer_url}/protocol/openid-connect/auth")
    assert "redirect_uri=" in location


def test_logout_clears_session_and_redirects_to_end_session_endpoint() -> None:
    from app.config import settings

    _login(username="user", email="user@example.com", roles=["user"])
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith(
        f"{settings.oidc_issuer_url}/protocol/openid-connect/logout"
    )

    # Po odhlášení už session identitu nenese.
    assert client.post("/_test/user-only", follow_redirects=False).status_code == 403
