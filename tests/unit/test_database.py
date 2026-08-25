import pytest

from culture.database import _with_psycopg_driver, get_engine


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "postgresql://user:pass@host/db",
            "postgresql+psycopg://user:pass@host/db",
        ),
        (
            "postgres://user:pass@host/db",
            "postgresql+psycopg://user:pass@host/db",
        ),
        (
            "postgresql://user:pass@ep-morning-fire.neon.tech/neondb?sslmode=require",
            "postgresql+psycopg://user:pass@ep-morning-fire.neon.tech/neondb?sslmode=require",
        ),
    ],
)
def test_bare_postgres_schemes_get_psycopg_driver(raw, expected):
    assert _with_psycopg_driver(raw) == expected


@pytest.mark.parametrize(
    "already_explicit",
    [
        "postgresql+psycopg://user:pass@host/db",
        "sqlite+pysqlite:///:memory:",
        "postgresql+psycopg2://user:pass@host/db",  # explicit choice stays untouched
    ],
)
def test_urls_with_explicit_driver_are_left_alone(already_explicit):
    assert _with_psycopg_driver(already_explicit) == already_explicit


def test_get_engine_uses_psycopg_driver_for_bare_postgres_url():
    # This is exactly the production crash: a bare postgresql:// URL (what
    # Neon and most managed providers hand out) must resolve to the psycopg
    # (v3) driver that's actually installed, not SQLAlchemy's psycopg2
    # default, which this project doesn't install anywhere.
    engine = get_engine("postgresql://user:pass@host/db")
    assert engine.url.drivername == "postgresql+psycopg"
