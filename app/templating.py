"""Sdílená instance Jinja2Templates + registrace globálů (Fáze 8+).

Jediné místo, kde se `Jinja2Templates` vytváří — routes v dalších fázích
importují `templates` odsud, aby všechny šablony sdílely stejné globály
(`csrf_field`) a stejný adresář.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.security import csrf_field

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["csrf_field"] = csrf_field
