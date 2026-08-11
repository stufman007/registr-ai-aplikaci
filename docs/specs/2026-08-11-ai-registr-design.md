# Spec: Registr interních AI aplikací

> Domácí úkol — pozice AI Implementation Expert, varianta A.
> Zadání viz [entr_specs.md](../../entr_specs.md). Tento dokument je schválený design
> a zároveň základ **Master Promptu** (povinná součást README).

## 1. Kontext a cíl

Fintech společnost interně vyvíjí AI nástroje a potřebuje nad nimi udržet přehled ze dvou důvodů:

1. **Governance a legislativa** — u každého nástroje musí být jasné, kdo ho vlastní,
   jak je rizikový (signály GDPR, EU AI Act, DORA) a v jakém je stavu životního cyklu.
   EU AI Act se vztahuje i na čistě interní aplikace (typicky HR nástroje hodnotící
   zaměstnance = vysoké riziko dle Annex III), proto klasifikace nemůže ignorovat
   legislativní signály ani u aplikací, jejichž výstup nikdy nejde ke klientovi.
2. **Interní pořádek** — zabránit vzniku duplicitních nástrojů: před založením nového
   záznamu aplikace upozorní na podobné existující.

Výsledek: malá webová aplikace, která se dá reálně provozovat. Uživatel se přihlásí
firemním účtem (OIDC), vidí registr, založí záznam přes klasifikační dotazník a AI
navrhne klasifikaci **MALÁ / STŘEDNÍ / VELKÁ** se zdůvodněním. Člověk má vždy poslední slovo.

## 2. Rozsah

**V rozsahu (MVP):**

- Přihlášení přes OIDC (Keycloak v docker-compose), role `user` a `admin` vynucené na backendu
- CRUD registru aplikací (karta aplikace dle datového modelu níže)
- AI klasifikace přes pevný dotazník + deterministická eskalační pravidla
- AI kontrola duplicit před uložením nového záznamu
- Historie změn záznamů (kdo, kdy, co, stará → nová hodnota)
- Anonymizér osobních údajů před každým voláním LLM (pseudonymizace s reverzní mapou)
- LLM abstrakční vrstva: adapter `mock` (default, běží bez klíče) a `anthropic` (Claude)
- Audit log LLM volání (metadata bez obsahu), retence 90 dní
- Docker compose, health endpoint, seed syntetických dat, README s Master Promptem, složka `prompts/`

**Mimo rozsah (vědomý dluh, popsat v README):**

- Schvalovací workflow klasifikace adminem
- Fulltextové vyhledávání, embeddings/vektorová podobnost pro duplicity
- Postgres (MVP používá SQLite; výměna popsána v README)
- CI/CD pipeline
- Obecné NER rozpoznávání jmen ve volném textu (anonymizér chytá jen známé kontakty + vzory)

## 3. Uživatelé a role

| Role | Práva |
|---|---|
| `user` | vidí celý registr a historii změn; zakládá záznamy; edituje záznamy, kde je vlastníkem nebo autorem (mazat nesmí nic) |
| `admin` | navíc edituje a maže libovolný záznam; může přepsat klasifikaci (povinná poznámka proč) |

„Vlastník nebo autor" = e-mail přihlášeného se shoduje s `vlastnik_email`, nebo jeho
username se shoduje s `created_by`.

- Role žijí v Keycloaku a přicházejí v OIDC tokenu.
- **Autorizace se vynucuje na backendu** u každého požadavku (FastAPI dependency kontrolující
  roli), ne skrytím tlačítka. UI navíc nezobrazuje akce, na které role nemá právo — ale to je
  jen kosmetika nad backendovou kontrolou.
- MFA aplikace neřeší — je zodpovědností identity providera (README vysvětlí: aplikace o MFA
  nesmí ani vědět, jinak by výměna IdP nebyla jen změna konfigurace).

## 4. Datový model

### 4.1 Aplikace (karta záznamu)

