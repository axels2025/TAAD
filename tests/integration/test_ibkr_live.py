"""Live IBKR integration tests using real paper trading connection.

CRITICAL SAFETY:
- Tests require IBKR TWS/Gateway running on port 7497 (PAPER TRADING)
- Tests are marked with @pytest.mark.live and skipped by default
- Run explicitly with: pytest -m live
- Tests use real market data and may place small test orders
- All orders are in PAPER TRADING ONLY

SETUP REQUIRED:
1. IBKR TWS/Gateway running
2. Logged into paper trading account (port 7497)
3. API connections enabled in TWS settings
4. Market data subscriptions active (if needed)

RUN WITH:
    pytest tests/integration/test_ibkr_live.py -m live -v

SKIP IN CI:
    pytest -m "not live"  # Default behavior
"""

import pytest
from datetime import datetime, timedelta
from time import sleep

from src.config.base import Config, IBKRConfig
from src.config.baseline_strategy import BaselineStrategy, ExitRules
from src.execution.exit_manager import ExitManager
from src.execution.order_executor import OrderExecutor
from src.execution.position_monitor import PositionMonitor
from src.execution.risk_governor import RiskGovernor
from src.strategies.base import TradeOpportunity
from src.tools.ibkr_client import IBKRClient


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture(scope="module")
def live_ibkr_client():
    """
    Live IBKR client connected to paper trading.

    This fixture attempts to connect to IBKR. If connection fails,
    all tests in this module are skipped.
    """
    # Use environment variables for configuration
    import os

    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "7497"))
    client_id = 999  # Use different client ID for live tests

    # Verify paper trading port
    if port != 7497:
        pytest.skip(
            f"SAFETY: Tests require port 7497 (paper trading), got {port}. "
            "Set IBKR_PORT=7497 in .env file."
        )

    # Create IBKRConfig
    config = IBKRConfig(
        host=host,
        port=port,
        client_id=client_id,
    )

    # Create client with config
    client = IBKRClient(config=config)

    try:
        client.connect()

        if not client.is_connected():
            pytest.skip(
                "Failed to connect to IBKR. "
                "Ensure TWS/Gateway is running on port 7497 (paper trading)."
            )

        yield client

        # Cleanup
        client.disconnect()

    except Exception as e:
        pytest.skip(
            f"IBKR not available: {e}\n"
            "Ensure TWS/Gateway is running on port 7497 (paper trading) "
            "with API connections enabled."
        )


@pytest.fixture(scope="module")
def config():
    """System configuration."""
    return Config()


@pytest.fixture(scope="module")
def strategy_config():
    """Strategy configuration."""
    return BaselineStrategy(
        otm_range=(0.10, 0.30),
        premium_range=(0.10, 2.00),
        dte_range=(5, 21),
        exit_rules=ExitRules(
            profit_target=0.50,
            stop_loss=-2.00,
            time_exit_dte=3,
        ),
    )


@pytest.fixture
def live_position_monitor(live_ibkr_client, strategy_config):
    """Live position monitor."""
    return PositionMonitor(
        ibkr_client=live_ibkr_client,
        config=strategy_config,
        update_interval_minutes=15,
    )


@pytest.fixture
def live_risk_governor(live_ibkr_client, live_position_monitor, config):
    """Live risk governor."""
    return RiskGovernor(
        ibkr_client=live_ibkr_client,
        position_monitor=live_position_monitor,
        config=config,
    )


@pytest.fixture
def live_order_executor(live_ibkr_client, config):
    """Live order executor (paper trading only)."""
    return OrderExecutor(
        ibkr_client=live_ibkr_client,
        config=config,
        dry_run=False,  # Use real IBKR orders (in paper account)
    )


@pytest.fixture
def live_exit_manager(live_ibkr_client, live_position_monitor, strategy_config):
    """Live exit manager."""
    return ExitManager(
        ibkr_client=live_ibkr_client,
        position_monitor=live_position_monitor,
        config=strategy_config,
    )


# ============================================================================
# TEST CLASS 1: CONNECTION & ACCOUNT
# ============================================================================


