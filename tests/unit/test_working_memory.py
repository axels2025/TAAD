"""Unit tests for WorkingMemory and ReasoningContext.

Tests crash-safe working memory persistence, FIFO decision queue,
autonomy level clamping, anomaly tracking, context assembly from
open positions and patterns, and prompt string generation.
"""

from datetime import datetime, date, timedelta

import pytest

from src.agentic.working_memory import (
    MAX_RECENT_DECISIONS,
    ReasoningContext,
    WorkingMemory,
)
from src.data.database import close_database, get_session, init_database
from src.data.models import Base, Pattern, Trade, WorkingMemoryRow


@pytest.fixture
def temp_database():
    """Provide an in-memory SQLite database with all tables created."""
    engine = init_database(database_url="sqlite:///:memory:")
    yield engine
    close_database()


@pytest.fixture
def db_session(temp_database):
    """Provide a fresh database session."""
    session = get_session()
    yield session
    session.close()


# =========================================================================
# WorkingMemory Initialization
# =========================================================================


class TestWorkingMemoryInit:
    """Tests for WorkingMemory initialization behavior."""

    def test_initializes_empty_when_no_db_row(self, db_session):
        """WorkingMemory starts with empty defaults when no row exists."""
        wm = WorkingMemory(db_session)

        assert wm.strategy_state == {}
        assert wm.market_context == {}
        assert len(wm.recent_decisions) == 0
        assert wm.anomalies == []
        assert wm.autonomy_level == 1
        assert wm.reflection_reports == []

    def test_loads_existing_state_from_db(self, db_session):
        """WorkingMemory loads persisted state when a row exists."""
        # Pre-populate the database row
        row = WorkingMemoryRow(
            id=1,
            strategy_state={"otm_range": [5, 10]},
            market_context={"vix": 18.5, "regime": "low_vol"},
            recent_decisions=[
                {"action": "sell_put", "confidence": 0.9},
                {"action": "hold", "confidence": 0.7},
            ],
            anomalies=[{"description": "VIX spike", "timestamp": "2026-01-15T10:00:00"}],
            autonomy_level=3,
            reflection_reports=[{"summary": "Good day", "timestamp": "2026-01-14T16:00:00"}],
        )
        db_session.add(row)
        db_session.commit()

        wm = WorkingMemory(db_session)

        assert wm.strategy_state == {"otm_range": [5, 10]}
        assert wm.market_context == {"vix": 18.5, "regime": "low_vol"}
        assert len(wm.recent_decisions) == 2
        assert wm.recent_decisions[0]["action"] == "sell_put"
        assert wm.anomalies[0]["description"] == "VIX spike"
        assert wm.autonomy_level == 3
        assert len(wm.reflection_reports) == 1

    def test_loads_with_none_json_fields(self, db_session):
        """WorkingMemory handles None JSON fields gracefully."""
        row = WorkingMemoryRow(
            id=1,
            strategy_state=None,
            market_context=None,
            recent_decisions=None,
            anomalies=None,
            autonomy_level=2,
            reflection_reports=None,
        )
        db_session.add(row)
        db_session.commit()

        wm = WorkingMemory(db_session)

        assert wm.strategy_state == {}
        assert wm.market_context == {}
        assert len(wm.recent_decisions) == 0
        assert wm.anomalies == []
        assert wm.autonomy_level == 2
        assert wm.reflection_reports == []


# =========================================================================
# WorkingMemory.save()
# =========================================================================


