# Eval report — Registr interních AI aplikací

- **Datum běhu:** 2026-08-12 11:51:39
- **Režim:** OSTRÝ (reálná API volání)
- **Dataset:** 24 klasifikačních případů, 8 případů duplicit (verze 2026-08-v1)
- **Běhů na případ:** 2
- **Judge:** cross-judge, claude-sonnet-5, gpt-flagship, gemini-3-pro
- **Ceník:** `pricing.json`, měna USD, verified = `true`

> **Anomálie — `gemini-3-pro` (`gemini-3.1-pro-preview`).** Všech 128 volání
> (64 klasifikace + 64 duplicit) i všech 188 volání jako porotce skončilo
> chybou `RATE_LIMIT`/`PROVIDER_5XX` — ověřeno dvěma nezávislými běhy nad celým
> datasetem (100% selhání pokaždé), zatímco přímé jednorázové volání stejným
> klíčem uspělo. Diagnóza: perzistentní nízký per-minute kvótový limit tohoto
> preview modelu na použitém API klíči/projektu, ne přechodný výpadek — gateway
> retry (1×) ani odstup mezi pokusy to nevyřešily. Řádky `gemini-3-pro` v
> tabulkách níže proto ukazují 0 (fallback na lokální pravidla, žádná reálná
> odpověď modelu) a nejsou signálem kvality modelu. Doporučení: ověřit/zvýšit
> kvótu pro `gemini-3.1-pro-preview` u Google (tier účtu), pak eval pro tento
> jeden model zopakovat samostatně: `--models gemini-3-pro --runs 2`.
>
> Ostatních pět modelů (`claude-sonnet-5`, `claude-haiku-4-5`, `gpt-flagship`,
> `gpt-mini`, `gemini-3-flash`) proběhlo bez systémových chyb — jen ojedinělé
> `fallback_used` z formátu odpovědi (počítá se do JSON validity, viz níže).

## Klasifikace

| Model | Model ID | Přesnost exact | Přesnost acceptable | JSON validita | Medián latence (ms) | Tokeny in (prům.) | Tokeny out (prům.) | Cena / 1000 klasifikací (USD) | Zmínka konceptů | Judge (1–5) |
|---|---|---|---|---|---|---|---|---|---|---|
| gemini-3-pro | `gemini-3.1-pro-preview` | 0.0 % | 0.0 % | 0.0 % | 125 | 0 | 0 | 0.00 | 0.0 % | — |
| claude-haiku-4-5 | `claude-haiku-4-5-20251001` | 79.2 % | 87.5 % | 95.8 % | 2211 | 896 | 151 | 1.65 | 83.8 % | 4.32 |
| gpt-mini | `gpt-5.4-mini` | 89.6 % | 97.9 % | 100.0 % | 2078 | 789 | 175 | 1.38 | 85.1 % | 4.31 |
| gpt-flagship | `gpt-5.4` | 79.2 % | 91.7 % | 100.0 % | 2585 | 789 | 178 | 4.65 | 95.9 % | 4.24 |
| claude-sonnet-5 | `claude-sonnet-5` | 75.0 % | 83.3 % | 95.8 % | 6015 | 1194 | 216 | 4.55 | 91.9 % | 4.66 |
| gemini-3-flash | `gemini-3.5-flash` | 83.3 % | 91.7 % | 91.7 % | 6164 | 787 | 1529 | 14.94 | 89.2 % | 4.12 |

## Kontrola duplicit

| Model | Precision | Recall | F1 | JSON validita | Medián latence (ms) |
|---|---|---|---|---|---|
| gemini-3-pro | — | — | — | 0.0 % | 125 |
| claude-haiku-4-5 | 85.7 % | 100.0 % | 92.3 % | 100.0 % | 1398 |
| gpt-mini | 66.7 % | 100.0 % | 80.0 % | 100.0 % | 1328 |
| gpt-flagship | 100.0 % | 100.0 % | 100.0 % | 100.0 % | 1524 |
| claude-sonnet-5 | 85.7 % | 100.0 % | 92.3 % | 100.0 % | 4039 |
| gemini-3-flash | 100.0 % | 100.0 % | 100.0 % | 100.0 % | 4289 |

## Skutečná cena tohoto běhu

Součet (tokeny_in × sazba_in + tokeny_out × sazba_out) přes všechna skutečná volání v tomto běhu — ne odhad z průměrné spotřeby.

### Klasifikace + duplicity (per model)

| Model | Volání celkem | Tokeny in (součet) | Tokeny out (součet) | Cena (USD) |
|---|---|---|---|---|
| gemini-3-pro | 64 | 0 | 0 | 0.0000 |
| claude-haiku-4-5 | 64 | 54384 | 8570 | 0.0972 |
| gpt-mini | 64 | 48294 | 9776 | 0.0802 |
| gpt-flagship | 64 | 48294 | 9578 | 0.2644 |
| claude-sonnet-5 | 64 | 72506 | 12172 | 0.2667 |
| gemini-3-flash | 64 | 48096 | 88877 | 0.8720 |

### Porotci (cross-judge, per porotce)

| Porotce | Volání | Tokeny in (součet) | Tokeny out (součet) | Cena (USD) |
|---|---|---|---|---|
| claude-sonnet-5 | 140 | 187946 | 28281 | 0.6587 |
| gpt-flagship | 136 | 137925 | 17764 | 0.6113 |
| gemini-3-pro | 188 | 0 | 0 | 0.0000 |

**Celková skutečná cena tohoto běhu: 2.8506 USD.**

## Sweetspot — doporučení

Práh přesnosti pro provoz: **exact ≥ 90 %**.

**Žádný model práh nesplnil.** Nejblíž je `gpt-mini` (exact 89.6 %, 1.38 USD / 1000 klasifikací).

Postup: buď snížit práh (klasifikaci jistí policy minimum a lidské potvrzení), nebo upravit prompt a eval zopakovat.

Pořadí podle ceny (vzestupně):

- `gemini-3-pro` — 0.00 USD / 1000 klasifikací, exact 0.0 %
- `gpt-mini` — 1.38 USD / 1000 klasifikací, exact 89.6 %
- `claude-haiku-4-5` — 1.65 USD / 1000 klasifikací, exact 79.2 %
- `claude-sonnet-5` — 4.55 USD / 1000 klasifikací, exact 75.0 %
- `gpt-flagship` — 4.65 USD / 1000 klasifikací, exact 79.2 %
- `gemini-3-flash` — 14.94 USD / 1000 klasifikací, exact 83.3 %

## Jak číst tabulky

- **Přesnost exact** — shoda s `expected_tier` zlatého datasetu. Hlavní metrika kvality.
- **Přesnost acceptable** — návrh spadl do `acceptable_tiers` (tolerance u hraničních případů).
- **JSON validita** — podíl volání, kde odpověď prošla Pydantic validací gateway. Zbytek skončil ve fallbacku (tier určila jen pravidla).
- **Zmínka konceptů** — podíl klíčových konceptů z datasetu, které se ve zdůvodnění skutečně objevily. Nízká hodnota = obecné, nekonkrétní texty.
- **Judge** — průměr cross-judge rubriky (1–5): čeština, citace odpovědí, absence vymyšlených faktů, vysvětlení tieru. Modely nehodnotí vlastní rodinu.
- **Duplicity** — precision/recall proti `expected_match_ids`; volání, která spadla do fallbacku, se do P/R nepočítají (odpověděla lokální heuristika, ne model).
