"""Tests for the historical enrichment engine."""

import pytest
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
import pandas as pd
import numpy as np

from src.taad.enrichment.engine import (
    HistoricalEnrichmentEngine,
    EnrichmentResult,
    EnrichmentBatchResult,
    calculate_historical_quality,
)
from src.data.models import TradeEntrySnapshot, TradeExitSnapshot
from src.taad.enrichment.providers import OHLCV


def _make_bars_df(n: int = 100) -> pd.DataFrame:
    """Generate synthetic OHLCV DataFrame."""
    np.random.seed(42)
    base = 100.0
    closes = base * np.cumprod(1 + np.random.normal(0.001, 0.02, n))
    highs = closes * (1 + np.random.uniform(0, 0.02, n))
    lows = closes * (1 - np.random.uniform(0, 0.02, n))
    opens = (closes + lows) / 2
    return pd.DataFrame({
        "Open": opens, "High": highs, "Low": lows,
        "Close": closes, "Volume": np.random.randint(100000, 1000000, n),
    })


def _make_mock_trade(
    trade_id: int = 1,
    symbol: str = "AAPL",
    strike: float = 180.0,
    entry_premium: float = 1.50,
    dte: int = 30,
    entry_date: date = date(2025, 1, 15),
    expiration: date = date(2025, 2, 14),
    exit_date: date = None,
    exit_premium: float = None,
    profit_loss: float = None,
    profit_pct: float = None,
    commission: float = None,
    exit_reason: str = None,
    days_held: int = None,
    option_type: str = "PUT",
):
    """Create a mock Trade object."""
    trade = MagicMock()
    trade.id = trade_id
    trade.symbol = symbol
    trade.strike = strike
    trade.entry_premium = entry_premium
    trade.dte = dte
    trade.entry_date = datetime.combine(entry_date, datetime.min.time())
    trade.expiration = expiration
    trade.exit_date = datetime.combine(exit_date, datetime.min.time()) if exit_date else None
    trade.exit_premium = exit_premium
    trade.profit_loss = profit_loss
    trade.profit_pct = profit_pct
    trade.commission = commission
    trade.exit_reason = exit_reason
    trade.days_held = days_held
    trade.option_type = option_type
    trade.account_id = "YOUR_ACCOUNT"
    trade.is_closed.return_value = exit_date is not None
    trade.is_profitable.return_value = profit_loss is not None and profit_loss > 0
    return trade


def _make_mock_provider(stock_price: float = 195.0, vix: float = 18.5):
    """Create a mock provider with realistic data."""
    provider = MagicMock()

    # Stock bar
    stock_bar = OHLCV(
        date=date(2025, 1, 15), open=193.0, high=196.0,
        low=192.0, close=stock_price, volume=50000000,
    )
    prev_bar = OHLCV(
        date=date(2025, 1, 14), open=190.0, high=194.0,
        low=189.0, close=192.0, volume=45000000,
    )
    provider.get_stock_bar.side_effect = lambda sym, dt: (
        stock_bar if dt == date(2025, 1, 15) else prev_bar
    )

    # Historical bars for indicators
    provider.get_historical_bars.return_value = _make_bars_df(100)

    # VIX
    provider.get_vix_close.side_effect = lambda dt: vix if dt == date(2025, 1, 15) else vix - 0.5

    # Index bars (SPY, QQQ, IWM)
    spy_bar = OHLCV(date=date(2025, 1, 15), open=580, high=585, low=578, close=582, volume=70000000)
    spy_prev = OHLCV(date=date(2025, 1, 14), open=575, high=581, low=574, close=578, volume=65000000)
    qqq_bar = OHLCV(date=date(2025, 1, 15), open=490, high=495, low=488, close=492, volume=40000000)
    qqq_prev = OHLCV(date=date(2025, 1, 14), open=487, high=491, low=486, close=488, volume=38000000)
    iwm_bar = OHLCV(date=date(2025, 1, 15), open=220, high=224, low=219, close=222, volume=20000000)
    iwm_prev = OHLCV(date=date(2025, 1, 14), open=218, high=221, low=217, close=219, volume=18000000)

    def index_bar_fn(sym, dt):
        if sym == "SPY":
            return spy_bar if dt == date(2025, 1, 15) else spy_prev
        elif sym == "QQQ":
            return qqq_bar if dt == date(2025, 1, 15) else qqq_prev
        elif sym == "IWM":
            return iwm_bar if dt == date(2025, 1, 15) else iwm_prev
        return None

    provider.get_index_bar.side_effect = index_bar_fn

    # Sector ETF bars
    sector_df = pd.DataFrame({
        "Open": [180 + i for i in range(10)],
        "High": [182 + i for i in range(10)],
        "Low": [178 + i for i in range(10)],
        "Close": [181 + i for i in range(10)],
        "Volume": [5000000] * 10,
    })
    provider.get_sector_etf_bars.return_value = sector_df

    # Option snapshot — not available from basic mock provider
    provider.get_option_snapshot.return_value = None

    return provider