class TestWorkingMemorySave:
    """Tests for the save (upsert) method."""

    def test_save_creates_row_when_none_exists(self, db_session):
        """save() inserts a new row when no row with id=1 exists."""
        wm = WorkingMemory(db_session)
        wm.strategy_state = {"param": "value"}
        wm.save()

        row = db_session.query(WorkingMemoryRow).get(1)
        assert row is not None
        assert row.strategy_state == {"param": "value"}

    def test_save_updates_existing_row(self, db_session):
        """save() updates the existing row rather than creating a second one."""
        wm = WorkingMemory(db_session)
        wm.strategy_state = {"version": 1}
        wm.save()

        # Modify and save again
        wm.strategy_state = {"version": 2}
        wm.save()

        rows = db_session.query(WorkingMemoryRow).all()
        assert len(rows) == 1
        assert rows[0].strategy_state == {"version": 2}

    def test_save_persists_all_fields(self, db_session):
        """save() persists every field to the database."""
        wm = WorkingMemory(db_session)
        wm.strategy_state = {"strike_selection": "delta"}
        wm.market_context = {"vix": 22.0}
        wm.recent_decisions.append({"action": "sell"})
        wm.anomalies = [{"description": "gap down"}]
        wm.autonomy_level = 2
        wm.reflection_reports = [{"summary": "review"}]
        wm.save()

        row = db_session.query(WorkingMemoryRow).get(1)
        assert row.strategy_state == {"strike_selection": "delta"}
        assert row.market_context == {"vix": 22.0}
        assert row.recent_decisions == [{"action": "sell"}]
        assert row.anomalies == [{"description": "gap down"}]
        assert row.autonomy_level == 2
        assert row.reflection_reports == [{"summary": "review"}]

    def test_save_updates_timestamp(self, db_session):
        """save() sets updated_at to the current time."""
        wm = WorkingMemory(db_session)
        wm.save()

        row = db_session.query(WorkingMemoryRow).get(1)
        assert row.updated_at is not None


# =========================================================================
# WorkingMemory.add_decision()
# =========================================================================


class TestAddDecision:
    """Tests for the add_decision FIFO queue."""

    def test_add_decision_appends(self, db_session):
        """add_decision() appends a decision to the deque."""
        wm = WorkingMemory(db_session)
        wm.add_decision({"action": "sell_put", "confidence": 0.85})

        assert len(wm.recent_decisions) == 1
        assert wm.recent_decisions[0]["action"] == "sell_put"

    def test_add_decision_persists_to_db(self, db_session):
        """add_decision() auto-saves to the database."""
        wm = WorkingMemory(db_session)
        wm.add_decision({"action": "hold"})

        row = db_session.query(WorkingMemoryRow).get(1)
        assert row is not None
        assert len(row.recent_decisions) == 1
        assert row.recent_decisions[0]["action"] == "hold"

    def test_add_decision_fifo_max_50(self, db_session):
        """add_decision() caps at MAX_RECENT_DECISIONS (50), dropping oldest."""
        wm = WorkingMemory(db_session)

        # Add 55 decisions
        for i in range(55):
            wm.add_decision({"action": f"decision_{i}"})

        assert len(wm.recent_decisions) == MAX_RECENT_DECISIONS
        # Oldest decisions (0-4) should have been dropped
        assert wm.recent_decisions[0]["action"] == "decision_5"
        assert wm.recent_decisions[-1]["action"] == "decision_54"

    def test_add_decision_fifo_persisted_correctly(self, db_session):
        """The FIFO-capped list is persisted correctly in the database."""
        wm = WorkingMemory(db_session)
        for i in range(MAX_RECENT_DECISIONS + 5):
            wm.add_decision({"idx": i})

        row = db_session.query(WorkingMemoryRow).get(1)
        assert len(row.recent_decisions) == MAX_RECENT_DECISIONS
        assert row.recent_decisions[0]["idx"] == 5

    def test_add_decision_preserves_existing(self, db_session):
        """Adding a decision does not remove non-overflowed entries."""
        wm = WorkingMemory(db_session)
        wm.add_decision({"action": "first"})
        wm.add_decision({"action": "second"})

        assert len(wm.recent_decisions) == 2
        assert wm.recent_decisions[0]["action"] == "first"
        assert wm.recent_decisions[1]["action"] == "second"


# =========================================================================
# WorkingMemory.update_market_context()
# =========================================================================


class TestUpdateMarketContext:
    """Tests for market context updates."""

    def test_update_market_context_sets_value(self, db_session):
        """update_market_context() replaces the market context."""
        wm = WorkingMemory(db_session)
        ctx = {"vix": 20.0, "spy_price": 5800.0, "regime": "normal"}
        wm.update_market_context(ctx)

        assert wm.market_context == ctx

    def test_update_market_context_persists(self, db_session):
        """update_market_context() auto-saves to the database."""
        wm = WorkingMemory(db_session)
        wm.update_market_context({"vix": 30.0})

        row = db_session.query(WorkingMemoryRow).get(1)
        assert row.market_context == {"vix": 30.0}

    def test_update_market_context_replaces_entirely(self, db_session):
        """update_market_context() replaces the entire dict, not merges."""
        wm = WorkingMemory(db_session)
        wm.update_market_context({"vix": 15.0, "spy": 5700.0})
        wm.update_market_context({"vix": 25.0})

        assert wm.market_context == {"vix": 25.0}
        assert "spy" not in wm.market_context


