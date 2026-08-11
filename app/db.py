"""SQLAlchemy engine, session factory a FastAPI dependency.

Jediné místo, kde vzniká DB engine. SQLite soubor dle `settings.database_url`;
`check_same_thread=False`, protože FastAPI obsluhuje požadavky ve více
threadech nad jedním souborovým připojením.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(settings.database_url, connect_args=connect_args)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Základní deklarativní třída pro všechny modely v `app.models`."""


def init_db() -> None:
    """Vytvoří všechny tabulky, pokud ještě neexistují (bez migrací — F0/F5 dluh)."""
    from app import models  # noqa: F401  — import kvůli registraci modelů do Base.metadata

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: vydá session, na konci požadavku ji vždy zavře."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
