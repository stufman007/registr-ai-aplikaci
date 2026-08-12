# Registr interních AI aplikací

> Domácí úkol — pozice AI Implementation Expert, varianta A. Zadání viz
> [`entr_specs.md`](entr_specs.md), schválený design viz
> [`docs/specs/2026-08-11-ai-registr-design.md`](docs/specs/2026-08-11-ai-registr-design.md),
> implementační plán viz [`docs/plans/2026-08-11-implementacni-plan.md`](docs/plans/2026-08-11-implementacni-plan.md).

## Co to je

Fintech firma interně vyvíjí AI nástroje a potřebuje nad nimi udržet přehled ze
dvou důvodů: **governance a legislativa** (kdo nástroj vlastní, jaký má interní
governance tier, jaké vykazuje legislativní signály GDPR / EU AI Act / DORA,
jaké AI komponenty používá) a **interní pořádek** (aby nevznikaly duplicitní
nástroje). Tahle aplikace je ten registr.

Uživatel se přihlásí firemním účtem (OIDC), založí záznam přes devítiotázkový
klasifikační dotazník. AI (Claude, nebo offline mock) navrhne governance tier
**MALÁ / STŘEDNÍ / VELKÁ** a zdůvodní ho. Nad návrhem ale stojí deterministická
pravidla — tabulka eskalačních minim (`app/services/policy.py`) a legislativní
signály (`app/services/regulatory.py`) — které AI nemůže obejít ani snížit;
efektivní tier je vždy `max(AI návrh, policy minimum, ruční potvrzení)`.
Před uložením nového záznamu aplikace lokálně porovná název a popis s
existujícím registrem a jen předvybrané kandidáty pošle AI k posouzení
podobnosti. Nic se fyzicky nemaže — vyřazení je soft delete a auditní historie
zůstává navždy.

Je to malý server-rendered monolit (FastAPI + Jinja2 + SQLite), navržený tak,
aby ho autor uměl u pohovoru vysvětlit v libovolném místě — deterministická
pravidla jsou deklarativní tabulky, ne řetězy `if`, a LLM je schovaný za
jednu abstrakční vrstvu (Gateway), kterou lze vyměnit za firemní AI Gateway
beze změny zbytku aplikace.

```text
[prohlížeč] ←HTML/form POST→ [FastAPI monolit] ←OIDC→ [Keycloak kontejner]
                                  ├── auth.py       — OIDC flow, session, require_user/require_admin
                                  ├── security.py   — CSRF, security headers, optimistic locking
                                  ├── routes/       — registry · classification · edit · admin
                                  ├── services/      policy (eskalační minima)
                                  │                  regulatory (GDPR/AI-ACT/DORA signály)
                                  │                  similarity (lokální předvýběr duplicit)
                                  │                  history (immutable audit)
                                  ├── llm/gateway.py — allowlist → pseudonymizace → adapter →
                                  │                    validace → deanonymizace → audit
                                  │      ├── mock.py       (default, offline, bez klíče)
                                  │      └── anthropic.py  (Claude, přes LLM_PROVIDER=anthropic)
                                  └── SQLite (soubor na named volume)
```

## Jak to spustit

Doporučený způsob — **bez `.env`**, vše má bezpečné defaulty (`.env` je jen
pro reálný Claude klíč, viz níže):

```bash
docker compose up
```

Spustí se dvě služby: `keycloak` (identity provider, port 8080) a `app`
(vlastní aplikace, port 8000). `app` čeká, až je Keycloak healthy
(`depends_on: condition: service_healthy`), takže první start trvá desítky
sekund.

Otevřete **http://localhost:8000** → přesměruje na přihlášení Keycloaku.
Demo účty (z `keycloak/realm-export.json`):

| Uživatel | Heslo | Role |
|---|---|---|
| `user` | `user` | `user` |
| `admin` | `admin` | `user`, `admin` |