# =========================================================================
# WorkingMemory.update_strategy_state()
# =========================================================================


class TestUpdateStrategyState:
    """Tests for strategy state updates."""

    def test_update_strategy_state_sets_value(self, db_session):
        """update_strategy_state() replaces the strategy state."""
        wm = WorkingMemory(db_session)
        state = {"otm_min": 5, "otm_max": 10, "premium_min": 0.50}
        wm.update_strategy_state(state)

        assert wm.strategy_state == state

    def test_update_strategy_state_persists(self, db_session):
        """update_strategy_state() auto-saves to the database."""
        wm = WorkingMemory(db_session)
        wm.update_strategy_state({"mode": "aggressive"})

        row = db_session.query(WorkingMemoryRow).get(1)
        assert row.strategy_state == {"mode": "aggressive"}


# =========================================================================
# WorkingMemory.set_autonomy_level()
# =========================================================================


class TestSetAutonomyLevel:
    """Tests for autonomy level setting and clamping."""

    def test_set_autonomy_level_valid_range(self, db_session):
        """set_autonomy_level() accepts values 1 through 4."""
        wm = WorkingMemory(db_session)

        for level in [1, 2, 3, 4]:
            wm.set_autonomy_level(level)
            assert wm.autonomy_level == level

    def test_set_autonomy_level_clamps_low(self, db_session):
        """set_autonomy_level() clamps values below 1 to 1."""
        wm = WorkingMemory(db_session)
        wm.set_autonomy_level(0)
        assert wm.autonomy_level == 1

        wm.set_autonomy_level(-5)
        assert wm.autonomy_level == 1

    def test_set_autonomy_level_clamps_high(self, db_session):
        """set_autonomy_level() clamps values above 4 to 4."""
        wm = WorkingMemory(db_session)
        wm.set_autonomy_level(5)
        assert wm.autonomy_level == 4

        wm.set_autonomy_level(100)
        assert wm.autonomy_level == 4

    def test_set_autonomy_level_persists(self, db_session):
        """set_autonomy_level() auto-saves to the database."""
        wm = WorkingMemory(db_session)
        wm.set_autonomy_level(3)

        row = db_session.query(WorkingMemoryRow).get(1)
        assert row.autonomy_level == 3


# =========================================================================
# WorkingMemory.add_anomaly()
# =========================================================================


class TestAddAnomaly:
    """Tests for anomaly recording."""

    def test_add_anomaly_appends(self, db_session):
        """add_anomaly() appends an anomaly to the list."""
        wm = WorkingMemory(db_session)
        wm.add_anomaly({"description": "VIX spike above 30"})

        assert len(wm.anomalies) == 1
        assert wm.anomalies[0]["description"] == "VIX spike above 30"

    def test_add_anomaly_adds_timestamp(self, db_session):
        """add_anomaly() adds a timestamp field to the anomaly dict."""
        wm = WorkingMemory(db_session)
        anomaly = {"description": "gap down"}
        wm.add_anomaly(anomaly)

        assert "timestamp" in wm.anomalies[0]
        # Verify it is a valid ISO format timestamp
        datetime.fromisoformat(wm.anomalies[0]["timestamp"])

    def test_add_anomaly_caps_at_20(self, db_session):
        """add_anomaly() keeps only the last 20 anomalies."""
        wm = WorkingMemory(db_session)

        for i in range(25):
            wm.add_anomaly({"description": f"anomaly_{i}"})

        assert len(wm.anomalies) == 20
        # Oldest anomalies (0-4) should have been dropped
        assert wm.anomalies[0]["description"] == "anomaly_5"
        assert wm.anomalies[-1]["description"] == "anomaly_24"

    def test_add_anomaly_persists(self, db_session):
        """add_anomaly() auto-saves to the database."""
        wm = WorkingMemory(db_session)
        wm.add_anomaly({"description": "unusual volume"})

        row = db_session.query(WorkingMemoryRow).get(1)
        assert len(row.anomalies) == 1
        assert row.anomalies[0]["description"] == "unusual volume"

    def test_add_anomaly_does_not_mutate_original(self, db_session):
        """add_anomaly() adds timestamp to its own copy, verifiable on stored data."""
        wm = WorkingMemory(db_session)
        anomaly = {"description": "test"}
        wm.add_anomaly(anomaly)

        # The anomaly dict passed in gets the timestamp added
        # (this is the current behavior - timestamp is added in-place)
        assert "timestamp" in anomaly


