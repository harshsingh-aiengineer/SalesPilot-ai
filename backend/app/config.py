"""Runtime configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "SalesPilot AI"
    salespilot_env: str = "development"
    salespilot_dev_mode: bool = False
    salespilot_auth_required: bool = True
    salespilot_password: str = ""
    salespilot_secret: str = "dev-insecure-secret-change-me"
    token_ttl_hours: int = 72

    database_url: str = "sqlite:///./data/salespilot.db"
    data_dir: str = "./data"

    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    public_api_base: str = "http://127.0.0.1:8000"
    frontend_base_url: str = "http://127.0.0.1:5173"

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:3b"
    ollama_num_ctx: int = 4096
    ollama_num_predict: int = 600
    ollama_timeout_seconds: int = 120
    max_concurrent_llm: int = 1
    max_agent_tool_calls: int = 8
    max_agent_seconds: int = 180
    max_history_messages: int = 8

    gmail_oauth_redirect_uri: str = "http://127.0.0.1:8000/api/v1/gmail/callback"
    google_client_secrets_file: str = ""
    gmail_token_file: str = ""
    gmail_test_recipient: str = ""

    business_timezone: str = "Asia/Dubai"
    log_level: str = "INFO"
    request_body_limit_bytes: int = 1_000_000

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def secrets_path(self) -> Path:
        return self.data_path / "secrets"

    @property
    def client_secrets_path(self) -> Path:
        if self.google_client_secrets_file:
            return Path(self.google_client_secrets_file)
        return self.secrets_path / "google_client_secret.json"

    @property
    def token_path(self) -> Path:
        if self.gmail_token_file:
            return Path(self.gmail_token_file)
        return self.secrets_path / "gmail_token.json"

    @property
    def using_default_password(self) -> bool:
        return self.salespilot_password.strip() in {"", "change-me", "change-me-now"}

    @property
    def using_default_secret(self) -> bool:
        return "change-me" in self.salespilot_secret or self.salespilot_secret.startswith("dev-insecure")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
