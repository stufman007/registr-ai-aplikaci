# Spec: Registr interních AI aplikací

> Domácí úkol — pozice AI Implementation Expert, varianta A.
> Zadání viz [entr_specs.md](../../entr_specs.md). Tento dokument je **schválený design
> a jediný source of truth** pro implementaci; zároveň základ **Master Promptu** v README.
>
> Revize 2026-08-11: zapracována externí oponentura (GPT 5.6) — viz
> [docs/reviews/2026-08-11-oponentura-gpt.md](../reviews/2026-08-11-oponentura-gpt.md),
> kde je záznam, co bylo převzato, co zamítnuto a proč.

## 0. Principy návrhu

1. **LLM není zdroj pravdy ani policy engine.** LLM pomáhá se srozumitelným zdůvodněním
   a hledáním podobností. Tvrdá governance pravidla, autorizace a audit jsou
   deterministické, verzované a testované.
2. **MALÁ / STŘEDNÍ / VELKÁ je interní governance tier, ne právní klasifikace.**
   Legislativní příznaky `GDPR` / `AI-ACT` / `DORA` jsou signály k dalšímu review,
   nikoli právní závěr. (Disclaimer v README i UI.)
3. **Audit přežívá záznam.** Záznamy se fyzicky nemažou — vyřazení je soft delete,
   auditní historie zůstává.
4. **Graceful degradation.** Výpadek LLM nesmí způsobit ztrátu formuláře ani obejití
   povinných pravidel; existuje deterministický fallback.
5. **Minimalizace před pseudonymizací.** Do LLM tečou jen allowlistovaná pole; osobní
   údaje se pseudonymizují. Anonymizér není obecné DLP — limit přiznaný v README.
6. **YAGNI.** Malý server-rendered monolit; žádné mikroservisy, React, vector DB.

## 1. Kontext a cíl

Fintech společnost interně vyvíjí AI nástroje a potřebuje nad nimi udržet přehled:

1. **Governance a legislativa** — u každého nástroje je jasné, kdo ho vlastní, jaký má
   governance tier, jaké vykazuje legislativní signály (GDPR, EU AI Act, DORA), jaké AI
   komponenty používá a v jakém je stavu. EU AI Act se vztahuje i na čistě interní
   aplikace (typicky HR nástroje hodnotící zaměstnance = vysoké riziko dle Annex III) —
   proto klasifikace nemůže legislativní signály ignorovat ani u aplikací, jejichž
   výstup nikdy nejde ke klientovi.
2. **Interní pořádek** — před založením nového záznamu aplikace upozorní na podobné
   existující nástroje, aby nevznikaly duplicity.

Výsledek: malá webová aplikace, která se dá reálně provozovat. Uživatel se přihlásí
firemním účtem (OIDC), založí záznam přes klasifikační dotazník, LLM navrhne tier
**MALÁ / STŘEDNÍ / VELKÁ** se zdůvodněním, deterministická pravidla stanoví minimální
povolený tier a legislativní signály. Člověk návrh potvrdí nebo zvýší; pod policy
minimum ho nesníží nikdo (ani admin).

## 2. Rozsah

**V rozsahu (MVP):**

- OIDC přihlášení (Keycloak v docker-compose), role `user`/`admin` vynucené na backendu
- CRUD registru s **vyřazením/obnovením místo mazání** (soft delete)
- Karta aplikace + 1:N seznam **AI komponent** (strukturovaně)
- Klasifikace: pevný dotazník (9 otázek) → LLM návrh → deterministická eskalační minima
  s reason codes → lidské potvrzení
- **Deterministické legislativní signály** GDPR / AI-ACT / DORA (badge + hover tooltip)
- Duplicity: lokální předvýběr kandidátů + LLM posouzení; override s důvodem
- Immutable historie změn (kdo, kdy, akce, pole, stará → nová hodnota)
- Review light: změna rizikových vstupů vynutí novou klasifikaci
- LLM Gateway: allowlist → limity → pseudonymizace → adapter → validace → deanonymizace
- Adaptery `mock` (default, bez klíče) a `anthropic` (Claude); failure contract
  (timeout, 1 retry, deterministický fallback)
- LLM audit log: jen metadata + verze promptu/pravidel, nikdy obsah; retence 90 dní
- CSRF ochrana, bezpečné session cookie, max délky vstupů, optimistic locking
- Docker compose, `/health`, syntetický seed, README s Master Promptem, složka `prompts/`
- Zúžené unit testy guardrailů (policy, signály, anonymizér, fallback)

