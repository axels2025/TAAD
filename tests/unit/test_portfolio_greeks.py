"""Unit tests for portfolio Greeks aggregation service.

Tests get_portfolio_greeks() with mock PositionSnapshot data to verify:
  - Contract-weighted Greek aggregation math
  - Edge cases: no positions, missing snapshots, partial Greeks
  - Snapshot staleness calculation
"""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.data.models import Base, PositionSnapshot, Trade
from src.services.portfolio_greeks import get_portfolio_greeks


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database with schema."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


def _make_trade(
    id: int,
    symbol: str = "AAPL",
    strike: float = 180.0,
    contracts: int = 5,
    open: bool = True,
) -> Trade:
    """Helper to create a Trade with minimal required fields."""
    return Trade(
        id=id,
        trade_id=f"TEST-{id}",
        symbol=symbol,
        strike=strike,
        expiration=date.today() + timedelta(days=12),
        option_type="PUT",
        entry_date=datetime.now() - timedelta(days=5),
        entry_premium=0.50,
        contracts=contracts,
        dte=12,
        exit_date=None if open else datetime.now(),
    )


def _make_snapshot(
    trade_id: int,
    delta: float | None = -0.15,
    gamma: float | None = 0.02,
    theta: float | None = -0.05,
    vega: float | None = 0.12,
    iv: float | None = 0.25,
    current_pnl: float | None = 45.0,
    snapshot_date: date | None = None,
    captured_at: datetime | None = None,
) -> PositionSnapshot:
    """Helper to create a PositionSnapshot."""
    return PositionSnapshot(
        trade_id=trade_id,
        snapshot_date=snapshot_date or date.today(),
        captured_at=captured_at or datetime.now(),
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        iv=iv,
        current_pnl=current_pnl,
    )


class TestGetPortfolioGreeksEmpty:
    """Tests when there are no open positions."""

    def test_no_trades_returns_empty(self, db_session):
        """Empty database returns zero portfolio Greeks."""
        result = get_portfolio_greeks(db_session)

        assert result["positions"] == []
        assert result["portfolio"]["position_count"] == 0
        assert result["portfolio"]["total_delta"] == 0.0
        assert result["portfolio"]["total_theta"] == 0.0
        assert result["portfolio"]["avg_iv"] is None

    def test_only_closed_trades_returns_empty(self, db_session):
        """Closed trades should not appear in portfolio Greeks."""
        trade = _make_trade(id=1, open=False)
        db_session.add(trade)
        db_session.commit()

        result = get_portfolio_greeks(db_session)

        assert result["positions"] == []
        assert result["portfolio"]["position_count"] == 0


class TestGetPortfolioGreeksSinglePosition:
    """Tests with one open position."""

    def test_single_position_with_snapshot(self, db_session):
        """Single position's Greeks should be contract-weighted."""
        trade = _make_trade(id=1, symbol="AAPL", contracts=5)
        snapshot = _make_snapshot(trade_id=1, delta=-0.15, theta=-0.05, gamma=0.02, vega=0.12)
        db_session.add(trade)
        db_session.add(snapshot)
        db_session.commit()

        result = get_portfolio_greeks(db_session)

        assert result["portfolio"]["position_count"] == 1
        # Contract-weighted: delta * contracts = -0.15 * 5 = -0.75
        assert result["portfolio"]["total_delta"] == pytest.approx(-0.75, abs=1e-4)
        # theta * contracts = -0.05 * 5 = -0.25
        assert result["portfolio"]["total_theta"] == pytest.approx(-0.25, abs=1e-4)
        # gamma * contracts = 0.02 * 5 = 0.10
        assert result["portfolio"]["total_gamma"] == pytest.approx(0.10, abs=1e-6)
        # vega * contracts = 0.12 * 5 = 0.60
        assert result["portfolio"]["total_vega"] == pytest.approx(0.60, abs=1e-4)

    def test_single_position_per_contract_in_positions_list(self, db_session):
        """The positions list should show per-contract Greeks (not weighted)."""
        trade = _make_trade(id=1, symbol="AAPL", contracts=5)
        snapshot = _make_snapshot(trade_id=1, delta=-0.15, theta=-0.05)
        db_session.add(trade)
        db_session.add(snapshot)
        db_session.commit()

        result = get_portfolio_greeks(db_session)

        pos = result["positions"][0]
        assert pos["symbol"] == "AAPL"
        assert pos["contracts"] == 5
        assert pos["delta"] == -0.15  # Per-contract, not weighted
        assert pos["theta"] == -0.05  # Per-contract, not weighted

    def test_position_without_snapshot(self, db_session):
        """Position with no snapshot should have None Greeks but still appear."""
        trade = _make_trade(id=1, symbol="TSLA", contracts=3)
        db_session.add(trade)
        db_session.commit()

        result = get_portfolio_greeks(db_session)

        assert result["portfolio"]["position_count"] == 1
        assert result["portfolio"]["total_delta"] == 0.0
        pos = result["positions"][0]
        assert pos["symbol"] == "TSLA"
        assert pos["delta"] is None
        assert pos["snapshot_age_minutes"] is None