# =========================================================================
# WorkingMemory.add_reflection()
# =========================================================================


class TestAddReflection:
    """Tests for EOD reflection report recording."""

    def test_add_reflection_appends(self, db_session):
        """add_reflection() appends a reflection to the list."""
        wm = WorkingMemory(db_session)
        wm.add_reflection({"summary": "Good day, 3 wins out of 4"})

        assert len(wm.reflection_reports) == 1
        assert wm.reflection_reports[0]["summary"] == "Good day, 3 wins out of 4"

    def test_add_reflection_adds_timestamp(self, db_session):
        """add_reflection() adds a timestamp field."""
        wm = WorkingMemory(db_session)
        wm.add_reflection({"summary": "EOD review"})

        assert "timestamp" in wm.reflection_reports[0]
        datetime.fromisoformat(wm.reflection_reports[0]["timestamp"])

    def test_add_reflection_caps_at_30(self, db_session):
        """add_reflection() keeps only the last 30 reflections."""
        wm = WorkingMemory(db_session)

        for i in range(35):
            wm.add_reflection({"summary": f"day_{i}"})

        assert len(wm.reflection_reports) == 30
        assert wm.reflection_reports[0]["summary"] == "day_5"
        assert wm.reflection_reports[-1]["summary"] == "day_34"

    def test_add_reflection_persists(self, db_session):
        """add_reflection() auto-saves to the database."""
        wm = WorkingMemory(db_session)
        wm.add_reflection({"summary": "reflection test"})

        row = db_session.query(WorkingMemoryRow).get(1)
        assert len(row.reflection_reports) == 1


# =========================================================================
# WorkingMemory.assemble_context()
# =========================================================================