@pytest.mark.live
class TestLiveConnection:
    """Test real IBKR connection and account access."""

    def test_connection_establishes(self, live_ibkr_client):
        """Test connection to IBKR paper trading."""
        assert live_ibkr_client.is_connected()

    def test_account_summary_retrieves(self, live_ibkr_client):
        """Test retrieving actual account summary."""
        summary = live_ibkr_client.get_account_summary()

        assert isinstance(summary, dict)
        assert len(summary) > 0

        # Check for expected fields
        # Don't assert specific values, just that we get data
        expected_fields = ["NetLiquidation", "AvailableFunds", "BuyingPower"]
        found_fields = [f for f in expected_fields if f in summary]

        assert len(found_fields) > 0, f"Expected some of {expected_fields}, got {summary.keys()}"

    def test_account_value_retrieves(self, live_ibkr_client):
        """Test getting account value."""
        summary = live_ibkr_client.get_account_summary()
        value = summary.get("NetLiquidation", 0)

        assert isinstance(value, (int, float))
        assert value > 0  # Paper account should have some balance

    def test_account_summary_handles_non_numeric(self, live_ibkr_client):
        """Test that account summary handles non-numeric values like 'LLC'."""
        summary = live_ibkr_client.get_account_summary()

        # Iterate through all values and verify we can handle them
        for key, value in summary.items():
            # Should be string, int, or float - no crashes
            assert isinstance(value, (str, int, float)), \
                f"Account field {key} has unexpected type: {type(value)}"


# ============================================================================
# TEST CLASS 2: MARKET DATA
# ============================================================================


@pytest.mark.live
class TestLiveMarketData:
    """Test real market data retrieval."""

    def test_stock_contract_creation(self, live_ibkr_client):
        """Test creating and qualifying stock contract."""
        contract = live_ibkr_client.get_stock_contract("SPY")

        assert contract is not None
        assert contract.symbol == "SPY"
        assert contract.secType == "STK"

        # Qualify contract
        qualified = live_ibkr_client.qualify_contract(contract)
        assert qualified is not None
        assert qualified.conId > 0

    def test_market_data_retrieval(self, live_ibkr_client):
        """Test retrieving real market data for SPY."""
        contract = live_ibkr_client.get_stock_contract("SPY")
        qualified = live_ibkr_client.qualify_contract(contract)

        market_data = live_ibkr_client.get_market_data(qualified, snapshot=True)

        # Note: Market data may not be available outside market hours
        # or without proper subscriptions
        if market_data is None:
            pytest.skip("Market data not available (may be outside market hours)")

        assert isinstance(market_data, dict)

        # Check for price data
        has_price = any(k in market_data for k in ["last", "bid", "ask"])
        assert has_price, f"Expected price data, got {market_data.keys()}"

        # Verify we got real numbers, not NaN
        import math
        for key in ["last", "bid", "ask"]:
            if key in market_data and market_data[key]:
                value = market_data[key]
                assert isinstance(value, (int, float))
                assert not math.isnan(value), f"{key} is NaN"
                assert value > 0, f"{key} should be positive"

    def test_invalid_symbol_returns_none(self, live_ibkr_client):
        """Test market data for invalid symbol returns None, not NaN."""
        contract = live_ibkr_client.get_stock_contract("INVALIDXYZ123")

        market_data = live_ibkr_client.get_market_data(contract, snapshot=True)

        # Should return None for invalid symbols (after our fix)
        assert market_data is None, \
            "Invalid symbol should return None, not dict with NaN values"

    def test_option_contract_creation(self, live_ibkr_client):
        """Test creating option contract."""
        # Create far OTM option (unlikely to have issues)
        expiration = (datetime.now() + timedelta(days=30)).strftime("%Y%m%d")

        contract = live_ibkr_client.get_option_contract(
            symbol="SPY",
            expiration=expiration,
            strike=400.0,  # Far OTM
            right="P",
        )

        assert contract is not None
        assert contract.symbol == "SPY"
        assert contract.secType == "OPT"
        assert contract.right == "P"
        assert contract.strike == 400.0


# ============================================================================
# TEST CLASS 3: POSITION MONITORING
# ============================================================================