**Mimo rozsah (vědomý dluh, popsat v README):**

Schvalovací/risk-acceptance workflow · fulltext, embeddings, vektorová DB · Postgres +
Alembic migrace · CI/CD · obecné NER/DLP nad volným textem · scheduler a notifikace
review (vč. periodických termínů review_due) · rate limiting · request correlation ID ·
verze modelu a data-egress flag u AI komponent · duplicitní kontrola při editaci ·
rotace session ID · integration/E2E testy.

## 3. Uživatelé a role

| Role | Práva |
|---|---|
| `user` | vidí aktivní registr a historii; zakládá záznamy; edituje záznamy, kde je vlastníkem nebo autorem; potvrzuje/zvyšuje tier; nevyřazuje ani neobnovuje |
| `admin` | navíc edituje libovolný záznam, vyřazuje a obnovuje (povinný důvod), může upravit návrh klasifikace — **ale nikdy pod deterministické policy minimum** |

„Vlastník nebo autor" = e-mail přihlášeného se shoduje s `vlastnik_email`, nebo jeho
OIDC username se shoduje s `created_by`.

- Role žijí v Keycloaku, přicházejí v OIDC tokenu; **autorizace se vynucuje na backendu
  u každého požadavku** (FastAPI dependency), ne skrytím tlačítka. UI akce bez práv
  nezobrazuje, ale to je jen kosmetika nad backendovou kontrolou.
- MFA aplikace neřeší — zodpovědnost identity providera (README vysvětlí proč: aplikace
  o MFA nesmí ani vědět, jinak by výměna IdP nebyla jen konfigurace).
- Výjimka pod policy minimum by byl samostatný risk-acceptance proces — není v MVP,
  je v dluhu. Žádný skrytý admin override.

## 4. Datový model

### 4.1 Aplikace (`applications`)

| Pole | Typ | Poznámka |
|---|---|---|
| `id` | UUID | |
| `nazev` | text, povinné | max délka dle validace |
| `popis` | text, povinné | vstup pro klasifikaci i duplicity |
| `vlastnik_jmeno`, `vlastnik_email` | text | osobní údaj |
| `zastupce_jmeno`, `zastupce_email` | text | osobní údaj |
| `spravce_jmeno`, `spravce_email` | text | technický správce; osobní údaj |
| `klasifikace_llm` | enum nullable | původní návrh LLM (nebo fallback baseline) |
| `klasifikace_minimum` | enum | deterministicky spočtený policy floor |
| `klasifikace` | enum `MALA`/`STREDNI`/`VELKA` | effective tier; vždy `>= klasifikace_minimum` |
| `klasifikace_zduvodneni` | text | české vysvětlení (LLM, případně doplněné pravidly) |
| `klasifikace_priznaky` | JSON | deterministické signály, viz 5.4 |
| `klasifikace_potvrdil` | text | kdo effective tier potvrdil/upravil |
| `klasifikace_poznamka` | text nullable | povinná při ruční změně proti návrhu |
| `dotaznik_odpovedi` | JSON | snapshot odpovědí poslední klasifikace (audit trail) |
| `stav` | enum `VYVOJ`/`PILOT`/`PROVOZ`/`UTLUMENO` | |
| `review_required` | bool | změna rizikových vstupů bez re-klasifikace |
| `version` | int | optimistic locking, +1 při každém update |
| `created_by`, `created_at`, `updated_by`, `updated_at` | | |
| `deleted_at`, `deleted_by`, `delete_reason` | nullable | soft delete; důvod povinný |

**Fyzický DELETE neexistuje.** Výchozí pohledy filtrují `deleted_at IS NULL`;
admin vidí a obnovuje vyřazené.

### 4.2 AI komponenty (`ai_components`)

Aplikace používá 1–N AI komponent (naplňuje pole „použitý AI model" ze zadání,
strukturovaně — volný text se nedá filtrovat ani auditovat). Minimálně jedna při založení.

| Pole | Poznámka |
|---|---|
| `id`, `application_id` | UUID, FK |
| `provider` | `Anthropic` / `OpenAI` / `Azure` / `interní` / `jiný` |
| `model_name` | např. „Claude Sonnet" |
| `purpose` | co komponenta v aplikaci dělá |
| `hosting_type` | `externí API` / `firemní cloud` / `on-prem` / `neznámé` |

