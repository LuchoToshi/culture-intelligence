import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from culture.database import Base


@pytest.fixture
def session():
    # In-memory SQLite: JSONField degrades from JSONB to JSON, everything else
    # matches production. PostgreSQL-specific behavior gets integration tests.
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()
