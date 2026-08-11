"""Failure contract LLM Gateway (spec kap. 8, plán Fáze 6, bod 6).

Testy nesahají na síť ani na skutečný adapter — používají fake adaptery
definované přímo tady, aby bylo vidět, co přesně gateway dostává a kolikrát.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.llm import gateway
from app.llm.base import AdapterResult, LlmServerError, LlmTimeout
from app.models import LlmAudit
from app.schemas import (
    Autonomie,
    CitlivostDat,
    DopadChyby,
    DotaznikOdpovedi,
    HostingType,
    KomponentaInfo,
    Kritictnost,
    LlmPurpose,
    OsobniUdaje,
    PocetUzivatelu,
    Provider,
    RozhodovaniOLidech,
    Tier,
    ViditelnostVystupu,
)

SENTINEL = "TAJNY-RETEZEC-Z-PROMPTU-XYZ"


@pytest.fixture()
def session() -> Session:
    """Čistá in-memory SQLite databáze pro jeden test."""
    import app.models  # noqa: F401 — registrace modelů do Base.metadata

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def answers() -> DotaznikOdpovedi:
    return DotaznikOdpovedi(
        ucel=f"Sumarizace smluv pro právní oddělení. {SENTINEL}",
        pocet_uzivatelu=PocetUzivatelu.OD_10_DO_50,
        kritictnost=Kritictnost.DULEZITY,
        osobni_udaje=OsobniUdaje.ZAMESTNANCU,
        rozhodovani=RozhodovaniOLidech.NE,
        viditelnost=ViditelnostVystupu.NE,
        citlivost=CitlivostDat.DUVERNA,
        autonomie=Autonomie.CLOVEK_SCHVALUJE,
        dopad=DopadChyby.PROVOZNI,
    )


@pytest.fixture()
def components() -> list[KomponentaInfo]:
    return [KomponentaInfo(provider=Provider.ANTHROPIC, hosting_type=HostingType.EXTERNI_API)]


# --- Fake adaptery ---------------------------------------------------------


class _BaseFakeAdapter:
    provider = "fake"
    model = "fake-model-1"

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []


class InvalidJsonAdapter(_BaseFakeAdapter):
    """Odpoví textem, ve kterém není použitelný JSON."""

    def complete(self, prompt: str, purpose: LlmPurpose) -> AdapterResult:
        self.calls += 1
        self.prompts.append(prompt)
        return AdapterResult(
            text="Omlouvám se, ale tuto aplikaci neumím zařadit.",
            tokens_in=100,
            tokens_out=20,
            model=self.model,
            provider=self.provider,
        )


class AlwaysTimeoutAdapter(_BaseFakeAdapter):
    def complete(self, prompt: str, purpose: LlmPurpose) -> AdapterResult:
        self.calls += 1
        raise LlmTimeout("simulovaný timeout")


class ServerErrorThenOkAdapter(_BaseFakeAdapter):
    """První volání 5xx, druhé projde — ověřuje, že retry má smysl."""

    def complete(self, prompt: str, purpose: LlmPurpose) -> AdapterResult:
        self.calls += 1
        if self.calls == 1:
            raise LlmServerError("simulovaná 5xx")
        return _ok_result(self.provider, self.model)


class OkAdapter(_BaseFakeAdapter):
    def complete(self, prompt: str, purpose: LlmPurpose) -> AdapterResult:
        self.calls += 1
        self.prompts.append(prompt)
        return _ok_result(self.provider, self.model)


def _ok_result(provider: str, model: str) -> AdapterResult:
    text = json.dumps(
        {
            "klasifikace": "STREDNI",
            "zduvodneni": "Aplikace zpracovává osobní údaje zaměstnanců a důvěrná data.",
        },
        ensure_ascii=False,
    )
    return AdapterResult(
        text=text, tokens_in=123, tokens_out=45, model=model, provider=provider
    )


def _audit_rows(session: Session) -> list[LlmAudit]:
    return session.query(LlmAudit).all()


# --- Testy -----------------------------------------------------------------


def test_nevalidni_json_vede_na_fallback(session, answers, components):
    adapter = InvalidJsonAdapter()

    outcome = gateway.classify(session, answers, components, adapter=adapter)

    assert outcome.fallback_used is True
    assert outcome.llm_tier == Tier.MALA
    assert outcome.zduvodneni == gateway.FALLBACK_CLASSIFY_MESSAGE

    rows = _audit_rows(session)
    assert len(rows) == 1
    assert rows[0].error_code == "INVALID_RESPONSE"
    assert rows[0].uspech is False
    assert rows[0].fallback_used is True


def test_nevalidni_json_se_neopakuje(session, answers, components):
    """Retry je jen na přechodné chyby — špatný tvar odpovědi se neopakuje."""
    adapter = InvalidJsonAdapter()

    gateway.classify(session, answers, components, adapter=adapter)

    assert adapter.calls == 1


def test_timeout_pokazde_znamena_dve_volani_a_fallback(session, answers, components):
    adapter = AlwaysTimeoutAdapter()

    outcome = gateway.classify(session, answers, components, adapter=adapter)

    assert adapter.calls == 2  # 1 volání + přesně 1 retry
    assert outcome.fallback_used is True
    assert outcome.llm_tier == Tier.MALA

    rows = _audit_rows(session)
    assert len(rows) == 1
    assert rows[0].error_code == "TIMEOUT"
    assert rows[0].uspech is False
    assert rows[0].tokens_in == 0
    assert rows[0].tokens_out == 0


def test_5xx_pak_uspech_projde_bez_fallbacku(session, answers, components):
    adapter = ServerErrorThenOkAdapter()

    outcome = gateway.classify(session, answers, components, adapter=adapter)

    assert adapter.calls == 2
    assert outcome.fallback_used is False
    assert outcome.llm_tier == Tier.STREDNI

    rows = _audit_rows(session)
    assert len(rows) == 1
    assert rows[0].uspech is True
    assert rows[0].error_code is None


def test_uspesny_pruchod_zapise_tokeny(session, answers, components):
    adapter = OkAdapter()

    outcome = gateway.classify(session, answers, components, adapter=adapter)

    assert outcome.fallback_used is False
    assert outcome.zduvodneni.startswith("Aplikace zpracovává")

    rows = _audit_rows(session)
    assert len(rows) == 1
    row = rows[0]
    assert row.uspech is True
    assert row.tokens_in == 123
    assert row.tokens_out == 45
    assert row.provider == "fake"
    assert row.model == "fake-model-1"
    assert row.prompt_version == "v1"
    assert row.rules_version


def test_audit_nikdy_neobsahuje_text_promptu(session, answers, components):
    """Projde všechny sloupce všech řádků auditu a hledá stopy promptu."""
    gateway.classify(session, answers, components, adapter=OkAdapter())
    gateway.classify(session, answers, components, adapter=InvalidJsonAdapter())

    rows = _audit_rows(session)
    assert len(rows) == 2

    zakazane = [SENTINEL, "Sumarizace smluv", "<<<DATA_", "zduvodneni", "klasifikace"]
    for row in rows:
        for column in LlmAudit.__table__.columns:
            hodnota = str(getattr(row, column.name))
            for zakazany in zakazane:
                assert zakazany not in hodnota, f"{column.name} obsahuje {zakazany!r}"


def test_duplicity_fallback_pouzije_lokalni_kandidaty(session):
    from app.services.similarity import Candidate

    kandidati = [
        Candidate("id-1", "Sumarizace smluv", "Sumarizuje smlouvy", 0.91),
        Candidate("id-2", "Přepis schůzek", "Přepisuje audio", 0.35),
    ]

    outcome = gateway.find_duplicates(
        session, "Sumarizace smluv", "Sumarizuje smlouvy", kandidati, adapter=AlwaysTimeoutAdapter()
    )

    assert outcome.fallback_used is True
    assert [m.record_id for m in outcome.matches] == ["id-1"]
    assert outcome.matches[0].duvod == gateway.FALLBACK_DUPLICATE_REASON

    rows = _audit_rows(session)
    assert len(rows) == 1
    assert rows[0].ucel == LlmPurpose.DUPLICATES
    assert rows[0].error_code == "TIMEOUT"
