# Eval — srovnání LLM modelů pro registr interních AI aplikací

Cílem evalu je najít **sweetspot cena/výkon** pro provoz registru: nejlevnější
model, který ještě klasifikuje dost přesně a píše použitelné české zdůvodnění.

Eval nevolá modely přímo. Každý případ jde přes `app.llm.gateway.classify` /
`find_duplicates` s injektovaným adapterem, takže se měří **identický produkční
tok** — stejný prompt z `prompts/`, stejná pseudonymizace, stejná Pydantic
validace i stejný fallback. Kdyby si eval stavěl vlastní prompt, měřil by něco
jiného, než co poběží v provozu.

## Rychlý start

### Dry-run (bez API klíčů, bez nákladů)

```bash
.venv/Scripts/python -m scripts.eval.run_eval --dry-run --runs 1 --skip-judge
```

Všem modelům se podstrčí deterministický `MockAdapter`. Ověří to celý řetěz
(gateway → validace → audit → agregace → report) offline. Čísla v reportu
**nevypovídají o kvalitě modelů** — report to sám nahoře napíše.

### Ostrý běh

1. Doplnit klíče do `.env` (vzor v `.env.example`):

   ```
   ANTHROPIC_API_KEY=...
   OPENAI_API_KEY=...
   GEMINI_API_KEY=...
   ```

   `LLM_PROVIDER` eval neřeší — adapter si vybírá podle aliasu modelu.

2. Ověřit ID modelů a ceník (viz níže) — **před** ostrým během.

3. Spustit:

   ```bash
   .venv/Scripts/python -m scripts.eval.run_eval
   ```

### Přepínače

| Přepínač | Význam | Výchozí |
|---|---|---|
| `--models` | Čárkami oddělené aliasy | všech šest |
| `--dry-run` | MockAdapter pro všechny modely | vypnuto |
| `--runs N` | Počet běhů na případ (variance) | 2 |
| `--skip-judge` | Vynechá LLM judge | vypnuto |
| `--output DIR` | Kam zapsat výsledky | `scripts/eval/results/` |
| `--limit-cases N` | Jen prvních N klasifikačních případů | vše |
| `--limit-duplicates N` | Jen prvních N případů duplicit | vše |
| `--threshold X` | Práh exact přesnosti pro sweetspot (0–1) | 0.90 |

Aliasy modelů: `claude-sonnet-5`, `claude-haiku-4-5`, `gpt-flagship`, `gpt-mini`,
`gemini-3-pro`, `gemini-3-flash` (definice v `models.py`).

## Metodika

### Zlatý dataset (`dataset.py`)

24 klasifikačních případů a 8 případů kontroly duplicit. Pokrytí:

- ≥1 čistý případ pro každé z 8 deterministických policy pravidel,
- kombinace více pravidel naráz,
- nevinné `MALA` případy (měří falešné eskalace),
- hraniční případy s tolerancí (`acceptable_tiers`) — např. důležitý, ale ne
  kritický proces nad interními daty, nebo „doporučuje, ale nerozhoduje o lidech".

**Klíčová invarianta:** `expected_tier` nesmí být pod deterministickým policy
minimem (`app.services.policy.compute_minimum`). Hlídá to
`tests/test_eval_dataset.py`, takže se dataset nemůže tiše rozejít s pravidly.
`expected_tier` naopak **smí** být nad minimem — tam se testuje nuance, kterou
pravidla neumí zachytit.

### Metriky

| Metrika | Co znamená |
|---|---|
| **Přesnost exact** | Shoda návrhu s `expected_tier`. Hlavní metrika. |
| **Přesnost acceptable** | Návrh spadl do `acceptable_tiers` (tolerance u hraničních případů). |
| **JSON validita** | Podíl volání, jejichž odpověď prošla Pydantic validací v gateway. Zbytek skončil ve fallbacku. |
| **Medián latence** | Z `llm_audit.latence_ms` — medián, ne průměr, kvůli odlehlým hodnotám. |
| **Tokeny in/out** | Průměr z `llm_audit`; u Gemini se do výstupu počítají i reasoning tokeny (účtují se jako výstup). |
| **Cena / 1000 klasifikací** | Průměrná spotřeba tokenů × sazby z `pricing.json`. |
| **Zmínka konceptů** | Podíl klíčových konceptů z datasetu, které se ve zdůvodnění objevily. Nízká hodnota = obecné, nekonkrétní texty. |
| **Duplicity P/R** | Precision a recall proti `expected_match_ids`. Volání spadlá do fallbacku se nepočítají — tam odpověděla lokální heuristika, ne model. |

`--runs N` opakuje každý případ N× nad stejným vstupem. Rozptyl mezi běhy je
vidět v `raw.json` (sloupec `run_index`) a je signálem nestability modelu.