@pytest.mark.live
class TestLivePositions:
    """Test real position monitoring."""

    def test_get_current_positions(self, live_position_monitor):
        """Test retrieving current positions from paper account."""
        positions = live_position_monitor.get_all_positions()

        # Should return list (may be empty if no positions open)
        assert isinstance(positions, list)

        # If we have positions, validate structure
        if len(positions) > 0:
            pos = positions[0]
            assert hasattr(pos, "symbol")
            assert hasattr(pos, "strike")
            assert hasattr(pos, "contracts")
            assert hasattr(pos, "current_pnl")
            assert hasattr(pos, "current_pnl_pct")
            assert hasattr(pos, "dte")

            # Validate data types
            assert isinstance(pos.symbol, str)
            assert isinstance(pos.strike, (int, float))
            assert isinstance(pos.contracts, int)
            assert isinstance(pos.current_pnl, (int, float))
            assert isinstance(pos.dte, int)

    def test_position_pnl_calculation(self, live_position_monitor):
        """Test P&L calculation for any open positions."""
        positions = live_position_monitor.get_all_positions()

        # If we have positions, verify P&L is calculated
        for pos in positions:
            assert hasattr(pos, "current_pnl")
            assert hasattr(pos, "current_pnl_pct")
            assert isinstance(pos.current_pnl, (int, float))
            assert isinstance(pos.current_pnl_pct, (int, float))

            # P&L percentage should be reasonable (-10.0 to +10.0)
            # (If outside this range, either huge win/loss or calculation error)
            if pos.current_pnl_pct < -10.0 or pos.current_pnl_pct > 10.0:
                # Log warning but don't fail
                print(f"Warning: Large P&L% for {pos.symbol}: {pos.current_pnl_pct:.1%}")

    def test_update_all_positions(self, live_position_monitor):
        """Test updating all positions with current market data."""
        positions = live_position_monitor.update_all_positions()

        assert isinstance(positions, list)
        assert live_position_monitor.last_update is not None


# ============================================================================
# TEST CLASS 4: RISK CHECKS WITH REAL DATA
# ============================================================================


@pytest.mark.live
class TestLiveRiskChecks:
    """Test risk checks with real account data."""

    def test_risk_check_with_real_account(self, live_risk_governor):
        """Test risk governor uses real account data."""
        # Create a small test opportunity
        opportunity = TradeOpportunity(
            symbol="SPY",
            strike=400.0,  # Far OTM, won't actually execute
            expiration=datetime.now() + timedelta(days=7),
            option_type="PUT",
            premium=0.10,
            contracts=1,
            otm_pct=0.30,
            dte=7,
            stock_price=600.0,
            trend="uptrend",
            confidence=0.80,
            reasoning="Live integration test",
            margin_required=100.0,
        )

        # Risk check should use real account data
        result = live_risk_governor.pre_trade_check(opportunity)

        assert hasattr(result, "approved")
        assert hasattr(result, "reason")
        assert isinstance(result.approved, bool)
        assert isinstance(result.reason, str)

    def test_account_value_in_risk_status(self, live_risk_governor, live_ibkr_client):
        """Test that risk status uses real account value."""
        # Get real account value
        summary = live_ibkr_client.get_account_summary()
        account_value = summary.get("NetLiquidation", 0)
        assert account_value > 0

        # Risk status should reflect real account
        status = live_risk_governor.get_risk_status()

        assert status["account_value"] == account_value
        assert "daily_pnl" in status
        assert "current_positions" in status
        assert "trades_today" in status

    def test_daily_loss_calculation_with_real_positions(self, live_risk_governor):
        """Test daily loss calculation uses real position data."""
        status = live_risk_governor.get_risk_status()

        # Daily P&L should be calculated from real positions
        assert "daily_pnl" in status
        assert "daily_pnl_pct" in status
        assert isinstance(status["daily_pnl"], (int, float))
        assert isinstance(status["daily_pnl_pct"], (int, float))

    def test_position_count_accurate(self, live_risk_governor, live_position_monitor):
        """Test position count matches between components."""
        # Get positions from monitor
        positions = live_position_monitor.get_all_positions()
        position_count = len(positions)

        # Get risk status
        status = live_risk_governor.get_risk_status()

        # Counts should match
        assert status["current_positions"] == position_count


