"""Založení záznamu: dotazník → duplicity → klasifikace → potvrzení (Fáze 11).

Hlavní scénář aplikace (spec kap. 5, 6 a 14 obrazovka 3). Tři kroky, mezistav
**jen v session** pod jedním klíčem `wizard_new_app` — do databáze se nezapíše
nic, dokud člověk klasifikaci nepotvrdí.

Proč jeden session klíč (riziko R5): CSRF token je jeden per session a mezistav
jeden per session, takže „tlačítko zpět + znovu odeslat" ani fallback LLM nikdy
nerozbije pár token↔stav. Selhání LLM stav **nemaže** — uživatel o vyplněný
formulář nepřijde.

Bezpečnostní invarianty tohoto modulu:

- `klasifikace_minimum` se před uložením počítá **znovu z odpovědí**, nikdy se
  nebere hodnota ze session (session je klientská cookie, byť podepsaná).
- Ruční tier pod minimum vyhodí `PolicyViolation` → 400 (handler v `app.main`),
  a to i při ručně sestaveném POSTu mimo UI.
- Ruční snížení proti navrhovanému tieru (i nad minimem) smí jen admin —
  `effective_tier(..., actor_is_admin=user.is_admin)` u ne-admina vyhodí
  `ManualBelowSuggestion`, kterou tato route chytá a vrací jako 400 s
  formulářovou chybou (spec kap. 5.5).
- Každý POST má `Depends(verify_csrf)`.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.auth import SessionUser, require_user
from app.db import get_db
from app.flash import set_flash
from app.llm import gateway
from app.models import AiComponent, Application, Department
from app.questionnaire import CHOICE_QUESTIONS, FREE_TEXT_QUESTION, QUESTIONS
from app.schemas import (
    AuditAction,
    DotaznikOdpovedi,
    HostingType,
    KomponentaInfo,
    Provider,
    Stav,
    Tier,
)
from app.security import verify_csrf
from app.services import history
from app.services.policy import (
    ManualBelowSuggestion,
    MinimumResult,
    compute_minimum,
    effective_tier,
)
from app.services.regulatory import compute_flags, flags_to_dicts
from app.services.similarity import top_candidates
from app.templating import templates

router = APIRouter(tags=["classification"])

#: Klíč mezistavu wizardu v session (spec kap. 11, riziko R5).
WIZARD_SESSION_KEY = "wizard_new_app"

#: Maximální počet AI komponent v jednom formuláři (UI i backend).
MAX_COMPONENTS = 5

#: Délkové limity vstupů (spec kap. 7.3 — stejný strop jako u LLM payloadu).
MAX_SHORT_TEXT = 200
MAX_LONG_TEXT = 4000

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Modul-level aliasy Gateway funkcí. Zbytek aplikace zná jen `gateway`, ale
# testy potřebují jeden bod, kde se volání LLM dá deterministicky podvrhnout —
# `monkeypatch.setattr(classification, "_classify", ...)`. Volání jde přes
# globální jméno, takže se podvržení projeví i uvnitř handlerů.
_classify = gateway.classify
_find_duplicates = gateway.find_duplicates


# --- Práce se stavem wizardu ------------------------------------------------


def _empty_values() -> dict[str, Any]:
    """Prázdný formulář — jedna prázdná komponenta, žádná odpověď vybraná.

    `spravce_je_vlastnik` je u nového záznamu defaultně zaškrtnuté (schválený
    PO požadavek) — pole technického správce se skryjí a při uložení se
    převezmou z vlastníka, dokud uživatel checkbox sám neodškrtne.
    """
    return {
        "nazev": "",
        "stav": Stav.VYVOJ.name,
        "vlastnik_jmeno": "",
        "vlastnik_email": "",
        "vlastnik_oddeleni": "",
        "zastupce_jmeno": "",
        "zastupce_email": "",
        "zastupce_oddeleni": "",
        "spravce_jmeno": "",
        "spravce_email": "",
        "spravce_oddeleni": "",
        "spravce_je_vlastnik": True,
        "komponenty": [_empty_component()],
        "odpovedi": {question.field: "" for question in QUESTIONS},
    }


def _empty_component() -> dict[str, str]:
    return {"provider": "", "model_name": "", "purpose": "", "hosting_type": ""}


def _get_state(request: Request) -> dict[str, Any] | None:
    """Mezistav wizardu ze session; `None` při přímém vstupu na krok 2 nebo 3."""
    state = request.session.get(WIZARD_SESSION_KEY)
    if not isinstance(state, dict) or not isinstance(state.get("values"), dict):
        return None
    return state


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def _answers_from(values: dict[str, Any]) -> DotaznikOdpovedi:
    """Rekonstrukce `DotaznikOdpovedi` z uloženého stavu (hodnoty = názvy enumů)."""
    odpovedi = values["odpovedi"]
    return DotaznikOdpovedi(
        ucel=odpovedi[FREE_TEXT_QUESTION.field],
        **{
            question.field: question.enum[odpovedi[question.field]]  # type: ignore[index]
            for question in CHOICE_QUESTIONS
        },
    )


def _components_from(values: dict[str, Any]) -> list[KomponentaInfo]:
    return [
        KomponentaInfo(
            provider=Provider[row["provider"]],
            hosting_type=HostingType[row["hosting_type"]],
        )
        for row in values["komponenty"]
    ]


# --- Validace formuláře -----------------------------------------------------


def _field(form: FormData, name: str) -> str:
    raw = form.get(name)
    return raw.strip() if isinstance(raw, str) else ""


def _field_list(form: FormData, name: str) -> list[str]:
    return [raw.strip() if isinstance(raw, str) else "" for raw in form.getlist(name)]


def _check_text(
    errors: dict[str, str], name: str, value: str, popisek: str, max_length: int
) -> None:
    """Povinné pole + délkový strop. Chyba se zapíše pod jménem form pole."""
    if not value:
        errors[name] = f"{popisek} je povinné pole."
    elif len(value) > max_length:
        errors[name] = f"{popisek} smí mít nejvýš {max_length} znaků."


def _active_department_names(db: Session) -> list[str]:
    """Aktivní oddělení pro roletku formuláře (spec kap. 4.5), seřazená podle názvu.

    Prázdný seznam (číselník nemá žádnou aktivní položku) je záměrně platný
    stav — `_validate_contacts` pak pole oddělení nevyžaduje (graceful
    degradation), aby chybějící administrace číselníku nezablokovala založení
    záznamu.
    """
    rows = db.execute(
        select(Department.nazev)
        .where(Department.aktivni.is_(True))
        .order_by(Department.nazev)
    ).all()
    return [row[0] for row in rows]


def _validate_contacts(
    form: FormData,
    values: dict[str, Any],
    errors: dict[str, str],
    active_departments: list[str],
) -> None:
    role_popisky = {
        "vlastnik": "Vlastník",
        "zastupce": "Zástupce",
        "spravce": "Technický správce",
    }

    # Checkbox „Vlastník je zároveň technický správce" — když je zaškrtnutý,
    # pole správce se ve formuláři skryjí (app.js) a validace/hodnoty správce
    # se dál v této funkci PŘEVEZMOU z vlastníka na backendu (spec: nespoléhat
    # na JS). Zástupce zůstává vždy samostatný.
    spravce_je_vlastnik = _field(form, "spravce_je_vlastnik") == "1"
    values["spravce_je_vlastnik"] = spravce_je_vlastnik

    for role, popisek in role_popisky.items():
        if role == "spravce" and spravce_je_vlastnik:
            continue  # doplní se po smyčce z hodnot vlastníka

        jmeno_field, email_field, oddeleni_field = (
            f"{role}_jmeno",
            f"{role}_email",
            f"{role}_oddeleni",
        )

        jmeno = _field(form, jmeno_field)
        values[jmeno_field] = jmeno
        _check_text(errors, jmeno_field, jmeno, f"{popisek} — jméno", MAX_SHORT_TEXT)

        email = _field(form, email_field)
        values[email_field] = email
        _check_text(errors, email_field, email, f"{popisek} — e-mail", MAX_SHORT_TEXT)
        if email_field not in errors and not _EMAIL_RE.match(email):
            errors[email_field] = f"{popisek} — e-mail nemá platný tvar."

        oddeleni = _field(form, oddeleni_field)
        values[oddeleni_field] = oddeleni
        # Prázdný číselník → pole se nevyžaduje (viz `_active_department_names`).
        if active_departments:
            if not oddeleni:
                errors[oddeleni_field] = f"{popisek} — vyberte oddělení."
            elif oddeleni not in active_departments:
                errors[oddeleni_field] = f"{popisek} — neplatné oddělení."

    if spravce_je_vlastnik:
        values["spravce_jmeno"] = values["vlastnik_jmeno"]
        values["spravce_email"] = values["vlastnik_email"]
        values["spravce_oddeleni"] = values["vlastnik_oddeleni"]


def _validate_components(
    form: FormData, values: dict[str, Any], errors: dict[str, str]
) -> None:
    """AI komponenty: min. 1, max. `MAX_COMPONENTS`, každý řádek kompletní."""
    providers = _field_list(form, "komponenta_provider")
    model_names = _field_list(form, "komponenta_model_name")
    purposes = _field_list(form, "komponenta_purpose")
    hostings = _field_list(form, "komponenta_hosting_type")

    pocet = max(len(providers), len(model_names), len(purposes), len(hostings))
    rows: list[dict[str, str]] = []

    for index in range(pocet):
        row = {
            "provider": providers[index] if index < len(providers) else "",
            "model_name": model_names[index] if index < len(model_names) else "",
            "purpose": purposes[index] if index < len(purposes) else "",
            "hosting_type": hostings[index] if index < len(hostings) else "",
        }
        # Zcela prázdný řádek (uživatel ho jen nechal viset) se tiše zahodí.
        if not any(row.values()):
            continue
        rows.append(row)

        problemy: list[str] = []
        if row["provider"] not in Provider.__members__:
            problemy.append("vyberte poskytovatele")
        if not row["model_name"]:
            problemy.append("vyplňte název modelu")
        elif len(row["model_name"]) > MAX_SHORT_TEXT:
            problemy.append(f"název modelu smí mít nejvýš {MAX_SHORT_TEXT} znaků")
        if not row["purpose"]:
            problemy.append("vyplňte účel")
        elif len(row["purpose"]) > MAX_LONG_TEXT:
            problemy.append(f"účel smí mít nejvýš {MAX_LONG_TEXT} znaků")
        if row["hosting_type"] not in HostingType.__members__:
            problemy.append("vyberte typ hostingu")

        if problemy:
            errors[f"komponenta_{len(rows) - 1}"] = (
                "Neúplná AI komponenta: " + ", ".join(problemy) + "."
            )

    if not rows:
        rows = [_empty_component()]
        errors["komponenty"] = "Uveďte alespoň jednu AI komponentu."
    elif len(rows) > MAX_COMPONENTS:
        errors["komponenty"] = f"Nejvýš {MAX_COMPONENTS} AI komponent na jeden záznam."

    values["komponenty"] = rows


def _validate_answers(
    form: FormData, values: dict[str, Any], errors: dict[str, str]
) -> None:
    odpovedi: dict[str, str] = {}

    ucel = _field(form, FREE_TEXT_QUESTION.field)
    odpovedi[FREE_TEXT_QUESTION.field] = ucel
    _check_text(
        errors, FREE_TEXT_QUESTION.field, ucel, "Popis účelu (otázka 1)", MAX_LONG_TEXT
    )

    for question in CHOICE_QUESTIONS:
        raw = _field(form, question.field)
        odpovedi[question.field] = raw
        assert question.enum is not None  # CHOICE_QUESTIONS mají vždy enum
        if not raw:
            errors[question.field] = f"Otázka {question.number}: vyberte jednu možnost."
        elif raw not in question.enum.__members__:
            errors[question.field] = f"Otázka {question.number}: neplatná hodnota."

    values["odpovedi"] = odpovedi


def _validate_form(
    form: FormData, active_departments: list[str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Ruční validace formuláře. Vrací `(hodnoty pro re-render, chyby po polích)`."""
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}

    nazev = _field(form, "nazev")
    values["nazev"] = nazev
    _check_text(errors, "nazev", nazev, "Název aplikace", MAX_SHORT_TEXT)

    stav = _field(form, "stav")
    values["stav"] = stav if stav in Stav.__members__ else Stav.VYVOJ.name

    _validate_contacts(form, values, errors, active_departments)
    _validate_components(form, values, errors)
    _validate_answers(form, values, errors)

    return values, errors


