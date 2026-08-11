"""CSRF ochrana a bezpečnostní HTTP hlavičky (spec kap. 11).

Dvě samostatné výjimky pro exception handlery v `app.main`:

- `CsrfError` — chybějící/neplatný CSRF token u state-changing POST požadavku.
- `VersionConflict` — optimistic locking (Fáze 12): záznam mezitím změnil
  někdo jiný. Definována už zde, protože ji spec řadí do `app/security.py`;
  vyhazuje ji až kód editace v pozdější fázi.
"""

from __future__ import annotations

import secrets

from markupsafe import Markup
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

#: Klíč v session, pod kterým žije CSRF token (spec kap. 11: "session + hidden field").
CSRF_SESSION_KEY = "csrf_token"

#: Název form pole, které nese CSRF token v odesílaných formulářích.
CSRF_FORM_FIELD = "csrf_token"


class CsrfError(Exception):
    """Chybějící nebo neplatný CSRF token u state-changing POST požadavku."""


class VersionConflict(Exception):
    """Optimistic locking (Fáze 12): záznam mezitím změnil někdo jiný."""


def get_csrf_token(request: Request) -> str:
    """Vrátí CSRF token uložený v session; při prvním použití ho vytvoří."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


async def verify_csrf(request: Request) -> None:
    """FastAPI dependency pro POST routes.

    Přečte `csrf_token` z form dat požadavku a porovná se session hodnotou
    konstantním časem (`secrets.compare_digest`). Starlette cachuje výsledek
    `request.form()` na `request`, takže následné `await request.form()` v
    samotné route handler funkci dostane stejná (již naparsovaná) data, ne
    prázdný stream.
    """
    form = await request.form()
    submitted = form.get(CSRF_FORM_FIELD)
    expected = request.session.get(CSRF_SESSION_KEY)

    if (
        not expected
        or not isinstance(submitted, str)
        or not submitted
        or not secrets.compare_digest(submitted, expected)
    ):
        raise CsrfError("Neplatný nebo chybějící bezpečnostní token formuláře (CSRF).")


def csrf_field(request: Request) -> Markup:
    """Jinja2 globál — vrátí hidden input s CSRF tokenem pro aktuální session.

    Použití v šabloně: `{{ csrf_field(request) }}` uvnitř `<form method="post">`.
    Token pochází ze `secrets.token_urlsafe`, obsahuje jen URL-safe znaky,
    proto se neescapuje ručně — Markup jen označuje výsledek jako bezpečné HTML.
    """
    token = get_csrf_token(request)
    return Markup(f'<input type="hidden" name="{CSRF_FORM_FIELD}" value="{token}">')


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Přidá základní bezpečnostní hlavičky ke každé odpovědi (spec kap. 11)."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'"
        )
        return response