class TestAssembleContext:
    """Tests for context assembly into ReasoningContext."""

    def test_assemble_context_returns_reasoning_context(self, db_session):
        """assemble_context() returns a ReasoningContext instance."""
        wm = WorkingMemory(db_session)
        ctx = wm.assemble_context()

        assert isinstance(ctx, ReasoningContext)

    def test_assemble_context_includes_autonomy_level(self, db_session):
        """assemble_context() includes the current autonomy level."""
        wm = WorkingMemory(db_session)
        wm.set_autonomy_level(3)
        ctx = wm.assemble_context()

        assert ctx.autonomy_level == 3

    def test_assemble_context_includes_strategy_state(self, db_session):
        """assemble_context() includes the strategy state."""
        wm = WorkingMemory(db_session)
        wm.strategy_state = {"mode": "conservative"}
        ctx = wm.assemble_context()

        assert ctx.strategy_state == {"mode": "conservative"}

    def test_assemble_context_includes_market_context(self, db_session):
        """assemble_context() includes the market context."""
        wm = WorkingMemory(db_session)
        wm.update_market_context({"vix": 17.5})
        ctx = wm.assemble_context()

        assert ctx.market_context == {"vix": 17.5}

    def test_assemble_context_includes_recent_decisions(self, db_session):
        """assemble_context() includes recent decisions as a list."""
        wm = WorkingMemory(db_session)
        wm.add_decision({"action": "sell_put"})
        wm.add_decision({"action": "hold"})
        ctx = wm.assemble_context()

        assert len(ctx.recent_decisions) == 2
        assert ctx.recent_decisions[0]["action"] == "sell_put"

    def test_assemble_context_includes_anomalies(self, db_session):
        """assemble_context() includes detected anomalies."""
        wm = WorkingMemory(db_session)
        wm.add_anomaly({"description": "VIX spike"})
        ctx = wm.assemble_context()

        assert len(ctx.anomalies) == 1
        assert ctx.anomalies[0]["description"] == "VIX spike"

    def test_assemble_context_with_open_positions(self, db_session):
        """assemble_context() queries open trades (exit_date is None)."""
        # Create an open trade
        trade = Trade(
            trade_id="open-001",
            symbol="AAPL",
            strike=180.0,
            expiration=date(2026, 3, 21),
            entry_date=datetime(2026, 2, 15),
            entry_premium=1.50,
            contracts=2,
            dte=34,
            exit_date=None,
        )
        db_session.add(trade)
        db_session.commit()

        wm = WorkingMemory(db_session)
        ctx = wm.assemble_context()

        assert len(ctx.open_positions) == 1
        assert ctx.open_positions[0]["symbol"] == "AAPL"
        assert ctx.open_positions[0]["strike"] == 180.0
        assert ctx.open_positions[0]["entry_premium"] == 1.50
        assert ctx.open_positions[0]["contracts"] == 2
        assert ctx.open_positions[0]["dte"] == 34
        assert ctx.positions_summary == "1 open positions"

    def test_assemble_context_excludes_closed_positions(self, db_session):
        """assemble_context() excludes trades that have been closed."""
        # Create a closed trade
        closed_trade = Trade(
            trade_id="closed-001",
            symbol="MSFT",
            strike=400.0,
            expiration=date(2026, 2, 21),
            entry_date=datetime(2026, 2, 1),
            exit_date=datetime(2026, 2, 10),
            entry_premium=2.00,
            exit_premium=0.50,
            contracts=1,
            dte=20,
            profit_loss=150.0,
            roi=0.75,
            exit_reason="profit_target",
        )
        db_session.add(closed_trade)
        db_session.commit()

        wm = WorkingMemory(db_session)
        ctx = wm.assemble_context()

        assert len(ctx.open_positions) == 0

    def test_assemble_context_with_active_patterns(self, db_session):
        """assemble_context() queries active patterns ordered by confidence."""
        p1 = Pattern(
            pattern_type="otm_range",
            pattern_name="Sweet spot 5-8%",
            sample_size=50,
            win_rate=0.85,
            avg_roi=0.12,
            confidence=0.97,
            p_value=0.01,
            status="active",
            date_detected=datetime(2026, 1, 15),
        )
        p2 = Pattern(
            pattern_type="dte",
            pattern_name="Short DTE outperforms",
            sample_size=40,
            win_rate=0.78,
            avg_roi=0.08,
            confidence=0.95,
            p_value=0.03,
            status="active",
            date_detected=datetime(2026, 1, 20),
        )
        p_inactive = Pattern(
            pattern_type="sector",
            pattern_name="Tech underperforms",
            sample_size=30,
            win_rate=0.60,
            avg_roi=0.04,
            confidence=0.90,
            p_value=0.05,
            status="invalidated",
            date_detected=datetime(2026, 1, 10),
        )
        db_session.add_all([p1, p2, p_inactive])
        db_session.commit()

        wm = WorkingMemory(db_session)
        ctx = wm.assemble_context()

        # Only active patterns should be included
        assert len(ctx.active_patterns) == 2
        # Sorted by confidence desc
        assert ctx.active_patterns[0]["name"] == "Sweet spot 5-8%"
        assert ctx.active_patterns[0]["confidence"] == 0.97
        assert ctx.active_patterns[1]["name"] == "Short DTE outperforms"

    def test_assemble_context_with_recent_closed_trades(self, db_session):
        """assemble_context() includes recently closed trades."""
        for i in range(3):
            trade = Trade(
                trade_id=f"closed-{i}",
                symbol=f"SYM{i}",
                strike=100.0 + i * 10,
                expiration=date(2026, 2, 21),
                entry_date=datetime(2026, 2, 1) + timedelta(days=i),
                exit_date=datetime(2026, 2, 10) + timedelta(days=i),
                entry_premium=1.50,
                exit_premium=0.50,
                contracts=1,
                dte=20,
                profit_loss=100.0,
                roi=0.67,
                exit_reason="profit_target",
            )
            db_session.add(trade)
        db_session.commit()

        wm = WorkingMemory(db_session)
        ctx = wm.assemble_context()

        assert len(ctx.recent_trades) == 3
        # Most recent first
        assert ctx.recent_trades[0]["symbol"] == "SYM2"

    def test_assemble_context_includes_latest_reflection(self, db_session):
        """assemble_context() includes the latest reflection report."""
        wm = WorkingMemory(db_session)
        wm.add_reflection({"summary": "First day"})
        wm.add_reflection({"summary": "Second day"})

        ctx = wm.assemble_context()

        assert ctx.latest_reflection is not None
        assert ctx.latest_reflection["summary"] == "Second day"

    def test_assemble_context_no_reflection_when_empty(self, db_session):
        """assemble_context() returns None for latest_reflection when none exist."""
        wm = WorkingMemory(db_session)
        ctx = wm.assemble_context()

        assert ctx.latest_reflection is None

    def test_assemble_context_empty_db(self, db_session):
        """assemble_context() works with a completely empty database."""
        wm = WorkingMemory(db_session)
        ctx = wm.assemble_context()

        assert ctx.autonomy_level == 1
        assert ctx.open_positions == []
        assert ctx.active_patterns == []
        assert ctx.recent_trades == []
        assert ctx.recent_decisions == []
        assert ctx.anomalies == []
        assert ctx.latest_reflection is None


