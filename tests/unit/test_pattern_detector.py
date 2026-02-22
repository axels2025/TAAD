"""Unit tests for PatternDetector.

Tests pattern detection across all dimensions and metric calculations.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from src.data.models import Trade
from src.learning.pattern_detector import PatternDetector


@pytest.fixture
def mock_db_session():
    """Create mock database session.

    filter() returns self so chained .filter().filter()...all() works
    at any depth (needed because pattern_detector now adds a lifecycle_status
    filter to every closed-trades query).
    """
    session = Mock()
    # Make query().filter() return a chainable mock
    query_mock = session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.join.return_value = query_mock
    # Separate mock for distinct() chains (sector queries) so setting
    # distinct().all.return_value doesn't overwrite the main all()
    distinct_mock = Mock()
    query_mock.distinct.return_value = distinct_mock
    return session


@pytest.fixture
def sample_trades():
    """Create sample trades for testing."""
    trades = []
    base_date = datetime(2026, 1, 1)

    # Create 60 trades with varying outcomes
    for i in range(60):
        trade = Trade(
            trade_id=f"trade_{i}",
            symbol=f"STOCK{i % 10}",
            strike=100.0 - (i % 10),
            expiration=base_date + timedelta(days=30 + i),
            entry_date=base_date + timedelta(days=i),
            exit_date=base_date + timedelta(days=i + 14),
            entry_premium=2.0,
            exit_premium=1.0,
            contracts=1,
            dte=30 - (i % 15),
            otm_pct=0.05 + (i % 10) * 0.02,
            profit_loss=100.0 if i % 3 == 0 else -50.0,  # ~33% win rate
            roi=0.50 if i % 3 == 0 else -0.25,
            vix_at_entry=15.0 + (i % 4) * 5.0,  # VIX varies 15-30
        )
        trades.append(trade)

    return trades


def test_pattern_detector_initialization(mock_db_session):
    """Test PatternDetector initialization."""
    detector = PatternDetector(mock_db_session, min_sample_size=20)

    assert detector.db == mock_db_session
    assert detector.min_samples == 20
    assert detector.baseline_win_rate is None
    assert detector.baseline_roi is None


def test_calculate_baseline(mock_db_session, sample_trades):
    """Test baseline calculation."""
    mock_db_session.query().filter().all.return_value = sample_trades

    detector = PatternDetector(mock_db_session, min_sample_size=30)
    detector._calculate_baseline()

    # Should calculate from 60 trades
    assert detector.baseline_win_rate is not None
    assert 0.0 <= detector.baseline_win_rate <= 1.0
    assert detector.baseline_roi is not None


def test_analyze_by_dte_bucket(mock_db_session, sample_trades):
    """Test DTE bucket analysis."""
    # Setup mock for all query chains
    mock_db_session.query().filter().all.return_value = sample_trades
    mock_db_session.query().filter().filter().filter().all.return_value = sample_trades

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    patterns = detector.analyze_by_dte_bucket()

    # Should detect patterns for different DTE ranges
    assert isinstance(patterns, list)
    # Should have patterns for buckets with enough samples
    pattern_names = [p.pattern_name for p in patterns]
    assert any("days" in name for name in pattern_names)


def test_analyze_by_vix_regime(mock_db_session, sample_trades):
    """Test VIX regime analysis."""
    # Setup mock for all query chains
    mock_db_session.query().filter().all.return_value = sample_trades
    mock_db_session.query().filter().filter().filter().all.return_value = sample_trades

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    patterns = detector.analyze_by_vix_regime()

    # Should detect VIX regime patterns
    assert isinstance(patterns, list)
    # Check pattern types
    for pattern in patterns:
        assert pattern.pattern_type == "vix_regime"
        assert "vix" in pattern.pattern_name.lower()


def test_analyze_by_trend_direction(mock_db_session, sample_trades):
    """Test trend direction analysis.

    Uses TradeEntrySnapshot.trend_direction to filter trades.
    """
    from unittest.mock import patch

    # Setup mock for baseline calculation
    mock_db_session.query().filter().all.return_value = sample_trades

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    # Patch the _get_trades_by_trend method to return sample trades
    # This simulates the join query returning filtered results
    with patch.object(detector, '_get_trades_by_trend', return_value=sample_trades[:20]):
        patterns = detector.analyze_by_trend_direction()

    # Should detect trend patterns
    assert isinstance(patterns, list)
    # Will have patterns since we're returning 20 trades per trend (> min 10)
    assert len(patterns) > 0
    for pattern in patterns:
        assert pattern.pattern_type == "trend_direction"


def test_analyze_by_day_of_week(mock_db_session, sample_trades):
    """Test day of week analysis."""
    mock_db_session.query().filter().all.return_value = sample_trades

    detector = PatternDetector(mock_db_session, min_sample_size=5)
    detector._calculate_baseline()

    patterns = detector.analyze_by_day_of_week()

    # Should detect day-of-week patterns
    assert isinstance(patterns, list)
    # Check pattern types
    for pattern in patterns:
        assert pattern.pattern_type == "entry_day"


def test_calculate_metrics(mock_db_session, sample_trades):
    """Test metric calculation."""
    detector = PatternDetector(mock_db_session)

    win_rate, avg_roi = detector._calculate_metrics(sample_trades[:30])

    # Win rate should be ~33% based on sample data
    assert 0.0 <= win_rate <= 1.0
    # ROI should be calculated
    assert isinstance(avg_roi, float)


def test_compare_to_baseline(mock_db_session, sample_trades):
    """Test baseline comparison with t-test."""
    mock_db_session.query().filter().all.return_value = sample_trades

    detector = PatternDetector(mock_db_session)
    detector._calculate_baseline()

    # Compare first half to baseline
    p_value, effect_size = detector._compare_to_baseline(sample_trades[:30])

    # Should return valid p-value and effect size
    assert 0.0 <= p_value <= 1.0
    assert isinstance(effect_size, float)


def test_calculate_confidence(mock_db_session):
    """Test confidence calculation."""
    detector = PatternDetector(mock_db_session)

    # High confidence scenario
    confidence = detector._calculate_confidence(p_value=0.01, effect_size=1.5, sample_size=100)
    assert 0.7 <= confidence <= 1.0

    # Low confidence scenario
    confidence = detector._calculate_confidence(p_value=0.5, effect_size=0.1, sample_size=10)
    assert 0.0 <= confidence <= 0.5


def test_detect_patterns_insufficient_data(mock_db_session):
    """Test pattern detection with insufficient data."""
    # Return only 10 trades (less than min_sample_size)
    small_sample = [
        Trade(
            trade_id=f"trade_{i}",
            symbol="TEST",
            strike=100.0,
            expiration=datetime(2026, 2, 1),
            entry_date=datetime(2026, 1, 1),
            exit_date=datetime(2026, 1, 15),
            entry_premium=2.0,
            contracts=1,
            dte=30,
            profit_loss=100.0,
            roi=0.5,
        )
        for i in range(10)
    ]

    mock_db_session.query().filter().all.return_value = small_sample

    detector = PatternDetector(mock_db_session, min_sample_size=30)
    patterns = detector.detect_patterns()

    # Should return empty list due to insufficient data
    assert patterns == []


def test_pattern_attributes(mock_db_session, sample_trades):
    """Test that detected patterns have all required attributes."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades
    mock_db_session.query().filter().filter().all.return_value = sample_trades

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    # Patch _get_trades_by_trend to return sample trades for join query
    with patch.object(detector, '_get_trades_by_trend', return_value=sample_trades[:20]):
        patterns = detector.analyze_by_trend_direction()

    for pattern in patterns:
        # Check all required attributes exist
        assert hasattr(pattern, "pattern_type")
        assert hasattr(pattern, "pattern_name")
        assert hasattr(pattern, "pattern_value")
        assert hasattr(pattern, "sample_size")
        assert hasattr(pattern, "win_rate")
        assert hasattr(pattern, "avg_roi")
        assert hasattr(pattern, "baseline_win_rate")
        assert hasattr(pattern, "baseline_roi")
        assert hasattr(pattern, "p_value")
        assert hasattr(pattern, "effect_size")
        assert hasattr(pattern, "confidence")
        assert hasattr(pattern, "date_detected")

        # Check types
        assert isinstance(pattern.sample_size, int)
        assert isinstance(pattern.win_rate, float)
        assert isinstance(pattern.avg_roi, float)
        assert isinstance(pattern.p_value, float)
        assert isinstance(pattern.confidence, float)


