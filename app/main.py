"""FastAPI kostra: `/health`, session, CSRF a error handlery (Fáze 8).

Startup (lifespan, moderní API místo `@app.on_event`):
    logging setup → "Aplikace startuje" → `init_db()` → `seed_if_empty`
    (jen prázdná DB) → `purge_older_than` (vlastní `SessionLocal` pro každý
    krok, zavře se po použití) → log počtů (spec kap. 12: create_all → seed
    → purge audit).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.auth import LoginRequired, auth_router
from app.config import settings
from app.db import SessionLocal, init_db
from app.llm.audit import purge_older_than
from app.routes import admin as admin_routes
from app.routes import classification as classification_routes
from app.routes import edit as edit_routes
from app.routes import registry as registry_routes
from app.routes import rules_info as rules_info_routes
from app.security import CsrfError, SecurityHeadersMiddleware, VersionConflict
from app.seed_data import seed_if_empty
from app.services.policy import PolicyViolation
from app.templating import STATIC_DIR, templates

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Základní logging setup — INFO, čas + úroveň + logger + zpráva."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    logger.info("Aplikace startuje")

    init_db()

    seed_session = SessionLocal()
    try:
        seeded = seed_if_empty(seed_session)
    finally:
        seed_session.close()
    logger.info("Seed syntetických dat: vloženo %d aplikací", seeded)

    session = SessionLocal()
    try:
        smazano = purge_older_than(session, settings.llm_audit_retention_days)
    finally:
        session.close()
    logger.info("Startovní úklid LLM auditu: smazáno %d záznamů starších než %d dní", smazano, settings.llm_audit_retention_days)

    yield

    logger.info("Aplikace se ukončuje")


app = FastAPI(title="Registr interních AI aplikací", lifespan=lifespan)

# Starlette obaluje middlewary v opačném pořadí, než jsou přidány — naposledy
# přidaný je nejvíc navenek. SessionMiddleware se proto přidává až po
# SecurityHeadersMiddleware, aby session byla dostupná dřív, než požadavek
# dorazí k CSRF dependency v routách.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret_key,
    https_only=settings.cookie_secure,
    same_site="lax",
    max_age=settings.session_max_age,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth_router)
# Wizard zakládání a editace musí být registrované PŘED registrem:
# `/aplikace/nova` i `/aplikace/{id}/upravit` by jinak spadly do
# `/aplikace/{application_id}` a skončily na 404.
app.include_router(classification_routes.router)
app.include_router(edit_routes.router)
app.include_router(rules_info_routes.router)
app.include_router(registry_routes.router)
app.include_router(admin_routes.router)
app.include_router(admin_routes.aplikace_router)


@app.get("/health")
def health() -> JSONResponse:
    """Bez autentizace. `{"status": "ok", "db": "ok"}`, při chybě DB HTTP 503.

    Nevypisuje žádnou konfiguraci (spec kap. 12).
    """
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        logger.exception("Health check: databáze nedostupná")
        db_ok = False
    finally:
        session.close()

    if not db_ok:
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "error"})
    return JSONResponse(status_code=200, content={"status": "ok", "db": "ok"})


@app.exception_handler(PolicyViolation)
async def handle_policy_violation(request: Request, exc: PolicyViolation) -> Response:
    return templates.TemplateResponse(
        request,
        "error.html",
        {"status_code": 400, "message": str(exc)},
        status_code=400,
    )


@app.exception_handler(LoginRequired)
async def handle_login_required(request: Request, exc: LoginRequired) -> Response:
    """Nepřihlášený uživatel u GET stránky — pošle ho rovnou na přihlášení."""
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(CsrfError)
async def handle_csrf_error(request: Request, exc: CsrfError) -> Response:
    return templates.TemplateResponse(
        request,
        "error.html",
        {"status_code": 403, "message": str(exc)},
        status_code=403,
    )


@app.exception_handler(VersionConflict)
async def handle_version_conflict(request: Request, exc: VersionConflict) -> Response:
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": 409,
            "message": "Záznam mezitím změnil někdo jiný. Načtěte stránku znovu a proveďte úpravu znovu.",
        },
        status_code=409,
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> Response:
    """Poslední záchytná síť. Loguje výjimku (bez těla requestu) a vrací error.html."""
    logger.exception(
        "Neošetřená výjimka při zpracování %s %s", request.method, request.url.path
    )
    return templates.TemplateResponse(
        request,
        "error.html",
        {"status_code": 500, "message": "Došlo k neočekávané chybě. Zkuste to prosím znovu."},
        status_code=500,
    )