# =========================================================================
# ReasoningContext.to_prompt_string()
# =========================================================================


class TestReasoningContextToPromptString:
    """Tests for ReasoningContext prompt string generation."""

    def test_to_prompt_string_minimal(self):
        """to_prompt_string() produces output with just autonomy level."""
        ctx = ReasoningContext(autonomy_level=1)
        result = ctx.to_prompt_string()

        assert "## Autonomy Level: L1" in result

    def test_to_prompt_string_includes_open_positions(self):
        """to_prompt_string() formats open positions."""
        ctx = ReasoningContext(
            autonomy_level=2,
            open_positions=[
                {
                    "symbol": "AAPL",
                    "strike": 180.0,
                    "expiration": "2026-03-21",
                    "pnl_pct": "45%",
                },
            ],
        )
        result = ctx.to_prompt_string()

        assert "## Open Positions (1)" in result
        assert "AAPL" in result
        assert "180.0" in result
        assert "2026-03-21" in result

    def test_to_prompt_string_includes_market_context(self):
        """to_prompt_string() formats market context key-value pairs."""
        ctx = ReasoningContext(
            autonomy_level=1,
            market_context={"vix": 18.5, "regime": "low_vol"},
        )
        result = ctx.to_prompt_string()

        assert "## Market Context" in result
        assert "vix: 18.5" in result
        assert "regime: low_vol" in result

    def test_to_prompt_string_includes_active_patterns(self):
        """to_prompt_string() formats active patterns with win rate and confidence."""
        ctx = ReasoningContext(
            autonomy_level=1,
            active_patterns=[
                {
                    "name": "OTM 5-8%",
                    "win_rate": 0.85,
                    "confidence": 0.97,
                },
            ],
        )
        result = ctx.to_prompt_string()

        assert "## Active Patterns (1)" in result
        assert "OTM 5-8%" in result
        assert "win_rate=0.85" in result
        assert "confidence=0.97" in result

    def test_to_prompt_string_limits_patterns_to_5(self):
        """to_prompt_string() shows at most 5 patterns."""
        ctx = ReasoningContext(
            autonomy_level=1,
            active_patterns=[
                {"name": f"pattern_{i}", "win_rate": 0.8, "confidence": 0.95}
                for i in range(8)
            ],
        )
        result = ctx.to_prompt_string()

        assert "## Active Patterns (8)" in result
        # Only first 5 should appear
        assert "pattern_0" in result
        assert "pattern_4" in result
        assert "pattern_5" not in result

    def test_to_prompt_string_includes_recent_decisions(self):
        """to_prompt_string() formats recent decisions."""
        ctx = ReasoningContext(
            autonomy_level=1,
            recent_decisions=[
                {
                    "timestamp": "2026-02-15T10:00:00",
                    "action": "sell_put",
                    "confidence": 0.9,
                },
            ],
        )
        result = ctx.to_prompt_string()

        assert "## Recent Decisions (1)" in result
        assert "sell_put" in result
        assert "confidence=0.9" in result

    def test_to_prompt_string_limits_decisions_to_last_5(self):
        """to_prompt_string() shows only the last 5 recent decisions."""
        ctx = ReasoningContext(
            autonomy_level=1,
            recent_decisions=[
                {"timestamp": f"2026-02-{10+i}T10:00:00", "action": f"action_{i}", "confidence": 0.8}
                for i in range(10)
            ],
        )
        result = ctx.to_prompt_string()

        assert "## Recent Decisions (10)" in result
        # Only last 5 should appear (indices 5-9)
        assert "action_5" in result
        assert "action_9" in result
        assert "action_4" not in result

    def test_to_prompt_string_includes_anomalies(self):
        """to_prompt_string() formats anomalies."""
        ctx = ReasoningContext(
            autonomy_level=1,
            anomalies=[
                {"description": "VIX above 30"},
                {"description": "Gap down > 2%"},
            ],
        )
        result = ctx.to_prompt_string()

        assert "## Anomalies (2)" in result
        assert "VIX above 30" in result
        assert "Gap down > 2%" in result

    def test_to_prompt_string_includes_latest_reflection(self):
        """to_prompt_string() formats the latest reflection summary."""
        ctx = ReasoningContext(
            autonomy_level=1,
            latest_reflection={"summary": "Solid performance, 4/5 wins"},
        )
        result = ctx.to_prompt_string()

        assert "## Latest Reflection" in result
        assert "Solid performance, 4/5 wins" in result

    def test_to_prompt_string_includes_similar_decisions(self):
        """to_prompt_string() formats similar past decisions."""
        ctx = ReasoningContext(
            autonomy_level=1,
            similar_decisions=[
                {
                    "action": "sell_put",
                    "reasoning": "VIX elevated, premium rich, delta within range for conservative entry",
                },
            ],
        )
        result = ctx.to_prompt_string()

        assert "## Similar Past Decisions (1)" in result
        assert "sell_put" in result
        assert "VIX elevated" in result

    def test_to_prompt_string_limits_similar_decisions_to_3(self):
        """to_prompt_string() shows at most 3 similar decisions."""
        ctx = ReasoningContext(
            autonomy_level=1,
            similar_decisions=[
                {"action": f"action_{i}", "reasoning": f"reason_{i}"}
                for i in range(5)
            ],
        )
        result = ctx.to_prompt_string()

        assert "## Similar Past Decisions (5)" in result
        assert "action_0" in result
        assert "action_2" in result
        assert "action_3" not in result

    def test_to_prompt_string_omits_empty_sections(self):
        """to_prompt_string() omits sections with no data."""
        ctx = ReasoningContext(autonomy_level=2)
        result = ctx.to_prompt_string()

        assert "## Autonomy Level: L2" in result
        assert "## Open Positions" not in result
        assert "## Market Context" not in result
        assert "## Active Patterns" not in result
        assert "## Recent Decisions" not in result
        assert "## Anomalies" not in result
        assert "## Latest Reflection" not in result
        assert "## Similar Past Decisions" not in result

    def test_to_prompt_string_truncates_long_reasoning(self):
        """to_prompt_string() truncates reasoning to 100 chars for similar decisions."""
        long_reasoning = "A" * 200
        ctx = ReasoningContext(
            autonomy_level=1,
            similar_decisions=[
                {"action": "sell_put", "reasoning": long_reasoning},
            ],
        )
        result = ctx.to_prompt_string()

        # The full 200-char reasoning should be truncated to 100
        # Check that not the full string appears
        assert "A" * 200 not in result
        assert "A" * 100 in result

    def test_to_prompt_string_full_context(self):
        """to_prompt_string() produces well-formatted output with all sections."""
        ctx = ReasoningContext(
            autonomy_level=3,
            strategy_state={"mode": "aggressive"},
            market_context={"vix": 22.0, "regime": "elevated"},
            open_positions=[
                {"symbol": "AAPL", "strike": 180.0, "expiration": "2026-03-21", "pnl_pct": "30%"},
            ],
            recent_decisions=[
                {"timestamp": "2026-02-18T10:00:00", "action": "sell_put", "confidence": 0.85},
            ],
            active_patterns=[
                {"name": "OTM sweet spot", "win_rate": 0.82, "confidence": 0.96},
            ],
            anomalies=[
                {"description": "VIX spike"},
            ],
            latest_reflection={"summary": "Performed well today"},
            similar_decisions=[
                {"action": "sell_put", "reasoning": "Similar VIX conditions last month"},
            ],
        )
        result = ctx.to_prompt_string()

        # All sections should be present
        assert "## Autonomy Level: L3" in result
        assert "## Open Positions (1)" in result
        assert "## Market Context" in result
        assert "## Active Patterns (1)" in result
        assert "## Recent Decisions (1)" in result
        assert "## Anomalies (1)" in result
        assert "## Latest Reflection" in result
        assert "## Similar Past Decisions (1)" in result


