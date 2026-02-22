"""Live integration test for AI performance analysis.

Calls the real Claude API — requires ANTHROPIC_API_KEY in .env.
Run with: pytest tests/integration/test_ai_analysis_live.py -v -s
"""

import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from anthropic import BadRequestError
from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agents.data_aggregator import DataAggregator
from src.agents.models import AnalysisDepth
from src.agents.performance_analyzer import PerformanceAnalyzer
from src.data.models import (
    Base,
    Experiment,
    LearningHistory,
    Pattern,
    Trade,
    TradeEntrySnapshot,
)


def _load_real_api_key() -> str:
    """Read the real API key directly from .env file, bypassing os.environ."""
    env_path = Path(__file__).parent.parent.parent / ".env"
    if not env_path.exists():
        return ""
    values = dotenv_values(env_path)
    return values.get("ANTHROPIC_API_KEY", "")


_REAL_API_KEY = _load_real_api_key()


@pytest.fixture(autouse=True)
def _require_real_api_key():
    """Skip tests if real API key is not available."""
    if not _REAL_API_KEY or not _REAL_API_KEY.startswith("sk-ant-api"):
        pytest.skip("Real ANTHROPIC_API_KEY not available in .env")


@pytest.fixture
def seeded_session():
    """Create an in-memory DB with realistic synthetic trades."""
    engine = create_engine("sqlite:///:memory:")
    tables = [t for t in Base.metadata.sorted_tables if t.schema is None]
    Base.metadata.create_all(engine, tables=tables)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()

    now = datetime.now()

    # 25 winning trades across sectors
    symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "JPM", "V", "UNH", "PG"]
    sectors = [
        "Technology", "Technology", "Technology", "Consumer Discretionary",
        "Technology", "Technology", "Financials", "Financials",
        "Healthcare", "Consumer Staples",
    ]
    for i in range(25):
        t = Trade(
            trade_id=f"WIN-{i:03d}",
            symbol=symbols[i % len(symbols)],
            strike=150.0 + i * 2,
            expiration=(now + timedelta(days=30)).date(),
            entry_date=now - timedelta(days=80 - i * 2),
            entry_premium=0.55 + (i % 5) * 0.05,
            contracts=1,
            exit_date=now - timedelta(days=50 - i * 2),
            exit_premium=0.10 + (i % 3) * 0.05,
            exit_reason="profit_target",
            profit_loss=35.0 + (i % 7) * 5,
            profit_pct=0.75,
            roi=0.032 + (i % 5) * 0.003,
            days_held=14 + (i % 7),
            otm_pct=0.07 + (i % 4) * 0.01,
            dte=21 + (i % 10),
            vix_at_entry=[14.5, 17.2, 21.3, 24.8, 16.1, 19.5, 22.7, 13.8, 18.4, 25.2][i % 10],
            sector=sectors[i % len(sectors)],
        )
        session.add(t)

    # 10 losing trades
    loss_symbols = ["XOM", "CVX", "BA", "INTC", "T"]
    loss_sectors = ["Energy", "Energy", "Industrials", "Technology", "Communication"]
    for i in range(10):
        t = Trade(
            trade_id=f"LOSS-{i:03d}",
            symbol=loss_symbols[i % len(loss_symbols)],
            strike=100.0 + i * 3,
            expiration=(now + timedelta(days=30)).date(),
            entry_date=now - timedelta(days=70 - i * 3),
            entry_premium=0.40,
            contracts=1,
            exit_date=now - timedelta(days=40 - i * 3),
            exit_premium=0.85,
            exit_reason="stop_loss",
            profit_loss=-45.0 - (i % 4) * 10,
            profit_pct=-1.1,
            roi=-0.028 - (i % 3) * 0.005,
            days_held=8 + (i % 5),
            otm_pct=0.05,
            dte=14 + (i % 7),
            vix_at_entry=[26.0, 30.5, 18.0, 22.0, 28.0][i % 5],
            sector=loss_sectors[i % len(loss_sectors)],
        )
        session.add(t)

    session.flush()

    # Entry snapshots for first 15 trades
    all_trades = session.query(Trade).all()
    for i, trade in enumerate(all_trades[:15]):
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
            delta=-0.12 - (i * 0.015),
            iv=0.28 + (i * 0.015),
            vix=trade.vix_at_entry,
            rsi_14=40.0 + i * 2.5,
            trend_direction=["uptrend", "sideways", "uptrend", "downtrend", "uptrend"][i % 5],
            vol_regime=["normal", "elevated", "low", "normal", "elevated"][i % 5],
            sector=trade.sector,
        )
        session.add(snap)

    # Patterns
    patterns = [
        Pattern(
            pattern_type="delta_bucket", pattern_name="delta_15_20_outperforms",
            pattern_value="15-20%", sample_size=38, win_rate=0.82,
            avg_roi=0.038, confidence=0.91, p_value=0.012,
            date_detected=now - timedelta(days=7), status="active",
        ),
        Pattern(
            pattern_type="sector", pattern_name="sector_technology_outperforms",
            pattern_value="Technology", sample_size=42, win_rate=0.79,
            avg_roi=0.034, confidence=0.87, p_value=0.025,
            date_detected=now - timedelta(days=7), status="active",
        ),
        Pattern(
            pattern_type="sector", pattern_name="sector_energy_underperforms",
            pattern_value="Energy", sample_size=12, win_rate=0.42,
            avg_roi=0.008, confidence=0.78, p_value=0.031,
            date_detected=now - timedelta(days=7), status="active",
        ),
        Pattern(
            pattern_type="vix_regime", pattern_name="elevated_vix_outperforms",
            pattern_value="20-25", sample_size=29, win_rate=0.79,
            avg_roi=0.035, confidence=0.85, p_value=0.028,
            date_detected=now - timedelta(days=5), status="active",
        ),
    ]
    for p in patterns:
        session.add(p)

    # Experiment
    session.add(Experiment(
        experiment_id="EXP-001", name="Test wider delta range",
        parameter_name="delta_range", control_value="(0.10, 0.20)",
        test_value="(0.10, 0.25)", status="active",
        start_date=now - timedelta(days=30),
        control_trades=18, test_trades=14,
    ))

    # Learning event
    session.add(LearningHistory(
        event_type="parameter_adjusted",
        event_date=now - timedelta(days=5),
        pattern_name="elevated_vix_outperforms",
        confidence=0.85, sample_size=29,
        parameter_changed="preferred_vix_regime",
        old_value="any", new_value="elevated",
        reasoning="Elevated VIX trades show 3.5% ROI vs 2.8% baseline",
        expected_improvement=0.007,
    ))

    session.commit()
    yield session
    session.close()
    engine.dispose()