Změna komponent nastaví `review_required=true` (kap. 9). Verze modelu a data-egress
flag = vědomý dluh.

### 4.3 Immutable historie (`record_history`)

Append-only; **nemaže se nikdy**, ani při vyřazení záznamu.

| Pole | Poznámka |
|---|---|
| `id`, `record_id`, `actor`, `kdy` | |
| `action` | `CREATE` / `UPDATE` / `CLASSIFY` / `RETIRE` / `RESTORE` / `DUPLICATE_OVERRIDE` |
| `pole` | změněné pole, `null` u událostí bez pole |
| `stara_hodnota`, `nova_hodnota` | textová reprezentace |
| `reason` | povinný u `RETIRE` a `DUPLICATE_OVERRIDE`, jinak volitelný |

Privacy: nikdy neserializovat session/tokeny/secrets ani LLM obsah. Historie může
obsahovat historické hodnoty kontaktů — stejná access control jako registr, trade-off
otevřeně popsaný v README (produkční retence auditu = governance rozhodnutí mimo MVP).

### 4.4 LLM audit (`llm_audit`)

| Pole | Poznámka |
|---|---|
| `id`, `kdy` | |
| `ucel` | `classify` / `duplicates` |
| `provider`, `model` | např. `anthropic`, `claude-…`, nebo `mock` |
| `prompt_version`, `rules_version` | reprodukovatelnost přes verze, ne obsah |
| `tokens_in`, `tokens_out`, `latence_ms` | u mocku tokeny 0 |
| `uspech` | bool |
| `error_code` | nullable: `TIMEOUT` / `INVALID_RESPONSE` / `PROVIDER_5XX`… |
| `fallback_used` | bool |

**Nikdy prompt ani odpověď** — ani pseudonymizovaně. Retence 90 dní, čistí se při startu.

## 5. AI funkce 1: klasifikace

### 5.1 Klasifikační dotazník (pevný, 9 otázek)

Vyplňuje se při založení a znovu při změně rizikových vstupů. Volby s ⓘ mají tooltip
s vysvětlením a příklady.

1. **K čemu aplikace slouží a jaký proces podporuje?** — volný text
2. **Kolik lidí ji používá / bude používat?** — `do 10` / `10–50` / `přes 50`
   *(vstup pro LLM, není sám o sobě policy floor)*
3. **Jak kritický proces podporuje?**
   - `Pomocný` ⓘ *Usnadňuje práci, bez něj se obejdeme; výpadek nikoho nezastaví.
     Příklad: generátor e-mailových podpisů, přehled obědů.*
   - `Důležitý` ⓘ *Tým se bez něj citelně zpomalí, existuje ruční náhrada.
     Příklad: příprava reportů, sumarizace smluv.*
   - `Kritický` ⓘ *Výpadek zastaví část firmy nebo ohrozí povinnost vůči klientům či
     regulátorovi. Příklad: podpora schvalování úvěrů, výpočet rizikových skóre.*
4. **Zpracovává osobní údaje?**
   - `Ne` ⓘ *Žádná jména, e-maily, telefony, identifikátory osob — ani v logách.*
   - `Zaměstnanců` ⓘ *Údaje kolegů: docházka, výkonnost, interní adresář.*
   - `Klientů` ⓘ *Údaje zákazníků: smlouvy, transakce, kontakty. Nejpřísnější režim.*
5. **Rozhoduje nebo doporučuje o lidech?**
   - `Ne` ⓘ *Výstup se netýká hodnocení ani třídění konkrétních osob.*
   - `Doporučuje, člověk rozhoduje` ⓘ *AI připraví návrh (pořadí kandidátů, skóre),
     finální rozhodnutí dělá člověk.*
   - `Rozhoduje automatizovaně` ⓘ *Výstup se přímo promítne do rozhodnutí o osobě bez
     lidské kontroly. Signál vysokého rizika dle EU AI Act.*
6. **Je výstup viditelný mimo firmu?** — `Ne` / `Nepřímo (vstupuje do klientských
   výstupů)` / `Ano, přímo`
