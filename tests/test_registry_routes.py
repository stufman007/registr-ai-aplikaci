"""Testy registru — seznam a detail karty (Fáze 10, spec kap. 12 UI obrazovky 2 a 4).

`get_db` je pro tento soubor přepsaný na in-memory SQLite (`StaticPool` drží
jedno sdílené spojení, aby data vložená přímo přes `Session` viděl i request
přes `TestClient`). Session se podvrhuje stejným vzorem jako v `test_auth.py`,
ale přes vlastní testovací router s jinou cestou — soubory se nesmí spoléhat
na pořadí importu napříč testovacími moduly.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import SESSION_USER_KEY
from app.db import Base, get_db
from app.main import app
from app.models import AiComponent, Application
from app.schemas import HostingType, Provider, Stav, Tier
from app.ui_texts import REVIEW_BADGE_TOOLTIP

# --- in-memory DB sdílená přes všechny requesty v tomto souboru ------------

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def _override_get_db() -> Iterator[Session]:
    db = _TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _registry_db() -> Iterator[None]:
    """Čerstvé schéma + `get_db` override jen po dobu testů v tomto souboru."""
    Base.metadata.create_all(bind=_engine)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=_engine)


# --- vlastní testovací router pro podvržení session (vzor z test_auth.py) --

_test_router = APIRouter()


@_test_router.post("/_test/registry/session-login")
async def _session_login(request: Request) -> dict[str, bool]:
    request.session[SESSION_USER_KEY] = await request.json()
    return {"ok": True}


@_test_router.post("/_test/registry/session-logout")
async def _session_logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


app.include_router(_test_router)

client = TestClient(app)


def _login(**session_data: object) -> None:
    assert client.post("/_test/registry/session-login", json=session_data).status_code == 200


def _logout() -> None:
    assert client.post("/_test/registry/session-logout").status_code == 200


@pytest.fixture(autouse=True)
def _clean_session() -> None:
    client.cookies.clear()


# --- seed helper -------------------------------------------------------------


def _make_application(**overrides: object) -> Application:
    defaults: dict[str, object] = dict(
        nazev="Sumarizátor smluv",
        popis="Shrnuje nahrané smlouvy do bodů pro právní tým.",
        vlastnik_jmeno="Jana Nová",
        vlastnik_email="jana.nova@example.com",
        zastupce_jmeno="Petr Svoboda",
        zastupce_email="petr.svoboda@example.com",
        spravce_jmeno="Tomáš Malý",
        spravce_email="tomas.maly@example.com",
        klasifikace_llm=Tier.MALA,
        klasifikace_minimum=Tier.MALA,
        klasifikace=Tier.MALA,
        klasifikace_zduvodneni="Nízké riziko, interní pomocný nástroj.",
        klasifikace_potvrdil="jana.nova",
        dotaznik_odpovedi={},
        stav=Stav.PILOT,
        created_by="jana.nova",
        updated_by="jana.nova",
    )
    defaults.update(overrides)
    return Application(**defaults)


def _seed(**overrides: object) -> Application:
    """Uloží aplikaci přímo přes session (bez create route — ta je Fáze 11)."""
    session = _TestSessionLocal()
    try:
        entity = _make_application(**overrides)
        session.add(entity)
        session.commit()
        session.refresh(entity)
        return entity
    finally:
        session.close()


def _seed_many(count: int, **overrides: object) -> list[Application]:
    """Nasadí `count` aplikací s předvídatelnými, abecedně řaditelnými názvy."""
    return [_seed(nazev=f"Aplikace {i:02d}", **overrides) for i in range(1, count + 1)]


# --- GET / -------------------------------------------------------------------


def test_get_root_redirects_anonymous_to_login() -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_list_shows_active_application_for_logged_in_user() -> None:
    active = _seed()
    _login(username="jana.nova", email="jana.nova@example.com", roles=["user"])

    response = client.get("/")
    assert response.status_code == 200
    assert active.nazev in response.text


def test_retired_application_hidden_from_regular_user_list() -> None:
    retired = _seed(
        nazev="Starý nástroj",
        deleted_at=datetime.now(timezone.utc),
        deleted_by="admin",
        delete_reason="Nahrazeno novým nástrojem.",
    )
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/")
    assert response.status_code == 200
    assert retired.nazev not in response.text


def test_admin_sees_retired_applications_with_query_param() -> None:
    retired = _seed(
        nazev="Starý nástroj",
        deleted_at=datetime.now(timezone.utc),
        deleted_by="admin",
        delete_reason="Nahrazeno novým nástrojem.",
    )
    _login(username="admin", email="admin@example.com", roles=["user", "admin"])

    response = client.get("/", params={"zobrazit": "vyrazene"})
    assert response.status_code == 200
    assert retired.nazev in response.text


def test_regular_user_retired_query_param_is_ignored() -> None:
    retired = _seed(
        nazev="Starý nástroj",
        deleted_at=datetime.now(timezone.utc),
        deleted_by="admin",
        delete_reason="Nahrazeno novým nástrojem.",
    )
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"zobrazit": "vyrazene"})
    assert response.status_code == 200
    assert retired.nazev not in response.text


def test_stav_filter_narrows_list() -> None:
    pilot = _seed(nazev="Pilotní nástroj", stav=Stav.PILOT)
    provoz = _seed(nazev="Provozní nástroj", stav=Stav.PROVOZ)
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"stav": "PROVOZ"})
    assert response.status_code == 200
    assert provoz.nazev in response.text
    assert pilot.nazev not in response.text


def test_unknown_stav_filter_value_is_ignored() -> None:
    pilot = _seed(nazev="Pilotní nástroj", stav=Stav.PILOT)
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"stav": "NESMYSL"})
    assert response.status_code == 200
    assert pilot.nazev in response.text


# --- Per-sloupcové filtry seznamu registru (nazev/vlastnik/signal/provider) --


def test_nazev_filter_matches_contains_case_insensitive() -> None:
    match = _seed(nazev="Sumarizátor smluv")
    other = _seed(nazev="Klasifikátor dokumentů")
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"nazev": "sumariz"})
    assert response.status_code == 200
    assert match.nazev in response.text
    assert other.nazev not in response.text


def test_nazev_filter_percent_literal_does_not_match_all() -> None:
    """LIKE escape (spec požadavku): `%` v hodnotě filtru se bere doslova,
    ne jako SQL wildcard — jinak by `nazev=%` matchovalo úplně vše."""
    alfa = _seed(nazev="Alfa nástroj")
    beta = _seed(nazev="Beta nástroj")
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"nazev": "%"})
    assert response.status_code == 200
    assert alfa.nazev not in response.text
    assert beta.nazev not in response.text


def test_vlastnik_filter_matches_email() -> None:
    match = _seed(
        nazev="Aplikace Jany",
        vlastnik_jmeno="Jana Nová",
        vlastnik_email="jana.nova@example.com",
    )
    other = _seed(
        nazev="Aplikace Petra",
        vlastnik_jmeno="Petr Svoboda",
        vlastnik_email="petr.svoboda@example.com",
    )
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"vlastnik": "jana.nova@example.com"})
    assert response.status_code == 200
    assert match.nazev in response.text
    assert other.nazev not in response.text


def test_signal_filter_gdpr_matches_only_flagged_records() -> None:
    with_gdpr = _seed(
        nazev="S GDPR signálem",
        klasifikace_priznaky=[
            {
                "zkratka": "GDPR",
                "titulek": "GDPR — údaje klientů",
                "detail": "Aplikace zpracovává osobní údaje klientů.",
                "reason_code": "PERSONAL_DATA_PROCESSING",
                "source": "deterministic_rule",
            }
        ],
    )
    without_gdpr = _seed(nazev="Bez signálu")
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"signal": "GDPR"})
    assert response.status_code == 200
    assert with_gdpr.nazev in response.text
    assert without_gdpr.nazev not in response.text


def test_provider_filter_matches_component_provider() -> None:
    anthropic_app = _seed(
        nazev="Anthropic aplikace",
        components=[
            AiComponent(
                provider=Provider.ANTHROPIC,
                model_name="claude-3",
                purpose="Sumarizace",
                hosting_type=HostingType.EXTERNI_API,
            )
        ],
    )
    openai_app = _seed(
        nazev="OpenAI aplikace",
        components=[
            AiComponent(
                provider=Provider.OPENAI,
                model_name="gpt-4o",
                purpose="Sumarizace",
                hosting_type=HostingType.EXTERNI_API,
            )
        ],
    )
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"provider": "ANTHROPIC"})
    assert response.status_code == 200
    assert anthropic_app.nazev in response.text
    assert openai_app.nazev not in response.text


def test_combined_filters_apply_as_and() -> None:
    match = _seed(nazev="Shoda", stav=Stav.PILOT, klasifikace=Tier.STREDNI)
    wrong_stav = _seed(nazev="Shoda jiný stav", stav=Stav.PROVOZ, klasifikace=Tier.STREDNI)
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get(
        "/", params={"nazev": "Shoda", "stav": "PILOT", "tier": "STREDNI"}
    )
    assert response.status_code == 200
    assert match.nazev in response.text
    assert wrong_stav.nazev not in response.text


def test_unknown_signal_and_provider_filter_values_are_ignored() -> None:
    aplikace = _seed(nazev="Bez filtru")
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"signal": "NESMYSL", "provider": "NESMYSL"})
    assert response.status_code == 200
    assert aplikace.nazev in response.text


# --- Multiple choice (více hodnot v jednom sloupci, OR uvnitř sloupce) ------


def test_multi_tier_filter_combines_as_or() -> None:
    mala = _seed(nazev="Malá aplikace", klasifikace=Tier.MALA)
    stredni = _seed(nazev="Střední aplikace", klasifikace=Tier.STREDNI)
    velka = _seed(nazev="Velká aplikace", klasifikace=Tier.VELKA)
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"tier": ["MALA", "VELKA"]})
    assert response.status_code == 200
    assert mala.nazev in response.text
    assert velka.nazev in response.text
    assert stredni.nazev not in response.text


def test_multi_provider_filter_combines_as_or() -> None:
    anthropic_app = _seed(
        nazev="Anthropic aplikace",
        components=[
            AiComponent(
                provider=Provider.ANTHROPIC,
                model_name="claude-3",
                purpose="Sumarizace",
                hosting_type=HostingType.EXTERNI_API,
            )
        ],
    )
    openai_app = _seed(
        nazev="OpenAI aplikace",
        components=[
            AiComponent(
                provider=Provider.OPENAI,
                model_name="gpt-4o",
                purpose="Sumarizace",
                hosting_type=HostingType.EXTERNI_API,
            )
        ],
    )
    azure_app = _seed(
        nazev="Azure aplikace",
        components=[
            AiComponent(
                provider=Provider.AZURE,
                model_name="gpt-4",
                purpose="Sumarizace",
                hosting_type=HostingType.EXTERNI_API,
            )
        ],
    )
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"provider": ["ANTHROPIC", "OPENAI"]})
    assert response.status_code == 200
    assert anthropic_app.nazev in response.text
    assert openai_app.nazev in response.text
    assert azure_app.nazev not in response.text


def test_multi_signal_filter_combines_as_or() -> None:
    with_gdpr = _seed(
        nazev="GDPR aplikace",
        klasifikace_priznaky=[
            {
                "zkratka": "GDPR",
                "titulek": "GDPR — údaje klientů",
                "detail": "Aplikace zpracovává osobní údaje klientů.",
                "reason_code": "PERSONAL_DATA_PROCESSING",
                "source": "deterministic_rule",
            }
        ],
    )
    with_dora = _seed(
        nazev="DORA aplikace",
        klasifikace_priznaky=[
            {
                "zkratka": "DORA",
                "titulek": "DORA — finanční sektor",
                "detail": "Aplikace podporuje regulovanou finanční službu.",
                "reason_code": "FINANCIAL_SERVICE",
                "source": "deterministic_rule",
            }
        ],
    )
    without_signal = _seed(nazev="Bez signálu")
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"signal": ["GDPR", "DORA"]})
    assert response.status_code == 200
    assert with_gdpr.nazev in response.text
    assert with_dora.nazev in response.text
    assert without_signal.nazev not in response.text


def test_multi_filter_within_column_or_combines_with_other_columns_as_and() -> None:
    """Uvnitř sloupce OR (tier), mezi sloupci AND (tier × stav) — spolu."""
    match_mala = _seed(nazev="Shoda malá", klasifikace=Tier.MALA, stav=Stav.PILOT)
    match_velka = _seed(nazev="Shoda velká", klasifikace=Tier.VELKA, stav=Stav.PILOT)
    wrong_stav = _seed(nazev="Malá provoz", klasifikace=Tier.MALA, stav=Stav.PROVOZ)
    wrong_tier = _seed(nazev="Střední pilot", klasifikace=Tier.STREDNI, stav=Stav.PILOT)
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"tier": ["MALA", "VELKA"], "stav": "PILOT"})
    assert response.status_code == 200
    assert match_mala.nazev in response.text
    assert match_velka.nazev in response.text
    assert wrong_stav.nazev not in response.text
    assert wrong_tier.nazev not in response.text


def test_pagination_preserves_repeated_multi_filter_values() -> None:
    _seed_many(15, klasifikace=Tier.MALA)
    _seed_many(15, klasifikace=Tier.VELKA)
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"tier": ["MALA", "VELKA"]})
    assert response.status_code == 200
    assert "tier=MALA" in response.text
    assert "tier=VELKA" in response.text
    assert "strana=2" in response.text


def test_unknown_value_in_multi_filter_list_is_dropped_valid_ones_kept() -> None:
    pilot = _seed(nazev="Pilotní nástroj", stav=Stav.PILOT)
    provoz = _seed(nazev="Provozní nástroj", stav=Stav.PROVOZ)
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"stav": ["NESMYSL", "PILOT"]})
    assert response.status_code == 200
    assert pilot.nazev in response.text
    assert provoz.nazev not in response.text


def test_single_multi_filter_value_behaves_like_before() -> None:
    """Zpětná kompatibilita: jedna hodnota v novém multi-choice parametru
    filtruje stejně jako dřív s jednohodnotovým `stav`."""
    pilot = _seed(nazev="Pilotní nástroj", stav=Stav.PILOT)
    provoz = _seed(nazev="Provozní nástroj", stav=Stav.PROVOZ)
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"stav": "PILOT"})
    assert response.status_code == 200
    assert pilot.nazev in response.text
    assert provoz.nazev not in response.text


def test_clear_filters_link_only_shown_when_filter_active() -> None:
    _seed(nazev="Test aplikace")
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/")
    assert "Zrušit filtry" not in response.text

    response = client.get("/", params={"nazev": "Test"})
    assert "Zrušit filtry" in response.text


def test_pagination_preserves_all_active_filters() -> None:
    _seed_many(
        25,
        stav=Stav.PILOT,
        vlastnik_jmeno="Jana Nová",
        vlastnik_email="jana.nova@example.com",
    )
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get(
        "/", params={"stav": "PILOT", "vlastnik": "jana", "nazev": "Aplikace"}
    )
    assert response.status_code == 200
    assert "stav=PILOT" in response.text
    assert "vlastnik=jana" in response.text
    assert "nazev=Aplikace" in response.text
    assert "strana=2" in response.text


# --- GET /aplikace/{id} -------------------------------------------------------


def test_detail_404_for_unknown_id() -> None:
    _login(username="user", email="user@example.com", roles=["user"])
    response = client.get("/aplikace/does-not-exist")
    assert response.status_code == 404


def test_detail_shows_history_and_classification_for_owner() -> None:
    active = _seed()
    _login(username="jana.nova", email="jana.nova@example.com", roles=["user"])

    response = client.get(f"/aplikace/{active.id}")
    assert response.status_code == 200
    assert active.klasifikace_zduvodneni in response.text
    assert "Upravit" in response.text


def test_detail_shows_ai_kontext_when_present() -> None:
    """`_flags.html` (spec kap. 5.4): tooltip zobrazí AI kontextovou větu
    vizuálně odlišenou (`<em class="ai-kontext">`) hned za deterministickým
    detailem, když ji `klasifikace_priznaky` obsahuje."""
    active = _seed(
        klasifikace_priznaky=[
            {
                "zkratka": "GDPR",
                "titulek": "GDPR — údaje klientů",
                "detail": "Aplikace zpracovává osobní údaje klientů.",
                "reason_code": "PERSONAL_DATA_PROCESSING",
                "source": "deterministic_rule",
                "ai_kontext": "Tato aplikace ukládá e-maily klientů z kontaktního formuláře.",
            }
        ]
    )
    _login(username="jana.nova", email="jana.nova@example.com", roles=["user"])

    response = client.get(f"/aplikace/{active.id}")
    assert response.status_code == 200
    assert (
        "AI kontext: Tato aplikace ukládá e-maily klientů z kontaktního formuláře."
        in response.text
    )
    # Deterministický detail je pořád vidět vedle AI věty.
    assert "Aplikace zpracovává osobní údaje klientů." in response.text


def test_detail_flags_without_ai_kontext_render_without_extra_text() -> None:
    """Starý záznam / fallback bez `ai_kontext` — kostra signálu stačí,
    tooltip nezobrazí žádný AI text navíc (graceful degradation)."""
    active = _seed(
        klasifikace_priznaky=[
            {
                "zkratka": "GDPR",
                "titulek": "GDPR — údaje klientů",
                "detail": "Aplikace zpracovává osobní údaje klientů.",
                "reason_code": "PERSONAL_DATA_PROCESSING",
                "source": "deterministic_rule",
            }
        ]
    )
    _login(username="jana.nova", email="jana.nova@example.com", roles=["user"])

    response = client.get(f"/aplikace/{active.id}")
    assert response.status_code == 200
    assert "Aplikace zpracovává osobní údaje klientů." in response.text
    assert "AI kontext:" not in response.text


def test_detail_retired_hidden_for_regular_user_but_visible_for_admin() -> None:
    retired = _seed(
        nazev="Starý nástroj",
        deleted_at=datetime.now(timezone.utc),
        deleted_by="admin",
        delete_reason="Nahrazeno novým nástrojem.",
    )
    _login(username="user", email="user@example.com", roles=["user"])
    assert client.get(f"/aplikace/{retired.id}").status_code == 404

    _logout()
    _login(username="admin", email="admin@example.com", roles=["user", "admin"])
    response = client.get(f"/aplikace/{retired.id}")
    assert response.status_code == 200
    assert "VYŘAZENO" in response.text
    assert retired.delete_reason in response.text


# --- Paginace seznamu registru -------------------------------------------------


def test_pagination_first_page_shows_twenty_of_twentyfive() -> None:
    _seed_many(25)
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/")
    assert response.status_code == 200
    assert "Aplikace 01" in response.text
    assert "Aplikace 20" in response.text
    assert "Aplikace 21" not in response.text
    assert "strana 1 z 2" in response.text


def test_pagination_second_page_shows_remaining_five() -> None:
    _seed_many(25)
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"strana": "2"})
    assert response.status_code == 200
    assert "Aplikace 21" in response.text
    assert "Aplikace 25" in response.text
    assert "Aplikace 01" not in response.text
    assert "strana 2 z 2" in response.text


def test_pagination_out_of_range_page_falls_back_to_last_valid() -> None:
    _seed_many(25)
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"strana": "99"})
    assert response.status_code == 200
    assert "strana 2 z 2" in response.text
    assert "Aplikace 21" in response.text


def test_pagination_invalid_page_value_falls_back_to_first() -> None:
    _seed_many(25)
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"strana": "nesmysl"})
    assert response.status_code == 200
    assert "strana 1 z 2" in response.text
    assert "Aplikace 01" in response.text


def test_pagination_links_preserve_active_filters() -> None:
    _seed_many(25, stav=Stav.PILOT)
    _login(username="user", email="user@example.com", roles=["user"])

    response = client.get("/", params={"stav": "PILOT"})
    assert response.status_code == 200
    assert "stav=PILOT" in response.text
    assert "strana=2" in response.text


# --- Tooltip texty a rozbalitelné komponenty ------------------------------------


def test_detail_review_badge_shows_tooltip_text() -> None:
    active = _seed(review_required=True)
    _login(username="jana.nova", email="jana.nova@example.com", roles=["user"])

    response = client.get(f"/aplikace/{active.id}")
    assert response.status_code == 200
    assert REVIEW_BADGE_TOOLTIP in response.text


def test_list_component_details_element_present_when_multiple_components() -> None:
    multi = _seed(
        nazev="Multi-komponentní nástroj",
        components=[
            AiComponent(
                provider=Provider.OPENAI,
                model_name="gpt-4o",
                purpose="Sumarizace",
                hosting_type=HostingType.EXTERNI_API,
            ),
            AiComponent(
                provider=Provider.ANTHROPIC,
                model_name="claude-3",
                purpose="Klasifikace",
                hosting_type=HostingType.EXTERNI_API,
            ),
        ],
    )
    _login(username="jana.nova", email="jana.nova@example.com", roles=["user"])

    response = client.get("/")
    assert response.status_code == 200
    assert multi.nazev in response.text
    # `<details` samo o sobě teď matchuje i sloupcové multi-choice filtry
    # (vždy přítomné) — kontrolujeme konkrétně komponentové rozbalení.
    assert 'class="inline-details"' in response.text


def test_list_no_details_element_when_single_component() -> None:
    single = _seed(
        nazev="Jednoduchý nástroj",
        components=[
            AiComponent(
                provider=Provider.OPENAI,
                model_name="gpt-4o",
                purpose="Sumarizace",
                hosting_type=HostingType.EXTERNI_API,
            )
        ],
    )
    _login(username="jana.nova", email="jana.nova@example.com", roles=["user"])

    response = client.get("/")
    assert response.status_code == 200
    assert single.nazev in response.text
    # Sloupcové multi-choice filtry taky renderují `<details>` — kontrolujeme
    # konkrétně, že komponentové rozbalení (>1 komponenta) chybí.
    assert 'class="inline-details"' not in response.text