def _call_with_retry(analyzer, context, max_retries=3, base_wait=15):
    """Call analyzer.analyze with retries on transient API errors."""
    for attempt in range(1, max_retries + 1):
        try:
            return analyzer.analyze(context)
        except BadRequestError as e:
            if "credit balance" in str(e) and attempt < max_retries:
                wait = base_wait * attempt
                print(f"\n  Throttled (attempt {attempt}/{max_retries}), waiting {wait}s...")
                time.sleep(wait)
            else:
                raise


class TestLiveQuickAnalysis:
    """Quick analysis using Haiku — cheapest, fastest."""

    def test_quick_analysis_returns_insights(self, seeded_session):
        """End-to-end: aggregate -> prompt -> Claude API -> parse."""
        time.sleep(10)  # Avoid throttling from rapid API calls

        aggregator = DataAggregator(seeded_session)
        context = aggregator.build_context(days=90, depth=AnalysisDepth.QUICK)

        assert context.performance.total_trades == 35
        assert len(context.patterns) <= 5

        analyzer = PerformanceAnalyzer(depth=AnalysisDepth.QUICK, api_key=_REAL_API_KEY)
        report = _call_with_retry(analyzer, context)

        # Verify structure
        assert report.narrative, "Narrative should not be empty"
        assert len(report.narrative) > 50, "Narrative should be substantive"
        assert report.model_used == "claude-haiku-4-5-20251001"
        assert report.input_tokens > 0
        assert report.output_tokens > 0
        assert report.cost_estimate > 0

        # Verify insights parsed
        assert len(report.insights) >= 1, "Should have at least 1 insight"
        for insight in report.insights:
            assert insight.category in ("recommendation", "risk", "hypothesis", "observation")
            assert insight.title
            assert insight.body
            assert insight.confidence in ("high", "medium", "low")

        print(f"\n{'='*60}")
        print(f"QUICK ANALYSIS (Haiku)")
        print(f"{'='*60}")
        print(f"Tokens: {report.input_tokens} in / {report.output_tokens} out")
        print(f"Cost: ${report.cost_estimate:.4f}")
        print(f"Insights: {len(report.insights)}")
        print(f"\nNarrative:\n{report.narrative[:500]}")
        for i, insight in enumerate(report.insights[:3]):
            print(f"\n--- Insight #{i+1}: [{insight.category}] {insight.title}")
            print(f"    {insight.body[:200]}")


class TestLiveStandardAnalysis:
    """Standard analysis using Sonnet — the weekly review default."""

    def test_standard_analysis_with_question(self, seeded_session):
        """End-to-end with a user question."""
        time.sleep(15)  # Longer wait after first test to avoid throttling

        aggregator = DataAggregator(seeded_session)
        context = aggregator.build_context(
            days=90,
            depth=AnalysisDepth.STANDARD,
            user_question="Why are my Energy sector trades underperforming?",
        )

        assert context.performance.total_trades == 35
        assert context.user_question is not None
        assert len(context.breakdowns) > 0

        analyzer = PerformanceAnalyzer(depth=AnalysisDepth.STANDARD, api_key=_REAL_API_KEY)
        report = _call_with_retry(analyzer, context)

        assert report.narrative
        assert len(report.insights) >= 1
        assert report.model_used == "claude-sonnet-4-5-20250929"
        assert report.cost_estimate > 0

        # The response should address the Energy question
        full_text = report.narrative + " ".join(i.body for i in report.insights)
        assert "energy" in full_text.lower(), (
            "Response should address the Energy question"
        )

        print(f"\n{'='*60}")
        print(f"STANDARD ANALYSIS (Sonnet) — with question")
        print(f"{'='*60}")
        print(f"Tokens: {report.input_tokens} in / {report.output_tokens} out")
        print(f"Cost: ${report.cost_estimate:.4f}")
        print(f"Insights: {len(report.insights)}")
        print(f"  Recommendations: {len(report.recommendations)}")
        print(f"  Risks: {len(report.risks)}")
        print(f"  Hypotheses: {len(report.hypotheses)}")
        print(f"\nNarrative:\n{report.narrative[:800]}")
        for i, insight in enumerate(report.insights):
            print(f"\n--- [{insight.confidence}] #{insight.priority} "
                  f"[{insight.category}] {insight.title}")
            print(f"    {insight.body[:300]}")
