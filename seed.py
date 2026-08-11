"""CLI wrapper: `python seed.py` vloží syntetická data do prázdné DB.

Skutečná logika žije v `app.seed_data.seed_if_empty` — `app.main` ji volá
napřímo v rámci `lifespan` (spec kap. 12: `create_all` → seed → purge audit).
Tento skript existuje navíc pro ruční/CI ověření mimo běžící aplikaci
(spec kap. 14, ověření Fáze 14): vytvoří si vlastní `SessionLocal`, zavolá
stejnou funkci a na stdout vypíše počet vložených záznamů.
"""

from __future__ import annotations

from app.db import SessionLocal, init_db
from app.seed_data import seed_if_empty


def main() -> None:
    init_db()

    session = SessionLocal()
    try:
        inserted = seed_if_empty(session)
    finally:
        session.close()

    if inserted:
        print(f"Seed: vloženo {inserted} syntetických aplikací.")
    else:
        print("Seed: tabulka 'applications' už obsahuje data, nic se nevkládá.")


if __name__ == "__main__":
    main()
