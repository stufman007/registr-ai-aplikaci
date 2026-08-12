"""Editace záznamu, optimistic locking, review light (Fáze 12, spec kap. 9 a 11.5).

Wizard zakládání (`app.routes.classification`) a editace sdílejí formulář
(`app_form.html`), validaci (`_validate_form`) a klasifikační krok
(`classify_step.html`, `_render_classification`) — tento modul jen dodává
jiný cíl formuláře a jinou logiku uložení, žádná ze šablon ani validací se
nekopíruje. Proto se sem importují „privátní" pomocné funkce z
`classification.py` přímo; `_classify` se volá vždy přes `classification.\
_classify(...)` (ne importem jménem), aby test monkeypatching
(`monkeypatch.setattr(classification, "_classify", ...)`) fungoval shodně
pro nové i editované záznamy.

Bezpečnostní a governance invarianty:

- Přístup: `require_user` + `check_owner_or_admin` — 403 i pro GET, ne jen
  skrytí tlačítka (spec kap. 3).
- Vyřazený záznam nejde editovat vůbec (400), bez ohledu na roli.
- **Optimistic locking**: DB `version` se porovná se skrytým polem formuláře
  těsně před zápisem; při neshodě `VersionConflict` (409) a nic se nezapíše.
- **Review light**: změna rizikových odpovědí (ot. 3–9) vynutí re-klasifikaci
  přes mezistav v session (`wizard_edit_{id}`) — přímé uložení je zakázané.
  Samotná změna AI komponent bez rizikové otázky nastaví `review_required`;
  re-klasifikace ho vždy vrátí zpět na `False`.
- Kontrola duplicit se při editaci záměrně nedělá (vědomý dluh, spec kap. 2).
"""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.auth import SessionUser, check_owner_or_admin, require_user
from app.db import get_db
from app.flash import set_flash
from app.models import AiComponent, Application
from app.questionnaire import CHOICE_QUESTIONS, FREE_TEXT_QUESTION
from app.routes import classification
from app.routes.classification import (
    _compose_zduvodneni,
    _empty_component,
    _field,
    _known_contacts,
    _redirect,
    _render_classification,
    _render_form,
    _stored_llm_tier,
    _validate_form,
)
from app.schemas import AuditAction, HostingType, Provider, Stav, Tier
from app.security import VersionConflict, verify_csrf
from app.services import history
from app.services.policy import ManualBelowSuggestion, compute_minimum, effective_tier
from app.services.regulatory import compute_flags, flags_to_dicts
from app.templating import templates

router = APIRouter(tags=["edit"])

#: Otázky 3–9 dotazníku (spec kap. 9) — jejich změna vynutí re-klasifikaci.
RISKY_QUESTION_FIELDS = frozenset(q.field for q in CHOICE_QUESTIONS if q.number >= 3)


def _wizard_key(application_id: str) -> str:
    return f"wizard_edit_{application_id}"


# --- Přístup a načtení záznamu ----------------------------------------------


def _load_editable_application(
    db: Session, application_id: str, user: SessionUser
) -> Application:
    """Společný guard pro všechny edit routy: 404 → 403 → 400 (vyřazeno)."""
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Aplikace nenalezena."
        )
    if not check_owner_or_admin(user, application):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nemáte oprávnění tento záznam upravit.",
        )
    if application.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vyřazený záznam nelze editovat.",
        )
    return application


# --- Převod záznamu na tvar formuláře ---------------------------------------


def _application_to_values(application: Application) -> dict[str, Any]:
    """Předvyplní formulář ze záznamu — stejný tvar jako `_validate_form`."""
    komponenty = [
        {
            "provider": c.provider.name,
            "model_name": c.model_name,
            "purpose": c.purpose,
            "hosting_type": c.hosting_type.name,
        }
        for c in application.components
    ]
    return {
        "nazev": application.nazev,
        "stav": application.stav.name,
        "vlastnik_jmeno": application.vlastnik_jmeno,
        "vlastnik_email": application.vlastnik_email,
        "zastupce_jmeno": application.zastupce_jmeno,
        "zastupce_email": application.zastupce_email,
        "spravce_jmeno": application.spravce_jmeno,
        "spravce_email": application.spravce_email,
        "komponenty": komponenty or [_empty_component()],
        "odpovedi": dict(application.dotaznik_odpovedi),
    }


