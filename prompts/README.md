# `prompts/` — verzované prompty LLM Gateway

Tento adresář obsahuje jediné dva prompty, které aplikace posílá do LLM
(`app/llm/gateway.py`). Jiná cesta k LLM v kódu neexistuje — gateway je
jediné místo, které tyto soubory čte (`gateway._load_prompt`).

## Verzování

- Každý soubor začíná řádkem `PROMPT_VERSION: v<N>`.
- `gateway._load_prompt` tuto hlavičku vytáhne regexem a verzi uloží do
  `llm_audit.prompt_version` u **každého** volání (úspěšného i selhaného).
- Změna promptu = nový obsah **a** zvýšení `PROMPT_VERSION`. Stará verze čísla
  se znovu nepoužívá — audit musí umět zpětně rozlišit, podle jakého znění
  promptu byla odpověď vygenerována (reprodukovatelnost přes verzi, ne přes
  logování obsahu, spec kap. 7.3 a 13).
- HTML komentáře (`<!-- ... -->`) v hlavičce souboru jsou dokumentace pro
  vývojáře a **do modelu se neposílají** — `gateway._load_prompt` je před
  odesláním odstraní.

## Co do promptu smí

- `classification.md`: odpovědi klasifikačního dotazníku (otázky 1–9) a
  seznam AI komponent (poskytovatel + typ hostingu). Volný text (otázka 1)
  vždy prochází `Anonymizer.pseudonymize` před vložením do promptu.
- `duplicates.md`: název a popis nově zakládaného záznamu + max 10 lokálně
  předvybraných kandidátů z registru (název, popis, skóre podobnosti) —
  nikdy celý registr.

## Co do promptu nesmí

- **Žádné kontaktní údaje** — jméno/e-mail/telefon vlastníka, zástupce ani
  správce. Pro `classify` nejsou potřeba vůbec (minimalizace má přednost
  před pseudonymizací, spec kap. 7.1); pokud se přesto objeví ve volném textu
  (např. v popisu), projdou pseudonymizací dřív, než odejdou.
- **Identifikátory mimo allowlist** — ID záznamu (kromě `record_id` u
  `duplicates`, kde je to nutné pro spárování odpovědi), uživatelská jména,
  session tokeny, konfigurace, secrets.
- **Legislativní příznaky ani `klasifikace_minimum`** — obojí vzniká
  deterministicky v kódu (`policy.py`, `regulatory.py`). Model o nich neví a
  nesmí je vracet ani ovlivňovat.
- **Nepseudonymizovaný volný text** — každý uživatelský vstup, který jde do
  promptu, prochází `Anonymizer` a je v šabloně ohraničený jako `DATA`, ne
  instrukce (obrana proti prompt injection z popisu aplikace).

Nový prompt (nový účel LLM) znamená: nový `.md` soubor s hlavičkou
`PROMPT_VERSION`, odpovídající Pydantic schéma v `app/llm/gateway.py` pro
validaci structured outputu, a explicitní allowlist polí v komentáři na
začátku souboru — stejně jako u obou stávajících promptů.
