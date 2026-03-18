"""Shared fixtures for TAAD tests.

Uses SQLite ATTACH DATABASE to create the 'import' schema that TAAD models
require, instead of mutating global SQLAlchemy table metadata. This prevents
test pollution when running the full test suite.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.data.models import Base
from src.taad.models import ImportSession


@pytest.fixture
def taad_engine():
    """Create an in-memory SQLite engine with the 'import' schema attached.

    TAAD models use ``schema='import'`` for PostgreSQL. In SQLite, we emulate
    this by ATTACHing a second in-memory database under the alias 'import'.
    The listener fires on every new connection so pooled connections also work.
    """
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _attach_import_schema(dbapi_conn, connection_record):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS 'import'")

    Base.metadata.create_all(engine)

    yield engine

    engine.dispose()


@pytest.fixture
def db_session(taad_engine):
    """Provide a SQLAlchemy session with the 'import' schema available."""
    factory = sessionmaker(bind=taad_engine)
    session = factory()
    yield session
    session.close()


@pytest.fixture
def db_session_with_import(db_session):
    """Provide (session, import_session_id) for tests that need a pre-created ImportSession."""
    import_session = ImportSession(
        status="completed",
        source_type="flex_query",
        account_id="YOUR_ACCOUNT",
    )
    db_session.add(import_session)
    db_session.flush()

    yield db_session, import_session.id
