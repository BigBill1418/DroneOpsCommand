from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://droneops:changeme@db:5432/droneops"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Ollama
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "mistral:7b-instruct-v0.3-q4_K_M"

    # OpenDroneLog
    opendronelog_url: str = ""

    # JWT
    jwt_secret_key: str = "changeme_generate_a_random_secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 30

    # Admin
    admin_username: str = "admin"
    admin_password: str = "changeme_in_production"

    # SMTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "reports@barnardhq.com"
    smtp_from_name: str = "BarnardHQ Drone Operations"
    smtp_use_tls: bool = True

    # File storage
    upload_dir: str = "/data/uploads"
    reports_dir: str = "/data/reports"

    class Config:
        env_file = ".env"


settings = Settings()
