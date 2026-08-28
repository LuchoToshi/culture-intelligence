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


def test_supabase_database_url_takes_precedence_when_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost:5432/neon_db")
    monkeypatch.setenv("SUPABASE_DATABASE_URL", "postgresql+psycopg://localhost:5432/supabase_db")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://localhost:5432/supabase_db"


def test_database_url_is_unchanged_when_supabase_database_url_is_absent(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost:5432/neon_db")
    monkeypatch.delenv("SUPABASE_DATABASE_URL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://localhost:5432/neon_db"