# ============================================================================
# TEST CLASS 5: EXIT MANAGER WITH REAL DATA
# ============================================================================


@pytest.mark.live
class TestLiveExitManager:
    """Test exit manager with real position data."""

    def test_evaluate_positions_for_exit(self, live_exit_manager):
        """Test exit evaluation with real positions."""
        # Evaluate all positions
        decisions = live_exit_manager.evaluate_exits()

        # Should return dict (may be empty if no positions)
        assert isinstance(decisions, dict)

        # If we have decisions, validate structure
        for pos_id, decision in decisions.items():
            assert hasattr(decision, "should_exit")
            assert hasattr(decision, "reason")
            assert isinstance(decision.should_exit, bool)

    def test_check_alerts_with_real_data(self, live_position_monitor):
        """Test alert generation with real position data."""
        alerts = live_position_monitor.check_alerts()

        # Should return list (may be empty)
        assert isinstance(alerts, list)

        # If we have alerts, validate structure
        for alert in alerts:
            assert hasattr(alert, "position_id")
            assert hasattr(alert, "alert_type")
            assert hasattr(alert, "severity")
            assert hasattr(alert, "message")


# ============================================================================
# TEST CLASS 6: ORDER PLACEMENT (MANUAL ONLY)
# ============================================================================


@pytest.mark.live
@pytest.mark.slow
class TestLiveOrderPlacement:
    """
    Test actual order placement in paper trading.

    WARNING: These tests place REAL orders in paper trading account.
    Orders are intentionally small and far OTM to avoid fills.
    All tests are SKIPPED by default - run manually only.
    """

    @pytest.mark.skip(reason="Only run manually with explicit confirmation")
    def test_place_small_limit_order(self, live_order_executor, live_ibkr_client):
        """
        Test placing a small limit order that won't fill.

        MANUAL RUN ONLY:
        pytest tests/integration/test_ibkr_live.py::TestLiveOrderPlacement::test_place_small_limit_order -v
        """
        # Create far OTM opportunity (unlikely to fill)
        opportunity = TradeOpportunity(
            symbol="SPY",
            strike=350.0,  # Very far OTM
            expiration=datetime.now() + timedelta(days=7),
            option_type="PUT",
            premium=0.05,
            contracts=1,  # Just 1 contract
            otm_pct=0.40,
            dte=7,
            stock_price=600.0,
            trend="uptrend",
            confidence=0.80,
            reasoning="Live integration test - will be canceled",
            margin_required=50.0,
        )

        # Place order with limit price that won't fill
        result = live_order_executor.execute_trade(
            opportunity=opportunity,
            order_type="LIMIT",
            limit_price=0.01,  # Very low, won't fill
        )

        # Order should be submitted
        assert result.success is True
        assert result.order_id is not None

        # Wait for order to be submitted
        sleep(2)

        # Cancel the order immediately
        if result.order_id:
            cancelled = live_order_executor.cancel_order(result.order_id)
            assert cancelled is True
            sleep(1)


# ============================================================================
# TEST CLASS 7: ERROR HANDLING WITH REAL CONNECTION
# ============================================================================


@pytest.mark.live
class TestLiveErrorHandling:
    """Test error handling with real IBKR connection."""

    def test_handles_invalid_contract(self, live_ibkr_client):
        """Test handling of invalid contract requests."""
        from ib_insync import Stock

        # Create invalid contract
        invalid = Stock("", "", "")

        # Qualification should fail gracefully
        result = live_ibkr_client.qualify_contract(invalid)
        assert result is None or result.conId == 0

    def test_handles_nonexistent_option(self, live_ibkr_client):
        """Test handling of nonexistent option contract."""
        # Try to get option with invalid expiration
        contract = live_ibkr_client.get_option_contract(
            symbol="SPY",
            expiration="19900101",  # Past date
            strike=100.0,
            right="P",
        )

        # Should either return None or a contract that fails qualification
        if contract:
            qualified = live_ibkr_client.qualify_contract(contract)
            # Should fail to qualify
            assert qualified is None or qualified.conId == 0

    def test_reconnection_after_disconnect(self, live_ibkr_client):
        """Test that client can reconnect after disconnect."""
        # Verify connected initially
        assert live_ibkr_client.is_connected()

        # Disconnect
        live_ibkr_client.disconnect()
        assert not live_ibkr_client.is_connected()

        # Reconnect
        live_ibkr_client.connect()
        assert live_ibkr_client.is_connected()

        # Verify still works after reconnection
        summary = live_ibkr_client.get_account_summary()
        value = summary.get("NetLiquidation", 0)
        assert value > 0