Po přihlášení uvidíte registr s **8 syntetickými aplikacemi** (7 aktivních +
1 vyřazená — „Plánovač směn v kavárnách", demonstruje soft delete a nesmazanou
historii). Jedna aplikace („Nástroj pro hodnocení výkonu zaměstnanců") má
rozsvícený badge „vyžaduje review". Různé tiery, legislativní signály a AI
komponenty jsou vidět napříč seznamem — viz `app/seed_data.py`.

S výchozím `LLM_PROVIDER=mock` funguje **kompletní demo offline**, bez API
klíče — klasifikace i kontrola duplicit vrací deterministické, věrohodné
odpovědi. Pro reálné volání Claude vytvořte `.env` (viz `.env.example`) s
`LLM_PROVIDER=anthropic` a `ANTHROPIC_API_KEY=...`.

### Alternativa — spuštění lokálně (bez kontejneru pro `app`)

Užitečné při vývoji, kdy chcete `--reload`:

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Windows
# .venv/bin/pip install -r requirements.txt         # macOS/Linux

docker compose up keycloak                          # jen identity provider
.venv\Scripts\python -m uvicorn app.main:app --reload
```

Defaulty v `app/config.py` počítají s tímto scénářem (`OIDC_ISSUER_URL` ==
`OIDC_INTERNAL_URL` == `http://localhost:8080/realms/registr`, protože mimo
Docker síť není mezi „veřejnou" a „interní" URL Keycloaku rozdíl).

### Troubleshooting

- **Port 8080 nebo 8000 obsazený** — jiná služba na stroji drží port. Upravte
  mapování portů v `docker-compose.yaml` (`ports:`) a odpovídající
  `OIDC_ISSUER_URL`/`OIDC_INTERNAL_URL` v `.env`.
- **Po úpravě `keycloak/realm-export.json` se změna neprojeví** — Keycloak
  importuje realm jen do prázdného datového volume. Spusťte
  `docker compose down -v` (smaže i volume s daty Keycloaku a SQLite, tedy i
  seed) a `docker compose up` znovu.
- **`docker compose up` hlásí unhealthy `app`** — zkontrolujte, že
  `LLM_PROVIDER` je buď `mock`, nebo `anthropic` s vyplněným
  `ANTHROPIC_API_KEY`; s `anthropic` bez klíče selže první LLM volání (fail
  fast), ne start kontejneru.

## Master Prompt

Zadání níže je **doslovný přepis** [`Master_prompt.md`](Master_prompt.md) —
prvního promptu, kterým byla aplikace zadaná do Claude Code na začátku práce
(vlastník úkolu = netechnický PO). Kompletní rozpracované zadání vzniklo
následně iterativně přes skill `brainstorming` (doptávání na otevřené otázky)
a je celé v [`docs/specs/2026-08-11-ai-registr-design.md`](docs/specs/2026-08-11-ai-registr-design.md)
— ten spec **je** plný, schválený Master Prompt pro implementaci; text níže
je jen jeho původní zadávací verze.

**Model: Claude Fable 5 (`claude-fable-5`)**

> Uložil jsem ti sem soubor s kompletním zadáním domácího úkolu, který jsem
> dostal zadaný u pohovoru na pozici AI implementačního specialisty. Vybral
> jsem si variantu A - Registr interních aplikací. Potřebuju abys prošel to
> zadání rozpracoval tu variantu A. Jde o nástroj potenciálně používaný ve
> společnosti co řeší fintech a potřebuje udržovat přehled nad všemi AI
> nástroji, které se interně vyvinou. Řeší se to z pohledu legislativy i z
> pohledu interní potřeby a evidence (aby se netvořily duplikáty). Vydefinuj
> přesné zadání dle požadovaných bodů a i pro vývoj aplikace připrav zadání
> tak, aby mi jako netechnickému PO bylo jasné, co se kde řeší a jak to
> funguje. API klíč ti dodám pro test, zatím funguj s mockem. Zahaj
> brainstorming skill a doptávej se mě na otevřené věci, které potřebuješ
> doplnit
>
> model: Fable 5

### Invarianty (spec kap. 17), které musí platit v každé implementaci vzniklé z tohoto zadání

1. **LLM není policy engine** — pomáhá se srozumitelným zdůvodněním, nikdy
   nerozhoduje o povinném minimu.
2. **Effective tier nikdy neklesá pod deterministický floor**
   (`max(LLM návrh, policy minimum, ruční hodnota)`; snížení pod minimum je
   `PolicyViolation`, ne tichá korekce).
3. **Legislativní signály vznikají deterministicky** v kódu — LLM je nemůže
   vytvořit ani odstranit.
4. **Žádný hard delete** — ani záznamu, ani historie. Jen soft delete.
5. **Žádný raw prompt/response v auditu** — jen metadata a verze
   (`prompt_version`, `rules_version`), reprodukovatelnost přes verze, ne obsah.
