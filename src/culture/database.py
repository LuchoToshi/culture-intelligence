import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from sqlalchemy import JSON, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from culture.config import get_settings
from culture.logging import get_logger

log = get_logger("culture.database")

# JSONB on PostgreSQL, plain JSON on SQLite so unit tests can run in memory.
JSONField = JSONB().with_variant(JSON(), "sqlite")

T = TypeVar("T")


class Base(DeclarativeBase):
    pass


def _with_psycopg_driver(url: str) -> str:
    """Force the psycopg (v3) driver for bare postgres schemes.

    Managed providers (Neon, RDS, ...) hand out driver-agnostic
    postgresql:// URLs. SQLAlchemy's default dialect for that scheme is
    psycopg2, which this project does not install — only psycopg (v3), used
    everywhere else (CLI, tests, Alembic). Without this, create_engine()
    silently picks the wrong, uninstalled driver.
    """
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


def get_engine(database_url: str | None = None) -> Engine:
    url = _with_psycopg_driver(database_url or get_settings().database_url)
    return create_engine(url, pool_pre_ping=True)


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    factory = get_session_factory(engine or get_engine())
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def retry_on_disconnect(
    session: Session,
    operation: Callable[[], T],
    *,
    attempts: int = 5,
    pause_seconds: float = 10.0,
) -> T:
    """Run operation(); if the database connection drops under it, run it again.

    Neon closes idle connections, and a Mac that falls asleep mid-run (launchd starts
    the weekly job in a short DarkWake window) wakes up holding a dead socket. The
    session is rolled back, which throws the dead connection away, and the next attempt
    opens a fresh one. Pauses grow (10s, 20s, ...) to give Wi-Fi time to come back.

    Only wrap work that is safe to repeat from the top: reads, or writes that a rollback
    undoes completely. The last attempt is not caught, so a database that stays down
    still fails the run.
    """
    for attempt in range(1, attempts):
        try:
            return operation()
        except OperationalError as exc:
            pause = pause_seconds * attempt
            reason = (str(exc.orig).splitlines() or [type(exc).__name__])[0]
            log.warning(
                "database connection dropped (%s); retrying in %.0fs (attempt %d of %d)",
                reason,
                pause,
                attempt + 1,
                attempts,
            )
            session.rollback()
            time.sleep(pause)
    return operation()
