from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_secret_key: str = "dev-insecure-secret-key-change-me"
    database_url: str = "sqlite:///./registr.db"

    llm_provider: str = "mock"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str | None = None
    # ID ověřena při ostrém běhu evalu 2026-08 přes `models.list` daného SDK
    # (viz scripts/eval/README.md). Při dalším upgradu providera ověřit znovu.
    # Produkční doporučení dle evalu cena/výkon (docs/eval/2026-08-12_eval-report.md).
    openai_model: str = "gpt-5.4-mini"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.1-pro-preview"
    llm_timeout_seconds: int = 10
    llm_audit_retention_days: int = 90
    # Demo přepínač: mock adapter vždy selže (ukázka fallbacku bez sítě).
    llm_force_fail: bool = False

    # Veřejná URL (prohlížeč, authorize + logout) a backchannel URL (token, jwks,
    # userinfo) jsou vedeny odděleně kvůli riziku R1 — issuer v tokenu vždy
    # odpovídá veřejné variantě. Default cílí na lokální běh (uvicorn na hostu),
    # kde jsou obě URL shodné; docker-compose backchannel přepíše na `keycloak:8080`.
    oidc_issuer_url: str = "http://localhost:8080/realms/registr"
    oidc_internal_url: str = "http://localhost:8080/realms/registr"
    oidc_client_id: str = "registr-app"
    oidc_client_secret: str = "dev-oidc-client-secret"
    oidc_roles_claim: str = "roles"

    session_max_age: int = 28800  # 8 hodin v sekundách
    cookie_secure: bool = False


settings = Settings()
