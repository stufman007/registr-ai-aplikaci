Domácí úkol: AI Implementation Expert

**Cíl:** postavit s pomocí AI malou aplikaci, která se dá reálně provozovat. Hodnotíme způsob práce, ne vzhled.
**Rozsah:** maximálně 8 hodin. Nechceme, abys nad tím strávil/a víkend. Co nestihneš, popiš v README jako další krok.

## 1. Vyber si jedno téma

- **A) Registr interních aplikací.** Evidence aplikací vytvořených ve firmě: název, vlastník, zástupce, technický správce, klasifikace (MALÁ, STŘEDNÍ, VELKÁ), stav, použitý AI model. Aplikace sama navrhne klasifikaci podle odpovědí uživatele a zdůvodní ji.
- **B) Osobní znalostní báze.** Ukládání poznámek a dokumentů, vyhledávání nad nimi s pomocí LLM. Odpověď musí vždy odkázat na zdrojový dokument.
- **C) Zápis z jednání.** Aplikace přijme vstup třemi způsoby: záznam pořízený přímo v aplikaci z mikrofonu zařízení, nahraný zvukový soubor, nebo hotový přepis ve formátu VTT, SRT či TXT. Zvuk pošle ke službě pro přepis řeči. Z přepisu vytěží účastníky, projednaná témata, rozhodnutí a úkoly s vlastníkem a termínem. Člověk výstup opraví a potvrdí, teprve pak se uloží. Musí fungovat na počítači i na mobilu (Android i iOS) z jedné codebase.

## 2. Povinné podmínky

**Identita a přístup**

- Přihlášení přes **OIDC nebo OAuth2**, žádná vlastní tabulka uživatelů a hesel. Použij libovolného veřejného poskytovatele nebo lokální mock. Podmínkou je, že **výměna za Microsoft Entra ID je jen změna konfigurace**, ne přepis kódu. Popiš v README, co konkrétně se mění.
- **MFA neimplementuj v aplikaci**, řeší ji identity provider. V README napiš proč.
- Minimálně dvě role s odlišnými právy (například uživatel a admin). Autorizaci vynuť na backendu, ne skrytím tlačítka.

**LLM a data**

- Žádné přímé volání veřejného AI API. Volání LLM schovej za **vlastní abstrakční vrstvu**, aby šla vyměnit za firemní AI Gateway. Pro svůj běh použij vlastní klíč přes proměnnou prostředí.
- Pokud tvoje varianta potřebuje **přepis řeči**, schovej ho za stejnou abstrakci. Použij libovolnou službu na vlastní účet, lokální model nebo mock. Volba není součástí hodnocení, zaměnitelnost ano.
- Pracuj se **syntetickými daty**. Ukaž jednoduchou anonymizaci: jméno, e-mail a telefon nahraď zástupným symbolem a po zpracování je vrať zpět.
- Pokud aplikace zpracovává zvuk nebo osobní údaje, urči v README **jak dlouho se co uchovává a kdy se to maže**, a chování podle toho naprogramuj.
- Logy z volání modelu: model, čas, tokeny. Bez obsahu promptu a bez přepisu.

**Provoz**

- `Dockerfile` a `docker-compose.yaml`. Aplikace musí naběhnout přes `docker compose up` **bez ručních kroků**.
- Žádné secrets v kódu, v image ani v repozitáři. Lokálně `.env`, v repu jen `.env.example`. `.gitignore` to hlídá.
- Health endpoint. Logování startu, chyb a přihlášení či odhlášení.
- Závislosti pinované na konkrétní verze (lockfile nebo `requirements.txt`).

**Repozitář a dokumentace**

- Veřejný Git repozitář (GitHub nebo GitLab.com), případně ZIP s historií commitů. Commit zprávy ať dávají smysl.
- `README.md`: k čemu to je, jak to spustit, jaký model jsi použil/a, klasifikace aplikace **a proč**, co bys dodělal/a a co je vědomý dluh.
- Složka `prompts/` s klíčovými prompty.
- V README uveď **Master Prompt**, tedy zadání, kterým jsi aplikaci nechal/a vygenerovat, a název i verzi modelu.

## 3. Odevzdání a hodnocení

- Odkaz na repozitář a 3-5 minut videa (klidně z mobilu), kde aplikaci spustíš a projdeš. Termín: **10 kalendářních dnů**.
- Hodnotíme: běží to na první pokus podle README, rozumíš tomu, co jsi odevzdal/a, kvalita README a klasifikace, čistota práce se secrets a daty, co jsi vědomě neudělal/a a víš o tom.
- **Nehodnotíme:** design, počet funkcí, testovací pokrytí, počet řádků kódu.
- Na pohovoru tě požádáme, abys vysvětlil/a buď libovolné místo v kódu, nebo specifické řešení nějaké funkcionality, tedy „proč zrovna takto". To je hlavní část hodnocení.

> Úkol slouží výhradně k posouzení tvých dovedností. Výsledek nepoužijeme ani nezveřejníme, práva zůstávají tobě. Máš-li k zadání otázku, napiš. Dotaz není mínus.