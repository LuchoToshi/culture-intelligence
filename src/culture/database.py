from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import JSON, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from culture.config import get_settings

# JSONB on PostgreSQL, plain JSON on SQLite so unit tests can run in memory.
JSONField = JSONB().with_variant(JSON(), "sqlite")


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