### Cross-judge (`judge.py`)

Kvalitu českého zdůvodnění hodnotí porota tří modelů (`claude-sonnet-5`,
`gpt-flagship`, `gemini-3-pro`) podle rubriky se čtyřmi kritérii 1–5:

1. čeština a srozumitelnost pro netechnického čtenáře,
2. cituje konkrétní odpovědi dotazníku,
3. neobsahuje vymyšlená fakta ani pravidla,
4. vysvětluje, proč právě tento tier.

**Model nikdy nehodnotí vlastní rodinu** — výstup Claude posuzují jen porotci
OpenAI a Gemini a naopak. Tím se obchází známá zaujatost modelů vůči vlastnímu
stylu psaní. Výsledek případu je průměr přes porotce a kritéria.

Judge volá adaptery přímo, ne přes gateway: hodnotí už hotový, pseudonymizovaný
výstup produkčního toku, takže druhá pseudonymizace by text jen zkreslila.
Prompt je verzovaný (`judge_prompt.md`, hlavička `PROMPT_VERSION`).

S `--skip-judge` zůstane sloupec `Judge` v reportu prázdný (`—`).

### Ceník (`pricing.json`)

> **Ceny nejsou ověřené.** Pole `verified` je `false`. Sazby jsou v USD za
> 1 000 000 tokenů a pocházejí z veřejných ceníků podle nejlepších znalostí
> autora — nikoli z živého ceníku.

Před jakýmkoli rozhodnutím o modelu:

1. Zkontrolovat sazby proti aktuálnímu ceníku každého providera.
2. Opravit hodnoty v `pricing.json`.
3. Přepnout `"verified": true` — varovný odstavec z reportu pak zmizí.

Cena za 1000 klasifikací je čistě model-cena. Nezahrnuje kontrolu duplicit
(další volání na každý zakládaný záznam) ani retry při přechodných chybách.

### Ověření ID modelů a ceníku

ID modelů OpenAI a Gemini v `models.py` jsou označená komentářem
„ověřit při běhu". Před ostrým během:

```bash
# OpenAI
.venv/Scripts/python -c "import openai; [print(m.id) for m in openai.OpenAI().models.list()]"

# Gemini
.venv/Scripts/python -c "from google import genai; [print(m.name) for m in genai.Client().models.list()]"
```

Oprava se dělá na jednom místě — pole `model_id` v `models.py`. Alias (klíč
slovníku `MODELS`, sloupce reportu, klíč v `pricing.json`) zůstává stabilní.

## Jak číst report

Report `<timestamp>_report.md` má čtyři části:

1. **Hlavička** — režim běhu, dataset, počet běhů, stav ověření ceníku.
   Varovné odstavce (dry-run, neověřené ceny) čti dřív než tabulky.
2. **Klasifikace** — hlavní tabulka. Doporučené pořadí čtení sloupců:
   JSON validita → exact → cena. Model s nízkou JSON validitou je nepoužitelný
   bez ohledu na přesnost: každá nevalidní odpověď znamená fallback, tedy
   klasifikaci bez zdůvodnění.
3. **Duplicity** — precision je důležitější než recall: falešná duplicita blokuje
   zakládání záznamu a otravuje uživatele, kdežto přehlédnutá duplicita se chytí
   při ručním review.
4. **Sweetspot** — doporučení generované z dat: nejlevnější model nad prahem
   přesnosti, s upozorněním, o kolik je nejpřesnější model dražší.

Surová data (každé jednotlivé volání včetně celého zdůvodnění) jsou v
`<timestamp>_raw.json` — pro ruční prohlédnutí sporných případů.

## Co eval **neměří**

- Chování při reálném zatížení, rate limitech a výpadcích providera.
- Kvalitu na skutečných firemních datech — dataset je syntetický a bez osobních
  údajů (proto ho lze držet v gitu).
- Bezpečnost proti prompt injection (to je záležitost `tests/test_gateway_allowlist.py`).
- Náklady na kontrolu duplicit a retry — cena v reportu pokrývá jen klasifikaci.

## Soubory

| Soubor | Role |
|---|---|
| `dataset.py` | Zlatý dataset — klasifikační i duplicitní případy |
| `models.py` | Registr modelů (alias → provider + ID), načtení ceníku, volba adapteru |
| `pricing.json` | Sazby za tokeny (**neověřené**) |
| `run_eval.py` | CLI harness — běh, agregace, `raw.json` + `report.md` |
| `judge.py` | Cross-judge hodnocení kvality zdůvodnění |
| `judge_prompt.md` | Verzovaný prompt porotce |
| `results/` | Výstupy běhů (generované, do gitu nepatří) |

Offline testy: `tests/test_eval_dataset.py`, `tests/test_eval_harness.py`,
`tests/test_openai_gemini_adapters.py`.
