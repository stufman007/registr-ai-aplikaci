# Oponentura specu (GPT 5.6) — vstupní review, NENÍ source of truth

> **Status: externí oponentura, zapracováno výběrově.** Source of truth je
> [docs/specs/2026-08-11-ai-registr-design.md](../specs/2026-08-11-ai-registr-design.md).
> Oponent dostal zadání „současný spec je 7/10, navrhni 11/10" — návrhy jsou proto
> záměrně maximalistické a byly tříděny podle hodnoty pro 8h MVP a obhajitelnosti u pohovoru.
>
> **Převzato:** LLM failure contract (timeout/retry/fallback) · trojice polí AI návrh /
> policy minimum / effective tier · deterministické legislativní signály místo
> LLM-generovaných · soft delete + immutable historie · CSRF, session flagy, max délky,
> optimistic locking · allowlist polí do LLM (kontakty se do classify neposílají) ·
> verzování promptů a pravidel v auditu · lokální předvýběr duplicit + override s důvodem ·
> rozšíření dotazníku o citlivost dat, autonomii a dopad chyby · strukturované AI
> komponenty (zjednodušené: bez verze modelu a egress flagu) · review light (re-klasifikace
> při rizikové změně + review_required badge) · zúžené unit testy guardrailů · disclaimer
> „tier ≠ právní klasifikace".
>
> **Zamítnuto (→ vědomý dluh v README):** samostatné verze anonymizéru/gateway/schématu ·
> request correlation ID · periodická review s termíny due/overdue · rotace session ·
> duplicitní kontrola při editaci · integration/E2E testy · plných 30 akceptačních kritérií.
> Důvod: byrokratická vrstva bez nosné story pro netechnického PO, který bude kód
> obhajovat; zadání hodnotí srozumitelnost a čistotu, ne počet funkcí.

---

Původní text oponentury (v2 návrh specu):

> Cíl revize v2: zachovat malý, realizovatelný MVP scope, ale odstranit slabá místa v auditu,
> governance, bezpečnosti a failure-mode chování LLM.

## 0. Design principles

1. **LLM není zdroj pravdy ani policy engine.** LLM pomáhá s interpretací, srozumitelným
   zdůvodněním a hledáním podobností. Tvrdá governance pravidla, autorizace a audit jsou
   deterministické a verzované.
2. **`MALA / STREDNI / VELKA` je interní governance tier, ne právní klasifikace.**
   Legislativní položky `GDPR`, `AI-ACT`, `DORA` jsou signály k dalšímu review, nikoli
   automatický právní závěr.
3. **Audit musí přežít životní cyklus záznamu.** Záznamy se v aplikaci fyzicky nemažou;
   vyřazení z evidence je soft delete a auditní stopa zůstává.
4. **Graceful degradation.** Výpadek nebo nekorektní odpověď LLM nesmí způsobit ztrátu
   formuláře ani obejití povinných pravidel. Aplikace musí umět bezpečný deterministický fallback.
5. **Data minimization před LLM.** Do externího modelu tečou jen allowlistovaná pole po
   pseudonymizaci/sanitizaci. Anonymizér není vydáván za obecné DLP řešení.
6. **Reprodukovatelnost přes verze, ne přes logování obsahu.** Prompty a odpovědi se nelogují;
   ukládají se verze promptu, pravidel, anonymizéru, schématu a modelu.
7. **YAGNI.** Žádné mikroservisy, React, Kubernetes, vector DB ani komplexní workflow jen proto,
   že jde o AI aplikaci. Preferovat malý monolit a explicitní trade-offy.

## 1. Kontext a cíl

Fintech společnost interně vyvíjí AI nástroje a potřebuje nad nimi udržet přehled ze dvou důvodů:

1. **Governance a legislativa** — u každého nástroje musí být jasné, kdo ho vlastní,
   jaký má interní governance tier, zda vykazuje signály relevantní pro GDPR / EU AI Act / DORA,
   jaké AI komponenty používá a v jakém je stavu životního cyklu.
2. **Interní pořádek** — zabránit vzniku duplicitních nástrojů: před založením nového
   záznamu aplikace upozorní na podobné existující.

Výsledek: malá webová aplikace, která se dá reálně provozovat. Uživatel se přihlásí
firemním účtem (OIDC), vidí registr, založí záznam přes klasifikační dotazník a LLM
navrhne governance tier **MALÁ / STŘEDNÍ / VELKÁ** se zdůvodněním. Deterministická policy
pravidla následně stanoví minimální povolený tier a legislativní signály. Člověk návrh
potvrdí nebo zvýší; nikdy jej běžným přepisem nesníží pod policy minimum.

## 2. Rozsah

### V rozsahu (MVP)

