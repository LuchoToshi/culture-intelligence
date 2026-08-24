from culture.config import Settings


def test_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost:5432/test_db")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://localhost:5432/test_db"
    assert settings.ai_provider == "openai"
    assert settings.log_level == "DEBUG"


def test_settings_defaults(monkeypatch):
    for var in ("DATABASE_URL", "AI_PROVIDER", "AI_MODEL", "LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=None)
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.ai_provider == "anthropic"
    assert settings.anthropic_api_key == ""
