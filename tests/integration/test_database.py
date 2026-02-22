"""Integration tests for database operations."""

import os
from datetime import datetime, timedelta

import pytest

from src.data.database import (
    close_database,
    get_db_session,
    init_database,
)
from src.data.models import Experiment, LearningHistory, Pattern, Trade
from src.data.repositories import (
    ExperimentRepository,
    LearningHistoryRepository,
    PatternRepository,
    TradeRepository,
)


@pytest.fixture(scope="function")
def test_database():
    """Setup and teardown test database."""
    # Use in-memory SQLite for testing
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"

    # Initialize database
    init_database(database_url="sqlite:///:memory:")

    yield

    # Cleanup
    close_database()


class TestTradeRepository:
    """Tests for Trade repository operations."""

    def test_create_trade(self, test_database) -> None:
        """Test creating a new trade."""
        with get_db_session() as session:
            repo = TradeRepository(session)

            trade = Trade(
                trade_id="TEST001",
                symbol="AAPL",
                strike=150.0,
                expiration=datetime.now().date() + timedelta(days=7),
                entry_date=datetime.now(),
                entry_premium=0.45,
                contracts=5,
                otm_pct=0.18,
                dte=7,
            )

            created_trade = repo.create(trade)
            assert created_trade.id is not None
            assert created_trade.trade_id == "TEST001"

    def test_get_trade_by_id(self, test_database) -> None:
        """Test retrieving a trade by ID."""
        with get_db_session() as session:
            repo = TradeRepository(session)

            # Create a trade
            trade = Trade(
                trade_id="TEST002",
                symbol="MSFT",
                strike=300.0,
                expiration=datetime.now().date() + timedelta(days=7),
                entry_date=datetime.now(),
                entry_premium=0.50,
                contracts=3,
                otm_pct=0.15,
                dte=7,
            )
            repo.create(trade)

        # Retrieve in new session
        with get_db_session() as session:
            repo = TradeRepository(session)
            retrieved_trade = repo.get_by_id("TEST002")

            assert retrieved_trade is not None
            assert retrieved_trade.symbol == "MSFT"
            assert retrieved_trade.strike == 300.0

    def test_get_open_trades(self, test_database) -> None:
        """Test retrieving open trades."""
        with get_db_session() as session:
            repo = TradeRepository(session)

            # Create open trade
            open_trade = Trade(
                trade_id="OPEN001",
                symbol="AAPL",
                strike=150.0,
                expiration=datetime.now().date() + timedelta(days=7),
                entry_date=datetime.now(),
                entry_premium=0.45,
                contracts=5,
                otm_pct=0.18,
                dte=7,
            )
            repo.create(open_trade)

            # Create closed trade
            closed_trade = Trade(
                trade_id="CLOSED001",
                symbol="MSFT",
                strike=300.0,
                expiration=datetime.now().date() + timedelta(days=7),
                entry_date=datetime.now(),
                entry_premium=0.50,
                contracts=3,
                otm_pct=0.15,
                dte=7,
                exit_date=datetime.now(),
                exit_premium=0.25,
                profit_loss=75.0,
            )
            repo.create(closed_trade)

        # Query open trades
        with get_db_session() as session:
            repo = TradeRepository(session)
            open_trades = repo.get_open_trades()

            assert len(open_trades) == 1
            assert open_trades[0].trade_id == "OPEN001"

    def test_get_closed_trades(self, test_database) -> None:
        """Test retrieving closed trades."""
        with get_db_session() as session:
            repo = TradeRepository(session)

            # Create closed trade
            closed_trade = Trade(
                trade_id="CLOSED002",
                symbol="GOOGL",
                strike=100.0,
                expiration=datetime.now().date() + timedelta(days=7),
                entry_date=datetime.now(),
                entry_premium=0.40,
                contracts=4,
                otm_pct=0.17,
                dte=7,
                exit_date=datetime.now(),
                exit_premium=0.20,
                profit_loss=80.0,
            )
            repo.create(closed_trade)

        # Query closed trades
        with get_db_session() as session:
            repo = TradeRepository(session)
            closed_trades = repo.get_closed_trades()

            assert len(closed_trades) == 1
            assert closed_trades[0].trade_id == "CLOSED002"
            assert closed_trades[0].is_closed() is True