def _field_snapshot(values: dict[str, Any]) -> dict[str, str]:
    """Plochý snapshot pro `history.diff_fields` — základní pole + všechny
    odpovědi dotazníku (i rizikové, ty se zapíší jen když se skutečně změní)."""
    odpovedi = values["odpovedi"]
    snapshot = {
        "nazev": values["nazev"],
        "stav": values["stav"],
        "vlastnik_jmeno": values["vlastnik_jmeno"],
        "vlastnik_email": values["vlastnik_email"],
        "zastupce_jmeno": values["zastupce_jmeno"],
        "zastupce_email": values["zastupce_email"],
        "spravce_jmeno": values["spravce_jmeno"],
        "spravce_email": values["spravce_email"],
        "popis": odpovedi[FREE_TEXT_QUESTION.field],
    }
    for question in CHOICE_QUESTIONS:
        snapshot[question.field] = odpovedi[question.field]
    return snapshot


def _risky_answers_changed(old_values: dict[str, Any], new_values: dict[str, Any]) -> bool:
    old_odpovedi, new_odpovedi = old_values["odpovedi"], new_values["odpovedi"]
    return any(
        str(old_odpovedi.get(field, "")) != str(new_odpovedi.get(field, ""))
        for field in RISKY_QUESTION_FIELDS
    )


def _component_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (row["provider"], row["model_name"], row["purpose"], row["hosting_type"])


def _components_changed(old_rows: list[dict[str, str]], new_rows: list[dict[str, str]]) -> bool:
    return Counter(_component_key(r) for r in old_rows) != Counter(
        _component_key(r) for r in new_rows
    )


