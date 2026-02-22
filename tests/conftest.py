"""Pytest configuration and fixtures."""

import os
import sys
from pathlib import Path

import pytest

@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setenv("IBKR_PORT", "7497")


@pytest.fixture(autouse=True)
def _reset_config_singleton():
    """Ensure each test gets a fresh Config singleton.

    Without this, monkeypatch.setenv in individual tests would be
    ignored because get_config() returns the cached singleton from
    a previous test.
    """
    from src.config.base import reset_config

    reset_config()
    yield
    reset_config()

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Setup test environment before all tests."""
    # Set test environment variables
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test123456789"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["PAPER_TRADING"] = "true"
    os.environ["LOG_LEVEL"] = "ERROR"  # Reduce noise during tests

    yield

    # Cleanup
    pass


@pytest.fixture
def mock_config():
    """Provide a mock configuration for testing."""
    from src.config.base import Config

    return Config()


@pytest.fixture
def temp_database():
    """Provide a temporary database for testing."""
    from src.data.database import close_database, init_database

    # Initialize in-memory database
    engine = init_database(database_url="sqlite:///:memory:")

    yield engine

    # Cleanup
    close_database()


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "e2e: End-to-end tests")
    config.addinivalue_line("markers", "slow: Tests that take a long time to run")
    config.addinivalue_line(
        "markers",
        "live: marks tests requiring live IBKR connection (deselect with '-m \"not live\"')",
    )