class TestExperimentRepository:
    """Tests for Experiment repository operations."""

    def test_create_experiment(self, test_database) -> None:
        """Test creating a new experiment."""
        with get_db_session() as session:
            repo = ExperimentRepository(session)

            experiment = Experiment(
                experiment_id="EXP001",
                name="Test OTM Range",
                parameter_name="otm_range",
                control_value="(0.15, 0.20)",
                test_value="(0.18, 0.22)",
                start_date=datetime.now(),
            )

            created = repo.create(experiment)
            assert created.id is not None
            assert created.experiment_id == "EXP001"

    def test_get_active_experiments(self, test_database) -> None:
        """Test retrieving active experiments."""
        with get_db_session() as session:
            repo = ExperimentRepository(session)

            # Create active experiment
            active = Experiment(
                experiment_id="EXP_ACTIVE",
                name="Active Test",
                parameter_name="premium_range",
                control_value="(0.30, 0.50)",
                test_value="(0.35, 0.55)",
                start_date=datetime.now(),
                status="active",
            )
            repo.create(active)

            # Create completed experiment
            completed = Experiment(
                experiment_id="EXP_COMPLETE",
                name="Completed Test",
                parameter_name="dte_range",
                control_value="(7, 14)",
                test_value="(10, 17)",
                start_date=datetime.now(),
                status="completed",
            )
            repo.create(completed)

        # Query active experiments
        with get_db_session() as session:
            repo = ExperimentRepository(session)
            active_experiments = repo.get_active_experiments()

            assert len(active_experiments) == 1
            assert active_experiments[0].experiment_id == "EXP_ACTIVE"


class TestPatternRepository:
    """Tests for Pattern repository operations."""

    def test_create_pattern(self, test_database) -> None:
        """Test creating a new pattern."""
        with get_db_session() as session:
            repo = PatternRepository(session)

            pattern = Pattern(
                pattern_type="otm_range",
                pattern_name="18-20% OTM outperforms",
                sample_size=50,
                win_rate=0.82,
                avg_roi=0.045,
                confidence=0.96,
                p_value=0.02,
                date_detected=datetime.now(),
            )

            created = repo.create(pattern)
            assert created.id is not None
            assert created.pattern_name == "18-20% OTM outperforms"

    def test_get_active_patterns(self, test_database) -> None:
        """Test retrieving active patterns."""
        with get_db_session() as session:
            repo = PatternRepository(session)

            # Create active pattern
            pattern = Pattern(
                pattern_type="sector",
                pattern_name="Tech sector performs well",
                sample_size=40,
                win_rate=0.85,
                avg_roi=0.05,
                confidence=0.97,
                p_value=0.01,
                date_detected=datetime.now(),
                status="active",
            )
            repo.create(pattern)

        # Query active patterns
        with get_db_session() as session:
            repo = PatternRepository(session)
            patterns = repo.get_active_patterns()

            assert len(patterns) == 1
            assert patterns[0].pattern_name == "Tech sector performs well"
            assert patterns[0].is_valid() is True


class TestLearningHistoryRepository:
    """Tests for LearningHistory repository operations."""

    def test_create_learning_event(self, test_database) -> None:
        """Test creating a learning history entry."""
        with get_db_session() as session:
            repo = LearningHistoryRepository(session)

            event = LearningHistory(
                event_type="pattern_detected",
                event_date=datetime.now(),
                pattern_name="Test Pattern",
                confidence=0.95,
                sample_size=45,
                reasoning="Pattern shows strong statistical significance",
            )

            created = repo.create(event)
            assert created.id is not None
            assert created.event_type == "pattern_detected"

    def test_get_by_event_type(self, test_database) -> None:
        """Test retrieving events by type."""
        with get_db_session() as session:
            repo = LearningHistoryRepository(session)

            # Create pattern detected event
            event1 = LearningHistory(
                event_type="pattern_detected",
                event_date=datetime.now(),
                pattern_name="Pattern 1",
                confidence=0.95,
                sample_size=45,
            )
            repo.create(event1)

            # Create parameter adjusted event
            event2 = LearningHistory(
                event_type="parameter_adjusted",
                event_date=datetime.now(),
                parameter_changed="otm_range",
                old_value="(0.15, 0.20)",
                new_value="(0.18, 0.22)",
            )
            repo.create(event2)

        # Query by event type
        with get_db_session() as session:
            repo = LearningHistoryRepository(session)
            pattern_events = repo.get_by_event_type("pattern_detected")

            assert len(pattern_events) == 1
            assert pattern_events[0].event_type == "pattern_detected"