def _summarize_components(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "(žádná komponenta)"
    return "; ".join(f"{r['provider']}/{r['model_name']} ({r['hosting_type']})" for r in rows)


# --- Zápis změn --------------------------------------------------------------


def _apply_field_changes(
    db: Session,
    application: Application,
    old_values: dict[str, Any],
    values: dict[str, Any],
    actor: str,
) -> None:
    """Základní údaje + kontakty + odpovědi dotazníku: historie po polích + zápis."""
    old_snapshot, new_snapshot = _field_snapshot(old_values), _field_snapshot(values)
    for pole, stara, nova in history.diff_fields(old_snapshot, new_snapshot, old_snapshot.keys()):
        history.log(db, application.id, actor, AuditAction.UPDATE, pole=pole, stara=stara, nova=nova)

    application.nazev = values["nazev"]
    application.stav = Stav[values["stav"]]
    application.vlastnik_jmeno = values["vlastnik_jmeno"]
    application.vlastnik_email = values["vlastnik_email"]
    application.zastupce_jmeno = values["zastupce_jmeno"]
    application.zastupce_email = values["zastupce_email"]
    application.spravce_jmeno = values["spravce_jmeno"]
    application.spravce_email = values["spravce_email"]
    application.popis = values["odpovedi"][FREE_TEXT_QUESTION.field]
    application.dotaznik_odpovedi = dict(values["odpovedi"])


def _replace_components(
    db: Session,
    application: Application,
    old_rows: list[dict[str, str]],
    new_rows: list[dict[str, str]],
    actor: str,
    *,
    mark_review_required: bool,
) -> None:
    """Nahradí AI komponenty a zapíše jednu historii (spec kap. 9 — review light)."""
    history.log(
        db,
        application.id,
        actor,
        AuditAction.UPDATE,
        pole="komponenty",
        stara=_summarize_components(old_rows),
        nova=_summarize_components(new_rows),
    )
    application.components.clear()
    for row in new_rows:
        application.components.append(
            AiComponent(
                provider=Provider[row["provider"]],
                model_name=row["model_name"],
                purpose=row["purpose"],
                hosting_type=HostingType[row["hosting_type"]],
            )
        )
    if mark_review_required:
        application.review_required = True


# --- GET/POST /aplikace/{id}/upravit ----------------------------------------


@router.get(
    "/aplikace/{application_id}/upravit",
    response_class=HTMLResponse,
    name="edit_application_form",
)
def edit_application_form(
    request: Request,
    application_id: str,
    user: Annotated[SessionUser, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    application = _load_editable_application(db, application_id, user)
    values = _application_to_values(application)
    return _render_form(
        request,
        user,
        values,
        {},
        form_action=f"/aplikace/{application_id}/upravit",
        cancel_url=f"/aplikace/{application_id}",
        is_edit=True,
        version=application.version,
    )


@router.post("/aplikace/{application_id}/upravit", dependencies=[Depends(verify_csrf)])
async def submit_edit(
    request: Request,
    application_id: str,
    user: Annotated[SessionUser, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Validace → detekce rizikové/komponentní změny → přímé uložení nebo
    re-klasifikace. Policy floor edit nemění — jen ho promítne CLASSIFY krok."""
    application = _load_editable_application(db, application_id, user)

    form = await request.form()
    values, errors = _validate_form(form)
    if errors:
        return _render_form(
            request,
            user,
            values,
            errors,
            status.HTTP_400_BAD_REQUEST,
            form_action=f"/aplikace/{application_id}/upravit",
            cancel_url=f"/aplikace/{application_id}",
            is_edit=True,
            version=application.version,
        )

    version_raw = _field(form, "version")
    try:
        version_from_form = int(version_raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Neplatná verze formuláře."
        ) from None

    old_values = _application_to_values(application)

    if _risky_answers_changed(old_values, values):
        request.session[_wizard_key(application_id)] = {
            "values": values,
            "version": version_from_form,
            "duplicate_reason": None,
        }
        return _redirect(f"/aplikace/{application_id}/upravit/klasifikace")

    # Optimistic locking: porovnat DB verzi se skrytým polem TĚSNĚ před
    # zápisem — na neshodu se nikdy nesahá na atributy záznamu (spec kap. 11.5).
    if application.version != version_from_form:
        raise VersionConflict("Záznam mezitím změnil někdo jiný.")

    _apply_field_changes(db, application, old_values, values, user.username)

    if _components_changed(old_values["komponenty"], values["komponenty"]):
        _replace_components(
            db,
            application,
            old_values["komponenty"],
            values["komponenty"],
            user.username,
            mark_review_required=True,
        )

    application.version += 1
    application.updated_by = user.username
    db.commit()

    set_flash(request, f"Aplikace „{application.nazev}“ byla upravena.")
    return _redirect(f"/aplikace/{application_id}")


# --- GET/POST /aplikace/{id}/upravit/klasifikace ----------------------------


@router.get(
    "/aplikace/{application_id}/upravit/klasifikace",
    response_class=HTMLResponse,
    name="edit_application_classification",
)
def edit_classification_step(
    request: Request,
    application_id: str,
    user: Annotated[SessionUser, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Návrh AI · povinné minimum · výsledný tier · signály — stejná logika
    jako u založení (`classification.classification_step`), jen nad
    mezistavem `wizard_edit_{id}` a bez kroku duplicit."""
    application = _load_editable_application(db, application_id, user)

    state = request.session.get(_wizard_key(application_id))
    if not isinstance(state, dict) or not isinstance(state.get("values"), dict):
        return _redirect(f"/aplikace/{application_id}/upravit")

    values = state["values"]
    answers = classification._answers_from(values)
    components = classification._components_from(values)

    # Signály se počítají PŘED voláním LLM — jdou do promptu jako allowlist
    # kontext pro `signal_context` (spec kap. 5.4), stejně jako ve wizardu.
    minimum = compute_minimum(answers, components)
    flags = compute_flags(answers, components)
    outcome = classification._classify(
        db, answers, components, active_flags=flags, known_contacts=_known_contacts(db)
    )
    navrh = effective_tier(outcome.llm_tier, minimum)
    flags_data = flags_to_dicts(flags, outcome.signal_context)

    state["klasifikace"] = {
        "llm_tier": outcome.llm_tier.name if outcome.llm_tier else None,
        "zduvodneni": outcome.zduvodneni,
        "fallback_used": outcome.fallback_used,
        "minimum_tier": minimum.tier.name,
        "navrh_tier": navrh.name,
        "flags": flags_data,
        "signal_context": outcome.signal_context,
    }
    request.session[_wizard_key(application_id)] = state

    return _render_classification(
        request,
        user,
        state,
        minimum,
        flags_data,
        navrh,
        selected=navrh,
        action_url=f"/aplikace/{application_id}/upravit/klasifikace",
        back_url=f"/aplikace/{application_id}/upravit",
    )


@router.post(
    "/aplikace/{application_id}/upravit/klasifikace", dependencies=[Depends(verify_csrf)]
)
async def confirm_edit_classification(
    request: Request,
    application_id: str,
    user: Annotated[SessionUser, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Potvrzení re-klasifikace: uloží změny + novou klasifikaci najednou.

    Optimistic locking se ověří tady (ne dřív) — mezi krokem klasifikace a
    potvrzením mohl záznam změnit někdo jiný. `review_required` se re-klasifikací
    vždy vrátí na `False`, i když se zároveň měnily AI komponenty (spec kap. 9).
    """
    application = _load_editable_application(db, application_id, user)

    key = _wizard_key(application_id)
    state = request.session.get(key)
    if not isinstance(state, dict) or not isinstance(state.get("values"), dict):
        return _redirect(f"/aplikace/{application_id}/upravit")

    values = state["values"]
    answers = classification._answers_from(values)
    components = classification._components_from(values)

    minimum = compute_minimum(answers, components)
    flags = compute_flags(answers, components)
    # AI kontextová věta se znovu nevolá — použije se ta z klasifikačního
    # kroku (session), stejně jako `llm_tier`/`zduvodneni` (spec kap. 5.4).
    signal_context = (state.get("klasifikace") or {}).get("signal_context") or {}
    flags_data = flags_to_dicts(flags, signal_context)
    llm_tier = _stored_llm_tier(state)
    navrh = effective_tier(llm_tier, minimum)

    form = await request.form()
    poznamka = _field(form, "poznamka")
    tier_raw = _field(form, "tier")
    manual = Tier[tier_raw] if tier_raw in Tier.__members__ else None

    render_kwargs = {
        "action_url": f"/aplikace/{application_id}/upravit/klasifikace",
        "back_url": f"/aplikace/{application_id}/upravit",
    }

    if manual is None:
        return _render_classification(
            request, user, state, minimum, flags_data, navrh, navrh, poznamka,
            error="Vyberte výsledný governance tier.",
            status_code=status.HTTP_400_BAD_REQUEST,
            **render_kwargs,
        )

    # Pořadí je záměrné (stejně jako u založení): policy floor se ověří dřív
    # než formulářová pravidla, aby ručně sestavený POST bez poznámky skončil
    # na 400 z `PolicyViolation`. `actor_is_admin` rozlišuje, kdo smí snížit
    # proti návrhu AI (spec kap. 5.5) — wizard tady slouží oběma rolím.
    try:
        vysledny = effective_tier(
            llm_tier, minimum, manual_tier=manual, actor_is_admin=user.is_admin
        )
    except ManualBelowSuggestion as exc:
        return _render_classification(
            request, user, state, minimum, flags_data, navrh, manual, poznamka,
            error=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
            **render_kwargs,
        )

    if vysledny != navrh and not poznamka:
        return _render_classification(
            request, user, state, minimum, flags_data, navrh, manual, poznamka,
            error="Při změně proti navrhovanému tieru je poznámka povinná.",
            status_code=status.HTTP_400_BAD_REQUEST,
            **render_kwargs,
        )

    version_from_form = state.get("version")
    if not isinstance(version_from_form, int) or application.version != version_from_form:
        raise VersionConflict("Záznam mezitím změnil někdo jiný.")

    old_values = _application_to_values(application)
    _apply_field_changes(db, application, old_values, values, user.username)

    if _components_changed(old_values["komponenty"], values["komponenty"]):
        _replace_components(
            db,
            application,
            old_values["komponenty"],
            values["komponenty"],
            user.username,
            mark_review_required=False,
        )

    application.klasifikace_llm = llm_tier
    application.klasifikace_minimum = minimum.tier
    application.klasifikace = vysledny
    application.klasifikace_zduvodneni = _compose_zduvodneni(state, minimum, llm_tier)
    application.klasifikace_priznaky = flags_data
    application.klasifikace_potvrdil = user.username
    application.klasifikace_poznamka = poznamka or None
    application.review_required = False
    application.version += 1
    application.updated_by = user.username

    history.log(
        db,
        application.id,
        user.username,
        AuditAction.CLASSIFY,
        pole="klasifikace",
        nova=vysledny.name,
        reason=poznamka or None,
    )

    db.commit()
    request.session.pop(key, None)
    set_flash(request, f"Aplikace „{application.nazev}“ byla překlasifikována.")
    return _redirect(f"/aplikace/{application_id}")