class TestGetPortfolioGreeksMultiplePositions:
    """Tests with multiple open positions."""

    def test_two_positions_summed(self, db_session):
        """Greeks from multiple positions should sum correctly."""
        t1 = _make_trade(id=1, symbol="AAPL", contracts=5)
        t2 = _make_trade(id=2, symbol="MSFT", strike=400.0, contracts=3)
        s1 = _make_snapshot(trade_id=1, delta=-0.15, theta=-0.05, gamma=0.02, vega=0.12, iv=0.25, current_pnl=45.0)
        s2 = _make_snapshot(trade_id=2, delta=-0.20, theta=-0.08, gamma=0.03, vega=0.15, iv=0.30, current_pnl=-20.0)
        db_session.add_all([t1, t2, s1, s2])
        db_session.commit()

        result = get_portfolio_greeks(db_session)

        pg = result["portfolio"]
        assert pg["position_count"] == 2
        # AAPL: -0.15*5 = -0.75, MSFT: -0.20*3 = -0.60, total = -1.35
        assert pg["total_delta"] == pytest.approx(-1.35, abs=1e-4)
        # AAPL: -0.05*5 = -0.25, MSFT: -0.08*3 = -0.24, total = -0.49
        assert pg["total_theta"] == pytest.approx(-0.49, abs=1e-4)
        # AAPL: 0.02*5 = 0.10, MSFT: 0.03*3 = 0.09, total = 0.19
        assert pg["total_gamma"] == pytest.approx(0.19, abs=1e-6)
        # AAPL: 0.12*5 = 0.60, MSFT: 0.15*3 = 0.45, total = 1.05
        assert pg["total_vega"] == pytest.approx(1.05, abs=1e-4)
        # P&L: 45 + (-20) = 25
        assert pg["total_pnl"] == pytest.approx(25.0, abs=0.01)
        # Avg IV: (0.25 + 0.30) / 2 = 0.275
        assert pg["avg_iv"] == pytest.approx(0.275, abs=1e-4)

    def test_mixed_snapshot_and_no_snapshot(self, db_session):
        """One position with snapshot + one without: only snapshotted contributes."""
        t1 = _make_trade(id=1, symbol="AAPL", contracts=5)
        t2 = _make_trade(id=2, symbol="GOOG", contracts=2)
        s1 = _make_snapshot(trade_id=1, delta=-0.10, theta=-0.03, gamma=0.01, vega=0.08)
        db_session.add_all([t1, t2, s1])
        db_session.commit()

        result = get_portfolio_greeks(db_session)

        pg = result["portfolio"]
        assert pg["position_count"] == 2
        # Only AAPL contributes: -0.10 * 5 = -0.50
        assert pg["total_delta"] == pytest.approx(-0.50, abs=1e-4)
        assert len(result["positions"]) == 2


class TestSnapshotSelection:
    """Tests that the latest snapshot is selected when multiple exist."""

    def test_uses_latest_snapshot(self, db_session):
        """Should pick the most recent snapshot, not an older one."""
        trade = _make_trade(id=1, contracts=5)
        old_snapshot = _make_snapshot(
            trade_id=1,
            delta=-0.10,
            snapshot_date=date.today() - timedelta(days=2),
            captured_at=datetime.now() - timedelta(days=2),
        )
        new_snapshot = _make_snapshot(
            trade_id=1,
            delta=-0.20,
            snapshot_date=date.today(),
            captured_at=datetime.now(),
        )
        db_session.add_all([trade, old_snapshot, new_snapshot])
        db_session.commit()

        result = get_portfolio_greeks(db_session)

        pos = result["positions"][0]
        assert pos["delta"] == -0.20  # Should be from the newest snapshot


class TestPartialGreeks:
    """Tests when some Greeks are None."""

    def test_partial_greeks_handled(self, db_session):
        """Positions with some None Greeks should not break aggregation."""
        trade = _make_trade(id=1, contracts=3)
        snapshot = _make_snapshot(
            trade_id=1,
            delta=-0.15,
            gamma=None,  # Missing
            theta=-0.04,
            vega=None,  # Missing
            iv=None,
        )
        db_session.add_all([trade, snapshot])
        db_session.commit()

        result = get_portfolio_greeks(db_session)

        pg = result["portfolio"]
        assert pg["total_delta"] == pytest.approx(-0.45, abs=1e-4)  # -0.15 * 3
        assert pg["total_gamma"] == 0.0  # None doesn't contribute
        assert pg["total_theta"] == pytest.approx(-0.12, abs=1e-4)  # -0.04 * 3
        assert pg["total_vega"] == 0.0  # None doesn't contribute
        assert pg["avg_iv"] is None  # No IV data


class TestSnapshotStaleness:
    """Tests for snapshot age calculation."""

    def test_snapshot_age_minutes(self, db_session):
        """Snapshot age should be calculated in minutes from now."""
        trade = _make_trade(id=1, contracts=1)
        snapshot = _make_snapshot(
            trade_id=1,
            captured_at=datetime.now() - timedelta(minutes=35),
        )
        db_session.add_all([trade, snapshot])
        db_session.commit()

        result = get_portfolio_greeks(db_session)

        age = result["positions"][0]["snapshot_age_minutes"]
        assert age is not None
        assert 34 <= age <= 36  # Allow small time drift during test execution

    def test_last_updated_is_latest_capture(self, db_session):
        """portfolio.last_updated should reflect the most recent captured_at."""
        t1 = _make_trade(id=1, contracts=1)
        t2 = _make_trade(id=2, symbol="MSFT", contracts=1)
        older = datetime.now() - timedelta(hours=2)
        newer = datetime.now() - timedelta(minutes=10)
        s1 = _make_snapshot(trade_id=1, captured_at=older)
        s2 = _make_snapshot(trade_id=2, captured_at=newer)
        db_session.add_all([t1, t2, s1, s2])
        db_session.commit()

        result = get_portfolio_greeks(db_session)

        last_updated = result["portfolio"]["last_updated"]
        assert last_updated is not None
        # Should be the newer timestamp (s2)
        parsed = datetime.fromisoformat(last_updated)
        assert abs((parsed - newer).total_seconds()) < 2