- Přihlášení přes OIDC (Keycloak v `docker-compose`), role `user` a `admin` vynucené na backendu.
- CRUD registru s **vyřazením/obnovením místo hard delete**.
- Karta aplikace + 1:N seznam použitých AI komponent.
- Klasifikace přes pevný dotazník, LLM návrh a deterministická eskalační minima.
- Deterministické legislativní/governance signály `GDPR`, `AI-ACT`, `DORA` s reason codes.
- Kontrola duplicit: lokální předvýběr kandidátů + LLM reranking/vysvětlení.
- Immutable audit historie změn (kdo, kdy, akce, co, stará → nová hodnota).
- Review lifecycle: `last_reviewed_at`, `review_due_at`, `review_required`.
- LLM Gateway: allowlist vstupů → sanitizace/pseudonymizace → LLM adapter → schema validation → deanonymizace.
- LLM abstrakční vrstva: adapter `mock` (default, bez klíče) a `anthropic` (Claude).
- Explicitní LLM timeout/retry/fallback chování.
- Audit log LLM volání: pouze metadata, verze a outcome, nikdy prompt/response; retence 90 dní.
- Backend RBAC, CSRF ochrana, bezpečné session cookie, input-size limity.
- Optimistic locking proti tichému přepsání souběžné změny.
- Docker Compose, health endpoint, syntetický seed, README s Master Promptem, složka `prompts/`.
- Unit/integration testy kritických guardrailů.

### Mimo rozsah (vědomý dluh, popsat v README)

- Vícekrokové schvalovací workflow klasifikace / risk acceptance workflow.
- Fulltextové vyhledávání, embeddings a vektorová DB.
- Postgres (MVP používá SQLite; výměna popsána v README).
- CI/CD pipeline.
- Alembic/DB migrace; MVP může na čistém prostředí použít `create_all`.
- Obecné NER/DLP rozpoznávání libovolných jmen, tajemství nebo citlivých dat ve volném textu.
- Automatický scheduler/notifikace review; MVP pouze zobrazuje stav `review due/overdue`.
- Rate limiting (interní nástroj za SSO; vědomě přiznat).
- Komplexní právní expert systém. Legislativní položky jsou governance signály, ne právní stanovisko.

## 3. Uživatelé a role

| Role | Práva |
|---|---|
| `user` | vidí aktivní registr a historii; zakládá záznamy; edituje záznamy, kde je vlastníkem nebo autorem; může potvrdit nebo zvýšit governance tier; nemůže vyřazovat/obnovovat záznamy |
| `admin` | navíc edituje libovolný záznam, vyřazuje a obnovuje záznamy, může upravit LLM návrh; **nemůže uložit effective tier pod deterministické policy minimum** |

„Vlastník nebo autor“ = e-mail přihlášeného se shoduje s `vlastnik_email`, nebo jeho
OIDC subject/username se shoduje s `created_by`.

- Role žijí v Keycloaku a přicházejí v OIDC tokenu.
- **Autorizace se vynucuje na backendu u každého requestu**, ne skrytím tlačítka.
- UI nezobrazuje akce bez oprávnění, ale je to jen UX nad backendovou kontrolou.
- MFA aplikace neřeší; je odpovědností identity providera.
- Session má omezenou životnost (např. max 8 hodin), aby změna oprávnění nezůstala platná neomezeně dlouho.
- Pokud by firma někdy potřebovala udělit výjimku pod policy minimum, má to být samostatný
  explicitní risk-acceptance proces, nikoli skrytý admin override. Tento workflow není v MVP.

## 4. Datový model

### 4.1 Aplikace (`applications`)

Pole `klasifikace` je zachované kvůli terminologii zadání, ale jeho význam je výslovně
**interní governance tier**.

| Pole | Typ | Poznámka |
|---|---|---|
| `id` | UUID | stabilní identifikátor |
| `nazev` | text, povinné | max délka definovaná validací |
| `popis` | text, povinné | účel aplikace; vstup pro klasifikaci i duplicity |
| `vlastnik_jmeno`, `vlastnik_email` | text | osobní údaj → pseudonymizace před LLM |
| `zastupce_jmeno`, `zastupce_email` | text | osobní údaj → pseudonymizace před LLM |
| `spravce_jmeno`, `spravce_email` | text | technický správce; osobní údaj → pseudonymizace před LLM |
| `klasifikace_llm` | enum `MALA` / `STREDNI` / `VELKA` | původní návrh LLM nebo fallbacku |
| `klasifikace_minimum` | enum | deterministicky spočtený policy floor |
| `klasifikace` | enum | effective tier uložený do registru; musí být `>= klasifikace_minimum` |
| `klasifikace_zduvodneni` | text | srozumitelné české vysvětlení; nesmí měnit hard rules |
| `klasifikace_priznaky` | JSON pole | deterministické regulatory/governance signals, viz 5.5 |
| `klasifikace_potvrdil` | text | kdo effective tier potvrdil/upravil |
| `klasifikace_poznamka` | text nullable | povinná při ruční změně proti LLM návrhu |
| `dotaznik_odpovedi` | JSON | snapshot odpovědí použitých pro poslední klasifikaci |
| `stav` | enum `VYVOJ` / `PILOT` / `PROVOZ` / `UTLUMENO` | lifecycle aplikace |
| `last_reviewed_at` | timestamp nullable | poslední potvrzené review |
| `review_due_at` | timestamp nullable | termín dalšího review |
| `review_required` | bool | změna relevantních dat vyžaduje nové review/klasifikaci |
| `version` | integer | optimistic locking; při každém update +1 |
| `created_by`, `created_at` | | OIDC subject/username + timestamp |
| `updated_by`, `updated_at` | | poslední změna |
| `deleted_at`, `deleted_by` | nullable | soft delete / vyřazení z aktivní evidence |
| `delete_reason` | text nullable | při vyřazení povinné |

