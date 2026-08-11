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
    llm_timeout_seconds: int = 10
    llm_audit_retention_days: int = 90
    # Demo přepínač: mock adapter vždy selže (ukázka fallbacku bez sítě).
    llm_force_fail: bool = False

    oidc_issuer_url: str = "http://localhost:8080/realms/registr"
    oidc_internal_url: str = "http://keycloak:8080/realms/registr"
    oidc_client_id: str = "registr-app"
    oidc_client_secret: str = "dev-oidc-client-secret"
    oidc_roles_claim: str = "roles"

    session_max_age: int = 28800  # 8 hodin v sekundách
    cookie_secure: bool = False


settings = Settings()
