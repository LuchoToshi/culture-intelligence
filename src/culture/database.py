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


def get_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
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