**Fyzický DELETE z běžné aplikace není dostupný.** Aktivní registry standardně filtrují
`deleted_at IS NULL`. Admin může vyřazené záznamy zobrazit a obnovit.

### 4.2 AI komponenta (`ai_components`)

Jedna aplikace může používat více modelů/AI služeb. Textové pole typu
„Claude Sonnet — sumarizace smluv“ není dostatečný inventory model.

| Pole | Poznámka |
|---|---|
| `id` | UUID |
| `application_id` | FK na aplikaci |
| `provider` | např. `Anthropic`, `OpenAI`, `Azure`, `internal`, `other` |
| `model_name` | např. `Claude Sonnet` |
| `model_version` | pokud je známá; jinak `unknown` |
| `purpose` | co komponenta v aplikaci dělá |
| `hosting_type` | `external_api` / `company_cloud` / `on_prem` / `unknown` |
| `sends_company_data_externally` | bool/unknown |
| `created_at`, `updated_at` | |

Změna provideru/modelu/purpose/hosting typu nastaví parent aplikaci `review_required=true`.

### 4.3 Immutable historie (`record_history`)

Auditní historie je append-only z pohledu aplikačního CRUDu. Jeden řádek = jedna auditní událost
nebo jedna změna pole. Nikdy se nemaže spolu s vyřazením záznamu.

| Pole | Poznámka |
|---|---|
| `id` | UUID |
| `record_id` | UUID aplikace |
| `actor` | OIDC subject/username |
| `kdy` | timestamp |
| `action` | `CREATE` / `UPDATE` / `CLASSIFY` / `REVIEW` / `RETIRE` / `RESTORE` / `DUPLICATE_OVERRIDE` |
| `pole` | změněné pole nebo `null` pro událost bez konkrétního pole |
| `stara_hodnota`, `nova_hodnota` | serializovaná reprezentace; viz privacy pravidla níže |
| `reason` | volitelná/povinná podle akce |
| `request_id` | korelační ID requestu |

**Privacy pravidla historie:**

- Do historie neserializovat session, tokeny, secrets ani raw LLM obsah.
- U kontaktních údajů audit eviduje změnu hodnoty; protože jde o interní audit, lze hodnotu uložit,
  ale musí být pod stejnými access controls jako registr a její životní cyklus je explicitně popsán v README.
- Pokud by se požadavky na výmaz osobních údajů zpřísnily, je možné auditní hodnoty změnit na redacted/hash reprezentaci;
  pro MVP je důležitější transparentně popsat trade-off než předstírat anonymitu.

### 4.4 LLM audit (`llm_audit`)

| Pole | Poznámka |
|---|---|
| `id`, `kdy` | UUID + timestamp |
| `operation_id`, `request_id` | korelace jednoho logického AI kroku/requestu |
| `record_id` | nullable; u nového záznamu může být dočasně null |
| `ucel` | `classify` / `duplicates` |
| `provider`, `model`, `model_version` | přesná použitá konfigurace |
| `prompt_version` | např. git hash / explicitní `v1` z `prompts/` |
| `rules_version` | verze policy pravidel |
| `anonymizer_version` | verze sanitizace/pseudonymizace |
| `schema_version` | verze očekávaného structured output |
| `tokens_in`, `tokens_out` | u mocku 0 |
| `latence_ms` | |
| `uspech` | bool |
| `error_code` | nullable; např. `TIMEOUT`, `INVALID_JSON`, `PROVIDER_5XX` |
| `fallback_used` | bool |
| `classification_llm` | nullable |
| `classification_minimum` | nullable |
| `classification_effective` | nullable |

**Nikdy se neloguje prompt ani odpověď**, ani v pseudonymizované podobě. Retence 90 dní;
starší řádky se smažou při startu aplikace. Reprodukovatelnost je zajištěna verzemi, ne obsahem.

## 5. AI funkce 1: klasifikace

### 5.1 Klasifikační dotazník

Dotazník se vyplňuje při založení záznamu a znovu při relevantním review. Má být krátký,
ale musí pokrýt signály, ze kterých lze odvodit governance tier.