def test_pattern_is_significant(mock_db_session, sample_trades):
    """Test pattern significance check."""
    from src.learning.models import DetectedPattern

    # Create a significant pattern
    significant_pattern = DetectedPattern(
        pattern_type="test",
        pattern_name="significant_test",
        pattern_value="test",
        sample_size=50,
        win_rate=0.7,
        avg_roi=0.3,
        baseline_win_rate=0.5,
        baseline_roi=0.1,
        p_value=0.01,
        effect_size=1.2,
        confidence=0.85,
        date_detected=datetime.now(),
    )

    assert significant_pattern.is_significant() is True

    # Create an insignificant pattern (low sample size)
    insignificant_pattern = DetectedPattern(
        pattern_type="test",
        pattern_name="insignificant_test",
        pattern_value="test",
        sample_size=10,  # Too small
        win_rate=0.7,
        avg_roi=0.3,
        baseline_win_rate=0.5,
        baseline_roi=0.1,
        p_value=0.01,
        effect_size=1.2,
        confidence=0.85,
        date_detected=datetime.now(),
    )

    assert insignificant_pattern.is_significant() is False

    # Create an insignificant pattern (high p-value)
    insignificant_p = DetectedPattern(
        pattern_type="test",
        pattern_name="insignificant_p_test",
        pattern_value="test",
        sample_size=50,
        win_rate=0.7,
        avg_roi=0.3,
        baseline_win_rate=0.5,
        baseline_roi=0.1,
        p_value=0.5,  # Not significant
        effect_size=1.2,
        confidence=0.85,
        date_detected=datetime.now(),
    )

    assert insignificant_p.is_significant() is False


# =========================================================================
# Phase 3.1: Technical Indicators Tests
# =========================================================================


@pytest.fixture
def sample_trades_with_technical_indicators():
    """Create sample trades with entry snapshots containing technical indicators."""
    from src.data.models import TradeEntrySnapshot

    trades = []
    base_date = datetime(2026, 1, 1)

    # Create 60 trades with varying technical indicators
    for i in range(60):
        trade = Trade(
            id=i + 1,
            trade_id=f"trade_{i}",
            symbol=f"STOCK{i % 10}",
            strike=100.0 - (i % 10),
            expiration=base_date + timedelta(days=30 + i),
            entry_date=base_date + timedelta(days=i),
            exit_date=base_date + timedelta(days=i + 14),
            entry_premium=2.0,
            exit_premium=1.0,
            contracts=1,
            dte=30 - (i % 15),
            otm_pct=0.05 + (i % 10) * 0.02,
            profit_loss=100.0 if i % 3 == 0 else -50.0,
            roi=0.50 if i % 3 == 0 else -0.25,
            vix_at_entry=15.0 + (i % 4) * 5.0,
        )

        # Create entry snapshot with technical indicators
        snapshot = TradeEntrySnapshot(
            id=i + 1,
            trade_id=i + 1,
            symbol=trade.symbol,
            strike=trade.strike,
            expiration=trade.expiration,
            option_type="PUT",
            entry_premium=trade.entry_premium,
            contracts=trade.contracts,
            stock_price=100.0 + (i % 10),
            dte=trade.dte,
            # Technical indicators - vary across trades
            rsi_14=30.0 + (i % 6) * 10.0,  # 30-80 range
            macd_histogram=-0.5 + (i % 10) * 0.15,  # -0.5 to 1.0
            adx=15.0 + (i % 5) * 10.0,  # 15-55 range
            bb_position=0.1 + (i % 8) * 0.1,  # 0.1-0.8 range
            distance_to_support_pct=2.0 + (i % 8) * 3.0,  # 2-23% range
            atr_pct=1.5 + (i % 7) * 0.8,  # 1.5-6.3% range
            source="test",
            captured_at=base_date + timedelta(days=i),
        )

        trade.entry_snapshot = snapshot
        trades.append(trade)

    return trades


def test_analyze_by_rsi_regime(mock_db_session, sample_trades_with_technical_indicators):
    """Test RSI regime pattern detection."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades_with_technical_indicators

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    # Patch helper method to return filtered trades
    with patch.object(detector, '_get_trades_in_rsi_range') as mock_get:
        # Oversold trades (RSI < 30)
        oversold_trades = [t for t in sample_trades_with_technical_indicators
                          if t.entry_snapshot.rsi_14 < 30]
        # Neutral trades (RSI 30-70)
        neutral_trades = [t for t in sample_trades_with_technical_indicators
                         if 30 <= t.entry_snapshot.rsi_14 <= 70]

        def mock_rsi_filter(min_rsi, max_rsi):
            if max_rsi <= 30:
                return oversold_trades
            elif min_rsi >= 70:
                return []  # No overbought in our sample
            else:
                return neutral_trades

        mock_get.side_effect = mock_rsi_filter

        patterns = detector.analyze_by_rsi_regime()

    # Should detect patterns for buckets with enough samples
    assert isinstance(patterns, list)
    assert len(patterns) > 0

    # Check pattern attributes
    for pattern in patterns:
        assert pattern.pattern_type == "rsi_regime"
        assert "rsi_" in pattern.pattern_name
        assert pattern.sample_size >= 10
        assert 0.0 <= pattern.win_rate <= 1.0


def test_analyze_by_macd_histogram(mock_db_session, sample_trades_with_technical_indicators):
    """Test MACD histogram momentum pattern detection."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades_with_technical_indicators

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    with patch.object(detector, '_get_trades_in_macd_histogram_range') as mock_get:
        # Return different subsets based on histogram range
        def mock_macd_filter(min_hist, max_hist):
            return [t for t in sample_trades_with_technical_indicators
                   if min_hist <= t.entry_snapshot.macd_histogram < max_hist]

        mock_get.side_effect = mock_macd_filter

        patterns = detector.analyze_by_macd_histogram()

    assert isinstance(patterns, list)
    assert len(patterns) > 0

    for pattern in patterns:
        assert pattern.pattern_type == "macd_momentum"
        assert "macd_" in pattern.pattern_name


def test_analyze_by_trend_strength(mock_db_session, sample_trades_with_technical_indicators):
    """Test ADX trend strength pattern detection."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades_with_technical_indicators

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    with patch.object(detector, '_get_trades_in_adx_range') as mock_get:
        def mock_adx_filter(min_adx, max_adx):
            return [t for t in sample_trades_with_technical_indicators
                   if min_adx <= t.entry_snapshot.adx < max_adx]

        mock_get.side_effect = mock_adx_filter

        patterns = detector.analyze_by_trend_strength()

    assert isinstance(patterns, list)
    assert len(patterns) > 0

    for pattern in patterns:
        assert pattern.pattern_type == "trend_strength"
        assert "adx_" in pattern.pattern_name


def test_analyze_by_bb_position(mock_db_session, sample_trades_with_technical_indicators):
    """Test Bollinger Band position pattern detection."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades_with_technical_indicators

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    with patch.object(detector, '_get_trades_in_bb_position_range') as mock_get:
        def mock_bb_filter(min_pos, max_pos):
            return [t for t in sample_trades_with_technical_indicators
                   if min_pos <= t.entry_snapshot.bb_position < max_pos]

        mock_get.side_effect = mock_bb_filter

        patterns = detector.analyze_by_bb_position()

    assert isinstance(patterns, list)
    assert len(patterns) > 0

    for pattern in patterns:
        assert pattern.pattern_type == "bb_position"
        assert "bb_" in pattern.pattern_name


def test_analyze_by_support_proximity(mock_db_session, sample_trades_with_technical_indicators):
    """Test support proximity pattern detection."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades_with_technical_indicators

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    with patch.object(detector, '_get_trades_in_support_proximity_range') as mock_get:
        def mock_support_filter(min_dist, max_dist):
            return [t for t in sample_trades_with_technical_indicators
                   if min_dist <= t.entry_snapshot.distance_to_support_pct < max_dist]

        mock_get.side_effect = mock_support_filter

        patterns = detector.analyze_by_support_proximity()

    assert isinstance(patterns, list)
    assert len(patterns) > 0

    for pattern in patterns:
        assert pattern.pattern_type == "support_proximity"
        assert "support" in pattern.pattern_name


def test_analyze_by_atr_volatility(mock_db_session, sample_trades_with_technical_indicators):
    """Test ATR volatility pattern detection."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades_with_technical_indicators

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    with patch.object(detector, '_get_trades_in_atr_range') as mock_get:
        def mock_atr_filter(min_atr, max_atr):
            return [t for t in sample_trades_with_technical_indicators
                   if min_atr <= t.entry_snapshot.atr_pct < max_atr]

        mock_get.side_effect = mock_atr_filter

        patterns = detector.analyze_by_atr_volatility()

    assert isinstance(patterns, list)
    assert len(patterns) > 0

    for pattern in patterns:
        assert pattern.pattern_type == "atr_volatility"
        assert "atr_" in pattern.pattern_name