6. **Veškerý LLM provoz jde přes Gateway** — allowlist polí, pseudonymizace,
   schema validace, deanonymizace. Adaptery se importují výhradně tam.
7. **LLM selhání má deterministický fallback** — formulář se nikdy neztrácí,
   povinná pravidla fungují i bez LLM.
8. **Rizikové změny spouštějí re-klasifikaci** (review light).
9. **Jednoduchý server-rendered FastAPI monolit** — žádné mikroservisy, žádný
   frontend framework, žádná vektorová DB.

## Použitý model

- **Generování a implementace aplikace**: Claude Fable 5 (`claude-fable-5`)
  přes Claude Code, po fázích (viz [Způsob práce](#způsob-práce-ai-workflow)).
- **Běh aplikace za provozu** — dva vyměnitelné adaptery za `LlmAdapter`
  abstrakcí (`app/llm/base.py`):
  - `mock` (**default**, `app/llm/mock.py`) — deterministický, offline, bez
    API klíče; stejný vstup dá stejný výstup.
  - `anthropic` (`app/llm/anthropic.py`) — oficiální Anthropic SDK, model
    `ANTHROPIC_MODEL` (default `claude-sonnet-5`).

Přepnutí je čistě konfigurační, žádná změna kódu:

```bash
# .env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-5
```

S `LLM_PROVIDER=anthropic` a chybějícím klíčem selže **první LLM volání**
hned s čitelnou chybou (`RuntimeError: LLM_PROVIDER=anthropic vyžaduje
ANTHROPIC_API_KEY v .env`) — žádné tiché přepnutí zpátky na mock
(`app/llm/gateway.py:get_adapter`). Stejně fungují adaptery `openai` a `gemini`.

## Výběr modelu podle vlastního evalu cena/výkon

Model pro produkční provoz nebyl zvolen dojmem, ale **změřením**: eval harness
(`scripts/eval/`, metodika v [`scripts/eval/README.md`](scripts/eval/README.md))
prohnal 6 modelů tří providerů identickým produkčním tokem (gateway včetně
pseudonymizace a fallbacku) nad zlatým datasetem 24 klasifikačních případů
(validovaným proti policy pravidlům) a 8 duplicitních párů, 2 běhy na případ.
Kvalitu českého zdůvodnění hodnotila cross-judge porota (soudci vždy z cizí
modelové rodiny). Kompletní report: [`docs/eval/2026-08-12_eval-report.md`](docs/eval/2026-08-12_eval-report.md).

| Model | Přesnost exact / acceptable | JSON validita | Zdůvodnění (judge 1–5) | Cena / 1000 klasifikací |
|---|---|---|---|---|
| **gpt-5.4-mini** | **89,6 % / 97,9 %** | 100 % | 4,31 | **$1,38** |
| gemini-3.5-flash | 83,3 % / 91,7 % | 91,7 % | 4,12 | $14,94 † |
| claude-haiku-4-5 | 79,2 % / 87,5 % | 95,8 % | 4,32 | $1,65 |
| gpt-5.4 | 79,2 % / 91,7 % | 100 % | 4,24 | $4,65 |
| claude-sonnet-5 | 75,0 % / 83,3 % | 95,8 % | **4,66** | $4,55 |
| gemini-3.1-pro | nevyhodnoceno — trvalý rate-limit kvóty účtu | | | |

† Gemini 3.5 Flash účtuje reasoning („thinking") tokeny jako výstup — průměrně
1 529 výstupních tokenů na volání proti ~175 u ostatních. Nominálně levný model
je tak v této úloze **10× dražší než gpt-5.4-mini**. Přesně kvůli takovým
překvapením se cena měří na reálné úloze, ne z ceníku.

**Sweetspot: `gpt-5.4-mini`** — nejpřesnější a zároveň nejlevnější model testu
(dominuje ve všech tvrdých metrikách). Slabinou je nižší precision u duplicit
(66,7 % — víc falešných poplachů), což je v tomto UX levná chyba: uživatel
falešného kandidáta jednou kliknutím odmítne. Pokud by prioritou byla kvalita
zdůvodnění do governance dokumentace, prémiovou volbou je `claude-sonnet-5`
(judge 4,66, nejlepší text) za ~3× vyšší cenu. Skutečná cena celého evalu
(755 API volání vč. poroty): **$2,85**.

## Klasifikace této aplikace samotné a proč

Spec (kap. 17) i zadání (`entr_specs.md`) vyžadují, aby si aplikace prošla
vlastním dotazníkem. Odpovědi za Registr sám o sobě:

| Otázka | Odpověď | Proč |
|---|---|---|
| 2. Počet uživatelů | 10–50 | interní governance nástroj, ne masově používaná aplikace |
| 3. Kritičnost | Důležitý | usnadňuje governance přehled, existuje ruční náhrada (tabulka v Excelu) |
| 4. Osobní údaje | Zaměstnanců | kontakty vlastníka/zástupce/správce aplikací jsou interní zaměstnanci |
| 5. Rozhoduje o lidech | Ne | registr eviduje aplikace, nehodnotí ani netřídí konkrétní osoby |
| 6. Výstup mimo firmu | Ne | čistě interní nástroj |
| 7. Citlivost dat | Důvěrná | názvy, popisy a governance rozhodnutí o interních AI nástrojích jsou důvěrné firemní informace |
| 8. Autonomie | Ne | žádná automatická akce, jen návrh, člověk vždy potvrzuje |
| 9. Dopad chyby | Provozní | špatná klasifikace se opraví ručně, nezpůsobí finanční ani klientskou škodu |
| AI komponenta | Anthropic, `EXTERNI_API` | Claude adapter přes veřejné API |

Vyhodnoceno **skutečným kódem** `app.services.policy.compute_minimum` a
`app.services.regulatory.compute_flags` — reprodukovatelné skriptem
[`scripts/self_classify.py`](scripts/self_classify.py)
(`.venv/Scripts/python scripts/self_classify.py`):

```
klasifikace_minimum: STREDNI (Střední)
Aktivovana pravidla:
  - EXTERNAL_PROVIDER_SENSITIVE_DATA: Externí AI provider zpracovává důvěrná
    nebo vysoce citlivá data. -> min STREDNI

Legislativni signaly (compute_flags):
  - GDPR: GDPR — údaje zaměstnanců (reason_code=PERSONAL_DATA_PROCESSING)

effective_tier (bez LLM návrhu, bez ruční změny): STREDNI (Střední)
```

**Odvození:** aplikace sama o sobě nesplňuje žádné z pravidel pro `VELKA`
(nerozhoduje o lidech, není kritická + výstup mimo firmu, není autonomní se
zásadním dopadem) ani `CLIENT_PERSONAL_DATA`/`CRITICAL_PROCESS`/
`HIGHLY_SENSITIVE_DATA`/`AUTONOMOUS_ACTION` (zpracovává údaje zaměstnanců, ne
klientů; je jen důležitá, ne kritická; data jsou důvěrná, ne vysoce citlivá;
nejedná autonomně). Aktivuje se ale **`EXTERNAL_PROVIDER_SENSITIVE_DATA`** —
používá externího AI providera (Anthropic, `EXTERNI_API`) nad důvěrnými daty
— což samo o sobě zvedá minimum na **STŘEDNÍ**. Zároveň se deterministicky
rozsvítí signál **GDPR** (zpracovává osobní údaje zaměstnanců — kontakty
vlastníků aplikací). Výsledná klasifikace registru samotného je tedy
**STŘEDNÍ**, ne MALÁ, přestože jde o „jen" interní evidenční nástroj —
přesně to je smysl pravidla `EXTERNAL_PROVIDER_SENSITIVE_DATA`: externí LLM
providera nelze ignorovat jen proto, že aplikace vypadá nevinně.

## Identita a přístup

OIDC Authorization Code flow přes Keycloak (`app/auth.py`, Authlib). Role
(`user`, `admin`) žijí v Keycloaku a přicházejí v ID tokenu; **vynucují se na
backendu u každého requestu** přes FastAPI dependencies `require_user` /
`require_admin` / `check_owner_or_admin` — ne skrytím tlačítka v UI. V session
se drží jen `{username, email, roles}`, žádné IdP tokeny.

### Výměna Keycloak → Microsoft Entra ID

Žádná změna kódu — jen konfigurace:

1. **App registration** v Entra ID (Azure Portal):
   - Redirect URI: `http://localhost:8000/auth/callback` (typ „Web").
   - App roles `user` a `admin` (Enterprise application → App roles), přiřadit
     uživatelům/skupinám.
   - Vygenerovat client secret.
2. **Změna `.env`**:
   ```bash
   OIDC_ISSUER_URL=https://login.microsoftonline.com/<tenant-id>/v2.0
   OIDC_INTERNAL_URL=https://login.microsoftonline.com/<tenant-id>/v2.0
   OIDC_CLIENT_ID=<Application (client) ID z App registration>
   OIDC_CLIENT_SECRET=<vygenerovaný client secret>
   OIDC_ROLES_CLAIM=roles
   ```
   `OIDC_INTERNAL_URL` je u Keycloaku v Dockeru odlišná od `OIDC_ISSUER_URL`
   jen kvůli oddělení veřejné/backchannel sítě uvnitř `docker-compose`
   (riziko R1, viz `app/auth.py`). Entra ID žádný takový split nemá — je to
   veřejná cloudová služba, obě proměnné se nastaví na stejnou hodnotu.
3. Zbytek (`app/auth.py`) zůstává beze změny — Authlib skládá
   `/protocol/openid-connect/{auth,token,certs,userinfo,logout}` ručně nad
   Keycloakem; u Entra ID by bylo čistší přejít na `server_metadata_url`
   discovery, ale i současný ruční skládaní endpointů (Entra má vlastní tvar
   cest) je řešitelné bez zásahu do zbytku aplikace — jde jen o konstanty.

### Proč MFA neřeší aplikace

MFA je odpovědnost identity providera, ne aplikace. Aplikace o MFA **nesmí ani
vědět** — jakmile OIDC token dorazí, uživatel je ověřený, a to včetně
případného druhého faktoru, který si vynutil Keycloak/Entra podle vlastní
politiky (Conditional Access, autentizační metody). Kdyby aplikace musela
znát nebo implementovat MFA logiku, výměna identity providera by přestala být
„jen změna konfigurace" — což je explicitní podmínka zadání. MFA se zapíná v
politikách Keycloaku (Authentication flows) nebo Entra ID (Conditional
Access), ne v této codebase.

## Práce s daty

### Anonymizace (pseudonymizace s reverzní mapou)

Tok (`app/llm/anonymizer.py`, `app/llm/gateway.py`):

```
Jméno „Jan Novák"        → [OSOBA_1]  ┐
E-mail jan.novak@…       → [EMAIL_1]  ├─→ LLM vidí jen placeholdery →
Telefon +420 601 123 456 → [TEL_1]    ┘   odpověď obsahuje stejné placeholdery
                                            → po schema validaci se dosadí zpět
                                              skutečné hodnoty → teprve to vidí uživatel
```

Reverzní mapa (`Anonymizer._placeholder_to_value`) žije **jen v paměti jedné
instance = jednoho requestu**. Nikam se neukládá, neloguje, `__repr__` mapu
nevypisuje. E-maily a telefony se chytají regexem (mají pevný tvar), jména
case-insensitive shodou proti **známým kontaktům z registru** předaným do
konstruktoru.

**Přiznaný limit:** anonymizér **není obecné NER/DLP řešení**. Nerozpozná
jméno, které není mezi kontakty registru, ani jiné typy citlivých údajů
(rodná čísla, čísla karet, obchodní tajemství) ve volném textu. Chrání jen to,
co má šanci znát — kontakty vlastníka/zástupce/správce.

### Allowlist — minimalizace před pseudonymizací

Každý LLM účel má explicitní seznam polí, co smí odejít (`app/llm/gateway.py`,
`prompts/*.md` hlavičky):

- `classify`: jen odpovědi dotazníku + AI komponenty. **Kontakty se do
  klasifikace neposílají vůbec** — nejsou potřeba, minimalizace má přednost
  před pseudonymizací.
- `duplicates`: název + popis nového záznamu + max 10 lokálně předvybraných
  kandidátů (`app/services/similarity.py`, `difflib`).

### LLM audit — jen metadata, nikdy obsah

`app/llm/audit.py` / tabulka `llm_audit` ukládá `provider`, `model`,
`prompt_version`, `rules_version`, tokeny, latenci, úspěch/`error_code`,
`fallback_used` — **nikdy prompt ani odpověď**, ani pseudonymizovaně.
Reprodukovatelnost je zajištěná verzemi (`PROMPT_VERSION` v `prompts/*.md`,
`RULES_VERSION` v `policy.py`), ne logováním obsahu.

### Retence dat (spec kap. 13)

| Data | Retence |
|---|---|
| Aktivní/vyřazené záznamy (vč. kontaktů) | po dobu evidence; vyřazení = soft delete |
| Immutable historie (`record_history`) | zachována i po vyřazení; produkční retenční politika = governance rozhodnutí mimo MVP |
| LLM audit metadata | 90 dní (`LLM_AUDIT_RETENTION_DAYS`), čistí se při každém startu aplikace |
| Session | do expirace cookie (max 8 h, `SESSION_MAX_AGE`) nebo odhlášení |
| Reverzní pseudonymizační mapa | jen RAM jednoho requestu |
| Obsah promptů/odpovědí LLM | neukládá se nikdy |
| Zvuk/přepisy | aplikace žádné nezpracovává (mimo scope varianty A) |

Podle této tabulky se skutečně programuje — retence LLM auditu se maže v
`app.main.lifespan` při každém startu (`purge_older_than`), soft delete a
immutable historie jsou vynucené v modelech i routách (žádná cesta v
`app/` nevolá `session.delete()` nad `Application`/`AiComponent`/
`RecordHistory` — jediné mazání v repu je bulk `delete(LlmAudit)` v purge).

### Syntetická data

Seed (`app/seed_data.py`, spuštěný jen nad prázdnou DB) obsahuje 8 fiktivních
aplikací s vymyšlenými jmény a kontakty na doméně `@example.com` — žádná
reálná osobní data.

## Bezpečnost

- **RBAC na backendu** — `require_user`, `require_admin`,
  `check_owner_or_admin` (`app/auth.py`); UI akce podmíněné rolí jsou jen
  kosmetika nad touto kontrolou.
- **CSRF** — `app/security.py`: token v session + hidden form pole u každého
  state-changing POSTu, `secrets.compare_digest`, jinak 403.
- **Security headers** — `SecurityHeadersMiddleware`:
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Content-Security-Policy`.
- **Optimistic locking** — skryté pole `version` ve formuláři, porovnané s DB
  hodnotou těsně před zápisem; neshoda → `VersionConflict` (409), žádný tichý
  přepis (`app/routes/edit.py`, `app/routes/admin.py`).
- **Soft delete + immutable historie** — fyzický DELETE nad `Application`,
  `AiComponent` ani `RecordHistory` v kódu neexistuje.
- **Secrets jen v `.env`** — `.env` je v `.gitignore`, do repa jde jen
  `.env.example` s placeholdery. Docker image nedostane žádný secret zabalený
  (`.dockerignore` vylučuje `.env`); běhové secrety jdou přes
  `environment:` v `docker-compose.yaml` s bezpečnými dev defaulty
  (`${VAR:-default}`).

## Disclaimer

**MALÁ / STŘEDNÍ / VELKÁ je interní governance tier, ne právní klasifikace.**
Legislativní signály `GDPR` / `AI-ACT` / `DORA` jsou deterministické podněty
k dalšímu (lidskému, právnímu) review — nejsou automatickým právním závěrem o
tom, že se daná legislativa na aplikaci vztahuje nebo nevztahuje.

## Způsob práce (AI workflow)

1. **Brainstorming** (skill `brainstorming`) — dialog nad `Master_prompt.md`:
   kontext → doptávání na otevřené otázky → návrh přístupů → schválený design.
2. **Spec** — [`docs/specs/2026-08-11-ai-registr-design.md`](docs/specs/2026-08-11-ai-registr-design.md),
   jediný source of truth pro implementaci.
3. **Externí oponentura druhým modelem** — [`docs/reviews/2026-08-11-oponentura-gpt.md`](docs/reviews/2026-08-11-oponentura-gpt.md)
   (GPT 5.6): co bylo převzato, co zamítnuto a proč (např. periodická review s
   termíny nebo request correlation ID — hodnoceno jako byrokratická vrstva
   bez nosné story pro netechnického PO).
4. **Implementační plán** — [`docs/plans/2026-08-11-implementacni-plan.md`](docs/plans/2026-08-11-implementacni-plan.md),
   17 fází v pořadí „deterministická logika dřív než infrastruktura a UI".
5. **Implementace po fázích** orchestrátorem s podagenty (Haiku pro
   jednoduché/masové úpravy, Sonnet pro hlavní vývoj, Opus pro architektonická
   rozhodnutí a review) — každá fáze = vlastní commit, vlastní ověření.
6. **Review každé fáze** + **testy** — deterministická jádra (policy,
   regulatory, anonymizer, fallback) mají unit testy hned ve fázi vzniku, ne
   až na konci.

## Co bych dodělal / vědomý dluh

Vyjmenováno explicitně dle spec kap. 2 a `entr_specs.md`:

| Co | Proč odloženo |
|---|---|
| Schvalovací / risk-acceptance workflow (výjimka pod policy minimum) | Samostatný governance proces mimo MVP — žádný skrytý admin override; radši žádný než špatně navržený. |
| Fulltext, embeddings, vektorová DB | YAGNI pro registr v řádu desítek záznamů — `difflib` na lokální předvýběr stačí. |
| Postgres + Alembic migrace | SQLite + `create_all` stačí pro MVP a demo bez ručních kroků; produkční nasazení by potřebovalo migrace a víc-uživatelskou DB. |
| CI/CD | Mimo 8h časový rámec zadání; testy se zatím spouští ručně (`pytest`). |
| Obecné NER/DLP nad volným textem | Anonymizér cílí jen na regex (e-mail/telefon) + známé kontakty z registru — obecné rozpoznávání libovolných jmen/tajemství je jiná třída nástroje (DLP produkt), limit je vědomě přiznaný v README. |
| Scheduler a periodická review s termíny (`review_due_at`) | `review_required` badge po rizikové změně stačí pro MVP; termínovaná review by potřebovala background job a e-mailovou infrastrukturu. |
| Rate limiting | Interní nástroj za firemním SSO, riziko zneužití v MVP nízké. |
| Request correlation ID | Usnadnilo by trasování napříč logy, ale není nutné pro funkčnost ani pro guardraily, které zadání hodnotí. |
| Verze modelu a data-egress flag u AI komponent | Zjednodušený model AI komponenty (provider + typ hostingu stačí pro policy pravidla); přesná verze modelu je alespoň dohledatelná v `llm_audit.model` u reálných volání. |
| Duplicitní kontrola při editaci | Dělá se jen při založení záznamu; při editaci by přidala další UX rozhodnutí (kdy spouštět, jak nerušit rozpracovanou editaci) nad rámec MVP. |
| Rotace session ID po přihlášení | Session se čistí (`request.session.clear()`) při každém loginu/logoutu a má omezenou životnost (8 h) — plná rotace ID by vyžadovala vlastní session backend místo Starlette `SessionMiddleware`. |
| Integration/E2E testy | Zúžené testovací minimum (spec kap. 16) cílí na deterministické guardraily (policy, signály, anonymizér, fallback, RBAC, CSRF, locking) — zadání testovací pokrytí explicitně nehodnotí; kritické scénáře (login, wizard, RBAC 403, 409 konflikt) byly ověřené ručně a jsou popsané v plánu. |

## Testy

176 testů (`.venv/Scripts/python -m pytest tests/ -v`), pokrývají:

- **`test_policy.py`** — každé eskalační pravidlo zvlášť, kombinace pravidel,
  LLM `MALA` + floor `VELKA` → `VELKA`, ruční zvýšení OK, snížení pod minimum
  → `PolicyViolation`.
- **`test_regulatory.py`** — GDPR/AI-ACT/DORA podmínky, whitelist zkratek.
- **`test_anonymizer.py`** — round-trip jméno/e-mail/telefon, různé osoby →
  různé placeholdery, mapa není persistentní napříč instancemi.
- **`test_fallback.py`** — nevalidní JSON → fallback, timeout → 1 retry →
  fallback, 5xx pak úspěch → bez fallbacku, audit bez obsahu promptu.
- **`test_gateway_allowlist.py`** — `classify` payload neobsahuje kontaktní
  pole, `duplicates` max 10 kandidátů.
- **`test_similarity.py`**, **`test_history.py`** — lokální předvýběr
  duplicit, append-only historie přežívající soft delete.
- **`test_auth.py`**, **`test_admin.py`**, **`test_edit.py`**,
  **`test_registry_routes.py`**, **`test_wizard.py`** — RBAC 403 i při
  přímém POSTu, optimistic locking 409, vyřazení s povinným důvodem, review
  light.
- **`test_health_csrf.py`** — `/health` bez configu, CSRF 403.
- **`test_anthropic_adapter.py`** — mapování chyb SDK, fail-fast bez klíče.

Spuštění:

```bash
.venv\Scripts\python -m pytest tests/ -v      # Windows
# .venv/bin/python -m pytest tests/ -v         # macOS/Linux
```