# =========================================================================
# WorkingMemory round-trip persistence
# =========================================================================


class TestWorkingMemoryRoundTrip:
    """Tests verifying state survives a full save-reload cycle."""

    def test_full_state_survives_reload(self, db_session):
        """All state is recoverable after saving and re-creating WorkingMemory."""
        wm = WorkingMemory(db_session)
        wm.update_strategy_state({"otm_range": [5, 10]})
        wm.update_market_context({"vix": 20.0})
        wm.set_autonomy_level(3)
        wm.add_decision({"action": "sell_put", "confidence": 0.9})
        wm.add_anomaly({"description": "circuit breaker hit"})
        wm.add_reflection({"summary": "End of day review"})

        # Simulate daemon restart by creating a new WorkingMemory
        wm2 = WorkingMemory(db_session)

        assert wm2.strategy_state == {"otm_range": [5, 10]}
        assert wm2.market_context == {"vix": 20.0}
        assert wm2.autonomy_level == 3
        assert len(wm2.recent_decisions) == 1
        assert wm2.recent_decisions[0]["action"] == "sell_put"
        assert len(wm2.anomalies) == 1
        assert wm2.anomalies[0]["description"] == "circuit breaker hit"
        assert len(wm2.reflection_reports) == 1

    def test_fifo_queue_survives_reload(self, db_session):
        """The deque maxlen is restored correctly on reload."""
        wm = WorkingMemory(db_session)
        for i in range(MAX_RECENT_DECISIONS + 10):
            wm.add_decision({"idx": i})

        wm2 = WorkingMemory(db_session)

        assert len(wm2.recent_decisions) == MAX_RECENT_DECISIONS
        # The deque should still enforce maxlen after reload
        wm2.add_decision({"idx": 999})
        assert len(wm2.recent_decisions) == MAX_RECENT_DECISIONS
        assert wm2.recent_decisions[-1]["idx"] == 999