7. **Jak citlivá data zpracovává?**
   - `Veřejná` ⓘ *Informace běžně dostupné mimo firmu.*
   - `Interní` ⓘ *Běžné pracovní dokumenty; únik je nepříjemný, ne kritický.*
   - `Důvěrná` ⓘ *Smlouvy, finanční data, strategie; únik = reálná škoda.*
   - `Vysoce citlivá` ⓘ *Klientská portfolia, platební data, zvláštní kategorie osobních
     údajů; únik = incident vůči regulátorovi.*
8. **Může výstup AI vyvolat akci bez kontroly člověkem?**
   - `Ne` ⓘ *Výstup si člověk jen přečte.*
   - `Člověk schvaluje` ⓘ *AI akci připraví (draft e-mailu, návrh transakce), člověk
     odesílá.*
   - `Ano, automaticky` ⓘ *Výstup přímo spouští akci — odeslání, zápis, transakci.*
9. **Jaký je potenciální dopad chybného výstupu AI?**
   - `Zanedbatelný` ⓘ *Chybu si nikdo nemusí všimnout, nic se nestane.*
   - `Provozní` ⓘ *Ztracený čas, nutná ruční oprava.*
   - `Finanční nebo klientský` ⓘ *Špatné číslo v reportu, chybná informace klientovi.*
   - `Zásadní` ⓘ *Regulatorní incident, významná finanční ztráta, poškození klientů.*

Použité AI modely se nezadávají v dotazníku, ale jako strukturované **AI komponenty**
(kap. 4.2). Signál „externí AI provider" se odvozuje z `hosting_type` komponent.

### 5.2 Vyhodnocení: LLM navrhuje, pravidla rozhodují minimum

```text
dotazník + AI komponenty
→ LLM Gateway (kap. 7)                → návrh tieru + zdůvodnění (structured output)
→ schema validace (Pydantic)
→ deterministická policy pravidla     → klasifikace_minimum + reason codes
→ deterministické legislativní signály
→ effective tier = max(LLM návrh, minimum)
→ lidské potvrzení (potvrdit / zvýšit / admin upravit ≥ minimum)
```

LLM structured output: `{"klasifikace": "MALA|STREDNI|VELKA", "zduvodneni": "…česky,
cituje konkrétní odpovědi…"}`. LLM **nevrací legislativní příznaky** — ty vznikají
deterministicky v kódu (5.4).

### 5.3 Deterministická eskalační minima

Pravidla mají explicitní `RULES_VERSION` (např. `2026-08-v1`), ukládanou do LLM auditu.

| Podmínka | Minimum | Reason code |
|---|---|---|
| Automatizované rozhodování o lidech (ot. 5) | `VELKA` | `AUTO_DECISION_PERSON` |
| Kritický proces **a** výstup mimo firmu (ot. 3+6) | `VELKA` | `CRITICAL_EXTERNAL_OUTPUT` |
| Automatická akce **a** zásadní dopad (ot. 8+9) | `VELKA` | `AUTONOMOUS_HIGH_IMPACT` |
| Osobní údaje klientů (ot. 4) | `STREDNI` | `CLIENT_PERSONAL_DATA` |
| Kritický proces (ot. 3) | `STREDNI` | `CRITICAL_PROCESS` |
| Vysoce citlivá data (ot. 7) | `STREDNI` | `HIGHLY_SENSITIVE_DATA` |
| Automatická akce bez člověka (ot. 8) | `STREDNI` | `AUTONOMOUS_ACTION` |
| Externí AI provider (komponenty) **a** důvěrná/vysoce citlivá data (ot. 7) | `STREDNI` | `EXTERNAL_PROVIDER_SENSITIVE_DATA` |

```text
klasifikace_minimum  = max(aktivovaná pravidla, MALA)
navrh                = max(klasifikace_llm, klasifikace_minimum)
klasifikace          = navrh                                    # bez ruční změny
klasifikace          = max(klasifikace_minimum, ruční hodnota)  # ruční změna admina
klasifikace          = ruční hodnota, pokud >= navrh             # ruční změna uživatele
```

`max(...)` platí jen dokud klasifikaci nikdo ručně nemění. Jakmile člověk zadá
ruční hodnotu, floor (`klasifikace_minimum`) zůstává jediná tvrdá hranice, ale
admin ji smí uplatnit i *pod* AI návrhem — AI návrh je jen podnět, ne zdroj
pravdy. Běžný uživatel smí návrh jen potvrdit nebo zvýšit; ruční snížení proti
návrhu (i nad minimem) backend od něj odmítne, aby se neshodovalo tiše přebít
zpátky na `max(...)`.