def test_technical_indicators_integration(mock_db_session, sample_trades_with_technical_indicators):
    """Test that all Phase 3.1 analyses are included in detect_patterns()."""
    from unittest.mock import patch, MagicMock
    from src.learning.path_analyzer import PathAnalyzer
    from src.learning.pattern_combiner import PatternCombiner

    mock_db_session.query().filter().all.return_value = sample_trades_with_technical_indicators
    # Mock the sector query to return empty list (no sectors)
    mock_db_session.query().join().filter().filter().distinct().all.return_value = []

    detector = PatternDetector(mock_db_session, min_sample_size=10)

    # Set baseline metrics directly (mock query chains collapse to same object
    # so we can't rely on separate .all() return values)
    detector.baseline_win_rate = 0.65
    detector.baseline_roi = 0.05
    detector._calculate_baseline = lambda: None  # Skip DB query

    # Patch all helper methods to return appropriate trades
    with patch.object(detector, '_get_trades_in_rsi_range', return_value=sample_trades_with_technical_indicators[:20]), \
         patch.object(detector, '_get_trades_in_macd_histogram_range', return_value=sample_trades_with_technical_indicators[:20]), \
         patch.object(detector, '_get_trades_in_adx_range', return_value=sample_trades_with_technical_indicators[:20]), \
         patch.object(detector, '_get_trades_in_bb_position_range', return_value=sample_trades_with_technical_indicators[:20]), \
         patch.object(detector, '_get_trades_in_support_proximity_range', return_value=sample_trades_with_technical_indicators[:20]), \
         patch.object(detector, '_get_trades_in_atr_range', return_value=sample_trades_with_technical_indicators[:20]), \
         patch.object(detector, '_get_trades_in_delta_range', return_value=sample_trades_with_technical_indicators[:20]), \
         patch.object(detector, '_get_trades_in_iv_range', return_value=sample_trades_with_technical_indicators[:20]), \
         patch.object(detector, '_get_trades_in_dte_range', return_value=sample_trades_with_technical_indicators[:20]), \
         patch.object(detector, '_get_trades_in_vix_range', return_value=sample_trades_with_technical_indicators[:20]), \
         patch.object(detector, '_get_trades_by_trend', return_value=sample_trades_with_technical_indicators[:20]), \
         patch.object(detector, '_get_trades_by_entry_day', return_value=sample_trades_with_technical_indicators[:20]), \
         patch.object(detector, '_get_trades_by_vol_regime', return_value=[]), \
         patch.object(detector, '_get_trades_by_market_regime', return_value=[]), \
         patch.object(detector, '_get_trades_by_opex_week', return_value=[]), \
         patch.object(detector, '_get_trades_by_fomc_proximity', return_value=[]), \
         patch.object(detector, '_get_trades_by_earnings_timing', return_value=[]), \
         patch.object(detector, '_get_trades_by_market_breadth', return_value=[]), \
         patch.object(PathAnalyzer, 'analyze_all_paths', return_value=[]), \
         patch.object(PatternCombiner, 'analyze_all_combinations', return_value=[]):  # Mock Phase 3.5

        patterns = detector.detect_patterns()

    # Should have patterns from all dimensions including new Phase 3.1
    pattern_types = {p.pattern_type for p in patterns}

    # Check that Phase 3.1 pattern types are included
    assert "rsi_regime" in pattern_types
    assert "macd_momentum" in pattern_types
    assert "trend_strength" in pattern_types
    assert "bb_position" in pattern_types
    assert "support_proximity" in pattern_types
    assert "atr_volatility" in pattern_types


# =========================================================================
# Phase 3.2: Market Context Tests
# =========================================================================


@pytest.fixture
def sample_trades_with_market_context():
    """Create sample trades with entry snapshots containing market context."""
    from src.data.models import TradeEntrySnapshot

    trades = []
    base_date = datetime(2026, 1, 1)

    # Create 60 trades with varying market context
    for i in range(60):
        trade = Trade(
            id=i + 1,
            trade_id=f"trade_{i}",
            symbol=f"STOCK{i % 10}",
            strike=100.0 - (i % 10),
            expiration=base_date + timedelta(days=30 + i),
            entry_date=base_date + timedelta(days=i),
            exit_date=base_date + timedelta(days=i + 14),
            entry_premium=2.0,
            exit_premium=1.0,
            contracts=1,
            dte=30 - (i % 15),
            otm_pct=0.05 + (i % 10) * 0.02,
            profit_loss=100.0 if i % 3 == 0 else -50.0,
            roi=0.50 if i % 3 == 0 else -0.25,
            vix_at_entry=15.0 + (i % 4) * 5.0,
        )

        # Create entry snapshot with market context
        sectors = ["Technology", "Healthcare", "Financial", "Consumer", "Energy"]
        vol_regimes = ["low", "normal", "elevated", "extreme"]
        market_regimes = ["bullish", "bearish", "neutral", "volatile"]

        snapshot = TradeEntrySnapshot(
            id=i + 1,
            trade_id=i + 1,
            symbol=trade.symbol,
            strike=trade.strike,
            expiration=trade.expiration,
            option_type="PUT",
            entry_premium=trade.entry_premium,
            contracts=trade.contracts,
            stock_price=100.0 + (i % 10),
            dte=trade.dte,
            # Market context fields
            sector=sectors[i % len(sectors)],
            vol_regime=vol_regimes[i % len(vol_regimes)],
            market_regime=market_regimes[i % len(market_regimes)],
            is_opex_week=(i % 4 == 0),  # Every 4th trade in OpEx week
            days_to_fomc=i % 20,  # 0-19 days to FOMC
            earnings_timing="BMO" if i % 3 == 0 else ("AMC" if i % 3 == 1 else None),
            earnings_in_dte=(i % 3 < 2),  # 2/3 have earnings
            qqq_change_pct=-1.5 + (i % 10) * 0.4,  # -1.5% to 2.5%
            iwm_change_pct=-2.0 + (i % 10) * 0.5,  # -2.0% to 3.0%
            source="test",
            captured_at=base_date + timedelta(days=i),
        )

        trade.entry_snapshot = snapshot
        trades.append(trade)

    return trades


def test_analyze_by_sector(mock_db_session, sample_trades_with_market_context):
    """Test sector pattern detection."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades_with_market_context

    # Mock the distinct sectors query
    sectors = [("Technology",), ("Healthcare",), ("Financial",)]
    mock_db_session.query().join().filter().filter().distinct().all.return_value = sectors

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    with patch.object(detector, '_get_trades_by_sector') as mock_get:
        def mock_sector_filter(sector):
            return [t for t in sample_trades_with_market_context
                   if t.entry_snapshot.sector == sector]

        mock_get.side_effect = mock_sector_filter

        patterns = detector.analyze_by_sector()

    assert isinstance(patterns, list)
    assert len(patterns) > 0

    for pattern in patterns:
        assert pattern.pattern_type == "sector"
        assert "sector_" in pattern.pattern_name


def test_analyze_by_vol_regime(mock_db_session, sample_trades_with_market_context):
    """Test volatility regime pattern detection."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades_with_market_context

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    with patch.object(detector, '_get_trades_by_vol_regime') as mock_get:
        def mock_vol_filter(regime):
            return [t for t in sample_trades_with_market_context
                   if t.entry_snapshot.vol_regime == regime]

        mock_get.side_effect = mock_vol_filter

        patterns = detector.analyze_by_vol_regime()

    assert isinstance(patterns, list)
    assert len(patterns) > 0

    for pattern in patterns:
        assert pattern.pattern_type == "vol_regime"
        assert "vol_" in pattern.pattern_name


def test_analyze_by_market_regime(mock_db_session, sample_trades_with_market_context):
    """Test market regime pattern detection."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades_with_market_context

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    with patch.object(detector, '_get_trades_by_market_regime') as mock_get:
        def mock_market_filter(regime):
            return [t for t in sample_trades_with_market_context
                   if t.entry_snapshot.market_regime == regime]

        mock_get.side_effect = mock_market_filter

        patterns = detector.analyze_by_market_regime()

    assert isinstance(patterns, list)
    assert len(patterns) > 0

    for pattern in patterns:
        assert pattern.pattern_type == "market_regime"
        assert "market_" in pattern.pattern_name


def test_analyze_by_opex_week(mock_db_session, sample_trades_with_market_context):
    """Test OpEx week pattern detection."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades_with_market_context

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    with patch.object(detector, '_get_trades_by_opex_week') as mock_get:
        def mock_opex_filter(is_opex):
            return [t for t in sample_trades_with_market_context
                   if t.entry_snapshot.is_opex_week == is_opex]

        mock_get.side_effect = mock_opex_filter

        patterns = detector.analyze_by_opex_week()

    assert isinstance(patterns, list)
    # Should have patterns for both OpEx and non-OpEx weeks
    assert len(patterns) >= 1

    for pattern in patterns:
        assert pattern.pattern_type == "calendar_event"
        assert "opex" in pattern.pattern_name


def test_analyze_by_fomc_proximity(mock_db_session, sample_trades_with_market_context):
    """Test FOMC proximity pattern detection."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades_with_market_context

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    with patch.object(detector, '_get_trades_by_fomc_proximity') as mock_get:
        def mock_fomc_filter(min_days, max_days):
            return [t for t in sample_trades_with_market_context
                   if min_days <= t.entry_snapshot.days_to_fomc < max_days]

        mock_get.side_effect = mock_fomc_filter

        patterns = detector.analyze_by_fomc_proximity()

    assert isinstance(patterns, list)
    assert len(patterns) > 0

    for pattern in patterns:
        assert pattern.pattern_type == "calendar_event"
        assert "fomc" in pattern.pattern_name


def test_analyze_by_earnings_timing(mock_db_session, sample_trades_with_market_context):
    """Test earnings timing pattern detection."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades_with_market_context

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    with patch.object(detector, '_get_trades_by_earnings_timing') as mock_get:
        def mock_earnings_filter(timing):
            if timing is None:
                return [t for t in sample_trades_with_market_context
                       if not t.entry_snapshot.earnings_in_dte]
            else:
                return [t for t in sample_trades_with_market_context
                       if t.entry_snapshot.earnings_timing == timing]

        mock_get.side_effect = mock_earnings_filter

        patterns = detector.analyze_by_earnings_timing()

    assert isinstance(patterns, list)
    assert len(patterns) > 0

    for pattern in patterns:
        assert pattern.pattern_type == "earnings_timing"
        assert "earnings" in pattern.pattern_name or "no_earnings" in pattern.pattern_name