class TestEnrichmentEngineEntrySnapshot:
    """Test entry snapshot creation."""

    def test_creates_entry_snapshot(self):
        """Should create an entry snapshot with populated fields."""
        trade = _make_mock_trade()
        provider = _make_mock_provider()
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=date(2025, 1, 30)):
            result = engine.enrich_trade(trade)

        assert result.success is True
        assert result.entry_snapshot_created is True
        assert result.quality_score > 0

    def test_populates_stock_ohlcv(self):
        """Should populate stock OHLCV fields."""
        trade = _make_mock_trade()
        provider = _make_mock_provider(stock_price=195.0)
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            result = engine.enrich_trade(trade)

        # Check that session.add was called with a snapshot
        add_calls = session.add.call_args_list
        assert len(add_calls) >= 1

        # Extract the entry snapshot from the add call
        snapshot = add_calls[0][0][0]
        assert snapshot.stock_price == 195.0
        assert snapshot.stock_open == 193.0
        assert snapshot.stock_high == 196.0
        assert snapshot.stock_low == 192.0
        assert snapshot.stock_prev_close == 192.0

    def test_populates_derived_metrics(self):
        """Should calculate OTM % and OTM dollars."""
        trade = _make_mock_trade(strike=180.0)
        provider = _make_mock_provider(stock_price=195.0)
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            engine.enrich_trade(trade)

        snapshot = session.add.call_args_list[0][0][0]
        assert snapshot.otm_dollars == 15.0  # 195 - 180
        assert abs(snapshot.otm_pct - 15.0/195.0) < 0.0001

    def test_populates_bs_iv(self):
        """Should calculate B-S implied volatility and Greeks."""
        # Use realistic parameters: ~5% OTM put with $3.00 premium, 45 DTE
        # This should be solvable by B-S (higher premium for closer-to-ATM)
        trade = _make_mock_trade(entry_premium=3.00, strike=185.0, dte=45,
                                 expiration=date(2025, 3, 1))
        provider = _make_mock_provider(stock_price=195.0)
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            engine.enrich_trade(trade)

        snapshot = session.add.call_args_list[0][0][0]
        # B-S should produce IV for a ~5% OTM put with $3.00 premium at 45 DTE
        assert snapshot.iv is not None
        assert snapshot.iv > 0
        assert snapshot.delta is not None
        assert snapshot.delta < 0  # Put delta is negative


class TestEnrichmentEngineIdempotency:
    """Test idempotent behavior."""

    def test_merge_already_enriched(self):
        """Existing snapshot should be merged (gaps filled), not skipped."""
        trade = _make_mock_trade()
        provider = _make_mock_provider()
        session = MagicMock()

        # Existing snapshot with some data but gaps
        existing = _make_real_entry_snapshot(vix=18.5)
        session.query.return_value.filter_by.return_value.first.return_value = existing

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            result = engine.enrich_trade(trade, force=False)

        assert result.success is True
        assert result.entry_snapshot_created is False
        # Should be merged or skipped (if nothing new to fill)
        assert result.quality_score > 0

    def test_force_overwrites_existing(self):
        """Force should overwrite existing snapshot in-place with fresh data."""
        trade = _make_mock_trade()
        provider = _make_mock_provider()
        session = MagicMock()

        existing = _make_real_entry_snapshot()
        session.query.return_value.filter_by.return_value.first.return_value = existing

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            result = engine.enrich_trade(trade, force=True)

        assert result.success is True
        assert result.entry_snapshot_created is True
        # Force now updates in-place rather than deleting
        session.delete.assert_not_called()


