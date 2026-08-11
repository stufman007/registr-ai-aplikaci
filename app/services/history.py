"""Immutable audit historie (spec kap. 4.3).

Append-only: tento modul obsahuje jen zápis. Žádná funkce zde nesmí záznam
historie smazat ani upravit — to by porušilo invariant „audit přežívá
záznam" (spec kap. 0, princip 3).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from app.models import RecordHistory
from app.schemas import AuditAction

# Akce, u kterých je `reason` povinný — bez důvodu nejde smazat/duplikovat.
REASON_REQUIRED_ACTIONS = frozenset({AuditAction.RETIRE, AuditAction.DUPLICATE_OVERRIDE})

# Substring guard: `pole` obsahující kteroukoli z těchto hodnot se do historie
# nikdy nezapíše (session/tokeny/secrets/obsah promptů se neserializují — spec
# kap. 4.3 „Privacy").
SENSITIVE_FIELD_BLACKLIST = frozenset({"session", "token", "secret", "prompt", "response"})


def log(
    session: Session,
    record_id: str,
    actor: str,
    action: AuditAction,
    pole: str | None = None,
    stara: str | None = None,
    nova: str | None = None,
    reason: str | None = None,
) -> RecordHistory:
    """Zapíše jeden řádek `RecordHistory`.

    Vyhodí `ValueError`, pokud:
    - `action` je `RETIRE` nebo `DUPLICATE_OVERRIDE` a `reason` chybí,
    - `pole` obsahuje substring z `SENSITIVE_FIELD_BLACKLIST`.
    """
    if action in REASON_REQUIRED_ACTIONS and not reason:
        raise ValueError(f"Akce {action.value} vyžaduje 'reason'.")

    if pole is not None:
        pole_lower = pole.lower()
        for blacklisted in SENSITIVE_FIELD_BLACKLIST:
            if blacklisted in pole_lower:
                raise ValueError(
                    f"Pole '{pole}' je na blacklistu citlivých polí a nesmí se "
                    "zapsat do historie."
                )

    entry = RecordHistory(
        record_id=record_id,
        actor=actor,
        action=action,
        pole=pole,
        stara_hodnota=stara,
        nova_hodnota=nova,
        reason=reason,
    )
    session.add(entry)
    return entry


def diff_fields(
    old: dict[str, Any], new: dict[str, Any], tracked_fields: Iterable[str]
) -> list[tuple[str, str, str]]:
    """Porovná `old` a `new` nad `tracked_fields`, vrátí `(pole, stará, nová)`
    pro každé pole, jehož hodnota se liší. Hodnoty serializované přes `str()`."""
    changes: list[tuple[str, str, str]] = []
    for field in tracked_fields:
        old_value = old.get(field)
        new_value = new.get(field)
        if old_value != new_value:
            changes.append((field, str(old_value), str(new_value)))
    return changes
