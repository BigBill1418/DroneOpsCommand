from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://doc:changeme@db:5432/doc"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Ollama
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5:3b"

    # Claude (Anthropic)
    anthropic_api_key: str = ""

    # LLM provider selection: "ollama" or "claude"
    llm_provider: str = "ollama"

    # OpenDroneLog
    opendronelog_url: str = ""

    # JWT
    jwt_secret_key: str = "changeme_generate_a_random_secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 30

    # Demo mode admin (only used when DEMO_MODE=true)
    demo_admin_username: str = ""
    demo_admin_password: str = ""

    # SMTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = ""
    smtp_use_tls: bool = True

    # File storage
    upload_dir: str = "/data/uploads"
    reports_dir: str = "/data/reports"

    # Customer intake
    frontend_url: str = "http://localhost:3080"
    intake_token_expire_days: int = 7

    # Client portal
    client_token_expire_days: int = 30

    # Stripe (optional — falls back to DB-stored settings)
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_publishable_key: str = ""

    # Demo mode
    demo_mode: bool = False
    demo_reset_interval_hours: int = 24

    @property
    def database_url_sync(self) -> str:
        """Synchronous database URL for Celery tasks."""
        return self.database_url.replace("+asyncpg", "")

    class Config:
        env_file = ".env"


settings = Settings()
