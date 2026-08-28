import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from culture.database import Base


@pytest.fixture(autouse=True)
def _no_ambient_supabase_database_url(monkeypatch):
    """Settings.database_url prefers SUPABASE_DATABASE_URL when it's set
    (config.py) — a developer's local .env can have a real one for manual
    testing against Supabase's own Postgres. Without this, every test that
    relies on get_settings()/get_engine() (most CLI tests; the web test
    suite mostly sidesteps it by passing an explicit engine to create_app)
    would silently point at that real database instead of its own isolated
    sqlite file, depending on what happens to be in .env on the machine
    running the suite.

    monkeypatch.delenv does NOT fix this: pydantic-settings' env_file
    support reads the .env file directly, independent of os.environ, so a
    real value sitting in .env is picked up even after the OS env var is
    deleted. Setting it to an explicit empty string does work, because an
    OS environment variable (even an empty one) takes precedence over the
    .env file in pydantic-settings' resolution order.

    Autouse so no individual test has to remember this; a test that
    specifically wants to exercise the precedence behavior itself
    (test_config.py) overrides this with its own monkeypatch.setenv call
    after this fixture has already run."""
    monkeypatch.setenv("SUPABASE_DATABASE_URL", "")


@pytest.fixture(autouse=True)
def _default_auth_mode(monkeypatch):
    """create_app() branches on Settings.auth_mode on every single call —
    if a developer's local .env has AUTH_MODE=supabase set (for manual
    testing against a real Supabase project), every test building an app
    via create_app(require_auth=True) would silently mount the Supabase
    route set instead of magic-link, breaking the entire magic-link test
    suite in ways that look like unrelated 422s and redirect mismatches.
    Forced to "magiclink" here; test_auth_supabase.py's own fixture
    overrides this back to "supabase" for the tests that need it."""
    monkeypatch.setenv("AUTH_MODE", "magiclink")


@pytest.fixture
def session():
    # In-memory SQLite: JSONField degrades from JSONB to JSON, everything else
    # matches production. PostgreSQL-specific behavior gets integration tests.
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()