Navrhne-li LLM méně než minimum, aplikace tier zvedne a do zdůvodnění doplní, které
pravidlo (reason code) eskalaci způsobilo. Odpověď na „proč zrovna takto" u pohovoru:
*AI nesmí být jediná pojistka v governance nástroji — pravidla jsou auditovatelná
a testovaná, LLM dodává srozumitelné zdůvodnění a chytá nuance mimo pravidla.*

### 5.4 Deterministické legislativní signály

Vznikají v kódu z odpovědí a komponent; LLM je nemůže odstranit ani vytvořit.
Whitelist zkratek: `GDPR`, `AI-ACT`, `DORA`.

| Signál | Pravidlo |
|---|---|
| `GDPR` | ot. 4 ≠ Ne |
| `AI-ACT` | ot. 5 ≠ Ne (u „rozhoduje automatizovaně" důraznější detail) |
| `DORA` | kritický proces (ot. 3) **a** externí AI provider (z komponent) |

Struktura: `{zkratka, titulek, detail, reason_code, source: "deterministic_rule"}`.
UI: **badge se zkratkou**, hover tooltip s titulkem a detailem. Signál = podnět
k governance/legal review, ne právní závěr (disclaimer).

**AI kontextová věta (doplněk, ne náhrada):** existence a kostra signálu je a
zůstává 100% deterministická — LLM je nemůže vytvořit ani odstranit. Nad rámec
toho ale `gateway.classify` pošle modelu seznam AKTIVNÍCH signálů (zkratka +
reason_code + deterministický detail, spočtené `compute_flags` PŘED voláním
LLM) a model smí ke KAŽDÉMU z nich vrátit jednu českou kontextovou větu
vázanou na konkrétní záznam (structured output `signal_context: dict[str,
str]`, klíč = zkratka). Validace přijme jen zkratky, které byly skutečně
poslané jako aktivní (cizí/vymyšlená zkratka se zahodí), větu ořízne na max.
délku a deanonymizuje až po validaci — stejný vzor jako `zduvodneni`. Selže-li
LLM nebo větu nevrátí, signál se uloží beze změny (prázdný `signal_context`,
graceful degradation) — kostra sama o sobě stačí. Uložení: `Flag.to_dict()`
zůstává beze změny, dict v `klasifikace_priznaky` se obohatí o volitelný klíč
`ai_kontext` až při skládání v route (`regulatory.flags_to_dicts`). UI:
tooltip zobrazí deterministický detail a pod ním, jen pokud existuje,
vizuálně odlišenou (kurzívou) AI kontextovou větu.

### 5.5 Lidská kontrola

UI v klasifikačním kroku zobrazí vedle sebe: **AI návrh** · **policy minimum**
(+ aktivovaná pravidla) · **výsledný tier** · **signály** · zdůvodnění. Uživatel
potvrdí nebo zvýší; admin může upravit, ale jen `>= minimum`. Každá ruční změna proti
návrhu vyžaduje poznámku a zapisuje se do historie (`CLASSIFY`).

## 6. AI funkce 2: kontrola duplicit

Při ukládání nového záznamu:

1. **Lokální předvýběr** (stdlib `difflib`, žádná nová závislost): top-10 kandidátů
   z aktivního registru dle podobnosti názvu+popisu. Do LLM nejde celý registr —
   levnější, méně dat ven, škáluje.
2. Kandidáti projdou **LLM Gateway** → LLM vrátí max 3 skutečně podobné:
   `[{"record_id": …, "duvod_podobnosti": …}]` (klidně prázdné).
3. UI je zobrazí s vysvětlením; uživatel volí:
   - **„Zrušit — už existuje"**, nebo
   - **„Pokračovat — je to jiná aplikace"** + povinný krátký důvod
     → zapíše se `DUPLICATE_OVERRIDE` do historie.

**Fallback**: když LLM selže, zobrazí se lokální kandidáti nad prahem s poznámkou
„AI kontrola podobnosti není dostupná; zobrazeny výsledky lokální kontroly."
Formulář se neztratí, pokračování se stejným povinným důvodem.

## 7. LLM Gateway a pseudonymizace

**Jediná cesta do LLM.** Tok:

```text
allowlist polí → limity délky + sanitizace → pseudonymizace → prompt assembly
→ LLM adapter (mock | anthropic) → schema validace → deanonymizace → aplikace
```

### 7.1 Allowlist a minimalizace

Každý AI účel má explicitní seznam polí, která smí odejít:

- `classify`: odpovědi dotazníku + AI komponenty. **Kontakty se neposílají vůbec** —
  pro klasifikaci nejsou potřeba (minimalizace > pseudonymizace).
- `duplicates`: název + popis nového záznamu + top-10 kandidátů (název + popis).

### 7.2 Pseudonymizace s reverzní mapou

Chrání hlavně volné texty (popis může zmiňovat jména). Tok **Jméno → Pseudonym → LLM →
Pseudonym ve výstupu → Jméno ve výstupu**, na příkladu:

**Krok 0 — vstup (reálná data):**

```text
Popis: „Sumarizace smluv, technicky spravuje Jan Novák (jan.novak@firma.cz)."
```

**Krok 1 — pseudonymizace (před sestavením promptu):** anonymizér vytvoří pro požadavek
převodní mapu a nahradí výskyty ve všech odchozích textech:

```text
Jan Novák           → [OSOBA_1]
jan.novak@firma.cz  → [EMAIL_1]
+420 601 123 456    → [TEL_1]
```

- E-maily a telefony se ve volném textu chytají vzorem (mají pevný tvar); jména
  porovnáním proti známým kontaktům z registru. Obecný NER neděláme — limit v README.
- Stejná hodnota = stejný placeholder v rámci požadavku.

**Krok 2 — volání LLM:** prompt obsahuje jen pseudonymy:
`„…technicky spravuje [OSOBA_1] ([EMAIL_1])…"`. LLM reálné údaje nikdy nevidí.
Mapa existuje **jen v paměti požadavku** — neukládá se, neloguje.

**Krok 3 — výstup LLM (s pseudonymy):**
`„…Aplikaci spravuje [OSOBA_1] a zpracovává smlouvy…"`

**Krok 4 — deanonymizace:** po schema validaci se dle mapy dosadí zpět reálné hodnoty:
`„…Aplikaci spravuje Jan Novák…"` — teprve to vidí uživatel a ukládá se. Mapa se
s koncem požadavku zahodí.

**Proč pseudonymizace, ne plná anonymizace:** zadání vyžaduje „po zpracování je vrať
zpět" — reverzibilita pro srozumitelný výstup; AI přitom osobní údaje nevidí.

### 7.3 Sanitizace a limity

- Max délky všech volných textů (validace i před promptem).
- Vstupní texty jsou v promptu explicitně ohraničené jako **data, ne instrukce**
  (obrana proti prompt injection z popisu aplikace).
- Structured output vždy validován Pydantic schématem.
- `PROMPT_VERSION` v souborech `prompts/`, ukládá se do auditu.

## 8. LLM failure contract

- Timeout na volání: **10 s** (env). Max **1 retry**, jen na transportní chybu /
  rate-limit / 5xx. Nekorektní JSON po retry → `INVALID_RESPONSE`, fallback.
- Chyba se zapíše jen jako metadata do `llm_audit` (nikdy raw response).
- **Fallback klasifikace**: `klasifikace_llm = MALA (baseline)` → effective =
  `max(MALA, minimum z pravidel)`. Uživateli: *„AI zdůvodnění není momentálně dostupné.
  Governance tier byl určen povinnými pravidly."* Může tier zvýšit a uložit — povinná
  pravidla fungují bez LLM.
- **Fallback duplicit**: kap. 6.
- Formulář se při žádném selhání neztrácí.

## 9. Review light

Registr nemá být pravdivý jen v okamžiku založení:

- Editace **rizikových vstupů** (odpovědi dotazníku ot. 3–9) → před uložením se znovu
  spustí klasifikace (nový návrh + minimum + signály, člověk potvrdí).
- Změna **AI komponent** → `review_required = true`; badge „vyžaduje review" na kartě
  i v registru, zmizí po proběhlé re-klasifikaci.
- Periodická review s termíny (`review_due_at` + 12 měsíců, scheduler, notifikace) =
  vědomý dluh.

## 10. Architektura a technologie

```text
[prohlížeč] ←HTML/form POST→ [FastAPI monolit] ←OIDC→ [Keycloak kontejner]
                                  ├── auth / RBAC / CSRF / session
                                  ├── services: policy · regulatory · similarity · history
                                  ├── LLM Gateway → adapter mock | anthropic
                                  └── SQLite (soubor na volume)
```

- **Python + FastAPI + Jinja2**, server-rendered HTML; minimální vanilla JS (tooltips,
  přidání řádku komponenty).
- **SQLite** + SQLAlchemy; `create_all` při startu (bez migrací = přiznaný dluh).
- Struktura repa:

```text
app/
  main.py              # startup: create_all, seed, purge llm_audit, /health
  config.py            # pydantic-settings, vše z .env
  auth.py              # OIDC flow, session, require_user/require_admin
  security.py          # CSRF helpers, security headers
  models.py, db.py, schemas.py
  routes/              # registry.py, classification.py, admin.py
  services/
    policy.py          # eskalační minima + RULES_VERSION
    regulatory.py      # deterministické signály GDPR/AI-ACT/DORA
    similarity.py      # difflib předvýběr duplicit
    history.py         # immutable audit události
  llm/
    gateway.py         # allowlist → sanitizace → pseudonymizace → validace → deanonymizace
    base.py, mock.py, anthropic.py
    anonymizer.py, audit.py
  templates/, static/
tests/                 # test_policy.py, test_regulatory.py, test_anonymizer.py, test_fallback.py
keycloak/realm-export.json
prompts/               # classification.md, duplicates.md (s PROMPT_VERSION)
docs/specs/, docs/reviews/
Dockerfile, docker-compose.yaml, .env.example, README.md, seed.py, requirements.txt
```

## 11. Identita, web security, souběh

- **OIDC**: Keycloak v compose, `--import-realm` (2 účty: `user`, `admin`), Authlib
  Authorization Code flow. Env: `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`,
  `OIDC_CLIENT_SECRET`, `OIDC_ROLES_CLAIM`. **Výměna za Entra ID = změna těchto hodnot**
  (issuer `https://login.microsoftonline.com/<tenant>/v2.0`, App registration client
  ID/secret, app roles claim) — README popíše přesný postup. Žádná změna kódu.
- **Session**: podepsaný cookie (Starlette SessionMiddleware), `HttpOnly`,
  `SameSite=Lax`, `Secure` mimo lokální dev, `max_age` omezená (např. 8 h), secret z env.
  V session jen identita + role, žádné IdP tokeny.
- **CSRF**: každý state-changing POST nese token (session + hidden field); bez platného
  tokenu 403. Nezávislé na RBAC.
- **Validace vstupů**: povinná pole, enumy, e-mail formát, max délky; ORM = žádné raw SQL.
- **Optimistic locking**: formulář nese skryté `version`; při neshodě s DB → 409 +
  hláška „záznam mezitím změnil někdo jiný", žádný tichý přepis.
- Logování přihlášení/odhlášení (username, čas) — bez tokenů.

## 12. Provoz

- `docker compose up` → Keycloak (healthcheck) → app (depends_on healthy). Start app:
  `create_all`, seed (jen prázdná DB), purge `llm_audit` > 90 dní.
- `/health` bez autentizace: stav app + DB; nevypisuje konfiguraci ani secrets.
- Logy: start, chyby, login/logout, LLM audit metadata.
- Secrets jen `.env` (v repu úplný `.env.example` bez reálných hodnot); `.gitignore`:
  `.env`, `*.db`. Závislosti pinované (`requirements.txt` s `==`).
- **Seed**: ~8 fiktivních aplikací (fiktivní jména/e-maily na `@example.com`), různé
  tiery, stavy, signály, více komponent, jedna vyřazená — ať je na videu co ukazovat.

## 13. Retence dat

| Data | Retence |
|---|---|
| Aktivní/vyřazené záznamy (vč. kontaktů) | po dobu evidence; vyřazení = soft delete |
| Immutable historie | zachována i po vyřazení; produkční retenční politika = governance rozhodnutí mimo MVP (README) |
| LLM audit metadata | 90 dní, čistí se při startu |
| Session | do expirace cookie (max 8 h) / odhlášení |
| Reverzní pseudonymizační mapa | jen RAM jednoho požadavku |
| Obsah promptů/odpovědí LLM | neukládá se nikdy |
| Zvuk/přepisy | aplikace žádné nezpracovává |

## 14. UI obrazovky (server-rendered, česky)

1. **Přihlášení** — tlačítko → Keycloak.
2. **Registr** — tabulka: název, vlastník, tier (barevný štítek), signály (badge +
   hover), stav, komponenty (zkráceně), badge „vyžaduje review". Filtry: stav, tier.
   Default jen aktivní; admin má filtr vyřazených.
3. **Nový záznam** — kontakty + AI komponenty (≥1) + dotazník (9 otázek, tooltips ⓘ)
   → krok duplicity (jen když jsou kandidáti) → **klasifikační krok**: vedle sebe AI
   návrh / policy minimum + pravidla / výsledný tier / signály / zdůvodnění → potvrzení.
4. **Detail** — karta, komponenty, signály s tooltips, historie změn, akce dle práv
   (editovat / překlasifikovat / vyřadit / obnovit).
5. **Editace** — změna rizikových vstupů vynutí re-klasifikaci před uložením.

## 15. Akceptační kritéria

1. `docker compose up` na čistém stroji → app + Keycloak bez ručních kroků.
2. `user` i `admin` se přihlásí přes Keycloak; nepovolené akce → 403 i při ručním
   POSTu (user: cizí edit, vyřazení; obojí umí jen admin).
3. Založení: dotazník + komponenty → duplicity → klasifikační krok (návrh / minimum /
   výsledek / signály) → potvrzení → registr.
4. `AUTO_DECISION_PERSON` nikdy neskončí pod `VELKA`; admin neuloží tier pod minimum
   (backend odmítne).
5. Signály vznikají deterministicky; LLM je nemůže změnit ani přidat cizí zkratku.
6. LLM vrátí nevalidní odpověď / timeout → max 1 retry → fallback; formulář se
   neztratí; chyba jen jako metadata v auditu.
7. Mock provider umožní kompletní demo offline (deterministicky), bez API klíče.
8. Pseudonymizace: známá jména/e-maily/telefony se v payloadu do adapteru neobjeví;
   classify payload neobsahuje kontaktní pole vůbec.
9. `llm_audit` bez obsahu, s provider/model/verzemi/outcome.
10. Vyřazení: povinný důvod, záznam zmizí z aktivního pohledu, historie zůstává,
    admin obnoví.
11. `DUPLICATE_OVERRIDE` s důvodem v historii; do LLM jde max top-10 předvybraných.
12. Historie zapisuje kdo/kdy/akce/pole/stará→nová; přežívá vyřazení.
13. Změna rizikových odpovědí vynutí re-klasifikaci; změna komponent rozsvítí
    „vyžaduje review".
14. CSRF bez tokenu → 403; souběžný edit se starou `version` → 409, ne tichý přepis.
15. `/health` odpovídá; přepnutí `LLM_PROVIDER=mock↔anthropic` mění jen env; testy
    guardrailů (kap. 16) procházejí.
16. Repo bez secrets; `.env.example` úplný; `prompts/` s verzovanými prompty; README
    s Master Promptem, modelem, klasifikací této aplikace samotné, vědomým dluhem
    a disclaimerem „tier ≠ právní klasifikace".

## 16. Testovací minimum (zúžené — čistá logika)

- **policy**: každé pravidlo zvlášť · kombinace = nejvyšší minimum · LLM `MALA` + floor
  `VELKA` → `VELKA` · ruční zvýšení OK · snížení pod minimum odmítnuto.
- **regulatory**: osobní údaje → GDPR · rozhodování o lidech → AI-ACT · kritický +
  externí provider → DORA · neznámá zkratka se nikdy neobjeví.
- **anonymizer**: jméno/e-mail/telefon → placeholder a zpět · mapa není persistentní.
- **fallback**: nevalidní JSON / timeout → fallback klasifikace s dodrženými minimy.

Integration/E2E testy = vědomý dluh (zadání testovací pokrytí nehodnotí).

## 17. Master Prompt (poznámka)

README uvede Master Prompt = kondenzovaná verze tohoto specu + název a verzi modelu
(Claude Fable 5 / claude-fable-5). Invarianty, které Master Prompt musí zachovat:

- LLM není policy engine; effective tier nikdy pod deterministický floor.
- Legislativní signály vznikají deterministicky.
- Žádný hard delete záznamů ani historie.
- Žádný raw prompt/response v auditu — reprodukovatelnost přes verze.
- Veškerý LLM provoz přes Gateway (allowlist + pseudonymizace + validace).
- LLM selhání má deterministický fallback; formulář se neztrácí.
- Rizikové změny spouštějí re-klasifikaci.
- Jednoduchý server-rendered FastAPI monolit.
