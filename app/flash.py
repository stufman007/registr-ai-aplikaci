"""Jednorázové hlášky mezi requesty (flash) přes session.

Wizard zakládání záznamu končí redirectem (POST → 303 → GET), takže výsledek
akce nelze předat kontextem šablony. Hláška se proto odloží do session a
`base.html` ji při nejbližším renderu vypíše a zároveň smaže — každá hláška
se ukáže právě jednou.
"""

from __future__ import annotations

from starlette.requests import Request

#: Klíč v session, pod kterým žije čekající hláška.
FLASH_SESSION_KEY = "flash"


def set_flash(request: Request, message: str) -> None:
    """Uloží hlášku, kterou zobrazí až následující vyrenderovaná stránka."""
    request.session[FLASH_SESSION_KEY] = message


def pop_flash(request: Request) -> str | None:
    """Vrátí a zároveň zahodí čekající hlášku (`None`, když žádná není).

    Volá se ze šablony `base.html`. Jinja2 renderuje obsah odpovědi dřív, než
    `SessionMiddleware` serializuje cookie, takže smazání se do session promítne.
    """
    message = request.session.pop(FLASH_SESSION_KEY, None)
    return message if isinstance(message, str) and message else None
