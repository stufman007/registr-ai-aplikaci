# Scénář demo videa (3–5 minut)

Cíl: ukázat, že to běží na první pokus, a předvést governance jádro. Natáčet po zprovoznění Dockeru, po `docker compose down -v`.

Poznámka k AI provozu: video běží na `LLM_PROVIDER=mock` (default) — deterministické,
offline, žádný API klíč, opakovatelné natáčení se stejným výstupem. Ostrá verze
(`LLM_PROVIDER=openai` + `gpt-5.4-mini`, doporučený produkční setup dle evalu — README)
se pro demo nepoužívá záměrně, aby se natáčení nezaseklo na latenci/nákladech reálného volání.

## Osnova (čas ~4:30)

1. **(0:00–0:40) Start** — terminál: `docker compose up`. Komentář: dvě služby, žádné ruční kroky, žádný `.env` — vše má defaulty, mock AI režim. Mezitím ukázat `http://localhost:8000/health` → `{"status":"ok"}`.
2. **(0:40–1:20) Přihlášení a registr** — login `user`/`user` přes Keycloak (zdůraznit: aplikace nemá vlastní hesla, OIDC; výměna za Entra ID = 4 hodnoty v .env). Registr: 8 seed aplikací, barevné tiery, badge signálů — najet myší na GDPR/AI-ACT/DORA tooltip, ukázat badge „vyžaduje review" a filtr. Volitelně: ukázat per-sloupcový multi-choice filtr (tier/stav) a stránkování.
3. **(1:20–2:50) Založení aplikace — jádro** — „Založit novou aplikaci": vyplnit kontakty, AI komponentu (Anthropic, externí API), dotazník — u kritičnosti ukázat ⓘ tooltip s příklady. Zvolit **„rozhoduje automatizovaně o lidech"**. Pokud vyskočí krok duplicit: ukázat kandidáty a pokračovat s důvodem. Klasifikační krok: **vedle sebe AI návrh / povinné minimum (AUTO_DECISION_PERSON → VELKÁ) / výsledný tier / signály**. Komentář: „AI navrhuje, ale tvrdá pravidla jistí — tohle AI nemůže obejít." Uložit, ukázat detail s historií CREATE+CLASSIFY.
4. **(2:50–3:30) Admin akce** — odhlásit, login `admin`/`admin`. Na detailu záznamu „Vyřadit" s důvodem → záznam zmizí z aktivního pohledu → filtr vyřazených → historie RETIRE zůstala. Zkusit překlasifikovat pod minimum → 400 s vysvětlením pravidla („ani admin nesmí pod policy floor").
5. **(3:30–4:30) Kód — „proč zrovna takto"** — krátce v editoru:
   - `app/services/policy.py` — deklarativní tabulka pravidel s reason codes,
   - `app/llm/gateway.py` — tok allowlist → pseudonymizace → validace → deanonymizace; audit bez obsahu,
   - README — Master Prompt + sebe-klasifikace aplikace (STŘEDNÍ kvůli EXTERNAL_PROVIDER_SENSITIVE_DATA).
   - Volitelně: stránka `/pravidla` — jak se z otázek dotazníku odvozuje minimum tieru, generovaná přímo z `policy.py`/`regulatory.py`.

## Záložní plán
Kdyby cokoli s Dockerem na místě selhalo: `docker compose up keycloak` + lokální uvicorn (README sekce Alternativa) — demo je identické.