1. **K čemu aplikace slouží a jaký proces podporuje?** — volný text.
2. **Kolik lidí ji používá / bude používat?** — `do 10` / `10–50` / `přes 50`.
3. **Jak kritický proces podporuje?**
   - `Pomocný` — výpadek nikoho zásadně nezastaví.
   - `Důležitý` — tým se citelně zpomalí, existuje ruční náhrada.
   - `Kritický` — výpadek může zastavit část firmy nebo ohrozit povinnost vůči klientům/regulátorovi.
4. **Zpracovává osobní údaje?** — `Ne` / `Zaměstnanců` / `Klientů`.
5. **Rozhoduje nebo doporučuje o lidech?**
   - `Ne`.
   - `Doporučuje, člověk rozhoduje`.
   - `Rozhoduje automatizovaně`.
6. **Je výstup viditelný mimo firmu?** — `Ne` / `Nepřímo` / `Ano přímo`.
7. **Jak citlivá data systém zpracovává?** — `Veřejná` / `Interní` / `Důvěrná` / `Vysoce citlivá`.
8. **Může výstup AI vyvolat akci bez kontroly člověkem?** — `Ne` / `Člověk schvaluje` / `Ano automaticky`.
9. **Posílají se firemní data externímu AI providerovi?** — `Ne` / `Ano` / `Nevím`.
10. **Jaký je potenciální dopad chybného AI výstupu?** — `Zanedbatelný` / `Provozní` / `Finanční nebo klientský` / `Zásadní`.

Použité AI modely nejsou jen volný text v dotazníku; uživatel je zadává jako jednu či více
strukturovaných `AIComponent` položek (kap. 4.2).

### 5.2 Vyhodnocení: LLM navrhuje, pravidla rozhodují minimum

Tok:

```text
dotazník + vybraná metadata
→ LLM Gateway (kap. 7)
→ LLM návrh governance tier + zdůvodnění
→ Pydantic/schema validace
→ deterministická policy pravidla
→ deterministické regulatory signals
→ effective tier
→ lidské potvrzení
```

LLM structured output:

```json
{
  "klasifikace": "MALA|STREDNI|VELKA",
  "zduvodneni": "české vysvětlení odkazující na konkrétní odpovědi",
  "nuance_notes": ["volitelné poznámky bez právního závěru"]
}
```

LLM **nevrací autoritativní legislativní flags**. Ty vznikají deterministicky v kódu.

### 5.3 Deterministická eskalační minima

Policy pravidla mají explicitní `RULES_VERSION`, např. `2026-08-v1`.

| Podmínka | Minimální tier | Reason code |
|---|---|---|
| Automatizované rozhodování o lidech (ot. 5) | `VELKA` | `AUTO_DECISION_PERSON` |
| Kritický proces **a** výstup mimo firmu | `VELKA` | `CRITICAL_EXTERNAL_OUTPUT` |
| Automatická akce bez člověka **a** dopad `Zásadní` | `VELKA` | `AUTONOMOUS_HIGH_IMPACT` |
| Osobní údaje klientů | `STREDNI` | `CLIENT_PERSONAL_DATA` |
| Kritický proces | `STREDNI` | `CRITICAL_PROCESS` |
| Vysoce citlivá data | `STREDNI` | `HIGHLY_SENSITIVE_DATA` |
| Automatická akce bez člověka | `STREDNI` | `AUTONOMOUS_ACTION` |
| Externí AI provider + důvěrná/vysoce citlivá data | `STREDNI` | `EXTERNAL_PROVIDER_SENSITIVE_DATA` |

Počet uživatelů je vstup pro LLM a kontext, ale **není sám o sobě hard policy floor**.

Výpočet:

```text
classification_minimum = max(tier všech aktivovaných pravidel, MALA)
classification_effective = max(classification_llm, classification_minimum, manual_raise)
```

Pokud LLM navrhne méně než policy minimum, aplikace tier zvýší a do vysvětlení přidá konkrétní
reason code/pravidlo. LLM nikdy nemůže hard rule vypnout ani obejít.

### 5.4 Lidská kontrola

UI vedle sebe zobrazuje:

- **AI návrh**,
- **policy minimum** + pravidla, která jej způsobila,
- **effective tier**,
- **regulatory signals**,
- AI zdůvodnění.

Uživatel může:

- potvrdit effective tier,
- governance tier zvýšit,
- upravit poznámku/vysvětlení.

Admin může upravit LLM návrh nebo effective tier **jen v rozsahu `>= policy minimum`**.
Každá ruční změna proti LLM návrhu vyžaduje poznámku a zapisuje se do historie.

### 5.5 Deterministické legislativní/governance signály

Signály nejsou právním závěrem. Každý je strukturovaný objekt:

```json
{
  "zkratka": "AI-ACT",
  "titulek": "Rozhodování nebo doporučování o osobách",
  "detail": "Dotazník uvádí automatizované rozhodování o osobách; vyžaduje governance/legal review.",
  "reason_code": "AUTO_DECISION_PERSON",
  "source": "deterministic_rule"
}
```

Povolené zkratky drží whitelist v kódu: `GDPR`, `AI-ACT`, `DORA`.

Minimální pravidla:

- `GDPR` — pokud ot. 4 není `Ne`.
- `AI-ACT` — pokud ot. 5 není `Ne`; automatizované rozhodování má výraznější text/detail.
- `DORA` — governance signal k review při kombinaci kritického procesu a externí AI služby/provideru.
  Jde o konzervativní interní signál, nikoli tvrzení, že konkrétní use case právně spadá pod DORA.

LLM může dodat `nuance_notes`, ale nemůže deterministický signal odstranit ani vytvořit novou zkratku.

## 6. AI funkce 2: kontrola duplicit

Kontrola proběhne před uložením nového záznamu a také při významné změně `nazev` nebo `popis`.

Tok:

1. Lokální algoritmus nad názvem + popisem vybere např. **top 10 kandidátů** z aktivního registru
   (jednoduchá textová podobnost, např. RapidFuzz; žádná vector DB).
2. Pouze nová aplikace + těchto max 10 kandidátů projde LLM Gateway.
3. LLM vrátí max 3 kandidáty:

```json
[
  {"record_id": "...", "duvod_podobnosti": "..."}
]
```

4. Pokud jsou kandidáti, UI nabídne:
   - **„Zrušit — aplikace už existuje“**,
   - **„Pokračovat — je to jiná aplikace“** + povinný krátký důvod.
5. Pokračování navzdory kandidátům zapíše `DUPLICATE_OVERRIDE` do immutable historie.

LLM nedostává celý registr, pouze předvybrané kandidáty. Je to levnější, omezuje data egress a
škáluje lépe, přitom stále bez nové infrastruktury.

### Fallback duplicit

Pokud LLM není dostupné, aplikace zobrazí lokální kandidáty nad zvoleným thresholdem a označí je:

> „AI kontrola podobnosti není momentálně dostupná; zobrazeny jsou kandidáti z lokální kontroly.“

Uživatel může pokračovat se stejným povinným důvodem. Formulář se neztratí.

## 7. LLM Gateway a pseudonymizace

**Jediná cesta do LLM je přes `LLMGateway`.** Anonymizér je jeho součást, ne jediná ochrana.

Tok:

```text
allowlist polí
→ canonicalizace + input-size limit
→ sanitizace známých secrets/vzorů
→ pseudonymizace osobních údajů
→ prompt assembly
→ LLM adapter
→ schema validation
→ deanonymizace povolených placeholderů
→ výsledek aplikaci
```

### 7.1 Allowlist a minimalizace

Každý AI use case explicitně určí, která pole smí odejít:

- `classify`: jen odpovědi dotazníku a nezbytné neidentifikační metadata.
- `duplicates`: název + popis nového záznamu a max 10 kandidátů.

Kontaktní pole se neposílají, pokud nejsou pro danou AI funkci potřeba.

### 7.2 Pseudonymizace s reverzní mapou

Příklad:

```text
Jan Novák           → [OSOBA_1]
jan.novak@firma.cz  → [EMAIL_1]
+420 601 123 456    → [TEL_1]
```

- Hodnoty ze strukturovaných kontaktních polí známe přesně a nahrazují se spolehlivě.
- Ve volném textu se e-maily/telefony chytají regexem a známá jména porovnáním proti známým kontaktům.
- Stejná hodnota = stejný placeholder v rámci jednoho requestu.
- Reverzní mapa existuje jen v paměti requestu; neukládá se ani neloguje.
- LLM pracuje pouze s pseudonymy; po validaci odpovědi se známé placeholdery deanonymizují.

### 7.3 Sanitizace a limity

Před LLM se navíc:

- omezí maximální délka volných textů,
- zachytí zjevné token/secret patterns, pokud je lze bezpečně detekovat jednoduchým vzorem,
- vstupní text je v promptu jednoznačně označen jako **data, nikoli instrukce**,
- structured output se vždy validuje Pydantic schématem.

Důležité omezení:

> Pseudonymizátor/sanitizér **není obecné DLP ani NER řešení**. Nezaručuje odstranění libovolných
> obchodních tajemství, jmen nebo citlivých údajů z volného textu. Tento limit musí být přiznaný v README.

### 7.4 Versioning

Gateway má explicitní `ANONYMIZER_VERSION`/`GATEWAY_VERSION`. Prompty mají verzi přímo v souboru
nebo metadatech. Tyto verze se ukládají do `llm_audit`.

## 8. LLM failure contract

LLM je pomocná závislost, ne single point of failure pro governance guardraily.

### 8.1 Technická pravidla