# ============================================================================
# TEST CLASS 8: DATA CONSISTENCY
# ============================================================================


@pytest.mark.live
class TestLiveDataConsistency:
    """Test that real data matches expected formats."""

    def test_account_summary_data_types(self, live_ibkr_client):
        """Test account summary returns consistent data types."""
        summary = live_ibkr_client.get_account_summary()

        # Verify we can handle all returned values
        for key, value in summary.items():
            # Should be string, int, or float - no objects
            assert isinstance(value, (str, int, float)), \
                f"Account summary {key} has unexpected type: {type(value)}"

    def test_position_data_consistency(self, live_position_monitor):
        """Test position data has consistent format."""
        positions = live_position_monitor.get_all_positions()

        for pos in positions:
            # Verify all expected fields present
            required_fields = [
                "symbol",
                "strike",
                "contracts",
                "current_pnl",
                "current_pnl_pct",
                "dte",
                "entry_premium",
                "current_premium",
            ]

            for field in required_fields:
                assert hasattr(pos, field), f"Position missing field: {field}"

            # Verify data types
            assert isinstance(pos.symbol, str)
            assert isinstance(pos.contracts, int)
            assert isinstance(pos.current_pnl, (int, float))
            assert isinstance(pos.strike, (int, float))
            assert isinstance(pos.dte, int)

            # Verify reasonable values
            assert pos.contracts > 0
            assert pos.strike > 0
            assert pos.dte >= 0

    def test_market_data_no_nan_values(self, live_ibkr_client):
        """Test that market data never contains NaN values."""
        import math

        contract = live_ibkr_client.get_stock_contract("SPY")
        qualified = live_ibkr_client.qualify_contract(contract)

        market_data = live_ibkr_client.get_market_data(qualified, snapshot=True)

        if market_data:
            # Verify no NaN values in numeric fields
            for key, value in market_data.items():
                if isinstance(value, float):
                    assert not math.isnan(value), \
                        f"Market data contains NaN for {key}"


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def wait_for_market_data(client, contract, timeout=5):
    """Wait for market data to be available.

    Args:
        client: IBKRClient instance
        contract: Contract to get data for
        timeout: Maximum seconds to wait

    Returns:
        Market data dict or None
    """
    for _ in range(timeout):
        data = client.get_market_data(contract, snapshot=True)
        if data and "last" in data and data["last"]:
            return data
        sleep(1)
    return None


def get_spy_current_price(client):
    """Get current SPY price.

    Args:
        client: IBKRClient instance

    Returns:
        float: SPY price or 500.0 as fallback
    """
    try:
        contract = client.get_stock_contract("SPY")
        qualified = client.qualify_contract(contract)
        data = wait_for_market_data(client, qualified, timeout=3)

        if data and "last" in data:
            return float(data["last"])

    except Exception:
        pass

    # Fallback
    return 500.0


def create_far_otm_test_opportunity(symbol="SPY", days=7):
    """Create test opportunity that won't accidentally fill.

    Args:
        symbol: Stock symbol
        days: Days to expiration

    Returns:
        TradeOpportunity for testing
    """
    return TradeOpportunity(
        symbol=symbol,
        strike=300.0,  # Very far OTM for SPY
        expiration=datetime.now() + timedelta(days=days),
        option_type="PUT",
        premium=0.05,
        contracts=1,
        otm_pct=0.40,
        dte=days,
        stock_price=600.0,
        trend="uptrend",
        confidence=0.80,
        reasoning="Live integration test opportunity",
        margin_required=50.0,
    )