class TestEnrichmentEngineExitSnapshot:
    """Test exit snapshot creation."""

    def test_creates_exit_snapshot_for_closed_trade(self):
        """Closed trades should get exit snapshots."""
        trade = _make_mock_trade(
            exit_date=date(2025, 2, 10),
            exit_premium=0.30,
            profit_loss=120.0,
            profit_pct=0.80,
            exit_reason="profit_target",
            days_held=26,
        )
        provider = _make_mock_provider()
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            result = engine.enrich_trade(trade)

        assert result.success is True
        assert result.exit_snapshot_created is True

    def test_no_exit_snapshot_for_open_trade(self):
        """Open trades should not get exit snapshots."""
        trade = _make_mock_trade()  # No exit_date
        provider = _make_mock_provider()
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            result = engine.enrich_trade(trade)

        assert result.exit_snapshot_created is False


class TestEnrichmentBatch:
    """Test batch enrichment."""

    def test_batch_enrichment(self):
        """Batch should process multiple trades."""
        trades = [_make_mock_trade(trade_id=i, symbol="AAPL") for i in range(3)]
        provider = _make_mock_provider()
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            batch = engine.enrich_batch(trades)

        assert batch.total == 3
        assert batch.enriched == 3
        assert batch.failed == 0


class TestTradeColumnUpdates:
    """Test that trade-level columns get updated."""

    def test_updates_trade_columns(self):
        """Enrichment should update vix_at_entry, spy_price, sector, etc."""
        trade = _make_mock_trade()
        provider = _make_mock_provider(vix=18.5)
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            engine.enrich_trade(trade)

        # Check trade columns were set
        assert trade.enrichment_status == "complete"
        assert trade.enrichment_quality is not None
        assert trade.vix_at_entry is not None
        assert trade.spy_price_at_entry is not None
        assert trade.sector is not None


class TestHistoricalQualityScore:
    """Test era-adjusted quality scoring."""

    def test_post2023_uses_standard_scoring(self):
        """Post-2023 should use standard quality score."""
        snapshot = MagicMock()
        snapshot.calculate_data_quality_score.return_value = 0.85
        score = calculate_historical_quality(snapshot, "post2023")
        assert score == 0.85

    def test_pre2023_adjusts_for_missing_fields(self):
        """Pre-2023 should be more lenient about missing option data."""
        snapshot = MagicMock()
        # Set up fields that would be available from yfinance
        snapshot.delta = -0.20  # From B-S
        snapshot.iv = 0.30  # From B-S
        snapshot.vix = 18.5
        snapshot.dte = 30
        snapshot.trend_direction = "uptrend"
        snapshot.days_to_earnings = 15
        snapshot.gamma = 0.01
        snapshot.theta = -0.02
        snapshot.vega = 0.10
        snapshot.rho = -0.01
        snapshot.hv_20 = 0.25
        snapshot.iv_hv_ratio = 1.2
        snapshot.stock_open = 195.0
        snapshot.stock_high = 196.0
        snapshot.stock_low = 192.0
        snapshot.stock_prev_close = 192.0
        snapshot.stock_change_pct = 0.015
        snapshot.sma_20 = 190.0
        snapshot.sma_50 = 185.0
        snapshot.trend_strength = 0.7
        snapshot.price_vs_sma20_pct = 0.026
        snapshot.price_vs_sma50_pct = 0.054
        snapshot.spy_price = 580.0
        snapshot.spy_change_pct = 0.005
        snapshot.vix_change_pct = -0.02
        snapshot.earnings_date = date(2025, 1, 30)
        snapshot.earnings_in_dte = True

        score = calculate_historical_quality(snapshot, "pre2023")
        assert score > 0.3  # Should be reasonable even without live option data
        assert score < 1.0


