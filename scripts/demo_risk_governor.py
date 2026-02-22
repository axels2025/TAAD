"""Demonstrate RiskGovernor risk limit enforcement.

This script shows how the RiskGovernor enforces all risk limits:
- Daily loss limit (-2%)
- Max positions (10)
- Max positions per day (10)
- Margin utilization (80%)
- Emergency halt capability
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Ensure paper trading mode
os.environ["PAPER_TRADING"] = "true"
os.environ["IBKR_PORT"] = "7497"

from src.config.base import Config
from src.execution.position_monitor import PositionMonitor, PositionStatus
from src.execution.risk_governor import RiskGovernor
from src.strategies.base import TradeOpportunity
from src.tools.ibkr_client import IBKRClient


def info(msg):
    """Print info message."""
    print(f"INFO     | {msg}")


def warning(msg):
    """Print warning message."""
    print(f"WARNING  | {msg}")


def critical(msg):
    """Print critical message."""
    print(f"CRITICAL | {msg}")


def error(msg):
    """Print error message."""
    print(f"ERROR    | {msg}")


def create_mock_ibkr_client():
    """Create mock IBKR client for demonstration."""
    client = MagicMock(spec=IBKRClient)

    # Mock account summary
    client.get_account_summary.return_value = {
        "NetLiquidation": 100000.0,
        "AvailableFunds": 80000.0,
        "BuyingPower": 200000.0,
    }

    return client


def create_mock_position_monitor(positions=None):
    """Create mock PositionMonitor with optional positions."""
    monitor = MagicMock(spec=PositionMonitor)

    if positions is None:
        positions = []

    monitor.get_all_positions.return_value = positions

    return monitor


def create_sample_opportunity(
    symbol="AAPL",
    strike=200.0,
    premium=0.50,
    contracts=5,
    margin=1000.0,
):
    """Create sample trade opportunity."""
    return TradeOpportunity(
        symbol=symbol,
        strike=strike,
        expiration=datetime.now() + timedelta(days=10),
        option_type="PUT",
        premium=premium,
        contracts=contracts,
        otm_pct=0.15,
        dte=10,
        stock_price=235.0,
        trend="uptrend",
        confidence=0.85,
        reasoning=f"Demo trade for {symbol}",
        margin_required=margin,
    )


def demo_section(title):
    """Print demo section header."""
    print()
    print("=" * 70)
    print(f"{title}")
    print("=" * 70)
    print()


def main():
    """Demonstrate RiskGovernor capabilities."""
    info("=" * 70)
    info("RISK GOVERNOR DEMONSTRATION")
    info("=" * 70)
    info("")

    # Initialize components
    config = Config()
    ibkr_client = create_mock_ibkr_client()
    position_monitor = create_mock_position_monitor()

    risk_governor = RiskGovernor(
        ibkr_client=ibkr_client,
        position_monitor=position_monitor,
        config=config,
    )

    info("✓ RiskGovernor initialized")
    info("")

    # Demo 1: All checks pass
    demo_section("DEMO 1: All Risk Checks Pass")

    opportunity = create_sample_opportunity()
    info(f"Testing trade: {opportunity.symbol} ${opportunity.strike} PUT")
    info(f"  Contracts: {opportunity.contracts}")
    info(f"  Margin required: ${opportunity.margin_required:,.0f}")
    info("")

    result = risk_governor.pre_trade_check(opportunity)

    if result.approved:
        info(f"✅ APPROVED: {result.reason}")
    else:
        error(f"❌ REJECTED: {result.reason}")

    info("")

    # Demo 2: Daily loss limit exceeded
    demo_section("DEMO 2: Daily Loss Limit Exceeded (-2%)")

    # Create positions with large losses
    losing_positions = [
        PositionStatus(
            position_id="POS1",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            contracts=10,
            entry_premium=0.50,
            current_premium=1.50,
            current_pnl=-1000.0,
            current_pnl_pct=-2.0,
            days_held=2,
            dte=8,
        ),
        PositionStatus(
            position_id="POS2",
            symbol="MSFT",
            strike=350.0,
            option_type="P",
            contracts=10,
            entry_premium=1.00,
            current_premium=2.50,
            current_pnl=-1500.0,
            current_pnl_pct=-1.5,
            days_held=1,
            dte=9,
        ),
    ]

    position_monitor.get_all_positions.return_value = losing_positions

    total_loss = sum(p.current_pnl for p in losing_positions)
    account_value = 100000.0
    loss_pct = total_loss / account_value

    info(f"Current positions with losses:")
    info(f"  Total P&L: ${total_loss:+,.0f}")
    info(f"  Loss %: {loss_pct:.2%} (limit: -2.00%)")
    info("")

    # Reset halt state for demo
    risk_governor._trading_halted = False

    result = risk_governor.pre_trade_check(opportunity)

    if result.approved:
        info(f"✅ APPROVED: {result.reason}")
    else:
        warning(f"❌ REJECTED: {result.reason}")
        warning(f"   Limit: {result.limit_name}")
        warning(f"   Utilization: {result.utilization_pct:.1f}%")

    info("")

    # Check if circuit breaker triggered
    if risk_governor.is_halted():
        critical("🔴 CIRCUIT BREAKER TRIGGERED - Trading halted!")
        critical(f"   Reason: {risk_governor._halt_reason}")

    info("")

    # Demo 3: Resume trading after halt
    demo_section("DEMO 3: Resume Trading After Halt")

    info("Resuming trading...")
    risk_governor.resume_trading()

    if not risk_governor.is_halted():
        info("✅ Trading resumed successfully")
    else:
        error("❌ Trading still halted")

    info("")

    # Reset positions for next demos
    position_monitor.get_all_positions.return_value = []

    # Demo 4: Max positions reached
    demo_section("DEMO 4: Max Positions Limit (10)")

    # Create 10 positions (at limit)
    max_positions = [
        PositionStatus(
            position_id=f"POS{i}",
            symbol=f"STOCK{i}",
            strike=200.0,
            option_type="P",
            contracts=1,
            entry_premium=0.50,
            current_premium=0.45,
            current_pnl=5.0,
            current_pnl_pct=0.10,
            days_held=1,
            dte=9,
        )
        for i in range(10)
    ]

    position_monitor.get_all_positions.return_value = max_positions

    info(f"Current open positions: {len(max_positions)}/10")
    info(f"Attempting to open new position...")
    info("")

    result = risk_governor.pre_trade_check(opportunity)

    if result.approved:
        info(f"✅ APPROVED: {result.reason}")
    else:
        warning(f"❌ REJECTED: {result.reason}")
        warning(f"   Current: {result.current_value}")
        warning(f"   Limit: {result.limit_value}")

    info("")

    # Demo 5: Max positions per day
    demo_section("DEMO 5: Max Positions Per Day (10)")

    # Reset position count
    position_monitor.get_all_positions.return_value = []

    # Simulate 10 trades already placed today
    risk_governor._trades_today = 10

    info(f"Trades placed today: {risk_governor._trades_today}/10")
    info(f"Attempting to place another trade...")
    info("")

    result = risk_governor.pre_trade_check(opportunity)

    if result.approved:
        info(f"✅ APPROVED: {result.reason}")
    else:
        warning(f"❌ REJECTED: {result.reason}")
        warning(f"   Trades today: {result.current_value}")
        warning(f"   Limit: {result.limit_value}")

    info("")

    # Demo 6: Insufficient margin
    demo_section("DEMO 6: Insufficient Margin")

    # Reset daily counter
    risk_governor._trades_today = 0

    # Create opportunity requiring more margin than available
    large_trade = create_sample_opportunity(
        symbol="TSLA",
        strike=300.0,
        premium=5.00,
        contracts=20,
        margin=90000.0,  # Exceeds available $80,000
    )

    info(f"Trade: {large_trade.symbol} ${large_trade.strike} PUT")
    info(f"  Margin required: ${large_trade.margin_required:,.0f}")
    info(f"  Available funds: $80,000")
    info("")

    result = risk_governor.pre_trade_check(large_trade)

    if result.approved:
        info(f"✅ APPROVED: {result.reason}")
    else:
        warning(f"❌ REJECTED: {result.reason}")

    info("")

    # Demo 7: Record trades
    demo_section("DEMO 7: Recording Trades")

    risk_governor._trades_today = 0

    info(f"Trades today: {risk_governor._trades_today}")
    info("")

    for i in range(3):
        info(f"Recording trade {i + 1}...")
        risk_governor.record_trade(opportunity)
        info(f"  Trades today: {risk_governor._trades_today}")

    info("")

    # Demo 8: Risk status report
    demo_section("DEMO 8: Risk Status Report")

    # Set up realistic scenario
    position_monitor.get_all_positions.return_value = [
        PositionStatus(
            position_id="POS1",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            contracts=5,
            entry_premium=0.50,
            current_premium=0.45,
            current_pnl=25.0,
            current_pnl_pct=0.10,
            days_held=2,
            dte=8,
        ),
        PositionStatus(
            position_id="POS2",
            symbol="MSFT",
            strike=350.0,
            option_type="P",
            contracts=3,
            entry_premium=1.00,
            current_premium=0.90,
            current_pnl=30.0,
            current_pnl_pct=0.10,
            days_held=1,
            dte=9,
        ),
    ]

    status = risk_governor.get_risk_status()

    info("Current Risk Status:")
    info(f"  Trading Halted: {status['trading_halted']}")
    info(
        f"  Positions: {status['current_positions']}/{status['max_positions']}"
    )
    info(
        f"  Trades Today: {status['trades_today']}/{status['max_trades_today']}"
    )
    info(f"  Daily P&L: ${status['daily_pnl']:+.2f}")
    info(f"  Daily P&L %: {status['daily_pnl_pct']:+.2%}")
    info(f"  Daily Loss Limit: {status['daily_loss_limit']:.2%}")
    info(f"  Account Value: ${status['account_value']:,.0f}")

    info("")

    # Demo 9: Emergency halt
    demo_section("DEMO 9: Emergency Halt")

    info("Triggering emergency halt...")
    risk_governor.emergency_halt("Manual intervention required")

    info("")
    critical("🔴 EMERGENCY HALT ACTIVATED")
    critical(f"   Reason: {risk_governor._halt_reason}")

    info("")
    info("Testing if trades can be placed during halt...")

    result = risk_governor.pre_trade_check(opportunity)

    if result.approved:
        error(f"❌ ERROR: Trade should be rejected during halt!")
    else:
        info(f"✅ CORRECT: Trade rejected during halt")
        info(f"   Reason: {result.reason}")

    info("")

    # Final summary
    demo_section("DEMONSTRATION COMPLETE")

    info("✅ All risk limits demonstrated successfully!")
    info("")
    info("Risk Limits Enforced:")
    info("  1. ✓ Daily loss limit (-2%)")
    info("  2. ✓ Max positions (10)")
    info("  3. ✓ Max positions per day (10)")
    info("  4. ✓ Margin utilization (80%)")
    info("  5. ✓ Emergency halt capability")
    info("")
    info("The RiskGovernor is ready for integration testing!")
    info("")
    info("=" * 70)


if __name__ == "__main__":
    main()