| Pole | Typ | Poznámka |
|---|---|---|
| `id` | UUID | |
| `nazev` | text, povinné | |
| `popis` | text, povinné | k čemu slouží — vstup pro klasifikaci i duplicity |
| `vlastnik_jmeno`, `vlastnik_email` | text | osobní údaj → anonymizace před LLM |
| `zastupce_jmeno`, `zastupce_email` | text | osobní údaj → anonymizace před LLM |
| `spravce_jmeno`, `spravce_email` | text | technický správce; osobní údaj → anonymizace před LLM |
| `klasifikace` | enum `MALA` / `STREDNI` / `VELKA` | navrhuje AI, potvrzuje člověk |
| `klasifikace_zduvodneni` | text | české zdůvodnění od AI (případně upravené adminem) |
| `klasifikace_priznaky` | JSON pole | legislativní příznaky, viz 5.4 |
| `klasifikace_potvrdil` | text | kdo návrh potvrdil / přepsal |
| `stav` | enum `VYVOJ` / `PILOT` / `PROVOZ` / `UTLUMENO` | |
| `ai_model` | text | použitý AI model a účel (např. „Claude Sonnet — sumarizace smluv") |
| `dotaznik_odpovedi` | JSON | uložené odpovědi klasifikačního dotazníku (audit trail) |
| `created_by`, `created_at`, `updated_at` | | `created_by` = OIDC subject/username |

### 4.2 Historie změn (`record_history`)

Při každé změně záznamu jeden řádek na každé změněné pole:

| Pole | Poznámka |
|---|---|
| `record_id` | FK na aplikaci |
| `zmenil` | username z tokenu |
| `kdy` | timestamp |
| `pole` | název změněného pole |
| `stara_hodnota`, `nova_hodnota` | textová reprezentace |

Zobrazuje se na detailu aplikace v sekci „Historie změn" (viditelná všem přihlášeným).
Maže se kaskádně se záznamem.

### 4.3 LLM audit log (`llm_audit`)

| Pole | Poznámka |
|---|---|
| `kdy` | timestamp |
| `ucel` | `classify` / `duplicates` |
| `provider`, `model` | např. `anthropic`, `claude-sonnet-…` nebo `mock` |
| `tokens_in`, `tokens_out` | u mocku 0 |
| `latence_ms` | |
| `uspech` | bool + případný typ chyby |

**Bez obsahu promptu a bez odpovědi** (ani v anonymizované podobě). Retence 90 dní —
při startu aplikace se starší řádky smažou.

## 5. AI funkce 1: klasifikace

### 5.1 Klasifikační dotazník (pevný, 7 otázek)

Vyplňuje se při založení záznamu (a lze znovu spustit při editaci). Volby s ⓘ mají
tooltip s vysvětlením a příklady.

1. **K čemu aplikace slouží a jaký proces podporuje?** — volný text
2. **Kolik lidí ji používá / bude používat?** — `do 10` / `10–50` / `přes 50`
3. **Jak kritický proces podporuje?** — volby s tooltipy:
   - `Pomocný` ⓘ *Usnadňuje práci, bez něj se obejdeme; výpadek nikoho nezastaví.
     Příklad: generátor e-mailových podpisů, přehled obědů.*
   - `Důležitý` ⓘ *Tým se bez něj citelně zpomalí, existuje ruční náhrada.
     Příklad: příprava reportů, sumarizace smluv.*
   - `Kritický` ⓘ *Výpadek zastaví část firmy nebo ohrozí povinnost vůči klientům či
     regulátorovi. Příklad: podpora schvalování úvěrů, výpočet rizikových skóre.*
4. **Zpracovává osobní údaje?** — volby s tooltipy:
   - `Ne` ⓘ *Žádná jména, e-maily, telefony, identifikátory osob — ani v logách.*
   - `Zaměstnanců` ⓘ *Údaje kolegů: docházka, výkonnost, interní adresář.*
   - `Klientů` ⓘ *Údaje zákazníků: smlouvy, transakce, kontakty. Nejpřísnější režim.*
5. **Rozhoduje nebo doporučuje o lidech?** — volby s tooltipy:
   - `Ne` ⓘ *Výstup se netýká hodnocení ani třídění konkrétních osob.*
   - `Doporučuje, člověk rozhoduje` ⓘ *AI připraví návrh (pořadí kandidátů, skóre),
     finální rozhodnutí dělá člověk.*
   - `Rozhoduje automatizovaně` ⓘ *Výstup AI se přímo promítne do rozhodnutí o osobě
     bez lidské kontroly. Signál vysokého rizika dle EU AI Act.*
6. **Je výstup viditelný mimo firmu?** — `Ne` / `Nepřímo (vstup do klientských výstupů)` / `Ano (přímo klientům/partnerům)`
7. **Jaký AI model používá a jak?** — volný text

### 5.2 Vyhodnocení: AI navrhuje, pravidla jistí

Tok: odpovědi dotazníku → anonymizér (kap. 7) → LLM adapter → návrh klasifikace →
**deterministická eskalační kontrola v kódu** → zobrazení uživateli.

LLM vrací strukturovaný JSON: `klasifikace`, `zduvodneni` (česky, cituje konkrétní
odpovědi), `priznaky[]`.

**Eskalační minima (v kódu, nezávislá na LLM):**

| Odpověď | Minimální klasifikace |
|---|---|
| Rozhoduje automatizovaně o lidech (ot. 5) | `VELKA` |
| Kritický proces (ot. 3) **a** výstup mimo firmu (ot. 6 ≠ Ne) | `VELKA` |
| Osobní údaje klientů (ot. 4) | `STREDNI` |
| Kritický proces (ot. 3) | `STREDNI` |
| Přes 50 uživatelů (ot. 2) | `STREDNI` |

Navrhne-li LLM méně, kód klasifikaci zvedne a do zdůvodnění doplní větu, které pravidlo
eskalaci způsobilo. Odpověď na „proč zrovna takto" u pohovoru: *AI nesmí být jediná
pojistka v governance nástroji — pravidla jsou auditovatelná, LLM dodává srozumitelné
zdůvodnění a chytá nuance mimo pravidla.*

### 5.3 Lidská kontrola

Uživatel návrh vidí (klasifikace + zdůvodnění + příznaky) a buď potvrdí, nebo přepíše
(povinná poznámka). Ukládá se `klasifikace_potvrdil`. Běžný uživatel nemůže klasifikaci
snížit pod eskalační minimum; smí to jen admin a zapíše se to do historie změn.

### 5.4 Legislativní příznaky

LLM vrací příznaky jako strukturované objekty:

```json
{ "zkratka": "AI-ACT", "titulek": "Automatizované rozhodování o osobách",
  "detail": "Aplikace rozhoduje o lidech bez lidské kontroly, což dle EU AI Act…" }
```

- Povolené zkratky drží **whitelist v kódu**: `GDPR`, `AI-ACT`, `DORA`. Příznak mimo
  whitelist se zahodí (LLM si nesmí vymýšlet legislativu).
- UI: v tabulce registru i na detailu svítí jen **badge se zkratkou**; po najetí myší
  tooltip s titulkem a detailem.

## 6. AI funkce 2: kontrola duplicit

Před uložením nového záznamu:

1. Aplikace vezme název + popis nové aplikace a seznam názvů + popisů existujících
   (projde anonymizérem — popisy mohou obsahovat jména).
2. LLM adapter vrátí kandidáty: `[{ "nazev": …, "duvod_podobnosti": … }]` (max 3, klidně prázdné).
3. Jsou-li kandidáti, UI je zobrazí s vysvětlením a uživatel volí:
   **„Pokračovat — je to jiná aplikace"** (záznam se uloží) / **„Zrušit — už existuje"**.

Žádné embeddingy ani vektorová DB — jeden LLM dotaz nad malým registrem stačí (YAGNI).

## 7. Anonymizér (pseudonymizace s reverzní mapou)

Jediná cesta, kudy data tečou do LLM. Tok **Jméno → Pseudonym → LLM → Pseudonym ve
výstupu → Jméno ve výstupu**, na konkrétním příkladu:

**Krok 0 — vstup (reálná data v aplikaci):**

```
Vlastník:  Jan Novák, jan.novak@firma.cz, +420 601 123 456
Popis:     „Sumarizace smluv, technicky spravuje Jan Novák."
```

**Krok 1 — pseudonymizace (před sestavením promptu):**
Anonymizér vytvoří pro daný požadavek převodní mapu a nahradí výskyty ve **všech**
textech jdoucích do promptu (strukturovaná pole i volný text):

```
Jan Novák           → [OSOBA_1]
jan.novak@firma.cz  → [EMAIL_1]
+420 601 123 456    → [TEL_1]
```

- Hodnoty ze **strukturovaných polí** (jména/e-maily/telefony kontaktů) známe přesně →
  nahrazují se spolehlivě, stejná hodnota = vždy stejný placeholder v rámci požadavku.
- Ve **volném textu** se e-maily a telefony chytají vzorem (mají pevný tvar); jména se
  chytají porovnáním proti známým kontaktům z registru. Obecný NER neděláme — limit
  přiznaný v README.

**Krok 2 — volání LLM:** prompt obsahuje jen pseudonymy:
`„…technicky spravuje [OSOBA_1] ([EMAIL_1])…"`. LLM reálné údaje nikdy nevidí.
Převodní mapa existuje **jen v paměti daného požadavku** — neukládá se, neloguje se.

**Krok 3 — výstup LLM (s pseudonymy):**
`„Klasifikace STŘEDNÍ. Aplikaci spravuje [OSOBA_1] a zpracovává smlouvy…"`

**Krok 4 — deanonymizace (po zpracování):** anonymizér projde odpověď a podle mapy
dosadí zpět reálné hodnoty: `„…Aplikaci spravuje Jan Novák…"`. Teprve tento text se
zobrazí uživateli a uloží do záznamu. Mapa se s koncem požadavku zahodí.

Do LLM audit logu nejde prompt ani odpověď — ani v pseudonymizované podobě (kap. 4.3).

**Proč pseudonymizace, a ne plná anonymizace:** zadání vyžaduje „po zpracování je vrať
zpět" — potřebujeme reverzibilitu pro srozumitelný výstup, AI přitom osobní údaje nevidí.

## 8. Architektura a technologie

```
[prohlížeč] ←HTML→ [FastAPI app (Python)] ←OIDC→ [Keycloak kontejner]
                        │
                        ├── SQLite (soubor na volume)
                        └── LLM vrstva: Anonymizér → LLMClient → adapter mock | anthropic
```

- **Python + FastAPI + Jinja2** (server-rendered HTML, žádný JS build). Formulářové
  POSTy, minimální vanilla JS jen pro tooltips/UX drobnosti.
- **SQLite** — pro interní registr (desítky uživatelů) dostačuje; o službu méně = menší
  riziko u „běží na první pokus". Výměna za Postgres = SQLAlchemy connection string (README).
- **SQLAlchemy** ORM + modely výše. Bez migrací (create_all při startu) — vědomé
  zjednodušení pro MVP, přiznané v README.
- Struktura repa:

```
app/
  main.py            # FastAPI app, startup (create_all, seed, log-retence), /health
  config.py          # načtení .env (pydantic-settings)
  auth.py            # OIDC flow, session, role dependencies (require_user, require_admin)
  models.py, db.py   # SQLAlchemy
  routes/            # registry.py (CRUD + historie), classification.py, admin.py
  llm/
    base.py          # LLMClient interface: classify(), find_duplicates()
    mock.py          # deterministický pravidlový adapter (default)
    anthropic.py     # Claude adapter (klíč z env)
    anonymizer.py    # kap. 7
    audit.py         # zápis llm_audit
  templates/, static/
keycloak/realm-export.json   # realm, klient, role, 2 testovací účty
prompts/             # classification.md, duplicates.md (klíčové prompty)
docs/specs/          # tento dokument
Dockerfile, docker-compose.yaml, .env.example, README.md, seed.py
```

## 9. Identita a přístup

- **Keycloak** jako další služba v docker-compose, start s `--import-realm` — realm
  se 2 účty (`user` / `admin`, testovací hesla v README) a rolemi naběhne bez ručních kroků.
- Aplikace používá standardní **OIDC Authorization Code flow** (knihovna Authlib),
  konfigurace výhradně z env: `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`,
  `OIDC_ROLES_CLAIM`.
- **Výměna za Microsoft Entra ID = změna těchto env hodnot** (issuer na
  `https://login.microsoftonline.com/<tenant>/v2.0`, client ID/secret z App registration,
  claim s rolemi dle Entra app roles). Žádná změna kódu — README popíše přesný postup
  včetně toho, kde se v Entra klikají app roles.
- Session: podepsaný session cookie (Starlette SessionMiddleware, secret z env);
  v cookie jen identita a role, žádné tokeny IdP.
- Logování přihlášení a odhlášení (username, čas) — bez tokenů.

## 10. Provoz

- `docker compose up` → naběhne Keycloak (healthcheck) → app (depends_on, čeká na
  healthy Keycloak) → app při startu: create_all, seed syntetických dat (jen je-li DB
  prázdná), smazání LLM audit logů starších 90 dní.
- `/health` — bez autentizace, vrací stav aplikace a DB.
- Logy: start aplikace, chyby, login/logout, LLM audit (viz 4.3).
- **Secrets**: jen `.env` (lokálně), v repu `.env.example` se všemi klíči a komentáři.
  `.gitignore` hlídá `.env`, `*.db`. Žádný secret v kódu, image ani historii commitů.
- Závislosti pinované (`requirements.txt` s `==`).
- **Syntetická data**: seed ~8 fiktivních aplikací s fiktivními jmény/e-maily
  (`jan.novak@example.com`…), různé klasifikace, stavy i příznaky — ať je na videu co ukazovat.

## 11. Retence dat (README + kód)

| Data | Retence |
|---|---|
| Záznamy registru vč. kontaktů (osobní údaje zaměstnanců) | po dobu evidence; mazání záznamu = kaskádní smazání historie |
| LLM audit log (jen metadata) | 90 dní, čistí se při startu |
| Session | zánik s cookie / odhlášením |
| Zvuk / přepisy | aplikace žádné nezpracovává |
| Obsah promptů a odpovědí LLM | neukládá se nikdy |

## 12. UI obrazovky (server-rendered, česky)

1. **Přihlášení** — jen tlačítko „Přihlásit se" → redirect na Keycloak.
2. **Registr** — tabulka: název, vlastník, klasifikace (barevný štítek), příznaky
   (badge se zkratkou + hover tooltip), stav, AI model. Filtr dle stavu a klasifikace.
3. **Nový záznam** — kontakty + dotazník (7 otázek, tooltips ⓘ) → krok „kontrola
   duplicit" (jen když LLM něco našel) → krok „návrh klasifikace" (potvrdit / přepsat) → uloženo.
4. **Detail** — celá karta, příznaky s tooltipy, historie změn, tlačítka dle práv
   (editovat / smazat / překlasifikovat).
5. **Admin drobnosti** — mazání a přepis klasifikace přímo v detailu (žádná zvláštní
   admin sekce — YAGNI).

## 13. Akceptační kritéria (mapování na zadání)

1. `docker compose up` na čistém stroji → do pár minut běží app + Keycloak, bez ručních kroků.
2. Přihlášení účtem `user` i `admin` přes Keycloak; `user` nemůže mazat žádný záznam
   ani editovat cizí (backend vrátí 403 i při ručním POSTu), `admin` může obojí.
3. Založení záznamu: dotazník → (případní duplicitní kandidáti) → návrh klasifikace se
   zdůvodněním a příznaky → potvrzení → záznam v registru.
4. Eskalační minima fungují: odpověď „rozhoduje automatizovaně" nikdy neskončí jako MALÁ.
5. Anonymizér: v promptu odchozím do LLM adapteru nejsou reálná jména/e-maily/telefony
   (ověřitelné v mock adapteru), ve výstupu uživateli ano.
6. LLM audit log obsahuje model/čas/tokeny/latenci, neobsahuje žádný obsah.
7. Historie změn: úprava pole zapíše kdo/kdy/co/stará→nová.
8. `/health` odpovídá; logy obsahují start, přihlášení, chyby.
9. Repo: žádný secret, `.env.example` úplný, `prompts/` s klíčovými prompty, README
   s Master Promptem, modelem, klasifikací této aplikace samotné a vědomým dluhem.
10. Přepnutí `LLM_PROVIDER=mock` ↔ `anthropic` mění jen env, ne kód.

## 14. Vědomý dluh (do README)

Schvalovací workflow · fulltext · embeddings duplicity · Postgres · CI/CD · DB migrace ·
obecný NER v anonymizéru · rate limiting (interní nástroj za SSO; přiznat, zdůvodnit).

## 15. Master Prompt (poznámka)

README uvede Master Prompt = kondenzovaná verze tohoto specu (zadání, kterým byla
aplikace vygenerována) + název a verzi použitého modelu (Claude Fable 5 / claude-fable-5).