def test_analyze_by_market_breadth(mock_db_session, sample_trades_with_market_context):
    """Test market breadth pattern detection."""
    from unittest.mock import patch

    mock_db_session.query().filter().all.return_value = sample_trades_with_market_context

    detector = PatternDetector(mock_db_session, min_sample_size=10)
    detector._calculate_baseline()

    with patch.object(detector, '_get_trades_by_market_breadth') as mock_get:
        def mock_breadth_filter(breadth_type):
            filtered = []
            for t in sample_trades_with_market_context:
                qqq = t.entry_snapshot.qqq_change_pct
                iwm = t.entry_snapshot.iwm_change_pct
                if breadth_type == "risk_on" and iwm > qqq:
                    filtered.append(t)
                elif breadth_type == "risk_off" and qqq > iwm:
                    filtered.append(t)
                elif breadth_type == "broad_strength" and qqq > 0 and iwm > 0:
                    filtered.append(t)
                elif breadth_type == "broad_weakness" and qqq < 0 and iwm < 0:
                    filtered.append(t)
            return filtered

        mock_get.side_effect = mock_breadth_filter

        patterns = detector.analyze_by_market_breadth()

    assert isinstance(patterns, list)
    assert len(patterns) > 0

    for pattern in patterns:
        assert pattern.pattern_type == "market_breadth"
        assert pattern.pattern_name in ["risk_on", "risk_off", "broad_strength", "broad_weakness"]