- Timeout na provider call: **10 s** (konfigurovatelný env).
- Max **1 retry** pouze pro transportní chybu, rate-limit nebo 5xx; žádný nekonečný retry loop.
- Odpověď musí projít Pydantic/schema validací.
- Nekorektní JSON/schema po retry → `INVALID_RESPONSE`, použít fallback.
- Chyba se zapíše pouze jako metadata/error code do `llm_audit`; nikdy raw response.

### 8.2 Classification fallback

Pokud LLM selže:

```text
classification_llm = MALA (fallback baseline)
classification_minimum = deterministická pravidla
classification_effective = max(MALA, classification_minimum)
```

Uživateli zobrazit např.:

> „AI zdůvodnění není momentálně dostupné. Governance tier byl určen povinnými pravidly.“

Uživatel může tier zvýšit a dokončit uložení. Povinné rules/signals fungují bez LLM.

### 8.3 Duplicate fallback

Použije se lokální podobnost z kap. 6. Uživatel neztratí data a může pokračovat s explicitním důvodem.

## 9. Review lifecycle

Registr nesmí být pravdivý jen v okamžiku vytvoření záznamu.

### 9.1 Periodické review

Pro `PROVOZ` nastav po potvrzeném review například:

```text
last_reviewed_at = now
review_due_at = now + 12 měsíců
review_required = false
```

MVP nemá scheduler ani e-mailové notifikace. UI pouze ukazuje:

- `Review OK`,
- `Review due soon`,
- `Review overdue`,
- `Review required after change`.

### 9.2 Změny vyžadující nové review

Minimálně tyto změny nastaví `review_required=true`:

- osobní/citlivá data,
- rozhodování o lidech,
- autonomie,
- kritičnost procesu,
- externí výstup,
- externí provider/data egress,
- dopad chyby,
- provider/model/hosting/purpose AI komponenty.

Při editaci relevantních odpovědí aplikace před finálním uložením znovu spustí klasifikaci.

## 10. Architektura a technologie

```text
[prohlížeč]
    ↓ HTML/form POST
[FastAPI monolit]
    ├── auth / RBAC / CSRF / session
    ├── registry service
    ├── policy + regulatory rules
    ├── duplicate similarity
    ├── LLM Gateway
    │     └── LLMClient → mock | anthropic
    ├── SQLite volume
    └── OIDC → Keycloak
```

- **Python + FastAPI + Jinja2**, server-rendered HTML; minimální vanilla JS jen pro UX.
- **SQLite** pro MVP; SQLAlchemy ORM.
- `create_all` při čistém startu je akceptovaný vědomý dluh; produkční evoluce = Alembic + Postgres.
- Lokální duplicate prefilter může použít lehkou knihovnu typu `RapidFuzz`.
- Struktura repa:

```text
app/
  main.py
  config.py
  auth.py
  security.py          # CSRF/session helpers, security headers
  models.py
  db.py
  schemas.py
  routes/
    registry.py
    classification.py
    admin.py
  services/
    policy.py           # deterministic tier floor + RULES_VERSION
    regulatory.py       # deterministic GDPR/AI-ACT/DORA signals
    similarity.py       # local duplicate candidate selection
    review.py           # review_required / due logic
    history.py          # immutable audit events
  llm/
    base.py
    mock.py
    anthropic.py
    gateway.py          # allowlist -> sanitize -> pseudonymize -> validate -> deanonymize
    anonymizer.py
    audit.py
  templates/
  static/
keycloak/realm-export.json
prompts/
  classification.md    # obsahuje PROMPT_VERSION
  duplicates.md
  README.md             # pravidla verzování promptů

docs/specs/
  2026-08-11-ai-registr-design-v2.md
Dockerfile
docker-compose.yaml
.env.example
README.md
seed.py
requirements.txt
```

## 11. Identita, web security a souběh

### 11.1 OIDC

- Keycloak jako služba v Docker Compose, `--import-realm`, dvě testovací identity (`user`, `admin`).
- Standardní OIDC Authorization Code flow přes Authlib.
- Konfigurace z env: `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_ROLES_CLAIM`.
- Výměna za Microsoft Entra ID má být konfigurace, ne rewrite auth vrstvy.
- MFA je odpovědností IdP.

### 11.2 Session/cookie

- Session cookie: `HttpOnly`, `Secure` mimo explicitní lokální dev režim, vhodné `SameSite`.
- Secret pouze z env.
- Do cookie/session neukládat access/refresh token, pokud to flow nepotřebuje; držet minimální identitu a role.
- Po loginu rotovat session identifikátor/state dle možností použité session implementace.
- Omezená max session age.

### 11.3 CSRF

Každý state-changing form POST musí mít CSRF ochranu. Server odmítne chybějící/neplatný token.
Backend RBAC zůstává nezávislá druhá vrstva.

### 11.4 Input validation

- Pydantic/form validace povinných polí a enumů.
- Max délky pro `nazev`, `popis`, poznámky a další volné texty.
- E-mailová pole validovat formátem.
- Žádná raw SQL concatenation.

