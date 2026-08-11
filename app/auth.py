"""OIDC přihlášení přes Keycloak a vynucení rolí na backendu (Fáze 9, spec kap. 3 a 11).

Riziko R1 — issuer mismatch v Dockeru. Prohlížeč dosáhne na Keycloak jen přes
veřejnou URL (`OIDC_ISSUER_URL`), kontejner aplikace jen přes interní jméno
služby (`OIDC_INTERNAL_URL`). Endpointy se proto skládají ručně a každý má
pevně danou stranu:

    authorize / end_session  → veřejná URL  (redirect prohlížeče)
    token / certs / userinfo → interní URL  (backchannel z aplikace)
    issuer pro validaci      → veřejná URL  (tak ho Keycloak vydává v tokenu)

`server_metadata_url` se záměrně nepoužívá: discovery dokument stažený přes
interní URL by nesl interní endpointy i pro prohlížeč a naopak. Protože je
`issuer` v metadatech vyplněný ručně, Authlib metadata nikdy nestahuje a
`parse_id_token` validuje `iss` proti veřejné URL.

V session žije jen `{"username", "email", "roles"}` — žádné IdP tokeny.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.config import settings

logger = logging.getLogger(__name__)

#: Klíč v session, pod kterým žije přihlášený uživatel.
SESSION_USER_KEY = "user"

#: Role, která opravňuje ke správcovským akcím (spec kap. 3).
ADMIN_ROLE = "admin"

#: Jméno Authlib klienta — používá se jako `oauth.keycloak`.
OAUTH_CLIENT_NAME = "keycloak"


def _endpoint(base_url: str, name: str) -> str:
    """Sestaví OIDC endpoint nad zadanou realm URL bez discovery dotazu."""
    return f"{base_url.rstrip('/')}/protocol/openid-connect/{name}"


#: Veřejné (prohlížečové) endpointy.
AUTHORIZE_URL = _endpoint(settings.oidc_issuer_url, "auth")
END_SESSION_URL = _endpoint(settings.oidc_issuer_url, "logout")

#: Backchannel endpointy volané aplikací.
TOKEN_URL = _endpoint(settings.oidc_internal_url, "token")
JWKS_URI = _endpoint(settings.oidc_internal_url, "certs")
USERINFO_URL = _endpoint(settings.oidc_internal_url, "userinfo")


oauth = OAuth()
oauth.register(
    name=OAUTH_CLIENT_NAME,
    client_id=settings.oidc_client_id,
    client_secret=settings.oidc_client_secret,
    authorize_url=AUTHORIZE_URL,
    access_token_url=TOKEN_URL,
    # Nadbytečné kwargs Authlib ukládá jako `server_metadata`. Vyplněný `issuer`
    # znamená, že se metadata nikdy nestahují a `iss` v ID tokenu se validuje
    # proti veřejné URL — právě tady se riziko R1 buď ustojí, nebo ne.
    issuer=settings.oidc_issuer_url,
    jwks_uri=JWKS_URI,
    userinfo_endpoint=USERINFO_URL,
    end_session_endpoint=END_SESSION_URL,
    client_kwargs={"scope": "openid profile email"},
)


@dataclass(frozen=True, slots=True)
class SessionUser:
    """Identita přihlášeného uživatele — jediné, co se drží v session."""

    username: str
    email: str
    roles: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return ADMIN_ROLE in self.roles


class LoginRequired(Exception):
    """Nepřihlášený uživatel u GET požadavku — `app.main` z toho udělá redirect na `/login`."""


def extract_roles(claims: Mapping[str, Any], claim_name: str = "") -> frozenset[str]:
    """Vytáhne role z claims robustně vůči tvaru claimu.

    Podporované tvary hodnoty pod `claim_name`:

    - seznam řetězců (`["user", "admin"]`) — mapper „realm roles → roles",
    - dict s klíčem `roles` (`{"roles": [...]}`) — tvar `realm_access`,
    - jediný řetězec,
    - cokoli jiného nebo chybějící claim → prázdná množina.
    """
    name = claim_name or settings.oidc_roles_claim
    raw: Any = claims.get(name)

    if isinstance(raw, Mapping):
        raw = raw.get("roles")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset()

    return frozenset(role for role in raw if isinstance(role, str) and role)


def session_user_from_claims(claims: Mapping[str, Any]) -> SessionUser:
    """Poskládá `SessionUser` z OIDC claims. Bez `preferred_username` nemá identita smysl."""
    username = claims.get("preferred_username") or claims.get("sub")
    if not isinstance(username, str) or not username:
        raise ValueError("OIDC claims neobsahují použitelné uživatelské jméno.")

    email = claims.get("email")
    return SessionUser(
        username=username,
        email=email if isinstance(email, str) else "",
        roles=extract_roles(claims),
    )


def get_current_user(request: Request) -> SessionUser | None:
    """Přečte přihlášeného uživatele ze session; `None`, když nikdo přihlášen není."""
    data = request.session.get(SESSION_USER_KEY)
    if not isinstance(data, dict):
        return None

    username = data.get("username")
    if not isinstance(username, str) or not username:
        return None

    email = data.get("email")
    roles = data.get("roles")
    return SessionUser(
        username=username,
        email=email if isinstance(email, str) else "",
        roles=frozenset(r for r in (roles or ()) if isinstance(r, str)),
    )


def require_user(request: Request) -> SessionUser:
    """Dependency: vyžaduje přihlášení.

    GET (čtení stránky) → `LoginRequired` → redirect na `/login`, aby uživatel
    neviděl chybovou stránku. Jakákoli state-changing metoda → rovnou 403;
    přesměrovat POST na přihlášení by tiše zahodilo odeslaná data.
    """
    user = get_current_user(request)
    if user is None:
        if request.method == "GET":
            raise LoginRequired
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Pro tuto akci je nutné být přihlášen.",
        )
    return user


def require_admin(request: Request) -> SessionUser:
    """Dependency: vyžaduje přihlášení a roli `admin`, jinak 403."""
    user = require_user(request)
    if not user.is_admin:
        logger.warning(
            "Uživatel %s bez role admin zkusil %s %s",
            user.username,
            request.method,
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="K této akci je potřeba role admin.",
        )
    return user


def check_owner_or_admin(user: SessionUser, record: Any) -> bool:
    """Smí `user` editovat `record`? (spec kap. 3)

    Admin smí vše; jinak musí jít o vlastníka (shoda e-mailu, case-insensitive)
    nebo autora záznamu (shoda OIDC username s `created_by`). Používají routes
    editace ve Fázi 12 — proto samostatná funkce, ne dependency.
    """
    if user.is_admin:
        return True

    vlastnik = getattr(record, "vlastnik_email", None)
    if isinstance(vlastnik, str) and user.email and vlastnik.lower() == user.email.lower():
        return True

    created_by = getattr(record, "created_by", None)
    return isinstance(created_by, str) and created_by == user.username


auth_router = APIRouter(tags=["auth"])


@auth_router.get("/login", name="login")
async def login(request: Request) -> RedirectResponse:
    """Zahájí Authorization Code flow — redirect prohlížeče na veřejný Keycloak."""
    redirect_uri = str(request.url_for("auth_callback"))
    return await oauth.keycloak.authorize_redirect(request, redirect_uri)


@auth_router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request) -> RedirectResponse:
    """Vymění kód za token, ověří ID token a uloží do session jen identitu."""
    try:
        token = await oauth.keycloak.authorize_access_token(request)
    except OAuthError:
        logger.warning("OIDC callback selhal při výměně kódu za token", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Přihlášení se nezdařilo. Zkuste to prosím znovu.",
        ) from None

    claims = token.get("userinfo")
    if not isinstance(claims, Mapping):
        logger.error("OIDC odpověď neobsahuje ověřený ID token")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Přihlášení se nezdařilo. Zkuste to prosím znovu.",
        )

    try:
        user = session_user_from_claims(claims)
    except ValueError:
        logger.error("OIDC claims neobsahují uživatelské jméno")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Přihlášení se nezdařilo. Zkuste to prosím znovu.",
        ) from None

    # Nová session pro nové přihlášení: zahodí Authlib state i starý CSRF token.
    request.session.clear()
    request.session[SESSION_USER_KEY] = {
        "username": user.username,
        "email": user.email,
        "roles": sorted(user.roles),
    }
    logger.info("Přihlášen uživatel %s (role: %s)", user.username, ", ".join(sorted(user.roles)) or "žádné")
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@auth_router.get("/logout", name="logout")
async def logout(request: Request) -> RedirectResponse:
    """Smaže session a pošle prohlížeč na RP-initiated logout Keycloaku."""
    user = get_current_user(request)
    request.session.clear()
    if user is not None:
        logger.info("Odhlášen uživatel %s", user.username)

    params = urlencode(
        {
            "client_id": settings.oidc_client_id,
            "post_logout_redirect_uri": str(request.base_url),
        }
    )
    return RedirectResponse(f"{END_SESSION_URL}?{params}", status_code=status.HTTP_303_SEE_OTHER)