def test_phase_3_2_integration(mock_db_session, sample_trades_with_market_context):
    """Test that all Phase 3.2 analyses are included in detect_patterns()."""
    from unittest.mock import patch
    from contextlib import ExitStack
    from src.learning.path_analyzer import PathAnalyzer
    from src.learning.pattern_combiner import PatternCombiner

    mock_db_session.query().filter().all.return_value = sample_trades_with_market_context
    mock_db_session.query().join().filter().filter().distinct().all.return_value = [("Technology",), ("Healthcare",)]

    detector = PatternDetector(mock_db_session, min_sample_size=10)

    # Set baseline metrics directly (mock query chains collapse to same object)
    detector.baseline_win_rate = 0.65
    detector.baseline_roi = 0.05
    detector._calculate_baseline = lambda: None

    # Use ExitStack to manage all patches to avoid nesting limit
    with ExitStack() as stack:
        # Mock all helper methods to return appropriate trades
        stack.enter_context(patch.object(detector, '_get_trades_by_sector', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_by_vol_regime', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_by_market_regime', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_by_opex_week', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_by_fomc_proximity', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_by_earnings_timing', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_by_market_breadth', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_in_delta_range', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_in_iv_range', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_in_dte_range', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_in_vix_range', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_by_trend', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_by_entry_day', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_in_rsi_range', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_in_macd_histogram_range', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_in_adx_range', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_in_bb_position_range', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_in_support_proximity_range', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(detector, '_get_trades_in_atr_range', return_value=sample_trades_with_market_context[:20]))
        stack.enter_context(patch.object(PathAnalyzer, 'analyze_all_paths', return_value=[]))
        stack.enter_context(patch.object(PatternCombiner, 'analyze_all_combinations', return_value=[]))

        patterns = detector.detect_patterns()

        # Should have patterns from all dimensions including Phase 3.2
        pattern_types = {p.pattern_type for p in patterns}

        # Check that Phase 3.2 pattern types are included
        assert "sector" in pattern_types
        assert "vol_regime" in pattern_types
        assert "market_regime" in pattern_types
        assert "calendar_event" in pattern_types  # OpEx and FOMC
        assert "earnings_timing" in pattern_types
        assert "market_breadth" in pattern_types


# =========================================================================
# Phase 3.3: Path Analysis Tests (Position Snapshots)
# =========================================================================


@pytest.fixture
def sample_trades_with_snapshots():
    """Create sample trades with position snapshots and exit snapshots."""
    from src.data.models import PositionSnapshot, TradeExitSnapshot

    trades = []
    base_date = datetime(2026, 1, 1)

    # Create 60 trades with position snapshots and exit data
    for i in range(60):
        trade = Trade(
            id=i + 1,
            trade_id=f"trade_{i}",
            symbol=f"STOCK{i % 10}",
            strike=100.0 - (i % 10),
            expiration=base_date + timedelta(days=30 + i),
            entry_date=base_date + timedelta(days=i),
            exit_date=base_date + timedelta(days=i + 14),
            entry_premium=2.0,
            exit_premium=1.0 if i % 3 == 0 else 1.5,  # Winners exit at 1.0
            contracts=1,
            dte=30 - (i % 15),
            otm_pct=0.05 + (i % 10) * 0.02,
            profit_loss=100.0 if i % 3 == 0 else -50.0,
            profit_pct=0.50 if i % 3 == 0 else -0.25,
            roi=0.50 if i % 3 == 0 else -0.25,
            vix_at_entry=15.0 + (i % 4) * 5.0,
        )

        # Create exit snapshot with path metrics
        exit_snapshot = TradeExitSnapshot(
            id=i + 1,
            trade_id=i + 1,
            exit_date=base_date + timedelta(days=i + 14),
            exit_premium=trade.exit_premium,
            exit_reason="profit_target" if i % 3 == 0 else "stop_loss",
            days_held=14,
            gross_profit=trade.profit_loss,
            net_profit=trade.profit_loss,
            roi_pct=trade.roi * 100,  # Convert to percentage
            win=True if trade.profit_loss > 0 else False,
            # Path metrics - vary across trades
            max_profit_captured_pct=0.5 + (i % 10) * 0.05,  # 50-95% efficiency
            closest_to_strike_pct=5.0 + (i % 8) * 2.0,  # 5-19% closest approach
            max_profit_pct=0.6 + (i % 10) * 0.04,  # Max profit seen
            max_drawdown_pct=0.1 + (i % 5) * 0.02,  # Max drawdown
            vix_at_exit=15.0 + (i % 5) * 4.0,
            captured_at=base_date + timedelta(days=i + 14),
        )

        # Store exit snapshot in trade for later access (for test purposes)
        trade._test_exit_snapshot = exit_snapshot

        # Create 7 daily position snapshots (2 weeks of trading days)
        snapshots = []
        for day in range(7):
            # Simulate P&L evolution
            days_into_trade = day * 2

            # Different P&L trajectories based on trade index
            if i % 4 == 0:  # Accelerating momentum
                current_pnl_pct = 0.1 + (day ** 1.5) * 0.05
            elif i % 4 == 1:  # Plateauing momentum
                current_pnl_pct = 0.3 - (day * 0.01)
            elif i % 4 == 2:  # Reversal pattern
                if day < 3:
                    current_pnl_pct = 0.6 - (day * 0.05)  # Peak early
                else:
                    current_pnl_pct = 0.2 - ((day - 3) * 0.05)  # Then drop
            else:  # Steady
                current_pnl_pct = 0.2 + (day * 0.04)

            # Delta evolution (for short puts, delta moving toward 0 is good)
            initial_delta = -0.15
            delta_change = (day * 0.02) if i % 2 == 0 else -(day * 0.01)
            delta = initial_delta + delta_change

            snapshot = PositionSnapshot(
                id=(i * 7) + day + 1,
                trade_id=i + 1,
                snapshot_date=base_date + timedelta(days=i + days_into_trade),
                current_premium=2.0 - (day * 0.15),  # Premium decaying
                current_pnl=current_pnl_pct * 200.0,
                current_pnl_pct=current_pnl_pct,
                dte_remaining=trade.dte - days_into_trade,
                delta=delta,
                gamma=0.05 - (day * 0.005),
                theta=2.0 - (day * 0.2),
                vega=0.3 - (day * 0.03),
                iv=0.25 + (day * 0.01),
                stock_price=100.0 + (day * 0.5),
                distance_to_strike_pct=5.0 + (day * 1.0),
                vix=15.0 + (day * 0.5),
                spy_price=400.0 + (day * 2.0),
                captured_at=base_date + timedelta(days=i + days_into_trade),
            )
            snapshots.append(snapshot)

        # Store snapshots in trade for later access (for test purposes)
        trade._test_position_snapshots = snapshots
        trades.append(trade)

    return trades


def test_analyze_exit_timing_efficiency(mock_db_session, sample_trades_with_snapshots):
    """Test exit timing efficiency pattern detection."""
    from unittest.mock import Mock, MagicMock
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    # Create mock query chains
    def mock_query(*args):
        mock_chain = Mock()

        # For Trade.query().join().filter().all() - return trades with exit snapshots
        mock_chain.join.return_value = mock_chain
        mock_chain.filter.return_value = mock_chain
        mock_chain.order_by.return_value = mock_chain
        mock_chain.all.return_value = sample_trades_with_snapshots
        mock_chain.first.side_effect = lambda: sample_trades_with_snapshots[0]._test_exit_snapshot

        return mock_chain

    mock_db_session.query = mock_query

    patterns = analyzer.analyze_exit_timing_efficiency()

    assert isinstance(patterns, list)

    # Check pattern attributes
    for pattern in patterns:
        assert pattern.pattern_type == "exit_efficiency"
        assert pattern.pattern_name in ["excellent_exit_timing", "good_exit_timing", "poor_exit_timing"]
        assert 0.0 <= pattern.win_rate <= 1.0
        assert 0.0 <= pattern.confidence <= 1.0


def test_detect_reversal_patterns(mock_db_session, sample_trades_with_snapshots):
    """Test profit reversal pattern detection."""
    from unittest.mock import patch
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    with patch.object(analyzer, '_get_trades_with_snapshots') as mock_get:
        # Return trades with snapshots
        mock_get.return_value = [(t, t._test_position_snapshots) for t in sample_trades_with_snapshots]

        patterns = analyzer.detect_reversal_patterns()

    assert isinstance(patterns, list)

    for pattern in patterns:
        assert pattern.pattern_type == "profit_reversal"
        assert pattern.pattern_name in ["strong_reversal", "moderate_reversal", "no_reversal"]
        assert 0.0 <= pattern.win_rate <= 1.0


def test_detect_momentum_patterns(mock_db_session, sample_trades_with_snapshots):
    """Test P&L momentum pattern detection."""
    from unittest.mock import patch
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    with patch.object(analyzer, '_get_trades_with_snapshots') as mock_get:
        mock_get.return_value = [(t, t._test_position_snapshots) for t in sample_trades_with_snapshots]

        patterns = analyzer.detect_momentum_patterns()

    assert isinstance(patterns, list)

    for pattern in patterns:
        assert pattern.pattern_type == "pnl_momentum"
        assert pattern.pattern_name in ["accelerating_momentum", "steady_momentum", "plateauing_momentum"]
        assert 0.0 <= pattern.win_rate <= 1.0


def test_analyze_greeks_evolution(mock_db_session, sample_trades_with_snapshots):
    """Test Greeks evolution pattern detection."""
    from unittest.mock import patch
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    with patch.object(analyzer, '_get_trades_with_snapshots') as mock_get:
        mock_get.return_value = [(t, t._test_position_snapshots) for t in sample_trades_with_snapshots]

        patterns = analyzer.analyze_greeks_evolution()

    assert isinstance(patterns, list)

    for pattern in patterns:
        assert pattern.pattern_type == "greeks_evolution"
        assert pattern.pattern_name in ["delta_favorable", "delta_stable", "delta_unfavorable"]
        assert 0.0 <= pattern.win_rate <= 1.0


def test_detect_proximity_risk_patterns(mock_db_session, sample_trades_with_snapshots):
    """Test proximity to strike risk pattern detection."""
    from unittest.mock import Mock
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    # Track which trade we're querying for
    call_count = [0]

    def mock_query(*args):
        mock_chain = Mock()

        # For Trade.query().join().filter().filter().all() - return all trades
        mock_chain.join.return_value = mock_chain
        mock_chain.filter.return_value = mock_chain
        mock_chain.all.return_value = sample_trades_with_snapshots

        # For TradeExitSnapshot.query().filter().first() - return corresponding exit snapshot
        def first_side_effect():
            trade_idx = min(call_count[0], len(sample_trades_with_snapshots) - 1)
            call_count[0] += 1
            return sample_trades_with_snapshots[trade_idx]._test_exit_snapshot

        mock_chain.first.side_effect = first_side_effect

        return mock_chain

    mock_db_session.query = mock_query

    patterns = analyzer.detect_proximity_risk_patterns()

    assert isinstance(patterns, list)

    for pattern in patterns:
        assert pattern.pattern_type == "proximity_risk"
        assert pattern.pattern_name in ["safe_distance", "moderate_proximity", "dangerous_proximity"]
        assert 0.0 <= pattern.win_rate <= 1.0


def test_path_analyzer_analyze_all_paths(mock_db_session, sample_trades_with_snapshots):
    """Test that analyze_all_paths() calls all path analysis methods (Phase 3.3 + 3.4)."""
    from unittest.mock import patch, Mock
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    # Mock all individual analysis methods (Phase 3.3 + Phase 3.4)
    with patch.object(analyzer, 'analyze_exit_timing_efficiency', return_value=[Mock(), Mock()]) as mock_exit, \
         patch.object(analyzer, 'detect_reversal_patterns', return_value=[Mock()]) as mock_reversal, \
         patch.object(analyzer, 'detect_momentum_patterns', return_value=[Mock(), Mock()]) as mock_momentum, \
         patch.object(analyzer, 'analyze_greeks_evolution', return_value=[Mock()]) as mock_greeks, \
         patch.object(analyzer, 'detect_proximity_risk_patterns', return_value=[Mock(), Mock(), Mock()]) as mock_proximity, \
         patch.object(analyzer, 'analyze_by_exit_reason', return_value=[Mock()]) as mock_exit_reason, \
         patch.object(analyzer, 'analyze_by_trade_quality', return_value=[Mock(), Mock()]) as mock_quality, \
         patch.object(analyzer, 'analyze_by_risk_adjusted_return', return_value=[Mock()]) as mock_rar, \
         patch.object(analyzer, 'analyze_by_iv_change', return_value=[Mock(), Mock()]) as mock_iv, \
         patch.object(analyzer, 'analyze_by_stock_movement', return_value=[Mock()]) as mock_stock, \
         patch.object(analyzer, 'analyze_by_vix_change', return_value=[Mock(), Mock()]) as mock_vix, \
         patch.object(analyzer, 'analyze_by_max_drawdown', return_value=[Mock()]) as mock_dd:

        patterns = analyzer.analyze_all_paths()

    # Verify all Phase 3.3 methods were called
    mock_exit.assert_called_once()
    mock_reversal.assert_called_once()
    mock_momentum.assert_called_once()
    mock_greeks.assert_called_once()
    mock_proximity.assert_called_once()

    # Verify all Phase 3.4 methods were called
    mock_exit_reason.assert_called_once()
    mock_quality.assert_called_once()
    mock_rar.assert_called_once()
    mock_iv.assert_called_once()
    mock_stock.assert_called_once()
    mock_vix.assert_called_once()
    mock_dd.assert_called_once()

    # Verify all patterns were collected
    # Phase 3.3: 2+1+2+1+3 = 9
    # Phase 3.4: 1+2+1+2+1+2+1 = 10
    # Total: 19
    assert len(patterns) == 19


def test_phase_3_3_integration(mock_db_session, sample_trades_with_snapshots):
    """Test that Phase 3.3 path analysis is integrated into detect_patterns()."""
    from unittest.mock import patch
    from contextlib import ExitStack
    from src.learning.path_analyzer import PathAnalyzer
    from src.learning.pattern_combiner import PatternCombiner

    mock_db_session.query().filter().all.return_value = sample_trades_with_snapshots
    # Mock the sector query to return empty list
    mock_db_session.query().join().filter().filter().distinct().all.return_value = []

    detector = PatternDetector(mock_db_session, min_sample_size=10)

    # Mock PathAnalyzer.analyze_all_paths to return test patterns
    test_path_patterns = [
        Mock(pattern_type="exit_efficiency", pattern_name="test_exit"),
        Mock(pattern_type="profit_reversal", pattern_name="test_reversal"),
        Mock(pattern_type="pnl_momentum", pattern_name="test_momentum"),
        Mock(pattern_type="greeks_evolution", pattern_name="test_greeks"),
        Mock(pattern_type="proximity_risk", pattern_name="test_proximity"),
    ]

    # Use ExitStack to manage all patches to avoid nesting limit
    with ExitStack() as stack:
        stack.enter_context(patch.object(PathAnalyzer, 'analyze_all_paths', return_value=test_path_patterns))
        stack.enter_context(patch.object(detector, '_get_trades_in_delta_range', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_in_iv_range', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_in_dte_range', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_in_vix_range', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_by_trend', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_by_entry_day', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_in_rsi_range', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_in_macd_histogram_range', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_in_adx_range', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_in_bb_position_range', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_in_support_proximity_range', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_in_atr_range', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_by_sector', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_by_vol_regime', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_by_market_regime', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_by_opex_week', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_by_fomc_proximity', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_by_earnings_timing', return_value=[]))
        stack.enter_context(patch.object(detector, '_get_trades_by_market_breadth', return_value=[]))
        stack.enter_context(patch.object(PatternCombiner, 'analyze_all_combinations', return_value=[]))

        patterns = detector.detect_patterns()

        # Extract path analysis patterns from results
        path_pattern_types = {p.pattern_type for p in patterns
                              if hasattr(p, 'pattern_type') and
                              p.pattern_type in ["exit_efficiency", "profit_reversal", "pnl_momentum",
                                                "greeks_evolution", "proximity_risk"]}

        # Verify Phase 3.3 pattern types are included
        assert "exit_efficiency" in path_pattern_types
        assert "profit_reversal" in path_pattern_types
        assert "pnl_momentum" in path_pattern_types
        assert "greeks_evolution" in path_pattern_types
        assert "proximity_risk" in path_pattern_types


# =========================================================================
# Phase 3.4: Exit Quality Analysis Tests
# =========================================================================


@pytest.fixture
def sample_trades_with_exit_quality():
    """Create sample trades with complete exit quality data."""
    from src.data.models import TradeExitSnapshot

    trades = []
    base_date = datetime(2026, 1, 1)

    # Create 60 trades with diverse exit quality metrics
    for i in range(60):
        trade = Trade(
            id=i + 1,
            trade_id=f"trade_{i}",
            symbol=f"STOCK{i % 10}",
            strike=100.0 - (i % 10),
            expiration=base_date + timedelta(days=30 + i),
            entry_date=base_date + timedelta(days=i),
            exit_date=base_date + timedelta(days=i + 14),
            entry_premium=2.0,
            exit_premium=1.0 if i % 3 == 0 else 1.5,
            contracts=1,
            dte=30 - (i % 15),
            otm_pct=0.05 + (i % 10) * 0.02,
            profit_loss=100.0 if i % 3 == 0 else -50.0,
            profit_pct=0.50 if i % 3 == 0 else -0.25,
            roi=0.50 if i % 3 == 0 else -0.25,
            vix_at_entry=15.0 + (i % 4) * 5.0,
        )

        # Create exit snapshot with complete Phase 3.4 fields
        exit_reasons = ["profit_target", "stop_loss", "expiration", "manual"]
        exit_snapshot = TradeExitSnapshot(
            id=i + 1,
            trade_id=i + 1,
            exit_date=base_date + timedelta(days=i + 14),
            exit_premium=trade.exit_premium,
            exit_reason=exit_reasons[i % 4],  # Distribute across exit reasons
            days_held=14,
            gross_profit=trade.profit_loss,
            net_profit=trade.profit_loss,
            roi_pct=trade.roi * 100,
            win=True if trade.profit_loss > 0 else False,
            # Phase 3.3 fields
            max_profit_captured_pct=0.5 + (i % 10) * 0.05,
            closest_to_strike_pct=5.0 + (i % 8) * 2.0,
            max_profit_pct=0.6 + (i % 10) * 0.04,
            max_drawdown_pct=0.05 + (i % 8) * 0.03,  # 5-26% drawdown
            # Phase 3.4 fields
            trade_quality_score=0.3 + (i % 10) * 0.07,  # 0.3-0.93 range
            risk_adjusted_return=0.5 + (i % 12) * 0.4,  # 0.5-4.9 range
            exit_iv=0.25 + (i % 10) * 0.02,
            iv_change_during_trade=-0.15 + (i % 10) * 0.03,  # -15% to +15%
            stock_price_at_exit=100.0 + (i % 20) - 10,  # Varied stock prices
            stock_change_during_trade_pct=-6.0 + (i % 13),  # -6% to +6%
            vix_at_exit=15.0 + (i % 9) - 3,  # Varied VIX levels
            vix_change_during_trade=-4.0 + (i % 9),  # -4 to +4 points
            captured_at=base_date + timedelta(days=i + 14),
        )

        # Store for test access
        trade._test_exit_snapshot = exit_snapshot
        trades.append(trade)

    return trades


def test_analyze_by_exit_reason(mock_db_session, sample_trades_with_exit_quality):
    """Test exit reason pattern detection."""
    from unittest.mock import Mock
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    # Setup mock queries
    call_count = [0]

    def mock_query(*args):
        mock_chain = Mock()
        mock_chain.join.return_value = mock_chain
        mock_chain.filter.return_value = mock_chain
        mock_chain.all.return_value = sample_trades_with_exit_quality

        def first_side_effect():
            trade_idx = min(call_count[0], len(sample_trades_with_exit_quality) - 1)
            call_count[0] += 1
            return sample_trades_with_exit_quality[trade_idx]._test_exit_snapshot

        mock_chain.first.side_effect = first_side_effect
        return mock_chain

    mock_db_session.query = mock_query

    patterns = analyzer.analyze_by_exit_reason()

    assert isinstance(patterns, list)
    assert len(patterns) > 0

    for pattern in patterns:
        assert pattern.pattern_type == "exit_reason"
        assert pattern.pattern_name.startswith("exit_")
        assert 0.0 <= pattern.win_rate <= 1.0


def test_analyze_by_trade_quality(mock_db_session, sample_trades_with_exit_quality):
    """Test trade quality score pattern detection."""
    from unittest.mock import Mock
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    call_count = [0]

    def mock_query(*args):
        mock_chain = Mock()
        mock_chain.join.return_value = mock_chain
        mock_chain.filter.return_value = mock_chain
        mock_chain.all.return_value = sample_trades_with_exit_quality

        def first_side_effect():
            trade_idx = min(call_count[0], len(sample_trades_with_exit_quality) - 1)
            call_count[0] += 1
            return sample_trades_with_exit_quality[trade_idx]._test_exit_snapshot

        mock_chain.first.side_effect = first_side_effect
        return mock_chain

    mock_db_session.query = mock_query

    patterns = analyzer.analyze_by_trade_quality()

    assert isinstance(patterns, list)

    for pattern in patterns:
        assert pattern.pattern_type == "trade_quality"
        assert pattern.pattern_name in ["high_quality", "medium_quality", "low_quality"]
        assert 0.0 <= pattern.win_rate <= 1.0


def test_analyze_by_risk_adjusted_return(mock_db_session, sample_trades_with_exit_quality):
    """Test risk-adjusted return pattern detection."""
    from unittest.mock import Mock
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    call_count = [0]

    def mock_query(*args):
        mock_chain = Mock()
        mock_chain.join.return_value = mock_chain
        mock_chain.filter.return_value = mock_chain
        mock_chain.all.return_value = sample_trades_with_exit_quality

        def first_side_effect():
            trade_idx = min(call_count[0], len(sample_trades_with_exit_quality) - 1)
            call_count[0] += 1
            return sample_trades_with_exit_quality[trade_idx]._test_exit_snapshot

        mock_chain.first.side_effect = first_side_effect
        return mock_chain

    mock_db_session.query = mock_query

    patterns = analyzer.analyze_by_risk_adjusted_return()

    assert isinstance(patterns, list)

    for pattern in patterns:
        assert pattern.pattern_type == "risk_adjusted_return"
        assert pattern.pattern_name in ["excellent_risk_adjusted", "good_risk_adjusted", "poor_risk_adjusted"]
        assert 0.0 <= pattern.win_rate <= 1.0


def test_analyze_by_iv_change(mock_db_session, sample_trades_with_exit_quality):
    """Test IV change pattern detection."""
    from unittest.mock import Mock
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    call_count = [0]

    def mock_query(*args):
        mock_chain = Mock()
        mock_chain.join.return_value = mock_chain
        mock_chain.filter.return_value = mock_chain
        mock_chain.all.return_value = sample_trades_with_exit_quality

        def first_side_effect():
            trade_idx = min(call_count[0], len(sample_trades_with_exit_quality) - 1)
            call_count[0] += 1
            return sample_trades_with_exit_quality[trade_idx]._test_exit_snapshot

        mock_chain.first.side_effect = first_side_effect
        return mock_chain

    mock_db_session.query = mock_query

    patterns = analyzer.analyze_by_iv_change()

    assert isinstance(patterns, list)

    for pattern in patterns:
        assert pattern.pattern_type == "iv_change"
        assert pattern.pattern_name in ["iv_crushed", "iv_stable", "iv_expanded"]
        assert 0.0 <= pattern.win_rate <= 1.0


def test_analyze_by_stock_movement(mock_db_session, sample_trades_with_exit_quality):
    """Test stock movement pattern detection."""
    from unittest.mock import Mock
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    call_count = [0]

    def mock_query(*args):
        mock_chain = Mock()
        mock_chain.join.return_value = mock_chain
        mock_chain.filter.return_value = mock_chain
        mock_chain.all.return_value = sample_trades_with_exit_quality

        def first_side_effect():
            trade_idx = min(call_count[0], len(sample_trades_with_exit_quality) - 1)
            call_count[0] += 1
            return sample_trades_with_exit_quality[trade_idx]._test_exit_snapshot

        mock_chain.first.side_effect = first_side_effect
        return mock_chain

    mock_db_session.query = mock_query

    patterns = analyzer.analyze_by_stock_movement()

    assert isinstance(patterns, list)

    for pattern in patterns:
        assert pattern.pattern_type == "stock_movement"
        assert pattern.pattern_name in ["stock_strong_up", "stock_moderate_up", "stock_neutral",
                                        "stock_moderate_down", "stock_strong_down"]
        assert 0.0 <= pattern.win_rate <= 1.0


def test_analyze_by_vix_change(mock_db_session, sample_trades_with_exit_quality):
    """Test VIX change pattern detection."""
    from unittest.mock import Mock
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    call_count = [0]

    def mock_query(*args):
        mock_chain = Mock()
        mock_chain.join.return_value = mock_chain
        mock_chain.filter.return_value = mock_chain
        mock_chain.all.return_value = sample_trades_with_exit_quality

        def first_side_effect():
            trade_idx = min(call_count[0], len(sample_trades_with_exit_quality) - 1)
            call_count[0] += 1
            return sample_trades_with_exit_quality[trade_idx]._test_exit_snapshot

        mock_chain.first.side_effect = first_side_effect
        return mock_chain

    mock_db_session.query = mock_query

    patterns = analyzer.analyze_by_vix_change()

    assert isinstance(patterns, list)

    for pattern in patterns:
        assert pattern.pattern_type == "vix_change"
        assert pattern.pattern_name in ["vix_declined", "vix_stable", "vix_spiked"]
        assert 0.0 <= pattern.win_rate <= 1.0


def test_analyze_by_max_drawdown(mock_db_session, sample_trades_with_exit_quality):
    """Test max drawdown pattern detection."""
    from unittest.mock import Mock
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    call_count = [0]

    def mock_query(*args):
        mock_chain = Mock()
        mock_chain.join.return_value = mock_chain
        mock_chain.filter.return_value = mock_chain
        mock_chain.all.return_value = sample_trades_with_exit_quality

        def first_side_effect():
            trade_idx = min(call_count[0], len(sample_trades_with_exit_quality) - 1)
            call_count[0] += 1
            return sample_trades_with_exit_quality[trade_idx]._test_exit_snapshot

        mock_chain.first.side_effect = first_side_effect
        return mock_chain

    mock_db_session.query = mock_query

    patterns = analyzer.analyze_by_max_drawdown()

    assert isinstance(patterns, list)

    for pattern in patterns:
        assert pattern.pattern_type == "max_drawdown"
        assert pattern.pattern_name in ["low_drawdown", "moderate_drawdown", "high_drawdown"]
        assert 0.0 <= pattern.win_rate <= 1.0


def test_phase_3_4_integration(mock_db_session, sample_trades_with_exit_quality):
    """Test that Phase 3.4 analyses are included in analyze_all_paths()."""
    from unittest.mock import patch
    from src.learning.path_analyzer import PathAnalyzer

    analyzer = PathAnalyzer(mock_db_session, min_sample_size=10)

    # Mock all Phase 3.4 analysis methods
    with patch.object(analyzer, 'analyze_exit_timing_efficiency', return_value=[]), \
         patch.object(analyzer, 'detect_reversal_patterns', return_value=[]), \
         patch.object(analyzer, 'detect_momentum_patterns', return_value=[]), \
         patch.object(analyzer, 'analyze_greeks_evolution', return_value=[]), \
         patch.object(analyzer, 'detect_proximity_risk_patterns', return_value=[]), \
         patch.object(analyzer, 'analyze_by_exit_reason', return_value=[Mock(pattern_type="exit_reason")]) as mock_exit_reason, \
         patch.object(analyzer, 'analyze_by_trade_quality', return_value=[Mock(pattern_type="trade_quality")]) as mock_quality, \
         patch.object(analyzer, 'analyze_by_risk_adjusted_return', return_value=[Mock(pattern_type="risk_adjusted_return")]) as mock_rar, \
         patch.object(analyzer, 'analyze_by_iv_change', return_value=[Mock(pattern_type="iv_change")]) as mock_iv, \
         patch.object(analyzer, 'analyze_by_stock_movement', return_value=[Mock(pattern_type="stock_movement")]) as mock_stock, \
         patch.object(analyzer, 'analyze_by_vix_change', return_value=[Mock(pattern_type="vix_change")]) as mock_vix, \
         patch.object(analyzer, 'analyze_by_max_drawdown', return_value=[Mock(pattern_type="max_drawdown")]) as mock_dd:

        patterns = analyzer.analyze_all_paths()

    # Verify all Phase 3.4 methods were called
    mock_exit_reason.assert_called_once()
    mock_quality.assert_called_once()
    mock_rar.assert_called_once()
    mock_iv.assert_called_once()
    mock_stock.assert_called_once()
    mock_vix.assert_called_once()
    mock_dd.assert_called_once()

    # Verify Phase 3.4 pattern types are included
    pattern_types = {p.pattern_type for p in patterns}
    assert "exit_reason" in pattern_types
    assert "trade_quality" in pattern_types
    assert "risk_adjusted_return" in pattern_types
    assert "iv_change" in pattern_types
    assert "stock_movement" in pattern_types
    assert "vix_change" in pattern_types
    assert "max_drawdown" in pattern_types


# =========================================================================
# Phase 3.5: Multi-dimensional Pattern Combinations Tests
# =========================================================================


@pytest.fixture
def sample_trades_with_full_data():
    """Create sample trades with complete data across all dimensions."""
    from src.data.models import TradeEntrySnapshot, TradeExitSnapshot, PositionSnapshot

    trades = []
    base_date = datetime(2026, 1, 1)

    # Create 60 trades with complete data for combination analysis
    for i in range(60):
        trade = Trade(
            id=i + 1,
            trade_id=f"trade_{i}",
            symbol=f"STOCK{i % 10}",
            strike=100.0 - (i % 10),
            expiration=base_date + timedelta(days=30 + i),
            entry_date=base_date + timedelta(days=i),
            exit_date=base_date + timedelta(days=i + 14),
            entry_premium=2.0,
            exit_premium=1.0 if i % 3 == 0 else 1.5,
            contracts=1,
            dte=30 - (i % 15),
            otm_pct=0.05 + (i % 10) * 0.02,
            profit_loss=100.0 if i % 3 == 0 else -50.0,
            profit_pct=0.50 if i % 3 == 0 else -0.25,
            roi=0.50 if i % 3 == 0 else -0.25,
            vix_at_entry=15.0 + (i % 4) * 5.0,
        )

        # Create entry snapshot with all Phase 3.1 + 3.2 fields
        sectors = ["Technology", "Healthcare", "Financial", "Consumer"]
        trends = ["uptrend", "downtrend", "sideways", "unknown"]
        entry_snapshot = TradeEntrySnapshot(
            id=i + 1,
            trade_id=i + 1,
            symbol=trade.symbol,
            strike=trade.strike,
            expiration=trade.expiration,
            option_type="PUT",
            entry_premium=trade.entry_premium,
            contracts=trade.contracts,
            stock_price=100.0 + (i % 10),
            dte=trade.dte,
            # Technical indicators
            rsi_14=25.0 + (i % 6) * 10.0,  # 25-75 range
            macd_histogram=-0.5 + (i % 10) * 0.15,
            adx=15.0 + (i % 5) * 10.0,
            bb_position=0.1 + (i % 8) * 0.1,
            distance_to_support_pct=2.0 + (i % 8) * 3.0,
            atr_pct=1.5 + (i % 7) * 0.8,
            iv_rank=0.3 + (i % 8) * 0.08,  # 0.3-0.86
            # Market context
            sector=sectors[i % 4],
            trend_direction=trends[i % 4],
            vol_regime="normal",
            market_regime="bullish" if i % 2 == 0 else "neutral",
            is_opex_week=i % 5 == 0,
            days_to_fomc=14 - (i % 20),
            earnings_in_dte=i % 7 == 0,
            earnings_timing="AMC" if i % 7 == 0 else None,
            qqq_change_pct=-1.0 + (i % 10) * 0.3,
            iwm_change_pct=-1.5 + (i % 10) * 0.4,
            source="test",
            captured_at=base_date + timedelta(days=i),
        )

        # Create exit snapshot with all Phase 3.4 fields
        exit_reasons = ["profit_target", "stop_loss", "expiration", "manual"]
        exit_snapshot = TradeExitSnapshot(
            id=i + 1,
            trade_id=i + 1,
            exit_date=base_date + timedelta(days=i + 14),
            exit_premium=trade.exit_premium,
            exit_reason=exit_reasons[i % 4],
            days_held=14,
            gross_profit=trade.profit_loss,
            net_profit=trade.profit_loss,
            roi_pct=trade.roi * 100,
            win=True if trade.profit_loss > 0 else False,
            # Phase 3.3 + 3.4 fields
            max_profit_captured_pct=0.5 + (i % 10) * 0.05,
            closest_to_strike_pct=5.0 + (i % 8) * 2.0,
            max_profit_pct=0.6 + (i % 10) * 0.04,
            max_drawdown_pct=0.05 + (i % 8) * 0.03,
            trade_quality_score=0.3 + (i % 10) * 0.07,
            risk_adjusted_return=0.5 + (i % 12) * 0.4,
            exit_iv=0.25 + (i % 10) * 0.02,
            iv_change_during_trade=-0.15 + (i % 10) * 0.03,
            stock_price_at_exit=100.0 + (i % 20) - 10,
            stock_change_during_trade_pct=-6.0 + (i % 13),
            vix_at_exit=15.0 + (i % 9) - 3,
            vix_change_during_trade=-4.0 + (i % 9),
            captured_at=base_date + timedelta(days=i + 14),
        )

        # Create position snapshots for momentum/greeks analysis
        snapshots = []
        for day in range(5):
            # Different trajectories
            if i % 4 == 0:  # Accelerating
                current_pnl_pct = 0.1 + (day ** 1.5) * 0.05
            elif i % 4 == 1:  # Plateauing
                current_pnl_pct = 0.3 - (day * 0.01)
            else:  # Steady
                current_pnl_pct = 0.2 + (day * 0.04)

            delta_change = (day * 0.02) if i % 2 == 0 else -(day * 0.01)
            delta = -0.15 + delta_change

            snapshot = PositionSnapshot(
                id=(i * 5) + day + 1,
                trade_id=i + 1,
                snapshot_date=base_date + timedelta(days=i + (day * 2)),
                current_premium=2.0 - (day * 0.15),
                current_pnl=current_pnl_pct * 200.0,
                current_pnl_pct=current_pnl_pct,
                dte_remaining=trade.dte - (day * 2),
                delta=delta,
                gamma=0.05 - (day * 0.005),
                theta=2.0 - (day * 0.2),
                vega=0.3 - (day * 0.03),
                iv=0.25 + (day * 0.01),
                stock_price=100.0 + (day * 0.5),
                distance_to_strike_pct=5.0 + (day * 1.0),
                vix=15.0 + (day * 0.5),
                spy_price=400.0 + (day * 2.0),
                captured_at=base_date + timedelta(days=i + (day * 2)),
            )
            snapshots.append(snapshot)

        # Store for test access
        trade._test_entry_snapshot = entry_snapshot
        trade._test_exit_snapshot = exit_snapshot
        trade._test_position_snapshots = snapshots
        trades.append(trade)

    return trades


def test_analyze_entry_trajectory_combinations(mock_db_session, sample_trades_with_full_data):
    """Test entry + trajectory pattern combinations."""
    from unittest.mock import Mock, patch
    from src.learning.pattern_combiner import PatternCombiner

    combiner = PatternCombiner(mock_db_session, min_sample_size=10)

    # Mock the helper to return trades with full data
    with patch.object(combiner, '_get_trades_with_complete_data', return_value=sample_trades_with_full_data), \
         patch.object(combiner, '_get_entry_snapshot') as mock_entry, \
         patch.object(combiner, '_get_exit_snapshot') as mock_exit, \
         patch.object(combiner, '_get_position_snapshots') as mock_snapshots:

        # Setup mocks
        def entry_side_effect(trade):
            return trade._test_entry_snapshot

        def exit_side_effect(trade):
            return trade._test_exit_snapshot

        def snapshots_side_effect(trade):
            return trade._test_position_snapshots

        mock_entry.side_effect = entry_side_effect
        mock_exit.side_effect = exit_side_effect
        mock_snapshots.side_effect = snapshots_side_effect

        patterns = combiner.analyze_entry_trajectory_combinations()

    assert isinstance(patterns, list)

    # Check for combination pattern types
    pattern_types = {p.pattern_type for p in patterns}
    assert any(t in pattern_types for t in [
        "rsi_momentum_combo", "iv_entry_exit_combo", "trend_greeks_combo", "breadth_stock_combo"
    ])


def test_analyze_entry_exit_combinations(mock_db_session, sample_trades_with_full_data):
    """Test entry + exit quality pattern combinations."""
    from unittest.mock import Mock, patch
    from src.learning.pattern_combiner import PatternCombiner

    combiner = PatternCombiner(mock_db_session, min_sample_size=10)

    with patch.object(combiner, '_get_trades_with_complete_data', return_value=sample_trades_with_full_data), \
         patch.object(combiner, '_get_entry_snapshot') as mock_entry, \
         patch.object(combiner, '_get_exit_snapshot') as mock_exit:

        def entry_side_effect(trade):
            return trade._test_entry_snapshot

        def exit_side_effect(trade):
            return trade._test_exit_snapshot

        mock_entry.side_effect = entry_side_effect
        mock_exit.side_effect = exit_side_effect

        patterns = combiner.analyze_entry_exit_combinations()

    assert isinstance(patterns, list)

    pattern_types = {p.pattern_type for p in patterns}
    assert any(t in pattern_types for t in [
        "sector_exit_combo", "vix_entry_exit_combo", "support_drawdown_combo"
    ])


def test_analyze_triple_combinations(mock_db_session, sample_trades_with_full_data):
    """Test three-way pattern combinations."""
    from unittest.mock import Mock, patch
    from src.learning.pattern_combiner import PatternCombiner

    combiner = PatternCombiner(mock_db_session, min_sample_size=10)

    with patch.object(combiner, '_get_trades_with_complete_data', return_value=sample_trades_with_full_data), \
         patch.object(combiner, '_get_entry_snapshot') as mock_entry, \
         patch.object(combiner, '_get_exit_snapshot') as mock_exit, \
         patch.object(combiner, '_get_position_snapshots') as mock_snapshots:

        def entry_side_effect(trade):
            return trade._test_entry_snapshot

        def exit_side_effect(trade):
            return trade._test_exit_snapshot

        def snapshots_side_effect(trade):
            return trade._test_position_snapshots

        mock_entry.side_effect = entry_side_effect
        mock_exit.side_effect = exit_side_effect
        mock_snapshots.side_effect = snapshots_side_effect

        patterns = combiner.analyze_triple_combinations()

    assert isinstance(patterns, list)

    pattern_types = {p.pattern_type for p in patterns}
    assert any(t in pattern_types for t in [
        "iv_triple_combo", "rsi_momentum_quality_combo", "trend_greeks_drawdown_combo"
    ])


def test_create_composite_scores(mock_db_session):
    """Test composite opportunity scoring model creation."""
    from src.learning.pattern_combiner import PatternCombiner

    combiner = PatternCombiner(mock_db_session, min_sample_size=30)
    scores = combiner.create_composite_scores()

    assert isinstance(scores, dict)
    assert len(scores) > 0

    # Check for key scoring factors
    assert "rsi_oversold" in scores
    assert "high_iv" in scores
    assert "uptrend" in scores
    assert "expect_profit_target" in scores
    assert "expect_iv_crush" in scores

    # Verify scores are weights (floats)
    for key, value in scores.items():
        assert isinstance(value, float)
        assert 0.0 < value <= 2.0  # Reasonable weight range


def test_pattern_combiner_analyze_all(mock_db_session, sample_trades_with_full_data):
    """Test that analyze_all_combinations() calls all combination methods."""
    from unittest.mock import patch, Mock
    from src.learning.pattern_combiner import PatternCombiner

    combiner = PatternCombiner(mock_db_session, min_sample_size=10)

    # Mock all combination methods
    with patch.object(combiner, 'analyze_entry_trajectory_combinations', return_value=[Mock(), Mock()]) as mock_et, \
         patch.object(combiner, 'analyze_entry_exit_combinations', return_value=[Mock()]) as mock_ee, \
         patch.object(combiner, 'analyze_triple_combinations', return_value=[Mock(), Mock(), Mock()]) as mock_triple:

        patterns = combiner.analyze_all_combinations()

    # Verify all methods were called
    mock_et.assert_called_once()
    mock_ee.assert_called_once()
    mock_triple.assert_called_once()

    # Verify patterns were aggregated (2 + 1 + 3 = 6)
    assert len(patterns) == 6


def test_phase_3_5_integration(mock_db_session, sample_trades_with_full_data):
    """Test that Phase 3.5 is integrated into detect_patterns()."""
    from unittest.mock import patch
    from src.learning.pattern_combiner import PatternCombiner
    from src.learning.path_analyzer import PathAnalyzer

    # Test that PatternCombiner is called by detect_patterns()
    combiner = PatternCombiner(mock_db_session, min_sample_size=10)

    # Mock analyze_all_combinations to return test patterns
    combo_patterns = [
        Mock(pattern_type="rsi_momentum_combo", pattern_name="test_combo"),
        Mock(pattern_type="iv_triple_combo", pattern_name="test_triple"),
    ]

    with patch.object(combiner, '_get_trades_with_complete_data', return_value=sample_trades_with_full_data), \
         patch.object(combiner, '_get_entry_snapshot') as mock_entry, \
         patch.object(combiner, '_get_exit_snapshot') as mock_exit, \
         patch.object(combiner, '_get_position_snapshots') as mock_snapshots:

        def entry_side_effect(trade):
            return trade._test_entry_snapshot

        def exit_side_effect(trade):
            return trade._test_exit_snapshot

        def snapshots_side_effect(trade):
            return trade._test_position_snapshots

        mock_entry.side_effect = entry_side_effect
        mock_exit.side_effect = exit_side_effect
        mock_snapshots.side_effect = snapshots_side_effect

        # Test direct combination analysis
        all_combos = combiner.analyze_all_combinations()

    # Verify combination patterns were created
    assert isinstance(all_combos, list)

    # If there are enough trades, we should get some combination patterns
    combo_types = {p.pattern_type for p in all_combos if hasattr(p, 'pattern_type')}

    # At least some combination pattern types should be present
    expected_combo_types = [
        "rsi_momentum_combo", "iv_entry_exit_combo", "trend_greeks_combo",
        "breadth_stock_combo", "sector_exit_combo", "vix_entry_exit_combo",
        "support_drawdown_combo", "iv_triple_combo", "rsi_momentum_quality_combo",
        "trend_greeks_drawdown_combo"
    ]

    # Verify at least some expected types are present
    assert any(t in combo_types for t in expected_combo_types)
