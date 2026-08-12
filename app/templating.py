"""Sdílená instance Jinja2Templates + registrace globálů (Fáze 8+).

Jediné místo, kde se `Jinja2Templates` vytváří — routes v dalších fázích
importují `templates` odsud, aby všechny šablony sdílely stejné globály
(`csrf_field`) a stejný adresář.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.auth import get_current_user
from app.flash import pop_flash
from app.security import csrf_field
from app.ui_texts import REVIEW_BADGE_TOOLTIP, role_tooltip, stav_tooltip, tier_tooltip

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

def _static_version() -> str:
    """Cache-busting verze statických souborů = max mtime v app/static.

    Prohlížeče cachují /static/app.js napříč nasazeními (bez toho by uživatel
    po deployi klidně běžel se starým JS — reálně se to stalo s toggle
    checkboxem správce). Verze v query stringu (`?v=...`) cache zneplatní
    přesně tehdy, když se soubor změní.
    """
    mtimes = (p.stat().st_mtime_ns for p in STATIC_DIR.glob("*") if p.is_file())
    return str(max(mtimes, default=0))


STATIC_VERSION = _static_version()

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["static_version"] = STATIC_VERSION
templates.env.globals["csrf_field"] = csrf_field
# `base.html` potřebuje přihlášeného uživatele v hlavičce na každé stránce —
# jako globál se nemusí protahovat kontextem každé jednotlivé šablony.
templates.env.globals["current_user"] = get_current_user
# Flash hlášky po redirectu — `base.html` je vypíše a zároveň smaže ze session.
templates.env.globals["pop_flash"] = pop_flash
# Centralizované tooltip texty (app/ui_texts.py) — jedno místo pro všechny
# šablony místo rozházených stringů.
templates.env.globals["review_badge_tooltip"] = REVIEW_BADGE_TOOLTIP
templates.env.globals["tier_tooltip"] = tier_tooltip
templates.env.globals["stav_tooltip"] = stav_tooltip
templates.env.globals["role_tooltip"] = role_tooltip
