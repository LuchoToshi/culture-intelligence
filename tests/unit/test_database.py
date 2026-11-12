from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

import culture.database as database
from culture.database import _with_psycopg_driver, get_engine, retry_on_disconnect


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


class FakeSession:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


def dropped_connection():
    # What psycopg raises when the socket under a session is dead (see logs/analyze.log).
    return OperationalError(
        "SELECT 1",
        {},
        Exception("consuming input failed: server closed the connection unexpectedly"),
    )


@pytest.fixture
def pauses(monkeypatch):
    recorded = []
    monkeypatch.setattr(database, "time", SimpleNamespace(sleep=recorded.append))
    return recorded


def test_retry_on_disconnect_runs_again_on_a_fresh_connection(pauses):
    session = FakeSession()
    calls = []

    def operation():
        calls.append(1)
        if len(calls) < 3:
            raise dropped_connection()
        return "done"

    assert retry_on_disconnect(session, operation) == "done"
    assert len(calls) == 3
    assert session.rollbacks == 2  # the dead connection is discarded before each retry
    assert pauses == [10.0, 20.0]


def test_retry_on_disconnect_gives_up_after_the_last_attempt(pauses):
    session = FakeSession()
    calls = []

    def operation():
        calls.append(1)
        raise dropped_connection()

    with pytest.raises(OperationalError):
        retry_on_disconnect(session, operation, attempts=3)
    assert len(calls) == 3
    assert len(pauses) == 2


def test_retry_on_disconnect_does_not_hide_other_errors(pauses):
    session = FakeSession()
    calls = []

    def operation():
        calls.append(1)
        raise ValueError("a bug, not a network problem")

    with pytest.raises(ValueError):
        retry_on_disconnect(session, operation)
    assert len(calls) == 1
    assert session.rollbacks == 0
    assert pauses == []
