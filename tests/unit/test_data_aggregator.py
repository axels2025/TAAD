"""Tests for the data aggregator.

Seeds the database with synthetic trades and verifies aggregation
produces correct stats across all methods.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agents.data_aggregator import DataAggregator
from src.agents.models import AnalysisDepth
from src.data.models import (
    Base,
    Experiment,
    LearningHistory,
    Pattern,
    Trade,
    TradeEntrySnapshot,
)


@pytest.fixture
def db_session():
    """Create a fully isolated in-memory database (no global singletons).

    Filters out TAAD models that use PostgreSQL schemas, which are
    incompatible with SQLite.
    """
    engine = create_engine("sqlite:///:memory:")

    # Only create tables without a schema (TAAD uses schema="import" which breaks SQLite)
    tables = [
        t for t in Base.metadata.sorted_tables
        if t.schema is None
    ]
    Base.metadata.create_all(engine, tables=tables)

    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionFactory()

    yield session

    session.close()
    engine.dispose()


@pytest.fixture
def seeded_session(db_session):
    """Seed the database with realistic synthetic trades."""
    now = datetime.now()
    trades = []

    # 20 winning trades
    for i in range(20):
        t = Trade(
            trade_id=f"WIN-{i:03d}",
            symbol=["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"][i % 5],
            strike=150.0 + i,
            expiration=(now + timedelta(days=30)).date(),
            entry_date=now - timedelta(days=60 - i),
            entry_premium=0.50,
            contracts=1,
            exit_date=now - timedelta(days=30 - i),
            exit_premium=0.10,
            exit_reason="profit_target",
            profit_loss=40.0,
            profit_pct=0.80,
            roi=0.035,
            days_held=14,
            otm_pct=0.08,
            dte=21,
            vix_at_entry=[14.0, 18.0, 22.0, 26.0, 16.0][i % 5],
            sector=["Technology", "Technology", "Technology", "Consumer", "Technology"][i % 5],
        )
        trades.append(t)

    # 8 losing trades
    for i in range(8):
        t = Trade(
            trade_id=f"LOSS-{i:03d}",
            symbol=["XOM", "CVX", "META", "TSLA"][i % 4],
            strike=100.0 + i,
            expiration=(now + timedelta(days=30)).date(),
            entry_date=now - timedelta(days=50 - i),
            entry_premium=0.40,
            contracts=1,
            exit_date=now - timedelta(days=20 - i),
            exit_premium=0.80,
            exit_reason="stop_loss",
            profit_loss=-40.0,
            profit_pct=-1.00,
            roi=-0.025,
            days_held=10,
            otm_pct=0.06,
            dte=14,
            vix_at_entry=[25.0, 30.0, 16.0, 20.0][i % 4],
            sector=["Energy", "Energy", "Technology", "Consumer"][i % 4],
        )
        trades.append(t)

    for t in trades:
        db_session.add(t)
    db_session.flush()

    # Add entry snapshots for a subset of trades
    for i, trade in enumerate(trades[:10]):
        snap = TradeEntrySnapshot(
            trade_id=trade.id,
            symbol=trade.symbol,
            strike=trade.strike,
            expiration=trade.expiration,
            option_type="PUT",
            entry_premium=trade.entry_premium,
            stock_price=trade.strike * 1.1,
            dte=trade.dte,
            contracts=1,
            captured_at=trade.entry_date,
            delta=-0.12 - (i * 0.02),
            iv=0.30 + (i * 0.02),
            vix=trade.vix_at_entry,
            rsi_14=45.0 + i * 3,
            trend_direction=["uptrend", "sideways", "uptrend", "downtrend", "uptrend"][i % 5],
            vol_regime=["normal", "elevated", "low", "normal", "elevated"][i % 5],
            sector=trade.sector,
        )
        db_session.add(snap)

    # Add a pattern
    pattern = Pattern(
        pattern_type="delta_bucket",
        pattern_name="delta_15_20_outperforms",
        pattern_value="15-20%",
        sample_size=38,
        win_rate=0.82,
        avg_roi=0.038,
        confidence=0.91,
        p_value=0.012,
        date_detected=now - timedelta(days=7),
        status="active",
    )
    db_session.add(pattern)

    # Add an experiment
    experiment = Experiment(
        experiment_id="EXP-001",
        name="Test wider delta range",
        parameter_name="delta_range",
        control_value="(0.10, 0.20)",
        test_value="(0.10, 0.25)",
        status="active",
        start_date=now - timedelta(days=30),
        control_trades=15,
        test_trades=12,
    )
    db_session.add(experiment)

    # Add a learning event
    event = LearningHistory(
        event_type="parameter_adjusted",
        event_date=now - timedelta(days=5),
        pattern_name="high_iv_outperforms",
        confidence=0.88,
        sample_size=29,
        parameter_changed="preferred_iv_regime",
        old_value="any",
        new_value="high_iv",
        reasoning="High IV trades show 3.5% ROI vs 2.8% baseline",
        expected_improvement=0.007,
    )
    db_session.add(event)

    db_session.commit()

    return db_session


class TestPerformanceSummary:
    """Tests for performance summary computation."""

    def test_basic_stats(self, seeded_session):
        agg = DataAggregator(seeded_session)
        context = agg.build_context(days=90, depth=AnalysisDepth.QUICK)
        perf = context.performance

        assert perf.total_trades == 28
        assert perf.win_rate == pytest.approx(20 / 28, abs=0.01)
        assert perf.total_pnl == pytest.approx(20 * 40.0 + 8 * (-40.0))

    def test_avg_roi(self, seeded_session):
        agg = DataAggregator(seeded_session)
        context = agg.build_context(days=90)
        perf = context.performance

        expected_roi = (20 * 0.035 + 8 * (-0.025)) / 28
        assert perf.avg_roi == pytest.approx(expected_roi, abs=0.001)

    def test_max_drawdown_negative(self, seeded_session):
        agg = DataAggregator(seeded_session)
        context = agg.build_context(days=90)

        # Drawdown should be zero or negative
        assert context.performance.max_drawdown <= 0

    def test_empty_database(self, db_session):
        agg = DataAggregator(db_session)
        context = agg.build_context(days=90)

        assert context.performance.total_trades == 0
        assert context.performance.win_rate == 0.0
        assert context.performance.total_pnl == 0.0

    def test_recent_window(self, seeded_session):
        agg = DataAggregator(seeded_session)
        context = agg.build_context(days=90)
        perf = context.performance

        # Recent trades should be a subset of total
        assert perf.recent_trades <= perf.total_trades


class TestPatterns:
    """Tests for pattern aggregation."""

    def test_patterns_loaded(self, seeded_session):
        agg = DataAggregator(seeded_session)
        context = agg.build_context(days=90, depth=AnalysisDepth.STANDARD)

        assert len(context.patterns) >= 1
        p = context.patterns[0]
        assert p.pattern_name == "delta_15_20_outperforms"
        assert p.sample_size == 38
        assert p.win_rate == 0.82

    def test_pattern_limit_by_depth(self, seeded_session):
        agg = DataAggregator(seeded_session)

        quick = agg.build_context(days=90, depth=AnalysisDepth.QUICK)
        assert len(quick.patterns) <= 5

        standard = agg.build_context(days=90, depth=AnalysisDepth.STANDARD)
        assert len(standard.patterns) <= 20


class TestBreakdowns:
    """Tests for dimensional breakdowns."""

    def test_sector_breakdown(self, seeded_session):
        agg = DataAggregator(seeded_session)
        context = agg.build_context(days=90, depth=AnalysisDepth.STANDARD)

        sector_bd = next(
            (bd for bd in context.breakdowns if bd.dimension == "sector"), None
        )
        assert sector_bd is not None
        assert len(sector_bd.buckets) > 0

        # Check bucket structure
        for bucket in sector_bd.buckets:
            assert "label" in bucket
            assert "trades" in bucket
            assert "win_rate" in bucket
            assert "avg_roi" in bucket
            assert 0.0 <= bucket["win_rate"] <= 1.0

    def test_dimension_count_by_depth(self, seeded_session):
        agg = DataAggregator(seeded_session)

        quick = agg.build_context(days=90, depth=AnalysisDepth.QUICK)
        # Quick has 2 dimensions max, but we may have fewer if data is sparse
        assert len(quick.breakdowns) <= 2

        standard = agg.build_context(days=90, depth=AnalysisDepth.STANDARD)
        assert len(standard.breakdowns) <= 4

    def test_small_buckets_filtered(self, seeded_session):
        agg = DataAggregator(seeded_session)
        context = agg.build_context(days=90, depth=AnalysisDepth.STANDARD)

        for bd in context.breakdowns:
            for bucket in bd.buckets:
                # Buckets with fewer than 3 trades should be filtered out
                assert bucket["trades"] >= 3


class TestExperiments:
    """Tests for experiment summaries."""

    def test_active_experiments_included(self, seeded_session):
        agg = DataAggregator(seeded_session)
        context = agg.build_context(days=90)

        assert len(context.experiments) >= 1
        exp = context.experiments[0]
        assert exp.experiment_id == "EXP-001"
        assert exp.status == "active"
        assert exp.control_trades == 15


class TestLearningEvents:
    """Tests for recent learning events."""

    def test_events_included(self, seeded_session):
        agg = DataAggregator(seeded_session)
        context = agg.build_context(days=90)

        assert len(context.recent_learning_events) >= 1
        event = context.recent_learning_events[0]
        assert event["type"] == "parameter_adjusted"
        assert event["parameter"] == "preferred_iv_regime"


class TestProposals:
    """Tests for optimizer proposal summaries."""

    def test_proposals_from_learning_history(self, seeded_session):
        agg = DataAggregator(seeded_session)
        context = agg.build_context(days=90)

        # We seeded a parameter_adjusted event which serves as proposal source
        assert len(context.proposals) >= 1
        prop = context.proposals[0]
        assert prop.parameter == "preferred_iv_regime"


class TestContextBuilding:
    """Tests for the full context building pipeline."""

    def test_user_question_preserved(self, seeded_session):
        agg = DataAggregator(seeded_session)
        context = agg.build_context(
            days=90,
            user_question="Why are my Energy trades bad?",
        )
        assert context.user_question == "Why are my Energy trades bad?"

    def test_depth_preserved(self, seeded_session):
        agg = DataAggregator(seeded_session)
        context = agg.build_context(days=90, depth=AnalysisDepth.DEEP)
        assert context.depth == AnalysisDepth.DEEP

    def test_analysis_period_preserved(self, seeded_session):
        agg = DataAggregator(seeded_session)
        context = agg.build_context(days=180)
        assert context.analysis_period_days == 180