def _make_real_entry_snapshot(**overrides) -> TradeEntrySnapshot:
    """Create a real TradeEntrySnapshot instance (not a mock).

    Needed for merge tests because the merge method iterates __table__.columns.
    """
    defaults = dict(
        trade_id=1,
        symbol="AAPL",
        strike=180.0,
        expiration=date(2025, 2, 14),
        option_type="PUT",
        entry_premium=1.50,
        stock_price=195.0,
        dte=30,
        contracts=1,
        captured_at=datetime.now(),
        source="historical_enrichment",
    )
    defaults.update(overrides)
    return TradeEntrySnapshot(**defaults)


class TestEnrichmentEngineMerge:
    """Test merge behavior — fill empty fields, preserve existing values."""

    def test_merge_fills_empty_fields(self):
        """Existing snapshot has gaps; merge should fill them from fresh data."""
        # Existing snapshot: has vix but NOT spy_price, iv, delta
        existing = _make_real_entry_snapshot(vix=18.5, spy_price=None, iv=None, delta=None)

        trade = _make_mock_trade()
        provider = _make_mock_provider(stock_price=195.0)
        session = MagicMock()

        # Return existing snapshot from DB query
        session.query.return_value.filter_by.return_value.first.return_value = existing

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            result = engine.enrich_trade(trade, force=False)

        # Should have merged, not created or skipped
        assert result.success is True
        assert result.entry_snapshot_merged is True
        assert result.entry_snapshot_created is False

        # Original value preserved
        assert existing.vix == 18.5

        # Gaps filled by fresh data
        assert existing.spy_price is not None
        assert existing.spy_price > 0
        assert existing.iv is not None  # B-S should have computed this
        assert existing.delta is not None

        # session.add should NOT be called for a new entry snapshot
        # (the existing one is already tracked by SQLAlchemy)
        add_calls = [c[0][0] for c in session.add.call_args_list]
        for obj in add_calls:
            assert not isinstance(obj, TradeEntrySnapshot), (
                "Should not session.add a new TradeEntrySnapshot during merge"
            )

    def test_merge_preserves_existing_values(self):
        """Existing non-null values must NOT be overwritten, even if fresh differs."""
        existing = _make_real_entry_snapshot(
            iv=0.30,
            delta=-0.25,
            vix=18.5,
            spy_price=580.0,
            rsi_14=45.0,
            sma_20=190.0,
        )

        trade = _make_mock_trade()
        provider = _make_mock_provider(stock_price=195.0)
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = existing

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            result = engine.enrich_trade(trade, force=False)

        assert result.success is True

        # All original values preserved — NOT overwritten by fresh data
        assert existing.iv == 0.30
        assert existing.delta == -0.25
        assert existing.vix == 18.5
        assert existing.spy_price == 580.0
        assert existing.rsi_14 == 45.0
        assert existing.sma_20 == 190.0

    def test_merge_recalculates_quality(self):
        """Quality score should be recalculated after merge fills fields."""
        # Bare-minimum snapshot: only required fields
        existing = _make_real_entry_snapshot()
        old_quality = existing.calculate_data_quality_score()

        trade = _make_mock_trade()
        provider = _make_mock_provider(stock_price=195.0)
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = existing

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            result = engine.enrich_trade(trade, force=False)

        assert result.success is True
        assert result.entry_snapshot_merged is True
        # Quality should improve now that fields are filled
        assert result.quality_score >= old_quality

    def test_force_overwrites_with_fresh_data(self):
        """--force should overwrite existing fields with fresh data."""
        existing = _make_real_entry_snapshot(vix=18.5, spy_price=575.0)

        trade = _make_mock_trade()
        provider = _make_mock_provider(stock_price=195.0)  # spy=582 in mock
        session = MagicMock()

        session.query.return_value.filter_by.return_value.first.return_value = existing

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            result = engine.enrich_trade(trade, force=True)

        assert result.success is True
        assert result.entry_snapshot_created is True

        # Fresh data should overwrite (spy_price updated from provider)
        assert existing.spy_price == 582.0  # from mock provider, not old 575.0
        # Fresh IV from B-S should be populated
        assert existing.iv is not None

        # No delete should happen (in-place update)
        session.delete.assert_not_called()

    def test_force_preserves_fields_fresh_cant_populate(self):
        """--force should keep fields the fresh build can't populate.

        Fields like margin_requirement come from IBKR at trade time.
        _build_entry_snapshot can't reconstruct them, so --force must
        preserve them from the existing snapshot.
        """
        existing = _make_real_entry_snapshot(
            margin_requirement=15909.0,
            margin_efficiency_pct=0.0175,
            bid=1.20,
            ask=1.55,
        )

        trade = _make_mock_trade()
        provider = _make_mock_provider(stock_price=195.0)
        session = MagicMock()

        session.query.return_value.filter_by.return_value.first.return_value = existing

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            result = engine.enrich_trade(trade, force=True)

        assert result.success is True
        assert result.entry_snapshot_created is True

        # Margin fields preserved — fresh build can't populate these
        assert existing.margin_requirement == 15909.0
        assert existing.margin_efficiency_pct == 0.0175

        # Fresh data also populated
        assert existing.iv is not None  # B-S computed
        assert existing.spy_price is not None  # from provider

    def test_batch_counts_merges(self):
        """Batch result should have correct enriched/merged/skipped counts."""
        # Trade 1: no existing snapshot → enriched
        trade1 = _make_mock_trade(trade_id=1, symbol="AAPL")
        # Trade 2: existing snapshot with gaps → merged
        trade2 = _make_mock_trade(trade_id=2, symbol="MSFT")
        existing2 = _make_real_entry_snapshot(trade_id=2, symbol="MSFT")  # bare minimum

        # Trade 3: existing snapshot fully populated → skipped
        trade3 = _make_mock_trade(trade_id=3, symbol="GOOG")
        existing3 = _make_real_entry_snapshot(
            trade_id=3, symbol="GOOG",
            vix=18.5, spy_price=580.0, iv=0.30, delta=-0.25,
            gamma=0.01, theta=-0.02, vega=0.10, rho=-0.01,
            hv_20=0.22, iv_hv_ratio=1.36,
            sma_20=190.0, sma_50=185.0,
            trend_direction="uptrend", trend_strength=0.7,
            price_vs_sma20_pct=0.026, price_vs_sma50_pct=0.054,
            rsi_14=55.0, rsi_7=58.0,
            macd=1.2, macd_signal=0.8, macd_histogram=0.4,
            adx=25.0, plus_di=30.0, minus_di=20.0,
            atr_14=3.5, atr_pct=0.018,
            bb_upper=200.0, bb_lower=180.0, bb_position=0.75,
            support_1=185.0, support_2=180.0,
            resistance_1=200.0, resistance_2=205.0,
            distance_to_support_pct=0.05,
            spy_change_pct=0.005, vix_change_pct=-0.02,
            qqq_price=492.0, qqq_change_pct=0.008,
            iwm_price=222.0, iwm_change_pct=0.014,
            sector="Technology", sector_etf="XLK",
            sector_change_1d=0.005, sector_change_5d=0.02,
            vol_regime="low", market_regime="bull_quiet",
            day_of_week=2, is_opex_week=False, days_to_fomc=15,
            otm_pct=0.077, otm_dollars=15.0,
            stock_open=193.0, stock_high=196.0, stock_low=192.0,
            stock_prev_close=192.0, stock_change_pct=0.015,
        )

        provider = _make_mock_provider()
        session = MagicMock()

        # Map trade_id to existing snapshot
        existing_map = {1: None, 2: existing2, 3: existing3}

        def mock_first():
            # Access the filter_by kwargs to get trade_id
            trade_id = session.query.return_value.filter_by.call_args[1].get("trade_id")
            return existing_map.get(trade_id)

        session.query.return_value.filter_by.return_value.first = mock_first

        engine = HistoricalEnrichmentEngine(provider=provider, session=session)

        with patch("src.taad.enrichment.historical_context.get_historical_earnings_date", return_value=None):
            batch = engine.enrich_batch([trade1, trade2, trade3])

        assert batch.total == 3
        assert batch.enriched == 1   # trade1: new
        assert batch.merged >= 1     # trade2: had gaps filled
        assert batch.failed == 0
