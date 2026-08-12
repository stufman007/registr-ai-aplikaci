"""AI kontextová věta u legislativních signálů (spec kap. 5.4, doplněk k v1).

Existence a kostra signálu (`app.services.regulatory.compute_flags`) zůstává
100% deterministická — tyto testy pokrývají jen doplňkovou vrstvu:
`gateway.classify(..., active_flags=...)` smí LLM vrátit `signal_context`
(`{zkratka: věta}`), gateway ho po validaci ořeže na povolené zkratky, na
maximální délku, a deanonymizuje. Fallback nebo chybějící pole → prázdný dict.

Fake adaptery jsou definované přímo tady (stejný vzor jako
`test_gateway_allowlist.py` / `test_fallback.py`), aby bylo vidět přesně, co
gateway dostane a co s tím udělá.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.llm import gateway
from app.llm.base import AdapterResult
from app.llm.mock import MockAdapter
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
    ViditelnostVystupu,
)
from app.services.regulatory import Flag, compute_flags


@pytest.fixture()
def session() -> Session:
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


def _answers(**overrides: object) -> DotaznikOdpovedi:
    zaklad: dict[str, object] = {
        "ucel": "Sumarizace smluv pro právní oddělení.",
        "pocet_uzivatelu": PocetUzivatelu.OD_10_DO_50,
        "kritictnost": Kritictnost.DULEZITY,
        "osobni_udaje": OsobniUdaje.KLIENTU,
        "rozhodovani": RozhodovaniOLidech.NE,
        "viditelnost": ViditelnostVystupu.NE,
        "citlivost": CitlivostDat.DUVERNA,
        "autonomie": Autonomie.CLOVEK_SCHVALUJE,
        "dopad": DopadChyby.PROVOZNI,
    }
    zaklad.update(overrides)
    return DotaznikOdpovedi(**zaklad)  # type: ignore[arg-type]


def _components() -> list[KomponentaInfo]:
    return [KomponentaInfo(provider=Provider.ANTHROPIC, hosting_type=HostingType.EXTERNI_API)]


def _gdpr_flag() -> Flag:
    return compute_flags(_answers(), _components())[0]


class SpyAdapter:
    """Zachytí prompt a vrátí předem danou odpověď (vzor z `test_gateway_allowlist.py`)."""

    provider = "spy"
    model = "spy-1"

    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str, purpose: LlmPurpose) -> AdapterResult:
        self.prompts.append(prompt)
        return AdapterResult(
            text=self.response, tokens_in=10, tokens_out=10, model=self.model, provider=self.provider
        )


class InvalidJsonAdapter:
    provider = "fake"
    model = "fake-model-1"

    def complete(self, prompt: str, purpose: LlmPurpose) -> AdapterResult:
        return AdapterResult(
            text="Tohle není JSON.", tokens_in=10, tokens_out=5, model=self.model, provider=self.provider
        )


def _response(zduvodneni: str, signal_context: dict[str, str]) -> str:
    return json.dumps(
        {"klasifikace": "STREDNI", "zduvodneni": zduvodneni, "signal_context": signal_context},
        ensure_ascii=False,
    )


# --- Allowlist zkratek -------------------------------------------------------


def test_signal_context_prijme_jen_aktivni_zkratku(session: Session) -> None:
    flag = _gdpr_flag()  # jediná aktivní zkratka je GDPR
    adapter = SpyAdapter(
        _response("Zdůvodnění.", {"GDPR": "Věta o GDPR.", "AI-ACT": "Neměl by se objevit."})
    )

    outcome = gateway.classify(session, _answers(), _components(), active_flags=[flag], adapter=adapter)

    assert outcome.signal_context == {"GDPR": "Věta o GDPR."}


def test_signal_context_bez_aktivnich_signalu_je_prazdny_i_kdyz_llm_neco_vrati(
    session: Session,
) -> None:
    adapter = SpyAdapter(_response("Zdůvodnění.", {"GDPR": "Vymyšlený signál."}))

    outcome = gateway.classify(session, _answers(), _components(), active_flags=[], adapter=adapter)

    assert outcome.signal_context == {}


# --- Ořez délky ---------------------------------------------------------------


def test_signal_context_se_orizne_na_max_delku(session: Session) -> None:
    flag = _gdpr_flag()
    dlouha_veta = "A" * (gateway.MAX_SIGNAL_CONTEXT_LENGTH + 100)
    adapter = SpyAdapter(_response("Zdůvodnění.", {"GDPR": dlouha_veta}))

    outcome = gateway.classify(session, _answers(), _components(), active_flags=[flag], adapter=adapter)

    veta = outcome.signal_context["GDPR"]
    assert len(veta) == gateway.MAX_SIGNAL_CONTEXT_LENGTH + 1  # + ořezová elipsa
    assert veta.endswith("…")


# --- Fallback -------------------------------------------------------------


def test_fallback_vraci_prazdny_signal_context(session: Session) -> None:
    flag = _gdpr_flag()
    adapter = InvalidJsonAdapter()

    outcome = gateway.classify(session, _answers(), _components(), active_flags=[flag], adapter=adapter)

    assert outcome.fallback_used is True
    assert outcome.signal_context == {}


def test_chybejici_pole_v_odpovedi_znamena_prazdny_signal_context(session: Session) -> None:
    """`signal_context` je v `ClassificationSuggestion` volitelné — LLM ho
    nemusí vrátit vůbec, validace i tak projde."""
    flag = _gdpr_flag()
    adapter = SpyAdapter(json.dumps({"klasifikace": "STREDNI", "zduvodneni": "Zdůvodnění."}))

    outcome = gateway.classify(session, _answers(), _components(), active_flags=[flag], adapter=adapter)

    assert outcome.fallback_used is False
    assert outcome.signal_context == {}


# --- Deanonymizace -------------------------------------------------------


def test_signal_context_se_deanonymizuje(session: Session) -> None:
    """Placeholder v odpovědi LLM se deanonymizuje jen tehdy, pokud ho
    anonymizér skutečně vytvořil (jméno se muselo objevit v odchozím textu) —
    proto `ucel` obsahuje stejné jméno jako `known_contacts`, stejný vzor jako
    `test_gateway_allowlist.test_zduvodneni_se_po_validaci_deanonymizuje`."""
    answers = _answers(ucel="Sumarizace smluv, spravuje ji Jan Novák.")
    flag = compute_flags(answers, _components())[0]
    known_contacts = [("Jan Novák", "jan.novak@firma.cz")]
    adapter = SpyAdapter(_response("Zdůvodnění.", {"GDPR": "Spravuje ji [OSOBA_1]."}))

    outcome = gateway.classify(
        session,
        answers,
        _components(),
        active_flags=[flag],
        known_contacts=known_contacts,
        adapter=adapter,
    )

    assert outcome.signal_context["GDPR"] == "Spravuje ji Jan Novák."
    assert "[OSOBA_1]" not in outcome.signal_context["GDPR"]


def test_mock_adapter_vraci_deanonymizovany_signal_context(session: Session) -> None:
    """End-to-end přes skutečný `MockAdapter` (ne fake spy) — ověřuje, že mock
    umí feature demonstrovat bez API klíče (spec kap. 5.4, bod 6 zadání):
    placeholder z pseudonymizovaného textu se propíše do `signal_context` a po
    validaci se dosadí zpátky skutečné jméno."""
    answers = _answers(ucel="Sumarizace smluv, spravuje ji Jan Novák.")
    flag = compute_flags(answers, _components())[0]
    known_contacts = [("Jan Novák", "jan.novak@firma.cz")]

    outcome = gateway.classify(
        session,
        answers,
        _components(),
        active_flags=[flag],
        known_contacts=known_contacts,
        adapter=MockAdapter(),
    )

    assert outcome.fallback_used is False
    assert flag.zkratka in outcome.signal_context
    veta = outcome.signal_context[flag.zkratka]
    assert "Jan Novák" in veta
    assert "[OSOBA_1]" not in veta
