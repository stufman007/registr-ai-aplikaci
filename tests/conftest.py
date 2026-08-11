"""Sdílené testovací fixtures.

`TestClient(app)` použitý bez kontext manažeru (`with TestClient(app) as c:`)
nikdy nespustí FastAPI lifespan (`app.main.lifespan`) — startup/shutdown
eventy dostává jen `TestClient.__enter__`/`__exit__`. Testy v tomto repu si
`TestClient` takhle nepodrží, takže `init_db()` by se pro routy nad reálnou
DB (`app.db.engine`, `DATABASE_URL` z `.env`/defaultu) nikdy nezavolalo a
první dotaz nad tabulkou `applications` (Fáze 10, `GET /`) by spadl na
`no such table`. Schéma se proto připraví jednou na začátku testovací
session přímo, nezávisle na lifecyclu jednotlivých `TestClient` instancí.
"""

from __future__ import annotations

from app import models  # noqa: F401 — registruje modely do Base.metadata
from app.db import Base, engine, init_db

# Čistý stav při každém spuštění testů — testy nesmí záviset na datech
# ze sdíleného `registr.db` z předchozího běhu.
Base.metadata.drop_all(bind=engine)
init_db()
