from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost:5432/culture_intelligence"

    ai_provider: str = "anthropic"
    ai_model: str = ""
    # Weekly synthesis is one call a week where judgment quality is the product;
    # it defaults to a stronger model than per-item analysis.
    ai_synthesis_model: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
