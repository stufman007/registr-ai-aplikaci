"""Správcovské routes. Zatím jen kontrolní endpoint role — Fáze 13 sem doplní vyřazení a obnovu."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth import SessionUser, require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/ping")
def admin_ping(user: Annotated[SessionUser, Depends(require_admin)]) -> dict[str, str]:
    """Ověřuje, že se role vynucuje na backendu: bez role `admin` vrací 403."""
    return {"status": "ok", "username": user.username}