# --- Krok 1: formulář -------------------------------------------------------


def _render_form(
    request: Request,
    user: SessionUser,
    values: dict[str, Any],
    errors: dict[str, str],
    db: Session,
    status_code: int = status.HTTP_200_OK,
    *,
    form_action: str = "/aplikace/nova",
    cancel_url: str = "/",
    is_edit: bool = False,
    version: int | None = None,
) -> HTMLResponse:
    """Vykreslí `app_form.html`. Sdílí ho i editace (Fáze 12, `app.routes.edit`) —
    `form_action`/`cancel_url`/`is_edit`/`version` odlišují cíl POSTu a hidden
    pole optimistic lockingu, zbytek šablony i validace je identický."""
    return templates.TemplateResponse(
        request,
        "app_form.html",
        {
            "user": user,
            "values": values,
            "errors": errors,
            "questions": QUESTIONS,
            "providers": list(Provider),
            "hosting_types": list(HostingType),
            "all_stav": list(Stav),
            "departments": _active_department_names(db),
            "max_components": MAX_COMPONENTS,
            "form_action": form_action,
            "cancel_url": cancel_url,
            "is_edit": is_edit,
            "version": version,
        },
        status_code=status_code,
    )


@router.get("/aplikace/nova", response_class=HTMLResponse, name="new_application_form")
def new_application_form(
    request: Request,
    user: Annotated[SessionUser, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    """Formulář nového záznamu; rozdělaný wizard se předvyplní ze session."""
    state = _get_state(request)
    values = state["values"] if state else _empty_values()
    return _render_form(request, user, values, {}, db)


@router.post("/aplikace/nova", dependencies=[Depends(verify_csrf)])
async def submit_new_application(
    request: Request,
    user: Annotated[SessionUser, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Validace vstupu → uložení do session → kontrola duplicit (spec kap. 6)."""
    form = await request.form()
    values, errors = _validate_form(form, _active_department_names(db))
    if errors:
        return _render_form(request, user, values, errors, db, status.HTTP_400_BAD_REQUEST)

    state: dict[str, Any] = {"values": values, "duplicate_reason": None}
    request.session[WIZARD_SESSION_KEY] = state

    nazev = values["nazev"]
    popis = values["odpovedi"][FREE_TEXT_QUESTION.field]
    candidates = top_candidates(nazev, popis, _active_records(db))

    if candidates:
        outcome = _find_duplicates(
            db, nazev, popis, candidates, known_contacts=_known_contacts(db)
        )
        if outcome.matches:
            state["duplicates"] = {
                "matches": [
                    {"record_id": m.record_id, "nazev": m.nazev, "duvod": m.duvod}
                    for m in outcome.matches
                ],
                "fallback_used": outcome.fallback_used,
            }
            request.session[WIZARD_SESSION_KEY] = state
            return _redirect("/aplikace/nova/duplicity")

    return _redirect("/aplikace/nova/klasifikace")


def _active_records(db: Session) -> list[tuple[str, str, str]]:
    """`(id, název, popis)` aktivních záznamů pro lokální předvýběr duplicit."""
    rows = db.execute(
        select(Application.id, Application.nazev, Application.popis).where(
            Application.deleted_at.is_(None)
        )
    ).all()
    return [(row[0], row[1], row[2]) for row in rows]


def _known_contacts(db: Session) -> list[tuple[str, str]]:
    """Kontakty z registru — anonymizér podle nich pozná jména ve volném textu."""
    rows = db.execute(
        select(
            Application.vlastnik_jmeno,
            Application.vlastnik_email,
            Application.zastupce_jmeno,
            Application.zastupce_email,
            Application.spravce_jmeno,
            Application.spravce_email,
        ).where(Application.deleted_at.is_(None))
    ).all()

    contacts: list[tuple[str, str]] = []
    for row in rows:
        contacts.extend([(row[0], row[1]), (row[2], row[3]), (row[4], row[5])])
    return contacts


# --- Krok 2: duplicity ------------------------------------------------------


def _render_duplicates(
    request: Request,
    user: SessionUser,
    state: dict[str, Any],
    error: str = "",
    reason: str = "",
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    duplicates = state.get("duplicates") or {}
    return templates.TemplateResponse(
        request,
        "duplicates_step.html",
        {
            "user": user,
            "nazev": state["values"]["nazev"],
            "matches": duplicates.get("matches", []),
            "fallback_used": bool(duplicates.get("fallback_used")),
            "error": error,
            "reason": reason,
        },
        status_code=status_code,
    )


@router.get(
    "/aplikace/nova/duplicity", response_class=HTMLResponse, name="new_application_duplicates"
)
def duplicates_step(
    request: Request,
    user: Annotated[SessionUser, Depends(require_user)],
) -> Response:
    """Podobné existující nástroje (spec kap. 6). Bez shod se krok přeskakuje."""
    state = _get_state(request)
    if state is None:
        return _redirect("/aplikace/nova")
    if not (state.get("duplicates") or {}).get("matches"):
        return _redirect("/aplikace/nova/klasifikace")
    return _render_duplicates(request, user, state)


@router.post("/aplikace/nova/duplicity/zrusit", dependencies=[Depends(verify_csrf)])
async def cancel_for_duplicate(
    request: Request,
    user: Annotated[SessionUser, Depends(require_user)],
) -> Response:
    """„Zrušit — už existuje": zahodí mezistav, nic se do registru nezapisuje."""
    request.session.pop(WIZARD_SESSION_KEY, None)
    set_flash(
        request,
        "Založení bylo zrušeno — vybraná aplikace už je v registru evidovaná.",
    )
    return _redirect("/")


@router.post("/aplikace/nova/duplicity/pokracovat", dependencies=[Depends(verify_csrf)])
async def continue_despite_duplicate(
    request: Request,
    user: Annotated[SessionUser, Depends(require_user)],
) -> Response:
    """„Pokračovat — je to jiná aplikace": důvod je povinný (→ `DUPLICATE_OVERRIDE`)."""
    state = _get_state(request)
    if state is None:
        return _redirect("/aplikace/nova")

    form = await request.form()
    reason = _field(form, "duvod")
    if not reason:
        return _render_duplicates(
            request,
            user,
            state,
            error="Uveďte prosím důvod, proč nejde o duplicitu.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if len(reason) > MAX_LONG_TEXT:
        return _render_duplicates(
            request,
            user,
            state,
            error=f"Důvod smí mít nejvýš {MAX_LONG_TEXT} znaků.",
            reason=reason,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    state["duplicate_reason"] = reason
    request.session[WIZARD_SESSION_KEY] = state
    return _redirect("/aplikace/nova/klasifikace")


# --- Krok 3: klasifikace ----------------------------------------------------


def _stored_llm_tier(state: dict[str, Any]) -> Tier | None:
    """Návrh LLM ze session. Slouží jen k `max()`, nikdy jako validační podklad."""
    name = (state.get("klasifikace") or {}).get("llm_tier")
    return Tier[name] if isinstance(name, str) and name in Tier.__members__ else None


def _render_classification(
    request: Request,
    user: SessionUser,
    state: dict[str, Any],
    minimum: MinimumResult,
    flags: list[dict[str, Any]],
    navrh: Tier,
    selected: Tier,
    poznamka: str = "",
    error: str = "",
    status_code: int = status.HTTP_200_OK,
    *,
    action_url: str = "/aplikace/nova/ulozit",
    back_url: str = "/aplikace/nova",
) -> HTMLResponse:
    """Vykreslí `classify_step.html`. Sdílí ho i re-klasifikace při editaci
    (Fáze 12, `app.routes.edit`) — `action_url`/`back_url` odlišují jen cíl
    formuláře, `state` má v obou tocích stejný tvar (`values`/`klasifikace`/
    `duplicate_reason`).

    `flags` jsou už serializované dicty (`regulatory.flags_to_dicts`), ne
    `Flag` objekty — obsahují i volitelný `ai_kontext` (spec kap. 5.4), který
    `_flags.html` vykreslí vedle deterministického detailu."""
    klasifikace = state.get("klasifikace") or {}
    return templates.TemplateResponse(
        request,
        "classify_step.html",
        {
            "user": user,
            "nazev": state["values"]["nazev"],
            "llm_tier": _stored_llm_tier(state),
            "zduvodneni": klasifikace.get("zduvodneni", ""),
            "fallback_used": bool(klasifikace.get("fallback_used")),
            "minimum": minimum,
            "flags": flags,
            "navrh": navrh,
            "selected": selected,
            # Ne-admin smí jen potvrdit/zvýšit návrh, admin celý rozsah nad
            # minimem (spec kap. 5.5). Backend to při uložení validuje znovu
            # přes `effective_tier(actor_is_admin=...)`, tohle je jen UI filtr.
            "tier_options": (
                [t for t in Tier if t >= minimum.tier]
                if user.is_admin
                else [t for t in Tier if t >= navrh]
            ),
            "poznamka": poznamka,
            "error": error,
            "duplicate_reason": state.get("duplicate_reason") or "",
            "form_action": action_url,
            "back_url": back_url,
        },
        status_code=status_code,
    )


@router.get(
    "/aplikace/nova/klasifikace",
    response_class=HTMLResponse,
    name="new_application_classification",
)
def classification_step(
    request: Request,
    user: Annotated[SessionUser, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Návrh AI · povinné minimum · výsledný tier · signály (spec kap. 5.2 a 5.5).

    Selhání LLM se nikdy neprojeví ztrátou formuláře — Gateway vrací fallback
    a mezistav v session zůstává nedotčený.
    """
    state = _get_state(request)
    if state is None:
        return _redirect("/aplikace/nova")

    values = state["values"]
    answers = _answers_from(values)
    components = _components_from(values)

    # Signály se počítají PŘED voláním LLM — jdou do promptu jako allowlist
    # kontext pro `signal_context` (spec kap. 5.4). Existence signálu je tím
    # nedotčená, LLM k ní jen smí doplnit kontextovou větu.
    minimum = compute_minimum(answers, components)
    flags = compute_flags(answers, components)
    outcome = _classify(
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
    request.session[WIZARD_SESSION_KEY] = state

    return _render_classification(
        request, user, state, minimum, flags_data, navrh, selected=navrh
    )


@router.post("/aplikace/nova/ulozit", dependencies=[Depends(verify_csrf)])
async def save_new_application(
    request: Request,
    user: Annotated[SessionUser, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Potvrzení klasifikace a zápis záznamu do registru.

    Policy minimum se počítá **znovu z odpovědí** — hodnota ze session slouží
    jen k vykreslení, nikdy k rozhodnutí. Tier pod minimum vyhodí
    `PolicyViolation`, kterou `app.main` převede na HTTP 400.
    """
    state = _get_state(request)
    if state is None:
        return _redirect("/aplikace/nova")

    values = state["values"]
    answers = _answers_from(values)
    components = _components_from(values)

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

    if manual is None:
        return _render_classification(
            request, user, state, minimum, flags_data, navrh, navrh, poznamka,
            error="Vyberte výsledný governance tier.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Pořadí je záměrné: policy floor se ověří dřív než formulářová pravidla,
    # aby ručně sestavený POST bez poznámky skončil na 400 z `PolicyViolation`.
    # `actor_is_admin` rozlišuje, kdo smí snížit proti návrhu AI (spec kap.
    # 5.5) — ne-admin dostane `ManualBelowSuggestion` místo tiché korekce.
    try:
        vysledny = effective_tier(
            llm_tier, minimum, manual_tier=manual, actor_is_admin=user.is_admin
        )
    except ManualBelowSuggestion as exc:
        return _render_classification(
            request, user, state, minimum, flags_data, navrh, manual, poznamka,
            error=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if vysledny != navrh and not poznamka:
        return _render_classification(
            request, user, state, minimum, flags_data, navrh, manual, poznamka,
            error="Při změně proti navrhovanému tieru je poznámka povinná.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    application = _build_application(state, user, minimum, flags_data, vysledny, poznamka)
    db.add(application)
    db.flush()

    _write_history(db, application.id, user.username, state, vysledny, poznamka)
    db.commit()

    request.session.pop(WIZARD_SESSION_KEY, None)
    set_flash(request, f"Aplikace „{application.nazev}“ byla založena.")
    return _redirect(f"/aplikace/{application.id}")


def _build_application(
    state: dict[str, Any],
    user: SessionUser,
    minimum: MinimumResult,
    flags_data: list[dict[str, Any]],
    vysledny: Tier,
    poznamka: str,
) -> Application:
    values = state["values"]
    llm_tier = _stored_llm_tier(state)

    application = Application(
        nazev=values["nazev"],
        popis=values["odpovedi"][FREE_TEXT_QUESTION.field],
        vlastnik_jmeno=values["vlastnik_jmeno"],
        vlastnik_email=values["vlastnik_email"],
        vlastnik_oddeleni=values["vlastnik_oddeleni"] or None,
        zastupce_jmeno=values["zastupce_jmeno"],
        zastupce_email=values["zastupce_email"],
        zastupce_oddeleni=values["zastupce_oddeleni"] or None,
        spravce_jmeno=values["spravce_jmeno"],
        spravce_email=values["spravce_email"],
        spravce_oddeleni=values["spravce_oddeleni"] or None,
        klasifikace_llm=llm_tier,
        klasifikace_minimum=minimum.tier,
        klasifikace=vysledny,
        klasifikace_zduvodneni=_compose_zduvodneni(state, minimum, llm_tier),
        klasifikace_priznaky=flags_data,
        klasifikace_potvrdil=user.username,
        klasifikace_poznamka=poznamka or None,
        dotaznik_odpovedi=dict(values["odpovedi"]),
        stav=Stav[values["stav"]],
        created_by=user.username,
        updated_by=user.username,
    )

    for row in values["komponenty"]:
        application.components.append(
            AiComponent(
                provider=Provider[row["provider"]],
                model_name=row["model_name"],
                purpose=row["purpose"],
                hosting_type=HostingType[row["hosting_type"]],
            )
        )
    return application


def _compose_zduvodneni(
    state: dict[str, Any], minimum: MinimumResult, llm_tier: Tier | None
) -> str:
    """Zdůvodnění LLM + věta o eskalaci pravidlem, pokud k ní došlo (spec kap. 5.3)."""
    zduvodneni = (state.get("klasifikace") or {}).get("zduvodneni", "")

    if llm_tier is not None and llm_tier >= minimum.tier:
        return zduvodneni

    reason_codes = ", ".join(rule.reason_code for rule in minimum.triggered_rules)
    if not reason_codes:
        return zduvodneni

    eskalace = (
        f"Povinné minimum {minimum.tier.label().upper()} vyplývá z deterministických "
        f"pravidel ({reason_codes}) a má přednost před návrhem AI."
    )
    return f"{zduvodneni} {eskalace}".strip()


def _write_history(
    db: Session,
    record_id: str,
    actor: str,
    state: dict[str, Any],
    vysledny: Tier,
    poznamka: str,
) -> None:
    """CREATE + CLASSIFY vždy; DUPLICATE_OVERRIDE jen při pokračování přes duplicitu."""
    history.log(db, record_id, actor, AuditAction.CREATE)
    history.log(
        db,
        record_id,
        actor,
        AuditAction.CLASSIFY,
        pole="klasifikace",
        nova=vysledny.name,
        reason=poznamka or None,
    )

    duplicate_reason = state.get("duplicate_reason")
    if duplicate_reason:
        history.log(
            db,
            record_id,
            actor,
            AuditAction.DUPLICATE_OVERRIDE,
            reason=duplicate_reason,
        )