### 11.5 Optimistic locking

Edit formulář nese skryté `version`. Update smí proběhnout pouze pokud se DB `version` shoduje.
Pokud mezitím jiný uživatel záznam změnil:

- vrátit conflict (např. HTTP 409),
- zobrazit uživateli, že data byla mezitím upravena,
- **nikdy tiše nepřepsat** novější hodnoty.

## 12. Provoz

- `docker compose up` → Keycloak health → app start.
- App při startu: `create_all`, seed syntetických dat pokud DB prázdná, purge `llm_audit` > 90 dní.
- `/health` bez autentizace: stav aplikace + DB; nemá vypisovat secrets ani citlivé config hodnoty.
- Logy: start, chyby, login/logout, request correlation ID, LLM audit metadata.
- Secrets pouze `.env` lokálně; `.env.example` úplný a bez reálných hodnot.
- `.gitignore`: `.env`, `*.db`, lokální artifacts.
- Dependency verze pinované.
- Syntetický seed cca 8 aplikací s různými tiery, stavy, review statusy, signály a více AI komponentami.
- Seed nesmí obsahovat reálné osobní údaje ani reálné secrets.

## 13. Retence dat

| Data | Retence |
|---|---|
| Aktivní/vyřazené záznamy registru | po dobu evidence; vyřazení = soft delete |
| Immutable `record_history` | zachovat i po vyřazení záznamu; přesná produkční retence je governance rozhodnutí mimo MVP |
| LLM audit metadata | 90 dní |
| Session | do expirace cookie / logoutu |
| Reverzní pseudonymizační mapa | pouze RAM jednoho requestu |
| Obsah promptů/odpovědí LLM | neukládá se nikdy |
| Zvuk/přepisy | aplikace žádné nezpracovává |

README musí otevřeně popsat, že auditní historie může obsahovat historické hodnoty kontaktních údajů,
a že produkční retenční politika by měla být potvrzena vlastníkem governance/privacy procesu.

## 14. UI obrazovky

1. **Přihlášení** — tlačítko „Přihlásit se“ → Keycloak.
2. **Registr** — tabulka: název, vlastník, effective governance tier, signals, stav, AI komponenty,
   review status. Filtr dle stavu, tieru, review statusu. Defaultně pouze aktivní záznamy.
3. **Nový záznam** — kontakty + dotazník + strukturované AI komponenty → duplicate check →
   klasifikační krok → potvrzení → uložení.
4. **Klasifikační krok** — jasně oddělit `AI návrh`, `policy minimum`, `effective tier`,
   aktivovaná pravidla a regulatory signals.
5. **Detail** — celá karta, AI komponenty, signals, review stav, immutable historie,
   tlačítka dle práv.
6. **Editace** — při relevantní změně automaticky vyžádat nové review/klasifikaci.
7. **Admin** — „Vyřadit z evidence“ s povinným důvodem, „Obnovit“; žádný běžný hard delete.
8. **Vyřazené záznamy** — dostupné adminovi přes filtr; audit stále čitelný.

## 15. Acceptance criteria

1. `docker compose up` na čistém stroji → app + Keycloak naběhnou bez ručních kroků.
2. `user` a `admin` se přihlásí přes Keycloak; backend vrací 403 na nepovolené akce i při ručním POSTu.
3. Založení záznamu: dotazník + AI komponenty → duplicate check → AI návrh → policy minimum/signals → potvrzení → registr.
4. `AUTO_DECISION_PERSON` nikdy neskončí effective tierem pod `VELKA`.
5. Počet uživatelů sám o sobě nevynucuje tier; hard floors odpovídají tabulce pravidel.
6. Admin nemůže běžnou editací uložit `klasifikace < klasifikace_minimum`.
7. Regulatory signals vznikají deterministicky; LLM je nemůže odstranit ani vytvořit nepovolenou zkratku.
8. LLM vrátí invalidní JSON/schema → aplikace nespadne, zapíše error metadata a použije fallback.
9. Anthropic timeout/5xx → max 1 retry; následně deterministic fallback; formulář se neztratí.
10. Classification fallback funguje bez API klíče a hard rules jsou stále dodrženy.
11. Duplicate fallback zobrazí lokální kandidáty; pokračování vyžaduje důvod.
12. Duplicate check dostane do LLM max top-N lokálně předvybraných kandidátů, ne celý registr.
13. `DUPLICATE_OVERRIDE` se zapisuje do immutable historie.
14. Pseudonymizace: známá jména/e-maily/telefony se v outbound payloadu do adapteru neobjeví.
15. LLM Gateway má allowlist; nepovolená kontaktní pole nejsou součástí classify promptu.
16. `llm_audit` neobsahuje prompt ani response, ale obsahuje provider/model a verze prompt/rules/gateway/schema.
17. Vyřazení záznamu nastaví `deleted_at/deleted_by/delete_reason`; záznam zmizí z aktivního registru.
18. Vyřazení nesmaže `record_history`; admin záznam může obnovit a audit zůstane.
19. Změna modelu/provideru, práce s daty, autonomie nebo kritičnosti nastaví `review_required=true`.
20. `PROVOZ` záznam po review dostane `review_due_at` a UI rozliší due/overdue.
21. CSRF request bez platného tokenu na state-changing endpoint je odmítnut.
22. Session cookie používá definované bezpečnostní flagy dle prostředí.
23. Dva souběžné edity: druhý update se starou `version` skončí konfliktem, ne silent overwrite.
24. Historie změn zapisuje kdo/kdy/action/co/stará→nová/request_id.
25. `/health` odpovídá a nevypisuje citlivou konfiguraci.
26. Přepnutí `LLM_PROVIDER=mock` ↔ `anthropic` mění konfiguraci, ne business logiku.
27. Test suite obsahuje minimálně unit testy pro policy floors, regulatory signals, anonymizér,
    LLM fallback a backend RBAC; integration test pro create/classify/retire flow.
