# Akceptační checklist (spec kap. 15) — stav k 2026-08-11

Legenda: ✅ ověřeno · ⏳ PENDING (čeká na funkční Docker Desktop na vývojovém stroji)

| # | Kritérium | Stav | Jak ověřeno |
|---|---|---|---|
| 1 | `docker compose up` na čistém stroji bez ručních kroků | ⏳ | `docker compose config` staticky validní bez `.env`; živý běh čeká na Docker. OIDC část flow ověřena živě proti nativnímu Keycloaku 26.4 (F9). |
| 2 | Login `user`/`admin`; nepovolené akce → 403 i při ručním POSTu | ✅ | Živý OIDC flow (F9): user → `/admin/ping` 403, admin → 200. Testy: RBAC 403 v test_admin/test_edit/test_registry_routes. |
| 3 | Založení: dotazník + komponenty → duplicity → klasifikace → potvrzení | ✅ | test_wizard.py happy path + duplicity + historie CREATE/CLASSIFY/DUPLICATE_OVERRIDE. |
| 4 | `AUTO_DECISION_PERSON` nikdy pod VELKA; admin neuloží pod minimum | ✅ | test_policy.py + test_wizard (400 při POST s MALA) + test_admin (překlasifikace pod minimum → 400). |
| 5 | Signály deterministické; LLM je nemůže změnit ani přidat zkratku | ✅ | test_regulatory.py (whitelist), gateway signály nevrací (test_gateway_allowlist). |
| 6 | Nevalidní odpověď/timeout → max 1 retry → fallback; formulář se neztratí | ✅ | test_fallback.py (počítadlo volání) + test_wizard (fallback zachová session stav). |
| 7 | Mock provider = kompletní demo offline bez klíče | ✅ | Mock je default; smoke F6; testy bez sítě. |
| 8 | Pseudonymizace; classify payload bez kontaktních polí | ✅ | test_anonymizer.py (round-trip) + test_gateway_allowlist.py (spy na prompt). |
| 9 | `llm_audit` bez obsahu, s verzemi/outcome | ✅ | test_fallback.py + test_history.py (žádný sloupec pro obsah). |
| 10 | Vyřazení s důvodem, historie zůstává, admin obnoví | ✅ | test_admin.py (RETIRE/RESTORE + historie). |
| 11 | `DUPLICATE_OVERRIDE` s důvodem; do LLM max top-10 | ✅ | test_wizard.py + test_similarity.py + test_gateway_allowlist.py. |
| 12 | Historie kdo/kdy/akce/pole/stará→nová; přežívá vyřazení | ✅ | test_history.py + test_admin.py. |
| 13 | Riziková změna → re-klasifikace; komponenty → „vyžaduje review" | ✅ | test_edit.py (redirect na klasifikaci, review_required=True/False). |
| 14 | CSRF → 403; souběžný edit → 409 | ✅ | test_health_csrf.py + test_edit.py + test_admin.py (409 u všech admin akcí). |
| 15 | `/health`; LLM_PROVIDER jen env; guardrail testy zelené | ✅ | 176 testů zelených; /health smoke; přepnutí provider = env (test_anthropic_adapter fail-fast). |
| 16 | Repo bez secrets; `.env.example` úplný; `prompts/` verzované; README kompletní | ✅ | Sken git log -p (jediný nález = placeholder `sk-ant-...` v README); `.env`/`*.db` netrackované; README checklist F15. |

## Zbývá po zprovoznění Docker Desktopu

1. `docker compose down -v && docker compose up --build` z čistého klonu bez `.env`.
2. Ověřit: obě služby healthy → login user/admin přes prohlížeč → seed data viditelná → založení aplikace v mock režimu.
3. Ověřit keycloak healthcheck uvnitř kontejneru (TCP connect varianta — nikdy neběžela v reálném kontejneru).
4. `docker run --rm <image> env` — žádný secret v image.
