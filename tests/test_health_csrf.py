"""Testy `/health`, CSRF ochrany a security headers (Fáze 8, spec kap. 11–12)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.security import get_csrf_token, verify_csrf

# Malá testovací route jen pro tento test soubor — ověřuje, že `verify_csrf`
# funguje jako běžná FastAPI dependency a že po ní jde v handleru formulář
# přečíst znovu (Starlette cachuje `request.form()`).
_test_router = APIRouter()


@_test_router.get("/_test/csrf-token")
def _issue_csrf_token(request: Request) -> dict[str, str]:
    return {"csrf_token": get_csrf_token(request)}


@_test_router.post("/_test/protected", dependencies=[Depends(verify_csrf)])
async def _protected_endpoint(request: Request) -> dict[str, object]:
    # Formulář se dá po dependency přečíst znovu beze ztráty dat.
    form = await request.form()
    return {"ok": True, "csrf_token_seen_again": form.get("csrf_token") is not None}


app.include_router(_test_router)

client = TestClient(app)


def test_health_returns_ok_with_db_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_health_response_leaks_no_settings_keys_or_values() -> None:
    response = client.get("/health")
    body = response.json()

    assert set(body.keys()) == {"status", "db"}

    settings_field_names = set(Settings.model_fields.keys())
    assert not settings_field_names & set(body.keys())

    from app.config import settings

    raw = response.text
    for field_name, value in vars(settings).items():
        if isinstance(value, str) and value and field_name != "database_url":
            assert value not in raw, f"Hodnota pole '{field_name}' unikla do /health"


def test_security_headers_present_on_response() -> None:
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp


def test_post_without_csrf_token_returns_403() -> None:
    response = client.post("/_test/protected", data={})
    assert response.status_code == 403


def test_post_with_wrong_csrf_token_returns_403() -> None:
    client.get("/_test/csrf-token")  # zajistí, že session token existuje
    response = client.post("/_test/protected", data={"csrf_token": "neplatny-token"})
    assert response.status_code == 403


def test_post_with_valid_csrf_token_from_session_succeeds() -> None:
    token_response = client.get("/_test/csrf-token")
    token = token_response.json()["csrf_token"]

    response = client.post("/_test/protected", data={"csrf_token": token})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "csrf_token_seen_again": True}