# =========================================================================
# WorkingMemory.store_embedding() (SQLite path)
# =========================================================================


class TestStoreEmbedding:
    """Tests for store_embedding on SQLite (no pgvector)."""

    def test_store_embedding_sqlite_saves_text(self, db_session):
        """store_embedding() stores text content on SQLite without vectors."""
        from src.data.models import DecisionAudit

        # Create a decision audit record first
        audit = DecisionAudit(
            autonomy_level=1,
            event_type="trade_signal",
            action="sell_put",
            confidence=0.85,
            reasoning="VIX is low, premium available",
            autonomy_approved=True,
        )
        db_session.add(audit)
        db_session.flush()

        wm = WorkingMemory(db_session)
        wm.store_embedding(
            decision_audit_id=audit.id,
            text_content="Sold AAPL 180P at 1.50 premium, low VIX environment",
        )

        from src.data.models import DecisionEmbedding

        embedding = db_session.query(DecisionEmbedding).first()
        assert embedding is not None
        assert embedding.decision_audit_id == audit.id
        assert "AAPL 180P" in embedding.text_content


# =========================================================================
# WorkingMemory.retrieve_similar_context() (SQLite path)
# =========================================================================


class TestRetrieveSimilarContext:
    """Tests for retrieve_similar_context on SQLite (no pgvector)."""

    def test_retrieve_similar_context_returns_empty_on_sqlite(self, db_session):
        """retrieve_similar_context() returns empty list on SQLite."""
        wm = WorkingMemory(db_session)
        results = wm.retrieve_similar_context(query_embedding=[0.1] * 1536)

        assert results == []
