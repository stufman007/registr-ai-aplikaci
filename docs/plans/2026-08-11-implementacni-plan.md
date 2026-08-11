# Implementační plán: Registr interních AI aplikací

> Zdroj pravdy: [docs/specs/2026-08-11-ai-registr-design.md](../specs/2026-08-11-ai-registr-design.md)
> Zadání úkolu: [entr_specs.md](../../entr_specs.md)

**Poznámka:** pořadí fází je navrženo podle principu „deterministická logika dřív než infrastruktura a UI".

---

## 1. Restatement požadavků (co se vlastně hodnotí)

Zadání hodnotí **způsob práce, ne vzhled ani počet funkcí**. Z toho plyne, co musí plán zaručit:

- **Běží na první pokus podle README** na cizím stroji — `docker compose up`, žádné ruční kroky.
- **Autor rozumí tomu, co odevzdal** — u pohovoru se vysvětluje libovolné místo v kódu („proč zrovna takto"). → jednoduchý monolit, žádná magie, deterministická jádra oddělená a otestovaná.
- **Čistá práce se secrets a daty** — nic v repu, `.env.example`, `.gitignore`, syntetická data, anonymizace jméno/e-mail/telefon tam a zpět, definovaná retence.
- **Identita**: OIDC/OAuth2, žádná vlastní tabulka hesel, výměna za Entra ID = jen konfigurace, MFA neřeší aplikace, ≥2 role vynucené na backendu.
- **LLM za vlastní abstrakcí** (vyměnitelnou za firemní AI Gateway), logy volání bez obsahu promptu.
- **Dokumentace**: README (k čemu to je, jak spustit, model, klasifikace této aplikace a proč, dluh), `prompts/`, **Master Prompt** + název a verze modelu.
- **Vědomý dluh je plus, ne minus** — musí být explicitně vyjmenovaný.

Funkčně (varianta A): registr aplikací s vlastníky, stavem, AI komponentami; aplikace sama navrhne klasifikaci MALÁ/STŘEDNÍ/VELKÁ a zdůvodní ji; nad LLM návrhem stojí deterministický policy floor a legislativní signály; kontrola duplicit; immutable historie; soft delete.

---

## 2. Architektura — co kde vzniká

Cílová struktura dle kap. 10 specu:

- `app/` — FastAPI monolit (`main.py`, `config.py`, `auth.py`, `security.py`, `models.py`, `db.py`, `schemas.py`)
- `app/routes/` — `registry.py`, `classification.py`, `admin.py`
- `app/services/` — `policy.py`, `regulatory.py`, `similarity.py`, `history.py`
- `app/llm/` — `gateway.py`, `base.py`, `mock.py`, `anthropic.py`, `anonymizer.py`, `audit.py`
- `app/templates/`, `app/static/`
- `tests/` — `test_policy.py`, `test_regulatory.py`, `test_anonymizer.py`, `test_fallback.py`
- `keycloak/realm-export.json`, `prompts/`, `seed.py`
- `Dockerfile`, `docker-compose.yaml`, `.env.example`, `.gitignore`, `requirements.txt`, `README.md`

**Řídící princip pořadí:** fáze 1–6 (policy, signály, anonymizér, podobnost, data, gateway) jsou čistá logika testovatelná jedním `pytest` bez Dockeru, Keycloaku a klíčů. Teprve pak přichází infrastruktura (Keycloak, OIDC) a UI. Když se v poslední třetině něco zadrhne (typicky OIDC v Dockeru), jádro governance už je hotové a prokazatelně funguje.

---

## 3. Fáze

### Fáze 0 — Kostra repa, pinované závislosti, konfigurace
**Komplexita: LOW** · **Závislosti: žádné**

**Co vzniká a proč (pro PO):** prázdný, ale správně nastavený projekt — seznam přesných verzí knihoven, vzorový konfigurační soubor bez hesel a pravidlo, že se skutečná hesla nikdy nedostanou do repozitáře. Bez tohoto základu nelze splnit podmínku „žádné secrets v repu" a „pinované závislosti".

**Technické kroky:**
1. `requirements.txt` s `==` pro: `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart`, `sqlalchemy`, `pydantic`, `pydantic-settings`, `authlib`, `httpx`, `itsdangerous`, `anthropic`, `pytest`. Verze vygenerovat `pip freeze` z čisté venv, ne psát ručně.
2. `app/config.py` — `pydantic-settings`, vše z env: `APP_SECRET_KEY`, `DATABASE_URL`, `LLM_PROVIDER` (default `mock`), `ANTHROPIC_API_KEY` (optional), `ANTHROPIC_MODEL`, `LLM_TIMEOUT_SECONDS=10`, `OIDC_ISSUER_URL`, `OIDC_INTERNAL_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_ROLES_CLAIM`, `SESSION_MAX_AGE`, `COOKIE_SECURE`, `LLM_AUDIT_RETENTION_DAYS=90`.
3. `.env.example` — **úplný**, všechny klíče, prázdné/placeholder hodnoty, komentáře česky.
4. `.gitignore` — `.env`, `*.db`, `__pycache__/`, `.venv/`, `.pytest_cache/`.
5. `app/schemas.py` — enumy `Tier(MALA/STREDNI/VELKA)` s uspořádáním (porovnatelné, aby `max()` fungoval), enumy odpovědí dotazníku (ot. 2–9), enum `Stav`, enum `HostingType`, `Provider`. **Tohle je základ všech dalších fází — enum, ne string.**
6. `pytest.ini` / `pyproject.toml` s konfigurací pytestu.

**Ověření:** `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt` projde; `python -c "from app.config import settings; print(settings.llm_provider)"` vypíše `mock`; `git status` neukazuje `.env`.

**Commit:** `chore: kostra projektu, pinované závislosti a konfigurace z env`

---

### Fáze 1 — Policy engine (deterministická eskalační minima)
**Komplexita: MED** · **Závislosti: F0 (enumy)**

**Co vzniká a proč (pro PO):** srdce governance — tabulka tvrdých pravidel, která říká, jaká je **nejnižší povolená** klasifikace pro danou aplikaci. Například „rozhoduje automatizovaně o lidech" = vždy minimálně VELKÁ. Tohle pravidlo neobejde ani AI, ani admin. Vzniká první, protože se dá plně otestovat bez databáze, přihlášení i internetu.

**Technické kroky:**
1. `app/services/policy.py`:
   - `RULES_VERSION = "2026-08-v1"` (konstanta, jde do LLM auditu).
   - Datová tabulka 8 pravidel dle kap. 5.3 — každé jako `(predikát, minimální tier, reason_code, český popis)`. **Deklarativní seznam, ne řetěz `if`** — nové pravidlo = nový řádek, snadno vysvětlitelné u pohovoru.
   - `compute_minimum(answers, components) -> MinimumResult{tier, triggered_rules[]}`.
   - `effective_tier(llm_tier, minimum, manual_tier=None) -> Tier` = `max(...)`; pokus o `manual_tier < minimum` vyhodí doménovou výjimku `PolicyViolation` (ne tichá korekce — volající musí vrátit 400/403).
   - Pomocná funkce „má externího AI providera" nad seznamem komponent (`hosting_type == externí API`).
2. `tests/test_policy.py` dle kap. 16: každé pravidlo zvlášť · kombinace vrátí nejvyšší minimum · LLM `MALA` + floor `VELKA` → `VELKA` · ruční zvýšení OK · snížení pod minimum vyhodí výjimku.

**Ověření:** `pytest tests/test_policy.py -v` — všechny testy zelené, žádný neignorovaný.

**Commit:** `feat(policy): deterministická eskalační minima s reason codes a RULES_VERSION`

---

### Fáze 2 — Legislativní signály (GDPR / AI-ACT / DORA)
**Komplexita: LOW** · **Závislosti: F0, F1 (sdílené enumy a predikát externího providera)**

**Co vzniká a proč (pro PO):** logika, která z odpovědí odvodí legislativní upozornění — barevné štítky GDPR / AI-ACT / DORA. Vzniká v kódu, ne v AI, takže je nelze „upovídat" ani vymyslet. Zároveň to není právní verdikt, jen podnět k review.

**Technické kroky:**
1. `app/services/regulatory.py`:
   - `ALLOWED_FLAGS = frozenset({"GDPR", "AI-ACT", "DORA"})` — hard whitelist.
   - `compute_flags(answers, components) -> list[Flag]`, kde `Flag = {zkratka, titulek, detail, reason_code, source: "deterministic_rule"}`.
   - Pravidla dle kap. 5.4; u ot. 5 = „rozhoduje automatizovaně" důraznější `detail`.
   - Výstup se serializuje do `applications.klasifikace_priznaky` (JSON) — přes tuto funkci, nikdy odjinud.
2. `tests/test_regulatory.py`: osobní údaje → GDPR · rozhodování o lidech → AI-ACT · kritický + externí provider → DORA · žádná kombinace vstupů nevyprodukuje zkratku mimo whitelist.

**Ověření:** `pytest tests/test_regulatory.py -v` zelené.

**Commit:** `feat(regulatory): deterministické legislativní signály GDPR/AI-ACT/DORA`

---

### Fáze 3 — Pseudonymizér (anonymizace tam a zpět)
**Komplexita: MED** · **Závislosti: F0**

**Co vzniká a proč (pro PO):** ochrana osobních údajů před odesláním do AI. Jméno, e-mail a telefon v textu se nahradí zástupným symbolem `[OSOBA_1]`, AI vidí jen symbol, a ve výsledku se skutečné jméno vrátí zpět. Převodní tabulka žije jen v paměti jednoho požadavku — nikam se neukládá. Přímý požadavek zadání.

**Technické kroky:**
1. `app/llm/anonymizer.py`:
   - Třída `Anonymizer` (instance = jeden požadavek): `pseudonymize(text) -> str`, `restore(text) -> str`, interní `dict` mapa, čítače per typ.
   - E-mail a telefon regexem (české i mezinárodní formáty s mezerami); jména porovnáním proti předanému seznamu známých kontaktů z registru (kap. 7.2) — **obecné NER neděláme, limit patří do README**.
   - Stejná hodnota → stejný placeholder v rámci instance; delší jména nahrazovat před kratšími (aby „Jan Novák" nepřebilo dílčí shodu).
   - Mapa je instanční atribut bez jakéhokoli zápisu na disk/log; `__repr__` mapu nevypisuje.
2. `tests/test_anonymizer.py`: jméno/e-mail/telefon → placeholder a zpět (round-trip identita) · dvě různé osoby dostanou různé placeholdery · stejná osoba dvakrát = jeden placeholder · mapa není persistentní (nová instance nezná staré mapování) · text bez osobních údajů se nemění.

**Ověření:** `pytest tests/test_anonymizer.py -v` zelené.

**Commit:** `feat(llm): pseudonymizér jmen, e-mailů a telefonů s reverzní mapou v paměti`

---

### Fáze 4 — Lokální předvýběr duplicit
**Komplexita: LOW** · **Závislosti: F0**

**Co vzniká a proč (pro PO):** rychlé lokální porovnání nového názvu a popisu s existujícími záznamy. Vybere maximálně 10 nejpodobnějších kandidátů — jen ty pak posuzuje AI. Do AI nikdy neteče celý registr (levnější, méně dat ven, škáluje).

**Technické kroky:**
1. `app/services/similarity.py`: `top_candidates(nazev, popis, records, limit=10, threshold=…) -> list[Candidate]` nad stdlib `difflib.SequenceMatcher`; normalizace (lowercase, odstranění diakritiky, whitespace). Bez nové závislosti.
2. Konstanty `MAX_CANDIDATES = 10`, `FALLBACK_THRESHOLD` (práh pro zobrazení při výpadku LLM, kap. 6).
3. Smoke test `tests/test_similarity.py`: identický název je první; prázdný registr → prázdný seznam; nikdy nevrátí víc než 10.

**Ověření:** `pytest tests/test_similarity.py -v` zelené.

**Commit:** `feat(similarity): lokální předvýběr kandidátů duplicit přes difflib`

---

### Fáze 5 — Datový model, databáze a immutable historie
**Komplexita: MED** · **Závislosti: F0–F2 (enumy, tvar signálů)**

**Co vzniká a proč (pro PO):** databáze registru — tabulky aplikací, jejich AI komponent, nesmazatelné historie změn a technického logu volání AI. Klíčová vlastnost: záznamy se nikdy fyzicky nemažou, vyřazení je jen příznak a historie zůstává navždy.

**Technické kroky:**
1. `app/db.py` — SQLAlchemy engine (SQLite, `check_same_thread=False`), `SessionLocal`, `Base`, `create_all()`.
2. `app/models.py` — `Application`, `AiComponent`, `RecordHistory`, `LlmAudit` přesně dle kap. 4.1–4.4 (včetně `version`, `review_required`, `deleted_at/deleted_by/delete_reason`).
3. `app/services/history.py` — `log(session, record_id, actor, action, pole=None, stara=None, nova=None, reason=None)`; `diff_fields(old_obj, new_obj)` generující jeden řádek historie na změněné pole; **append-only, žádná mazací funkce v modulu**; guard proti serializaci polí z blacklistu (session, token, prompt).
4. `app/services/repository.py` (nebo funkce v `routes`) — dotazy vždy s filtrem `deleted_at IS NULL`, admin varianta explicitně opt-in.
5. `tests/test_history.py` (levný smoke): UPDATE dvou polí → dva řádky historie se starou a novou hodnotou; RETIRE bez `reason` → výjimka; historie přežije soft delete.

**Ověření:** `pytest tests/test_history.py -v`; `python -c "from app.db import init_db; init_db()"` vytvoří soubor DB a `sqlite3 registr.db ".tables"` ukáže 4 tabulky.

**Commit:** `feat(db): datový model registru, AI komponent a immutable historie`

---

### Fáze 6 — LLM Gateway, mock adapter, audit a failure contract
**Komplexita: HIGH** · **Závislosti: F1–F5**

**Co vzniká a proč (pro PO):** jediná povolená cesta k umělé inteligenci. Hlídá, která pole vůbec smí odejít, zkrátí a pseudonymizuje texty, zavolá AI, zkontroluje tvar odpovědi a vrátí jména zpět. Součástí je „mock" režim — aplikace umí kompletní demo úplně offline a bez API klíče. A když AI selže, aplikace se nezastaví: použije se náhradní deterministický výsledek a formulář uživatele se neztratí.

**Technické kroky:**
1. `app/llm/base.py` — `LlmAdapter` protokol: `complete(prompt: str, purpose: str) -> AdapterResult{text, tokens_in, tokens_out, model, provider}`. **Tohle je ta abstrakční vrstva ze zadání** — výměna za firemní AI Gateway = nový adapter.
2. `app/llm/mock.py` — deterministický adapter: heuristika nad odpověďmi vrátí věrohodný tier + české zdůvodnění; pro duplicity vrátí 0–2 kandidáty. Bez klíče, bez sítě, stejný vstup → stejný výstup.
3. `app/llm/audit.py` — zápis `LlmAudit` (metadata dle kap. 4.4), `purge_older_than(days)`. **Explicitní test/assert, že do zápisu neteče prompt ani response.**
4. `app/llm/gateway.py` — jediný veřejný vstup `classify(...)` a `find_duplicates(...)`:
   - allowlist polí per účel (kap. 7.1) — pro `classify` **kontakty vůbec nevstupují do buildu payloadu**,
   - ořez max délek + sanitizace, ohraničení uživatelských textů jako data (obrana proti prompt injection),
   - `Anonymizer` instance per požadavek,
   - prompt assembly ze souborů `prompts/classification.md`, `prompts/duplicates.md` s `PROMPT_VERSION` v hlavičce souboru,
   - volání adapteru s timeoutem 10 s, **max 1 retry** jen na timeout/transport/5xx/rate-limit,
   - Pydantic validace structured outputu (`ClassificationSuggestion`, `DuplicateMatches`); nevalidní po retry → `INVALID_RESPONSE`,
   - deanonymizace až po validaci,
   - fallback dle kap. 8: `klasifikace_llm = MALA (baseline)`, `fallback_used=True`, uživatelská hláška; duplicity → lokální kandidáti nad prahem,
   - zápis auditu vždy (úspěch i selhání).
5. `prompts/classification.md`, `prompts/duplicates.md` — česky, s `PROMPT_VERSION`, explicitní zákaz vracet legislativní příznaky.
6. `tests/test_fallback.py` — fake adapter vracející: nevalidní JSON → fallback s dodrženými minimy · timeout → 1 retry, pak fallback · 5xx pak úspěch → bez fallbacku · audit obsahuje `error_code` a neobsahuje text promptu.
7. `tests/test_gateway_allowlist.py` — payload pro `classify` neobsahuje `vlastnik_email` ani jiné kontaktní pole; payload pro `duplicates` má max 10 kandidátů; známé jméno/e-mail/telefon se v payloadu nevyskytuje (kritérium 8).

**Ověření:** `pytest tests/ -v` celé zelené; ruční smoke s `LLM_PROVIDER=mock` vypíše návrh tieru a zdůvodnění bez sítě.

**Commit:** `feat(llm): gateway s allowlistem, pseudonymizací, mock adapterem, auditem a fallbackem`

---

### Fáze 7 — Adapter pro Anthropic (Claude)
**Komplexita: LOW–MED** · **Závislosti: F6**

**Co vzniká a proč (pro PO):** druhý „konektor" — stejná aplikace umí místo simulace mluvit se skutečným Claude. Přepnutí je změna jediné položky v konfiguraci, nic v kódu. Tím se prokazuje vyměnitelnost poskytovatele, kterou zadání vyžaduje.

**Technické kroky:**
1. `app/llm/anthropic.py` — implementace `LlmAdapter` nad oficiálním SDK; model z env; timeout z env; mapování chyb na `error_code` (`TIMEOUT`, `PROVIDER_5XX`, `RATE_LIMIT`, `INVALID_RESPONSE`); tokeny z `usage` do auditu.
2. Factory v `gateway.py`: `LLM_PROVIDER` ∈ `{mock, anthropic}`; při `anthropic` bez klíče → jasná chyba při startu (fail fast), **ne tiché přepnutí** na mock.
3. Doplnit `.env.example` a README sekci „přepnutí providera".

**Ověření:** s `LLM_PROVIDER=mock` (bez klíče) aplikace/testy běží; s `LLM_PROVIDER=anthropic` a platným klíčem jedno ruční volání vrátí validní JSON a v `llm_audit` je řádek s `provider=anthropic`, tokeny > 0, **bez obsahu**; s `LLM_PROVIDER=anthropic` bez klíče start selže s čitelnou hláškou.

**Commit:** `feat(llm): adapter pro Anthropic Claude přepínatelný přes LLM_PROVIDER`

---

### Fáze 8 — FastAPI kostra, /health, session a CSRF
**Komplexita: MED** · **Závislosti: F0, F5, F6**

**Co vzniká a proč (pro PO):** samotný web server — spustitelná aplikace se stránkou „žije/nežije" (`/health`), bezpečnostním nastavením přihlašovací cookie a ochranou formulářů proti zneužití z cizí stránky (CSRF). Zatím bez obrazovek registru.

**Technické kroky:**
1. `app/main.py` — FastAPI app, `SessionMiddleware` (secret z env, `https_only` dle `COOKIE_SECURE`, `same_site="lax"`, `max_age`), mount `static`, Jinja2 templates, startup: `create_all()` → seed (jen prázdná DB) → `purge_llm_audit(90)`; logging (start, chyby, login/logout) bez tokenů.
2. `GET /health` — bez autentizace, vrací `{status, db: "ok"}`; **nevypisuje konfiguraci**.
3. `app/security.py` — `issue_csrf_token(request)`, `verify_csrf(request, form)` jako FastAPI dependency vracející 403; security headers middleware; Jinja2 globální makro `{{ csrf_field() }}`.
4. `app/templates/base.html`, `error.html`, `403.html`, `409.html`; `app/static/style.css`, `app/static/app.js` (tooltips ⓘ, přidání řádku komponenty).
5. Exception handlery: `PolicyViolation` → 400 s českou hláškou, `VersionConflict` → 409, `CsrfError` → 403.

**Ověření:** `uvicorn app.main:app --reload` → `curl http://localhost:8000/health` vrátí 200 a JSON bez secrets; POST bez CSRF tokenu vrátí 403.

**Commit:** `feat(app): FastAPI kostra, /health, bezpečná session a CSRF ochrana`

---

### Fáze 9 — Keycloak v compose, OIDC přihlášení a role
**Komplexita: HIGH** · **Závislosti: F8**

**Co vzniká a proč (pro PO):** firemní přihlášení. Aplikace nemá vlastní hesla — přesměruje uživatele na Keycloak (zastupuje firemní Entra ID), ten ověří totožnost a vrátí informaci o roli. Aplikace u každého požadavku sama kontroluje, jestli má uživatel právo — skrytí tlačítka se nepočítá.

**Technické kroky:**
1. `keycloak/realm-export.json` — realm, client (confidential, standard flow), redirect URI `http://localhost:8000/auth/callback`, web origins, 2 uživatelé s heslem a rolemi `user` / `admin`, role v tokenu (mapper dle `OIDC_ROLES_CLAIM`).
2. `docker-compose.yaml` (první verze, jen Keycloak) — `start-dev --import-realm`, volume s realm exportem, healthcheck, port 8080. **Kritické:** `KC_HOSTNAME=http://localhost:8080` + `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`, aby issuer v tokenu odpovídal tomu, co vidí prohlížeč, zatímco kontejner aplikace volá backchannel přes interní jméno.
3. `app/auth.py`:
   - Authlib `OAuth` client; **explicitně oddělené URL**: authorize přes `OIDC_ISSUER_URL` (veřejné, pro prohlížeč), token/jwks přes `OIDC_INTERNAL_URL` (interní síť compose). Metadata z interní URL, issuer validovat proti veřejné.
   - `GET /login` → redirect; `GET /auth/callback` → výměna kódu, extrakce `preferred_username`, `email`, rolí; do session jen `{username, email, roles}` — **žádné IdP tokeny**.
   - `GET /logout` → smazání session + RP-initiated logout na Keycloak.
   - Dependencies `require_user`, `require_admin`, `require_owner_or_admin(record)` — vrací 403.
   - Log login/logout (username, čas).
4. `app/templates/login.html`, uživatelské jméno a role v hlavičce `base.html`.

**Ověření:** `docker compose up keycloak` → healthy; v prohlížeči `/login` → Keycloak → přihlášení jako `user` → návrat, v hlavičce role `user`; jako `admin` → role `admin`; `curl -X POST` na admin endpoint s cookie uživatele `user` → **403**.

**Commit:** `feat(auth): OIDC přihlášení přes Keycloak a vynucení rolí na backendu`

---

### Fáze 10 — Registr: seznam a detail karty
**Komplexita: MED** · **Závislosti: F5, F8, F9**

**Co vzniká a proč (pro PO):** první viditelná část aplikace — tabulka všech evidovaných AI nástrojů s barevným štítkem klasifikace, legislativními badge (po najetí myší vysvětlení), stavem a upozorněním „vyžaduje review", plus detail jednoho záznamu včetně historie změn.

**Technické kroky:**
1. `app/routes/registry.py` — `GET /` (seznam, filtry `stav`, `tier`, default `deleted_at IS NULL`; admin má navíc filtr „vyřazené"), `GET /aplikace/{id}` (detail).
2. `app/templates/registry_list.html`, `registry_detail.html`, partials `_flags.html` (badge + tooltip), `_tier.html`, `_components.html`, `_history.html`.
3. Disclaimer „tier je interní governance tier, ne právní klasifikace" u signálů.
4. Akce v UI podmíněné rolí — kosmetika nad backendovou kontrolou z F9.

**Ověření:** ručně vložit 2 záznamy skriptem, přihlásit se, seznam ukáže tier, signály s tooltipem a stav; filtr funguje; `user` nevidí sekci vyřazených; detail zobrazí historii.

**Commit:** `feat(registry): seznam registru s filtry a detail karty aplikace`

---

### Fáze 11 — Založení záznamu: dotazník → duplicity → klasifikace → potvrzení
**Komplexita: HIGH** · **Závislosti: F1–F6, F9, F10**

**Co vzniká a proč (pro PO):** hlavní scénář aplikace. Uživatel vyplní kontakty, AI komponenty a devět otázek s nápovědou; aplikace nejdřív upozorní na podobné existující nástroje (a chce důvod, když se přesto pokračuje), pak vedle sebe ukáže **návrh AI**, **povinné minimum podle pravidel** a **výsledný tier** i s vysvětlením — a člověk to potvrdí nebo zvýší.

**Technické kroky:**
1. `app/routes/classification.py` — třístupňový wizard, mezistav v session (ne v DB), CSRF token v každém kroku:
   - `GET/POST /aplikace/nova` — formulář: kontakty, ≥1 AI komponenta (dynamické řádky), 9 otázek s ⓘ tooltipy; validace (povinná pole, e-mail formát, max délky, enumy).
   - `POST` → `similarity.top_candidates` → jsou-li kandidáti, krok „duplicity": `gateway.find_duplicates` (max 3 zdůvodněné), volby „Zrušit — už existuje" / „Pokračovat — jiná aplikace" + **povinný důvod** → `DUPLICATE_OVERRIDE` do historie. Bez kandidátů se krok přeskočí.
   - Krok „klasifikace": `gateway.classify` → `policy.compute_minimum` → `regulatory.compute_flags` → `effective_tier`; šablona zobrazí čtyři sloupce (návrh AI / minimum + aktivovaná pravidla / výsledek / signály) + zdůvodnění; pokud eskalováno pravidlem, zdůvodnění doplní který reason code.
   - Potvrzení → uložení `Application` + `AiComponent[]` + `dotaznik_odpovedi` snapshot + historie `CREATE` a `CLASSIFY`. Ruční změna proti návrhu vyžaduje `klasifikace_poznamka`. Uložení tieru < `klasifikace_minimum` → backend odmítne (400), i při ručně sestaveném POSTu.
2. Fallback UX: při selhání LLM hláška „AI zdůvodnění není momentálně dostupné. Governance tier byl určen povinnými pravidly." a **formulář se neztratí** (data v session).
3. Šablony `app_form.html`, `duplicates_step.html`, `classify_step.html`.

**Ověření:** ručně projít celé založení jako `user` s `LLM_PROVIDER=mock`; vynucené selhání mocku (env `LLM_FORCE_FAIL=1` jen pro mock) → fallback bez ztráty formuláře; `curl -X POST` s tierem `MALA` u aplikace s `AUTO_DECISION_PERSON` → 400/403; duplicitní název → krok duplicit, pokračování bez důvodu odmítnuto, s důvodem řádek `DUPLICATE_OVERRIDE`.

**Commit:** `feat(registry): založení záznamu — dotazník, kontrola duplicit a klasifikační krok`

---

### Fáze 12 — Editace, optimistic locking, review light
**Komplexita: MED** · **Závislosti: F11**

**Co vzniká a proč (pro PO):** registr musí zůstat pravdivý i po založení. Když někdo změní rizikovou odpověď, aplikace vynutí novou klasifikaci ještě před uložením. Když změní AI komponenty, rozsvítí se štítek „vyžaduje review". A když dva lidé editují stejný záznam zároveň, druhý dostane hlášku místo tichého přepsání práce prvního.

**Technické kroky:**
1. `GET/POST /aplikace/{id}/upravit` — přístup `require_owner_or_admin`; ve formuláři skryté `version`.
2. Optimistic locking: `UPDATE … WHERE id=? AND version=?`; 0 dotčených řádků → 409 se šablonou „záznam mezitím změnil někdo jiný". `version += 1` při každém úspěšném update.
3. Detekce změny rizikových vstupů (ot. 3–9) → klasifikační krok před uložením (znovupoužít z F11).
4. Změna AI komponent → `review_required = true`; badge v seznamu i na detailu; po re-klasifikaci `false`.
5. Historie `UPDATE` po polích + `CLASSIFY` při re-klasifikaci.
6. Duplicitní kontrola při editaci se **záměrně nedělá** (vědomý dluh → README).

**Ověření:** edit ve dvou záložkách, uložit v obou → druhé vrátí 409; změna odpovědi ot. 5 → vynucený klasifikační krok; přidání komponenty → badge „vyžaduje review", po re-klasifikaci zhasne; `user` na cizí záznam → 403 i přímým POSTem.

**Commit:** `feat(registry): editace s optimistic lockingem a vynucenou re-klasifikací (review light)`

---

### Fáze 13 — Admin: vyřazení, obnovení, úprava klasifikace
**Komplexita: MED** · **Závislosti: F12**

**Co vzniká a proč (pro PO):** správcovské akce. Záznam se nikdy nemaže — jen vyřadí, vždy s povinným důvodem, a historie zůstává. Admin může klasifikaci upravit, ale **nikdy pod povinné minimum** — žádný skrytý override.

**Technické kroky:**
1. `app/routes/admin.py` — `POST /aplikace/{id}/vyradit` (povinný `reason`, `deleted_at/deleted_by/delete_reason`, historie `RETIRE`), `POST /aplikace/{id}/obnovit` (historie `RESTORE`), obojí `require_admin` + CSRF.
2. Seznam vyřazených pro admina (`?zobrazit=vyrazene`).
3. Admin úprava tieru — validace přes `policy.effective_tier`; pod minimum → 400 s vysvětlením, které pravidlo brání; povinná `klasifikace_poznamka`; historie `CLASSIFY`.
4. Žádná cesta v kódu nedělá `session.delete()` nad `Application`, `AiComponent` ani `RecordHistory` (grep jako součást ověření).

**Ověření:** vyřazení bez důvodu → odmítnuto; s důvodem → zmizí z aktivního seznamu, historie `RETIRE`, obnovení funguje; `user` přímý POST na vyřazení → 403; admin snížení pod minimum → 400; `grep -rn "session.delete" app/` → nic (kromě purge `llm_audit`).

**Commit:** `feat(admin): vyřazení a obnovení záznamu s povinným důvodem a ochrana policy minima`

---

### Fáze 14 — Seed, Dockerfile, kompletní compose, spuštění na čistém stroji
**Komplexita: MED–HIGH** · **Závislosti: F8–F13**

**Co vzniká a proč (pro PO):** aby to na cizím počítači naběhlo jedním příkazem a bylo hned co ukazovat — osm vymyšlených aplikací s různými klasifikacemi, signály, komponentami a jednou vyřazenou. Nejostřeji hodnocený bod zadání („běží to na první pokus podle README").

**Technické kroky:**
1. `seed.py` — ~8 syntetických aplikací (`@example.com` kontakty), různé tiery/stavy/signály, více komponent, jedna vyřazená, jedna s `review_required`; jen nad prázdnou DB.
2. `Dockerfile` — `python:3.12-slim`, non-root user, `pip install --no-cache-dir`, `CMD uvicorn`. `.dockerignore` (`.git`, `.env`, `*.db`, `.venv`).
3. `docker-compose.yaml` — `keycloak` (healthcheck `/health/ready`) a `app` (`depends_on: condition: service_healthy`), **named volume** pro SQLite (ne bind mount — Windows + SQLite zamykání), named volume pro Keycloak, porty 8000/8080.
4. **Musí naběhnout bez `.env`**: `${VAR:-default}` pro vše kromě volitelného `ANTHROPIC_API_KEY`; default `LLM_PROVIDER=mock`, dev `APP_SECRET_KEY` a dev `OIDC_CLIENT_SECRET` shodný s realm exportem. `.env` je pak jen pro reálný klíč Claude.
5. Cross-platform: `.gitattributes` s `* text=auto eol=lf` pro `*.sh`, `Dockerfile`; žádné absolutní windows cesty.
6. Startup app: `create_all` → seed (prázdná DB) → purge `llm_audit` > 90 dní.

**Ověření (nejdůležitější v celém plánu):** čerstvý `git clone` do jiné složky, **bez `.env`**, `docker compose up` → obě služby healthy → `/health` 200 → přihlášení `user` i `admin` → seznam ukazuje 7 aktivních záznamů → kompletní založení nové aplikace v mock režimu. Zopakovat po `docker compose down -v`.

**Commit:** `chore(docker): kompletní compose s Keycloakem, seed a spuštění bez ručních kroků`

---

### Fáze 15 — README, Master Prompt, prompts/, retence a vědomý dluh
**Komplexita: MED** · **Závislosti: F14**

**Co vzniká a proč (pro PO):** dokumentace, kterou hodnotitel čte jako první a podle níž aplikaci spouští. Obsahuje i sebe-klasifikaci: jaký tier by měl **tento** registr sám o sobě a proč — explicitní požadavek zadání.

**Technické kroky:**
1. `README.md`:
   - K čemu to je · jak spustit (`docker compose up`, URL, demo účty) · architektura diagramem.
   - **Master Prompt** (kondenzát specu dle kap. 17, invarianty) + **název a verze modelu** (`Claude Fable 5 / claude-fable-5`) — zdroj: `Master_prompt.md`.
   - Použitý model pro běh aplikace a jak přepnout `LLM_PROVIDER=mock↔anthropic`.
   - **Klasifikace této aplikace samotné a proč** — projít vlastní dotazník, uvést tier + aktivovaná pravidla + signály.
   - **Výměna Keycloak → Microsoft Entra ID**: přesně které env hodnoty se mění, redirect URI v App registration; **žádná změna kódu**.
   - **Proč MFA neřeší aplikace** (odpovědnost IdP).
   - **Retence dat** — tabulka z kap. 13 specu, včetně poznámky, že se podle ní programuje.
   - **Anonymizace** — jak funguje tam a zpět, přiznaný limit (není obecné DLP/NER).
   - **Vědomý dluh** — celý seznam z kap. 2 specu + migrace + integration/E2E testy.
   - **Disclaimer**: tier ≠ právní klasifikace; signály jsou podnět k review.
2. `prompts/` — finalizovat s `PROMPT_VERSION` a komentářem, co se do promptu smí a nesmí dostat.
3. README příkazy kopírovat z ověřeného průchodu F14, ne psát z hlavy.

**Ověření:** spuštění jen podle README na čisté VM/druhém stroji. Checklist: repo bez secrets (`git log -p | grep -i "api[_-]key"`), `.env` netrackovaný, `.env.example` úplný, `prompts/` verzované, README má všechny sekce.

**Commit:** `docs: README s Master Promptem, klasifikací aplikace, retencí a vědomým dluhem`

---

### Fáze 16 — Akceptační průchod a příprava dema
**Komplexita: LOW** · **Závislosti: F0–F15**

**Co vzniká a proč (pro PO):** poslední kontrola — projde se všech 16 akceptačních kritérií jako checklist a připraví se scénář 3–5minutového videa.

**Technické kroky:**
1. Mapping tabulku (sekce 5) projít bod po bodu na běžící instanci.
2. `pytest tests/ -v` — kompletní sada zelená.
3. Sken secrets: `git log -p`, kontrola image (`docker run --rm <img> env`), `.gitignore` funkční.
4. Scénář videa: `docker compose up` → login `user` → registr + signály/tooltips → založení aplikace s automatizovaným rozhodováním (eskalace na VELKÁ + reason code) → duplicita a override → login `admin` → vyřazení s důvodem + historie → `/health` → krátce kód `policy.py` a `gateway.py`.
5. Drobné opravy → samostatné `fix:` commity.

**Ověření:** všech 16 kritérií odškrtnuto, testy zelené, žádný nález secretů.

**Commit:** `docs: akceptační checklist a scénář dema`

---

## 4. Testovací strategie

| Vrstva | Rozsah | Kdy |
|---|---|---|
| Unit — guardrails (povinné dle kap. 16 specu) | `test_policy.py`, `test_regulatory.py`, `test_anonymizer.py`, `test_fallback.py` | F1, F2, F3, F6 |
| Unit — levné doplňky | `test_similarity.py`, `test_history.py`, `test_gateway_allowlist.py` | F4, F5, F6 |
| Ruční ověření (nahrazuje integration testy) | RBAC 403 přes `curl`, CSRF 403, 409 konflikt, fallback bez ztráty formuláře, soft delete + historie | F9, F11, F12, F13 |
| Akceptační | 16 kritérií na běžící instanci z čistého clone | F14, F16 |

Integration/E2E testy jsou **vědomý dluh** (zadání testovací pokrytí nehodnotí) — patří do README.

---

## 5. Mapping: akceptační kritéria (kap. 15 specu) → fáze

| # | Kritérium | Plní fáze | Ověřeno ve fázi |
|---|---|---|---|
| 1 | `docker compose up` na čistém stroji bez ručních kroků | F14, F9 | F14, F16 |
| 2 | login `user`/`admin`; nepovolené akce → 403 i při ručním POSTu | F9, F12, F13 | F9, F12, F13 |
| 3 | Založení: dotazník + komponenty → duplicity → klasifikační krok → potvrzení | F11 | F11 |
| 4 | `AUTO_DECISION_PERSON` nikdy pod `VELKA`; admin neuloží pod minimum | F1, F11, F13 | F1, F11, F13 |
| 5 | Signály deterministické; LLM je nemůže změnit | F2, F6 | F2, F6 |
| 6 | Nevalidní odpověď / timeout → 1 retry → fallback; formulář se neztratí | F6, F11 | F6, F11 |
| 7 | Mock provider = kompletní demo offline bez klíče | F6, F14 | F6, F14 |
| 8 | Pseudonymizace; classify payload bez kontaktních polí | F3, F6 | F3, F6 |
| 9 | `llm_audit` bez obsahu, s verzemi/outcome | F6, F7 | F6, F7 |
| 10 | Vyřazení s důvodem, historie zůstává, admin obnoví | F5, F13 | F13 |
| 11 | `DUPLICATE_OVERRIDE` v historii; do LLM max top-10 | F4, F11 | F4, F11 |
| 12 | Historie kdo/kdy/akce/pole/stará→nová; přežívá vyřazení | F5, F10 | F5, F13 |
| 13 | Riziková změna → re-klasifikace; komponenty → „vyžaduje review" | F12 | F12 |
| 14 | CSRF → 403; souběžný edit → 409 | F8, F12 | F8, F12 |
| 15 | `/health`; `LLM_PROVIDER` jen env; testy guardrailů zelené | F8, F7, F1–F3+F6 | F7, F8, F16 |
| 16 | Repo bez secrets; `.env.example`; `prompts/`; README kompletní | F0, F6, F15 | F15, F16 |

**Všech 16 kritérií je pokryto.**

---

## 6. Rizika

| # | Riziko | Pravd. | Dopad | Mitigace |
|---|---|---|---|---|
| R1 | **Keycloak v Dockeru: neshoda issueru a redirect URI.** Prohlížeč vidí `localhost:8080`, kontejner aplikace `keycloak:8080` → „issuer mismatch" nebo „connection refused". | HIGH | HIGH | Oddělené `OIDC_ISSUER_URL` (veřejná) a `OIDC_INTERNAL_URL` (backchannel). `KC_HOSTNAME=http://localhost:8080` + `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`. Authlib s explicitními endpointy. Ověřit ve F9 samostatně, ne až ve F14. |
| R2 | **Realm import selže / neobsahuje role a uživatele** → nejde se přihlásit. | MED | HIGH | Realm export vygenerovat z ručně nakonfigurovaného Keycloaku (`kc.sh export`), ne psát ručně. Healthcheck + `service_healthy`. F9 ověřuje login obou účtů. |
| R3 | **Authlib × httpx nekompatibilita verzí.** | MED | MED | Pinovat ze stejného ověřeného `pip freeze`; ověřit v Docker buildu (F14). Při konfliktu snížit httpx. |
| R4 | **„Běží na první pokus" na cizím stroji selže** — chybějící `.env`, port 8080, CRLF, SQLite na bind mountu. | MED | HIGH | Compose funguje bez `.env` (`${VAR:-default}`); named volumes; `.gitattributes eol=lf`; test z čerstvého clone po `down -v` (F14). Alternativní porty v README. |
| R5 | **CSRF u vícekrokového formuláře** — token vs. mezistav, tlačítko zpět, fallback. | MED | MED | Jeden CSRF token per session; mezistav wizardu pod jedním klíčem; po fallbacku se stav nemaže. Ruční test „zpět + znovu odeslat" (F11). |
| R6 | **Nepinované / rozbité závislosti.** | LOW | HIGH | Vše `==`; build image v F14 je autoritativní ověření. |
| R7 | **LLM vrátí nevalidní strukturovaný výstup.** | MED | LOW | Pydantic validace + 1 retry + fallback (F6); mock default pro demo. |
| R8 | **Rozsah plánu vs. 8h rámec zadání.** | HIGH | MED | Povinné jádro F0–F15. Odřezatelné: filtry, doplňkové testy F4/F5, kosmetika. Cokoli neuděláno → README vědomý dluh (zadání to oceňuje). |
| R9 | **`SameSite=Lax` + OIDC redirect.** | LOW | MED | Callback je GET navigace → Lax funguje. `Secure` řízený env. |
| R10 | **Volání LLM mimo Gateway.** | LOW | HIGH | Adaptery importované jen v `gateway.py`; grep ve F16; test allowlistu ve F6. |

---

## 7. Kritéria úspěchu plánu

- [ ] Fáze 0–16 v uvedeném pořadí; deterministická jádra (F1–F4) hotová a otestovaná dřív než Docker, Keycloak nebo UI.
- [ ] Každá fáze má vlastní commit (conventional commits).
- [ ] `pytest tests/ -v` zelené po F6 a znovu po F16.
- [ ] Čistý clone bez `.env` → `docker compose up` → funkční přihlášení a kompletní scénář v mock režimu.
- [ ] Všech 16 akceptačních kritérií odškrtnuto na běžící instanci (F16).
- [ ] README obsahuje Master Prompt, model a verzi, sebe-klasifikaci s odůvodněním, postup výměny za Entra ID, retenci dat, disclaimer a vědomý dluh.
- [ ] V repu ani v image žádné secrets.
