"""Úvodní stránka. Placeholder — Fázi 10 ji nahradí seznam registru."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.auth import get_current_user
from app.templating import templates

router = APIRouter(tags=["registry"])


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Nepřihlášenému nabídne přihlášení, přihlášenému zatím jen potvrdí identitu."""
    user = get_current_user(request)
    if user is None:
        return templates.TemplateResponse(request, "login.html")
    return templates.TemplateResponse(request, "home.html", {"user": user})