28. Repo neobsahuje secrets; `.env.example` je úplný; `prompts/` obsahuje versioned prompty.
29. README obsahuje Master Prompt, použitý model/verzi, klasifikaci této aplikace samotné,
    vědomý dluh a explicitní vysvětlení, že `MALA/STREDNI/VELKA` není právní klasifikace.
30. `mock` provider umožní kompletní demo flow offline a deterministicky.

## 16. Testovací minimum

### Policy tests

- každé eskalační pravidlo zvlášť,
- kombinace více pravidel = nejvyšší minimum,
- LLM `MALA` + floor `VELKA` → effective `VELKA`,
- ruční zvýšení je povolené,
- ruční snížení pod minimum je odmítnuté.

### Regulatory signal tests

- osobní údaje → `GDPR`,
- rozhodování o lidech → `AI-ACT`,
- critical + external AI provider → interní `DORA` review signal,
- žádná neznámá zkratka se neuloží.

### Gateway/anonymizer tests

- známé jméno/e-mail/telefon se pseudonymizuje,
- reverzní mapa není persistentní,
- classify allowlist neobsahuje kontaktní pole,
- invalid structured output aktivuje fallback,
- raw prompt/response není v audit logu.

### Auth/security tests

- user nemůže editovat cizí ani retire,
- admin může retire/restore,
- CSRF je vynuceno,
- optimistic locking vrací konflikt.

## 17. Vědomý dluh / future hardening

- Postgres + Alembic.
- CI/CD + automated security/dependency scanning.
- Formální risk acceptance/approval workflow.
- Pokročilejší DLP/NER gateway.
- Fulltext/embeddings podle velikosti registru.
- Review notifications/scheduler.
- Rate limiting a detailnější observability/metrics.
- Formální retenční politika auditních osobních údajů.
- Přesnější policy framework podle finálního interního právního/compliance výkladu.

## 18. Master Prompt (poznámka)

README uvede Master Prompt = kondenzovaná verze **této v2 specifikace** + název a přesnou verzi
modelu, který byl použit pro generování/implementaci. Pro aktuální řešení zachovat deklaraci
`Claude Fable 5 / claude-fable-5`, pokud odpovídá skutečně použitému modelu.

Master Prompt musí zachovat zejména tyto invariants:

- LLM není policy engine.
- Effective tier nikdy neklesá pod deterministic floor.
- Regulatory signals vznikají deterministicky.
- Žádný hard delete záznamu ani auditní historie.
- Žádný raw prompt/response v auditu.
- Veškerý LLM provoz jde přes LLM Gateway.
- LLM failure má deterministic fallback.
- Relevantní změny spouštějí re-review.
- MVP zůstává jednoduchý server-rendered FastAPI monolit.

## 19. Implementační pořadí pro Claude Code

Při implementaci postupuj po vrstvách; nepřidávej další scope mimo tento dokument:

1. **Datový model a soft delete** — nové sloupce, AI components, immutable history, optimistic `version`.
2. **Deterministická logika** — `policy.py`, `regulatory.py`, unit testy bez LLM.
3. **Review lifecycle** — triggers na relevantní změny + UI status.
4. **LLM Gateway** — allowlist, anonymizer, schema validation, audit versions.
5. **LLM adapters + failure contract** — mock nejdřív, Anthropic druhý.
6. **Duplicate prefilter + LLM rerank + override audit**.
7. **RBAC/CSRF/session hardening**.
8. **Server-rendered UI flow**.
9. **Acceptance/integration tests**.
10. **README + Master Prompt + vědomý dluh**.

Pokud je původní implementace v konfliktu s touto v2 specifikací, preferuj v2 a změnu udělej co
nejmenším způsobem, který zachová jednoduchost MVP. Nezaváděj novou infrastrukturu jen kvůli
architektonické čistotě.
