"""Database connection and session management.

This module provides database connectivity using SQLAlchemy with
proper session management and connection pooling.
"""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.config.base import get_config
from src.data.models import Base


# Global engine and session factory
_engine: Engine | None = None
_SessionFactory: sessionmaker | None = None

# TAAD schemas to create on PostgreSQL
TAAD_SCHEMAS = ["import", "enrichment", "analysis"]


def _is_sqlite(database_url: str) -> bool:
    """Check if the database URL is for SQLite."""
    return "sqlite" in database_url


def _setup_sqlite_pragmas(engine: Engine) -> None:
    """Register SQLite PRAGMA listener on a specific engine.

    Only call this for SQLite engines. Sets foreign_keys=ON and
    journal_mode=WAL for better performance and data integrity.
    """
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def _create_schemas(engine: Engine) -> None:
    """Create TAAD schemas on PostgreSQL if they don't exist.

    Creates the import, enrichment, and analysis schemas used by
    the Trade Archaeology & Alpha Discovery system.
    Idempotent - safe to call multiple times.
    """
    with engine.connect() as conn:
        for schema in TAAD_SCHEMAS:
            # 'import' is a reserved word in PostgreSQL, so we quote it
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        conn.commit()


def _apply_schema_migrations(engine: Engine) -> None:
    """Idempotent column additions for schema evolution.

    Adds new columns to existing tables without data loss.
    Safe to call on every startup — already-existing columns are silently skipped.
    """
    with engine.connect() as conn:
        if _is_sqlite(engine.url.drivername):
            # SQLite does not support IF NOT EXISTS on ALTER TABLE — use try/except
            try:
                conn.execute(
                    text(
                        "ALTER TABLE working_memory ADD COLUMN "
                        "last_scheduled_fingerprint VARCHAR(64)"
                    )
                )
                conn.commit()
            except Exception:
                pass  # Column already exists

            # Multi-action plan support
            for col in ("plan_id VARCHAR(36)", "plan_assessment TEXT", "decision_metadata TEXT"):
                try:
                    conn.execute(text(f"ALTER TABLE decision_audit ADD COLUMN {col}"))
                    conn.commit()
                except Exception:
                    pass  # Column already exists

            # Notification action choices
            for col in ("action_choices TEXT", "chosen_action VARCHAR(50)", "chosen_at TIMESTAMP"):
                try:
                    conn.execute(text(f"ALTER TABLE daemon_notifications ADD COLUMN {col}"))
                    conn.commit()
                except Exception:
                    pass  # Column already exists

            # IBKR connection status in daemon health
            try:
                conn.execute(text("ALTER TABLE daemon_health ADD COLUMN ibkr_connected BOOLEAN DEFAULT 0"))
                conn.commit()
            except Exception:
                pass  # Column already exists
        else:
            # PostgreSQL supports IF NOT EXISTS
            conn.execute(
                text(
                    "ALTER TABLE working_memory ADD COLUMN IF NOT EXISTS "
                    "last_scheduled_fingerprint VARCHAR(64)"
                )
            )
            conn.commit()

            # Multi-action plan support
            for col in ("plan_id VARCHAR(36)", "plan_assessment TEXT", "decision_metadata TEXT"):
                conn.execute(text(
                    f"ALTER TABLE decision_audit ADD COLUMN IF NOT EXISTS {col}"
                ))
            conn.commit()

            # Notification action choices
            for col in ("action_choices TEXT", "chosen_action VARCHAR(50)", "chosen_at TIMESTAMP"):
                conn.execute(text(
                    f"ALTER TABLE daemon_notifications ADD COLUMN IF NOT EXISTS {col}"
                ))
            conn.commit()

            # IBKR connection status in daemon health
            conn.execute(text(
                "ALTER TABLE daemon_health ADD COLUMN IF NOT EXISTS ibkr_connected BOOLEAN DEFAULT FALSE"
            ))
            conn.commit()


def init_database(database_url: str | None = None) -> Engine:
    """Initialize database connection and create tables.

    Supports both SQLite (for testing) and PostgreSQL (for production).
    Automatically applies dialect-specific settings.

    Args:
        database_url: Optional database URL (uses config if not provided)

    Returns:
        SQLAlchemy Engine instance

    Example:
        >>> engine = init_database()
        >>> print(engine.url)
        postgresql://localhost/trading_agent
    """
    global _engine, _SessionFactory

    if database_url is None:
        config = get_config()
        database_url = config.database_url

    # Create engine with dialect-appropriate settings
    if _is_sqlite(database_url):
        _engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False,
        )
        _setup_sqlite_pragmas(_engine)
    else:
        # PostgreSQL or other databases
        _engine = create_engine(
            database_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            echo=False,
        )

        # Force UTC on every connection so func.now() / CURRENT_TIMESTAMP
        # always returns UTC regardless of the server's global timezone
        # setting (e.g. Australia/Melbourne).  Without this, all
        # server_default=func.now() columns silently store AEDT.
        @event.listens_for(_engine, "connect")
        def set_pg_timezone(dbapi_conn, connection_record) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("SET timezone = 'UTC'")
            cursor.close()

        # Create TAAD schemas (idempotent)
        _create_schemas(_engine)

    # Create session factory
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)

    # Create all tables (skip schema-qualified TAAD tables on SQLite —
    # they require ATTACH DATABASE which only the TAAD test fixtures set up)
    if _is_sqlite(database_url):
        public_tables = [
            t for t in Base.metadata.sorted_tables if t.schema is None
        ]
        Base.metadata.create_all(_engine, tables=public_tables)
    else:
        Base.metadata.create_all(_engine)

    # Apply idempotent column additions for schema evolution
    _apply_schema_migrations(_engine)

    return _engine


def get_engine() -> Engine:
    """Get the global database engine.

    Returns:
        SQLAlchemy Engine instance

    Raises:
        RuntimeError: If database not initialized

    Example:
        >>> engine = get_engine()
        >>> print(engine.url)
        sqlite:///data/databases/trades.db
    """
    global _engine
    if _engine is None:
        init_database()
    return _engine


def get_session() -> Session:
    """Get a new database session.

    Returns:
        SQLAlchemy Session instance

    Raises:
        RuntimeError: If database not initialized

    Example:
        >>> session = get_session()
        >>> trades = session.query(Trade).all()
        >>> session.close()
    """
    global _SessionFactory
    if _SessionFactory is None:
        init_database()
    return _SessionFactory()


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Context manager for database sessions with automatic cleanup.

    Yields:
        SQLAlchemy Session instance

    Example:
        >>> with get_db_session() as session:
        ...     trades = session.query(Trade).all()
        ...     # session automatically closed and committed/rolled back
    """
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_database() -> None:
    """Drop all tables and recreate them.

    WARNING: This will delete all data! Only use for testing/development.

    Example:
        >>> reset_database()  # All data will be lost!
    """
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def close_database() -> None:
    """Close database connections and dispose of engine.

    Useful for cleanup in tests or when shutting down the application.

    Example:
        >>> close_database()
    """
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
        _engine = None
        _SessionFactory = None
